"""Rebuild check for market data: silver rows against what the stored raw files parse to. Read-only.

Silver market data is loaded from provider replies that are also kept, as received, in the
bronze-market-data container (raw_capture). This check rebuilds silver from those files with
the jobs' own parsers and compares:

- silver.benchmark_prices: each row names its raw file by raw_sha256, the yfinance history
  reply (yahoo-benchmark-history) or, for a close filled from Yahoo's quote, the quote reply.
  The file is found by the sha256 prefix in its name, its full sha256 is checked, and it is
  parsed with normalize_yfinance_history, or the quote field the row's source_reference names.
- silver.risk_free_rates, the U.S. Treasury 3-month series: for each year, the newest stored
  Treasury year file (treasury-par-yield), parsed with parse_treasury_3m_xml.

Only rows written in the last `days` days are checked. Counts and the ids and dates of rows
that differ are printed, never a price or rate. A row whose file cannot be found is counted
as `no_raw_file`; rows written before raw capture (18 Oct 2026) or with lineage recording
off name a digest of the parsed closes instead of a file. The check passes when no row
differs (and, with require_files, when every row's file is found).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

from . import raw_capture

BENCHMARK_FEED = "yahoo-benchmark-history"
TREASURY_FEED = "treasury-par-yield"
DEFAULT_DAYS = 7
STATEMENT_TIMEOUT_MS = 30000
_QUOTE_REFERENCE = re.compile(r"^yfinance:quote\((?P<symbol>[^)]*)\):(?P<field>[A-Za-z]+)$")
_TREASURY_KEY = re.compile(r"/year-(?P<year>\d{4})-[0-9a-f]{%d}\.xml$" % raw_capture.SHA_PREFIX)


@dataclass
class TableResult:
    table: str
    checked: int = 0
    matched: int = 0
    differing: list[str] = field(default_factory=list)  # "<id> <date>", never values
    missing_from_file: int = 0
    no_raw_file: int = 0
    files_read: int = 0
    unreadable_files: int = 0

    def summary(self) -> dict[str, Any]:
        return {"table": self.table, "checked": self.checked, "matched": self.matched,
                "differing": len(self.differing), "differing_rows": self.differing[:50],
                "missing_from_file": self.missing_from_file, "no_raw_file": self.no_raw_file,
                "files_read": self.files_read, "unreadable_files": self.unreadable_files}


class Storage:
    """bronze-market-data, read with the job's managed identity."""

    def __init__(self, account_url: str, container: str) -> None:
        from .cloud_storage import best_effort_credential, container_client

        self.client = container_client(
            account_url, container,
            credential=best_effort_credential(connection_timeout=5, read_timeout=30),
            connection_timeout=5, read_timeout=30, retry_total=1,
        )
        if self.client is None:
            raise RuntimeError("the Azure Storage SDK is not installed")

    def list(self, prefix: str) -> list[tuple[str, datetime | None]]:
        return [(blob.name, getattr(blob, "last_modified", None))
                for blob in self.client.list_blobs(name_starts_with=prefix)]

    def read(self, name: str) -> bytes:
        return self.client.download_blob(name).readall()


def _days(now: datetime, days: int) -> list[date]:
    today = now.astimezone(timezone.utc).date()
    return [today - timedelta(days=offset) for offset in range(days + 1)]


def _dated_prefixes(feed: str, now: datetime, days: int) -> list[str]:
    return [f"{feed}/{day:%Y/%m/%d}/" for day in _days(now, days)]


def find_files_by_sha(storage: Any, feed: str, digests: Iterable[str], *, now: datetime,
                      days: int) -> dict[str, str]:
    """{full sha256: blob name} for the digests whose file is in the feed's dated folders
    of the last `days` days or its history folder. A name carries the sha256's first
    SHA_PREFIX characters; the full digest is checked when the file is read."""
    wanted = {str(d)[:raw_capture.SHA_PREFIX]: str(d) for d in digests
              if re.fullmatch(r"[0-9a-f]{64}", str(d or ""))}
    found: dict[str, str] = {}
    if not wanted:
        return found
    for prefix in _dated_prefixes(feed, now, days) + [f"{feed}/history/"]:
        for name, _modified in storage.list(prefix):
            stem = name.rsplit(".", 1)[0]
            short = stem.rsplit("-", 1)[-1]
            digest = wanted.get(short)
            if digest and digest not in found:
                found[digest] = name
        if len(found) == len(wanted):
            break
    return found


