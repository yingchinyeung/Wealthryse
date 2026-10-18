"""Point-in-time feature exports: bronze and silver as the platform knew them on a date.

Experiments never query the live server. The research job exports what was known on a
date once, to the research-features container, and experiments read those files. This is
condition 2 of the research zone (docs/RESEARCH_ZONE.md): point-in-time data, as it really
exists.

Datasets:

* the five silver tables with "as known on" history (U8, docs/MEDALLION_AS_KNOWN_ON.md):
  trades, cash_transactions, fx_transactions, fx_rates and nav_snapshots, read through
  ``silver.<table>_as_known(at)``;
* three without history, rebuilt by replaying bronze in ingestion order with
  :func:`.point_in_time.replay_order`: ``positions`` and ``instruments`` from the raw IBKR
  statements in ibkr-raw (parsed with the platform's own parser), and ``market_prices`` from
  the Yahoo chart captures in bronze-market-data. A capture counts from the time it was
  stored, which is never earlier than when the platform knew it.

"As known on" a date means at the end of that day (UTC). The date must be in the past, so
the same export always gives the same rows.

Each dataset is written as ``<dataset>/<as_known_on>/<subjects>/data.parquet`` with
``manifest.json`` beside it (rows, columns, sha256 of the rows and of the file, the sources
and their times). One export manifest lists them all, at
``manifests/<as_known_on>/<subjects>.json``; its ``fingerprint`` is what a backtest records
as its data fingerprint in the experiment log. A manifest is written last and never
overwritten: running an export again keeps the files when the rows are the same, and stops
with :class:`ExportConflict` when they are not.

Sources and the destination are protocols. The job wires the live server's research_reader
login and the research identity's blob access (scripts/research/run_research_job.py); tests
use the in-memory ones here. pyarrow is imported only when a Parquet file is written or read.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from ..parser import parse_flex_xml
from ..util import decimal_text, pick
from . import experiment_log
from .point_in_time import Version, as_known, replay_order

CONTAINER = "research-features"
SCHEMA = "research-feature-export-v1"

# Silver tables with "as known on" history, and the column holding each row's own date.
HISTORY_TABLES = {
    "trades": "trade_date",
    "cash_transactions": "effective_date",
    "fx_transactions": "report_date",
    "fx_rates": "rate_date",
    "nav_snapshots": "snapshot_date",
}
REPLAYED = ("positions", "instruments", "market_prices")
DATASETS = tuple(HISTORY_TABLES) + REPLAYED
MARKET_FEED = "yahoo-chart"
ACCOUNT_ID = re.compile(r"^[A-Z]{1,4}[0-9]{3,12}$")

POSITION_KEY = ("account_id", "snapshot_date", "conid", "model_code")
POSITION_VALUES = ("currency", "quantity_text", "cost_basis_text", "mark_price_text", "market_value_text",
                   "unrealized_pnl_text", "row_sha256", "source_blob")
INSTRUMENT_FIELDS = ("symbol", "description", "asset_category", "currency", "listing_exchange",
                     "underlying_conid", "expiry", "strike_text", "put_call", "multiplier_text", "isin")
PRICE_KEY = ("symbol", "price_date")
PRICE_VALUES = ("close", "currency", "source_blob")


class ExportError(ValueError):
    """The export cannot run as asked."""


class ExportConflict(RuntimeError):
    """An export for this date and these subjects already exists with different rows."""


# --- sources and destination ---------------------------------------------------------------

@dataclass(frozen=True)
class Capture:
    """One bronze file: where it is, when it was stored (known from) and its bytes."""

    container: str
    name: str
    known_at: datetime
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class SilverReader(Protocol):
    def as_known(self, table: str, at: datetime, accounts: Sequence[str]) -> list[Mapping[str, Any]]:
        """``silver.<table>_as_known(at)`` for these accounts."""


class BronzeSource(Protocol):
    def statements(self, *, known_by: datetime) -> Iterable[Capture]:
        """Raw IBKR statements stored by ``known_by``."""

    def market_captures(self, feed: str, *, known_by: datetime) -> Iterable[Capture]:
        """Raw market-data responses of ``feed`` stored by ``known_by``."""


class FeatureSink(Protocol):
    def read(self, path: str) -> bytes | None: ...
    def write(self, path: str, data: bytes, *, content_type: str, overwrite: bool) -> None:
        """Store ``data``; raises FileExistsError when the file exists and ``overwrite`` is false."""


@dataclass
class MemorySilverReader:
    """History tables in memory, read as ``_as_known`` reads them (for tests and dry runs).

    ``versions[table]`` holds rows with ``recorded_from`` and ``recorded_to`` (None while
    current) plus the table's own columns.
    """

    versions: dict[str, list[Mapping[str, Any]]] = field(default_factory=dict)

    def as_known(self, table: str, at: datetime, accounts: Sequence[str]) -> list[Mapping[str, Any]]:
        rows = []
        for row in self.versions.get(table, []):
            if row["recorded_from"] <= at and (row.get("recorded_to") is None or row["recorded_to"] > at) \
                    and row.get("account_id") in accounts:
                rows.append({key: value for key, value in row.items() if key not in ("recorded_from", "recorded_to")})
        return rows


@dataclass
class MemoryBronzeSource:
    statement_files: list[Capture] = field(default_factory=list)
    market_files: dict[str, list[Capture]] = field(default_factory=dict)

    def statements(self, *, known_by: datetime) -> list[Capture]:
        return [item for item in self.statement_files if item.known_at <= known_by]

    def market_captures(self, feed: str, *, known_by: datetime) -> list[Capture]:
        return [item for item in self.market_files.get(feed, []) if item.known_at <= known_by]


class MemorySink:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def read(self, path: str) -> bytes | None:
        return self.files.get(path)

    def write(self, path: str, data: bytes, *, content_type: str, overwrite: bool) -> None:
        if path in self.files and not overwrite:
            raise FileExistsError(path)
        self.files[path] = bytes(data)


# --- helpers -------------------------------------------------------------------------------

def cutoff_for(as_known_on: str) -> datetime:
    """The end of ``as_known_on`` in UTC: everything stored that day or earlier counts."""
    try:
        day = date.fromisoformat(as_known_on)
    except (TypeError, ValueError):
        raise ExportError(f"--as-known-on must be a date as yyyy-mm-dd, not {as_known_on!r}") from None
    if day.isoformat() != as_known_on:
        raise ExportError(f"--as-known-on must be a date as yyyy-mm-dd, not {as_known_on!r}")
    return datetime.combine(day, time.max, tzinfo=timezone.utc)


def subjects_key(subjects: Sequence[str]) -> str:
    """The folder name for a set of accounts: their ids joined by +, or a hash when long."""
    ordered = sorted(set(subjects))
    joined = "+".join(ordered)
    if len(joined) <= 80:
        return joined
    return "set-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def check_subjects(subjects: Sequence[str]) -> tuple[str, ...]:
    if not subjects:
        raise ExportError("Name at least one --subject (an IBKR account id such as U1234567)")
    for subject in subjects:
        if not ACCOUNT_ID.match(subject):
            raise ExportError(f"{subject!r} is not an account id; a composite is exported by naming its accounts")
    return tuple(sorted(set(subjects)))


def plain(value: Any) -> Any:
    """A JSON- and Parquet-friendly copy of a database or parser value."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def rows_sha256(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """sha256 over the rows in order, one canonical JSON line each: the same rows give the same hash
    whatever the file format."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(_canonical([row.get(column) for column in columns]).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _sorted_rows(rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> list[dict[str, Any]]:
    cleaned = [{column: plain(row.get(column)) for column in columns} for row in rows]
    return sorted(cleaned, key=lambda row: _canonical([row.get(column) for column in columns]))


