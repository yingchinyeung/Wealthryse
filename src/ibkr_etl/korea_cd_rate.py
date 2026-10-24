"""Korea's won risk-free rate: the 91-day CD yield from the Bank of Korea (ECOS).

A Korean-won account's Sharpe and Sortino use this rate, the way USD accounts
use the 3-month Treasury bill and the HKD portfolios use 1-month HIBOR (owner's
choice, 21 Oct 2026). The Bank of Korea publishes the 91-day certificate of
deposit yield each business day in its market interest rates table (817Y002,
daily), in percent a year.

The table's item for the CD is looked up by name on every run rather than by a
fixed code, and each returned row must carry that same item, so a renumbered
table stops the load instead of storing another rate. The ECOS key is read
from BOK_ECOS_API_KEY and is never written to a log or a raw capture.
"""

from __future__ import annotations

import csv
import io
import json
import math
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from . import raw_capture
from .reference_data import _get
from .util import utc_now_iso

API_BASE = "https://ecos.bok.or.kr/api"
STAT_CODE = "817Y002"  # market interest rates, daily
SERIES_ID = "BOK-CD-91D"
CURRENCY = "KRW"
TENOR = "91-day"
PROVIDER = "Bank of Korea ECOS (817Y002 market interest rates, CD 91-day)"
KEY_ENV = "BOK_ECOS_API_KEY"
PAGE_SIZE = 1000


@dataclass(frozen=True)
class CdObservation:
    rate_date: str
    annual_rate: float


def _url(service: str, key: str, *parts: str, first: int = 1, last: int = PAGE_SIZE) -> str:
    quoted = [urllib.parse.quote(str(part), safe="") for part in (key, "json", "kr", first, last, *parts)]
    return "/".join((API_BASE, service, *quoted))


def _redacted(url: str, key: str) -> str:
    return url.replace(urllib.parse.quote(key, safe=""), "KEY")


def _payload(body: bytes, service: str) -> list[dict[str, Any]]:
    data = json.loads(body)
    if service not in data:
        result = data.get("RESULT") or {}
        code, message = str(result.get("CODE") or ""), str(result.get("MESSAGE") or "")
        if code == "INFO-200":  # ECOS: no data for the request
            return []
        raise RuntimeError(f"Bank of Korea ECOS answered {code or 'without data'}: {message[:200]}")
    rows = data[service].get("row") or []
    if not isinstance(rows, list):
        raise RuntimeError("Bank of Korea ECOS rows are not a list")
    return rows


def cd_item_code(rows: list[dict[str, Any]]) -> tuple[str, str]:
    """The daily CD 91-day item in the 817Y002 item list: (code, name)."""

    matches = [
        (str(row.get("ITEM_CODE") or ""), str(row.get("ITEM_NAME") or ""))
        for row in rows
        if str(row.get("CYCLE") or "D") == "D"
        and "CD" in str(row.get("ITEM_NAME") or "").upper()
        and "91" in str(row.get("ITEM_NAME") or "")
    ]
    matches = list(dict.fromkeys(matches))
    if len(matches) != 1 or not matches[0][0]:
        raise RuntimeError(f"Expected one daily CD 91-day item in {STAT_CODE}, found {len(matches)}")
    return matches[0]


def parse_cd_rows(rows: list[dict[str, Any]], item_code: str) -> list[CdObservation]:
    """Dated CD yields from StatisticSearch rows, validated and in date order."""

    by_date: dict[str, CdObservation] = {}
    for row in rows:
        if str(row.get("ITEM_CODE1") or "") != item_code:
            raise RuntimeError(f"Bank of Korea ECOS returned item {row.get('ITEM_CODE1')!r}, not {item_code}")
        value = str(row.get("DATA_VALUE") or "").strip()
        if not value:
            continue
        rate_date = datetime.strptime(str(row.get("TIME") or ""), "%Y%m%d").date().isoformat()
        annual_rate = float(value) / 100.0
        if not math.isfinite(annual_rate) or not 0.0 <= annual_rate <= 0.30:
            raise ValueError(f"CD 91-day yield is outside the validation range on {rate_date}")
        observation = CdObservation(rate_date, annual_rate)
        if by_date.get(rate_date, observation) != observation:
            raise ValueError(f"Conflicting CD 91-day yields for {rate_date}")
        by_date[rate_date] = observation
    return [by_date[day] for day in sorted(by_date)]


