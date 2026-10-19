"""Daily traded volumes in silver (docs/PHASE3_DATA.md, job ibkrv2stg-daily-volumes).

No new fetch. The Yahoo chart responses that market_data.fetch_daily_closes already keeps in
bronze-market-data (feed yahoo-chart) carry each day's volume beside its close. This module reads
those captures and upserts silver.daily_volumes. market_data itself is not changed, so with the
load off (DAILY_VOLUMES_LOAD unset or off) nothing anywhere behaves differently, and the four jobs
the publish gate binds never write this table.

What is read, and how:

- The instruments are the chart rows of market_data_prices (source "Yahoo Finance chart API"),
  each with the symbol and currency of its newest row.
- A capture belongs to an instrument when its request key starts with the instrument's symbol
  and the response's own symbol and currency match. Dates are the exchange's own, as
  fetch_daily_closes reads them.
- A day without a usable volume (null, not finite or below zero) is skipped, never stored as
  NULL. A zero is kept as Yahoo reports it (exchange holidays); readers skip zeros.
- A bar still trading when the capture was made (requested before that day's regular session
  ended) is skipped; the next capture has the full day.
- When several captures have the same day, the newest capture wins, in the run and in the table:
  a row is only replaced by a newer capture with a different volume, so loading the same or an
  older capture again changes nothing.

The daily run reads the last DEFAULT_DAYS dated folders. A backfill reads every capture kept
(dated folders and history/) and keeps the days in its date range. Each instrument is written in
one transaction. The table comes from a held migration: until it has run, the job stops before
reading anything and exits EXIT_HELD.

The holdings' own fetch (fetch_and_load; the job's --fetch, which does nothing unless
DAILY_VOLUMES_FETCH=on). The chart fetch above asks only for the benchmarks, and the job that runs
it is bound by the publish gate, so this job fetches the holdings itself:

- The instruments are those held (quantity not zero) by any physical account on its latest
  position date, read from silver: subject_position_series, instruments and the sector lookup's
  instrument_classifications. Each is stored under its IBKR conid as instrument_id.
- The Yahoo symbol is worked out as the platform already does it: sector_classification's
  candidate_symbols, tried in order as crisis_replay tries them, with the symbol the sector lookup
  matched first when it is one of those candidates. A holding that is not a listed share (IBKR
  asset category other than STK), a receivable (.REC) or a contract without a symbol is listed as
  not mapped, and one Yahoo has no chart for in the holding's currency as not found. Neither is
  guessed.
- Each request is market_data.fetch_daily_closes, so raw_capture keeps the response as received in
  bronze-market-data (yahoo-chart) first, and market_data's retries and backoff apply. The volumes
  are then parsed from those captures, read back from bronze, exactly as the load above does.
- The window is the last year for a holding with no volumes stored yet, then the last 10 days (from
  the day before its latest stored day when that is older, so a pause leaves no gap).
- Pacing: a fixed pause between two Yahoo requests, at most max_symbols requests and budget_seconds
  of fetching per run. The rest wait for the next run, which starts with the least recently loaded.
"""
from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import time
import urllib.error
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from . import market_data, raw_capture
from .sector_classification import YAHOO_PROVIDER, candidate_symbols

SETTING = "DAILY_VOLUMES_LOAD"  # off (default) or on
CONTAINER_ENV = "DAILY_VOLUMES_CONTAINER"  # where the captures are; default bronze-market-data
DEFAULT_CONTAINER = "bronze-market-data"
TABLE = "silver.daily_volumes"
RELATION = "daily_volumes"  # the name readers and the job use (the public view, or silver first)
FEED = "yahoo-chart"
SOURCE = "yahoo-chart"
CHART_SOURCE = "Yahoo Finance chart API"  # market_data_prices.source of the rows market_data writes
DEFAULT_DAYS = 3  # dated capture folders the daily run reads: today and the two days before
MAX_CAPTURE_BYTES = 16 * 1024 * 1024
EXIT_HELD = 65  # the held migration has not run: nothing was read or written

