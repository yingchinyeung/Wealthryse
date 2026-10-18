"""Data contracts for the silver and gold tables (ODCS v3 YAML in infra/medallion/contracts).

A contract says what a table promises its readers: its key, each column's type, whether
it may be null, what the values mean, quality rules and a freshness promise. This module:

- loads contracts and checks each one against the ODCS v3 shape (`validate`), offline;
- reads a table's real structure from PostgreSQL (`introspect`);
- compares the two (`compare`) and says whether the contract's version must rise by a
  major step (a column removed or renamed, a type changed, nullability relaxed, a key
  changed) or a minor one (a column added, nullability tightened).

Nothing here runs the quality rules. The later runtime checks and publish gate read the
same files. Paths are parameters because the installed package has no infra/ folder.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

API_VERSION = "v3.0.2"
KIND = "DataContract"
LAYERS = ("silver", "gold")
STATUSES = frozenset({"proposed", "draft", "active", "deprecated", "retired"})
LOGICAL_TYPES = frozenset({"string", "date", "number", "integer", "object", "array", "boolean"})
QUALITY_TYPES = frozenset({"text", "library", "sql", "custom"})
SEVERITIES = frozenset({"error", "warning"})  # this repository uses only these two
DIMENSIONS = frozenset({"accuracy", "completeness", "conformity", "consistency", "coverage",
                        "timeliness", "uniqueness"})
COMPARATORS = frozenset({"mustBe", "mustNotBe", "mustBeGreaterThan", "mustBeGreaterOrEqualTo",
                         "mustBeLessThan", "mustBeLessOrEqualTo", "mustBeBetween", "mustNotBeBetween"})
CLASSIFICATIONS = frozenset({"public", "internal", "confidential", "confidential-financial", "restricted"})
SLA_PROPERTIES = frozenset({"latency", "timeOfAvailability", "frequency", "retention", "generalAvailability",
                            "endOfSupport", "endOfLife", "throughput", "errorRate"})
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

TOP_LEVEL_KEYS = frozenset({
    "apiVersion", "kind", "id", "name", "version", "status", "tenant", "tags", "domain", "dataProduct",
    "description", "servers", "schema", "support", "price", "team", "roles", "slaDefaultElement",
    "slaProperties", "authoritativeDefinitions", "customProperties", "contractCreatedTs"})
DESCRIPTION_KEYS = frozenset({"purpose", "usage", "limitations", "authoritativeDefinitions", "customProperties"})
OBJECT_KEYS = frozenset({
    "name", "physicalName", "physicalType", "logicalType", "description", "businessName",
    "dataGranularityDescription", "tags", "properties", "quality", "authoritativeDefinitions",
    "customProperties"})
PROPERTY_KEYS = frozenset({
    "name", "physicalName", "physicalType", "logicalType", "logicalTypeOptions", "required", "unique",
    "primaryKey", "primaryKeyPosition", "partitioned", "partitionKeyPosition", "classification",
    "description", "businessName", "criticalDataElement", "encryptedName", "transformSourceObjects",
    "transformLogic", "transformDescription", "examples", "tags", "quality", "authoritativeDefinitions",
    "customProperties", "items", "properties"})
QUALITY_KEYS = frozenset({
    "name", "type", "description", "rule", "query", "engine", "implementation", "dimension", "method",
    "severity", "businessImpact", "unit", "validValues", "schedule", "scheduler", "tags", "arguments",
    "authoritativeDefinitions", "customProperties", "code", "toolRuleName", *COMPARATORS})
SLA_KEYS = frozenset({"property", "value", "valueExt", "unit", "element", "driver"})
TEAM_KEYS = frozenset({"username", "name", "role", "description", "dateIn", "dateOut", "replacedByUsername"})
ROLE_KEYS = frozenset({"role", "access", "description", "firstLevelApprovers", "secondLevelApprovers",
                       "customProperties"})
CUSTOM_KEYS = frozenset({"property", "value"})
MAJOR, MINOR = "major", "minor"


@dataclass(frozen=True)
class Difference:
    level: str  # MAJOR or MINOR
    table: str
    message: str

    def __str__(self) -> str:
        return f"{self.table}: {self.message} ({self.level})"


# ---------------------------------------------------------------- files


def catalog_objects(catalog_path: Path) -> dict[str, dict]:
    """name -> catalog entry for every silver and gold object."""
    entries = json.loads(Path(catalog_path).read_text(encoding="utf-8"))["postgres"]
    return {key.split(".", 1)[1]: value for key, value in entries.items()
            if key.startswith("public.") and value.get("layer") in LAYERS}


def contract_path(contracts_dir: Path, layer: str, name: str) -> Path:
    return Path(contracts_dir) / layer / f"{name}.yaml"


def load(path: Path) -> dict:
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a mapping")
    return data


def load_all(contracts_dir: Path) -> dict[Path, dict]:
    return {path: load(path) for layer in LAYERS
            for path in sorted((Path(contracts_dir) / layer).glob("*.yaml"))}


def dump(contract: Mapping) -> str:
    import yaml

    return yaml.safe_dump(dict(contract), sort_keys=False, allow_unicode=True, width=110,
                          default_flow_style=False)


def table_object(contract: Mapping) -> dict:
    """The single schema object a contract here describes."""
    return (contract.get("schema") or [{}])[0]


def custom(items: Iterable[Mapping] | None, name: str, default: Any = None) -> Any:
    for item in items or ():
        if item.get("property") == name:
            return item.get("value")
    return default


# ---------------------------------------------------------------- shape


def _keys(where: str, value: Any, allowed: frozenset, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{where}: must be a mapping")
        return False
    unknown = sorted(set(value) - allowed)
    if unknown:
        errors.append(f"{where}: unknown keys {', '.join(unknown)}")
    return True


def _text(where: str, value: Any, errors: list[str], *, required: bool = True) -> None:
    if value is None:
        if required:
            errors.append(f"{where}: missing")
    elif not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: must be a non-empty string")


def _list(where: str, value: Any, errors: list[str]) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{where}: must be a list")
        return []
    return value


def _custom_properties(where: str, value: Any, errors: list[str]) -> None:
    for i, item in enumerate(_list(where, value, errors)):
        if _keys(f"{where}[{i}]", item, CUSTOM_KEYS, errors):
            _text(f"{where}[{i}].property", item.get("property"), errors)
            if "value" not in item:
                errors.append(f"{where}[{i}].value: missing")


def _quality(where: str, value: Any, errors: list[str]) -> None:
    for i, rule in enumerate(_list(where, value, errors)):
        here = f"{where}[{i}]"
        if not _keys(here, rule, QUALITY_KEYS, errors):
            continue
        kind = rule.get("type", "library")
        if kind not in QUALITY_TYPES:
            errors.append(f"{here}.type: {kind!r} is not one of {sorted(QUALITY_TYPES)}")
        _text(f"{here}.name", rule.get("name"), errors)
        _text(f"{here}.description", rule.get("description"), errors)
        if rule.get("severity") not in SEVERITIES:
            errors.append(f"{here}.severity: must be error or warning")
        if rule.get("dimension") is not None and rule["dimension"] not in DIMENSIONS:
            errors.append(f"{here}.dimension: {rule['dimension']!r} is not an ODCS dimension")
        comparators = [key for key in COMPARATORS if key in rule]
        if kind == "sql":
            _text(f"{here}.query", rule.get("query"), errors)
        if kind == "library":
            _text(f"{here}.rule", rule.get("rule"), errors)
        if kind in {"sql", "library"} and len(comparators) != 1:
            errors.append(f"{here}: needs exactly one of {sorted(COMPARATORS)}")
        for key in comparators:
            bound = rule[key]
            if key.endswith("Between"):
                if not (isinstance(bound, list) and len(bound) == 2):
                    errors.append(f"{here}.{key}: must be a two-item list")
            elif not isinstance(bound, (int, float)) or isinstance(bound, bool):
                errors.append(f"{here}.{key}: must be a number")
        _custom_properties(f"{here}.customProperties", rule.get("customProperties"), errors)


def validate(contract: Any) -> list[str]:
    """Structural problems against the ODCS v3 shape this repository uses. Empty when well formed."""
    errors: list[str] = []
    if not _keys("contract", contract, TOP_LEVEL_KEYS, errors):
        return errors
    if contract.get("apiVersion") != API_VERSION:
        errors.append(f"apiVersion: must be {API_VERSION}")
    if contract.get("kind") != KIND:
        errors.append(f"kind: must be {KIND}")
    for key in ("id", "name", "domain"):
        _text(key, contract.get(key), errors)
    if not SEMVER.match(str(contract.get("version", ""))):
        errors.append("version: must be MAJOR.MINOR.PATCH")
    if contract.get("status") not in STATUSES:
        errors.append(f"status: must be one of {sorted(STATUSES)}")
    if contract.get("domain") not in LAYERS:
        errors.append(f"domain: must be one of {LAYERS}")
    description = contract.get("description")
    if _keys("description", description, DESCRIPTION_KEYS, errors):
        _text("description.purpose", description.get("purpose"), errors)
    for i, tag in enumerate(_list("tags", contract.get("tags"), errors)):
        _text(f"tags[{i}]", tag, errors)
    schema = _list("schema", contract.get("schema"), errors)
    if len(schema) != 1:
        errors.append("schema: must hold exactly one object (one contract per table)")
    for i, obj in enumerate(schema):
        here = f"schema[{i}]"
        if not _keys(here, obj, OBJECT_KEYS, errors):
            continue
        _text(f"{here}.name", obj.get("name"), errors)
        physical = obj.get("physicalName")
        _text(f"{here}.physicalName", physical, errors)
        if isinstance(physical, str) and physical.split(".", 1)[0] != contract.get("domain"):
            errors.append(f"{here}.physicalName: must start with the contract's layer schema")
        if obj.get("physicalType") not in {"table", "view"}:
            errors.append(f"{here}.physicalType: must be table or view")
        if obj.get("logicalType") != "object":
            errors.append(f"{here}.logicalType: must be object")
        _text(f"{here}.description", obj.get("description"), errors, required=False)
        properties = _list(f"{here}.properties", obj.get("properties"), errors)
        if not properties:
            errors.append(f"{here}.properties: must list the columns")
        seen, keys = set(), []
        for j, prop in enumerate(properties):
            where = f"{here}.properties[{j}]"
            if not _keys(where, prop, PROPERTY_KEYS, errors):
                continue
            name = prop.get("name")
            _text(f"{where}.name", name, errors)
            if name in seen:
                errors.append(f"{where}.name: {name!r} appears twice")
            seen.add(name)
            if prop.get("logicalType") not in LOGICAL_TYPES:
                errors.append(f"{where}.logicalType: must be one of {sorted(LOGICAL_TYPES)}")
            _text(f"{where}.physicalType", prop.get("physicalType"), errors)
            for flag in ("required", "unique", "primaryKey"):
                if not isinstance(prop.get(flag), bool):
                    errors.append(f"{where}.{flag}: must be true or false")
            position = prop.get("primaryKeyPosition")
            if not isinstance(position, int) or isinstance(position, bool):
                errors.append(f"{where}.primaryKeyPosition: must be an integer (-1 when not in the key)")
            elif prop.get("primaryKey"):
                keys.append(position)
                if prop.get("required") is not True:
                    errors.append(f"{where}: a primary key column must be required")
            elif position != -1:
                errors.append(f"{where}.primaryKeyPosition: must be -1 when not in the key")
            if prop.get("classification") not in CLASSIFICATIONS:
                errors.append(f"{where}.classification: must be one of {sorted(CLASSIFICATIONS)}")
            _text(f"{where}.description", prop.get("description"), errors, required=False)
            _quality(f"{where}.quality", prop.get("quality"), errors)
            _custom_properties(f"{where}.customProperties", prop.get("customProperties"), errors)
        if sorted(keys) != list(range(1, len(keys) + 1)):
            errors.append(f"{here}: primaryKeyPosition values must run 1..n")
        _quality(f"{here}.quality", obj.get("quality"), errors)
        _custom_properties(f"{here}.customProperties", obj.get("customProperties"), errors)
    for i, sla in enumerate(_list("slaProperties", contract.get("slaProperties"), errors)):
        here = f"slaProperties[{i}]"
        if _keys(here, sla, SLA_KEYS, errors):
            if sla.get("property") not in SLA_PROPERTIES:
                errors.append(f"{here}.property: {sla.get('property')!r} is not an ODCS SLA property")
            if "value" not in sla:
                errors.append(f"{here}.value: missing")
    team = _list("team", contract.get("team"), errors)
    if not team:
        errors.append("team: must name the owning team")
    for i, member in enumerate(team):
        if _keys(f"team[{i}]", member, TEAM_KEYS, errors):
            _text(f"team[{i}].username", member.get("username"), errors)
            _text(f"team[{i}].role", member.get("role"), errors)
    for i, role in enumerate(_list("roles", contract.get("roles"), errors)):
        if _keys(f"roles[{i}]", role, ROLE_KEYS, errors):
            _text(f"roles[{i}].role", role.get("role"), errors)
            if role.get("access") not in {"read", "write"}:
                errors.append(f"roles[{i}].access: must be read or write")
    _custom_properties("customProperties", contract.get("customProperties"), errors)
    if "@" in json.dumps(contract, default=str):
        errors.append("contract: names teams and roles, never email addresses")
    return errors


# ---------------------------------------------------------------- structure


def logical_type(physical: str) -> str:
    """ODCS v3.0 logical type for a PostgreSQL format_type() string."""
    base = physical.lower()
    if base.endswith("[]"):
        return "array"
    if base in {"smallint", "integer", "bigint"}:
        return "integer"
    if base.startswith(("numeric", "double precision", "real")):
        return "number"
    if base == "boolean":
        return "boolean"
    if base == "date" or base.startswith(("timestamp", "time ")):
        return "date"
    if base in {"json", "jsonb"}:
        return "object"
    return "string"


def introspect(connection, physical_name: str) -> dict | None:
    """Kind, columns, primary key and single-column unique columns of schema.name, or None."""
    schema, name = physical_name.split(".", 1)
    row = connection.execute(
        "SELECT c.oid, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s", (schema, name)).fetchone()
    if row is None:
        return None
    oid, relkind = row[0], row[1]
    columns = [
        {"name": col[0], "physicalType": col[1], "required": bool(col[2])}
        for col in connection.execute(
            "SELECT attname, format_type(atttypid, atttypmod), attnotnull FROM pg_attribute "
            "WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped ORDER BY attnum", (oid,)).fetchall()]
    key = [r[0] for r in connection.execute(
        "SELECT a.attname FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, n) "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        "WHERE c.conrelid = %s AND c.contype = 'p' ORDER BY k.n", (oid,)).fetchall()]
    # Unique in its own right: a one-column unique constraint or plain unique index.
    unique = {r[0] for r in connection.execute(
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
        "AND a.attnum = i.indkey[0] WHERE i.indrelid = %s AND i.indisunique AND NOT i.indisprimary "
        "AND i.indnatts = 1 AND i.indpred IS NULL AND i.indexprs IS NULL", (oid,)).fetchall()}
    if len(key) == 1:
        unique.add(key[0])
    checks = [r[0] for r in connection.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s AND contype = 'c' "
        "ORDER BY conname", (oid,)).fetchall()]
    composite_unique = [list(r[0]) for r in connection.execute(
        "SELECT array_agg(a.attname ORDER BY k.n) FROM pg_index i "
        "CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, n) "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
        "WHERE i.indrelid = %s AND i.indisunique AND NOT i.indisprimary AND i.indnatts > 1 "
        "AND i.indpred IS NULL AND i.indexprs IS NULL GROUP BY i.indexrelid ORDER BY 1", (oid,)).fetchall()]
    return {"physicalType": "view" if relkind in ("v", "m") else "table", "columns": columns,
            "primaryKey": key, "unique": sorted(unique), "checks": checks, "compositeUnique": composite_unique}


def declared(contract: Mapping) -> dict:
    """The same shape as introspect(), read from a contract."""
    obj = table_object(contract)
    props = obj.get("properties") or []
    key = sorted((p for p in props if p.get("primaryKey")), key=lambda p: p.get("primaryKeyPosition", 0))
    return {"physicalType": obj.get("physicalType"),
            "columns": [{"name": p.get("name"), "physicalType": p.get("physicalType"),
                         "required": bool(p.get("required"))} for p in props],
            "primaryKey": [p.get("name") for p in key],
            "unique": sorted(p.get("name") for p in props if p.get("unique"))}


def _type_change_level(old: str, new: str) -> str:
    """Widening a varchar or numeric is minor; anything else is major."""
    pattern = re.compile(r"^(character varying|numeric)\((\d+)(?:,(\d+))?\)$")
    a, b = pattern.match(old), pattern.match(new)
    if a and b and a.group(1) == b.group(1):
        if int(b.group(2)) >= int(a.group(2)) and int(b.group(3) or 0) >= int(a.group(3) or 0):
            return MINOR
    if a and a.group(1) == "character varying" and new == "text":
        return MINOR
    return MAJOR


def compare(contract: Mapping, actual: Mapping | None) -> list[Difference]:
    """Differences between what a contract declares and the live structure, each major or minor."""
    obj = table_object(contract)
    table = obj.get("physicalName") or contract.get("name") or "?"
    if actual is None:
        return [Difference(MAJOR, table, "missing from the database")]
    want, diffs = declared(contract), []
    if want["physicalType"] != actual["physicalType"]:
        diffs.append(Difference(MAJOR, table, f"is a {actual['physicalType']}, the contract says "
                                              f"{want['physicalType']}"))
    have = {c["name"]: c for c in actual["columns"]}
    for col in want["columns"]:
        live = have.get(col["name"])
        if live is None:
            diffs.append(Difference(MAJOR, table, f"column {col['name']} is in the contract but not the database "
                                                  "(removed or renamed)"))
            continue
        if live["physicalType"] != col["physicalType"]:
            diffs.append(Difference(_type_change_level(col["physicalType"], live["physicalType"]), table,
                                    f"column {col['name']} is {live['physicalType']}, the contract says "
                                    f"{col['physicalType']}"))
        if live["required"] != col["required"]:
            level = MAJOR if col["required"] else MINOR
            state = "allows nulls" if col["required"] else "is NOT NULL"
            diffs.append(Difference(level, table, f"column {col['name']} now {state}, the contract says "
                                                  f"required: {str(col['required']).lower()}"))
    named = {c["name"] for c in want["columns"]}
    for col in actual["columns"]:
        if col["name"] not in named:
            diffs.append(Difference(MINOR, table, f"column {col['name']} ({col['physicalType']}) is in the "
                                                  "database but not the contract (added)"))
    if want["primaryKey"] != actual["primaryKey"]:
        diffs.append(Difference(MAJOR, table, f"primary key is ({', '.join(actual['primaryKey'])}), the contract "
                                              f"says ({', '.join(want['primaryKey'])})"))
    for name in sorted(set(want["unique"]) - set(actual["unique"])):
        diffs.append(Difference(MAJOR, table, f"column {name} is no longer unique"))
    for name in sorted(set(actual["unique"]) - set(want["unique"])):
        if name in named:
            diffs.append(Difference(MINOR, table, f"column {name} is now unique"))
    return diffs


def advice(diffs: Iterable[Difference]) -> str:
    """What to do about a set of differences, for a test failure message."""
    diffs = list(diffs)
    if not diffs:
        return ""
    step = "MAJOR" if any(d.level == MAJOR for d in diffs) else "MINOR"
    lines = [str(d) for d in diffs]
    return ("The schema no longer matches its data contract:\n  " + "\n  ".join(lines) +
            f"\nUpdate the contract under infra/medallion/contracts/ and raise its version by a {step} step "
            "(major for a removed or renamed column, a type change, a column that now allows nulls or a key "
            "change; minor for an added column or a tightened rule). `python scripts/medallion_contracts.py "
            "generate` rewrites the structural part and keeps the hand-written descriptions and rules.")
