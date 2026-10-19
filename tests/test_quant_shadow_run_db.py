"""The shadow-run tool's commands on a real PostgreSQL standing in for the restored copy
(scripts/quant_shadow_run.py, docs/QUANT_ANALYTICS.md "Shadow run on a restored copy (Fri 30 Oct)").

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped
otherwise. Reuses the quant snapshots harness (tests/test_quant_snapshots_db.py): a database with
every migration and today's logins, but not the two held drafts, and two made-up accounts. The
tool is pointed at a copy-looking host name that the guard accepts; its one connection function
is pointed at the local database instead.

In order: preflight reads and writes nothing; run refuses before apply-held; apply-held applies
both drafts once and records them, never in schema_migrations; run builds the latest dates, then
the history, carrying on where a spent budget stopped and retrying a failed section; review
writes counts only, ids masked, its timing maths on known timings, and prints the same files
between its BEGIN and END lines; undo (and a broken or half-applied draft) leaves the database as
it was before, and a second rehearsal runs.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
import test_quant_snapshots_db as qdb
from test_quant_shadow_run import _module
from test_quant_snapshots import synthetic_data
from ibkr_etl import publish_gate as gate
from ibkr_etl import quant_snapshots as qs

shadow = _module()
ADMIN_DSN = qdb.ADMIN_DSN
TENANT, SMALL, LARGE, AS_OF = qdb.TENANT, qdb.SMALL, qdb.LARGE, qdb.AS_OF
COPY = "ibkrv2stg-test-shadow-1.postgres.database.azure.com"
HISTORY_DAYS = "6"  # 12 to 16 Oct: five business days of each account
ACCOUNT_LIKE = re.compile(r"[A-Z]{1,2}\d{5,}")


def setUpModule():
    qdb.setUpModule()  # the held files as their day will number them, for the logins' bootstrap


def _float_paths(value, path=()) -> list[tuple]:
    """The key paths of every fractional number in a decoded review."""
    if isinstance(value, float):
        return [path]
    if isinstance(value, dict):
        return [found for key, item in value.items() for found in _float_paths(item, (*path, key))]
    if isinstance(value, list):
        return [found for index, item in enumerate(value) for found in _float_paths(item, (*path, index))]
    return []


class Clock:
    """The tool's clock: each subject-date built takes a minute, so budgets stop at known places."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class ShadowRunTests(unittest.TestCase):
    """One database through every command, in order (the tests share it)."""

    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {
            "IBKR_RUN_MIGRATIONS": "false", gate.MODE_ENV: "", qs.BUILD_SETTING: "", qs.MINUTES_SETTING: "",
            "PORTFOLIO_TENANT_ID": TENANT, "MEDALLION_DIR": ""})
        cls.environment.start()
        cls.database = None
        try:
            cls.database, _ = qdb._new_database("quant_shadow_test")
            small = synthetic_data(holdings=5, days=300, seed=7)
            large = synthetic_data(holdings=6, days=420, seed=11, trade_every=3)
            cls.accounts = {SMALL: small, LARGE: large}
            for account, data in cls.accounts.items():
                data.update(subject_id=account, benchmark_id=qdb.BENCHMARK)
            for data in cls.accounts.values():
                data["fx"] = qdb._fx_for(cls.accounts)
            with layers._connect(cls.database) as owner:
                qdb._write(owner, cls.accounts)
                cls.before = qdb._snapshot(owner)
        except Exception:
            cls.tearDownClass()
            raise
        cls.local_url = layers._url(cls.database, layers.OWNER)
        # The copy's admin URL as the job gets it: a copy-looking host, the owner's login.
        cls.copy_url = cls.local_url.replace("@localhost:", f"@{COPY}:").replace("@127.0.0.1:", f"@{COPY}:")
        assert COPY in cls.copy_url, cls.copy_url
        cls.output = io.StringIO()  # every line any command printed, for the masking check
        cls.out_dir = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        if cls.database:
            layers._drop(cls.database, roles=qdb.ROLES)
        cls.environment.stop()
        if hasattr(cls, "out_dir"):
            cls.out_dir.cleanup()

    # --- helpers ---

    def _open(self, target, tenant_id):
        """The tool's connection, to the local database once the guard passed the copy's host."""
        from ibkr_etl.db import connect

        self.assertEqual(target.host, COPY)
        return connect(Path("/nonexistent/quant-shadow.sqlite3"), self.local_url, tenant_id)

    def _main(self, *args: str, clock: Clock | None = None) -> tuple[int, str, str]:
        environ = {"DATABASE_ADMIN_URL": self.copy_url, "PORTFOLIO_TENANT_ID": TENANT,
                   qs.MINUTES_SETTING: "8", "LIVE_DATABASE_HOSTS": shadow.LIVE_HOSTS[0]}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(shadow, "_open", self._open))
            if clock is not None:
                real = shadow._build_one

                def ticking(*arguments, **keywords):
                    item = real(*arguments, **keywords)
                    clock.now += 60.0
                    return item

                stack.enter_context(patch.object(shadow, "_now", clock))
                stack.enter_context(patch.object(shadow, "_build_one", ticking))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = shadow.main([args[0], "--copy-host", COPY, *args[1:]], environ=environ)
        self.output.write(out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def _lines(self, text: str) -> list[dict]:
        return [json.loads(line) for line in text.splitlines() if line.startswith("{")]

    def _rows(self, sql: str, *parameters):
        with layers._connect(self.database) as owner:
            return owner.execute(sql, parameters).fetchall()

    def _pairs(self) -> int:
        return self._rows("SELECT count(DISTINCT (subject_id, snapshot_date)) FROM subject_nav_series "
                          "WHERE snapshot_date >= '2026-10-10'")[0][0]

    def _sha(self, name: str) -> str:
        return hashlib.sha256((qdb.DRAFTS / name).read_text(encoding="utf-8").encode("utf-8")).hexdigest()

    # --- in order ---

    def test_1_preflight_reads_the_copy_and_changes_nothing(self):
        code, out, err = self._main("preflight")
        self.assertEqual(code, 0, err)
        self.assertIn(f"Restored copy: host {COPY}, database {self.database} (preflight)", out)
        report = self._lines(out)[-1]
        self.assertEqual(report["copy"], {"host": COPY, "database": self.database})
        self.assertEqual(set(report["objects"].values()), {None})
        self.assertEqual(report["prerequisites"], {"bronze_silver_gold_schemas": True, "lineage_stamp_run": True,
                                                   "live_tables": True})
        self.assertEqual(report["subjects"]["eligible"], 2)
        self.assertEqual(report["subjects"]["latest_nav"],
                         [{"subject": "u01", "subject_type": "physical_account", "latest_nav_date": AS_OF},
                          {"subject": "u02", "subject_type": "physical_account", "latest_nav_date": AS_OF}])
        self.assertEqual(report["subjects"]["by_type"],
                         [{"subject_type": "physical_account", "lifecycle_status": "active", "subjects": 2}])
        for name, value in report["inputs"].items():
            self.assertNotIn("error", value, name)
        self.assertGreater(report["inputs"]["performance_comparison_daily"]["rows"], 0)
        self.assertEqual(report["shadow_ledger_events"], 0)
        with layers._connect(self.database) as owner:
            self.assertEqual(qdb._snapshot(owner), self.before)
            self.assertIsNone(owner.execute("SELECT to_regnamespace('quant_shadow')").fetchone()[0])
        # Nothing to build before the held migrations are on the copy.
        code, _, err = self._main("run")
        self.assertEqual(code, shadow.EXIT_REFUSED)
        self.assertIn("run apply-held first", err)

    def test_2_apply_held_applies_each_draft_once_and_records_it_on_the_copy(self):
        code, out, err = self._main("apply-held")
        self.assertEqual(code, 0, err)
        results = {item["draft"]: item for item in self._lines(out)}
        self.assertEqual({name: item["outcome"] for name, item in results.items()},
                         {"quant_analytics": "applied", "desk_live_views": "applied"})
        self.assertEqual(results["quant_analytics"]["sha256"], self._sha("quant_analytics.sql"))
        self.assertEqual(results["desk_live_views"]["sha256"], self._sha("desk_live_views.sql"))
        # Run again: nothing to do (desk_live_views.sql itself could not run twice).
        code, out, err = self._main("apply-held")
        self.assertEqual(code, 0, err)
        self.assertEqual([item["outcome"] for item in self._lines(out)], ["already applied", "already applied"])
        self.assertEqual(self._rows("SELECT draft, file, action, outcome, sha256 FROM quant_shadow.draft_events "
                                    "ORDER BY event_id"),
                         [("quant_analytics", "quant_analytics.sql", "apply", "applied", self._sha("quant_analytics.sql")),
                          ("desk_live_views", "desk_live_views.sql", "apply", "applied", self._sha("desk_live_views.sql"))])
        for name in qdb.QUANT_OBJECTS + qdb.DESK_OBJECTS:
            self.assertIsNotNone(self._rows("SELECT to_regclass(%s)", name)[0][0], name)
        # Never the migrations ledger: on its day 076 is applied by the release, not by this.
        self.assertEqual(self._rows("SELECT version, sha256 FROM schema_migrations ORDER BY 1"),
                         self.before["migrations"])

    def test_3_run_builds_the_latest_dates_then_the_history_and_carries_on(self):
        pairs = self._pairs()
        self.assertEqual(pairs, 10)
        clock = Clock()
        # A budget of 2.5 minutes: the daily job's pass (its own 8 minutes) builds both latest
        # dates, then the history starts one more subject-date before the budget is spent.
        code, out, err = self._main("run", "--max-minutes", "2.5", "--history-days", HISTORY_DAYS, clock=clock)
        self.assertEqual(code, shadow.EXIT_INCOMPLETE, out + err)
        lines = self._lines(out)
        latest = [line for line in lines if line.get("phase") == "latest" and "subject" in line]
        self.assertEqual([(line["subject"], line["as_of"], line["status"]) for line in latest],
                         [("u02", AS_OF, "rebuilt"), ("u01", AS_OF, "rebuilt")])  # as the job orders them
        summary = next(line for line in lines if line.get("phase") == "latest" and "summary" in line)["summary"]
        self.assertEqual((summary["subject_dates"], summary["rebuilt"], summary["cold"], summary["minutes"]),
                         (2, 2, True, 8.0))
        self.assertEqual(summary["verdict"]["seconds"], 120.0)  # two minutes on the test clock
        self.assertEqual(summary["section_errors"], 0)
        final = lines[-1]
        self.assertEqual((final["status"], final["latest_pass"]), ("incomplete", "run now"))
        self.assertEqual({key: final["history"][key] for key in ("subject_dates", "already_built", "rebuilt", "left")},
                         {"subject_dates": pairs, "already_built": 2, "rebuilt": 1, "left": pairs - 3})
        # The next run carries on: the latest-date pass is not repeated, built dates are skipped.
        # One engine fails on each date it builds now; its error is caught and the run fails.
        def boom(inputs):
            raise RuntimeError(f"boom {SMALL} 0.123456")

        with patch.dict(qs.BUILDERS, {"execution": boom}):
            code, out, err = self._main("run", "--max-minutes", "3", "--history-days", HISTORY_DAYS, clock=clock)
        self.assertEqual(code, shadow.EXIT_FAILED, out + err)
        final = self._lines(out)[-1]
        self.assertEqual((final["status"], final["latest_pass"]), ("failed", "done before"))
        self.assertEqual({key: final["history"][key] for key in ("already_built", "rebuilt", "left", "section_errors")},
                         {"already_built": 3, "rebuilt": 3, "left": pairs - 6, "section_errors": 3})
        self.assertIn("quant section failed subject=u01", err)  # the engine's log line, masked
        # A long budget finishes, rebuilding the failed section where it is missing.
        code, out, err = self._main("run", "--history-days", HISTORY_DAYS, clock=clock)
        self.assertEqual(code, 0, out + err)
        final = self._lines(out)[-1]
        self.assertEqual({key: final["history"][key] for key in ("already_built", "rebuilt", "left", "section_errors")},
                         {"already_built": 3, "rebuilt": pairs - 3, "left": 0, "section_errors": 0})
        self.assertEqual(self._rows("SELECT count(*), count(DISTINCT (subject_id, as_of_date)) "
                                    "FROM gold.quant_analytics_snapshots"), [(9 * pairs, pairs)])
        # And once more: everything is built, so nothing is.
        code, out, err = self._main("run", "--history-days", HISTORY_DAYS, clock=clock)
        self.assertEqual(code, 0, out + err)
        final = self._lines(out)[-1]
        self.assertEqual((final["status"], final["history"]["already_built"], final["history"]["rebuilt"]),
                         ("complete", pairs, 0))
        self.assertEqual(len([line for line in self._lines(out) if "subject" in line]), 0)

    def test_4_the_review_holds_counts_only_with_every_id_masked(self):
        # Known timings: the daily job's pass took 612.5 s (over the window), its two dates 1 s and 3 s.
        with layers._connect(self.database) as owner:
            owner.execute("UPDATE quant_shadow.passes SET seconds = 612.5 WHERE phase = 'latest'")
            owner.execute("UPDATE quant_shadow.builds b SET seconds = CASE b.subject_id WHEN %s THEN 1.0 ELSE 3.0 END "
                          "FROM quant_shadow.passes p WHERE p.pass_id = b.pass_id AND p.phase = 'latest'", (SMALL,))
            owner.execute("UPDATE quant_shadow.draft_events SET seconds = 0.25")
        folder = Path(self.out_dir.name) / "review"
        code, out, err = self._main("review", "--out", str(folder))
        self.assertEqual(code, 0, err)
        markdown = (folder / "review.md").read_text(encoding="utf-8")
        text = (folder / "review.json").read_text(encoding="utf-8")
        review = json.loads(text)
        self.assertEqual(oct((folder / "review.md").stat().st_mode & 0o777), oct(0o600))
        pairs = self._pairs()
        # Rows by section and quality.
        rows = review["rows"]
        self.assertEqual(rows["total"]["rows"], 9 * pairs)
        self.assertEqual(rows["total"]["pass"] + rows["total"]["warn"], 9 * pairs)
        self.assertEqual(set(rows["by_section"]), set(qs.SECTION_NAMES))
        self.assertEqual((rows["subject_dates_complete"], rows["first_date"], rows["last_date"]),
                         (pairs, "2026-10-12", AS_OF))
        self.assertEqual([item["subject"] for item in rows["subjects"]], ["u01", "u02"])
        self.assertEqual(review["history"], {"history_days": 6, "subject_dates": pairs, "complete": pairs, "left": 0})
        # The timing maths on the known timings.
        latest = review["timing"]["latest_pass"]
        self.assertEqual({key: latest[key] for key in ("seconds", "would_end_at", "fits_window", "move_to_10_00",
                                                        "within_budget", "skipped_for_time", "cold", "runner")},
                         {"seconds": 612.5, "would_end_at": "09:40:13", "fits_window": False, "move_to_10_00": True,
                          "within_budget": False, "skipped_for_time": 0, "cold": True, "runner": "local"})
        self.assertEqual(latest["per_subject_date_seconds"], {"count": 2, "p50": 2.0, "p95": 2.9, "max": 3.0})
        self.assertIn("so it does not fit 09:30 to 09:40: the job moves to 10:00.", markdown)
        self.assertEqual(set(review["timing"]["per_section_ms"]), set(qs.SECTION_NAMES))
        # The contract rules on every built date, the 077 views and the drafts.
        self.assertEqual(len(review["contract_rules"]), 9)
        self.assertEqual([rule["rule"] for rule in review["contract_rules"] if not rule["passed"]], [])
        self.assertEqual(review["desk_views"], {"present": True, "subject_live_executions": {"rows": 0, "subjects": 0},
                                                "benchmark_live_quotes": {"rows": 0}})
        self.assertEqual({name: (held["state"], [event["outcome"] for event in held["events"]])
                          for name, held in review["held_migrations"].items()},
                         {"quant_analytics": ("applied", ["applied"]), "desk_live_views": ("applied", ["applied"])})
        self.assertIn(f"applied on the copy in 0.250 s (sha256 {self._sha('quant_analytics.sql')[:12]})", markdown)
        # The errors: the failed engine, masked, its figure gone, built since. The history goes
        # newest first, u02 before u01 on a date: 15 Oct was built in the first run.
        self.assertEqual(sorted((error["phase"], error["subject"], error["as_of"], error["section"], error["error"],
                                 error["built_since"]) for error in review["errors"]),
                         [("history", "u01", "2026-10-14", "execution", "RuntimeError: boom u01 <n>", True),
                          ("history", "u01", "2026-10-15", "execution", "RuntimeError: boom u01 <n>", True),
                          ("history", "u02", "2026-10-14", "execution", "RuntimeError: boom u01 <n>", True)])
        for item in review["unavailable_reasons"]:
            self.assertIsNone(re.search(r"\d", re.sub(r"u\d{2}", "", item["reason"])), item)
        # Every id masked, everywhere: the files, and every line any command printed.
        everything = markdown + text + out + err + self.output.getvalue()
        self.assertNotIn("U9", everything)
        for account in (SMALL, LARGE, qdb.OTHER_ACCOUNT):
            self.assertNotIn(account, everything)
        self.assertIsNone(ACCOUNT_LIKE.search(markdown + text))
        # Fractional numbers are timings only: every one sits under timing or the drafts' seconds.
        self.assertEqual(sorted({path[0] for path in _float_paths(review)}), ["held_migrations", "timing"])
        self.assertTrue(markdown.isascii() and text.isascii())
        self._assert_no_payload_figure(markdown + text)
        # The same files between the BEGIN and END lines, numbered, with their sha256.
        for name, content in (("md", markdown), ("json", text)):
            with self.subTest(name):
                begin = re.search(rf"^QUANT_SHADOW_REVIEW {name} BEGIN sha256=([0-9a-f]{{64}}) lines=(\d+)$", out,
                                  flags=re.MULTILINE)
                self.assertIsNotNone(begin)
                body = re.findall(rf"^QUANT_SHADOW_REVIEW {name} (\d{{5}}) (.*)$", out, flags=re.MULTILINE)
                self.assertEqual(len(body), int(begin.group(2)))
                rebuilt = "\n".join(line for _, line in sorted(body)) + "\n"
                self.assertEqual(rebuilt, content)
                self.assertEqual(hashlib.sha256(rebuilt.encode("utf-8")).hexdigest(), begin.group(1))
                self.assertIn(f"QUANT_SHADOW_REVIEW {name} END", out)

    def _assert_no_payload_figure(self, text: str) -> None:
        """No number from a stored payload appears: the review's decimals have three places at most,
        and no large payload number appears at all."""
        numbers: list[float] = []

        def walk(value):
            if isinstance(value, bool):
                return
            if isinstance(value, (int, float)):
                numbers.append(value)
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        for (payload,) in self._rows("SELECT payload_json FROM gold.quant_analytics_snapshots"):
            walk(payload)
        self.assertGreater(len(numbers), 1000)
        # The sha256 values, the generated time and the database name are not figures.
        plain = re.sub(r"[0-9a-f]{64}", "", text).replace(self.database, "")
        plain = re.sub(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?", "", plain)
        decimals = re.findall(r"\d+\.(\d+)", plain)
        self.assertTrue(decimals)
        self.assertEqual([digits for digits in decimals if len(digits) > 3], [])
        tokens = set(re.findall(r"\d+(?:\.\d+)?", plain))
        for value in numbers:
            if isinstance(value, float) and not value.is_integer() and len(repr(value).partition(".")[2]) > 3:
                for shown in (repr(value), f"{value:.4f}", f"{value:.6f}"):
                    self.assertNotIn(shown, plain)
            if abs(value) >= 10000:
                self.assertNotIn(str(int(abs(value))), tokens, value)

    def test_5_undo_leaves_the_copy_as_it_was_and_a_second_rehearsal_runs(self):
        # A half-applied draft is refused, not run over.
        with layers._connect(self.database) as owner:
            owner.execute("DROP VIEW public.benchmark_live_quotes")
        code, out, _ = self._main("apply-held")
        self.assertEqual(code, shadow.EXIT_FAILED)
        outcomes = {item["draft"]: item for item in self._lines(out)}
        self.assertEqual(outcomes["quant_analytics"]["outcome"], "already applied")
        self.assertEqual(outcomes["desk_live_views"]["outcome"], "failed")
        self.assertIn("run undo first", outcomes["desk_live_views"]["error"])
        # Undo, twice: the second has nothing to do. The database is as before apply-held.
        code, out, err = self._main("undo")
        self.assertEqual(code, 0, err)
        self.assertEqual([item.get("outcome") for item in self._lines(out)], ["undone", "undone", None])
        self.assertEqual(self._lines(out)[-1]["run_log_passes_cleared"], 5)  # 1 latest, 4 history
        code, out, err = self._main("undo")
        self.assertEqual(code, 0, err)
        self.assertEqual([item.get("outcome") for item in self._lines(out)][:2], ["nothing to undo", "nothing to undo"])
        with layers._connect(self.database) as owner:
            self.assertEqual(qdb._snapshot(owner), self.before)
        # A draft that fails rolls back whole and is recorded as failed, its error masked.
        broken = Path(self.out_dir.name) / "broken.sql"
        broken.write_text('CREATE VIEW public.subject_live_executions AS SELECT 1 AS one;\n'
                          f'SELECT * FROM "no_such_{SMALL}";\n', encoding="utf-8")
        real = shadow._draft_path
        with patch.object(shadow, "_draft_path",
                          lambda draft, undo=False: broken if draft.name == "desk_live_views" and not undo
                          else real(draft, undo)):
            code, out, _ = self._main("apply-held")
        self.assertEqual(code, shadow.EXIT_FAILED)
        outcomes = {item["draft"]: item for item in self._lines(out)}
        self.assertEqual((outcomes["quant_analytics"]["outcome"], outcomes["desk_live_views"]["outcome"]),
                         ("applied", "failed"))
        self.assertIn("no_such_u01", outcomes["desk_live_views"]["error"])
        self.assertIsNone(self._rows("SELECT to_regclass('public.subject_live_executions')")[0][0])
        self.assertEqual(self._rows("SELECT outcome FROM quant_shadow.draft_events WHERE draft = 'desk_live_views' "
                                    "ORDER BY event_id DESC LIMIT 1"), [("failed",)])
        # The second rehearsal: both applied, the daily job's pass timed cold again.
        code, out, err = self._main("apply-held")
        self.assertEqual(code, 0, err)
        self.assertEqual([item["outcome"] for item in self._lines(out)], ["already applied", "applied"])
        code, out, err = self._main("run", "--history-days", "0")
        self.assertEqual(code, 0, out + err)
        final = self._lines(out)[-1]
        self.assertEqual((final["status"], final["latest_pass"], final["history"]["already_built"]),
                         ("complete", "run now", 2))
        self.assertEqual(self._rows("SELECT count(*) FROM gold.quant_analytics_snapshots"), [(18,)])
        self.assertEqual(self._rows("SELECT version, sha256 FROM schema_migrations ORDER BY 1"),
                         self.before["migrations"])
        self.assertNotIn("U9", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
