""""As known on" history for IBKR corrections (review item U8): fill it, check it, merge FX rows.

docs/MEDALLION_AS_KNOWN_ON.md has the design. The held migration
infra/medallion/drafts/u8_silver_history.sql adds silver.<table>_history beside each tracked
table, and a trigger that records every write once public.silver_history_settings.recording
is true. This module:

- replays the kept raw IBKR files in the order they reached silver, with today's loader, into
  a throwaway SQLite, and writes down every change as a version recorded at the time the real
  load ran (`Replay`). The files are v2's own statements (bronze.raw_reports) and
  account-sync's dated copies of v1.1's tables (input-snapshots, one import run each);
- compares the replay's end state with the live tables; each live row the files cannot
  explain gets one baseline version (`reconcile`);
- inserts the versions and turns recording on in one transaction (`apply_backfill`);
- checks that each table equals its current versions (`check`);
- for the FX step, fills fx_transactions.event_key and merges FX rows IBKR re-sent with new
  amounts (`plan_fx_merge`, `apply_fx_merge`), and puts merged rows back (`apply_fx_unmerge`);
- counts, read-only, the FX events with more than one current row now and after the merge, and
  the FX amounts the merge changes (`fx_check`), for a restored copy before and after the FX step.

Nothing here runs unless scripts/medallion_silver_history.py is run by hand. Its reports hold
counts and table names only: no account numbers, amounts or names.
"""
from __future__ import annotations

import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from .db import DEFAULT_TENANT_ID, connect, initialize, start_run, transaction
from .loader import (FX_COLUMNS, FX_EVENT_FIELDS, fx_event_identity, fx_event_key, fx_transaction_values,
                     load_report)
from .parser import parse_flex_xml
from .pilot_snapshot import ACCOUNT_TABLES
from .util import parse_date_text, pick

# table -> (valid-date column, key columns): as in the held migration.
TRACKED: dict[str, tuple[str, tuple[str, ...]]] = {
    "trades": ("trade_date", ("account_id", "source_key")),
    "cash_transactions": ("effective_date", ("account_id", "source_key")),
    "fx_transactions": ("report_date", ("account_id", "source_key")),
    "fx_rates": ("rate_date", ("account_id", "rate_date", "from_currency", "to_currency")),
    "nav_snapshots": ("snapshot_date", ("account_id", "snapshot_date", "currency", "asset_class")),
}
EVENT_TABLES = ("trades", "cash_transactions", "fx_transactions")
HISTORY_COLUMNS = ("valid_date", "recorded_from", "recorded_to", "opened_by", "closed_by", "recorded_run_id",
                   "lineage_run_id", "opened_note", "closed_note")
SEEN = "last_seen_run_id"  # moves on every re-send; never a new version
STATEMENT, SNAPSHOT = "statement", "snapshot"
SNAPSHOT_RUN_TYPE = "staging-snapshot-import"  # account-sync (ibkr_etl.pilot_import)
REPLAY_RUN_TYPE = "silver-history-replay"
NOTE_UNEXPLAINED = "U8 backfill: not explained by a kept raw file"
NOTE_DIFFERS = "U8 backfill: live differs from the replay"
NOTE_NOT_LIVE = "U8 backfill: not in the live table"
NOTE_FX_MERGED = "U8 FX step: the same FX event re-sent with new amounts"
NOTE_FX_KEY = "U8 FX step: event key filled"
NOTE_FX_UNDONE = "U8 FX step undone: merged row put back"
LOCK_SECONDS = 30


class HistoryError(RuntimeError):
    pass


