"""What numbers change: a before-and-after report on the silver and gold tables (upgrade U6).

Before an image that touches silver or gold goes live, its jobs run on a restored copy of
the database. The public summary goes on the PR; the full report stays in the project's
private folder for the approver. docs/MEDALLION_RELEASE_REPORT.md has the steps. On a single copy, take a snapshot before and after the candidate's jobs run:

    python scripts/release_data_report.py snapshot --out before.jsonl.gz
    (run the candidate image's jobs against the copy)
    python scripts/release_data_report.py snapshot --out after.jsonl.gz
    python scripts/release_data_report.py compare --before before.jsonl.gz --after after.jsonl.gz

compare also reads two databases directly, for example two copies restored from the same
moment with one image's jobs run on each:

    python scripts/release_data_report.py compare --before env:BEFORE_DATABASE_URL --after env:AFTER_DATABASE_URL

Every silver and gold table in infra/medallion/catalog.json is compared by its primary
key: row counts; rows added, removed and changed; and for each numeric column (the IBKR
*_text columns and the numbers inside jsonb payloads included) the largest absolute and
relative change, per table and per account or subject. Columns that every rebuild
rewrites (generated_at, fetched_at and the like) are not compared. Frozen month-end
snapshots get the sha256 checks scripts/medallion_layers.py makes around each S3 batch.

The full report (Markdown and JSON) holds aggregates only: no account numbers (accounts
appear as u…#n and other subjects as s…#n, labels that hold within one report), no names
and no text values. Its deltas can still reveal client figures, and the repository is
public, so it never goes on a PR. The public summary (--public-summary, or the summary
command over the JSON) holds counts and table and column names only. Snapshot files hold figures per row, with keys,
subjects and text replaced by sha256 digests. They are still client data: keep them on
the admin VM and delete them with the copy.

Database URLs come from environment variables (snapshot reads DATABASE_ADMIN_URL unless
--url-env names another) and are never printed. Connections are read-only, and nothing
is written except the named output files. Row-level security applies as for
medallion_layers.py: a login that does not bypass it reads the --tenant rows.

The figures command compares each page's figures today with the certified figures on a
restored copy (certified figures, U5; scripts/release_data_figures.py):

    python scripts/release_data_report.py figures --batch 1 --private-dir <private folder>

gold.v_certified_figures, the view every certified reader uses, is compared like a table by
the key readers look figures up with, since which build's figure a reader sees can change.

Exit codes: 0 nothing changed, 1 figures changed (read the report), 2 could not run.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import medallion_layers  # noqa: E402  the catalog, relation lookups, sha256 and frozen-snapshot rules
from ibkr_etl.switch_dry_run import ACCOUNT_ID_IN_TEXT  # noqa: E402

FORMAT = "release-data-snapshot"
FORMAT_VERSION = 1
LAYERS = ("silver", "gold")
FROZEN_TABLE = "gold.reporting_snapshots"
# Rewritten by every rebuild without any figure changing.
IGNORED_COLUMNS = frozenset({"generated_at", "fetched_at", "checked_at", "first_seen_at", "last_seen_at",
                             "last_seen_run_id", "duration_ms", "timings_json"})
# Short code values (statuses and versions), shown as they are.
CATEGORY_COLUMNS = frozenset({"status", "quality_status", "calculation_version", "analytics_version",
                              "comparison_version", "formula_version", "observation_status",
                              "license_status"})
GROUP_COLUMNS = ("subject_id", "portfolio_id", "account_id")
DATE_COLUMNS = ("observation_date", "snapshot_date", "as_of_date", "holdings_as_of", "period_end", "rate_date",
                "price_date", "trade_date", "effective_date", "event_date", "report_date")
NUMERIC_TYPES = ("smallint", "integer", "bigint", "numeric", "real", "double precision")
ACCOUNT_ID = re.compile(r"[A-Z]{1,2}\d{5,}")
SAFE_SEGMENT = re.compile(r"[a-z][A-Za-z0-9_]{0,47}")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
COMPACT_DAY = re.compile(r"(\d{4})(\d{2})(\d{2})")
INDEX = re.compile(r"\[\d+\]")
SEPARATOR = "\x1f"  # between segments of a path inside a json value
# Views compared like tables, by the key readers look figures up with: they choose which
# build's certified figure each reader sees.
COMPARED_VIEWS = {"gold.v_certified_figures": ["tenant_id", "subject_id", "figure_id", "period", "item",
                                               "benchmark_id", "as_of_date"]}
FETCH_ROWS = 2000
TOP = 10
COMMENT_LIMIT = 60000  # GitHub refuses a PR comment over 65,536 characters


class ReportError(RuntimeError):
    pass


def mask(text: str) -> str:
    return ACCOUNT_ID_IN_TEXT.sub("u…", text)


def _digest(value, size: int = 16) -> str:
    return medallion_layers._sha(value)[:size]


# ---- reading one side: each row becomes [key digest, subject digest, date, values] ----

def column_kind(name: str, type_name: str, key: list[str]) -> str:
    if name in key:
        return "key"
    if name in IGNORED_COLUMNS:
        return "ignored"
    if type_name in ("json", "jsonb"):
        return "json"
    if type_name == "boolean" or name in CATEGORY_COLUMNS:
        return "category"
    if type_name.startswith(NUMERIC_TYPES) or (type_name == "text" and name.endswith("_text")):
        return "number"
    return "text"


def _hashed(value):
    return None if value is None else "#" + _digest(str(value))


def _number(value):
    if value is None:
        return None
    if isinstance(value, (bool, int, float, Decimal)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(Decimal(value.strip().replace(",", "")))
        except (InvalidOperation, ValueError):
            return _hashed(value)
    else:
        return _hashed(value)
    return number if math.isfinite(number) else str(number)


def _category(value):
    if value is None:
        return None
    text = "true" if value is True else "false" if value is False else str(value)
    return mask(text)[:60].replace("|", "/").replace("`", "'")


def _segment(key) -> str:
    """A json key in a stored path: a plain field name as it is; anything else (a ticker,
    an account, a date) as * and a digest, so the snapshot keeps it apart without naming it."""
    key = str(key)
    return key if SAFE_SEGMENT.fullmatch(key) else "*" + _digest(key, 12)


def _flatten(value, path: tuple, numbers: dict):
    """The value with numbers taken out into numbers[path]; ignored keys dropped."""
    if isinstance(value, dict):
        return {str(key): _flatten(item, path + (_segment(key),), numbers)
                for key, item in value.items() if key not in IGNORED_COLUMNS}
    if isinstance(value, list):
        return [_flatten(item, path + (f"[{index}]",), numbers) for index, item in enumerate(value)]
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        numbers[SEPARATOR.join(path)] = float(value)
        return 0
    return value


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {"n": {}, "h": _digest(value)}
    numbers: dict = {}
    skeleton = _flatten(value, (), numbers)
    return {"n": numbers, "h": _digest(skeleton)}


def _day(value):
    if value is None:
        return None
    text = str(value)[:10]
    if DAY.fullmatch(text):
        return text
    compact = COMPACT_DAY.fullmatch(text[:8])
    return "-".join(compact.groups()) if compact else None


ENCODERS = {"number": _number, "category": _category, "text": _hashed, "json": _json_value}


def encode_table(table: str, location: str, columns: list, key: list[str], rows) -> dict:
    """One table's rows, encoded for the snapshot. columns: [[name, type], ...]."""
    names = [column for column, _ in columns]
    types = dict((column, type_name) for column, type_name in columns)
    key = [column for column in key if column in types]
    whole_row = not key
    if whole_row:
        key = [column for column in names if column not in IGNORED_COLUMNS]
    kinds = {column: column_kind(column, types[column], key) for column in names}
    group = next((column for column in GROUP_COLUMNS if column in types), None)
    date = next((column for column in DATE_COLUMNS if column in key), None) or \
        next((column for column in DATE_COLUMNS if column in types), None)
    values = [column for column in names if kinds[column] not in ("key", "ignored")]
    categories = [column for column in names if column in CATEGORY_COLUMNS or types[column] == "boolean"]
    position = {column: index for index, column in enumerate(names)}
    counts = {column: Counter() for column in categories}
    group_kinds: dict[str, str] = {}
    encoded = []
    for row in rows:
        subject = None
        if group and row[position[group]] is not None:
            raw = str(row[position[group]])
            subject = _digest(raw)
            group_kinds[subject] = "u" if ACCOUNT_ID.fullmatch(raw) else "s"
        encoded.append([
            _digest([row[position[column]] for column in key], 24),
            subject,
            _day(row[position[date]]) if date else None,
            [ENCODERS[kinds[column]](row[position[column]]) for column in values],
        ])
        for column in categories:
            counts[column][_category(row[position[column]]) or "(none)"] += 1
    return {
        "table": table, "present": True, "location": location,
        "columns": [[column, types[column], kinds[column]] for column in names],
        "key": key, "whole_row": whole_row, "group": group, "date": date, "values": values,
        "categories": {column: dict(counts[column]) for column in categories},
        "group_kinds": group_kinds, "row_count": len(encoded), "rows": encoded,
    }