def fetch_cd_rates(key: str, start_date: date, end_date: date, *, timeout: int = 45,
                   get: Callable[..., bytes] = _get) -> tuple[str, list[CdObservation]]:
    if not key:
        raise RuntimeError(f"{KEY_ENV} is not set")
    if end_date < start_date:
        raise ValueError("Risk-free end date precedes start date")
    items = _payload(get(_url("StatisticItemList", key, STAT_CODE), timeout=timeout), "StatisticItemList")
    item_code, _ = cd_item_code(items)
    observations: list[CdObservation] = []
    first = 1
    while True:
        url = _url("StatisticSearch", key, STAT_CODE, "D", start_date.strftime("%Y%m%d"),
                   end_date.strftime("%Y%m%d"), item_code, first=first, last=first + PAGE_SIZE - 1)
        requested_at = raw_capture.utc_now()
        body = get(url, timeout=timeout)
        raw_capture.capture_http("bok-cd-91d", PROVIDER, url=_redacted(url, key), body=body,
                                 key=f"{start_date:%Y%m%d}-{end_date:%Y%m%d}-{first}", extension="json",
                                 requested_at=requested_at)
        rows = _payload(body, "StatisticSearch")
        observations.extend(parse_cd_rows(rows, item_code))
        if len(rows) < PAGE_SIZE:
            break
        first += PAGE_SIZE
    if not observations:
        raise RuntimeError("No CD 91-day yields fall within the requested period")
    dates = [o.rate_date for o in observations]
    if len(dates) != len(set(dates)):
        raise ValueError("Bank of Korea ECOS repeated a CD 91-day date across pages")
    return item_code, sorted(observations, key=lambda o: o.rate_date)


