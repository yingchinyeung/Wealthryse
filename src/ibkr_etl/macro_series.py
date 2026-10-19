"""Daily macro series for the macro section: FRED keyless CSV downloads and Yahoo USD/CNH.

Phase 3 data (docs/PHASE3_DATA_MACRO_13F.md). The job ibkrv2stg-macro-series
(scripts/load_macro_series.py) calls load(); nothing runs unless MACRO_SERIES_LOAD=on, and the table
comes from a held migration (infra/medallion/drafts/macro_series.sql).

For each series the raw response is captured to bronze-market-data first (fred-series/ for FRED,
yahoo-chart/ for USD/CNH, through raw_capture), then parsed and stored in silver macro_series:

- FRED CSV: '.' (or an empty cell) means no observation that day and is skipped;
- the vintage is the UTC date of the fetch. A value is stored under a new vintage only when it is new
  or differs from the latest stored vintage of its date, so every revision is kept and a repeat is
  not; a rerun on the same day rewrites the same rows (idempotent);
- one transaction per series, so a series that fails leaves the others stored.

Licences (Medallion condition 5: checked before anything is kept). From each series' FRED notes, as
quoted in SERIES below:
- DGS10, DGS2 (H.15) and DTWEXBGS (H.10) come from the Federal Reserve Board, and DCOILBRENTEU from
  the U.S. Energy Information Administration. Their notes carry no copyright notice: US government
  data, public domain, citation requested. Each capture says so in its metadata.
- VIXCLS comes from Cboe, and its FRED notes say "Copyright, 2016, Chicago Board Options Exchange,
  Inc. Reprinted with permission." It is not public domain, so it is not fetched or kept unless the
  owner confirms a licence (MACRO_SERIES_LICENSE_CONFIRMED=VIXCLS, as HIBOR has
  HIBOR_LICENSE_SCOPE_CONFIRMED).
- ICE BofA series (ids starting BAML) are copyrighted by ICE Data Indices and are never fetched.
- USD/CNH comes from the existing yahoo-chart feed, under its internal-only rule
  (dataLicense: yfinance-internal-only).
HIBOR stays in risk_free_rates and is not copied here.
"""
from __future__ import annotations

import csv
import io
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import market_data, raw_capture
from .db import transaction
from .reference_data import HttpBody, _ssl_context
from .util import utc_now_iso

SETTING = "MACRO_SERIES_LOAD"  # off (default) or on
LICENSE_SETTING = "MACRO_SERIES_LICENSE_CONFIRMED"  # restricted series the owner has licensed, comma-separated
TABLE = "silver.macro_series"
RELATION = "public.macro_series"
FEED = "fred-series"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_PROVIDER = "FRED, Federal Reserve Bank of St. Louis (keyless CSV download)"
USER_AGENT = "IBKR-Portfolio-Analytics/2.1 (macro series)"
DEFAULT_START = date(2015, 1, 1)
TIMEOUT_SECONDS = 45
ATTEMPTS = 3
MISSING = frozenset({"", "."})
EXIT_HELD = 65  # the table's held migration has not run: nothing written (as scripts/build_quant_analytics.py's 65)
EXIT_DAILY_WINDOW = 75  # started inside the daily chain's window: nothing done, run again after it
# The daily chain (07:50 to 09:25 UTC) is left alone, as the publish gate's jobs and the research job are.
DAILY_WINDOW = (clock_time(7, 50), clock_time(9, 25))

PUBLIC_DOMAIN = "public-domain-citation-requested"
CBOE_COPYRIGHT = "cboe-copyright-reprinted-with-permission"
YAHOO_INTERNAL = "yfinance-internal-only"
# ICE BofA indices on FRED (BAMLH0A0HYM2 and the like): copyrighted, left out (design, "Licences").
EXCLUDED_PREFIXES = ("BAML",)
UNITS = ("percent", "index", "usd_per_barrel", "cnh_per_usd")
# Plausible levels in each unit; a value outside means the response is not what we think it is.
RANGES = {"percent": (-5.0, 30.0), "index": (0.0, 1000.0), "usd_per_barrel": (0.0, 1000.0),
          "cnh_per_usd": (1.0, 20.0)}
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


@dataclass(frozen=True)
class Series:
    series_id: str
    name: str
    units: str
    source: str  # 'fred' or 'yahoo-chart'
    licence: str
    public: bool  # its licence lets us keep it without the owner's confirmation
    citation: str  # what each capture's licence metadata says
    notes: str  # what the source's own notes say about it (checked by hand, see the module docstring)