def catalog_tables(only: list[str] | None = None) -> list[dict]:
    """Silver and gold tables in the catalog, sorted by layer.name."""
    found = []
    for item in medallion_layers.catalog_objects(created_in_layer=True):
        if item["layer"] not in LAYERS or item["kind"] != "table":
            continue
        qualified = f"{item['layer']}.{item['name']}"
        if only and qualified not in only and item["name"] not in only:
            continue
        found.append({**item, "table": qualified})
    return sorted(found, key=lambda item: item["table"])


def catalog_views() -> list[str]:
    """The views that are not compared (they read the tables)."""
    return sorted(f"{item['layer']}.{item['name']}"
                  for item in medallion_layers.catalog_objects(created_in_layer=True)
                  if item["layer"] in LAYERS and item["kind"] == "view"
                  and f"{item['layer']}.{item['name']}" not in COMPARED_VIEWS)


def compared_views(only: list[str] | None = None) -> list[dict]:
    found = []
    for name in sorted(COMPARED_VIEWS):
        layer, relation = name.split(".", 1)
        if not only or name in only or relation in only:
            found.append({"layer": layer, "name": relation, "kind": "view", "table": name})
    return found


def _key_columns(connection, oid) -> list[str]:
    """The primary key, or else the first unique index without a predicate or expression."""
    row = connection.execute(
        "SELECT array_agg(a.attname ORDER BY k.position) FROM pg_index i "
        "CROSS JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY AS k(attnum, position) "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
        "WHERE i.indrelid = %s AND i.indisunique AND i.indpred IS NULL AND i.indexprs IS NULL "
        "AND k.position <= i.indnkeyatts "
        "GROUP BY i.indexrelid, i.indisprimary ORDER BY i.indisprimary DESC, i.indexrelid::regclass::text "
        "LIMIT 1", (oid,)).fetchone()
    return list(row[0]) if row else []


def _frozen(connection, schema: str) -> list[list]:
    """As medallion_layers.snapshot reads reporting_snapshots, with the subject digested."""
    from psycopg import sql

    rows = connection.execute(sql.SQL(
        "SELECT snapshot_id::text, status, data_sha256, "
        "encode(sha256(convert_to(snapshot_payload_json, 'UTF8')), 'hex'), "
        "subject_id, period_end::text, created_at::text FROM {} ORDER BY snapshot_id"
    ).format(sql.Identifier(schema, "reporting_snapshots"))).fetchall()
    return [[row[0], row[1], row[2], row[3], _digest(row[4]), row[5], row[6]] for row in rows]


def read_table(connection, item: dict) -> dict:
    from psycopg import sql

    view = item.get("kind") == "view"
    for schema in (item["layer"],) if view else (item["layer"], "public"):  # public: a copy from before the layer moves
        relation = medallion_layers._relation(connection, schema, item["name"])
        if relation is not None and relation[1] in (("v",) if view else ("r", "p")):
            break
    else:
        return {"table": item["table"], "present": False}
    columns = medallion_layers._columns(connection, relation[0])
    query = sql.SQL("SELECT {} FROM {}").format(
        sql.SQL(", ").join(sql.Identifier(column) for column, _ in columns), sql.Identifier(schema, item["name"]))
    with connection.cursor(name="release_data_report") as cursor:
        cursor.itersize = FETCH_ROWS
        cursor.execute(query)
        key = COMPARED_VIEWS[item["table"]] if view else _key_columns(connection, relation[0])
        entry = encode_table(item["table"], schema, columns, key, cursor)
    if item["table"] == FROZEN_TABLE:
        entry["frozen"] = _frozen(connection, schema)
    return entry