FETCH_SETTING = "DAILY_VOLUMES_FETCH"  # off (default) or on: the holdings' own chart fetch (--fetch)
FETCH_FIRST_DAYS = 365  # a holding with no volumes stored yet: the last year
FETCH_RECENT_DAYS = 10  # after that, the last 10 days
DEFAULT_MAX_SYMBOLS = 60  # Yahoo requests per run at most
PACE_SECONDS = 1.0  # the fixed pause between two Yahoo requests
FETCH_BUDGET_SECONDS = 480.0  # no new request after this, so the 15-minute run has time left to load
FETCH_TIMEOUT_SECONDS = 20  # per attempt; market_data makes three, with backoff
VOLUME_ASSET_CATEGORIES = frozenset({"STK"})  # IBKR's category for listed shares and ETFs
# The silver objects the fetch reads (lineage inputs).
FETCH_INPUTS = ("silver.subject_position_series", "silver.instruments", "silver.instrument_classifications")

INSTRUMENTS_SQL = """
SELECT DISTINCT ON (instrument_id) instrument_id, symbol, currency
FROM market_data_prices
WHERE source = ?
ORDER BY instrument_id, price_date DESC
"""
UPSERT_SQL = """
INSERT INTO daily_volumes AS v (instrument_id, price_date, volume, source, source_url, fetched_at)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (tenant_id, instrument_id, price_date) DO UPDATE SET
    volume = excluded.volume, source = excluded.source, source_url = excluded.source_url,
    fetched_at = excluded.fetched_at
WHERE excluded.fetched_at > v.fetched_at AND excluded.volume IS DISTINCT FROM v.volume
"""
# Every instrument held by a physical account on that account's latest position date (silver only).
HOLDINGS_SQL = """
WITH latest AS (
    SELECT subject_id, MAX(snapshot_date) AS snapshot_date
    FROM subject_position_series
    WHERE source_kind = 'physical_account'
    GROUP BY subject_id
)
SELECT p.conid::text AS conid,
       MIN(COALESCE(i.symbol, '')) AS symbol,
       MIN(COALESCE(i.listing_exchange, '')) AS listing_exchange,
       MIN(UPPER(COALESCE(NULLIF(p.currency, ''), i.currency, ''))) AS currency,
       MIN(UPPER(COALESCE(i.asset_category, ''))) AS asset_category,
       MIN(c.lookup_symbol) AS lookup_symbol
FROM subject_position_series p
JOIN latest l ON l.subject_id = p.subject_id AND l.snapshot_date = p.snapshot_date
LEFT JOIN instruments i ON i.conid = p.conid
LEFT JOIN instrument_classifications c
  ON c.conid = p.conid AND c.provider = ? AND c.status = 'classified' AND COALESCE(c.lookup_symbol, '') <> ''
WHERE p.source_kind = 'physical_account'
  AND CAST(COALESCE(NULLIF(p.quantity_text, ''), '0') AS NUMERIC) <> 0
GROUP BY p.conid
ORDER BY p.conid
"""
LOADED_SQL = """
SELECT instrument_id, MAX(price_date) AS latest
FROM daily_volumes
WHERE instrument_id = ANY(?::text[])
GROUP BY instrument_id
"""


class CaptureError(ValueError):
    """A capture that cannot be read as a Yahoo chart response."""


def load_enabled(environ: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return str(environ.get(SETTING, "")).strip().lower() == "on"


def fetch_enabled(environ: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return str(environ.get(FETCH_SETTING, "")).strip().lower() == "on"


# ---------------------------------------------------------------- bronze


@dataclass(frozen=True)
class CaptureRef:
    """One stored chart response: its blob name, when it was requested and the provider URL."""

    name: str
    stored_at: datetime
    source_url: str = ""


class BronzeReader(Protocol):
    container: str

    def list(self, prefix: str) -> Iterable[CaptureRef]:
        """The captures whose names start with prefix."""

    def read(self, name: str) -> bytes:
        """One capture's bytes."""


def _instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).strip())
        except (TypeError, ValueError):
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