SERIES: tuple[Series, ...] = (
    Series("DGS10", "Market yield on US Treasury securities at 10-year constant maturity", "percent", "fred",
           PUBLIC_DOMAIN, True,
           "Board of Governors of the Federal Reserve System (US), H.15 Selected Interest Rates, via FRED",
           "FRED: source Board of Governors of the Federal Reserve System (US), release H.15; units percent, "
           "daily, not seasonally adjusted. No copyright notice in the notes."),
    Series("DGS2", "Market yield on US Treasury securities at 2-year constant maturity", "percent", "fred",
           PUBLIC_DOMAIN, True,
           "Board of Governors of the Federal Reserve System (US), H.15 Selected Interest Rates, via FRED",
           "FRED: source Board of Governors of the Federal Reserve System (US), release H.15; units percent, "
           "daily, not seasonally adjusted. No copyright notice in the notes."),
    Series("DTWEXBGS", "Nominal broad US dollar index", "index", "fred", PUBLIC_DOMAIN, True,
           "Board of Governors of the Federal Reserve System (US), H.10 Foreign Exchange Rates, via FRED",
           "FRED: source Board of Governors of the Federal Reserve System (US), release H.10; units index "
           "Jan 2006=100, daily (published weekly). No copyright notice in the notes."),
    Series("DCOILBRENTEU", "Crude oil prices: Brent - Europe", "usd_per_barrel", "fred", PUBLIC_DOMAIN, True,
           "U.S. Energy Information Administration, Spot Prices, via FRED",
           "FRED: source U.S. Energy Information Administration, release Spot Prices; units dollars per "
           "barrel, daily (published weekly). Notes link EIA's definitions; no copyright notice. EIA "
           "republishes these spot prices from a commercial source: confirm before switch-on "
           "(docs/PHASE3_DATA_MACRO_13F.md)."),
    Series("VIXCLS", "Cboe volatility index: VIX", "index", "fred", CBOE_COPYRIGHT, False,
           "Chicago Board Options Exchange (Cboe), via FRED; copyright Cboe, reprinted with permission",
           "FRED: source Chicago Board Options Exchange; notes: 'Copyright, 2016, Chicago Board Options "
           "Exchange, Inc. Reprinted with permission.' Not public domain: kept only once licensed."),
    Series("USDCNH", "US dollar in offshore renminbi (Yahoo CNH=X)", "cnh_per_usd", "yahoo-chart",
           YAHOO_INTERNAL, True, "Yahoo Finance chart API, internal use only",
           "Yahoo chart closes of CNH=X, under the existing internal-only rule (yfinance-internal-only)."),
)
BY_ID = {series.series_id: series for series in SERIES}
CNH_INSTRUMENT = market_data.MarketInstrument(
    instrument_id="usd-cnh", symbol="CNH=X", name="US dollar in offshore renminbi", currency="CNH",
    price_type="fx_close")


# ---------------------------------------------------------------- settings


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    """MACRO_SERIES_LOAD=on. Anything else, a typo included, is off."""
    environ = os.environ if environ is None else environ
    return str(environ.get(SETTING, "")).strip().lower() == "on"