def _header(connection, tenant: str) -> dict:
    taken_at, server_version, all_tenants = connection.execute(
        "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS \"UTC\"'), "
        "current_setting('server_version'), r.rolsuper OR r.rolbypassrls "
        "FROM pg_roles r WHERE r.rolname = current_user").fetchone()
    migrations = []
    if connection.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]:
        migrations = [row[0] for row in connection.execute(
            "SELECT version FROM public.schema_migrations ORDER BY version").fetchall()]
    return {"format": FORMAT, "version": FORMAT_VERSION, "taken_at": taken_at, "server_version": server_version,
            "all_tenants": bool(all_tenants), "default_tenant": tenant == medallion_layers.DEFAULT_TENANT,
            "migrations": migrations}


def _conninfo(url: str) -> str:
    """Read-only, as medallion_layers._conninfo, under this tool's name."""
    from psycopg.conninfo import make_conninfo

    return make_conninfo(url, options="-c default_transaction_read_only=on -c statement_timeout=300000",
                         application_name="release-data-report")


def database_source(url: str, tenant: str, only: list[str] | None = None):
    """(header, table entries) from one database, in one read-only REPEATABLE READ transaction."""
    import psycopg

    connection = psycopg.connect(_conninfo(url))
    try:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
        header = _header(connection, tenant)
    except Exception:
        connection.close()
        raise

    def tables():
        try:
            for item in sorted(catalog_tables(only) + compared_views(only), key=lambda found: found["table"]):
                yield read_table(connection, item)
        finally:
            connection.close()

    return header, tables()


def _create_private(path: Path) -> None:
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
    os.chmod(path, 0o600)