def parse_time(value: Any) -> datetime | None:
    """A stored ISO time (ingestion_runs and raw_reports keep text) as an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def valid_date(value: Any) -> date | None:
    """As silver.history_date: a yyyy-mm-dd text, else NULL."""
    text = str(value or "")
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class Load:
    """One write that reached silver: a raw statement or an account-sync copy."""
    kind: str
    at: datetime
    run_id: int
    sha256: str
    tenant: str
    run_type: str = ""


@dataclass
class Version:
    values: tuple  # the table's columns, in the replay's (or live) order
    valid_date: date | None
    recorded_from: datetime
    opened_by: str
    recorded_run_id: int | None
    recorded_to: datetime | None = None
    closed_by: str | None = None
    opened_note: str | None = None
    closed_note: str | None = None


def order_loads(loads: Iterable[Load]) -> list[Load]:
    """In the order they reached silver, each run once."""
    unique = {(load.kind, load.run_id): load for load in loads}
    return sorted(unique.values(), key=lambda load: (load.at, load.run_id, load.kind))


# ---------------------------------------------------------------- replay


class Replay:
    """The silver tables rebuilt from the kept files, one load at a time, with every change as a version."""

    def __init__(self, work_dir: Path, tables: Iterable[str] = tuple(TRACKED), *,
                 instruments: Iterable[Mapping[str, Any]] = (), legacy_key_compat: bool = False) -> None:
        work_dir.mkdir(parents=True, exist_ok=True)
        self.path = work_dir / "replay.sqlite"
        initialize(self.path)
        self.connection = connect(self.path)
        self.tables = tuple(tables)
        self.legacy_key_compat = legacy_key_compat
        self.columns = {table: self._table_columns(table) for table in self.tables}
        for table in self.tables:
            self.connection.execute(f"CREATE INDEX IF NOT EXISTS replay_seen_{table} ON {table} ({SEEN})")
        self.current: dict[str, dict[tuple, Version]] = {table: {} for table in self.tables}
        self.versions: dict[str, list[Version]] = {table: [] for table in self.tables}
        self.counts: Counter = Counter()
        # (account, table) -> statements that carried that table: (from, to, keys)
        self.coverage: dict[tuple[str, str], list[tuple[str, str, frozenset]]] = defaultdict(list)
        self.row_digests: dict[str, set[tuple[str, str]]] = {table: set() for table in self.tables}
        self._seed_instruments(instruments)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def _table_columns(self, table: str) -> list[str]:
        return [row[1] for row in self.connection.execute(f'PRAGMA table_info("{table}")')]

    def _seed_instruments(self, instruments: Iterable[Mapping[str, Any]]) -> None:
        # Conid resolution sees the instruments live holds, as the switch rehearsal did.
        allowed = self._table_columns("instruments")
        for item in instruments:
            values = {key: item.get(key) for key in allowed if key in item}
            if values.get("conid"):
                names = list(values)
                self.connection.execute(
                    f"INSERT OR IGNORE INTO instruments({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                    [values[name] for name in names])

    def load(self, load: Load, content: bytes | Path) -> None:
        local = start_run(self.connection, REPLAY_RUN_TYPE, None, None)
        if load.kind == STATEMENT:
            report = parse_flex_xml(content if isinstance(content, bytes) else Path(content).read_bytes())
            with transaction(self.connection):
                load_report(self.connection, report, local, legacy_key_compat=self.legacy_key_compat,
                            fx_event_keys=False)
            windows = {row.account_id: (parse_date_text(row.attributes.get("fromDate", "")) or "",
                                        parse_date_text(row.attributes.get("toDate", "")) or "")
                       for row in report.rows if row.section == "Statement"}
        else:
            with transaction(self.connection):
                self._load_snapshot(Path(content), local)
            windows = {}
        self.counts[load.kind] += 1
        self._collect(load, local, windows)

    def _load_snapshot(self, path: Path, local: int) -> None:
        """As account-sync's import (ibkr_etl.pilot_import): every row upserted by its key, seen by this run."""
        source = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            tables = {"instruments": ("", "", ("conid",)), **ACCOUNT_TABLES}
            for table, (_, _, keys) in tables.items():
                allowed = self._table_columns(table)
                have = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
                names = [name for name in have if name in allowed and name != SEEN]
                if not names:
                    continue
                rows = source.execute(f'SELECT {", ".join(names)} FROM "{table}"').fetchall()
                seen = [SEEN] if SEEN in allowed else []  # the copies leave it out; the import sets it
                listed = names + seen
                updates = [name for name in listed if name not in keys]
                sql = (f"INSERT INTO {table} ({', '.join(listed)}) VALUES ({', '.join('?' for _ in listed)}) "
                       f"ON CONFLICT({', '.join(keys)}) DO "
                       + (f"UPDATE SET {', '.join(f'{name} = excluded.{name}' for name in updates)}"
                          if updates else "NOTHING"))
                self.connection.executemany(sql, [(*row, *([local] if seen else [])) for row in rows])
        finally:
            source.close()

    def _collect(self, load: Load, local: int, windows: Mapping[str, tuple[str, str]]) -> None:
        for table in self.tables:
            columns = self.columns[table]
            date_column, keys = TRACKED[table]
            key_at = [columns.index(name) for name in keys]
            seen_at, date_at = columns.index(SEEN), columns.index(date_column)
            sha_at = columns.index("row_sha256") if "row_sha256" in columns else None
            touched: dict[str, set] = defaultdict(set)
            for row in self.connection.execute(f"SELECT * FROM {table} WHERE {SEEN} = ?", (local,)):
                values = list(row)
                values[seen_at] = load.run_id
                values = tuple(values)
                key = tuple(values[i] for i in key_at)
                touched[key[0]].add(key)
                if sha_at is not None and load.kind == SNAPSHOT:
                    self.row_digests[table].add((key[0], values[sha_at]))
                current = self.current[table].get(key)
                if current is not None and _same(current.values, values, seen_at):
                    continue
                if current is not None:
                    current.recorded_to, current.closed_by = load.at, "restated"
                version = Version(values, valid_date(values[date_at]), load.at,
                                  "restated" if current is not None else "first", load.run_id)
                self.versions[table].append(version)
                self.current[table][key] = version
            if table in EVENT_TABLES:
                for account, (start, end) in windows.items():
                    if touched.get(account):
                        self.coverage[(account, table)].append((start, end, frozenset(touched[account])))

    def stopped_sending(self, accounts: set[str] | None = None) -> Counter:
        """Event rows the latest statement covering their date (and carrying that table) no longer has."""
        found: Counter = Counter()
        for table in EVENT_TABLES:
            if table not in self.tables:
                continue
            date_at = self.columns[table].index(TRACKED[table][0])
            for key, version in self.current[table].items():
                if accounts is not None and key[0] not in accounts:
                    continue
                day = str(version.values[date_at] or "")
                covering = [keys for start, end, keys in self.coverage.get((key[0], table), ())
                            if start and end and start <= day <= end]
                if covering and key not in covering[-1]:
                    found[table] += 1
        return found


def _same(left: tuple, right: tuple, seen_at: int) -> bool:
    return left[:seen_at] == right[:seen_at] and left[seen_at + 1:] == right[seen_at + 1:]


