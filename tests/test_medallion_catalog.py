"""The medallion catalog must name a layer for every table, view and blob container.

infra/medallion/catalog.json is the single list of which layer (bronze, silver,
gold, ops) each database object, blob container and job belongs to. These tests
fail when a migration adds a table or view, or Bicep adds a container, without
an entry, so the layering cannot quietly drift. See docs/MEDALLION_ARCHITECTURE.md.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "infra" / "medallion" / "catalog.json"
MIGRATIONS = ROOT / "migrations"

CREATE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNLOGGED\s+)?(TABLE|VIEW|MATERIALIZED\s+VIEW)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w.]*)",
    re.IGNORECASE,
)
# Created by src/ibkr_etl/db.py at runtime, not by a migration file.
RUNTIME_OBJECTS = {"public.schema_migrations"}
TARGET_SCHEMA = {"bronze": "bronze", "silver": "silver", "gold": "gold", "ops": "ops", "platform": "public"}


def _catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _migration_objects() -> dict[str, str]:
    """Qualified name -> 'table' or 'view' for everything the migrations create."""
    found = {}
    for folder, schema in (("postgres", "public"), ("agent", "agent")):
        for path in sorted((MIGRATIONS / folder).glob("*.sql")):
            for kind, name in CREATE.findall(path.read_text(encoding="utf-8")):
                name = name.lower()
                if "." not in name:
                    name = f"{schema}.{name}"
                found.setdefault(name, "view" if "VIEW" in kind.upper() else "table")
    return found


def _bicep_containers() -> set[str]:
    """Blob container names that Bicep creates (not 'existing' references)."""
    names = set()
    for path in sorted((ROOT / "infra").glob("*.bicep")):
        text = path.read_text(encoding="utf-8")
        params = dict(re.findall(r"param\s+(\w+)\s+string\s*=\s*'([^']+)'", text))
        for match in re.finditer(r"blobServices/containers@[\w-]+'\s*=\s*(\[for\s+\w+\s+in\s+\[([^\]]*)\])?", text):
            if match.group(2) is not None:
                names.update(re.findall(r"'([^']+)'", match.group(2)))
                continue
            block = text[match.end():match.end() + 400]
            name = re.search(r"\bname:\s*('([^']+)'|(\w+))", block)
            if name and name.group(2):
                names.add(name.group(2))
            elif name and name.group(3) in params:
                names.add(params[name.group(3)])
    return names


class MedallionCatalogTest(unittest.TestCase):
    def test_every_table_and_view_has_a_layer(self):
        catalog = _catalog()["postgres"]
        missing = sorted(set(_migration_objects()) - set(catalog))
        self.assertEqual(missing, [], "add these to infra/medallion/catalog.json with a layer")

    def test_catalog_has_no_stale_entries(self):
        catalog = _catalog()["postgres"]
        stale = sorted(set(catalog) - set(_migration_objects()) - RUNTIME_OBJECTS)
        self.assertEqual(stale, [], "these are in the catalog but no migration creates them")

    def test_kinds_match_the_migrations(self):
        catalog = _catalog()["postgres"]
        for name, kind in _migration_objects().items():
            self.assertEqual(catalog[name]["kind"], kind, name)

    def test_layers_and_target_schemas_are_consistent(self):
        catalog = _catalog()
        layers = set(catalog["layers"])
        for name, entry in catalog["postgres"].items():
            schema = name.split(".", 1)[0]
            if schema == "agent":
                # The agent schema stays as it is: agent state plus facades over silver and gold.
                self.assertIn(entry["layer"], {"ops", "consumer"}, name)
                self.assertEqual(entry["target_schema"], "agent", name)
                continue
            self.assertIn(entry["layer"], layers, name)
            self.assertEqual(entry["target_schema"], TARGET_SCHEMA[entry["layer"]], name)

    def test_every_bicep_container_has_a_layer(self):
        catalog = _catalog()
        layers = set(catalog["layers"])
        known = {key.split("/", 1)[1] for key in catalog["blob_containers"]}
        for entry in catalog["blob_containers"].values():
            self.assertIn(entry["layer"], layers)
        declared = _bicep_containers()
        self.assertTrue({"raw-statements", "internal-reports", "onboarding-intake"} <= declared, declared)
        self.assertEqual(sorted(declared - known), [], "add these containers to the catalog")

    def test_jobs_name_known_hops(self):
        catalog = _catalog()
        hops = set(catalog["hops"])
        for name, entry in catalog["jobs"].items():
            self.assertTrue(entry["hops"], name)
            self.assertLessEqual(set(entry["hops"]), hops, name)


if __name__ == "__main__":
    unittest.main()
