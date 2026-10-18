"""The research zone stays out of the served data path.

The research layer in infra/medallion/catalog.json holds experiments: a point-in-time
feature store, backtests, an experiment log and model training. It lives on its own
server and in the research-* containers. Nothing in the website, the agents or gold may
read it; a research result reaches gold only as a certified figure, through the publish
gate. These tests fail when code outside the research package refers to a research
object, or when the live server's migrations create one.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "infra" / "medallion" / "catalog.json"
# The only code allowed to name research objects. Neither exists yet; both are where
# the research job's code will live.
RESEARCH_CODE = (ROOT / "src" / "ibkr_etl" / "research", ROOT / "scripts" / "research")
SCHEMA_REFERENCE = re.compile(r"\bresearch\.[a-z_][a-z0-9_]*", re.IGNORECASE)


def _catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _research_containers() -> set[str]:
    return {
        key.split("/", 1)[1]
        for key, entry in _catalog()["blob_containers"].items()
        if entry["layer"] == "research"
    }


def _served_code() -> list[Path]:
    """Every Python file outside the research package: website, agents, jobs and scripts."""
    files = []
    for folder in (ROOT / "src", ROOT / "scripts"):
        for path in sorted(folder.rglob("*.py")):
            if any(path.is_relative_to(allowed) for allowed in RESEARCH_CODE):
                continue
            files.append(path)
    return files


class ResearchIsolationTest(unittest.TestCase):
    def test_catalog_has_a_research_layer_and_hop(self):
        catalog = _catalog()
        self.assertIn("research", catalog["layers"])
        self.assertIn("research", catalog["hops"])
        self.assertTrue(_research_containers(), "the research containers are catalogued")

    def test_research_containers_follow_the_naming_rule(self):
        for name in _research_containers():
            self.assertTrue(name.startswith("research-"), name)

    def test_no_research_object_on_the_live_server(self):
        """Research tables live on their own server, so the live catalog has none."""
        for name, entry in _catalog()["postgres"].items():
            with self.subTest(name):
                self.assertNotEqual(entry["layer"], "research")
                self.assertFalse(name.startswith("research."))
        for path in sorted((ROOT / "migrations").rglob("*.sql")):
            with self.subTest(str(path.relative_to(ROOT))):
                self.assertNotRegex(
                    path.read_text(encoding="utf-8"),
                    r"(?i)\b(?:CREATE|ALTER)\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?research\b",
                )

    def test_code_outside_research_never_names_research_objects(self):
        containers = _research_containers()
        container_pattern = re.compile(
            r"\b(?:" + "|".join(re.escape(name) for name in sorted(containers)) + r")\b"
        )
        found = []
        for path in _served_code():
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in (SCHEMA_REFERENCE, container_pattern):
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    found.append(f"{path.relative_to(ROOT)}:{line}: {match.group(0)}")
        self.assertEqual(found, [], "only the research package may refer to research objects")


if __name__ == "__main__":
    unittest.main()
