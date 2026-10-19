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
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from . import raw_capture

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


class CaptureError(ValueError):
    """A capture that cannot be read as a Yahoo chart response."""


def load_enabled(environ: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return str(environ.get(SETTING, "")).strip().lower() == "on"


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