def v11_explained(replay: Replay, v11_digests: set[tuple[str, str]], accounts: set[str] | None = None) -> Counter:
    """Rows first seen in an account-sync copy whose raw record is in a v1.1 statement (same row_sha256)."""
    found: Counter = Counter()
    for table, digests in replay.row_digests.items():
        for account, digest in digests:
            if accounts is None or account in accounts:
                found[f"{table}:{'explained' if (account, digest) in v11_digests else 'not_found'}"] += 1
    return found


def statement_digests(contents: Iterable[bytes]) -> set[tuple[str, str]]:
    return {(row.account_id, row.row_sha256) for content in contents for row in parse_flex_xml(content).rows}


# ---------------------------------------------------------------- reconcile with live


@dataclass
class TablePlan:
    table: str
    columns: list[str]  # live order
    versions: list[Version] = field(default_factory=list)  # values in live order
    counts: Counter = field(default_factory=Counter)

    def problems(self) -> int:
        return self.counts["differs"] + self.counts["replay_only"]


def reconcile(replay: Replay, table: str, live_columns: list[str], live: Mapping[tuple, tuple], accounts: set[str],
              *, now: datetime, run_times: Mapping[int, datetime],
              first_seen: Mapping[tuple[str, str], datetime], accept_differences: bool = False) -> TablePlan:
    """The versions to insert for one table and one tenant's accounts, with counts."""
    plan = TablePlan(table, list(live_columns))
    replay_columns = replay.columns[table]
    position = {name: i for i, name in enumerate(replay_columns)}
    order = [position.get(name) for name in live_columns]

    def in_live_order(values: tuple) -> tuple:
        return tuple(None if i is None else values[i] for i in order)

    date_column, keys = TRACKED[table]
    seen_at, date_at = live_columns.index(SEEN), live_columns.index(date_column)
    key_at = [live_columns.index(name) for name in keys]
    current: dict[tuple, Version] = {}
    account_at = replay_columns.index("account_id")
    for version in replay.versions[table]:
        if version.values[account_at] not in accounts:
            continue
        copy = replace(version, values=in_live_order(version.values))
        plan.versions.append(copy)
        if copy.recorded_to is None:
            current[tuple(copy.values[i] for i in key_at)] = copy
        plan.counts[f"opened_{copy.opened_by}"] += 1
    for key, values in live.items():
        replayed = current.get(key)
        if replayed is not None and _same(replayed.values, values, seen_at):
            plan.counts["matched"] += 1
            continue
        if replayed is not None:
            plan.counts["differs"] += 1
            if not accept_differences:
                continue
            replayed.recorded_to, replayed.closed_by, replayed.closed_note = now, "restated", NOTE_DIFFERS
            note = NOTE_DIFFERS
            since = now
        else:
            plan.counts["unexplained"] += 1
            note = NOTE_UNEXPLAINED
            known = [run_times.get(values[seen_at])]
            if "source_key" in keys:
                known.append(first_seen.get((key[0], key[keys.index("source_key")])))
            since = min([moment for moment in known if moment is not None] + [now])
        plan.versions.append(Version(values, valid_date(values[date_at]), since, "baseline", values[seen_at],
                                     opened_note=note))
        plan.counts["opened_baseline"] += 1
    for key, replayed in current.items():
        if key not in live:
            plan.counts["replay_only"] += 1
            if accept_differences:
                replayed.recorded_to, replayed.closed_by, replayed.closed_note = now, "deleted", NOTE_NOT_LIVE
    plan.counts["live_rows"] = len(live)
    plan.counts["versions"] = len(plan.versions)
    return plan


# ---------------------------------------------------------------- database


def tracked_with_history(connection) -> list[str]:
    """The tracked tables whose history table exists (the migration has run)."""
    return [table for table in TRACKED
            if connection.execute("SELECT to_regclass(%s)", (f"silver.{table}_history",)).fetchone()[0]]


def table_columns(connection, table: str, schema: str = "silver") -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped "
        "ORDER BY attnum", (f"{schema}.{table}",)).fetchall()]


def use_tenant(connection, tenant: str) -> set[str]:
    """Scope this transaction to one tenant; its accounts. Explicit, so a login that bypasses row security works too."""
    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
    return {row[0] for row in connection.execute(
        "SELECT account_id FROM silver.accounts WHERE tenant_id = %s", (tenant,)).fetchall()}


def recording(connection) -> bool | None:
    if connection.execute("SELECT to_regclass('public.silver_history_settings')").fetchone()[0] is None:
        return None
    return bool(connection.execute("SELECT silver.history_recording()").fetchone()[0])


def read_live(connection, table: str, columns: list[str], accounts: set[str]) -> dict[tuple, tuple]:
    from psycopg import sql

    keys = TRACKED[table][1]
    key_at = [columns.index(name) for name in keys]
    rows = connection.execute(sql.SQL("SELECT {} FROM {} WHERE account_id = ANY(%s)").format(
        sql.SQL(", ").join(sql.Identifier(name) for name in columns), sql.Identifier("silver", table)),
        (sorted(accounts),)).fetchall()
    return {tuple(row[i] for i in key_at): tuple(row) for row in rows}