def _columns(rows: Sequence[Mapping[str, Any]], first: Sequence[str] = ()) -> list[str]:
    seen = list(first)
    for row in rows:
        for key in row:
            if key not in seen:
                seen.append(key)
    return seen


def _captures_summary(container: str, captures: Sequence[Capture]) -> dict[str, Any]:
    listing = [[item.name, item.sha256] for item in sorted(captures, key=lambda c: c.name)]
    times = sorted(item.known_at for item in captures)
    return {
        "kind": "bronze-replay",
        "container": container,
        "captures": len(captures),
        "captures_sha256": hashlib.sha256(_canonical(listing).encode("utf-8")).hexdigest(),
        "first_known_at": plain(times[0]) if times else None,
        "last_known_at": plain(times[-1]) if times else None,
    }


# --- replays of bronze -----------------------------------------------------------------------

def _ordered(captures: Iterable[Capture], cutoff: datetime) -> list[Capture]:
    return sorted((item for item in captures if item.known_at <= cutoff), key=lambda item: (item.known_at, item.name))


def replay_positions(captures: Iterable[Capture], cutoff: datetime, accounts: Sequence[str]) -> list[dict[str, Any]]:
    """Open positions as known at ``cutoff``: each statement's OpenPosition rows, replayed in the
    order they were stored, the latest version of each (account, date, conid, model) winning, as
    the loader's upsert does."""
    wanted = set(accounts)
    records = []
    for capture in _ordered(captures, cutoff):
        for row in parse_flex_xml(capture.content).rows:
            if "openposition" not in row.record_type.lower() or row.account_id not in wanted:
                continue
            if not row.report_date or not row.conid:
                continue
            attributes = row.attributes
            records.append({
                "account_id": row.account_id, "snapshot_date": row.report_date, "conid": row.conid,
                "model_code": pick(attributes, "model", "modelCode"), "currency": row.currency,
                "quantity_text": decimal_text(pick(attributes, "position", "quantity")),
                "cost_basis_text": decimal_text(pick(attributes, "costBasisMoney", "costBasis")),
                "mark_price_text": decimal_text(pick(attributes, "markPrice", "closePrice")),
                "market_value_text": decimal_text(pick(attributes, "positionValue", "marketValue")),
                "unrealized_pnl_text": decimal_text(pick(attributes, "fifoPnlUnrealized", "unrealizedPL")),
                "row_sha256": row.row_sha256, "source_blob": capture.name, "ingested_at": capture.known_at,
            })
    versions = replay_order(records, key_fields=POSITION_KEY, date_field="snapshot_date",
                            ingested_field="ingested_at", value_fields=POSITION_VALUES)
    return _version_rows(as_known(versions, cutoff), POSITION_KEY)