def confirmed_licences(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    environ = os.environ if environ is None else environ
    return frozenset(part.strip().upper() for part in str(environ.get(LICENSE_SETTING, "")).split(",")
                     if part.strip())


def series_to_load(environ: Mapping[str, str] | None = None) -> tuple[list[Series], list[dict[str, str]]]:
    """The series this run fetches, and those it leaves out with the reason."""
    confirmed = confirmed_licences(environ)
    chosen, left_out = [], []
    for series in SERIES:
        if series.public or series.series_id in confirmed:
            chosen.append(series)
        else:
            left_out.append({"series_id": series.series_id, "licence": series.licence,
                             "reason": f"not public domain; set {LICENSE_SETTING}={series.series_id} once licensed"})
    return chosen, left_out


def in_daily_window(moment: datetime) -> bool:
    """True from 07:50 to 09:25 UTC, the daily chain's window."""
    now = moment.astimezone(timezone.utc).time()
    return DAILY_WINDOW[0] <= now <= DAILY_WINDOW[1]


# ---------------------------------------------------------------- FRED


def fred_url(series_id: str, start: date | None = None) -> str:
    """The keyless CSV download of one series; cosd asks for observations from start on."""
    series_id = str(series_id).strip().upper()
    if series_id.startswith(EXCLUDED_PREFIXES):
        raise ValueError(f"{series_id} is an ICE BofA series: copyrighted, never fetched")
    series = BY_ID.get(series_id)
    if series is None or series.source != "fred":
        raise ValueError(f"{series_id} is not a reviewed FRED series")
    query = {"id": series_id}
    if start is not None:
        query["cosd"] = start.isoformat()
    return f"{FRED_CSV_URL}?{urllib.parse.urlencode(query)}"


def parse_fred_csv(body: bytes | str, series_id: str) -> tuple[list[tuple[str, float]], int]:
    """(date, value) pairs in date order, and the number of missing days skipped.

    Accepts both header styles FRED has used (DATE or observation_date, then the series id). '.' or an
    empty cell is a day without an observation. Anything else that is not a finite number, a date that
    is not yyyy-MM-dd, or two different values for one date rejects the whole response.
    """
    text = body.decode("utf-8-sig") if isinstance(body, (bytes, bytearray)) else str(body)
    if text.lstrip()[:1] == "<":
        raise ValueError(f"FRED returned a web page, not CSV, for {series_id}")
    reader = csv.reader(io.StringIO(text))
    header = [cell.strip() for cell in next(reader, [])]
    if len(header) != 2 or header[0].lower() not in ("date", "observation_date") or header[1] != series_id:
        raise ValueError(f"FRED CSV for {series_id} has an unexpected header: {header[:3]}")
    observations: dict[str, float] = {}
    skipped = 0
    for line, row in enumerate(reader, start=2):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) != 2:
            raise ValueError(f"FRED CSV for {series_id}: line {line} has {len(row)} cells")
        day, cell = row[0].strip(), row[1].strip()
        if not _ISO_DATE.match(day):
            raise ValueError(f"FRED CSV for {series_id}: line {line} date {day!r} is not yyyy-MM-dd")
        date.fromisoformat(day)
        if cell in MISSING:
            skipped += 1
            continue
        try:
            value = float(cell)
        except ValueError:
            raise ValueError(f"FRED CSV for {series_id}: {day} value {cell!r} is not a number") from None
        if not math.isfinite(value):
            raise ValueError(f"FRED CSV for {series_id}: {day} value is not finite")
        if day in observations and observations[day] != value:
            raise ValueError(f"FRED CSV for {series_id}: two different values for {day}")
        observations[day] = value
    return sorted(observations.items()), skipped


def _http_get(url: str, *, timeout: int = TIMEOUT_SECONDS, attempts: int = ATTEMPTS,
              sleep: Callable[[float], None] = time.sleep) -> HttpBody:
    """The response body with its status and content type; retries network errors, 429 and 5xx."""
    last: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"})
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
                body = HttpBody(response.read())
                body.status = getattr(response, "status", None)
                headers = getattr(response, "headers", None)
                body.content_type = headers.get("Content-Type") if headers is not None else None
                return body
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"FRED refused {raw_capture.safe_url(url)}: HTTP {error.code}") from None
            last = error
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            last = error
        if attempt + 1 < attempts:
            sleep(2 ** (attempt + 1))
    raise RuntimeError(f"FRED did not answer after {attempts} attempts: {raw_capture.safe_url(url)}") from last


def fetch_fred(series: Series, start: date, *, getter: Callable[..., bytes] | None = None) -> dict[str, Any]:
    """Download one FRED series from start on, capture the raw CSV, then parse it."""
    url = fred_url(series.series_id, start)
    requested_at = raw_capture.utc_now()
    body = (getter or _http_get)(url)
    # Raw first, exactly as received, whether or not it parses.
    blob = raw_capture.capture_http("fred-series", FRED_PROVIDER, url=url, body=body,
                                    key=raw_capture.request_key(series.series_id, start.isoformat()),
                                    extension="csv", requested_at=requested_at,
                                    licence=f"{series.licence}; {series.citation}")
    observations, skipped = parse_fred_csv(body, series.series_id)
    return {"url": url, "observations": observations, "skipped_missing": skipped, "blob": blob}


def fetch_cnh(start: date, end: date, *, fetch: Callable[..., Any] | None = None) -> dict[str, Any]:
    """USD/CNH daily closes through market_data's chart fetch, which captures to yahoo-chart."""
    url, rows = (fetch or market_data.fetch_daily_closes)(CNH_INSTRUMENT, start, end)
    return {"url": url, "observations": [(str(day), float(value)) for day, value in rows],
            "skipped_missing": 0, "blob": None}


def checked(series: Series, observations: Iterable[tuple[str, float]], start: date,
            end: date) -> list[tuple[str, float]]:
    """The observations from start to end, each in its unit's plausible range (else ValueError)."""
    low, high = RANGES[series.units]
    kept = []
    for day, value in observations:
        if not start.isoformat() <= day <= end.isoformat():
            continue
        if not math.isfinite(value) or not low < value <= high:
            raise ValueError(f"{series.series_id} is {value} on {day}, outside {low} to {high} {series.units}")
        kept.append((day, float(value)))
    return kept


