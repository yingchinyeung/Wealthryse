"""scripts/medallion_layers.py check: what may change during a batch window. Synthetic states only."""
from __future__ import annotations

import copy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import medallion_layers as checks  # noqa: E402

TAKEN_AT = "2026-10-20 10:00:00.123456+00"
OLD = "11111111-1111-1111-1111-111111111111"
NEW = "22222222-2222-2222-2222-222222222222"


def _state() -> dict:
    return {
        "taken_at": TAKEN_AT,
        "schema_migrations": [["067_factsheet_design_agents.sql", "x", "2026-10-16"]],
        "objects": {"reporting_snapshots": {"kind": "table", "layer": "gold", "location": "public", "rows": 1}},
        "reporting_snapshots": [[OLD, "frozen", "a" * 64, "b" * 64, "U9100001", "2026-09-30",
                                 "2026-10-01T09:00:00+00:00"]],
        "stage43": {"releases": 0},
        "burn_in": {"latest_nav_date": "2026-10-19", "latest_snapshot": {"snapshot_id": OLD},
                    "ready_report_types": ["factsheet"]},
        "agent": {"subjects": 1, "views": {"month_end_snapshots": 1, "nav": 10}},
    }


def _after(*, created_at="2026-10-20T10:00:05+00:00", supersede=True, subject="U9100001") -> dict:
    state = _state()
    state["schema_migrations"].append(["068_medallion_gold_layer.sql", "y", "2026-10-20"])
    state["objects"]["reporting_snapshots"].update(location="gold", rows=2)
    if supersede:
        state["reporting_snapshots"][0][1] = "superseded"
    state["reporting_snapshots"].append([NEW, "frozen", "c" * 64, "d" * 64, subject, "2026-09-30", created_at])
    state["burn_in"]["latest_snapshot"] = {"snapshot_id": NEW}
    state["burn_in"]["ready_report_types"] = []
    return state


class AppendedSnapshotTests(unittest.TestCase):

    def test_a_snapshot_frozen_during_the_window_is_allowed(self):
        problems, notes = checks.compare(_state(), _after(), "gold")
        self.assertEqual(problems, [])
        self.assertIn("reporting_snapshots: 1 rows created during the batch window", notes)
        self.assertIn(f"reporting_snapshots {OLD} superseded by a snapshot created during the batch window", notes)
        self.assertIn("reporting_snapshots rows 1 -> 2", notes)
        self.assertIn("burn-in latest snapshot is one created during the batch window", notes)

    def test_the_agent_view_may_grow_by_the_new_rows_only(self):
        after = _after(supersede=False, subject="U9100002")
        after["agent"]["views"]["month_end_snapshots"] = 2
        after["burn_in"] = _state()["burn_in"]
        self.assertEqual(checks.compare(_state(), after, "gold")[0], [])
        after["agent"]["views"]["month_end_snapshots"] = 3
        self.assertTrue(any(p.startswith("agent views changed") for p in checks.compare(_state(), after, "gold")[0]))

    def test_a_row_dated_before_the_snapshot_is_refused(self):
        problems, _ = checks.compare(_state(), _after(created_at="2026-10-20T09:59:00+00:00"), "gold")
        self.assertIn(f"reporting_snapshots gained rows created before the snapshot: ['{NEW}']", problems)
        self.assertIn(f"reporting_snapshots row {OLD} changed or disappeared", problems)  # no fresh row for it

    def test_existing_rows_must_keep_their_content(self):
        after = _after()
        after["reporting_snapshots"][0][3] = "e" * 64  # its payload changed
        self.assertIn(f"reporting_snapshots row {OLD} changed or disappeared", checks.compare(_state(), after, "gold")[0])
        after = _after(subject="U9100002")  # superseded without a new row for its own subject
        self.assertIn(f"reporting_snapshots row {OLD} changed or disappeared", checks.compare(_state(), after, "gold")[0])
        after = _after()
        del after["reporting_snapshots"][0]
        self.assertIn(f"reporting_snapshots row {OLD} changed or disappeared", checks.compare(_state(), after, "gold")[0])

    def test_counts_and_other_answers_still_must_match(self):
        after = _after()
        after["objects"]["reporting_snapshots"]["rows"] = 3
        self.assertIn("reporting_snapshots rows 1 -> 3", checks.compare(_state(), after, "gold")[0])
        after = _after()
        after["burn_in"]["latest_nav_date"] = "2026-10-18"
        self.assertTrue(any(p.startswith("burn-in answers changed") for p in checks.compare(_state(), after, "gold")[0]))
        unchanged = _state()
        unchanged["schema_migrations"] = _after()["schema_migrations"]
        unchanged["objects"]["reporting_snapshots"]["location"] = "gold"
        self.assertEqual(checks.compare(_state(), unchanged, "gold"), ([], []))


class BurnInTests(unittest.TestCase):

    def test_the_burn_in_queries_get_the_day_they_answer_for(self):
        calls = []
        module = types.ModuleType("run_burn_in_check")
        module._database_state = lambda url, tenant, account, today_hkt: calls.append(today_hkt) or {"ok": True}
        with patch.dict(sys.modules, {"run_burn_in_check": module}):
            self.assertEqual(checks._burn_in("dsn", "tenant", "U9100001", "2026-10-20"), {"ok": True})
        self.assertEqual(str(calls[0]), "2026-10-20")
        older = types.ModuleType("run_burn_in_check")
        older._database_state = lambda url, tenant, account: {"older": True}
        with patch.dict(sys.modules, {"run_burn_in_check": older}):
            self.assertEqual(checks._burn_in("dsn", "tenant", "U9100001", "2026-10-20"), {"older": True})

    def test_the_check_reuses_the_snapshot_day(self):
        before = copy.deepcopy(_state())
        before["burn_in_day"] = "2026-10-19"
        seen = {}

        def snapshot(conninfo, *, tenant, account, burn_in_day=None):
            seen["day"] = burn_in_day
            raise LookupError("stop here")

        import json
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(before, handle)
        with patch.object(checks, "snapshot", snapshot), patch.object(checks, "_conninfo", lambda env: "dsn"), \
                self.assertRaises(LookupError):
            checks.main(["check", "--before", handle.name, "--account", "U9100001"])
        Path(handle.name).unlink()
        self.assertEqual(seen["day"], "2026-10-19")


if __name__ == "__main__":
    unittest.main()