def replay_instruments(captures: Iterable[Capture], cutoff: datetime, accounts: Sequence[str]) -> list[dict[str, Any]]:
    """Instrument details as known at ``cutoff``, folded like the loader: in the order they were
    stored, a non-empty value replaces the one before it and an empty one keeps it."""
    wanted = set(accounts)
    known: dict[str, dict[str, Any]] = {}
    for capture in _ordered(captures, cutoff):
        for row in parse_flex_xml(capture.content).rows:
            if not row.conid or row.account_id not in wanted:
                continue
            attributes = row.attributes
            values = {
                "symbol": pick(attributes, "symbol"), "description": pick(attributes, "description"),
                "asset_category": pick(attributes, "assetCategory", "assetClass"),
                "currency": pick(attributes, "currency"),
                "listing_exchange": pick(attributes, "listingExchange", "exchange"),
                "underlying_conid": pick(attributes, "underlyingConid"), "expiry": pick(attributes, "expiry"),
                "strike_text": decimal_text(pick(attributes, "strike")) or "",
                "put_call": pick(attributes, "putCall"),
                "multiplier_text": decimal_text(pick(attributes, "multiplier")) or "",
                "isin": pick(attributes, "isin", "securityID"),
            }
            held = known.setdefault(row.conid, {"conid": row.conid, **{name: None for name in INSTRUMENT_FIELDS},
                                                "first_known_at": capture.known_at, "source_blob": capture.name})
            for name, value in values.items():
                if value:
                    held[name] = value
            held["last_known_at"] = capture.known_at
            held["source_blob"] = capture.name
    return [dict(item) for _, item in sorted(known.items())]


