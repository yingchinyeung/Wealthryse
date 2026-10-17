"""One-day check before v2 takes over v1.1's Flex query (medallion step S5).

v1.1's own raw Flex statements are loaded with v2's Flex ingest path
(``service.ingest_content``, the call the institutional worker makes) into a
throwaway SQLite database. The result is compared with the v1.1 snapshots
account-sync imported for the same date and, optionally, with the live
database through a read-only transaction. Nothing is written anywhere except
the temporary directory and the caller's report.

If rekeyed rows are found, the loader's legacy-key step
(IBKR_FLEX_LEGACY_KEY_COMPAT) avoids duplicating them. It must not run while
account-sync imports the same accounts. Bronze (flex_rows) keeps the parser's
key while silver keeps the legacy key, so S6's replay will list those rows as
rekeyed.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .config import Settings
from .db import DEFAULT_TENANT_ID, initialize
from .loader import LEGACY_KEY_COMPAT_ENV
from .parser import parse_flex_xml
from .pilot_snapshot import ACCOUNT_TABLES, validate_pilot_snapshot
from .provisional_benchmark import _external_flows, _is_external_cash_transaction
from .service import ingest_content
from .snapshot_preflight import inspect_archived_xml
from .util import parse_date_text, sha256_bytes, utc_now_iso


REPORT_VERSION = 1
RUN_TYPE = "medallion-switch-dry-run"
EVENT_TABLES = ("trades", "cash_transactions", "corporate_actions", "fx_transactions")
NATURAL_TABLES = ("nav_snapshots", "position_snapshots", "cash_snapshots", "fx_rates")
# Positions and cash balances are reported as of each statement's end date only.
END_DATE_TABLES = {"position_snapshots", "cash_snapshots"}
IGNORED_COLUMNS = {"account_id", "tenant_id", "last_seen_run_id", "first_seen_run_id", "row_sha256"}
HASH_KEY_PREFIXES = ("cash:", "fx:", "corporate-action:", "derived:", "fx-rate:", "fx-rate-unresolved:")
SAMPLE_LIMIT = 20
# IBKR account ids anywhere in a text. No word boundaries, so ids inside names
# such as flex_U1234567_x or followed by a letter still match.
ACCOUNT_ID_IN_TEXT = re.compile(r"[A-Z]{1,2}\d{5,}")


def mask_account_ids(text: str) -> str:
    """For error lines, where the account list is not known: keep the last three characters."""

    return ACCOUNT_ID_IN_TEXT.sub(lambda match: "***" + match.group()[-3:], text)


class DryRunError(RuntimeError):
    pass


@dataclass(frozen=True)
class Statement:
    name: str
    content: bytes
    sha256: str
    windows: dict[str, tuple[str, str]]  # account -> (from, to)

    @property
    def to_date(self) -> str:
        return max((window[1] for window in self.windows.values()), default="")

    @property
    def from_date(self) -> str:
        return min((window[0] for window in self.windows.values()), default="")


@dataclass
class Snapshot:
    name: str
    sha256: str
    account_id: str
    start_date: str
    generated_at: str
    format_version: str
    columns: dict[str, list[str]]
    rows: dict[str, list[dict[str, Any]]]
    instruments: list[dict[str, Any]] = field(default_factory=list)


def key_kind(source_key: str) -> str:
    return "hash" if str(source_key).startswith(HASH_KEY_PREFIXES) else "id"


class AccountMasker:
    """Shortest unique suffix of at least three characters."""

    def __init__(self, accounts: Iterable[str]) -> None:
        names = sorted({str(account) for account in accounts if account})
        self.masks: dict[str, str] = {}
        for account in names:
            length = 3
            while length < len(account) and any(
                other != account and other.endswith(account[-length:]) for other in names
            ):
                length += 1
            self.masks[account] = "***" + account[-length:]

    def __call__(self, account: str) -> str:
        return self.masks.get(account) or ("***" + str(account)[-3:])

    def scrub(self, text: str) -> str:
        for account in sorted(self.masks, key=len, reverse=True):
            text = text.replace(account, self.masks[account])
        return text


def statement_from_bytes(name: str, content: bytes) -> Statement:
    report = parse_flex_xml(content)
    windows: dict[str, tuple[str, str]] = {}
    for row in report.rows:
        if row.section != "Statement":
            continue
        start = parse_date_text(row.attributes.get("fromDate", ""))
        end = parse_date_text(row.attributes.get("toDate", ""))
        if not start or not end or start > end:
            raise DryRunError(f"Statement {name} has no valid period for one account")
        if row.account_id in windows and windows[row.account_id] != (start, end):
            raise DryRunError(f"Statement {name} has two periods for one account")
        windows[row.account_id] = (start, end)
    if not windows:
        raise DryRunError(f"Statement {name} has no FlexStatement period")
    return Statement(name=name, content=content, sha256=sha256_bytes(content), windows=windows)


def order_statements(statements: Iterable[Statement]) -> list[Statement]:
    unique: dict[str, Statement] = {}
    for statement in statements:
        unique.setdefault(statement.sha256, statement)
    return sorted(unique.values(), key=lambda item: (item.to_date, item.from_date, item.name))


def _dates(start: str, end: str) -> set[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return {(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)}


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def read_snapshot(name: str, path: Path) -> Snapshot:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM snapshot_metadata"))
    finally:
        connection.close()
    account_id, start_date = metadata.get("account_id", ""), metadata.get("start_date", "")
    validation = validate_pilot_snapshot(path, account_id=account_id, start_date=start_date)
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        columns, rows = {}, {}
        for table in ACCOUNT_TABLES:
            columns[table] = [row[1] for row in connection.execute(f"PRAGMA table_info({_quote(table)})")]
            rows[table] = [dict(row) for row in connection.execute(f"SELECT * FROM {_quote(table)}")]
        instruments = [dict(row) for row in connection.execute("SELECT * FROM instruments")]
    finally:
        connection.close()
    return Snapshot(
        name=name, sha256=validation["sha256"], account_id=account_id, start_date=start_date,
        generated_at=metadata.get("generated_at", ""), format_version=metadata.get("format_version", ""),
        columns=columns, rows=rows, instruments=instruments,
    )


def _settings(work_dir: Path, tenant_id: str) -> Settings:
    # No Flex credentials, no database URL and no storage URL: SQLite only.
    return Settings(
        project_root=work_dir, flex_token="", flex_query_id="",
        database_path=work_dir / "v2.sqlite", raw_report_dir=work_dir / "raw",
        log_dir=work_dir / "logs", timezone_name="Asia/Hong_Kong", daily_lookback_days=14,
        user_agent="medallion-switch-dry-run", request_timeout_seconds=10, max_wait_seconds=20,
        database_url="", tenant_id=tenant_id, azure_storage_account_url="",
    )


def _seed_instruments(database_path: Path, instruments: Iterable[Mapping[str, Any]]) -> None:
    # Live already holds these instruments, so trade conid resolution sees the same set.
    connection = sqlite3.connect(database_path)
    try:
        allowed = [row[1] for row in connection.execute("PRAGMA table_info(instruments)")]
        for item in instruments:
            values = {key: item.get(key) for key in allowed if key in item}
            if not values.get("conid"):
                continue
            values.setdefault("first_seen_at", utc_now_iso())
            values.setdefault("last_seen_at", utc_now_iso())
            values.setdefault("source_json", "{}")
            names = list(values)
            connection.execute(
                f"INSERT OR IGNORE INTO instruments({', '.join(_quote(n) for n in names)}) "
                f"VALUES ({', '.join('?' for _ in names)})",
                [values[n] for n in names],
            )
        connection.commit()
    finally:
        connection.close()


def load_with_v2(statements: list[Statement], work_dir: Path, *, tenant_id: str,
                 instruments: Iterable[Mapping[str, Any]] = ()) -> Path:
    """Load the statements in order through v2's own ingest path."""

    work_dir.mkdir(parents=True, exist_ok=True)
    settings = _settings(work_dir, tenant_id)
    initialize(settings.database_path)
    _seed_instruments(settings.database_path, instruments)
    settings.raw_report_dir.mkdir(parents=True, exist_ok=True)
    for index, statement in enumerate(statements, start=1):
        raw_path = settings.raw_report_dir / f"{index:04d}-{statement.sha256[:12]}.xml"
        raw_path.write_bytes(statement.content)
        ingest_content(
            settings, statement.content, run_type=RUN_TYPE,
            requested_from=date.fromisoformat(statement.from_date),
            requested_to=date.fromisoformat(statement.to_date),
            existing_raw_path=raw_path,
        )
    return settings.database_path