def refresh_cd_rates(connection: Any, *, key: str, start_date: date, end_date: date | None = None,
                     timeout: int = 45, get: Callable[..., bytes] = _get) -> dict[str, Any]:
    """Idempotently load the validated daily CD 91-day yields into risk_free_rates."""

    end = end_date or date.today()
    item_code, observations = fetch_cd_rates(key, start_date, end, timeout=timeout, get=get)
    fetched_at = utc_now_iso()
    try:
        for observation in observations:
            connection.execute(
                """
                INSERT INTO risk_free_rates(
                    rate_date, series_id, currency, tenor, annual_rate,
                    day_count_convention, provider, fetched_at, quality_status
                ) VALUES (?, ?, ?, ?, ?, 'annualized-money-market-yield', ?, ?, 'pass')
                ON CONFLICT(tenant_id, rate_date, series_id) DO UPDATE SET
                    currency = excluded.currency,
                    tenor = excluded.tenor,
                    annual_rate = excluded.annual_rate,
                    day_count_convention = excluded.day_count_convention,
                    provider = excluded.provider,
                    fetched_at = excluded.fetched_at,
                    quality_status = excluded.quality_status
                """,
                (observation.rate_date, SERIES_ID, CURRENCY, TENOR, observation.annual_rate, PROVIDER, fetched_at),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    latest = observations[-1]
    return {
        "series_id": SERIES_ID,
        "currency": CURRENCY,
        "item_code": item_code,
        "provider": PROVIDER,
        "requested_start": start_date.isoformat(),
        "requested_end": end.isoformat(),
        "observations_loaded": len(observations),
        "latest_observation_date": latest.rate_date,
        "latest_annual_rate": latest.annual_rate,
        "quality_status": "pass",
    }


# No-key source: the OECD's monthly Korea 3-month CD rate, as republished by FRED. It is the
# month's average, so each month is stored on its last day: a figure is never used before the
# month it averages is over. It runs one to two months behind; risk_snapshots stops using a
# won rate that is more than OWN_RATE_MAX_AGE_DAYS old.
FRED_SERIES = "IR3TCD01KRM156N"
FRED_URL = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={FRED_SERIES}"
MONTHLY_SERIES_ID = "OECD-KR-CD-3M"
MONTHLY_TENOR = "3-month"
MONTHLY_PROVIDER = f"OECD Main Economic Indicators via FRED ({FRED_SERIES}, Korea 3-month CD, monthly average)"


def _month_end_observation(month: date, value: str, label: str) -> CdObservation:
    following = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    rate_date = date.fromordinal(following.toordinal() - 1).isoformat()
    annual_rate = round(float(value) / 100.0, 8)
    if not math.isfinite(annual_rate) or not 0.0 <= annual_rate <= 0.30:
        raise ValueError(f"Korea CD rate is outside the validation range for {label}")
    return CdObservation(rate_date, annual_rate)


# The OECD's own keyless SDMX API, FRED's source for the series above. FRED's CSV endpoint
# timed out from Azure on 22 and 23 Oct even with retries, so the OECD is asked first and
# FRED is kept as the fallback. Korea's short-term rate in this table is the 91-day CD
# (measure IR3TIB, "3-month interbank or equivalent", percent a year, monthly average).
OECD_DATAFLOW = "OECD.SDD.STES,DSD_STES@DF_FINMARK,4.0"
OECD_KEY = "KOR.M.IR3TIB.PA....."
OECD_MEASURE = "IR3TIB"
OECD_PROVIDER = "OECD SDMX (DF_FINMARK IR3TIB, Korea 3-month CD, monthly average)"


def oecd_url(start: date) -> str:
    return (f"https://sdmx.oecd.org/public/rest/data/{OECD_DATAFLOW}/{OECD_KEY}"
            f"?startPeriod={start:%Y-%m}&dimensionAtObservation=AllDimensions&format=csvfile")


def parse_oecd_csv(body: bytes) -> list[CdObservation]:
    """Month-end dated won CD rates from the OECD's SDMX-CSV answer, validated."""

    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    fields = set(reader.fieldnames or ())
    if not {"TIME_PERIOD", "OBS_VALUE"} <= fields:
        raise RuntimeError(f"OECD answer is not SDMX-CSV: columns {sorted(fields)[:12]}")
    expected = {"REF_AREA": "KOR", "FREQ": "M", "MEASURE": OECD_MEASURE}
    observations: dict[str, CdObservation] = {}
    for row in reader:
        for column, value in expected.items():
            if column in fields and (row.get(column) or "").strip() != value:
                raise RuntimeError(f"OECD returned {column}={row.get(column)!r}, not {value}")
        value = (row.get("OBS_VALUE") or "").strip()
        if not value:
            continue
        period = (row.get("TIME_PERIOD") or "").strip()
        month = datetime.strptime(period, "%Y-%m").date()
        observation = _month_end_observation(month, value, period)
        if observations.get(observation.rate_date, observation) != observation:
            raise ValueError(f"Conflicting OECD Korea CD rates for {period}")
        observations[observation.rate_date] = observation
    if not observations:
        raise RuntimeError("OECD returned no Korea CD observations")
    return [observations[day] for day in sorted(observations)]


def _fetch_oecd(get: Callable[..., bytes], start: date, *, timeout: int) -> tuple[str, bytes, list[CdObservation]]:
    url = oecd_url(start)
    requested_at = raw_capture.utc_now()
    body = get(url, timeout=timeout)
    raw_capture.capture_http("oecd-kr-cd-3m", OECD_PROVIDER, url=url, body=body,
                             key=f"oecd-{date.today():%Y%m%d}", extension="csv", requested_at=requested_at)
    return url, body, parse_oecd_csv(body)


def parse_monthly_csv(body: bytes) -> list[CdObservation]:
    """Month-end dated won CD rates from FRED's CSV, validated and in date order."""

    lines = [line.strip() for line in body.decode("utf-8-sig").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("FRED returned an empty file")
    header = [part.strip() for part in lines[0].split(",")]
    if len(header) != 2 or header[0].lower() not in {"date", "observation_date"} or header[1] != FRED_SERIES:
        raise RuntimeError(f"FRED file is not {FRED_SERIES}: header {lines[0][:80]!r}")
    observations: dict[str, CdObservation] = {}
    for line in lines[1:]:
        day_text, value = (part.strip() for part in line.split(",", 1))
        if value in {"", "."}:
            continue
        month = date.fromisoformat(day_text)
        if month.day != 1:
            raise RuntimeError(f"FRED monthly observation not dated on the 1st: {day_text}")
        observation = _month_end_observation(month, value, day_text)
        observations[observation.rate_date] = observation
    if not observations:
        raise RuntimeError(f"FRED {FRED_SERIES} has no observations")
    return [observations[day] for day in sorted(observations)]


# FRED's CSV endpoint is sometimes slow to answer (a 45 s read timeout on 22 Oct), so the
# fetch is tried a few times before the run reports the won rate as failed.
FRED_TRIES = 3
FRED_RETRY_SECONDS = (5, 20)


def _fetch_fred(get: Callable[..., bytes], *, timeout: int, sleep: Callable[[float], None]) -> bytes:
    for attempt in range(FRED_TRIES):
        try:
            return get(FRED_URL, timeout=timeout)
        except OSError:  # timeouts, connection resets and HTTP errors (urllib's are OSErrors)
            if attempt == FRED_TRIES - 1:
                raise
            sleep(FRED_RETRY_SECONDS[min(attempt, len(FRED_RETRY_SECONDS) - 1)])
    raise AssertionError("unreachable")


def refresh_monthly_cd_rates(connection: Any, *, start_date: date, end_date: date | None = None,
                             timeout: int = 90, get: Callable[..., bytes] = _get,
                             sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Idempotently load month-end won CD rates into risk_free_rates: OECD first, then FRED."""

    end = end_date or date.today()
    oecd_error = None
    try:
        _, _, parsed = _fetch_oecd(get, start_date, timeout=timeout)
        source, provider = "oecd-sdmx", OECD_PROVIDER
    except Exception as error:  # noqa: BLE001 - FRED is tried next and the error is reported
        oecd_error = f"{type(error).__name__}: {' '.join(str(error).split())[:200]}"
        requested_at = raw_capture.utc_now()
        body = _fetch_fred(get, timeout=timeout, sleep=sleep)
        raw_capture.capture_http("oecd-kr-cd-3m", MONTHLY_PROVIDER, url=FRED_URL, body=body,
                                 key=end.strftime("%Y%m%d"), extension="csv", requested_at=requested_at)
        parsed = parse_monthly_csv(body)
        source, provider = "fred", MONTHLY_PROVIDER
    observations = [o for o in parsed if start_date.isoformat() <= o.rate_date <= end.isoformat()]
    if not observations:
        raise RuntimeError("No Korea CD monthly rates fall within the requested period")
    fetched_at = utc_now_iso()
    try:
        for observation in observations:
            connection.execute(
                """
                INSERT INTO risk_free_rates(
                    rate_date, series_id, currency, tenor, annual_rate,
                    day_count_convention, provider, fetched_at, quality_status
                ) VALUES (?, ?, ?, ?, ?, 'monthly-average-annual-yield', ?, ?, 'pass')
                ON CONFLICT(tenant_id, rate_date, series_id) DO UPDATE SET
                    currency = excluded.currency,
                    tenor = excluded.tenor,
                    annual_rate = excluded.annual_rate,
                    day_count_convention = excluded.day_count_convention,
                    provider = excluded.provider,
                    fetched_at = excluded.fetched_at,
                    quality_status = excluded.quality_status
                """,
                (observation.rate_date, MONTHLY_SERIES_ID, CURRENCY, MONTHLY_TENOR, observation.annual_rate,
                 provider, fetched_at),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    latest = observations[-1]
    return {
        "series_id": MONTHLY_SERIES_ID,
        "currency": CURRENCY,
        "provider": provider,
        "source": source,
        **({"oecd_error": oecd_error} if oecd_error else {}),
        "requested_start": start_date.isoformat(),
        "requested_end": end.isoformat(),
        "observations_loaded": len(observations),
        "latest_observation_date": latest.rate_date,
        "latest_annual_rate": latest.annual_rate,
        "quality_status": "pass",
    }
