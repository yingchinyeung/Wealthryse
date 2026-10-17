"""Medallion step S3: read-only checks before and after each layer batch, and the undo SQL.

Run from a clone of main with a Python that has psycopg, on a machine that reaches the
database (the admin VM), first against the restored copy and then against live:

    python scripts/medallion_layers.py snapshot --account <pilot account> --out before-gold.json
    (the bootstrap run: bootstrap_postgres.py --only-migration 068_medallion_gold_layer.sql ...)
    python scripts/medallion_layers.py check --before before-gold.json --batch gold --account <pilot account>
    python scripts/medallion_layers.py undo-sql gold

snapshot and check only read. Their connection is read-only and they write nothing
except the --out file. The database URL comes from DATABASE_ADMIN_URL (or the variable
named by --url-env) and is never printed. check exits 1 when a check fails.

What check compares with the snapshot taken before the batch:
- schema_migrations: every earlier row unchanged, applied_at included, so nothing ran
  again (062 and the stage-43 correction among them), and exactly one new row: the batch.
- row counts of every table the catalog moves (live_* tables are written every few
  minutes, so their counts are reported, not compared), and each table's count read
  through its public view equals the count read from the table.
- each reporting_snapshots row's stored data_sha256 and a sha256 of its payload. Rows
  created after the snapshot was taken are allowed (the month-end factsheet job freezes
  new snapshots at 09:00 UTC, in the batch window), and so is an existing row going from
  frozen to superseded when such a new row is for the same subject and month; its stored
  sha256 and payload must not change. The row count and the agent month_end_snapshots
  view may grow by those rows, and the burn-in's latest snapshot may become one of them.
- the stage-43 correction: report_artifact_releases revocation state, and no duplicate
  active release groups.
- the burn-in queries (scripts/run_burn_in_check.py) return the same answers, and the
  agent views return the same rows (bounded counts) for every active account.
And on the state after the batch:
- the batch's tables and views are in their layer schema, each with a security_invoker
  view in public whose columns equal the table's and whose grants equal the table's;
- under a tenant id that does not exist, every tenant-scoped table read through its
  public view returns no rows (row-level security still applies through the views);
- each bronze table has the append-only triggers.

undo-sql prints the SQL that moves one layer back to public, for a forward migration
(it does not connect). Commit it as the next numbered migration, apply it with
bootstrap_postgres.py --only-migration as for the batch, and check with
--applied <that migration> instead of --batch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "infra" / "medallion" / "catalog.json"
MIGRATIONS = ROOT / "migrations" / "postgres"
LAYERS = ("gold", "bronze", "silver")  # batch order
BATCH_MIGRATION = {
    "gold": "068_medallion_gold_layer.sql",
    "bronze": "069_medallion_bronze_layer.sql",
    "silver": "070_medallion_silver_layer.sql",
}
# The live collector writes these every few minutes.
VOLATILE = {"live_account_values", "live_positions", "live_quotes", "live_executions",
            "live_daily_bars", "live_contracts"}
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
AGENT_ROW_LIMIT = 1000
STAGE43 = "ms-september-disclaimer-duplicate-20261011-v1"


def catalog_objects() -> list[dict]:
    """The moved objects: name, kind, layer."""
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))["postgres"]
    return [
        {"name": key.split(".", 1)[1], "kind": value["kind"], "layer": value["layer"]}
        for key, value in sorted(entries.items())
        if key.startswith("public.") and value["layer"] in LAYERS
    ]


def _one(connection, query, parameters=()):
    row = connection.execute(query, parameters).fetchone()
    return None if row is None else row[0]


def _relation(connection, schema: str, name: str):
    return connection.execute(
        "SELECT c.oid, c.relkind, c.relrowsecurity, c.relforcerowsecurity, c.reloptions "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s",
        (schema, name)).fetchone()


def _columns(connection, oid) -> list[list[str]]:
    rows = connection.execute(
        "SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped ORDER BY attnum", (oid,)).fetchall()
    return [[row[0], row[1]] for row in rows]


def _grants(connection, oid) -> list[str]:
    rows = connection.execute(
        """SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END || ':' ||
                  a.privilege_type || CASE WHEN a.is_grantable THEN '*' ELSE '' END
             FROM pg_class c, aclexplode(c.relacl) a
            WHERE c.oid = %s AND a.grantee <> c.relowner
              AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
           UNION ALL
           SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END || ':' ||
                  a.privilege_type || '(' || att.attname || ')'
             FROM pg_attribute att, aclexplode(att.attacl) a
            WHERE att.attrelid = %s AND att.attnum > 0 AND NOT att.attisdropped
              AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE')
            ORDER BY 1""", (oid, oid)).fetchall()
    return [row[0] for row in rows]


def _count(connection, schema: str, name: str) -> int:
    from psycopg import sql

    return _one(connection, sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, name)))


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _objects(connection, tenant: str) -> dict:
    found = {}
    stranger = str(uuid.uuid4())
    for item in catalog_objects():
        name, layer = item["name"], item["layer"]
        in_layer = _relation(connection, layer, name)
        in_public = _relation(connection, "public", name)
        entry = {"kind": item["kind"], "layer": layer}
        if in_layer is None and in_public is None:
            entry["location"] = None
            found[name] = entry
            continue
        real, schema = (in_layer, layer) if in_layer is not None else (in_public, "public")
        entry["location"] = schema
        entry["columns"] = _columns(connection, real[0])
        entry["grants"] = _grants(connection, real[0])
        compat = in_layer is not None and in_public is not None and in_public[1] == "v"
        if compat:
            entry["view"] = {
                "columns": _columns(connection, in_public[0]),
                "grants": _grants(connection, in_public[0]),
                "security_invoker": "security_invoker=true" in (in_public[4] or []),
            }
        if real[1] in ("r", "p"):
            entry["rows"] = _count(connection, schema, name)
            if compat:
                entry["view"]["rows"] = _count(connection, "public", name)
            entry["row_security"] = {"enabled": real[2], "forced": real[3]}
            if real[2]:
                # A tenant that does not exist sees nothing, through the view as on the table.
                connection.execute("SELECT set_config('app.tenant_id', %s, true)", (stranger,))
                entry["row_security"]["stranger_rows"] = _count(connection, "public", name)
                connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
        if schema == "bronze":
            entry["triggers"] = [row[0] for row in connection.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = %s AND NOT tgisinternal ORDER BY 1",
                (real[0],)).fetchall()]
        found[name] = entry
    return found


def _agent_views(connection, tenant: str) -> dict | None:
    from psycopg import sql

    if _one(connection, "SELECT to_regnamespace('agent')") is None:
        return None
    subjects = [row[0] for row in connection.execute(
        "SELECT subject_id FROM public.platform_subjects WHERE tenant_id = %s AND lifecycle_status = 'active' "
        "ORDER BY 1", (tenant,)).fetchall()]
    connection.execute("SELECT set_config('agent.subjects', %s, true)", (",".join(subjects),))
    views = [row[0] for row in connection.execute(
        "SELECT relname FROM pg_class WHERE relnamespace = 'agent'::regnamespace AND relkind = 'v' ORDER BY 1"
    ).fetchall()]
    result = {"subjects": len(subjects), "views": {}}
    for view in views:
        connection.execute("SAVEPOINT agent_view")
        try:
            result["views"][view] = _one(connection, sql.SQL("SELECT count(*) FROM (SELECT 1 FROM {} LIMIT {}) q")
                                         .format(sql.Identifier("agent", view), sql.Literal(AGENT_ROW_LIMIT)))
            connection.execute("RELEASE SAVEPOINT agent_view")
        except Exception as error:  # report the view and carry on
            connection.execute("ROLLBACK TO SAVEPOINT agent_view")
            result["views"][view] = "error: " + type(error).__name__
    connection.execute("SELECT set_config('agent.subjects', '', true)")
    return result


def _stage43(connection, tenant: str) -> dict:
    rows = connection.execute(
        "SELECT artifact_release_id, revoked_at, revoked_by, state_revision FROM public.report_artifact_releases "
        "WHERE tenant_id = %s ORDER BY artifact_release_id", (tenant,)).fetchall()
    duplicates = _one(connection, """SELECT count(*) FROM (SELECT s.subject_id, s.period_end, r.report_type
        FROM public.report_artifact_releases a
        JOIN public.report_generation_runs r ON r.tenant_id = a.tenant_id AND r.generation_run_id = a.generation_run_id
        JOIN public.reporting_snapshots s ON s.tenant_id = r.tenant_id AND s.snapshot_id = r.snapshot_id
        WHERE a.tenant_id = %s AND a.revoked_at IS NULL AND a.release_scope = 'internal_shareholder'
        GROUP BY s.subject_id, s.period_end, r.report_type HAVING count(*) > 1) q""", (tenant,))
    return {"releases_sha256": _sha([list(row) for row in rows]), "releases": len(rows),
            "corrected_releases": sum(1 for row in rows if row[2] == STAGE43),
            "duplicate_active_groups": duplicates}


def _burn_in(conninfo: str, tenant: str, account: str, day: str) -> dict:
    import inspect
    from datetime import date

    for path in (str(ROOT / "src"), str(ROOT / "scripts")):
        if path not in sys.path:
            sys.path.insert(0, path)
    from run_burn_in_check import _database_state

    arguments = [conninfo, tenant, account]
    if len(inspect.signature(_database_state).parameters) > 3:  # the HKT day the answers are for
        arguments.append(date.fromisoformat(day))
    return json.loads(json.dumps(_database_state(*arguments), default=str))


def _hkt_day() -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat()


def snapshot(conninfo: str, *, tenant: str, account: str | None, burn_in_day: str | None = None) -> dict:
    import psycopg

    files = {path.name: hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
             for path in sorted(MIGRATIONS.glob("*.sql"))}
    with psycopg.connect(conninfo) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
        identity = connection.execute(
            "SELECT current_user, r.rolsuper, r.rolbypassrls, current_setting('server_version'), now()::text, "
            "current_schema() FROM pg_roles r WHERE r.rolname = current_user").fetchone()
        ledger = [list(row) for row in connection.execute(
            "SELECT version, sha256, applied_at FROM public.schema_migrations ORDER BY version").fetchall()]
        applied = {row[0]: row[1] for row in ledger}
        state = {
            "taken_at": identity[4],
            "identity": {"role": identity[0], "superuser": identity[1], "bypass_rls": identity[2],
                         "server_version": identity[3], "current_schema": identity[5]},
            "tenant": tenant,
            "layers": [row[0] for row in connection.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s) ORDER BY 1", (list(LAYERS),)).fetchall()],
            "schema_migrations": ledger,
            "pending": sorted(name for name in files if name not in applied),
            "checksum_changed": sorted(name for name, digest in applied.items()
                                       if name in files and files[name] != digest),
            "not_in_repository": sorted(name for name in applied if name not in files),
            "objects": _objects(connection, tenant),
            "reporting_snapshots": [list(row) for row in connection.execute(
                "SELECT snapshot_id::text, status, data_sha256, "
                "encode(sha256(convert_to(snapshot_payload_json, 'UTF8')), 'hex'), "
                "subject_id, period_end::text, created_at::text "
                "FROM public.reporting_snapshots ORDER BY snapshot_id").fetchall()],
            "stage43": _stage43(connection, tenant),
            "agent": _agent_views(connection, tenant),
            "role_settings": [list(row) for row in connection.execute(
                "SELECT r.rolname, s.setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole "
                "WHERE s.setdatabase IN (0, (SELECT oid FROM pg_database WHERE datname = current_database())) "
                "ORDER BY 1").fetchall()],
        }
    if account:
        state["burn_in_day"] = burn_in_day or _hkt_day()
        state["burn_in"] = _burn_in(conninfo, tenant, account, state["burn_in_day"])
    return state


def after_problems(state: dict, batches: list[str]) -> list[str]:
    """What must hold once the given batches have run."""
    problems = []
    for name, entry in state["objects"].items():
        if entry["layer"] not in batches:
            continue
        if entry["location"] is None:
            continue  # reported separately: the table does not exist here
        where = f"{entry['layer']}.{name}"
        if entry["location"] != entry["layer"]:
            problems.append(f"{name} is in {entry['location']}, not {entry['layer']}")
            continue
        view = entry.get("view")
        if view is None:
            problems.append(f"public.{name} is not a compatibility view over {where}")
            continue
        if not view["security_invoker"]:
            problems.append(f"public.{name} is not security_invoker")
        if view["columns"] != entry["columns"]:
            problems.append(f"public.{name} columns differ from {where}")
        if view["grants"] != entry["grants"]:
            problems.append(f"public.{name} grants {view['grants']} differ from {where} {entry['grants']}")
        if "rows" in entry and view.get("rows") != entry["rows"]:
            problems.append(f"public.{name} shows {view.get('rows')} rows, {where} has {entry['rows']}")
        security = entry.get("row_security") or {}
        if security.get("stranger_rows"):
            problems.append(f"public.{name} shows {security['stranger_rows']} rows to an unknown tenant")
        if entry["layer"] == "bronze" and not {"bronze_append_only", "bronze_no_truncate"} <= set(entry["triggers"]):
            problems.append(f"{where} lacks the append-only triggers")
    return problems


def compare(before: dict, after: dict, batch: str | None,
            applied: str | None = None) -> tuple[list[str], list[str]]:
    problems, notes = [], []
    old = {row[0]: row for row in before["schema_migrations"]}
    new = {row[0]: row for row in after["schema_migrations"]}
    for version, row in old.items():
        if new.get(version) != row:
            problems.append(f"schema_migrations row {version} changed or disappeared")
    added = sorted(set(new) - set(old))
    expected = [applied] if applied else [BATCH_MIGRATION[batch]] if batch else []
    if added != expected:
        problems.append(f"schema_migrations gained {added}, expected {expected}")
    appended = _reporting_snapshots(before, after, problems, notes)
    for name, entry in before["objects"].items():
        later = after["objects"].get(name, {})
        if entry.get("rows") != later.get("rows"):
            text = f"{name} rows {entry.get('rows')} -> {later.get('rows')}"
            grown = name == "reporting_snapshots" and later.get("rows") == (entry.get("rows") or 0) + len(appended)
            (notes if name in VOLATILE or grown else problems).append(text)
        if (entry["location"] is None) != (later.get("location") is None):
            problems.append(f"{name} existence changed")
    if before["stage43"] != after["stage43"]:
        problems.append(f"stage-43 release state changed: {before['stage43']} -> {after['stage43']}")
    old_burn_in, new_burn_in = before.get("burn_in"), after.get("burn_in")
    if old_burn_in != new_burn_in:
        if _new_latest_snapshot(old_burn_in, new_burn_in, appended):
            notes.append("burn-in latest snapshot is one created during the batch window")
        else:
            problems.append(f"burn-in answers changed: {old_burn_in} -> {new_burn_in}")
    if before.get("agent") != after.get("agent"):
        if _agent_grew(before.get("agent"), after.get("agent"), len(appended)):
            notes.append("agent month_end_snapshots shows the snapshots created during the batch window")
        else:
            problems.append(f"agent views changed: {before.get('agent')} -> {after.get('agent')}")
    return problems, notes


def _after(value: str, moment: str) -> bool:
    from datetime import datetime, timezone

    def instant(text: str):
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    try:
        return instant(value) > instant(moment)
    except (TypeError, ValueError):
        return False


def _reporting_snapshots(before: dict, after: dict, problems: list[str], notes: list[str]) -> set[str]:
    """Existing rows unchanged; rows created after the snapshot allowed. Returns their ids."""
    old = {row[0]: row for row in before["reporting_snapshots"]}
    new = {row[0]: row for row in after["reporting_snapshots"]}
    appended = {key for key in new if key not in old}
    late = sorted(key for key in appended if not _after(new[key][6], before["taken_at"]))
    if late:
        problems.append(f"reporting_snapshots gained rows created before the snapshot: {late}")
    fresh = {(new[key][4], new[key][5]) for key in appended if key not in late}
    for key, row in old.items():
        later = new.get(key)
        if later == row:
            continue
        superseded = (later is not None and row[1] == "frozen" and later[1] == "superseded"
                      and later[:1] + later[2:] == row[:1] + row[2:] and (row[4], row[5]) in fresh)
        if superseded:
            notes.append(f"reporting_snapshots {key} superseded by a snapshot created during the batch window")
        else:
            problems.append(f"reporting_snapshots row {key} changed or disappeared")
    if appended and not late:
        notes.append(f"reporting_snapshots: {len(appended)} rows created during the batch window")
    return appended - set(late)


def _new_latest_snapshot(old: dict | None, new: dict | None, appended: set[str]) -> bool:
    if not (old and new and appended):
        return False
    moving = {"latest_snapshot", "ready_report_types"}
    if any(old.get(key) != new.get(key) for key in set(old) | set(new) if key not in moving):
        return False
    return str((new.get("latest_snapshot") or {}).get("snapshot_id")) in appended


def _agent_grew(old: dict | None, new: dict | None, appended: int) -> bool:
    if not (old and new and appended) or old.get("subjects") != new.get("subjects"):
        return False
    views_old, views_new = old.get("views") or {}, new.get("views") or {}
    if set(views_old) != set(views_new):
        return False
    for view, count in views_old.items():
        later = views_new[view]
        if view == "month_end_snapshots" and isinstance(count, int) and isinstance(later, int):
            if not 0 <= later - count <= appended:
                return False
        elif later != count:
            return False
    return True


def summary(state: dict, batches: list[str]) -> dict:
    missing = sorted(name for name, entry in state["objects"].items() if entry["location"] is None)
    errors = sorted(view for view, value in ((state.get("agent") or {}).get("views") or {}).items()
                    if isinstance(value, str))
    empty = sorted(view for view, value in ((state.get("agent") or {}).get("views") or {}).items() if value == 0)
    return {"layers": state["layers"], "pending_migrations": state["pending"],
            "checksum_changed": state["checksum_changed"], "not_in_repository": state["not_in_repository"],
            "catalog_objects_missing": missing, "agent_view_errors": errors, "agent_views_empty": empty,
            "after_problems": after_problems(state, batches)}


def undo_sql(layer: str) -> str:
    names = [item["name"] for item in catalog_objects() if item["layer"] == layer]
    items = ",\n".join(f"        '{name}'" for name in names)
    lines = [f"-- Undo medallion batch {layer}: move its tables and views back to public.",
             "-- Generated by scripts/medallion_layers.py undo-sql. Objects not in the layer are",
             "-- skipped, so it is safe to run again. Data, grants and policies move with each table.",
             "", "SET LOCAL lock_timeout = '20s';", ""]
    if layer == "bronze":
        lines += ["DO $$", "DECLARE", "    rel text;", "BEGIN",
                  "    IF to_regnamespace('bronze') IS NULL THEN RETURN; END IF;",
                  "    FOR rel IN SELECT relname FROM pg_class",
                  "                WHERE relnamespace = 'bronze'::regnamespace AND relkind IN ('r', 'p') LOOP",
                  "        EXECUTE format('DROP TRIGGER IF EXISTS bronze_append_only ON bronze.%I', rel);",
                  "        EXECUTE format('DROP TRIGGER IF EXISTS bronze_no_truncate ON bronze.%I', rel);",
                  "    END LOOP;", "    DROP FUNCTION IF EXISTS bronze.refuse_change();", "END;", "$$;", ""]
    lines += ["DO $$", "DECLARE", f"    layer CONSTANT text := '{layer}';",
              "    moved CONSTANT text[] := ARRAY[", items, "    ];", "    rel text;", '    kind "char";', "BEGIN",
              "    FOREACH rel IN ARRAY moved LOOP",
              "        SELECT relkind INTO kind FROM pg_class WHERE oid = to_regclass(format('%I.%I', layer, rel));",
              "        IF kind IS NULL THEN",
              "            CONTINUE;",
              "        END IF;",
              "        IF EXISTS (SELECT 1 FROM pg_class WHERE oid = to_regclass(format('public.%I', rel))",
              "                   AND relkind = 'v') THEN",
              "            EXECUTE format('DROP VIEW public.%I', rel);  -- the compatibility view",
              "        END IF;",
              "        EXECUTE format('ALTER %s %I.%I SET SCHEMA public',",
              "                       CASE WHEN kind = 'v' THEN 'VIEW' ELSE 'TABLE' END, layer, rel);",
              "    END LOOP;",
              "    IF to_regnamespace(layer) IS NOT NULL",
              "       AND NOT EXISTS (SELECT 1 FROM pg_class WHERE relnamespace = to_regnamespace(layer))",
              "       AND NOT EXISTS (SELECT 1 FROM pg_proc WHERE pronamespace = to_regnamespace(layer)) THEN",
              "        EXECUTE format('DROP SCHEMA %I', layer);",
              "    END IF;",
              "END;", "$$;", ""]
    return "\n".join(lines)


def _conninfo(url_env: str) -> str:
    from psycopg.conninfo import make_conninfo

    url = os.environ.get(url_env, "")
    if not url.startswith(("postgres://", "postgresql://")):
        raise SystemExit(f"{url_env} must hold a PostgreSQL URL")
    return make_conninfo(url, options="-c default_transaction_read_only=on -c statement_timeout=300000",
                         application_name="medallion-layers-check")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "check"):
        command = sub.add_parser(name)
        command.add_argument("--url-env", default="DATABASE_ADMIN_URL")
        command.add_argument("--tenant", default=DEFAULT_TENANT)
        command.add_argument("--account", help="the burn-in pilot account; leave out to skip the burn-in queries")
        command.add_argument("--out", help="also write the full state to this file")
    sub.choices["check"].add_argument("--before", help="a snapshot taken before the batch")
    sub.choices["check"].add_argument("--batch", choices=LAYERS,
                                      help="the batch that ran since --before (none: nothing should have changed)")
    sub.choices["check"].add_argument("--applied", help="the one migration applied since --before, when it is "
                                      "not the batch's own (an undo migration, for example)")
    undo = sub.add_parser("undo-sql")
    undo.add_argument("layer", choices=LAYERS)
    args = parser.parse_args(argv)

    if args.command == "undo-sql":
        print(undo_sql(args.layer))
        return 0
    before = None
    if args.command == "check" and args.before:
        before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    state = snapshot(_conninfo(args.url_env), tenant=args.tenant, account=args.account,
                     burn_in_day=(before or {}).get("burn_in_day"))
    if args.out:
        Path(args.out).write_text(json.dumps(state, indent=1, sort_keys=True, default=str), encoding="utf-8")
    batches = [layer for layer in LAYERS if layer in state["layers"]]
    report = {"command": args.command, "taken_at": state["taken_at"], **summary(state, batches)}
    problems = list(report["after_problems"])
    if before is not None:
        changed, notes = compare(before, state, args.batch, args.applied)
        problems += changed
        report["compared_with"] = before["taken_at"]
        report["notes"] = notes
    report["problems"] = problems
    report["status"] = "pass" if not problems else "fail"
    print(json.dumps(report, indent=1, sort_keys=True, default=str))
    return 0 if args.command == "snapshot" or not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