# ---------------------------------------------------------------- silver


LATEST_SQL = (
    "SELECT DISTINCT ON (observation_date) observation_date, value, vintage FROM macro_series "
    "WHERE series_id = ? AND observation_date BETWEEN ? AND ? ORDER BY observation_date, vintage DESC")
UPSERT_SQL = """
INSERT INTO macro_series (series_id, observation_date, value, units, source, vintage, fetched_at)
SELECT ?, r.observation_date, r.value, ?, ?, ?, ?
FROM unnest(?::text[], ?::float8[]) AS r(observation_date, value)
ON CONFLICT (tenant_id, series_id, observation_date, vintage) DO UPDATE SET
    value = excluded.value, units = excluded.units, source = excluded.source, fetched_at = excluded.fetched_at
"""


def changed(observations: Sequence[tuple[str, float]],
            latest: Mapping[str, tuple[float, str]]) -> list[tuple[str, float]]:
    """The observations to store: dates not stored yet, and values that differ from the latest vintage."""
    return [(day, value) for day, value in observations
            if day not in latest or latest[day][0] != value]


def store_series(connection: Any, series: Series, observations: Sequence[tuple[str, float]], *,
                 vintage: str, fetched_at: str) -> dict[str, int]:
    """Write one series in one transaction. Returns how many observations were new, revised or unchanged."""
    with transaction(connection):
        # One writer per series at a time, so two runs never interleave their reads and writes.
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"macro_series:{series.series_id}",))
        latest: dict[str, tuple[float, str]] = {}
        if observations:
            rows = connection.execute(LATEST_SQL, (series.series_id, observations[0][0],
                                                   observations[-1][0])).fetchall()
            latest = {str(row["observation_date"]): (float(row["value"]), str(row["vintage"])) for row in rows}
        writes = changed(observations, latest)
        if writes:
            connection.execute(UPSERT_SQL, (series.series_id, series.units, series.source, vintage, fetched_at,
                                            [day for day, _ in writes], [value for _, value in writes]))
    revised = sum(1 for day, _ in writes if day in latest)
    return {"observations": len(observations), "new": len(writes) - revised, "revised": revised,
            "unchanged": len(observations) - len(writes)}


def relation_present(connection: Any) -> bool:
    """False until the held migration has created the table (the job then exits EXIT_HELD)."""
    row = connection.execute("SELECT to_regclass(?) IS NOT NULL AS present", (RELATION,)).fetchone()
    connection.rollback()
    return bool(row["present"] if isinstance(row, Mapping) else row[0])


def load(connection: Any, *, start: date = DEFAULT_START, today: date | None = None,
         environ: Mapping[str, str] | None = None, getter: Callable[..., bytes] | None = None,
         cnh_fetch: Callable[..., Any] | None = None, stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Fetch, capture and store every series this run may keep, one transaction each."""
    today = today or datetime.now(timezone.utc).date()
    vintage, fetched_at = today.isoformat(), utc_now_iso()
    chosen, left_out = series_to_load(environ)
    loaded, failed, not_started = [], [], []
    for series in chosen:
        if stop is not None and stop():
            not_started.append({"series_id": series.series_id, "reason": "the daily window began"})
            continue
        try:
            if series.source == "fred":
                fetched, end = fetch_fred(series, start, getter=getter), today
            else:
                # Completed days only: today's FX bar is still moving.
                end = today - timedelta(days=1)
                fetched = fetch_cnh(start, end, fetch=cnh_fetch)
            if not fetched["observations"]:
                raise ValueError("the response has no observations at all")
            observations = checked(series, fetched["observations"], start, end)
            counts = store_series(connection, series, observations, vintage=vintage, fetched_at=fetched_at)
        except Exception as error:  # noqa: BLE001 - one series never stops the others
            connection.rollback()
            failed.append({"series_id": series.series_id, "error": f"{type(error).__name__}: {error}"[:500]})
            continue
        loaded.append({"series_id": series.series_id, "source": series.source, "units": series.units,
                       "licence": series.licence, "source_url": raw_capture.safe_url(fetched["url"]),
                       "skipped_missing": fetched["skipped_missing"],
                       "first_date": observations[0][0] if observations else None,
                       "latest_date": observations[-1][0] if observations else None, **counts})
    return {"vintage": vintage, "start": start.isoformat(), "series": loaded, "failed": failed,
            "not_started": not_started, "not_loaded_for_licence": left_out,
            "rows_stored": sum(item["new"] + item["revised"] for item in loaded)}