def gather_loads(connection, tenant: str, *, skip_run_types: Iterable[str] = ()) -> tuple[list[Load], Counter]:
    """Every raw statement and account-sync copy that reached this tenant's silver, with when and which run."""
    skip = set(skip_run_types)
    counts: Counter = Counter()
    loads = []
    for run_id, received_at, digest, run_type in connection.execute(
            "SELECT r.run_id, r.received_at, r.content_sha256, i.run_type FROM bronze.raw_reports r "
            "JOIN public.ingestion_runs i ON i.run_id = r.run_id WHERE r.tenant_id = %s ORDER BY r.run_id",
            (tenant,)).fetchall():
        # A raw_reports row commits with its load, so the rows reached silver whatever the run's status.
        if run_type in skip:
            counts[f"statements_skipped:{run_type}"] += 1
            continue
        loads.append(Load(STATEMENT, parse_time(received_at), int(run_id), digest, tenant, run_type))
    for run_id, started_at, digest, run_type in connection.execute(
            "SELECT i.run_id, i.started_at, i.raw_sha256, i.run_type FROM public.ingestion_runs i "
            "JOIN bronze.raw_reports r ON r.content_sha256 = i.raw_sha256 AND r.tenant_id = i.tenant_id "
            "WHERE i.tenant_id = %s AND i.status = 'success' AND i.run_id <> r.run_id ORDER BY i.run_id",
            (tenant,)).fetchall():
        # The same file loaded again (scripts/rebuild_from_raw.py): raw_reports keeps only its first run.
        if run_type in skip:
            counts[f"statements_skipped:{run_type}"] += 1
            continue
        counts["statements_loaded_again"] += 1
        loads.append(Load(STATEMENT, parse_time(started_at), int(run_id), digest, tenant, run_type))
    for run_id, completed_at, digest in connection.execute(
            "SELECT run_id, completed_at, raw_sha256 FROM public.ingestion_runs WHERE tenant_id = %s "
            "AND run_type = %s AND status = 'success' AND raw_sha256 IS NOT NULL ORDER BY run_id",
            (tenant, SNAPSHOT_RUN_TYPE)).fetchall():
        loads.append(Load(SNAPSHOT, parse_time(completed_at), int(run_id), digest, tenant, SNAPSHOT_RUN_TYPE))
    counts["statements"] = sum(1 for load in loads if load.kind == STATEMENT)
    counts["snapshots"] = sum(1 for load in loads if load.kind == SNAPSHOT)
    return loads, counts


def raw_report_paths(connection, tenant: str) -> dict[str, str]:
    """sha256 -> where the raw statement was kept (a blob URL or a local path)."""
    return dict(connection.execute("SELECT content_sha256, file_path FROM bronze.raw_reports WHERE tenant_id = %s",
                                   (tenant,)).fetchall())


def run_times(connection, tenant: str) -> dict[int, datetime]:
    found = {}
    for run_id, started_at, completed_at in connection.execute(
            "SELECT run_id, started_at, completed_at FROM public.ingestion_runs WHERE tenant_id = %s",
            (tenant,)).fetchall():
        moment = parse_time(completed_at) or parse_time(started_at)
        if moment is not None:
            found[int(run_id)] = moment
    return found


def first_seen(connection, tenant: str, times: Mapping[int, datetime]) -> dict[tuple[str, str], datetime]:
    """When bronze first held each record: the earliest time a baseline can claim."""
    found = {}
    for account, source_key, run_id in connection.execute(
            "SELECT account_id, source_key, min(first_seen_run_id) FROM bronze.flex_rows WHERE tenant_id = %s "
            "GROUP BY 1, 2", (tenant,)).fetchall():
        if run_id in times:
            found[(account, source_key)] = times[run_id]
    return found


def live_instruments(connection) -> list[dict]:
    cursor = connection.execute("SELECT * FROM silver.instruments")
    names = [column.name for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def insert_versions(connection, plan: TablePlan) -> int:
    from psycopg import sql

    names = [*HISTORY_COLUMNS, *plan.columns]
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier("silver", f"{plan.table}_history"), sql.SQL(", ").join(sql.Identifier(n) for n in names),
        sql.SQL(", ").join(sql.Placeholder() for _ in names))
    rows = [(v.valid_date, v.recorded_from, v.recorded_to, v.opened_by, v.closed_by, v.recorded_run_id, None,
             v.opened_note, v.closed_note, *v.values) for v in plan.versions]
    with connection.cursor() as cursor:
        cursor.executemany(statement, rows)
    return len(rows)


def lock_tables(connection, tables: Iterable[str], *, seconds: float = LOCK_SECONDS) -> None:
    """Writers wait, readers carry on. Never queues behind a long transaction: tries NOWAIT until `seconds`."""
    import psycopg
    from psycopg import sql

    targets = sql.SQL(", ").join(sql.Identifier("silver", table) for table in tables)
    deadline = time.monotonic() + seconds
    connection.execute("SELECT 1")  # inside the caller's transaction, so the lock lasts until it ends
    while True:
        try:
            with connection.transaction():
                connection.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE NOWAIT").format(targets))
            return
        except psycopg.errors.LockNotAvailable:
            if time.monotonic() >= deadline:
                raise HistoryError(f"the tables stayed busy for {seconds:.0f} seconds, so nothing was changed")
            time.sleep(0.2)


def history_counts(connection, table: str, accounts: set[str]) -> Counter:
    found: Counter = Counter()
    for opened, closed, count in connection.execute(
            f"SELECT opened_by, COALESCE(closed_by, 'current'), count(*) FROM silver.{table}_history "
            "WHERE account_id = ANY(%s) GROUP BY 1, 2", (sorted(accounts),)).fetchall():
        found[f"opened_{opened}"] += count
        found[f"closed_{closed}"] += count
    return found