def chart_closes(content: bytes) -> tuple[str, str, list[tuple[str, float]]]:
    """(symbol, currency, [(exchange-local date, close)]) from one Yahoo chart response, read as
    market_data.fetch_daily_closes reads it."""
    payload = json.loads(content.decode("utf-8"))
    chart = payload.get("chart") or {}
    results = chart.get("result") or []
    if chart.get("error") or not results:
        return "", "", []
    result = results[0]
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    blocks = (result.get("indicators") or {}).get("quote") or []
    closes = (blocks[0].get("close") if blocks else None) or []
    if len(timestamps) != len(closes):
        return "", "", []
    try:
        zone = ZoneInfo(str(meta.get("exchangeTimezoneName") or "UTC"))
    except Exception:  # noqa: BLE001 - an unknown zone name falls back to UTC, as the platform does
        zone = timezone.utc
    rows = {}
    for stamp, value in zip(timestamps, closes):
        if value is None:
            continue
        close = float(value)
        if not math.isfinite(close) or close <= 0:
            continue
        rows[datetime.fromtimestamp(int(stamp), timezone.utc).astimezone(zone).date().isoformat()] = close
    return str(meta.get("symbol") or ""), str(meta.get("currency") or "").upper(), sorted(rows.items())


def replay_market_prices(captures: Iterable[Capture], cutoff: datetime) -> list[dict[str, Any]]:
    """Daily closes as known at ``cutoff``: the latest stored capture's close for each symbol and date."""
    records = []
    for capture in _ordered(captures, cutoff):
        symbol, currency, closes = chart_closes(capture.content)
        if not symbol:
            continue
        for day, close in closes:
            records.append({"symbol": symbol, "price_date": day, "close": close, "currency": currency,
                            "source_blob": capture.name, "ingested_at": capture.known_at})
    versions = replay_order(records, key_fields=PRICE_KEY, date_field="price_date", ingested_field="ingested_at",
                            value_fields=PRICE_VALUES)
    return _version_rows(as_known(versions, cutoff), PRICE_KEY)


def _version_rows(known: Mapping[Any, Version], key_fields: Sequence[str]) -> list[dict[str, Any]]:
    return [{**dict(zip(key_fields, key)), **version.value, "known_at": version.known_at}
            for key, version in known.items()]


class _OnceBronze:
    """Reads each bronze listing once per export (positions and instruments share the statements)."""

    def __init__(self, source: BronzeSource) -> None:
        self._source = source
        self._seen: dict[tuple[str, datetime], list[Capture]] = {}

    def statements(self, *, known_by: datetime) -> list[Capture]:
        key = ("statements", known_by)
        if key not in self._seen:
            self._seen[key] = list(self._source.statements(known_by=known_by))
        return self._seen[key]

    def market_captures(self, feed: str, *, known_by: datetime) -> list[Capture]:
        key = ("market:" + feed, known_by)
        if key not in self._seen:
            self._seen[key] = list(self._source.market_captures(feed, known_by=known_by))
        return self._seen[key]


# --- Parquet ---------------------------------------------------------------------------------

def parquet_available() -> bool:
    """Whether pyarrow is installed (the research image has it; the platform images do not)."""
    import importlib.util

    return importlib.util.find_spec("pyarrow") is not None