class AzureBronze:
    """The bronze-market-data container, read with the job's managed identity. Read only."""

    def __init__(self, account_url: str, container: str = DEFAULT_CONTAINER) -> None:
        if not account_url:
            raise RuntimeError("Reading the chart captures needs AZURE_STORAGE_ACCOUNT_URL")
        from .cloud_storage import container_client

        self.container = container
        self._client = container_client(account_url, container, connection_timeout=10, read_timeout=60)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "AzureBronze":
        environ = os.environ if environ is None else environ
        return cls(str(environ.get(raw_capture.ACCOUNT_URL_ENV, "")).strip(),
                   str(environ.get(CONTAINER_ENV, "")).strip() or DEFAULT_CONTAINER)

    def list(self, prefix: str) -> list[CaptureRef]:
        found = []
        for blob in self._client.list_blobs(name_starts_with=prefix, include=["metadata"]):
            metadata = {str(k).lower(): v for k, v in (getattr(blob, "metadata", None) or {}).items()}
            stored = _instant(metadata.get("requested_at")) or _instant(getattr(blob, "last_modified", None))
            if stored is None:
                continue
            found.append(CaptureRef(blob.name, stored, str(metadata.get("source_url") or "")))
        return found

    def read(self, name: str) -> bytes:
        client = self._client.get_blob_client(name)
        if int(client.get_blob_properties().size) > MAX_CAPTURE_BYTES:
            raise CaptureError(f"{name} is larger than {MAX_CAPTURE_BYTES} bytes")
        return bytes(client.download_blob(max_concurrency=1).readall())


def capture_folder_day(name: str) -> date | None:
    """The UTC request date of a dated capture (<feed>/<yyyy>/<mm>/<dd>/...), None for history/."""
    parts = name.split("/")
    if len(parts) < 5 or not all(part.isdigit() for part in parts[1:4]):
        return None
    try:
        return date(int(parts[1]), int(parts[2]), int(parts[3]))
    except ValueError:
        return None


def capture_names_match(name: str, symbol: str) -> bool:
    """Whether a capture's request key is for this symbol (fetch_daily_closes names it
    request_key(symbol, start, end, "1d"), so the key starts with the symbol's own key)."""
    return name.rsplit("/", 1)[-1].startswith(raw_capture.request_key(symbol) + "_")


# ---------------------------------------------------------------- parsing


@dataclass(frozen=True)
class ChartVolumes:
    symbol: str
    currency: str
    volumes: list[tuple[str, float]]  # (exchange-local date, shares traded), oldest first
    skipped: int = 0  # days with no usable volume, or still trading when captured


