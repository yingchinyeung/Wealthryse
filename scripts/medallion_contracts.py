"""Write and check the silver and gold data contracts in infra/medallion/contracts/.

  python scripts/medallion_contracts.py validate           offline: every catalog object has a
                                                           well-formed contract in the right layer
  python scripts/medallion_contracts.py check    [--dsn]   compare contracts with the schema
  python scripts/medallion_contracts.py generate [--dsn]   write the structural part of each contract

Without --dsn, check and generate build a disposable database from migrations/postgres
on the server named by AGENT_TEST_PG_ADMIN_DSN (a superuser DSN; CI's agent-database
job has one), read it, and drop it. With --dsn they read an already-migrated database
and change nothing in it.

generate rewrites only what the schema decides: columns, types, nullability, keys, the
freshness promise from ibkr_etl.layer_freshness and who reads and writes the table. It
keeps descriptions, classifications, quality rules and everything else written by hand,
and never changes a version: raise it yourself as `check` says (major for removals,
renames, type narrowing or key changes; minor for additions).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ibkr_etl import layer_freshness  # noqa: E402
from ibkr_etl import medallion_contracts as mc  # noqa: E402

MIGRATIONS = ROOT / "migrations" / "postgres"
CATALOG = ROOT / "infra" / "medallion" / "catalog.json"
CONTRACTS = ROOT / "infra" / "medallion" / "contracts"
CODE = ROOT / "src" / "ibkr_etl"
ADMIN_ENV = "AGENT_TEST_PG_ADMIN_DSN"
OWNER = "medallion_contracts_owner"
PASSWORD = "medallion-contracts-test-only"  # throwaway role in a disposable server
TENANTS = ("00000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000001")
# These two fail on a fresh database (they repair rows only production has); db.py and
# the layer tests tolerate them the same way.
TOLERATED = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
CREATED = "2026-10-18T00:00:00Z"
TEAM = [{"username": "portfolio-platform", "role": "owner"}]
# Default classification of a new column; a hand-set classification is always kept.
# Restricted: can hold an IBKR account number. Internal: load metadata and versions.
RESTRICTED_COLUMNS = re.compile(r"^(account_id|source_account_id|subject_id|alias)$")
METADATA_COLUMNS = re.compile(
    r"^(tenant_id|.*_run_id|.*sha256|created_at|updated_at|fetched_at|generated_at|checked_at|first_seen_at|"
    r"last_seen_at|as_of_at|observed_at|quote_observed_at|provider|source|source_url|source_reference|source_key|"
    r"source_kind|source_type|quality_status|observation_status|license_status|status|error_code|.*_version|"
    r"duration_ms|timings_json|price_type|mapping_method|proxy_reason)$")


# ---------------------------------------------------------------- database


def _params(admin_dsn: str, database: str, user: str | None = None, tenant: str | None = None) -> dict:
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(admin_dsn)
    params["dbname"] = database
    if user:
        params["user"], params["password"] = user, PASSWORD
    if tenant:
        params["options"] = f"-c app.tenant_id={tenant}"
    return params


def build(admin_dsn: str, database: str, migrations: Path = MIGRATIONS) -> list[str]:
    """Create `database` and apply every migration in order as a non-superuser owner, like a new environment."""
    import psycopg

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER,)).fetchone() is None:
            admin.execute(f"CREATE ROLE {OWNER} LOGIN CREATEROLE PASSWORD '{PASSWORD}'")
        admin.execute(f'CREATE DATABASE "{database}" OWNER {OWNER}')
    with psycopg.connect(**_params(admin_dsn, database), autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    applied = []
    with psycopg.connect(**_params(admin_dsn, database, OWNER, TENANTS[-1])) as owner:
        owner.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, "
                      "applied_at TEXT NOT NULL)")
        owner.commit()
        for path in sorted(Path(migrations).glob("*.sql")):
            text = path.read_text(encoding="utf-8")
            try:
                owner.execute(text)
                owner.commit()
            except Exception:
                owner.rollback()
                if path.name not in TOLERATED:
                    raise
            owner.execute("INSERT INTO schema_migrations VALUES (%s, %s, '2026-10-01T00:00:00+00:00')",
                          (path.name, hashlib.sha256(text.encode("utf-8")).hexdigest()))
            if owner.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                    owner.execute("SELECT 1 FROM tenants").fetchone() is None:
                for tenant in TENANTS:  # later migrations expect tenants to exist
                    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
                    owner.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                  "VALUES (%s, 'Test', 'active', '2026-01-01')", (tenant,))
            owner.commit()
            applied.append(path.name)
    return applied


def drop(admin_dsn: str, database: str) -> None:
    import psycopg

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        # Another disposable database of this tool may still be open.
        if admin.execute("SELECT 1 FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba WHERE r.rolname = %s",
                         (OWNER,)).fetchone() is None:
            admin.execute(f"DROP ROLE IF EXISTS {OWNER}")


def read_structure(connection, catalog: dict[str, dict]) -> dict[str, dict | None]:
    """name -> introspect() result (plus comments and dependent views) for every catalog object."""
    found = {}
    for name, entry in sorted(catalog.items()):
        physical = f"{entry['layer']}.{name}"
        actual = mc.introspect(connection, physical)
        if actual is not None:
            schema, rel = physical.split(".")
            oid = connection.execute("SELECT %s::regclass::oid", (physical,)).fetchone()[0]
            actual["comment"] = connection.execute("SELECT obj_description(%s, 'pg_class')", (oid,)).fetchone()[0]
            actual["columnComments"] = dict(connection.execute(
                "SELECT attname, col_description(attrelid, attnum) FROM pg_attribute WHERE attrelid = %s "
                "AND attnum > 0 AND NOT attisdropped AND col_description(attrelid, attnum) IS NOT NULL",
                (oid,)).fetchall())
            # Views that read this object, other than its own public compatibility view.
            actual["views"] = sorted(r[0] for r in connection.execute(
                "SELECT DISTINCT n.nspname || '.' || v.relname FROM pg_depend d "
                "JOIN pg_rewrite w ON w.oid = d.objid JOIN pg_class v ON v.oid = w.ev_class "
                "JOIN pg_namespace n ON n.oid = v.relnamespace "
                "WHERE d.refobjid = %s AND d.classid = 'pg_rewrite'::regclass AND v.oid <> %s "
                "AND NOT (n.nspname = 'public' AND v.relname = %s)", (oid, oid, rel)).fetchall())
        found[name] = actual
    return found


# ---------------------------------------------------------------- code scan


def scan_code(names: list[str], code: Path = CODE) -> dict[str, dict[str, list[str]]]:
    """name -> {"readers": [...], "writers": [...]}: modules whose SQL reads or writes the table."""
    found = {name: {"readers": set(), "writers": set()} for name in names}
    prefix = r"(?:(?:public|silver|gold)\.)?"
    patterns = {name: (re.compile(rf"\b(?:FROM|JOIN)\s+{prefix}{re.escape(name)}\b", re.I),
                       re.compile(rf"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+"
                                  rf"{prefix}{re.escape(name)}\b", re.I)) for name in names}
    for path in sorted(Path(code).rglob("*.py")):
        module = ".".join(path.relative_to(Path(code).parent).with_suffix("").parts)
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, (reads, writes) in patterns.items():
            if name not in text:
                continue
            if reads.search(text):
                found[name]["readers"].add(module)
            if writes.search(text):
                found[name]["writers"].add(module)
    return {name: {k: sorted(v) for k, v in sides.items()} for name, sides in found.items()}


# ---------------------------------------------------------------- contracts


def _sla(name: str) -> tuple[str | None, list[dict], dict | None]:
    """slaDefaultElement, slaProperties and the freshnessCheck custom property from layer_freshness."""
    rule = next((r for r in layer_freshness.RULES if r.target == f"public.{name}"), None)
    if rule is None:
        return None, [], None
    column = re.search(r"MAX\((\w+)\)", rule.query or "").group(1)
    element = f"{name}.{column}"
    if rule.measure == layer_freshness.AS_OF:
        latency = {"property": "latency", "value": rule.max_age.days, "unit": "d", "element": element}
    else:
        latency = {"property": "latency", "value": int(rule.max_age.total_seconds() // 3600), "unit": "h",
                   "element": element}
    props = [
        {"property": "frequency", "value": 1, "unit": "d", "element": element},
        {"property": "timeOfAvailability", "value": f"{rule.due_utc:%H:%M}+00:00", "element": element,
         "driver": "operational"},
        {**latency, "driver": "operational"},
    ]
    check = {"checkId": rule.check_id, "measure": rule.measure, "required": rule.required,
             "query": rule.query, "source": "ibkr_etl.layer_freshness.RULES"}
    return element, props, check


def _classification(column: str, source: str) -> str:
    if RESTRICTED_COLUMNS.match(column):
        return "restricted"
    if source == "market-data" or METADATA_COLUMNS.match(column):
        return "internal"
    return "confidential-financial"


PROPERTY_ORDER = ("name", "physicalType", "logicalType", "logicalTypeOptions", "required", "unique",
                  "primaryKey", "primaryKeyPosition", "classification", "description", "examples", "quality",
                  "customProperties")
GENERATED_CUSTOM = ("source", "catalogNote", "readByViews", "consumers", "producers", "freshnessCheck")


def merge(name: str, entry: dict, actual: dict, existing: dict | None, scan: dict) -> dict:
    """The contract for one object: structure from `actual`, everything hand-written kept from `existing`."""
    existing = existing or {}
    layer, source = entry["layer"], entry.get("source", "")
    old_obj = mc.table_object(existing) if existing.get("schema") else {}
    old_props = {p.get("name"): p for p in old_obj.get("properties") or []}
    key = {col: i + 1 for i, col in enumerate(actual["primaryKey"])}
    properties = []
    for col in actual["columns"]:
        old = old_props.get(col["name"], {})
        prop = dict(old)
        logical = mc.logical_type(col["physicalType"])
        if logical == "string" and old.get("logicalType") == "date":
            logical = "date"  # a date stored as ISO text, set by hand
        prop.update({
            "name": col["name"], "physicalType": col["physicalType"],
            "logicalType": logical, "required": col["required"],
            "unique": col["name"] in actual["unique"], "primaryKey": col["name"] in key,
            "primaryKeyPosition": key.get(col["name"], -1),
        })
        prop.setdefault("classification", _classification(col["name"], source))
        comment = actual.get("columnComments", {}).get(col["name"])
        if comment and not prop.get("description"):
            prop["description"] = comment
        properties.append({k: prop[k] for k in (*PROPERTY_ORDER, *sorted(set(prop) - set(PROPERTY_ORDER)))
                           if k in prop})
    obj = {"name": name, "physicalName": f"{layer}.{name}", "physicalType": actual["physicalType"],
           "logicalType": "object"}
    for k in ("businessName", "description", "dataGranularityDescription", "tags"):
        if old_obj.get(k):
            obj[k] = old_obj[k]
    if "description" not in obj and actual.get("comment"):
        obj["description"] = actual["comment"]
    obj["properties"] = properties
    for k in ("quality", "authoritativeDefinitions", "customProperties"):
        if old_obj.get(k):
            obj[k] = old_obj[k]

    element, sla, check = _sla(name)
    generated = [{"property": "source", "value": source}, {"property": "catalogNote", "value": entry.get("note", "")}]
    if actual.get("views"):
        generated.append({"property": "readByViews", "value": actual["views"]})
    for side, label in (("readers", "consumers"), ("writers", "producers")):
        if scan.get(side):
            generated.append({"property": label, "value": scan[side]})
    if check:
        generated.append({"property": "freshnessCheck", "value": check})
    kept = [c for c in existing.get("customProperties") or [] if c.get("property") not in GENERATED_CUSTOM]

    contract = {
        "apiVersion": mc.API_VERSION, "kind": mc.KIND, "id": f"wealthryse.{layer}.{name}", "name": name,
        "version": existing.get("version", "1.0.0"), "status": existing.get("status", "active"), "domain": layer,
        "dataProduct": existing.get("dataProduct", "portfolio-medallion"),
        "description": existing.get("description") or {"purpose": entry.get("note") or name},
        "tags": existing.get("tags") or [layer, source],
        "schema": [obj],
    }
    if element:
        contract["slaDefaultElement"] = element
        contract["slaProperties"] = sla
    contract["team"] = existing.get("team") or TEAM
    if existing.get("roles"):
        contract["roles"] = existing["roles"]
    contract["customProperties"] = generated + kept
    contract["contractCreatedTs"] = existing.get("contractCreatedTs", CREATED)
    return contract


# ---------------------------------------------------------------- commands


def _structure(args) -> dict[str, dict | None]:
    import psycopg

    catalog = mc.catalog_objects(args.catalog)
    if args.dsn:
        with psycopg.connect(args.dsn) as connection:
            return read_structure(connection, catalog)
    admin = os.environ.get(ADMIN_ENV, "")
    if not admin:
        raise SystemExit(f"Give --dsn, or set {ADMIN_ENV} to a superuser DSN on a disposable server.")
    database = f"contracts_{uuid.uuid4().hex[:10]}"
    try:
        build(admin, database, args.migrations)
        with psycopg.connect(**_params(admin, database, OWNER, TENANTS[-1])) as connection:
            return read_structure(connection, catalog)
    finally:
        drop(admin, database)


def cmd_validate(args) -> int:
    catalog = mc.catalog_objects(args.catalog)
    problems = []
    for name, entry in sorted(catalog.items()):
        path = mc.contract_path(args.contracts, entry["layer"], name)
        if not path.exists():
            problems.append(f"{path.relative_to(ROOT)}: missing")
            continue
        problems += [f"{path.relative_to(ROOT)}: {e}" for e in mc.validate(mc.load(path))]
    print("\n".join(problems) or f"{len(catalog)} contracts are well formed.")
    return 1 if problems else 0


def cmd_check(args) -> int:
    catalog = mc.catalog_objects(args.catalog)
    structure = _structure(args)
    diffs = []
    for name, entry in sorted(catalog.items()):
        path = mc.contract_path(args.contracts, entry["layer"], name)
        if not path.exists():
            print(f"{path.relative_to(ROOT)}: missing; run generate")
            diffs.append(mc.Difference(mc.MAJOR, name, "no contract"))
            continue
        diffs += mc.compare(mc.load(path), structure[name])
    print(mc.advice(diffs) or f"{len(catalog)} contracts match the schema.")
    return 1 if diffs else 0


def cmd_generate(args) -> int:
    catalog = mc.catalog_objects(args.catalog)
    structure = _structure(args)
    scans = scan_code(sorted(catalog), args.code)
    written, missing = 0, []
    for name, entry in sorted(catalog.items()):
        actual = structure[name]
        if actual is None:
            missing.append(name)
            continue
        path = mc.contract_path(args.contracts, entry["layer"], name)
        existing = mc.load(path) if path.exists() else None
        if existing:
            for diff in mc.compare(existing, actual):
                print(f"changed: {diff}")
        contract = merge(name, entry, actual, existing, scans[name])
        text = mc.dump(contract)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            written += 1
    print(f"{written} contract files written, {len(catalog) - written - len(missing)} unchanged.")
    if missing:
        print("Not in the database: " + ", ".join(missing))
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("validate", "check", "generate"))
    parser.add_argument("--dsn", help="an already-migrated database to read (read-only)")
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--contracts", type=Path, default=CONTRACTS)
    parser.add_argument("--migrations", type=Path, default=MIGRATIONS)
    parser.add_argument("--code", type=Path, default=CODE)
    args = parser.parse_args(argv)
    return {"validate": cmd_validate, "check": cmd_check, "generate": cmd_generate}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