def _open_text(path: Path, mode: str):
    if path.name.endswith(".gz"):
        return gzip.open(path, mode + "t", encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def write_snapshot(header: dict, tables, out: Path) -> dict:
    _create_private(out)
    counts = {}
    with _open_text(out, "w") as handle:
        handle.write(json.dumps(header, sort_keys=True) + "\n")
        for entry in tables:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
            counts[entry["table"]] = entry.get("row_count") if entry.get("present") else None
    return counts


def file_source(path: Path, only: list[str] | None = None):
    handle = _open_text(path, "r")
    try:
        header = json.loads(handle.readline() or "{}")
    except ValueError:
        header = {}
    if header.get("format") != FORMAT or header.get("version") != FORMAT_VERSION:
        handle.close()
        raise ReportError(f"{path.name} is not a release data snapshot (format {FORMAT} v{FORMAT_VERSION})")

    def tables():
        with handle:
            for line in handle:
                if line.strip():
                    entry = json.loads(line)
                    table = entry["table"]
                    if not only or table in only or table.split(".", 1)[1] in only:
                        yield entry

    return header, tables()


def open_source(spec: str, tenant: str, only: list[str] | None = None):
    """A snapshot file, or env:NAME for a database URL held in that environment variable."""
    if spec.startswith("env:"):
        name = spec[4:]
        url = os.environ.get(name, "")
        if not url:
            raise ReportError(f"{name} is not set")
        header, tables = database_source(url, tenant, only)
        return f"database ({name})", header, tables
    path = Path(spec)
    if not path.is_file():
        raise ReportError(f"{path.name} not found (give a snapshot file or env:NAME)")
    header, tables = file_source(path, only)
    return f"snapshot {path.name}", header, tables


# ---- comparing ----

def _new_stats() -> dict:
    return {"changed": 0, "set": 0, "cleared": 0, "not_numeric": 0, "from_zero": 0,
            "max_abs": None, "max_rel": None}


class TableDiff:
    def __init__(self, before: dict, after: dict, rel_tol: float, abs_tol: float):
        self.before, self.after = before, after
        self.rel_tol, self.abs_tol = rel_tol, abs_tol
        self.rows = Counter()
        self.dates = {"added": [], "removed": [], "changed": []}
        self.columns: dict[str, dict] = {}
        self.transitions: dict[str, Counter] = {}
        self.groups: dict = {}

    def _group(self, subject) -> dict:
        return self.groups.setdefault(subject, {"before": 0, "after": 0, "added": 0, "removed": 0,
                                                 "changed": 0, "max_abs": None})

    def _date(self, kind: str, day) -> None:
        if day is None:
            return
        span = self.dates[kind]
        if not span:
            span.extend([day, day])
        else:
            span[0], span[1] = min(span[0], day), max(span[1], day)

    def _stats(self, column: str, path: str | None = None) -> dict:
        stats = self.columns.setdefault(column, _new_stats())
        if path is None:
            return stats
        return stats.setdefault("paths", {}).setdefault(path, _new_stats())

    def _number(self, stats: dict, column: str, old, new, subject, day) -> bool:
        if isinstance(old, float) and isinstance(new, float):
            if math.isclose(old, new, rel_tol=self.rel_tol, abs_tol=self.abs_tol):
                return False
            delta = new - old
            stats["changed"] += 1
            if stats["max_abs"] is None or abs(delta) > abs(stats["max_abs"]["delta"]):
                stats["max_abs"] = {"delta": delta, "subject": subject, "date": day}
            if old == 0:
                stats["from_zero"] += 1
            else:
                ratio = delta / abs(old)
                if stats["max_rel"] is None or abs(ratio) > abs(stats["max_rel"]["ratio"]):
                    stats["max_rel"] = {"ratio": ratio, "subject": subject, "date": day}
            largest = self._group(subject)["max_abs"]
            if largest is None or abs(delta) > abs(largest["delta"]):
                self._group(subject)["max_abs"] = {"column": column, "delta": delta, "date": day}
            return True
        if old == new:
            return False
        stats["changed"] += 1
        if old is None:
            stats["set"] += 1
        elif new is None:
            stats["cleared"] += 1
        else:
            stats["not_numeric"] += 1
        return True

    def _json(self, column: str, old, new, subject, day) -> bool:
        stats = self._stats(column)
        if old is None or new is None:
            return self._number(stats, column, old, new, subject, day)
        changed = False
        if old["h"] != new["h"]:
            stats["structure"] = stats.get("structure", 0) + 1
            changed = True
        before, after = old["n"], new["n"]
        for path in before.keys() | after.keys():
            if before.get(path) == after.get(path):
                continue
            label = f"{column}:{path_label(path)}"
            if self._number(self._stats(column, label), label, before.get(path), after.get(path), subject, day):
                changed = True
        if changed:
            stats["changed"] += 1
        return changed

    def run(self) -> None:
        before, after = self.before, self.after
        kinds_before = {column: kind for column, _, kind in before["columns"]}
        kinds_after = {column: kind for column, _, kind in after["columns"]}
        at_before = {column: index for index, column in enumerate(before["values"])}
        at_after = {column: index for index, column in enumerate(after["values"])}
        shared = [column for column in after["values"]
                  if column in at_before and kinds_before[column] == kinds_after[column]]
        old_rows = {}
        for row in before["rows"]:
            old_rows[row[0]] = row
            self._group(row[1])["before"] += 1
        for row in after["rows"]:
            subject, day = row[1], row[2]
            self._group(subject)["after"] += 1
            old = old_rows.pop(row[0], None)
            if old is None:
                self.rows["added"] += 1
                self._group(subject)["added"] += 1
                self._date("added", day)
                continue
            changed = False
            for column in shared:
                previous, current = old[3][at_before[column]], row[3][at_after[column]]
                if previous == current:
                    continue
                kind = kinds_after[column]
                if kind == "json":
                    changed |= self._json(column, previous, current, subject, day)
                elif kind == "number":
                    changed |= self._number(self._stats(column), column, previous, current, subject, day)
                else:
                    stats = self._stats(column)
                    stats["changed"] += 1
                    stats["set" if previous is None else "cleared" if current is None else "not_numeric"] += 1
                    if kind == "category":
                        moves = self.transitions.setdefault(column, Counter())
                        moves[f"{previous or '(none)'} → {current or '(none)'}"] += 1
                    changed = True
            if changed:
                self.rows["changed"] += 1
                self._group(subject)["changed"] += 1
                self._date("changed", day)
        for row in old_rows.values():
            self.rows["removed"] += 1
            self._group(row[1])["removed"] += 1
            self._date("removed", row[2])


def path_label(path: str) -> str:
    """A json path for the report: list positions as [], other keys as _segment stored them."""
    parts = []
    for segment in path.split(SEPARATOR) if path else []:
        if INDEX.fullmatch(segment):
            parts.append("[]")
        else:
            parts.append("." + ("*" if segment.startswith("*") else segment))
    return "".join(parts).lstrip(".") or "(value)"


def _schema_changes(before: dict, after: dict) -> dict:
    types_before = {column: (type_name, kind) for column, type_name, kind in before["columns"]}
    types_after = {column: (type_name, kind) for column, type_name, kind in after["columns"]}
    changes = {
        "columns_added": sorted(set(types_after) - set(types_before)),
        "columns_removed": sorted(set(types_before) - set(types_after)),
        "type_changed": sorted(column for column in set(types_before) & set(types_after)
                               if types_before[column] != types_after[column]),
        "key_changed": before["key"] != after["key"],
    }
    return {name: value for name, value in changes.items() if value}


def compare_table(before: dict | None, after: dict | None, rel_tol: float, abs_tol: float) -> dict:
    name = (before or after)["table"]
    had = bool(before and before.get("present"))
    has = bool(after and after.get("present"))
    result = {"table": name, "rows": {"before": before.get("row_count") if had else None,
                                      "after": after.get("row_count") if has else None}}
    if not had and not has:
        result["status"] = "absent"
        return result
    if not had or not has:
        result["status"] = "new" if has else "gone"
        result["location"] = {"before": before.get("location") if had else None,
                              "after": after.get("location") if has else None}
        return result
    diff = TableDiff(before, after, rel_tol, abs_tol)
    diff.run()
    schema = _schema_changes(before, after)
    categories = {}
    for column in sorted(set(before["categories"]) | set(after["categories"])):
        old, new = before["categories"].get(column, {}), after["categories"].get(column, {})
        if old != new:
            categories[column] = {value: [old.get(value, 0), new.get(value, 0)]
                                  for value in sorted(set(old) | set(new))}
    result.update({
        "location": {"before": before["location"], "after": after["location"]},
        "key": after["key"], "whole_row": after["whole_row"], "group": after["group"], "date": after["date"],
        "not_compared": sorted(column for column, _, kind in after["columns"] if kind == "ignored"),
    })
    result["rows"].update({name: diff.rows[name] for name in ("added", "removed", "changed")})
    changed = any(result["rows"][name] for name in ("added", "removed", "changed")) or bool(schema)
    result["status"] = "changed" if changed else "unchanged"
    if not changed:
        return result
    result.update({
        "dates": {kind: span for kind, span in diff.dates.items() if span},
        "columns": {column: stats for column, stats in diff.columns.items() if stats["changed"]},
        "transitions": {column: dict(moves.most_common(TOP)) for column, moves in diff.transitions.items()},
        "categories": categories,
        "groups": {subject: stats for subject, stats in diff.groups.items()
                   if subject is not None and (stats["added"] or stats["removed"] or stats["changed"])},
        "schema": schema,
        "group_kinds": {**before["group_kinds"], **after["group_kinds"]},
    })
    return result


def frozen_check(before: dict | None, after: dict | None) -> dict | None:
    """The month-end snapshot rules of medallion_layers.py: existing rows keep their sha256 and
    payload; new rows are allowed, and so is a frozen row superseded by a new one for its month."""
    if not (before and after and "frozen" in before and "frozen" in after):
        return None
    problems, notes = [], []
    # Every new row counts as made by the candidate's run: two copies read side by side
    # have no "before" moment that the rows' created_at could be checked against.
    medallion_layers._reporting_snapshots(
        {"reporting_snapshots": before["frozen"], "taken_at": "0001-01-01T00:00:00+00:00"},
        {"reporting_snapshots": after["frozen"]}, problems, notes)
    notes = [note.replace("the batch window", "the candidate's run") for note in notes]

    def mismatched(rows):
        return {row[0] for row in rows if row[1] in ("frozen", "approved") and row[2] != row[3]}

    new_mismatch = mismatched(after["frozen"]) - mismatched(before["frozen"])
    if new_mismatch:
        problems.append(f"frozen or approved snapshots that no longer match their stored sha256: {len(new_mismatch)}")
    return {"checked": len(before["frozen"]),
            "frozen": sum(1 for row in before["frozen"] if row[1] in ("frozen", "approved")),
            "problems": problems, "notes": notes}


def _pairs(before_tables, after_tables):
    """Both sides' entries matched by table name; each side is sorted by name."""
    old, new = next(before_tables, None), next(after_tables, None)
    while old is not None or new is not None:
        if new is None or (old is not None and old["table"] < new["table"]):
            yield old, None
            old = next(before_tables, None)
        elif old is None or new["table"] < old["table"]:
            yield None, new
            new = next(after_tables, None)
        else:
            yield old, new
            old, new = next(before_tables, None), next(after_tables, None)


def _labels(results: list[dict]) -> dict:
    kinds = {}
    for result in results:
        kinds.update(result.get("group_kinds") or {})
    labels = {}
    for prefix in ("u", "s"):
        for number, subject in enumerate(sorted(key for key, kind in kinds.items() if kind == prefix), 1):
            labels[subject] = f"{prefix}…#{number}"
    return labels


def _relabel(value, labels: dict):
    """Subject digests in a result replaced by their labels."""
    if isinstance(value, dict):
        return {key: (labels.get(item, "?") if key == "subject" and isinstance(item, str) else _relabel(item, labels))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_relabel(item, labels) for item in value]
    return value


def _publish(result: dict, labels: dict) -> dict:
    result = dict(result)
    result.pop("group_kinds", None)
    if "groups" in result:
        result["groups"] = {labels.get(subject, "?"): stats for subject, stats in result["groups"].items()}
    return _relabel(result, labels)


def _side(description: str, header: dict) -> dict:
    migrations = header.get("migrations") or []
    return {"source": description, "taken_at": header.get("taken_at"), "server_version": header.get("server_version"),
            "migrations": len(migrations), "last_migration": migrations[-1] if migrations else None,
            "rows_read": "all tenants" if header.get("all_tenants") else
            ("the default tenant" if header.get("default_tenant") else "one other tenant")}


def _affected(result: dict) -> int:
    rows = result["rows"]
    return sum(rows.get(name) or 0 for name in ("added", "removed", "changed"))


def _headline(result: dict) -> dict | None:
    """The column (or json path) with the most changed values."""
    candidates = []
    for column, stats in (result.get("columns") or {}).items():
        candidates.append((column, stats))
        candidates.extend((path, item) for path, item in (stats.get("paths") or {}).items())
    numeric = [item for item in candidates if item[1]["max_abs"] is not None]
    pool = numeric or candidates
    if not pool:
        return None
    column, stats = max(pool, key=lambda item: (item[1]["changed"], abs((item[1]["max_abs"] or {}).get("delta", 0))))
    return {"column": column, **{name: stats[name] for name in ("changed", "max_abs", "max_rel")}}


def build_report(before_source: tuple, after_source: tuple, *, rel_tol: float = 1e-9, abs_tol: float = 1e-12) -> dict:
    """Compare two sources, each (description, header, table entries)."""
    before_description, before_header, before_tables = before_source
    after_description, after_header, after_tables = after_source
    results, frozen = [], None
    for old, new in _pairs(iter(before_tables), iter(after_tables)):
        results.append(compare_table(old, new, rel_tol, abs_tol))
        if (old or new)["table"] == FROZEN_TABLE:
            frozen = frozen_check(old, new)
    labels = _labels(results)
    tables = {result["table"]: _publish(result, labels) for result in results}
    by_status = {status: sorted(name for name, result in tables.items() if result["status"] == status)
                 for status in ("changed", "unchanged", "new", "gone", "absent")}
    changed = [tables[name] for name in by_status["changed"] + by_status["new"] + by_status["gone"]]
    changed.sort(key=lambda result: (-_affected(result), result["table"]))
    before_migrations = set(before_header.get("migrations") or [])
    after_migrations = set(after_header.get("migrations") or [])
    attention = list((frozen or {}).get("problems") or [])
    if (before_header.get("all_tenants"), before_header.get("default_tenant")) != \
            (after_header.get("all_tenants"), after_header.get("default_tenant")):
        attention.append("before and after were read with different row-level security: counts are not comparable")
    attention += [f"{name} is no longer in the database" for name in by_status["gone"]]
    report = {
        "report": "release-data-report", "version": 1,
        "before": _side(before_description, before_header), "after": _side(after_description, after_header),
        "migrations": {"added": sorted(after_migrations - before_migrations),
                       "removed": sorted(before_migrations - after_migrations)},
        "summary": {"tables": len(results) - len(by_status["absent"]), **by_status,
                    "frozen_snapshots": frozen, "attention": attention},
        "biggest_changes": [{"table": result["table"], "status": result["status"], "rows": result["rows"],
                             "dates": result.get("dates") or {}, "largest": _headline(result)}
                            for result in changed],
        "tables": tables,
        "views_not_compared": catalog_views(),
        "tolerance": {"relative": rel_tol, "absolute": abs_tol},
    }
    return json.loads(mask(json.dumps(report, sort_keys=True, ensure_ascii=False)))


def has_changes(report: dict) -> bool:
    summary = report["summary"]
    return bool(summary["changed"] or summary["new"] or summary["gone"] or summary["attention"])


# ---- Markdown ----

def _count(value) -> str:
    return "—" if value is None else f"{value:,}"


def _signed(value: float) -> str:
    magnitude = abs(value)
    if magnitude == 0:
        return "0"
    return f"{value:+,.2f}" if magnitude >= 1000 else f"{value:+.4g}"


def _percent(ratio: float) -> str:
    percent = ratio * 100
    return f"{percent:+,.0f}%" if abs(percent) >= 1000 else f"{percent:+.3g}%"


def _plural(count: int, noun: str) -> str:
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def _where(info: dict | None) -> str:
    if not info:
        return "—"
    return ", ".join(part for part in (info.get("subject"), info.get("date")) if part) or "—"


def _span(dates: dict) -> str:
    days = [day for span in dates.values() for day in span]
    if not days:
        return "—"
    first, last = min(days), max(days)
    return first if first == last else f"{first} to {last}"


def _largest(item: dict | None, result: dict) -> str:
    rows = result["rows"]
    if result["status"] == "new":
        return "new table"
    if result["status"] == "gone":
        return "table no longer present"
    if item is None:
        if rows.get("added") and rows.get("removed"):
            return "rows added and removed"
        return "rows added only" if rows.get("added") else "rows removed only" if rows.get("removed") else \
            "table definition changed"
    text = f"`{item['column']}` {_plural(item['changed'], 'value')}"
    if item["max_abs"]:
        text += f"; Δ up to {_signed(item['max_abs']['delta'])} ({_where(item['max_abs'])})"
    if item["max_rel"]:
        text += f"; relative up to {_percent(item['max_rel']['ratio'])} ({_where(item['max_rel'])})"
    return text


def _column_rows(result: dict) -> list[str]:
    lines = ["| Column | Values changed | Set / cleared | Largest Δ | Where | Largest relative | Where |",
             "|---|---|---|---|---|---|---|"]
    entries = []
    for column, stats in result.get("columns", {}).items():
        entries.append((column, stats))
        paths = sorted((stats.get("paths") or {}).items(), key=lambda item: -item[1]["changed"])
        entries.extend(paths[:TOP])
        if len(paths) > TOP:
            entries.append((f"{column}: {len(paths) - TOP} more paths", None))
    for column, stats in entries:
        if stats is None:
            lines.append(f"| {column} | | | | | | |")
            continue
        largest, relative = stats.get("max_abs"), stats.get("max_rel")
        changed = f"{stats['changed']:,}"
        if stats.get("structure"):
            changed += f" ({stats['structure']:,} with other content changed)"
        if stats.get("from_zero"):
            changed += f" ({stats['from_zero']:,} from zero)"
        lines.append(" | ".join([
            f"| `{column}`", changed, f"{stats['set']:,} / {stats['cleared']:,}",
            _signed(largest["delta"]) if largest else "—", _where(largest),
            _percent(relative["ratio"]) if relative else "—", _where(relative) + " |"]))
    return lines


def _table_details(result: dict) -> list[str]:
    lines = [f"#### {result['table']}", ""]
    rows = result["rows"]
    if result["status"] in ("new", "gone"):
        return lines + [f"{'New table' if result['status'] == 'new' else 'No longer present'}: "
                        f"{_count(rows['before'])} → {_count(rows['after'])} rows.", ""]
    key = ", ".join(result["key"])
    text = f"Rows {_count(rows['before'])} → {_count(rows['after'])}: {rows['added']:,} added, " \
           f"{rows['removed']:,} removed, {rows['changed']:,} changed. "
    text += f"Compared {'as whole rows' if result['whole_row'] else f'by ({key})'}"
    text += f", per `{result['group']}`." if result.get("group") else "."
    if result.get("not_compared"):
        text += " Not compared: " + ", ".join(f"`{column}`" for column in result["not_compared"]) + "."
    if result["location"]["before"] != result["location"]["after"]:
        text += f" Read from {result['location']['before']} before and {result['location']['after']} after."
    lines += [text, ""]
    for kind, span in (result.get("dates") or {}).items():
        lines.append(f"- Dates of {kind} rows: {span[0] if span[0] == span[1] else f'{span[0]} to {span[1]}'}")
    for name, value in (result.get("schema") or {}).items():
        lines.append(f"- {name.replace('_', ' ').capitalize()}: "
                     + (", ".join(f"`{column}`" for column in value) if isinstance(value, list) else "yes"))
    for column, moves in (result.get("transitions") or {}).items():
        lines.append(f"- `{column}` changes: " + ", ".join(f"`{move}` ×{count:,}" for move, count in moves.items()))
    for column, values in (result.get("categories") or {}).items():
        shown = list(values.items())[:8]
        lines.append(f"- `{column}` rows: " + ", ".join(f"`{value}` {_count(old)} → {_count(new)}"
                                                         for value, (old, new) in shown)
                     + (f", and {len(values) - 8} more" if len(values) > 8 else ""))
    if lines[-1] != "":
        lines.append("")
    if result.get("columns"):
        lines += _column_rows(result) + [""]
    groups = sorted((result.get("groups") or {}).items(),
                    key=lambda item: (-(item[1]["added"] + item[1]["removed"] + item[1]["changed"]), item[0]))
    if groups:
        lines += [f"| {result['group']} | Rows | Added | Removed | Changed | Largest Δ |", "|---|---|---|---|---|---|"]
        for label, stats in groups[:TOP]:
            largest = stats["max_abs"]
            lines.append(f"| {label} | {stats['before']:,} → {stats['after']:,} | {stats['added']:,} | "
                         f"{stats['removed']:,} | {stats['changed']:,} | "
                         + (f"`{largest['column']}` {_signed(largest['delta'])} ({largest['date'] or '—'})"
                            if largest else "—") + " |")
        if len(groups) > TOP:
            lines.append(f"| and {len(groups) - TOP} more | | | | | |")
        lines.append("")
    return lines


def render_markdown(report: dict, *, details: bool = True) -> str:
    summary, before, after = report["summary"], report["before"], report["after"]
    changed = len(summary["changed"]) + len(summary["new"]) + len(summary["gone"])
    lines = ["## Release data report: silver and gold", "",
             "Private: keep this report in the project's private folder. Post only the public summary on the PR.",
             ""]
    if not has_changes(report):
        lines.append(f"**No figures changed.** All {summary['tables']} silver and gold tables are unchanged.")
    else:
        lines.append(f"**{changed} of {summary['tables']} tables changed; {len(summary['unchanged'])} unchanged.**")
    frozen = summary.get("frozen_snapshots")
    if frozen:
        state = "changed (see below)" if frozen["problems"] else "unchanged"
        lines.append(f"Frozen month-end snapshots: {frozen['checked']:,} checked "
                     f"({frozen['frozen']:,} frozen or approved), {state}.")
    lines.append("")
    if summary["attention"]:
        lines += ["**Needs attention:**", ""] + [f"- {item}" for item in summary["attention"]] + [""]
    if report["biggest_changes"]:
        lines += ["### Biggest changes", "",
                  "| Table | Rows | Added | Removed | Changed | Dates | Largest change |",
                  "|---|---|---|---|---|---|---|"]
        for item in report["biggest_changes"][:TOP]:
            rows, result = item["rows"], report["tables"][item["table"]]
            lines.append(f"| {item['table']} | {_count(rows['before'])} → {_count(rows['after'])} | "
                         f"{_count(rows.get('added'))} | {_count(rows.get('removed'))} | "
                         f"{_count(rows.get('changed'))} | {_span(item['dates'])} | "
                         f"{_largest(item['largest'], result)} |")
        rest = [item["table"] for item in report["biggest_changes"][TOP:]]
        if rest:
            lines.append(f"\nAlso changed: {', '.join(rest)}.")
        lines.append("")
    if summary["unchanged"]:
        lines += [f"**Unchanged ({len(summary['unchanged'])}):** {', '.join(summary['unchanged'])}.", ""]
    if summary["absent"]:
        lines += [f"Not in either database: {', '.join(summary['absent'])}.", ""]
    lines += ["| | Before | After |", "|---|---|---|"]
    for label, name in (("Source", "source"), ("Read at (database clock)", "taken_at"),
                        ("Migrations applied", "migrations"), ("Last migration", "last_migration"),
                        ("Rows read", "rows_read")):
        lines.append(f"| {label} | {before.get(name) if before.get(name) is not None else '—'} | "
                     f"{after.get(name) if after.get(name) is not None else '—'} |")
    lines.append("")
    if report["migrations"]["added"] or report["migrations"]["removed"]:
        lines += ["Migrations " + "; ".join(
            f"{kind}: {', '.join(names)}" for kind, names in report["migrations"].items() if names) + ".", ""]
    changed_tables = [report["tables"][item["table"]] for item in report["biggest_changes"]]
    if not details:
        lines += ["Details by table are in the JSON report: they are too long for one PR comment.", ""]
    elif changed_tables or (frozen and (frozen["problems"] or frozen["notes"])):
        lines += ["<details><summary>Details by table</summary>", ""]
        if frozen and (frozen["problems"] or frozen["notes"]):
            lines += ["#### Frozen month-end snapshots", ""] + \
                [f"- {item}" for item in frozen["problems"] + frozen["notes"]] + [""]
        for result in changed_tables:
            lines += _table_details(result)
        lines += ["</details>", ""]
    lines.append(
        f"Views ({len(report['views_not_compared'])}) are not compared; they read the tables above. Δ is after "
        "minus before; relative is Δ over |before|; numbers equal within a relative "
        f"{report['tolerance']['relative']:g} count as unchanged. u…#n (IBKR accounts) and s…#n (other "
        "subjects) are labels for this report only. The report holds no account numbers, names or row values. "
        "Made by `scripts/release_data_report.py`.")
    return mask("\n".join(lines) + "\n")


def _public_attention(report: dict) -> list[str]:
    """The attention items without figures, ids or dates."""
    summary = report["summary"]
    items = []
    if (summary.get("frozen_snapshots") or {}).get("problems"):
        items.append("A frozen or approved month-end snapshot changed. They never change: stop and investigate.")
    if report["before"].get("rows_read") != report["after"].get("rows_read"):
        items.append("Before and after were read with different row-level security, so they are not comparable.")
    items += [f"`{name}` is no longer in the database." for name in summary["gone"]]
    return items


def _public_table(result: dict) -> list[str]:
    name = result["table"]
    if result["status"] == "new":
        return [f"- `{name}`: new table."]
    if result["status"] == "gone":
        return [f"- `{name}`: no longer in the database."]
    rows = result["rows"]
    lines = [f"- `{name}`: {rows['added']:,} added, {rows['removed']:,} removed, {rows['changed']:,} changed."]
    columns = result.get("columns") or {}
    numeric = sorted(column for column, stats in columns.items()
                     if stats.get("max_abs") or any(item.get("max_abs") for item in (stats.get("paths") or {}).values()))
    categories = sorted(set(result.get("transitions") or {}) | set(result.get("categories") or {}))
    other = sorted(column for column in columns if column not in numeric and column not in categories)
    lines.append("  - Numbers changed beyond tolerance: "
                 + (("yes: " + ", ".join(f"`{column}`" for column in numeric)) if numeric else "no") + ".")
    for column in categories:
        if column in (result.get("transitions") or {}):
            count = columns.get(column, {}).get("changed", 0)
            lines.append(f"  - `{column}`: {count:,} value{'' if count == 1 else 's'} changed.")
        else:
            lines.append(f"  - `{column}`: rows per value changed.")
    if other:
        lines.append("  - Other columns changed: " + ", ".join(f"`{column}`" for column in other) + ".")
    schema = result.get("schema") or {}
    if schema:
        parts = [f"{kind.replace('_', ' ')} " + ", ".join(f"`{column}`" for column in value)
                 for kind, value in schema.items() if isinstance(value, list)]
        if schema.get("key_changed"):
            parts.append("key changed")
        lines.append("  - Definition changed: " + "; ".join(parts) + ".")
    return lines


def render_public_summary(report: dict) -> str:
    """What may go on a public PR: counts and names only. No figures, deltas, relative
    changes, dates or subject labels; the full report stays private."""
    summary = report["summary"]
    changed = [item["table"] for item in report["biggest_changes"]]
    not_compared = len(summary["absent"]) + len(report["views_not_compared"])
    frozen = summary.get("frozen_snapshots")
    lines = ["## Release data report: public summary", "",
             f"Tables changed: {len(changed):,}. Unchanged: {len(summary['unchanged']):,}. "
             f"Not compared: {not_compared:,} (views, and tables in neither database).",
             "Frozen month-end snapshot check: "
             + ("not run (no reporting snapshots table)" if frozen is None else "fail" if frozen["problems"] else "pass")
             + ".", ""]
    attention = _public_attention(report)
    if attention:
        lines += ["**Needs attention:**", ""] + [f"- {item}" for item in attention] + [""]
    if changed:
        lines += ["### Changed tables", ""]
        for name in changed:
            lines += _public_table(report["tables"][name])
        lines.append("")
    if report["migrations"]["added"]:
        lines += ["Migrations applied for the candidate: "
                  + ", ".join(f"`{name}`" for name in report["migrations"]["added"]) + ".", ""]
    lines.append("Counts and names only. The full report, with the figures, is kept privately for the approver. "
                 "Made by `scripts/release_data_report.py`.")
    return mask("\n".join(lines) + "\n")


# ---- the figures command ----

def figures_command(args) -> int:
    """Each page's figures today against the certified figures, on one restored copy."""
    import release_data_figures as figures

    try:
        pages = figures.pages_for(args.batch, args.page)
        folder = figures.private_folder(args.private_dir)
        url = os.environ.get(args.url_env, "")
        if not url:
            raise ReportError(f"{args.url_env} is not set")
        import psycopg

        with psycopg.connect(_conninfo(url)) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (args.tenant,))
            taken_at = _header(connection, args.tenant)["taken_at"]
            subject_dates = figures.subjects_and_dates(connection, only=args.subject or None, sample=args.sample)
            comparison = figures.compare(pages, figures.WebPages(url, args.tenant), connection, subject_dates)
        report = figures.build_report(comparison, source=f"database ({args.url_env})", taken_at=taken_at)
    except Exception as error:  # noqa: BLE001 - one clear line, then exit 2
        print(f"release data report: ERROR {type(error).__name__}: {mask(str(error))}", file=sys.stderr)
        return 2
    markdown = figures.write_private(folder, "certified-figures-report.md", figures.render_markdown(report))
    data = figures.write_private(folder, "certified-figures-report.json",
                                 json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    public_summary = figures.render_public_summary(report)
    Path(args.public_summary).write_text(public_summary, encoding="utf-8")
    print(public_summary.rstrip())  # the values stay in the private files, out of terminal logs
    print(f"\nPublic summary for the PR: {Path(args.public_summary).resolve()}")
    print(f"Full report, keep private: {markdown} and {data}")
    return 1 if figures.has_differences(report) else 0


# ---- command line ----

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot", help="read one database into a snapshot file")
    snapshot.add_argument("--url-env", default="DATABASE_ADMIN_URL",
                          help="environment variable holding the database URL")
    snapshot.add_argument("--out", required=True, help="snapshot file (.jsonl, or .jsonl.gz to compress)")
    compare = sub.add_parser("compare", help="compare two snapshots or databases and write the report")
    compare.add_argument("--before", required=True, help="snapshot file, or env:NAME for a database URL")
    compare.add_argument("--after", required=True, help="snapshot file, or env:NAME for a database URL")
    compare.add_argument("--markdown", default="release-data-report.md", help="full Markdown report (private)")
    compare.add_argument("--json", default="release-data-report.json", help="full JSON report (private)")
    compare.add_argument("--public-summary", default="release-data-summary.md",
                         help="counts and names only, for the PR")
    compare.add_argument("--rel-tol", type=float, default=1e-9,
                         help="numbers this close (relative) count as unchanged")
    compare.add_argument("--abs-tol", type=float, default=1e-12)
    figures = sub.add_parser("figures", help="compare each page's figures today with the certified figures "
                                             "on a restored copy")
    figures.add_argument("--batch", action="append", default=[], help="a batch of pages (1, 2 or 3); repeatable")
    figures.add_argument("--page", action="append", default=[], help="one page, such as overview; repeatable")
    figures.add_argument("--url-env", default="DATABASE_ADMIN_URL",
                         help="environment variable holding the copy's database URL")
    figures.add_argument("--subject", action="append", default=[],
                         help="only this account or portfolio; repeatable")
    figures.add_argument("--sample", type=int, default=12,
                         help="other NAV dates per subject besides the latest and each month-end")
    figures.add_argument("--private-dir", required=True,
                         help="private folder for the full report, with values (outside the checkout)")
    figures.add_argument("--public-summary", default="certified-figures-summary.md",
                         help="counts, figure ids and page names only, for the PR")
    figures.add_argument("--tenant", default=medallion_layers.DEFAULT_TENANT,
                         help="app.tenant_id for a login that does not bypass row-level security")
    public = sub.add_parser("summary", help="write the public summary from a saved JSON report")
    public.add_argument("--report", default="release-data-report.json", help="full JSON report")
    public.add_argument("--out", default="release-data-summary.md", help="public summary for the PR")
    for command in (snapshot, compare):
        command.add_argument("--tenant", default=medallion_layers.DEFAULT_TENANT,
                             help="app.tenant_id for a login that does not bypass row-level security")
        command.add_argument("--table", action="append", default=[],
                             help="only this table (layer.name or name); repeatable")
    args = parser.parse_args(argv)
    if args.command == "figures":
        return figures_command(args)
    if args.command == "summary":
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        text = render_public_summary(report)
        Path(args.out).write_text(text, encoding="utf-8")
        print(text.rstrip())
        return 1 if has_changes(report) else 0
    only = args.table or None
    try:
        if args.command == "snapshot":
            url = os.environ.get(args.url_env, "")
            if not url:
                raise ReportError(f"{args.url_env} is not set")
            header, tables = database_source(url, args.tenant, only)
            counts = write_snapshot(header, tables, Path(args.out))
            print(json.dumps({"snapshot": str(Path(args.out).resolve()), "taken_at": header["taken_at"],
                              "tables": len(counts),
                              "present": sum(1 for value in counts.values() if value is not None),
                              "rows": sum(value or 0 for value in counts.values())}, sort_keys=True))
            return 0
        before = open_source(args.before, args.tenant, only)
        after = open_source(args.after, args.tenant, only)
        report = build_report(before, after, rel_tol=args.rel_tol, abs_tol=args.abs_tol)
    except Exception as error:  # noqa: BLE001 - one clear line, then exit 2
        print(f"release data report: ERROR {type(error).__name__}: {mask(str(error))}", file=sys.stderr)
        return 2
    markdown = render_markdown(report)
    if len(markdown) > COMMENT_LIMIT:
        markdown = render_markdown(report, details=False)
    Path(args.markdown).write_text(markdown, encoding="utf-8")
    Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    public_summary = render_public_summary(report)
    Path(args.public_summary).write_text(public_summary, encoding="utf-8")
    print(public_summary.rstrip())  # the figures stay in the files, out of terminal logs
    print(f"\nPublic summary for the PR: {Path(args.public_summary).resolve()}")
    print(f"Full report, keep private: {Path(args.markdown).resolve()} and {Path(args.json).resolve()}")
    return 1 if has_changes(report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