def parse_chart_volumes(content: bytes, *, requested_at: datetime | None = None) -> ChartVolumes:
    """The daily volumes in one Yahoo chart response, read as market_data.fetch_daily_closes reads
    its closes. With requested_at, a bar still trading at that moment is left out."""
    try:
        payload = json.loads(bytes(content).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise CaptureError(f"not JSON: {error}") from error
    chart = payload.get("chart") if isinstance(payload, Mapping) else None
    if not isinstance(chart, Mapping):
        raise CaptureError("no chart in the response")
    if chart.get("error"):
        raise CaptureError(f"the provider rejected the request: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise CaptureError("no chart result")
    result = results[0]
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    blocks = (result.get("indicators") or {}).get("quote") or []
    volumes = (blocks[0].get("volume") if blocks else None) or []
    if len(timestamps) != len(volumes):
        raise CaptureError("the volume array does not match the timestamps")
    try:
        zone = ZoneInfo(str(meta.get("exchangeTimezoneName") or "UTC"))
    except Exception:  # noqa: BLE001 - an unknown zone falls back to UTC, as market_data does
        zone = timezone.utc
    trading = _trading_day(meta, zone, requested_at)
    rows: dict[str, float] = {}
    skipped = 0
    for stamp, raw in zip(timestamps, volumes):
        try:
            day = datetime.fromtimestamp(int(stamp), timezone.utc).astimezone(zone).date()
            volume = float(raw)
        except (TypeError, ValueError, OverflowError, OSError):
            skipped += 1
            continue
        if not math.isfinite(volume) or volume < 0 or day == trading:
            skipped += 1
            continue
        rows[day.isoformat()] = volume
    return ChartVolumes(str(meta.get("symbol") or ""), str(meta.get("currency") or "").upper(),
                        sorted(rows.items()), skipped)


def _trading_day(meta: Mapping[str, Any], zone: Any, requested_at: datetime | None) -> date | None:
    """The exchange date whose regular session had not ended when the request was made, if any."""
    if requested_at is None:
        return None
    regular = ((meta.get("currentTradingPeriod") or {}).get("regular") or {})
    try:
        start, end = int(regular["start"]), int(regular["end"])
    except (KeyError, TypeError, ValueError):
        return None
    moment = requested_at.astimezone(timezone.utc).timestamp()
    if start <= moment < end:
        return datetime.fromtimestamp(start, timezone.utc).astimezone(zone).date()
    return None


# ---------------------------------------------------------------- loading


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    symbol: str
    currency: str


@dataclass
class InstrumentResult:
    instrument: Instrument
    captures: int = 0
    rows: int = 0
    written: int = 0
    skipped_days: int = 0
    first_date: str | None = None
    latest_date: str | None = None
    unreadable: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        return {"instrument_id": self.instrument.instrument_id, "symbol": self.instrument.symbol,
                "captures": self.captures, "rows": self.rows, "written": self.written,
                "unchanged": self.rows - self.written if self.error is None else 0,
                "skipped_days": self.skipped_days, "first_date": self.first_date,
                "latest_date": self.latest_date, "unreadable_captures": self.unreadable,
                **({"error": self.error} if self.error else {})}


def table_exists(connection: Any) -> bool:
    """Whether the held migration has run (the name resolves for this login)."""
    row = connection.execute("SELECT to_regclass(?) AS relation", (RELATION,)).fetchone()
    connection.rollback()
    return row is not None and row["relation"] is not None


def instruments(connection: Any, only: Sequence[str] = ()) -> list[Instrument]:
    rows = connection.execute(INSTRUMENTS_SQL, (CHART_SOURCE,)).fetchall()
    connection.rollback()
    found = [Instrument(str(r["instrument_id"]), str(r["symbol"]), str(r["currency"]).upper()) for r in rows]
    return [item for item in found if not only or item.instrument_id in only]


def list_captures(bronze: BronzeReader, *, today: date, days: int = DEFAULT_DAYS,
                  since: date | None = None) -> list[CaptureRef]:
    """The chart captures a run reads: the last `days` dated folders, or for a backfill from
    `since` every capture kept that can hold a day from then on (history/ included)."""
    if since is None:
        found = []
        for offset in range(max(1, days)):
            found += list(bronze.list(f"{FEED}/{today - timedelta(days=offset):%Y/%m/%d}/"))
        return found
    # A capture never holds a day after its request date (an Asian date can be a day ahead of UTC).
    earliest = since - timedelta(days=1)
    return [ref for ref in bronze.list(f"{FEED}/")
            if (capture_folder_day(ref.name) or earliest) >= earliest]


def _newest_volumes(instrument: Instrument, refs: Sequence[CaptureRef], bronze: BronzeReader,
                    result: InstrumentResult, record: Any) -> dict[str, tuple[float, CaptureRef]]:
    """Per day, the volume of the newest capture that has it."""
    chosen: dict[str, tuple[float, CaptureRef]] = {}
    for ref in sorted(refs, key=lambda item: (item.stored_at, item.name)):
        try:
            content = bronze.read(ref.name)
            parsed = parse_chart_volumes(content, requested_at=ref.stored_at)
        except Exception as error:  # noqa: BLE001 - one bad file never stops the others
            result.unreadable.append({"capture": ref.name, "reason": f"{type(error).__name__}: {error}"[:200]})
            continue
        if parsed.symbol != instrument.symbol:
            continue  # another symbol with the same request-key prefix
        if parsed.currency and parsed.currency != instrument.currency:
            result.unreadable.append({"capture": ref.name, "reason": f"currency {parsed.currency}, expected "
                                                                     f"{instrument.currency}"})
            continue
        result.captures += 1
        result.skipped_days += parsed.skipped
        record.input(f"blob://{bronze.container}/{ref.name}", sha256=hashlib.sha256(content).hexdigest(),
                     feed=FEED)
        for day, volume in parsed.volumes:
            chosen[day] = (volume, ref)
    return chosen


def _write(connection: Any, instrument: Instrument, rows: Sequence[tuple]) -> int:
    """One transaction for the instrument; returns the rows inserted or changed."""
    raw = getattr(connection, "_connection", connection)
    try:
        with raw.cursor() as cursor:
            cursor.executemany(UPSERT_SQL, rows)
            written = max(0, cursor.rowcount)
        connection.commit()
        return written
    except Exception:
        connection.rollback()
        raise


def _load_instruments(connection: Any, bronze: BronzeReader, chosen: Sequence[Instrument],
                      refs: Sequence[CaptureRef], *, since: date | None, until: date | None,
                      record: Any) -> list[InstrumentResult]:
    """Each instrument's volumes from its captures among refs, written in one transaction each."""
    results = []
    for instrument in chosen:
        result = InstrumentResult(instrument)
        mine = [ref for ref in refs if capture_names_match(ref.name, instrument.symbol)]
        volumes = _newest_volumes(instrument, mine, bronze, result, record)
        rows = []
        for day, (volume, ref) in sorted(volumes.items()):
            if since is not None and day < since.isoformat():
                continue
            if until is not None and day > until.isoformat():
                continue
            rows.append((instrument.instrument_id, day, volume, SOURCE, ref.source_url or
                         f"blob://{bronze.container}/{ref.name}",
                         ref.stored_at.astimezone(timezone.utc).isoformat(timespec="seconds")))
        result.rows = len(rows)
        if rows:
            result.first_date, result.latest_date = rows[0][1], rows[-1][1]
            try:
                result.written = _write(connection, instrument, rows)
            except Exception as error:  # noqa: BLE001 - one instrument never stops the others
                result.error = f"{type(error).__name__}: {error}"[:500]
                if _undefined_table(error):
                    raise
        results.append(result)
    return results


def load(connection: Any, bronze: BronzeReader, *, today: date | None = None, days: int = DEFAULT_DAYS,
         since: date | None = None, until: date | None = None, only: Sequence[str] = (),
         record: Any = None) -> dict[str, Any]:
    """Upsert silver.daily_volumes from the chart captures; returns the counts per instrument.

    `record` is the run's lineage record (lineage.current()); its inputs are the captures used."""
    if record is None:
        from . import lineage

        record = lineage.current()
    today = today or datetime.now(timezone.utc).date()
    chosen = instruments(connection, only)
    refs = list_captures(bronze, today=today, days=days, since=since)
    results = _load_instruments(connection, bronze, chosen, refs, since=since, until=until, record=record)
    written = sum(item.written for item in results)
    record.output(TABLE, rows=written)
    return {
        "mode": "backfill" if since is not None else "daily",
        "since": since.isoformat() if since else None, "until": until.isoformat() if until else None,
        "captures_listed": len(refs), "instruments": [item.summary() for item in results],
        "rows": sum(item.rows for item in results), "written": written,
        "failed": [item.instrument.instrument_id for item in results if item.error],
        "without_captures": [item.instrument.instrument_id for item in results if item.captures == 0],
    }


def _undefined_table(error: BaseException) -> bool:
    return getattr(error, "sqlstate", None) == "42P01"


# ---------------------------------------------------------------- the holdings' own fetch


@dataclass(frozen=True)
class Holding:
    """One instrument held by a physical account on its latest position date."""

    conid: str
    symbol: str  # IBKR's
    listing_exchange: str
    currency: str
    asset_category: str
    lookup_symbol: str = ""  # the Yahoo symbol the sector lookup matched, if any


def held_instruments(connection: Any, only: Sequence[str] = ()) -> list[Holding]:
    rows = connection.execute(HOLDINGS_SQL, (YAHOO_PROVIDER,)).fetchall()
    connection.rollback()
    found = [Holding(str(r["conid"]), str(r["symbol"] or "").strip(), str(r["listing_exchange"] or "").strip(),
                     str(r["currency"] or "").strip().upper(), str(r["asset_category"] or "").strip().upper(),
                     str(r["lookup_symbol"] or "").strip().upper()) for r in rows]
    return [item for item in found if not only or item.conid in only]


def yahoo_symbols(holding: Holding) -> tuple[list[str], str | None]:
    """The Yahoo symbols to ask for, in order, or no symbols and the reason there are none.

    They are sector_classification.candidate_symbols, as crisis_replay tries them, with the symbol
    the sector lookup matched moved first when it is one of them (an owner's override to another
    line, such as a warrant's underlying, is not). Nothing else is guessed."""
    symbol = holding.symbol.strip()
    if not symbol or symbol == holding.conid:
        return [], "no IBKR symbol for this contract in instruments"
    if symbol.upper().endswith(".REC"):
        return [], "a receivable (.REC), not a listed line"
    if holding.asset_category not in VOLUME_ASSET_CATEGORIES:
        return [], f"asset category {holding.asset_category or 'unknown'}: not a listed share or ETF"
    if not holding.currency:
        return [], "no currency"
    candidates = candidate_symbols(symbol, holding.listing_exchange, holding.currency)
    lookup = holding.lookup_symbol.strip().upper()
    if lookup in candidates:
        candidates = [lookup] + [candidate for candidate in candidates if candidate != lookup]
    return (candidates, None) if candidates else ([], "no Yahoo symbol for this contract")


def latest_loaded(connection: Any, instrument_ids: Sequence[str]) -> dict[str, str]:
    """instrument_id -> its latest stored price_date, for those with any."""
    if not instrument_ids:
        return {}
    rows = connection.execute(LOADED_SQL, (list(instrument_ids),)).fetchall()
    connection.rollback()
    return {str(r["instrument_id"]): str(r["latest"])[:10] for r in rows if r["latest"]}


def fetch_window(latest: str | None, today: date) -> tuple[date, date]:
    """(start, end) of a holding's chart request: the last year when none of its volumes are stored,
    else the last FETCH_RECENT_DAYS, or from the day before its latest stored day when that is
    older; never more than a year."""
    year = today - timedelta(days=FETCH_FIRST_DAYS)
    try:
        stored = date.fromisoformat(str(latest)[:10]) if latest else None
    except ValueError:
        stored = None
    if stored is None:
        return year, today
    return max(year, min(today - timedelta(days=FETCH_RECENT_DAYS), stored - timedelta(days=1))), today


def _refused(error: BaseException) -> bool:
    """Whether Yahoo answered without a chart for this symbol in this currency (so the next
    candidate is tried), rather than the request failing. fetch_daily_closes raises its own checks
    on a response without a cause; a request that kept failing carries the last error."""
    cause = error.__cause__
    if cause is None:
        return isinstance(error, RuntimeError)
    return isinstance(cause, urllib.error.HTTPError) and cause.code in (400, 404)


@dataclass
class HoldingFetch:
    holding: Holding
    candidates: list[str]
    reason: str | None = None  # why it is not mapped
    start: date | None = None
    end: date | None = None
    tried: list[str] = field(default_factory=list)
    yahoo_symbol: str | None = None
    captures: list[str] = field(default_factory=list)  # blob names this run kept for it
    # not_mapped, deferred, not_found, failed, not_captured, fetched, then loaded
    outcome: str = "deferred"
    error: str | None = None
    load: InstrumentResult | None = None

    def summary(self) -> dict[str, Any]:
        item: dict[str, Any] = {"instrument_id": self.holding.conid, "ibkr_symbol": self.holding.symbol,
                                "currency": self.holding.currency, "outcome": self.outcome,
                                "yahoo_symbol": self.yahoo_symbol, "tried": list(self.tried)}
        if self.start is not None and self.end is not None:
            item["window"] = [self.start.isoformat(), self.end.isoformat()]
        for key in ("reason", "error"):
            if getattr(self, key):
                item[key] = getattr(self, key)
        if self.captures:
            item["captured"] = list(self.captures)
        if self.load is not None:
            item.update({k: v for k, v in self.load.summary().items() if k not in ("instrument_id", "symbol")})
        return item


def _stop_reason(requests: int, max_symbols: int, elapsed: float, budget_seconds: float,
                 capture: raw_capture.RawCapture) -> str | None:
    if capture.disabled_reason:
        return f"raw capture is off for the rest of the run: {capture.disabled_reason}"
    if requests >= max_symbols:
        return f"the cap of {max_symbols} Yahoo requests per run"
    if elapsed >= budget_seconds:
        return f"the fetch time budget of {budget_seconds:g} seconds"
    return None


def _throttled(error: BaseException) -> bool:
    """Whether Yahoo was still asking us to slow down (HTTP 429) after market_data's retries."""
    cause = error.__cause__
    return isinstance(cause, urllib.error.HTTPError) and cause.code == 429


def _ask(fetcher: Callable[..., Any], item: HoldingFetch, candidate: str, container: str,
         observed: list[dict[str, Any]]) -> tuple[str, list[str], str | None]:
    """One chart request: ("fetched", the captures it left in the container, None), or
    ("refused" | "throttled" | "failed", [], why)."""
    holding = item.holding
    instrument = market_data.MarketInstrument(holding.conid, candidate, holding.symbol, holding.currency,
                                              "unadjusted_close")
    key = raw_capture.request_key(candidate, item.start.isoformat(), item.end.isoformat(), "1d")
    mark = len(observed)
    try:
        fetcher(instrument, item.start, item.end)
    except Exception as error:  # noqa: BLE001 - one holding never stops the others
        why = " ".join(f"{type(error).__name__}: {error}".split())[:300]
        return ("refused" if _refused(error) else "throttled" if _throttled(error) else "failed"), [], why
    kept = [str(record["blob"]) for record in observed[mark:]
            if record.get("feed") == FEED and record.get("container") == container
            and record.get("request_key") == key and record.get("blob")]
    return "fetched", kept, None


def fetch_and_load(connection: Any, bronze: BronzeReader, *, today: date | None = None, days: int = DEFAULT_DAYS,
                   only: Sequence[str] = (), max_symbols: int = DEFAULT_MAX_SYMBOLS,
                   pace_seconds: float = PACE_SECONDS, budget_seconds: float = FETCH_BUDGET_SECONDS,
                   fetcher: Callable[..., Any] | None = None, sleep: Callable[[float], None] = time.sleep,
                   clock: Callable[[], float] = time.monotonic, capture: raw_capture.RawCapture | None = None,
                   record: Any = None) -> dict[str, Any]:
    """Fetch the holdings' daily charts, each kept raw in bronze first, then load their volumes from
    those captures under the holding's conid. Returns the counts and every holding's outcome.

    fetcher defaults to market_data.fetch_daily_closes; capture to the process-wide raw capture,
    which must write to the container the load reads, or nothing is fetched."""
    if record is None:
        from . import lineage

        record = lineage.current()
    capture = raw_capture.default() if capture is None else capture
    fetcher = fetcher or functools.partial(market_data.fetch_daily_closes, timeout=FETCH_TIMEOUT_SECONDS)
    today = today or raw_capture.utc_now().astimezone(timezone.utc).date()
    limits = {"max_symbols": max_symbols, "pace_seconds": pace_seconds, "budget_seconds": budget_seconds}

    items = []
    for holding in held_instruments(connection, only):
        candidates, reason = yahoo_symbols(holding)
        items.append(HoldingFetch(holding, candidates, reason, outcome="not_mapped" if reason else "deferred"))
    mapped = [item for item in items if item.reason is None]
    stored = latest_loaded(connection, [item.holding.conid for item in mapped])
    for item in mapped:
        item.start, item.end = fetch_window(stored.get(item.holding.conid), today)
    # Least recently loaded first, so a capped run reaches every holding over the next runs.
    mapped.sort(key=lambda item: (item.holding.conid in stored, stored.get(item.holding.conid, ""),
                                  item.holding.conid))

    usable = capture.enabled and capture.container == bronze.container
    stop = None if usable else (
        f"raw capture writes to {capture.container or 'nowhere'!r}, the load reads {bronze.container!r}; set "
        f"{raw_capture.CONTAINER_ENV}={bronze.container} and {raw_capture.ACCOUNT_URL_ENV}")
    observed: list[dict[str, Any]] = []
    remove = raw_capture.add_observer(observed.append)
    answers: dict[tuple, tuple[str, list[str], str | None]] = {}  # one request per symbol, currency, window
    started, requests = clock(), 0
    try:
        for item in mapped:
            if stop:
                break  # this and the rest stay deferred
            for candidate in item.candidates:
                key = (candidate, item.holding.currency, item.start, item.end)
                if key not in answers:
                    stop = _stop_reason(requests, max_symbols, clock() - started, budget_seconds, capture)
                    if stop:
                        break
                    if requests:
                        sleep(pace_seconds)
                    requests += 1
                    answers[key] = _ask(fetcher, item, candidate, bronze.container, observed)
                item.tried.append(candidate)
                status, kept, why = answers[key]
                if status == "refused":
                    item.outcome, item.error = "not_found", why  # unless a later candidate answers
                    continue
                if status in ("failed", "throttled"):
                    item.outcome, item.error = "failed", why
                    if status == "throttled":
                        stop = "Yahoo answered 429 (too many requests); the rest wait for the next run"
                elif kept:
                    item.outcome, item.yahoo_symbol, item.captures, item.error = "fetched", candidate, kept, None
                else:
                    item.outcome, item.yahoo_symbol = "not_captured", candidate
                    item.error = "Yahoo answered but raw capture did not keep the response, so it is not parsed"
                break
            if stop and item.outcome == "not_found":
                item.outcome, item.error = "deferred", None  # it still had candidates to try
    finally:
        remove()

    # The parse reads the captures back from bronze, exactly as the load does.
    ready = [item for item in mapped if item.outcome == "fetched"]
    refs = list_captures(bronze, today=today, days=days) if ready else []
    listed = {ref.name for ref in refs}
    for item in ready:
        if not listed & set(item.captures):
            item.outcome, item.error = "not_captured", "the capture is not in the folders the load reads"
    ready = [item for item in ready if item.outcome == "fetched"]
    results = _load_instruments(connection, bronze, [Instrument(item.holding.conid, item.yahoo_symbol,
                                                                item.holding.currency) for item in ready],
                                refs, since=None, until=None, record=record)
    for item, result in zip(ready, results):
        item.load, item.outcome = result, "loaded"
    record.output(TABLE, rows=sum(result.written for result in results))
    return _fetch_summary(items, usable=usable, requests=requests, stop=stop, captures_listed=len(refs),
                          seconds=clock() - started, capture=capture, limits=limits)


def _fetch_summary(items: Sequence[HoldingFetch], *, usable: bool, requests: int, stop: str | None,
                   captures_listed: int, seconds: float, capture: raw_capture.RawCapture,
                   limits: Mapping[str, Any]) -> dict[str, Any]:
    def having(*outcomes: str) -> list[HoldingFetch]:
        return [item for item in items if item.outcome in outcomes]

    loaded = having("loaded")
    unreadable = sum(len(item.load.unreadable) for item in loaded)
    failed = having("failed", "not_captured") + [item for item in loaded if item.load.error]
    deferred = having("deferred")
    status = "capture_off" if not usable else "failed" if failed or unreadable else "success"
    return {
        "setting": "on", "status": status,
        "holdings": len(items), "mapped": sum(1 for item in items if item.reason is None),
        # fetched: Yahoo answered with a chart; captured: that response was kept and parsed from bronze.
        "requests": requests, "fetched": len(having("loaded", "not_captured")), "captured": len(loaded),
        "rows": sum(item.load.rows for item in loaded), "written": sum(item.load.written for item in loaded),
        "captures_listed": captures_listed, "unreadable_captures": unreadable,
        "not_mapped": [{"instrument_id": item.holding.conid, "symbol": item.holding.symbol, "reason": item.reason}
                       for item in having("not_mapped")],
        "not_found": [{"instrument_id": item.holding.conid, "symbol": item.holding.symbol, "tried": item.tried}
                      for item in having("not_found")],
        "failed": [{"instrument_id": item.holding.conid, "symbol": item.holding.symbol,
                    "reason": item.error or (item.load.error if item.load else None)} for item in failed],
        "deferred": [item.holding.conid for item in deferred], "deferred_because": stop if deferred else None,
        **limits, "seconds": round(max(0.0, seconds), 3), "raw_capture": capture.summary(),
        "instruments": [item.summary() for item in items],
    }
