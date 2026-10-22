"""Month-end packages run inside lineage.run_main, so readiness can see when they finished.

With LINEAGE_RECORDING unset the job behaves exactly as before: the same exit codes and the
same output, and no lineage write. Synthetic data only; no database."""
from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import db, lineage, onboarding_agents, onboarding_month_end

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_registry_month_end_packages.py"
JOB = "ibkrv2stg-month-end-factsheet"
WRAPPED = f'raise SystemExit(lineage.run_main("{JOB}", main))'
PLAIN = "raise SystemExit(main())"
FAILING = "SYN-MONTH-END-2"


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    """Answers the job's two queries: subjects missing a registration, then registrations."""

    def __init__(self, missing, registered):
        self._answers = [missing, registered]

    def execute(self, sql, args=()):
        return _Rows(self._answers.pop(0))

    def close(self):
        return None


class _Writes:
    """Stands in for lineage._execute and keeps each event."""

    def __init__(self):
        self.events = []

    def __call__(self, database_url, tenant_id, statements):
        for sql, args in statements:
            if "lineage_events" in sql:
                self.events.append((args[2], args[5], json.loads(args[9])))


class MonthEndLineageTests(unittest.TestCase):
    def setUp(self):
        lineage.reset()
        self.environment = patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://month-end@127.0.0.1:1/unused",
            "PORTFOLIO_TENANT_ID": "00000000-0000-0000-0000-000000000001",
        })
        self.environment.start()
        for name in ("LINEAGE_RECORDING", "INPUT_READINESS_MODE", "ONBOARDING_AGENTS_ENABLED",
                     "CONTAINER_APP_JOB_EXECUTION_NAME", "CONTAINER_APP_JOB_NAME"):
            os.environ.pop(name, None)
        self.path = list(sys.path)

    def tearDown(self):
        sys.path[:] = self.path
        self.environment.stop()
        lineage.reset()
        self.assertEqual(lineage._CURRENT, [])

    def _source(self, last_line: str) -> str:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(text.rstrip().splitlines()[-1].strip(), WRAPPED)
        return text.rstrip()[: -len(WRAPPED)] + last_line + "\n"

    def _run(self, source: str, *, missing=(), registered=(), live=()):
        """Run the script as __main__ with fakes; returns (exit code, stdout, generator calls)."""
        calls = []

        def generator(args, **kwargs):
            calls.append((Path(args[-1]).name, kwargs["env"].get("PILOT_ACCOUNT_ID")))
            if kwargs["env"].get("PILOT_ACCOUNT_ID") == FAILING:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0)

        connection = _Connection([{"subject_id": s} for s in missing],
                                 [{"subject_id": s, "history_start_date": "2026-01-01"} for s in registered])
        cases = [{"target_subject_id": s} for s in live]
        output = io.StringIO()
        namespace = {"__name__": "__main__", "__file__": str(SCRIPT)}
        with patch.object(db, "connect", return_value=connection), \
                patch.object(onboarding_month_end, "live_cases", return_value=cases), \
                patch.object(onboarding_agents, "agents_enabled", return_value=False), \
                patch.object(subprocess, "run", side_effect=generator), redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                exec(compile(source, str(SCRIPT), "exec"), namespace)
        return raised.exception.code, output.getvalue(), calls

    def test_the_job_runs_inside_run_main_under_its_catalog_name(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        guard = tree.body[-1]
        self.assertIsInstance(guard, ast.If)
        self.assertEqual(ast.unparse(guard.test), "__name__ == '__main__'")
        self.assertEqual([ast.unparse(statement) for statement in guard.body],
                         [f"raise SystemExit(lineage.run_main('{JOB}', main))"])
        catalog = json.loads((ROOT / "infra/medallion/catalog.json").read_text(encoding="utf-8"))
        self.assertIn(JOB, catalog["jobs"])
        bicep = (ROOT / "infra/staging-month-end-factsheet-job.bicep").read_text(encoding="utf-8")
        self.assertIn(f"param jobName string = '{JOB}'", bicep)
        self.assertIn("'scripts/run_registry_month_end_packages.py'", bicep)

    def test_without_lineage_recording_exit_codes_and_output_are_unchanged(self):
        scenarios = {
            "success": {"registered": ("SYN-MONTH-END-1", "SYN-MONTH-END-3")},
            "partial failure": {"registered": ("SYN-MONTH-END-1", FAILING)},
            "missing registration": {"missing": ("SYN-MONTH-END-4",), "registered": ("SYN-MONTH-END-1",)},
            "live onboarding subject": {"registered": ("SYN-MONTH-END-1", "SYN-MONTH-END-5"),
                                        "live": ("SYN-MONTH-END-5",)},
        }
        expected_codes = {"success": 0, "partial failure": 1, "missing registration": 1,
                          "live onboarding subject": 0}
        writes = _Writes()
        with patch.object(lineage, "_execute", writes):
            for name, scenario in scenarios.items():
                with self.subTest(scenario=name):
                    before = self._run(self._source(PLAIN), **scenario)
                    after = self._run(self._source(WRAPPED), **scenario)
                    self.assertEqual(after, before)
                    self.assertEqual(after[0], expected_codes[name])
                    self.assertIn('"status": "', after[1])
        self.assertEqual(writes.events, [])

    def test_with_lineage_recording_a_partial_failure_is_a_finished_run(self):
        writes = _Writes()
        with patch.dict(os.environ, {"LINEAGE_RECORDING": "true"}), patch.object(lineage, "_execute", writes):
            code, _, _ = self._run(self._source(WRAPPED), registered=("SYN-MONTH-END-1", FAILING))
        self.assertEqual(code, 1)
        self.assertEqual([(event, job) for event, job, _ in writes.events], [("START", JOB), ("FAIL", JOB)])
        facets = writes.events[-1][2]
        self.assertEqual(facets.get("exit_code"), 1)
        self.assertNotIn("error", facets)

    def test_with_lineage_recording_a_readiness_stop_is_a_failed_run(self):
        writes = _Writes()
        with patch.dict(os.environ, {"LINEAGE_RECORDING": "true"}), patch.object(lineage, "_execute", writes), \
                patch("ibkr_etl.input_readiness.gate", side_effect=SystemExit(75)):
            code, output, calls = self._run(self._source(WRAPPED), registered=("SYN-MONTH-END-1",))
        self.assertEqual((code, output, calls), (75, "", []))
        self.assertEqual(writes.events[-1][0], "FAIL")
        self.assertEqual(writes.events[-1][2]["error"], {"type": "SystemExit"})


if __name__ == "__main__":
    unittest.main()