def _table_rows(connection: sqlite3.Connection, table: str, account_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(
        f"SELECT * FROM {_quote(table)} WHERE account_id = ?", (account_id,)
    )]


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({_quote(table)})")]


def _norm(column: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if column.endswith("_text"):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return str(value)
        return "NaN" if number.is_nan() else number
    return str(value)


def _show(value: Any) -> Any:
    return None if value is None else str(value)


def compare_table(
    table: str,
    v2_rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
    columns: Iterable[str],
    *,
    in_scope: Callable[[str | None], bool],
    before_start: Callable[[str | None], bool],
) -> dict[str, Any]:
    """Pair rows by the table's own key; event tables also pair by raw content."""

    date_column, _, keys = ACCOUNT_TABLES[table]
    key_columns = tuple(key for key in keys if key != "account_id")
    event = key_columns == ("source_key",)
    value_columns = sorted(set(columns) - IGNORED_COLUMNS - set(key_columns))

    def index(rows: list[dict[str, Any]], side: str) -> dict[tuple, dict[str, Any]]:
        result: dict[tuple, dict[str, Any]] = {}
        for row in rows:
            key = tuple(row.get(column) for column in key_columns)
            if key in result:
                raise DryRunError(f"{side} {table} has a duplicate key")
            result[key] = row
        return result

    v2, reference = index(v2_rows, "v2"), index(reference_rows, "reference")
    counts: Counter = Counter()
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dates: dict[str, set[str]] = defaultdict(set)

    def sample(kind: str, item: dict[str, Any]) -> None:
        if len(samples[kind]) < SAMPLE_LIMIT:
            samples[kind].append(item)

    def changed(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, list[Any]]:
        return {
            column: [_show(left.get(column)), _show(right.get(column))]
            for column in value_columns
            if _norm(column, left.get(column)) != _norm(column, right.get(column))
        }

    def describe(key: tuple, row: Mapping[str, Any]) -> dict[str, Any]:
        item = {"key": list(key), "date": row.get(date_column)}
        if event:
            item["key_kind"] = key_kind(key[0])
        return item

    for key in sorted(set(v2) & set(reference), key=str):
        difference = changed(reference[key], v2[key])
        if difference:
            counts["different"] += 1
            sample("different", {**describe(key, v2[key]), "reference_then_v2": difference})
        elif reference[key].get("row_sha256") != v2[key].get("row_sha256"):
            counts["raw_only_differences"] += 1
        else:
            counts["matched"] += 1

    v2_only = set(v2) - set(reference)
    reference_only = set(reference) - set(v2)
    if event:
        # Same raw IBKR attributes under another key: the first pull would duplicate it.
        by_content: dict[str, list[tuple]] = defaultdict(list)
        for key in reference_only:
            by_content[str(reference[key].get("row_sha256"))].append(key)
        for key in sorted(v2_only, key=str):
            candidates = by_content.get(str(v2[key].get("row_sha256")), [])
            if len(candidates) != 1:
                continue
            old = candidates.pop()
            v2_only.discard(key)
            reference_only.discard(old)
            counts["rekeyed"] += 1
            sample("rekeyed", {"date": v2[key].get(date_column), "v2_key": key[0],
                               "reference_key": old[0], "record_values": changed(reference[old], v2[key])})
    for key in sorted(v2_only, key=str):
        day = v2[key].get(date_column)
        if before_start(day):
            counts["extra_before_start"] += 1
        else:
            counts["extra_in_v2"] += 1
            dates["extra_in_v2"].add(str(day))
            sample("extra_in_v2", describe(key, v2[key]))
    for key in sorted(reference_only, key=str):
        day = reference[key].get(date_column)
        if in_scope(day):
            counts["missing_in_v2"] += 1
            dates["missing_in_v2"].add(str(day))
            sample("missing_in_v2", describe(key, reference[key]))
        else:
            counts["reference_outside_scope"] += 1
    result = {
        "v2_rows": len(v2),
        "reference_rows_in_scope": sum(
            1 for key, row in reference.items() if key in v2 or in_scope(row.get(date_column))
        ),
        **{name: counts.get(name, 0) for name in (
            "matched", "different", "raw_only_differences", "rekeyed", "extra_in_v2",
            "extra_before_start", "missing_in_v2", "reference_outside_scope",
        )},
    }
    if samples:
        result["samples"] = dict(samples)
    if dates:
        result["dates"] = {name: sorted(values) for name, values in sorted(dates.items())}
    return result


def _scope(table: str, covered: set[str], end_dates: set[str], v2_dates: set[str]) -> Callable[[str | None], bool]:
    if table in END_DATE_TABLES:
        allowed = (end_dates | v2_dates) & covered
        return lambda day: bool(day) and day in allowed
    return lambda day: bool(day) and day in covered


def account_coverage(statements: list[Statement], account_id: str) -> tuple[set[str], set[str]]:
    covered: set[str] = set()
    end_dates: set[str] = set()
    for statement in statements:
        window = statement.windows.get(account_id)
        if window:
            covered |= _dates(*window)
            end_dates.add(window[1])
    return covered, end_dates


def compare_account(v2: sqlite3.Connection, account_id: str, reference_rows: Mapping[str, list[dict]],
                    reference_columns: Mapping[str, list[str]], *, covered: set[str], end_dates: set[str],
                    start_date: str) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for table in (*NATURAL_TABLES, *EVENT_TABLES):
        date_column = ACCOUNT_TABLES[table][0]
        v2_rows = _table_rows(v2, table, account_id)
        columns = set(_table_columns(v2, table)) & set(reference_columns.get(table, ()))
        tables[table] = compare_table(
            table, v2_rows, list(reference_rows.get(table, ())), columns,
            in_scope=_scope(table, covered, end_dates, {str(r.get(date_column)) for r in v2_rows}),
            before_start=lambda day: bool(day) and bool(start_date) and day < start_date,
        )
    return tables


def unexplained(tables: Mapping[str, Mapping[str, Any]], *, missing_fails: bool = True) -> list[str]:
    failures = []
    for table, result in tables.items():
        for name in ("different", "rekeyed", "extra_in_v2") + (("missing_in_v2",) if missing_fails else ()):
            if result.get(name):
                failures.append(f"{table}: {result[name]} {name.replace('_', ' ')}")
        if result.get("rekeyed"):
            failures.append(f"{table}: the first pull would duplicate the rekeyed rows unless "
                            f"{LEGACY_KEY_COMPAT_ENV} is on for institutional-daily")
    return failures


def window_stability(statements: list[Statement], work_dir: Path, *, tenant_id: str,
                     accounts: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Load each statement alone and compare overlapping days between neighbours.

    A key that changes when only the statement period changes would be
    duplicated by v2's longer first pull.
    """

    databases = []
    for index, statement in enumerate(statements):
        path = load_with_v2([statement], work_dir / f"single-{index:04d}", tenant_id=tenant_id)
        databases.append(path)
    result: dict[str, dict[str, Any]] = {}
    for account_id in accounts:
        counts: Counter = Counter()
        samples: list[dict[str, Any]] = []
        pairs = 0
        indexed = [(statement, path) for statement, path in zip(statements, databases)
                   if account_id in statement.windows]
        for (left, left_path), (right, right_path) in zip(indexed, indexed[1:]):
            overlap = _dates(*left.windows[account_id]) & _dates(*right.windows[account_id])
            if not overlap:
                continue
            pairs += 1
            first, second = sqlite3.connect(left_path), sqlite3.connect(right_path)
            first.row_factory = second.row_factory = sqlite3.Row
            try:
                for table in (*EVENT_TABLES, *NATURAL_TABLES):
                    date_column, _, keys = ACCOUNT_TABLES[table]
                    key_columns = tuple(key for key in keys if key != "account_id")

                    def rows(connection: sqlite3.Connection) -> dict[tuple, dict[str, Any]]:
                        return {
                            tuple(row.get(column) for column in key_columns): row
                            for row in _table_rows(connection, table, account_id)
                            if row.get(date_column) in overlap
                        }

                    a, b = rows(first), rows(second)
                    if table in EVENT_TABLES:
                        for key in set(a) ^ set(b):
                            kind = key_kind(key[0])
                            counts[f"{table}:{kind}_key_changed"] += 1
                            if len(samples) < SAMPLE_LIMIT:
                                row = a.get(key) or b.get(key)
                                samples.append({"table": table, "date": row.get(date_column), "key_kind": kind,
                                                "only_in": left.name if key in a else right.name})
                    else:
                        common = set(a) & set(b)
                        if table not in END_DATE_TABLES:
                            counts[f"{table}:coverage_differs"] += len(set(a) ^ set(b))
                        columns = set(a[next(iter(common))]) - IGNORED_COLUMNS if common else set()
                        counts[f"{table}:restated"] += sum(
                            1 for key in common
                            if any(_norm(c, a[key].get(c)) != _norm(c, b[key].get(c)) for c in columns)
                        )
            finally:
                first.close()
                second.close()
        result[account_id] = {
            "statement_pairs_compared": pairs,
            "counts": {key: value for key, value in sorted(counts.items()) if value},
            "samples": samples,
        }
    return result


def flow_summary(v2: sqlite3.Connection, account_id: str, snapshot: Snapshot | None,
                 statements: list[Statement], window_start: str) -> dict[str, Any]:
    """External flows the website reader would see, and what history holds."""

    flows = _external_flows(v2, account_id, subject_type="physical_account")
    sections = Counter(
        row["section"] for row in v2.execute(
            "SELECT section FROM flex_rows WHERE account_id = ?", (account_id,)
        )
    )
    external_rows = 0
    for row in v2.execute(
        "SELECT data_json FROM flex_rows WHERE account_id = ? AND section = 'CashTransactions'",
        (account_id,),
    ):
        values = json.loads(row["data_json"])
        if str(values.get("accountId") or account_id) == account_id and _is_external_cash_transaction(values):
            external_rows += 1
    transfers = Counter()
    for statement in statements:
        if account_id not in statement.windows:
            continue
        checked = inspect_archived_xml(statement.content, expected_sha256=statement.sha256,
                                       accounts={account_id})
        for item in checked["statements"]:
            transfers[item["transfers_section"]] += 1
    history = []
    if snapshot is not None:
        for row in snapshot.rows.get("cash_transactions", ()):
            day = str(row.get("effective_date") or "")
            if day and day < window_start and _is_external_cash_transaction(
                {"type": row.get("transaction_type"), "description": row.get("description")}
            ):
                history.append({"date": day, "currency": row.get("currency"), "amount": _show(row.get("amount_text"))})
    return {
        "flex_row_sections": dict(sorted(sections.items())),
        "transfers_sections": dict(sorted(transfers.items())),
        "external_cash_transaction_rows": external_rows,
        "window_flows": {day: amount for day, amount in sorted(flows.items()) if amount},
        "history_external_cash_transactions": sorted(history, key=lambda item: item["date"]),
    }


def _account_rows(v2: sqlite3.Connection, account_id: str) -> int:
    return sum(len(_table_rows(v2, table, account_id)) for table in ACCOUNT_TABLES)


# ---------------------------------------------------------------- live check

def live_reference(connection: Any, *, tenant_id: str, account_id: str, v2: sqlite3.Connection,
                   window: set[str]) -> tuple[dict[str, list[dict]], dict[str, list[str]], dict[str, Any]]:
    """Read the live rows a first Flex pull would touch. Read-only connection required."""

    first, last = min(window), max(window)
    rows: dict[str, list[dict]] = {}
    columns: dict[str, list[str]] = {}
    for table in (*NATURAL_TABLES, *EVENT_TABLES):
        date_column = ACCOUNT_TABLES[table][0]
        base = (f"SELECT t.* FROM {table} t JOIN accounts a ON a.account_id = t.account_id "
                "WHERE a.tenant_id = %s AND t.account_id = %s AND ")
        if table in EVENT_TABLES:
            mine = _table_rows(v2, table, account_id)
            cursor = connection.execute(
                base + f"(t.{date_column} BETWEEN %s AND %s OR t.source_key = ANY(%s) OR t.row_sha256 = ANY(%s))",
                (tenant_id, account_id, first, last, [r["source_key"] for r in mine],
                 [r["row_sha256"] for r in mine]),
            )
        else:
            cursor = connection.execute(base + f"t.{date_column} BETWEEN %s AND %s",
                                        (tenant_id, account_id, first, last))
        names = [item.name for item in cursor.description or ()]
        columns[table] = [name for name in names if name != "tenant_id"]
        rows[table] = [dict(zip(names, values)) for values in cursor.fetchall()]
    extra = {
        "nav_base_currency_rows": int(connection.execute(
            "SELECT COUNT(*) FROM nav_snapshots n JOIN accounts a ON a.account_id = n.account_id "
            "WHERE a.tenant_id = %s AND n.account_id = %s AND n.currency = 'BASE'",
            (tenant_id, account_id),
        ).fetchone()[0]),
        "flex_row_sections": {
            str(section): int(count) for section, count in connection.execute(
                "SELECT section, COUNT(*) FROM flex_rows WHERE tenant_id = %s AND account_id = %s "
                "GROUP BY section ORDER BY section", (tenant_id, account_id),
            ).fetchall()
        },
    }
    return rows, columns, extra


def open_read_only(database_url: str, tenant_id: str):
    import psycopg
    from psycopg import IsolationLevel

    connection = psycopg.connect(database_url, options="-c default_transaction_read_only=on")
    connection.read_only = True
    connection.isolation_level = IsolationLevel.REPEATABLE_READ
    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    if connection.execute("SHOW transaction_read_only").fetchone()[0] != "on":
        connection.close()
        raise DryRunError("The live check requires a read-only transaction")
    return connection


def live_check(connection: Any, v2: sqlite3.Connection, *, tenant_id: str, accounts: Iterable[str],
               statements: list[Statement], lookback_days: int,
               unchecked: Iterable[str] = ()) -> dict[str, Any]:
    """Would the first pull duplicate or overwrite live rows? `unchecked` accounts have no snapshot."""

    unchecked = set(unchecked)
    anchor = max(statement.to_date for statement in statements)
    wanted = _dates((date.fromisoformat(anchor) - timedelta(days=lookback_days - 1)).isoformat(), anchor)
    result: dict[str, Any] = {"window": [min(wanted), max(wanted)], "lookback_days": lookback_days, "accounts": {}}
    status = "PASS"
    for account_id in accounts:
        covered, end_dates = account_coverage(statements, account_id)
        window = wanted & covered
        failures: list[str] = []
        if window != wanted:
            failures.append(f"statements cover {len(window)} of the last {lookback_days} days")
        if not window:
            result["accounts"][account_id] = {"status": "FAIL", "failures": failures}
            status = "FAIL"
            continue
        rows, columns, extra = live_reference(connection, tenant_id=tenant_id, account_id=account_id,
                                              v2=v2, window=window)
        # Nothing live to collide with: the pull only adds this account's rows.
        new_in_live = account_id in unchecked and not any(rows.values())
        tables = {}
        for table in (*NATURAL_TABLES, *EVENT_TABLES):
            date_column = ACCOUNT_TABLES[table][0]
            mine = [row for row in _table_rows(v2, table, account_id) if row.get(date_column) in window]
            in_scope = _scope(table, window, end_dates, {str(r.get(date_column)) for r in mine})
            tables[table] = compare_table(
                table, mine, rows[table], set(columns[table]) & set(_table_columns(v2, table)),
                in_scope=in_scope, before_start=lambda day: False,
            )
        # Natural-key rows absent from live fill a gap, unless live holds another key for that
        # date (a second NAV row, doubled holdings). Event keys absent from live are new rows.
        drift = []
        for table in NATURAL_TABLES:
            compared = tables[table]
            compared["would_insert"] = compared.pop("extra_in_v2")
            dates = compared.get("dates", {})
            if "extra_in_v2" in dates:
                dates["would_insert"] = dates.pop("extra_in_v2")
            shared = set(dates.get("would_insert", ())) & set(dates.get("missing_in_v2", ()))
            compared["dates_with_another_live_key"] = len(shared)
            if shared:
                drift.append(f"{table}: on {len(shared)} dates live holds a row under another key; "
                             "the first pull would add a second row beside it")
        if new_in_live:
            for table in EVENT_TABLES:
                tables[table]["new_rows"] = tables[table].pop("extra_in_v2")
        failures += drift + unexplained(tables, missing_fails=False)
        if extra["nav_base_currency_rows"]:
            failures.append(f"live has {extra['nav_base_currency_rows']} NAV rows in currency BASE")
        result["accounts"][account_id] = {
            "status": "FAIL" if failures else "PASS", "failures": failures,
            "checked_against_snapshot": account_id not in unchecked, "new_in_live": new_in_live,
            "days_checked": len(window), "tables": tables, **extra,
        }
        if failures:
            status = "FAIL"
    result["status"] = status
    return result


# ------------------------------------------------------------------- driver

def _allowed_unchecked(allow: Iterable[str], accounts: list[str]) -> set[str]:
    """Full ids, or the masked form the summary prints (***123)."""

    masker = AccountMasker(accounts)
    by_mask = {masker(account): account for account in accounts}
    allowed = set()
    for item in allow:
        account = item if item in accounts else by_mask.get(item)
        if account is None:
            raise DryRunError(f"--allow-unchecked-account {item} is not an account in the statements")
        allowed.add(account)
    return allowed


def run_dry_run(statements: Iterable[Statement], snapshots: Iterable[Snapshot], *, work_dir: Path,
                tenant_id: str = DEFAULT_TENANT_ID, live_connection: Any = None,
                open_live: Callable[[], Any] | None = None, lookback_days: int = 14,
                allow_unchecked: Iterable[str] = ()) -> dict[str, Any]:
    """Compare v2's load with the snapshots. `open_live` opens the read-only connection
    only when the offline work is done; that connection is closed afterwards."""

    ordered = order_statements(statements)
    if not ordered:
        raise DryRunError("At least one statement is required")
    by_account: dict[str, Snapshot] = {}
    for snapshot in snapshots:
        if snapshot.account_id in by_account:
            raise DryRunError("Two snapshots were supplied for one account")
        by_account[snapshot.account_id] = snapshot
    if not by_account:
        raise DryRunError("At least one snapshot is required")
    statement_accounts = sorted({account for s in ordered for account in s.windows})
    for account_id in by_account:
        if account_id not in statement_accounts:
            raise DryRunError("A snapshot account does not appear in any statement")
    allowed = _allowed_unchecked(allow_unchecked, statement_accounts)

    instruments = [item for snapshot in by_account.values() for item in snapshot.instruments]
    database_path = load_with_v2(ordered, work_dir / "sequential", tenant_id=tenant_id, instruments=instruments)
    stability = window_stability(ordered, work_dir, tenant_id=tenant_id, accounts=sorted(by_account))
    v2 = sqlite3.connect(database_path)
    v2.row_factory = sqlite3.Row
    report: dict[str, Any] = {
        "tool": RUN_TYPE, "version": REPORT_VERSION, "generated_at": utc_now_iso(),
        "inputs": {
            "statements": [{"name": s.name, "sha256": s.sha256, "bytes": len(s.content),
                            "periods": {a: list(w) for a, w in sorted(s.windows.items())}} for s in ordered],
            "snapshots": [{"name": s.name, "sha256": s.sha256, "account": s.account_id,
                           "start_date": s.start_date, "generated_at": s.generated_at,
                           "format_version": s.format_version} for s in by_account.values()],
        },
        "accounts": {},
        "accounts_not_compared": {},
        "failures": [],
    }
    status = "PASS"
    try:
        for account_id in sorted(by_account):
            snapshot = by_account[account_id]
            covered, end_dates = account_coverage(ordered, account_id)
            tables = compare_account(v2, account_id, snapshot.rows, snapshot.columns, covered=covered,
                                     end_dates=end_dates, start_date=snapshot.start_date)
            flows = flow_summary(v2, account_id, snapshot, ordered, min(covered))
            failures = unexplained(tables)
            warnings = []
            unstable = stability[account_id]["counts"]
            for name, count in unstable.items():
                if name.endswith(":hash_key_changed"):
                    failures.append(f"{name.split(':')[0]}: {count} content keys change with the statement period")
                else:
                    warnings.append(f"{name}: {count}")
            if flows["window_flows"]:
                failures.append(
                    f"{len(flows['window_flows'])} external-flow days in the statement window: "
                    "the switch changes returns on those days"
                )
            if flows["external_cash_transaction_rows"] and not flows["window_flows"]:
                failures.append("external cash transactions without a convertible amount")
            if flows["history_external_cash_transactions"]:
                warnings.append(
                    f"{len(flows['history_external_cash_transactions'])} earlier deposits or withdrawals "
                    "are not external flows today; see the flow backfill"
                )
            if flows["transfers_sections"].get("missing"):
                warnings.append("some statements have no Transfers section")
            for table, result in tables.items():
                if result.get("raw_only_differences"):
                    warnings.append(f"{table}: {result['raw_only_differences']} rows differ only in raw attributes")
            if snapshot.generated_at and max(end_dates) >= snapshot.generated_at[:10]:
                warnings.append("a statement ends on or after the snapshot date")
            report["accounts"][account_id] = {
                "status": "FAIL" if failures else "PASS",
                "failures": failures, "warnings": warnings,
                "coverage": {"first": min(covered), "last": max(covered), "days": len(covered),
                             "statement_end_dates": sorted(end_dates)},
                "tables": tables, "flows": flows, "window_stability": stability[account_id],
            }
            if failures:
                status = "FAIL"
        unchecked = [account for account in statement_accounts if account not in by_account]
        for account_id in unchecked:
            report["accounts_not_compared"][account_id] = {
                "rows_v2_would_load": _account_rows(v2, account_id), "allowed": account_id in allowed}
            if account_id not in allowed:
                report["failures"].append(
                    f"{account_id} is in the statements but has no snapshot; the pull would load it unchecked "
                    "(allow it with --allow-unchecked-account)")
                status = "FAIL"
        opened = None
        try:
            if live_connection is None and open_live is not None:
                live_connection = opened = open_live()
            if live_connection is not None:
                report["live_check"] = live_check(live_connection, v2, tenant_id=tenant_id,
                                                  accounts=sorted(by_account) + unchecked, statements=ordered,
                                                  lookback_days=lookback_days, unchecked=unchecked)
                if report["live_check"]["status"] != "PASS":
                    status = "FAIL"
        finally:
            if opened is not None:
                opened.rollback()
                opened.close()
    finally:
        v2.close()
    report["status"] = status
    return report


def masked_report(report: Mapping[str, Any]) -> tuple[dict[str, Any], AccountMasker]:
    accounts = set(report.get("accounts", {})) | set(report.get("accounts_not_compared", {}))
    for item in report.get("inputs", {}).get("statements", ()):
        accounts |= set(item.get("periods", {}))
    masker = AccountMasker(accounts)
    return json.loads(masker.scrub(json.dumps(report, sort_keys=True, default=str))), masker


def summary_lines(report: Mapping[str, Any]) -> list[str]:
    """Counts only: no amounts, keys or raw values. Expects a masked report."""

    statements = report["inputs"]["statements"]
    first = min((p[0] for s in statements for p in s["periods"].values()), default="")
    last = max((p[1] for s in statements for p in s["periods"].values()), default="")
    lines = [f"medallion switch dry run: {report['status']}",
             f"statements: {len(statements)} covering {first}..{last}; snapshots: {len(report['inputs']['snapshots'])}"]
    names = ("matched", "different", "rekeyed", "extra_in_v2", "missing_in_v2", "raw_only_differences")
    for account, item in sorted(report["accounts"].items()):
        lines.append(f"account {account}: {item['status']} ({item['coverage']['days']} days)")
        for table, result in item["tables"].items():
            counts = " ".join(f"{name}={result.get(name, 0)}" for name in names)
            lines.append(f"  {table:<19} v2={result['v2_rows']} reference={result['reference_rows_in_scope']} {counts}")
        flows = item["flows"]
        lines.append(f"  flows: window_days={len(flows['window_flows'])} "
                     f"history_rows={len(flows['history_external_cash_transactions'])} "
                     f"transfers_sections={flows['transfers_sections']}")
        for text in item["failures"]:
            lines.append(f"  FAIL {text}")
        for text in item["warnings"]:
            lines.append(f"  warn {text}")
    if report.get("accounts_not_compared"):
        lines.append("accounts in the statements without a snapshot (the Flex pull would load them too): "
                     + ", ".join(f"{a} ({v['rows_v2_would_load']} rows, "
                                 f"{'allowed' if v.get('allowed') else 'not allowed'})"
                                 for a, v in sorted(report["accounts_not_compared"].items())))
    for text in report.get("failures", ()):
        lines.append(f"FAIL {text}")
    live = report.get("live_check")
    if live:
        lines.append(f"live check: {live['status']} window {live['window'][0]}..{live['window'][1]}")
        for account, item in sorted(live["accounts"].items()):
            note = "" if item.get("checked_against_snapshot", True) else (
                " (no snapshot; new in live)" if item.get("new_in_live") else " (no snapshot)")
            lines.append(f"  account {account}: {item['status']}{note}")
            for table, result in item.get("tables", {}).items():
                counts = " ".join(f"{name}={result.get(name, 0)}" for name in
                                  ("matched", "different", "rekeyed", "extra_in_v2", "would_insert",
                                   "missing_in_v2", "dates_with_another_live_key", "new_rows"))
                lines.append(f"    {table:<19} v2={result['v2_rows']} {counts}")
            for text in item["failures"]:
                lines.append(f"    FAIL {text}")
    return lines