def parse_benchmark_file(name: str, content: bytes, source_reference: str | None) -> dict[str, float] | float:
    """The closes a stored benchmark reply parses to: {date: close} for a history CSV, or
    the single value of the quote field named by source_reference for a quote JSON."""
    if name.endswith(".csv"):
        import pandas as pd

        from .provisional_benchmark import normalize_yfinance_history

        frame = pd.read_csv(io.BytesIO(content), index_col=0, float_precision="round_trip")
        return dict(normalize_yfinance_history(frame))
    if name.endswith(".json"):
        match = _QUOTE_REFERENCE.match(source_reference or "")
        if not match:
            raise ValueError("a quote file needs a yfinance:quote(...) source_reference")
        payload = json.loads(content.decode("utf-8"))
        rows = ((payload.get("quoteResponse") or {}).get("result") or [])
        row = next((r for r in rows if r.get("symbol") == match.group("symbol")), None)
        if row is None or row.get(match.group("field")) is None:
            raise ValueError("the quote file has no value for the row's field")
        return float(row[match.group("field")])
    raise ValueError("unknown benchmark file type")


def check_benchmarks(rows: list[Mapping[str, Any]], storage: Any, *, now: datetime, days: int) -> TableResult:
    result = TableResult("silver.benchmark_prices")
    files = find_files_by_sha(storage, BENCHMARK_FEED, {row["raw_sha256"] for row in rows}, now=now, days=days)
    parsed: dict[tuple[str, str | None], Any] = {}
    for row in rows:
        result.checked += 1
        digest = str(row["raw_sha256"] or "")
        name = files.get(digest)
        if name is None:
            result.no_raw_file += 1
            continue
        is_quote = name.endswith(".json")
        key = (digest, row.get("source_reference") if is_quote else None)
        if key not in parsed:
            try:
                content = storage.read(name)
                if hashlib.sha256(content).hexdigest() != digest:
                    raise ValueError("sha256 does not match")
                parsed[key] = parse_benchmark_file(name, content, row.get("source_reference"))
                result.files_read += 1
            except Exception:  # noqa: BLE001 - counted, never fatal
                parsed[key] = None
                result.unreadable_files += 1
        value = parsed[key]
        label = f"{row['benchmark_id']} {row['observation_date']}"
        if value is None:
            result.differing.append(label)
            continue
        rebuilt = value if is_quote else value.get(str(row["observation_date"]))
        if rebuilt is None:
            result.missing_from_file += 1
        elif float(rebuilt) == float(row["level"]):
            result.matched += 1
        else:
            result.differing.append(label)
    return result


def newest_treasury_files(storage: Any, years: Iterable[int], *, now: datetime, days: int) -> dict[int, str]:
    """{year: blob name} of the newest stored Treasury file for each year: the latest dated
    folder in the last `days` days that has it, else the newest in history/."""
    wanted = set(years)
    found: dict[int, str] = {}
    for prefix in _dated_prefixes(TREASURY_FEED, now, days) + [f"{TREASURY_FEED}/history/"]:
        newest: dict[int, tuple[datetime, str]] = {}
        for name, modified in storage.list(prefix):
            match = _TREASURY_KEY.search(name)
            if not match:
                continue
            year = int(match.group("year"))
            if year in wanted and year not in found:
                stamp = modified or datetime.min.replace(tzinfo=timezone.utc)
                if year not in newest or stamp > newest[year][0]:
                    newest[year] = (stamp, name)
        for year, (_stamp, name) in newest.items():
            found[year] = name
        if len(found) == len(wanted):
            break
    return found