def check_table(connection, table: str, accounts: set[str]) -> dict[str, int]:
    """Problem counts for one table, all 0 when the history is sound, plus its version counts."""
    keys = ", ".join(TRACKED[table][1])
    listed = ", ".join(f'"{name}"' for name in table_columns(connection, table) if name != SEEN)
    scope = (sorted(accounts),)

    def count(query: str, parameters=scope) -> int:
        return int(connection.execute(query, parameters).fetchone()[0])

    known = f"silver.{table}_as_known(now())"
    result = {
        "table_rows_not_current": count(
            f"SELECT count(*) FROM (SELECT {listed} FROM silver.{table} WHERE account_id = ANY(%s) EXCEPT ALL "
            f"SELECT {listed} FROM {known} WHERE account_id = ANY(%s)) d", scope * 2),
        "current_versions_not_in_table": count(
            f"SELECT count(*) FROM (SELECT {listed} FROM {known} WHERE account_id = ANY(%s) EXCEPT ALL "
            f"SELECT {listed} FROM silver.{table} WHERE account_id = ANY(%s)) d", scope * 2),
        "keys_with_two_current_versions": count(
            f"SELECT count(*) FROM (SELECT 1 FROM silver.{table}_history WHERE recorded_to IS NULL "
            f"AND account_id = ANY(%s) GROUP BY {keys} HAVING count(*) > 1) d"),
        "overlapping_versions": count(
            f"SELECT count(*) FROM (SELECT recorded_from, lag(recorded_to) OVER w AS before_to, "
            f"lag(version_id) OVER w AS before FROM silver.{table}_history WHERE account_id = ANY(%s) "
            f"WINDOW w AS (PARTITION BY {keys} ORDER BY recorded_from, version_id)) d "
            "WHERE before IS NOT NULL AND (before_to IS NULL OR recorded_from < before_to)"),
        "removals_without_a_note": count(
            f"SELECT count(*) FROM silver.{table}_history WHERE account_id = ANY(%s) "
            "AND closed_by IN ('deleted', 'merged', 'withdrawn') AND closed_note IS NULL"),
    }
    return result


def check(connection, tenants: Iterable[str], tables: Iterable[str] | None = None) -> dict:
    """Each table equals its current versions (apart from last_seen_run_id); versions never overlap."""
    tables = list(tables or tracked_with_history(connection))
    report: dict[str, Any] = {"recording": recording(connection), "tenants": {}, "ok": True}
    if not tables:
        report.update(ok=False, error="no history tables: the migration has not run")
        return report
    for label, tenant in tenant_labels(tenants).items():
        accounts = use_tenant(connection, tenant)
        entry = report["tenants"][label] = {"accounts": len(accounts), "tables": {}}
        for table in tables:
            problems = check_table(connection, table, accounts)
            entry["tables"][table] = {"problems": problems, "versions": dict(history_counts(connection, table,
                                                                                              accounts))}
            if any(problems.values()):
                report["ok"] = False
    return report


def tenant_labels(tenants: Iterable[str]) -> dict[str, str]:
    """Report labels that never show a tenant id other than the repository's default one."""
    labels, n = {}, 0
    for tenant in dict.fromkeys(tenants):
        if tenant == DEFAULT_TENANT_ID:
            labels["default"] = tenant
        else:
            n += 1
            labels[f"tenant#{n}"] = tenant
    return labels


def apply_backfill(connection, replay: Replay, tenants: Iterable[str], *, accept_differences: bool = False,
                   lock_seconds: float = LOCK_SECONDS, dry_run: bool = False, note: str = "U8 backfill") -> dict:
    """Insert the replay's versions and baselines and turn recording on, in one transaction, then check.

    With dry_run nothing is locked or written: the same plan, as counts. Refuses, changing nothing,
    when recording is already on, a history table is not empty, or (without accept_differences)
    the replay's end state differs from a live table.
    """
    tables = tracked_with_history(connection)
    if sorted(tables) != sorted(replay.tables):
        raise HistoryError(f"history tables {sorted(tables)} do not match the replay's {sorted(replay.tables)}")
    state = recording(connection)
    if state and not dry_run:
        raise HistoryError("recording is already on")
    if not dry_run:
        lock_tables(connection, tables, seconds=lock_seconds)
    now = connection.execute("SELECT now()").fetchone()[0]
    report: dict[str, Any] = {"recording_before": state, "tenants": {}, "ready": True}
    accounted: set[str] = set()
    for label, tenant in tenant_labels(tenants).items():
        accounts = use_tenant(connection, tenant)
        accounted |= accounts
        times = run_times(connection, tenant)
        seen = first_seen(connection, tenant, times)
        entry = report["tenants"][label] = {"accounts": len(accounts), "tables": {},
                                            "stopped_sending": dict(replay.stopped_sending(accounts))}
        for table in tables:
            existing = connection.execute(f"SELECT count(*) FROM silver.{table}_history").fetchone()[0]
            if existing and not dry_run:
                raise HistoryError(f"silver.{table}_history already holds versions")
            columns = table_columns(connection, table)
            plan = reconcile(replay, table, columns, read_live(connection, table, columns, accounts), accounts,
                             now=now, run_times=times, first_seen=seen, accept_differences=accept_differences)
            if existing:
                plan.counts["history_not_empty"] = existing
            entry["tables"][table] = dict(plan.counts)
            if plan.problems() or existing:
                report["ready"] = False
                if not dry_run and not accept_differences:
                    raise HistoryError(f"{table}: the replay's end state differs from the live table "
                                       f"({plan.counts['differs']} differ, {plan.counts['replay_only']} only in "
                                       "the replay); explain them, or pass accept_differences")
            if not dry_run:
                insert_versions(connection, plan)
    outside = sum(1 for table in replay.tables for key in replay.current[table] if key[0] not in accounted)
    report["replayed_rows_outside_these_tenants"] = outside
    if dry_run:
        return report
    connection.execute("UPDATE public.silver_history_settings SET recording = true, changed_at = now(), "
                       "changed_by = current_user, note = %s", (note,))
    result = check(connection, tenants, tables)
    report["check"] = result
    if not result["ok"]:
        raise HistoryError("the check after the backfill failed, so nothing was changed")
    return report


