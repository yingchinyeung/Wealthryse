"""bootstrap_postgres.py tries a layer batch again when one of its tables is busy."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _bootstrap():
    spec = importlib.util.spec_from_file_location("bootstrap_postgres", ROOT / "scripts" / "bootstrap_postgres.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BusyRetryTests(unittest.TestCase):
    def setUp(self):
        import psycopg

        self.busy = psycopg.errors.LockNotAvailable("the gold batch could not lock its tables within 5 seconds")
        self.bootstrap = _bootstrap()
        self.pauses = []
        patcher = patch.object(self.bootstrap.time, "sleep", self.pauses.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, outcomes, only_migration="068_medallion_gold_layer.sql"):
        calls = []

        def apply():
            calls.append(1)
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with contextlib.redirect_stdout(io.StringIO()) as output:
            try:
                result = self.bootstrap._when_tables_free(apply, only_migration)
            except Exception as error:
                result = error
        return result, len(calls), output.getvalue()

    def test_a_busy_batch_is_tried_again_a_minute_later(self):
        result, calls, output = self._run([self.busy, self.busy, ["068_medallion_gold_layer.sql"]])
        self.assertEqual(result, ["068_medallion_gold_layer.sql"])
        self.assertEqual(calls, 3)
        self.assertEqual(self.pauses, [60, 60])
        self.assertIn("try 2 of 3", output)

    def test_it_gives_up_after_three_tries(self):
        result, calls, _ = self._run([self.busy] * 4)
        self.assertIs(result, self.busy)
        self.assertEqual(calls, 3)
        self.assertEqual(self.pauses, [60, 60])

    def test_other_errors_and_full_runs_are_not_retried(self):
        failure = RuntimeError("another migration is pending")
        self.assertEqual(self._run([failure])[:2], (failure, 1))
        self.assertEqual(self._run([self.busy], only_migration=None)[:2], (self.busy, 1))
        self.assertEqual(self.pauses, [])


if __name__ == "__main__":
    unittest.main()