def check_treasury(rows: list[Mapping[str, Any]], storage: Any, *, now: datetime, days: int) -> TableResult:
    from .risk_free import SERIES_ID, parse_treasury_3m_xml

    result = TableResult("silver.risk_free_rates " + SERIES_ID)
    years = {int(str(row["rate_date"])[:4]) for row in rows}
    files = newest_treasury_files(storage, years, now=now, days=days)
    rebuilt: dict[int, dict[str, float] | None] = {}
    for year, name in files.items():
        try:
            rebuilt[year] = {o.rate_date: o.annual_rate for o in parse_treasury_3m_xml(storage.read(name))}
            result.files_read += 1
        except Exception:  # noqa: BLE001
            rebuilt[year] = None
            result.unreadable_files += 1
    for row in rows:
        result.checked += 1
        year = int(str(row["rate_date"])[:4])
        if year not in files:
            result.no_raw_file += 1
            continue
        values = rebuilt.get(year)
        label = f"{SERIES_ID} {row['rate_date']}"
        if values is None:
            result.differing.append(label)
        elif str(row["rate_date"]) not in values:
            result.missing_from_file += 1
        elif values[str(row["rate_date"])] == float(row["annual_rate"]):
            result.matched += 1
        else:
            result.differing.append(label)
    return result


def _rows(connection: Any, sql: str, params: tuple) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def run(connection: Any, storage: Any, *, now: datetime | None = None, days: int = DEFAULT_DAYS,
        require_files: bool = False) -> dict[str, Any]:
    """Rebuild both tables' recent rows and compare. Returns the summary."""
    from .risk_free import SERIES_ID

    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).astimezone(timezone.utc).isoformat()
    benchmarks = _rows(connection, (
        "SELECT benchmark_id, observation_date, level, raw_sha256, source_reference "
        "FROM benchmark_prices WHERE fetched_at::timestamptz >= %s::timestamptz "
        "AND quality_status <> 'fail' ORDER BY benchmark_id, observation_date"), (since,))
    treasury = _rows(connection, (
        "SELECT rate_date, annual_rate FROM risk_free_rates WHERE series_id = %s "
        "AND fetched_at::timestamptz >= %s::timestamptz AND quality_status <> 'fail' "
        "ORDER BY rate_date"), (SERIES_ID, since))
    results = [check_benchmarks(benchmarks, storage, now=now, days=days),
               check_treasury(treasury, storage, now=now, days=days)]
    differing = sum(len(r.differing) for r in results)
    missing_files = sum(r.no_raw_file for r in results)
    passed = differing == 0 and (missing_files == 0 or not require_files)
    return {"checked_at": now.isoformat(timespec="seconds"), "days": days, "passed": passed,
            "require_files": require_files, "tables": [r.summary() for r in results]}


def open_read_only(database_url: str, tenant_id: str) -> Any:
    from .layer_freshness import open_read_only as open_session

    connection = open_session(database_url, tenant_id)
    connection.execute("SELECT set_config('statement_timeout', %s, true)", (str(STATEMENT_TIMEOUT_MS),))
    return connection


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None,
         out: Callable[[str], None] = print) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--require-files", action="store_true",
                        help="also fail when a row's raw file cannot be found")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    database_url = environ.get("DATABASE_URL", "")
    tenant_id = environ.get("PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    account_url = environ.get(raw_capture.ACCOUNT_URL_ENV, "")
    container = environ.get(raw_capture.CONTAINER_ENV, "")
    if not (database_url and account_url and container):
        out("MARKET_DATA_REBUILD " + json.dumps({"passed": False, "error": "DATABASE_URL, "
            f"{raw_capture.ACCOUNT_URL_ENV} and {raw_capture.CONTAINER_ENV} are needed"}))
        return 2
    connection = open_read_only(database_url, tenant_id)
    try:
        summary = run(connection, Storage(account_url, container), days=max(1, args.days),
                      require_files=args.require_files)
    finally:
        try:
            connection.rollback()
            connection.close()
        except Exception:  # noqa: BLE001
            pass
    out("MARKET_DATA_REBUILD " + json.dumps(summary, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