def parquet_bytes(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> bytes:
    """The rows as one Parquet file. A column with mixed types is written as text."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    arrays = {}
    for column in columns:
        values = [row.get(column) for row in rows]
        try:
            arrays[column] = pa.array(values) if any(value is not None for value in values) \
                else pa.array(values, type=pa.string())
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            arrays[column] = pa.array([None if value is None else str(value) for value in values], type=pa.string())
    stream = pa.BufferOutputStream()
    pq.write_table(pa.table(arrays) if columns else pa.table({}), stream)
    return stream.getvalue().to_pybytes()


def read_parquet(data: bytes) -> list[dict[str, Any]]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    return pq.read_table(pa.BufferReader(data)).to_pylist()


# --- the export ------------------------------------------------------------------------------

@dataclass(frozen=True)
class ExportRequest:
    as_known_on: str
    subjects: tuple[str, ...]
    tenant_id: str
    run_id: str
    code_version: str = "unknown"
    datasets: tuple[str, ...] = DATASETS


def dataset_folder(dataset: str, as_known_on: str, subjects: Sequence[str]) -> str:
    return f"{dataset}/{as_known_on}/{subjects_key(subjects)}"


def export_manifest_path(as_known_on: str, subjects: Sequence[str]) -> str:
    return f"manifests/{as_known_on}/{subjects_key(subjects)}.json"


def _read_dataset(dataset: str, request: ExportRequest, cutoff: datetime, *, silver: SilverReader,
                  bronze: BronzeSource) -> tuple[list[Mapping[str, Any]], list[str], list[dict[str, Any]], str]:
    accounts = list(request.subjects)
    if dataset in HISTORY_TABLES:
        rows = silver.as_known(dataset, cutoff, accounts)
        function = f"silver.{dataset}_as_known"
        source = {"kind": "silver-as-known", "function": function, "at": plain(cutoff), "accounts": accounts}
        method = (f"{function}(at) at the end of {request.as_known_on} UTC: each row's latest version recorded "
                  f"by then (U8 history), for the named accounts, under the reader's tenant row security.")
        return rows, _columns(rows, ("account_id", HISTORY_TABLES[dataset])), [source], method
    if dataset == "positions":
        captures = list(bronze.statements(known_by=cutoff))
        rows = replay_positions(captures, cutoff, accounts)
        method = ("OpenPosition rows of the raw IBKR statements stored by the end of the day, replayed in the order "
                  "they were stored; the latest version of each account, date, conid and model wins.")
        return rows, [*POSITION_KEY, *POSITION_VALUES, "known_at"], [_captures_summary("ibkr-raw", captures)], method
    if dataset == "instruments":
        captures = list(bronze.statements(known_by=cutoff))
        rows = replay_instruments(captures, cutoff, accounts)
        method = ("Instrument details from every statement row with a conid, stored by the end of the day, folded in "
                  "the order they were stored as the loader does (a non-empty value replaces the previous one).")
        return rows, ["conid", *INSTRUMENT_FIELDS, "first_known_at", "last_known_at", "source_blob"], \
            [_captures_summary("ibkr-raw", captures)], method
    if dataset == "market_prices":
        captures = list(bronze.market_captures(MARKET_FEED, known_by=cutoff))
        rows = replay_market_prices(captures, cutoff)
        method = (f"Daily closes from the {MARKET_FEED} captures stored by the end of the day; for each symbol and "
                  "exchange-local date the latest stored capture's close wins.")
        return rows, [*PRICE_KEY, *PRICE_VALUES, "known_at"], [_captures_summary("bronze-market-data", captures)], method
    raise ExportError(f"Unknown dataset {dataset!r}; choose from {', '.join(DATASETS)}")


def _write_manifest(sink: FeatureSink, path: str, manifest: Mapping[str, Any], *, same: Callable[[dict], bool]) -> str:
    """Write a manifest once. 'existing' when one with the same rows is there; ExportConflict otherwise."""
    existing = sink.read(path)
    if existing is not None:
        if same(json.loads(existing.decode("utf-8"))):
            return "existing"
        raise ExportConflict(f"{path} already exists with different rows; the earlier export is kept")
    data = (json.dumps(plain(manifest), indent=1, sort_keys=True) + "\n").encode("utf-8")
    try:
        sink.write(path, data, content_type="application/json", overwrite=False)
    except FileExistsError:
        return _write_manifest(sink, path, manifest, same=same)
    return "written"


def export_features(request: ExportRequest, *, silver: SilverReader, bronze: BronzeSource, sink: FeatureSink,
                    write_parquet: bool = True, now: datetime | None = None,
                    before_read: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Export each dataset as known on ``request.as_known_on`` and return the export manifest.

    ``before_read(dataset)`` runs before each dataset is read (the job uses it to stay out of
    the daily 07:50 to 09:25 UTC window). With ``write_parquet=False`` only the manifests are
    written, for a run without pyarrow.
    """
    subjects = check_subjects(request.subjects)
    request = replace(request, subjects=subjects)
    cutoff = cutoff_for(request.as_known_on)
    moment = now or datetime.now(timezone.utc)
    if cutoff >= moment:
        raise ExportError("--as-known-on must be a day that has ended (UTC), so the export can never change")
    unknown = sorted(set(request.datasets) - set(DATASETS))
    if unknown or not request.datasets:
        raise ExportError(f"Unknown dataset {', '.join(unknown) or '(none named)'}; choose from {', '.join(DATASETS)}")

    summary: dict[str, Any] = {}
    bronze = _OnceBronze(bronze)
    for dataset in request.datasets:
        if before_read:
            before_read(dataset)
        raw, columns, sources, method = _read_dataset(dataset, request, cutoff, silver=silver, bronze=bronze)
        rows = _sorted_rows(raw, columns)
        digest = rows_sha256(rows, columns)
        folder = dataset_folder(dataset, request.as_known_on, subjects)
        manifest_path = f"{folder}/manifest.json"
        existing = sink.read(manifest_path)
        file_info = None
        if existing is not None:
            held = json.loads(existing.decode("utf-8"))
            if held.get("rows_sha256") != digest:
                raise ExportConflict(f"{manifest_path} already exists with different rows; the earlier export is kept")
            file_info = held.get("file")
        elif write_parquet:
            data = parquet_bytes(rows, columns)
            # No manifest yet, so a file here is from a run that stopped before its manifest: replace it.
            sink.write(f"{folder}/data.parquet", data, content_type="application/vnd.apache.parquet", overwrite=True)
            file_info = {"path": f"{folder}/data.parquet", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        manifest = {
            "schema": SCHEMA, "dataset": dataset, "as_known_on": request.as_known_on, "known_at_cutoff": cutoff,
            "subjects": list(subjects), "tenant_id": request.tenant_id, "rows": len(rows), "columns": columns,
            "rows_sha256": digest, "file": file_info, "sources": sources, "method": method,
            "run_id": request.run_id, "code_version": request.code_version,
        }
        outcome = _write_manifest(sink, manifest_path, manifest, same=lambda held, digest=digest: held.get("rows_sha256") == digest)
        summary[dataset] = {"manifest": manifest_path, "file": file_info, "rows": len(rows), "rows_sha256": digest,
                            "outcome": outcome}

    fingerprint = experiment_log.data_fingerprint(
        "feature-export", request.as_known_on, list(subjects),
        {dataset: item["rows_sha256"] for dataset, item in sorted(summary.items())})
    export = {
        "schema": SCHEMA, "as_known_on": request.as_known_on, "known_at_cutoff": cutoff, "subjects": list(subjects),
        "tenant_id": request.tenant_id, "datasets": summary, "fingerprint": fingerprint,
        "run_id": request.run_id, "code_version": request.code_version,
    }
    path = export_manifest_path(request.as_known_on, subjects)
    outcome = _write_manifest(sink, path, export, same=lambda held: held.get("fingerprint") == fingerprint)
    return {**plain(export), "manifest": path, "outcome": outcome}


def load_dataset(sink: FeatureSink, export_manifest: str, dataset: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(export manifest, rows) for one dataset of an export, read back from its Parquet file."""
    raw = sink.read(export_manifest)
    if raw is None:
        raise ExportError(f"No export manifest at {export_manifest}")
    export = json.loads(raw.decode("utf-8"))
    entry = export.get("datasets", {}).get(dataset)
    if not entry or not entry.get("file"):
        raise ExportError(f"The export {export_manifest} has no {dataset} file")
    data = sink.read(entry["file"]["path"])
    if data is None or hashlib.sha256(data).hexdigest() != entry["file"]["sha256"]:
        raise ExportError(f"{entry['file']['path']} is missing or does not match its manifest")
    return export, read_parquet(data)