# ---------------------------------------------------------------- the FX step


@dataclass
class FxIndex:
    """From raw statements: each FX record's event key in the latest statement that had it."""
    latest: dict[tuple[str, str], tuple[str, int]] = field(default_factory=dict)
    statements: dict[tuple[str, str], set[int]] = field(default_factory=lambda: defaultdict(set))
    # (account, row_sha256) -> the parser's key for that raw record: finds a row account-sync
    # copied from v1.1 under another key (the loader's legacy-key step matches the same way).
    digests: dict[tuple[str, str], str] = field(default_factory=dict)
    count: int = 0
    records: int = 0
    without_report_date: int = 0  # their identity takes the statement's end date instead

    def add(self, content: bytes) -> None:
        order = self.count
        self.count += 1
        occurrences: Counter = Counter()
        for row in parse_flex_xml(content).rows:
            if row.record_type.lower() != "fxtransaction":
                continue
            self.records += 1
            if not pick(row.attributes, "reportDate"):
                self.without_report_date += 1
            identity = fx_event_identity(fx_transaction_values(row, 0))
            occurrences[(row.account_id, identity)] += 1
            key = (row.account_id, row.source_key)
            self.latest[key] = (fx_event_key(identity, occurrences[(row.account_id, identity)]), order)
            self.statements[key].add(order)
            self.digests[(row.account_id, row.row_sha256)] = row.source_key


@dataclass
class FxPlan:
    fill: dict[tuple[str, str], str] = field(default_factory=dict)  # (account, source_key) -> event key
    merge: list[tuple[str, str]] = field(default_factory=list)  # (account, source_key) to remove
    counts: Counter = field(default_factory=Counter)
    # For the read-only check: each event's live rows, oldest report first, and what the merge does with them.
    groups: dict[tuple[str, str], list[tuple[str, str]]] = field(default_factory=dict)
    outcome: dict[tuple[str, str], str] = field(default_factory=dict)
    located: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)  # live key -> index key


def plan_fx_merge(rows: Iterable[tuple], index: FxIndex) -> FxPlan:
    """rows: (account, source_key, current event_key[, row_sha256]). Same event, never in one statement together: merge.

    A row whose key no statement has is looked up by its raw record (row_sha256), unless the
    parser's key for that record is a live row too (the same record twice: left, and counted).
    """
    plan = FxPlan()
    rows = [tuple(row) for row in rows]
    live = {(row[0], row[1]) for row in rows}
    held = {(row[0], row[2]): row[1] for row in rows if row[2] is not None}
    groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for account, source_key, current, *rest in rows:
        plan.counts["rows"] += 1
        at = (account, source_key)
        found = index.latest.get(at)
        if found is None and rest and rest[0]:
            parser_key = index.digests.get((account, rest[0]))
            if parser_key is not None:
                if (account, parser_key) in live:
                    plan.counts["same_record_under_two_keys"] += 1
                    continue
                at = (account, parser_key)
                found = index.latest[at]
                plan.counts["found_by_raw_record"] += 1
        if found is None:
            plan.counts["not_in_any_statement"] += 1
            continue
        if current is not None and current != found[0]:
            plan.counts["event_key_differs"] += 1
            continue
        plan.located[(account, source_key)] = at
        groups[(account, found[0])].append((account, source_key))
    for event, members in groups.items():
        account, event_key = event
        seen = [index.statements[plan.located[member]] for member in members]
        members = sorted(members, key=lambda member: index.latest[plan.located[member]][1])
        plan.groups[event] = members
        if any(seen[i] & seen[j] for i in range(len(seen)) for j in range(i + 1, len(seen))):
            plan.counts["groups_seen_together"] += 1  # an occurrence moved: left as they are
            plan.outcome[event] = "seen_together"
            continue
        kept = members[-1]  # the latest report stays current
        if held.get((account, event_key), kept[1]) != kept[1]:
            plan.counts["event_key_held_by_another_row"] += 1
            plan.outcome[event] = "held_by_another_row"
            continue
        plan.outcome[event] = "merged" if len(members) > 1 else "single"
        if len(members) > 1:
            plan.merge += members[:-1]
            plan.counts["merged"] += len(members) - 1
            plan.counts["events_with_duplicates"] += 1
        plan.fill[kept] = event_key
    plan.counts["events"] = len(plan.fill)
    return plan


# The amounts IBKR restates on a re-sent FX row; the rest of an FX row is its identity.
FX_AMOUNTS = ("proceeds_text", "realized_pnl_text")
# Identity fields a re-send might still change. Rows alike in everything else, never in one
# statement together, are counted as re-sends the rule would not recognise (never merged).
FX_LOOSE_LEFT_OUT = ("report_date", "cost_text")


def _amount(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def fx_counts(values: Mapping[tuple[str, str], Mapping[str, Any]], plan: FxPlan, index: FxIndex) -> Counter:
    """Counts for the read-only FX check: no account, key or amount, only how many.

    values: each live row's columns by (account, source_key); plan: plan_fx_merge for those rows.
    """
    counts: Counter = Counter(rows=len(values))
    # Every count is listed, 0 included, so a report reads the same before and after the FX step.
    for name in ("events_whose_amounts_change", "removed_rows_with_the_same_amounts", "amounts_not_decimal",
                 "possible_resends_outside_the_rule", "possible_resends_outside_the_rule_unplaced",
                 *(f"events_where_{name}_changes" for name in FX_AMOUNTS),
                 *(f"possible_resends_outside_the_rule_differ_in_{name}" for name in FX_LOOSE_LEFT_OUT)):
        counts[name] = 0
    counts["event_key_filled"] = sum(1 for row in values.values() if row.get("event_key"))
    alike = Counter((key[0], fx_event_identity(row)) for key, row in values.items())
    counts["look_alike_groups"] = sum(1 for n in alike.values() if n > 1)
    counts["rows_in_look_alike_groups"] = sum(n for n in alike.values() if n > 1)
    duplicated = {event: members for event, members in plan.groups.items()
                  if len(members) > 1 and plan.outcome[event] != "seen_together"}
    counts["events_with_more_than_one_current_row"] = len(duplicated)
    counts["rows_beyond_one_per_event"] = sum(len(members) - 1 for members in duplicated.values())
    counts["events_with_more_than_one_current_row_after_merge"] = sum(
        1 for event in duplicated if plan.outcome[event] != "merged")
    counts["rows_the_merge_removes"] = len(plan.merge)
    counts["accounts_the_merge_changes"] = len({key[0] for key in plan.merge})
    for name in ("groups_seen_together", "not_in_any_statement", "event_key_differs", "event_key_held_by_another_row",
                 "same_record_under_two_keys", "found_by_raw_record"):
        counts[name] = plan.counts[name]
    # The amounts the merge takes out of the current table (the history keeps them).
    removed = set(plan.merge)
    for event, members in duplicated.items():
        if plan.outcome[event] != "merged":
            continue
        kept, gone = values[members[-1]], [values[member] for member in members[:-1]]
        changed = [name for name in FX_AMOUNTS if any(row[name] != kept[name] for row in gone)]
        for name in changed:
            counts[f"events_where_{name}_changes"] += 1
        counts["events_whose_amounts_change"] += bool(changed)
        counts["removed_rows_with_the_same_amounts"] += sum(
            1 for row in gone if all(row[name] == kept[name] for name in FX_AMOUNTS))
    totals: dict[tuple, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for key, row in values.items():
        for name in FX_AMOUNTS:
            amount = _amount(row[name]) if row[name] not in (None, "") else Decimal(0)
            if amount is None:
                counts["amounts_not_decimal"] += 1
                continue
            total = totals[(key[0], row.get("functional_currency"), name)]
            total[0] += amount
            if key not in removed:
                total[1] += amount
    counts["account_currency_totals_that_change"] = len(
        {group[:2] for group, (before, after) in totals.items() if before != after})
    # Re-sends the rule would miss: alike but for a left-out identity field, never reported together.
    loose: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
    for key, row in values.items():
        if key not in removed and row.get("event_datetime"):
            loose[(key[0], *(row.get(name) for name in FX_EVENT_FIELDS
                             if name not in FX_LOOSE_LEFT_OUT and name != "account_id"))].append(key)
    for members in loose.values():
        if len({fx_event_identity(values[key]) for key in members}) < 2:
            continue
        located = [plan.located.get(key) for key in members]
        if None in located:
            counts["possible_resends_outside_the_rule_unplaced"] += 1
            continue
        seen = [index.statements[at] for at in located]
        if any(seen[i] & seen[j] for i in range(len(seen)) for j in range(i + 1, len(seen))):
            continue
        counts["possible_resends_outside_the_rule"] += 1
        for name in FX_LOOSE_LEFT_OUT:
            if len({values[key].get(name) for key in members}) > 1:
                counts[f"possible_resends_outside_the_rule_differ_in_{name}"] += 1
    return counts


def fx_check(connection, index: FxIndex, tenants: Iterable[str]) -> dict:
    """Read-only: FX events with more than one current row, now and after the merge, and the FX amounts
    the merge changes. Counts only. Works before and after the FX step's migration."""
    from psycopg import sql

    has_key = "event_key" in table_columns(connection, "fx_transactions")
    has_history = connection.execute("SELECT to_regclass('silver.fx_transactions_history')").fetchone()[0]
    names = [name for name in FX_COLUMNS if name != "last_seen_run_id"]
    query = sql.SQL("SELECT {}, {} FROM silver.fx_transactions WHERE account_id = ANY(%s)").format(
        sql.SQL(", ").join(sql.Identifier(name) for name in names),
        sql.Identifier("event_key") if has_key else sql.SQL("NULL::text"))
    report: dict[str, Any] = {"event_key_column": has_key, "statements_read": index.count,
                              "fx_records_read": index.records,
                              "fx_records_without_report_date": index.without_report_date, "tenants": {}}
    duplicates = 0
    for label, tenant in tenant_labels(tenants).items():
        accounts = use_tenant(connection, tenant)
        values = {}
        for row in connection.execute(query, (sorted(accounts),)).fetchall():
            values[(row[0], row[1])] = {**dict(zip(names, row)), "event_key": row[-1]}
        plan = plan_fx_merge([(*key, row["event_key"], row["row_sha256"]) for key, row in values.items()], index)
        counts = fx_counts(values, plan, index)
        if has_history:
            counts["history_rows_closed_as_merged"] = int(connection.execute(
                "SELECT count(*) FROM silver.fx_transactions_history WHERE account_id = ANY(%s) "
                "AND closed_by = 'merged'", (sorted(accounts),)).fetchone()[0])
        duplicates += counts["events_with_more_than_one_current_row"]
        report["tenants"][label] = {"accounts": len(accounts), "tables": {"fx_transactions": dict(counts)}}
    report["ok"] = duplicates == 0
    return report


def _require_fx_step(connection) -> None:
    if "event_key" not in table_columns(connection, "fx_transactions"):
        raise HistoryError("silver.fx_transactions has no event_key: apply the FX step's migration first")


def apply_fx_merge(connection, index: FxIndex, tenants: Iterable[str], *, dry_run: bool = False,
                   lock_seconds: float = LOCK_SECONDS) -> dict:
    """Fill event_key and remove the older reports of each re-sent FX event, recorded as 'merged'."""
    _require_fx_step(connection)
    if not dry_run:
        if not recording(connection):
            raise HistoryError("recording is off: the merge must be recorded in the history")
        lock_tables(connection, ["fx_transactions"], seconds=lock_seconds)
    report: dict[str, Any] = {"tenants": {}}
    for label, tenant in tenant_labels(tenants).items():
        accounts = use_tenant(connection, tenant)
        rows = connection.execute("SELECT account_id, source_key, event_key, row_sha256 FROM silver.fx_transactions "
                                  "WHERE account_id = ANY(%s)", (sorted(accounts),)).fetchall()
        plan = plan_fx_merge([tuple(row) for row in rows], index)
        report["tenants"][label] = {"accounts": len(accounts), "tables": {"fx_transactions": dict(plan.counts)}}
        if dry_run:
            continue
        connection.execute("SELECT set_config('silver_history.reason', 'merged', true), "
                           "set_config('silver_history.note', %s, true)", (NOTE_FX_MERGED,))
        with connection.cursor() as cursor:
            cursor.executemany("DELETE FROM silver.fx_transactions WHERE account_id = %s AND source_key = %s",
                               plan.merge)
        connection.execute("SELECT set_config('silver_history.reason', '', true), "
                           "set_config('silver_history.note', %s, true)", (NOTE_FX_KEY,))
        with connection.cursor() as cursor:
            cursor.executemany("UPDATE silver.fx_transactions SET event_key = %s WHERE account_id = %s "
                               "AND source_key = %s AND event_key IS DISTINCT FROM %s",
                               [(key, account, source_key, key) for (account, source_key), key in plan.fill.items()])
        connection.execute("SELECT set_config('silver_history.note', '', true)")
    if not dry_run:
        result = check(connection, tenants, ["fx_transactions"])
        report["check"] = result
        if not result["ok"]:
            raise HistoryError("the check after the merge failed, so nothing was changed")
    return report


def apply_fx_unmerge(connection, tenants: Iterable[str], *, dry_run: bool = False,
                     lock_seconds: float = LOCK_SECONDS) -> dict:
    """Undo of the merge: put back each merged row from its last version, before the column is dropped."""
    from psycopg import sql

    _require_fx_step(connection)
    if not dry_run:
        if not recording(connection):
            raise HistoryError("recording is off: putting rows back must be recorded in the history")
        lock_tables(connection, ["fx_transactions"], seconds=lock_seconds)
    columns = [name for name in table_columns(connection, "fx_transactions") if name != "event_key"]
    listed = sql.SQL(", ").join(sql.Identifier(name) for name in columns)
    report: dict[str, Any] = {"tenants": {}}
    for label, tenant in tenant_labels(tenants).items():
        accounts = use_tenant(connection, tenant)
        latest = sql.SQL(
            "SELECT DISTINCT ON (h.account_id, h.source_key) h.* FROM silver.fx_transactions_history h "
            "WHERE h.account_id = ANY(%s) ORDER BY h.account_id, h.source_key, h.recorded_from DESC, "
            "h.version_id DESC")
        missing = sql.SQL(
            "SELECT {listed} FROM ({latest}) v WHERE v.closed_by = 'merged' AND NOT EXISTS (SELECT 1 FROM "
            "silver.fx_transactions t WHERE t.account_id = v.account_id AND t.source_key = v.source_key)"
        ).format(listed=listed, latest=latest)
        count = connection.execute(sql.SQL("SELECT count(*) FROM ({}) m").format(missing),
                                   (sorted(accounts),)).fetchone()[0]
        report["tenants"][label] = {"accounts": len(accounts),
                                    "tables": {"fx_transactions": {"rows_to_put_back": int(count)}}}
        if dry_run or not count:
            continue
        connection.execute("SELECT set_config('silver_history.note', %s, true)", (NOTE_FX_UNDONE,))
        connection.execute(sql.SQL("INSERT INTO silver.fx_transactions ({}) {}").format(listed, missing),
                           (sorted(accounts),))
        connection.execute("SELECT set_config('silver_history.note', '', true)")
    if not dry_run:
        result = check(connection, tenants, ["fx_transactions"])
        report["check"] = result
        if not result["ok"]:
            raise HistoryError("the check after putting rows back failed, so nothing was changed")
    return report


def fx_index(contents: Iterable[bytes]) -> FxIndex:
    index = FxIndex()
    for content in contents:
        index.add(content)
    return index

