"""Lineage records: off by default, never fails or slows a job, OpenLineage-shaped run events,
raw files from raw_capture as inputs, and every daily-chain job wired. No database here;
test_lineage_db.py runs the same code against PostgreSQL. Synthetic data only."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import lineage, raw_capture
from ibkr_etl.raw_capture import RawCapture

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = "postgresql://lineage@127.0.0.1:1/unused"
ON = {"LINEAGE_RECORDING": "true", "DATABASE_URL": DATABASE_URL}
SLACK = 0.5


class _Database:
    """Stands in for lineage._execute: keeps each transaction's statements."""

    def __init__(self, error: BaseException | None = None, hang: float = 0.0):
        self.transactions: list[list[tuple[str, tuple]]] = []
        self.error = error
        self.hang = hang
        self.release = threading.Event()

    def __call__(self, database_url, tenant_id, statements):
        if self.hang:
            self.release.wait(self.hang)
        if self.error is not None:
            raise self.error
        self.transactions.append(list(statements))

    def events(self) -> list[str]:
        return [args[2] for statements in self.transactions for sql, args in statements if "lineage_events" in sql]

    def facets(self, event_type: str) -> dict:
        for statements in self.transactions:
            for sql, args in statements:
                if "lineage_events" in sql and args[2] == event_type:
                    return json.loads(args[9])
        raise AssertionError(f"no {event_type} event")

    def io(self) -> list[dict]:
        rows = []
        for statements in self.transactions:
            for sql, args in statements:
                if "lineage_io" in sql:
                    rows.extend(json.loads(args[2]))
        return rows


class _Uploads:
    def __init__(self):
        self.calls = []

    def factory(self, account_url, container):
        return self.upload

    def upload(self, name, content, *, content_type, metadata):
        self.calls.append({"name": name, "content": content})
        return "https://example.invalid/bronze-market-data/" + name


class _Base(unittest.TestCase):
    def setUp(self):
        lineage.reset()
        self.environment = patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        for name in ("LINEAGE_RECORDING", "LINEAGE_CODE_VERSION", "LINEAGE_BUDGET_SECONDS",
                     "CONTAINER_APP_JOB_EXECUTION_NAME", "CONTAINER_APP_JOB_NAME"):
            os.environ.pop(name, None)

    def tearDown(self):
        self.environment.stop()
        lineage.reset()
        raw_capture.reset()
        self.assertEqual(lineage._CURRENT, [])


class OffByDefaultTests(_Base):
    def test_the_switch_is_exactly_true(self):
        self.assertFalse(lineage.enabled({}))
        for value in ("1", "yes", "on", "false", "", "truthy"):
            self.assertFalse(lineage.enabled({"LINEAGE_RECORDING": value}), value)
        for value in ("true", "TRUE", " True "):
            self.assertTrue(lineage.enabled({"LINEAGE_RECORDING": value}), value)

    def test_off_touches_no_database_and_changes_nothing(self):
        database = _Database()
        os.environ["DATABASE_URL"] = DATABASE_URL
        with patch.object(lineage, "_execute", database):
            with lineage.run("ibkrv2stg-benchmark-daily", connection=object()) as record:
                record.input("silver.trades", rows=3)
                record.output("gold.performance_comparison_daily", rows=1)
                record.facet(job_run_id=1)
                record.append("ingestion_run_ids", 1)
                self.assertFalse(record.active)
                self.assertIsNone(record.id)
                self.assertIs(lineage.current(), record)
            self.assertEqual(lineage.run_main("ibkrv2stg-risk-free-daily", lambda: 7), 7)
        self.assertEqual(database.transactions, [])
        self.assertFalse(lineage.current().active)

    def test_on_without_a_postgres_url_records_nothing(self):
        database = _Database()
        with patch.dict(os.environ, {"LINEAGE_RECORDING": "true", "DATABASE_URL": "sqlite:///x"}), \
                patch.object(lineage, "_execute", database):
            with lineage.run("job") as record:
                self.assertFalse(record.active)
        self.assertEqual(database.transactions, [])

    def test_outside_a_run_current_is_an_inactive_no_op(self):
        record = lineage.current()
        self.assertFalse(record.active)
        record.output("silver.trades", rows=1)
        self.assertFalse(record.bind(object()))


class NeverFailsTheJobTests(_Base):
    def test_a_database_error_switches_recording_off_and_the_job_carries_on(self):
        database = _Database(error=OSError("connection refused"))
        with patch.dict(os.environ, ON), patch.object(lineage, "_execute", database):
            with self.assertLogs("ibkr_etl.lineage", "WARNING"):
                self.assertEqual(lineage.run_main("job", lambda: 0), 0)
            self.assertIn("OSError", lineage.disabled_reason())
            # Later runs in the same process do not try again.
            with lineage.run("job") as record:
                self.assertFalse(record.active)
        self.assertEqual(database.transactions, [])

    def test_a_hung_database_costs_at_most_the_write_wait(self):
        database = _Database(hang=30)
        try:
            with patch.dict(os.environ, ON), patch.object(lineage, "_execute", database), \
                    patch.object(lineage, "WRITE_WAIT_SECONDS", 0.2), self.assertLogs("ibkr_etl.lineage", "WARNING"):
                started = time.monotonic()
                with lineage.run("job") as record:
                    pass
                self.assertLess(time.monotonic() - started, 0.2 + SLACK)
                self.assertFalse(record.active)
                self.assertIn("did not answer", lineage.disabled_reason())
        finally:
            database.release.set()

    def test_all_waits_share_one_budget(self):
        database = _Database()
        with patch.dict(os.environ, {**ON, "LINEAGE_BUDGET_SECONDS": "0"}), \
                patch.object(lineage, "_execute", database), self.assertLogs("ibkr_etl.lineage", "WARNING"):
            with lineage.run("job") as record:
                self.assertFalse(record.active)
        self.assertEqual(database.transactions, [])
        self.assertIn("budget", lineage.disabled_reason())

    def test_the_jobs_own_exception_passes_through_unchanged(self):
        for environment, database in (({}, _Database()), (ON, _Database()), (ON, _Database(error=OSError("x")))):
            lineage.reset()
            error = ValueError("the job's own failure")
            with patch.dict(os.environ, environment), patch.object(lineage, "_execute", database), \
                    patch.object(lineage.LOGGER, "warning"), self.assertRaises(ValueError) as caught:
                with lineage.run("job"):
                    raise error
            self.assertIs(caught.exception, error)

    def test_a_failing_end_write_never_raises(self):
        database = _Database()
        with patch.dict(os.environ, ON), patch.object(lineage, "_execute", database), \
                self.assertLogs("ibkr_etl.lineage", "WARNING"):
            with lineage.run("job") as record:
                self.assertTrue(record.active)
                database.error = RuntimeError("disk full")
                record.output("silver.trades", rows=1)
        self.assertEqual(database.events(), ["START"])
        self.assertFalse(record.active)

    def test_bad_arguments_are_ignored(self):
        database = _Database()
        with patch.dict(os.environ, ON), patch.object(lineage, "_execute", database), \
                self.assertLogs("ibkr_etl.lineage", "WARNING"):
            with lineage.run("job") as record:
                record.output(None)
                record.output("DROP TABLE x")
                record.output("silver.trades", rows="many")
                record.input("blob://bronze-market-data/a.csv", sha256="not-a-digest")
                record.facet(**{"x": object()})
                record.append("ingestion_run_ids", object())
                record.exit_code(object())
                self.assertFalse(record.bind(object()))
        io = database.io()
        self.assertEqual([(row["namespace"], row["name"], row["sha256"]) for row in io],
                         [("blob", "bronze-market-data/a.csv", None)])


class RunEventTests(_Base):
    def _run(self, body, job="ibkrv2stg-risk-free-daily", environment=None):
        database = _Database()
        with patch.dict(os.environ, {**ON, **(environment or {})}), patch.object(lineage, "_execute", database):
            try:
                with lineage.run(job) as record:
                    body(record)
            except BaseException:  # noqa: BLE001 - the test looks at the events
                pass
        return database, record

    def test_a_completed_run_has_start_and_complete_and_its_datasets(self):
        def body(record):
            record.output("silver.risk_free_rates", rows=2)
            record.output("postgres://silver.risk_free_rates", rows=3)
            record.input("blob://bronze-market-data/treasury-par-yield/2026/10/16/x.xml", sha256="a" * 64)

        database, record = self._run(body, environment={"CONTAINER_APP_JOB_EXECUTION_NAME": "job-abc12"})
        self.assertEqual(database.events(), ["START", "COMPLETE"])
        self.assertEqual(len(database.transactions), 2, "START alone, then the end event and datasets together")
        start = database.transactions[0][0][1]
        self.assertEqual(start[1], record.run_id)
        self.assertEqual((start[4], start[5], start[7], start[8]),
                         ("wealthryse", "ibkrv2stg-risk-free-daily", "job-abc12", lineage.PRODUCER))
        self.assertIn("openlineage.io", database.facets("START")["schema_url"])
        io = {(row["direction"], row["namespace"], row["name"]): row for row in database.io()}
        self.assertEqual(io[("output", "postgres", "silver.risk_free_rates")]["row_count"], 5)
        self.assertEqual(io[("input", "blob", "bronze-market-data/treasury-par-yield/2026/10/16/x.xml")]["sha256"],
                         "a" * 64)
        self.assertFalse(record.active)
        self.assertEqual(record.status, "COMPLETE")

    def test_a_failure_records_only_the_error_type(self):
        def body(record):
            raise RuntimeError("a message that may quote sensitive-detail-7731")

        database, record = self._run(body)
        self.assertEqual(database.events(), ["START", "FAIL"])
        self.assertEqual(database.facets("FAIL")["error"], {"type": "RuntimeError"})
        self.assertNotIn("sensitive-detail-7731", json.dumps(database.transactions, default=str))

    def test_an_interrupt_is_an_abort(self):
        def body(record):
            raise KeyboardInterrupt

        database, _ = self._run(body)
        self.assertEqual(database.events(), ["START", "ABORT"])

    def test_system_exit_zero_completes_and_non_zero_fails(self):
        def zero(record):
            raise SystemExit(0)

        def two(record):
            raise SystemExit(2)

        self.assertEqual(self._run(zero)[0].events(), ["START", "COMPLETE"])
        lineage.reset()
        self.assertEqual(self._run(two)[0].events(), ["START", "FAIL"])

    def test_run_main_returns_the_code_and_a_non_zero_code_fails(self):
        for code, status in ((0, "COMPLETE"), (None, "COMPLETE"), (2, "FAIL")):
            lineage.reset()
            database = _Database()
            with patch.dict(os.environ, ON), patch.object(lineage, "_execute", database):
                self.assertEqual(lineage.run_main("ibkrv2stg-sector-classification", lambda: code), code)
            self.assertEqual(database.events(), ["START", status])
            self.assertEqual(database.facets(status)["exit_code"], code)

    def test_the_jobs_declared_inputs_are_recorded(self):
        database, _ = self._run(lambda record: None, job="ibkrv2stg-risk-snapshots")
        inputs = {row["name"] for row in database.io() if row["direction"] == "input"}
        self.assertEqual(inputs, set(lineage.JOB_INPUTS["ibkrv2stg-risk-snapshots"]))

    def test_list_facets_collect_distinct_values(self):
        def body(record):
            for value in (11, 12, 11):
                record.append("ingestion_run_ids", value)

        database, _ = self._run(body)
        self.assertEqual(database.facets("COMPLETE")["ingestion_run_ids"], [11, 12])

    def test_nested_runs_restore_the_outer_one(self):
        database = _Database()
        with patch.dict(os.environ, ON), patch.object(lineage, "_execute", database):
            with lineage.run("outer") as outer:
                with lineage.run("inner") as inner:
                    self.assertIs(lineage.current(), inner)
                self.assertIs(lineage.current(), outer)


class CodeVersionTests(_Base):
    def test_the_environment_wins(self):
        version, details = lineage.code_facets({"LINEAGE_CODE_VERSION": "ibkr-platform@sha256:" + "b" * 64})
        self.assertEqual(version, "ibkr-platform@sha256:" + "b" * 64)
        self.assertRegex(details["source_sha256"], "^[0-9a-f]{64}$")

    def test_otherwise_a_fingerprint_of_the_package(self):
        version, _ = lineage.code_facets({})
        self.assertRegex(version, "^source-sha256:[0-9a-f]{64}$")

    def test_the_fingerprint_follows_the_files_and_skips_caches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a.py").write_text("x = 1\n")
            first = lineage.source_fingerprint(root)
            lineage._FINGERPRINT.clear()
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"cache")
            self.assertEqual(lineage.source_fingerprint(root), first)
            lineage._FINGERPRINT.clear()
            (root / "a.py").write_text("x = 2\n")
            self.assertNotEqual(lineage.source_fingerprint(root), first)
            lineage._FINGERPRINT.clear()


class DatasetNameTests(unittest.TestCase):
    def test_tables_and_blobs(self):
        self.assertEqual(lineage.dataset("silver.trades"), ("postgres", "silver.trades"))
        self.assertEqual(lineage.dataset("postgres://gold.risk_analytics_snapshots"),
                         ("postgres", "gold.risk_analytics_snapshots"))
        self.assertEqual(lineage.dataset("blob://ibkr-raw/t/2026/10/16/flex.xml"),
                         ("blob", "ibkr-raw/t/2026/10/16/flex.xml"))
        for bad in ("trades", "silver.Trades", "other.trades", "blob://", "blob://x", "silver.trades; drop",
                    "postgres://silver"):
            self.assertIsNone(lineage.dataset(bad), bad)

    def test_blob_urls_become_blob_datasets(self):
        self.assertEqual(
            lineage.blob_dataset("https://acct.blob.core.windows.net/ibkr-raw/t/2026/10/16/flex_a%2Bb.xml"),
            "blob://ibkr-raw/t/2026/10/16/flex_a+b.xml")
        for other in ("/data/raw/flex.xml", "http://acct.blob.core.windows.net/ibkr-raw/x",
                      "https://example.invalid/ibkr-raw/x", None, 5):
            self.assertIsNone(lineage.blob_dataset(other), other)


class _Info:
    def __init__(self, status):
        self.transaction_status = status


class _Session:
    def __init__(self, status):
        self.info = _Info(status)
        self.autocommit = False
        self.executed = []
        self.commits = 0

    def execute(self, sql, args=()):
        self.executed.append((sql, args))

    def commit(self):
        self.commits += 1


class BindTests(_Base):
    def test_an_idle_session_carries_the_run_id_committed_at_once(self):
        from psycopg import pq

        session = _Session(pq.TransactionStatus.IDLE)
        database = _Database()
        with patch.dict(os.environ, ON), patch.object(lineage, "_execute", database):
            with lineage.run("job", connection=session) as record:
                pass
        self.assertEqual(session.executed, [("SELECT set_config(%s, %s, false)", ("lineage.run_id", record.run_id))])
        self.assertEqual(session.commits, 1)
        self.assertEqual(database.facets("COMPLETE")["bound_sessions"], 1)

    def test_a_session_in_a_transaction_or_an_unrecorded_run_is_left_alone(self):
        from psycopg import pq

        busy = _Session(pq.TransactionStatus.INTRANS)
        with patch.dict(os.environ, ON), patch.object(lineage, "_execute", _Database()), \
                self.assertLogs("ibkr_etl.lineage", "WARNING"):
            with lineage.run("job", connection=busy):
                pass
        self.assertEqual(busy.executed, [])
        lineage.reset()
        idle = _Session(pq.TransactionStatus.IDLE)
        with lineage.run("job", connection=idle):
            pass
        self.assertEqual(idle.executed, [])

    def test_the_wrapped_connection_is_unwrapped(self):
        from psycopg import pq

        class Wrapper:
            def __init__(self, inner):
                self._connection = inner

        session = _Session(pq.TransactionStatus.IDLE)
        with patch.dict(os.environ, ON), patch.object(lineage, "_execute", _Database()):
            with lineage.run("job") as record:
                self.assertTrue(record.bind(Wrapper(session)))
        self.assertEqual(len(session.executed), 1)


class RawFileInputTests(_Base):
    def test_each_stored_raw_file_becomes_an_input_with_its_sha256(self):
        uploads = _Uploads()
        capture = RawCapture(container="bronze-market-data", account_url="https://example.invalid",
                             uploader_factory=uploads.factory)
        database = _Database()
        with patch.dict(os.environ, ON), patch.object(lineage, "_execute", database):
            with lineage.run("ibkrv2stg-sector-classification"):
                name = capture.document("yahoo-sector-profile", "yfinance", payload={"sector": "Tech"},
                                        request={"symbol": "SPY"}, key="SPY")
            # After the run, files are no longer added to it.
            capture.document("yahoo-sector-profile", "yfinance", payload={"sector": "Energy"},
                             request={"symbol": "XLE"}, key="XLE")
        self.assertTrue(name)
        blobs = [row for row in database.io() if row["namespace"] == "blob"]
        self.assertEqual([row["name"] for row in blobs], ["bronze-market-data/" + name])
        self.assertEqual(blobs[0]["sha256"], hashlib.sha256(uploads.calls[0]["content"]).hexdigest())
        self.assertEqual(blobs[0]["facets"]["feed"], "yahoo-sector-profile")
        self.assertEqual(raw_capture._OBSERVERS, [])

    def test_a_failing_observer_never_fails_a_capture(self):
        uploads = _Uploads()
        capture = RawCapture(container="bronze-market-data", account_url="https://example.invalid",
                             uploader_factory=uploads.factory)

        def broken(record):
            raise RuntimeError("observer")

        remove = raw_capture.add_observer(broken)
        try:
            self.assertTrue(capture.document("yahoo-sector-profile", "p", payload={}, request={}, key="k"))
        finally:
            remove()
        self.assertEqual(len(uploads.calls), 1)


class BenchmarkRawDigestTests(_Base):
    def test_raw_sha256_names_the_raw_file_only_when_recording_is_on(self):
        from ibkr_etl.provisional_benchmark import raw_prices_digest
        from ibkr_etl.util import canonical_json, sha256_text

        received = [("2026-10-15", 100.0), ("2026-10-16", 101.5)]
        parsed = sha256_text(canonical_json(received))
        captured = {"blob": "yahoo-benchmark-history/x.csv", "sha256": "c" * 64}
        self.assertEqual(raw_prices_digest(captured, received), parsed)
        with patch.dict(os.environ, {"LINEAGE_RECORDING": "true"}):
            self.assertEqual(raw_prices_digest(captured, received), "c" * 64)
            self.assertEqual(raw_prices_digest({}, received), parsed)
            self.assertEqual(raw_prices_digest(None, received), parsed)

    def test_the_fetch_reports_the_raw_file_it_stored(self):
        import pandas as pd

        from ibkr_etl.provisional_benchmark import BENCHMARKS, fetch_benchmark_yfinance

        class Ticker:
            def __init__(self, frame):
                self.frame = frame

            def history(self, **kwargs):
                return self.frame

        uploads = _Uploads()
        raw_capture.reset(RawCapture(container="bronze-market-data", account_url="https://example.invalid",
                                     uploader_factory=uploads.factory))
        today = raw_capture.utc_now().date()
        frame = pd.DataFrame({"Close": [6000.5], "Adj Close": [6000.5], "Volume": [1]},
                             index=pd.DatetimeIndex([today.isoformat()], name="Date"))
        captured: dict = {}
        fetch_benchmark_yfinance(BENCHMARKS[0], today - timedelta(days=3), today, lambda _s: Ticker(frame),
                                 captured=captured)
        self.assertEqual(captured["blob"], uploads.calls[0]["name"])
        self.assertEqual(captured["sha256"], hashlib.sha256(uploads.calls[0]["content"]).hexdigest())
        raw_capture.reset(RawCapture())
        captured = {}
        fetch_benchmark_yfinance(BENCHMARKS[0], today - timedelta(days=3), today, lambda _s: Ticker(frame),
                                 captured=captured)
        self.assertEqual(captured, {})


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location("lineage_wiring_" + name.replace(".py", ""), ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Each scheduled job of the daily chain: its Bicep file, the script that does the work, and
# whether that script's database session writes gold rows (so it must carry the run id).
WIRED = {
    "staging-institutional-daily.bicep": ("run_institutional_flex_worker.py", "ibkrv2stg-institutional-daily", False),
    "staging-benchmark-job.bicep": ("refresh_provisional_benchmark.py", "ibkrv2stg-benchmark-daily", True),
    "staging-sector-classification-job.bicep": (
        "refresh_staging_sector_classifications.py", "ibkrv2stg-sector-classification", False),
    "staging-risk-free-job.bicep": ("refresh_staging_risk_free.py", "ibkrv2stg-risk-free-daily", False),
    "staging-derived-refresh.bicep": ("run_self_service_derived_refresh.py", "ibkrv2stg-derived-daily", True),
    "staging-risk-snapshot-job.bicep": ("build_risk_snapshots.py", "ibkrv2stg-risk-snapshots", True),
    "staging-crisis-scenarios-job.bicep": ("refresh_crisis_scenarios.py", "ibkrv2stg-crisis-scenarios-daily", True),
}


class WiringTests(unittest.TestCase):
    def test_each_daily_job_records_its_runs_under_its_catalog_name(self):
        catalog = json.loads((ROOT / "infra/medallion/catalog.json").read_text())
        for bicep, (script, job, writes_gold) in WIRED.items():
            with self.subTest(job=job):
                args = (ROOT / "infra" / bicep).read_text()
                entry = "run_institutional_daily_pipeline.py" if "institutional" in bicep else script
                self.assertIn(f"'scripts/{entry}'", args)
                if entry != script:
                    self.assertIn(f'"{script}"', (ROOT / "scripts" / entry).read_text())
                tree = ast.parse((ROOT / "scripts" / script).read_text())
                names = [node.args[0].value for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                         and node.func.attr == "run_main" and node.args and isinstance(node.args[0], ast.Constant)]
                self.assertEqual(names, [job])
                self.assertIn(job, catalog["jobs"])
                self.assertIn(job, lineage.JOB_INPUTS)
                binds = [node for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                         and node.func.attr == "bind"]
                self.assertEqual(bool(binds), writes_gold)

    def test_every_recorded_table_is_in_the_catalog(self):
        catalog = json.loads((ROOT / "infra/medallion/catalog.json").read_text())["postgres"]
        known = {f"{entry.get('target_schema', key.split('.')[0])}.{key.split('.', 1)[1]}"
                 for key, entry in catalog.items()}
        recorded = {name for names in lineage.JOB_INPUTS.values() for name in names}
        recorded |= set(lineage.TRACEABLE)
        for script, _job, _gold in WIRED.values():
            text = (ROOT / "scripts" / script).read_text()
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and lineage.dataset(node.value) and lineage.dataset(node.value)[0] == "postgres" \
                        and node.value.split(".")[0] in {"bronze", "silver", "gold"}:
                    recorded.add(node.value)
        self.assertEqual(sorted(recorded - known), [])

    def test_the_flex_worker_records_the_statement_and_the_tables_it_loaded(self):
        worker = _load_script("run_institutional_flex_worker.py")
        record = lineage.Run("ibkrv2stg-institutional-daily")
        record.active = True
        result = {
            "run_id": 41,
            "raw_path": "https://acct.blob.core.windows.net/ibkr-raw/t/2026/10/16/flex_x.xml",
            "raw_sha256": "d" * 64,
            "reconciliation": {"record_types": {"Trade": 3, "CashTransaction": 2, "OpenPosition": 4,
                                                "EquitySummaryByReportDateInBase": 1, "ConversionRate": 5,
                                                "SecurityInfo": 6, "legacy_source_keys_reused": 9}},
        }
        with patch.object(worker.lineage, "current", return_value=record):
            worker._record_statement(result)
            worker._record_statement({**result, "run_id": 42, "raw_path": "/data/raw/flex_y.xml"})
        datasets = {(d, ns, n): entry for (d, ns, n), entry in record._datasets.items()}
        blob = datasets[("input", "blob", "ibkr-raw/t/2026/10/16/flex_x.xml")]
        self.assertEqual((blob["sha256"], blob["facets"]["ingestion_run_id"]), ("d" * 64, 41))
        self.assertEqual(len([key for key in datasets if key[1] == "blob"]), 1, "a local file is not a blob")
        rows = {name: entry["row_count"] for (direction, ns, name), entry in datasets.items() if direction == "output"}
        self.assertEqual(rows["silver.trades"], 6)
        self.assertEqual(rows["silver.cash_transactions"], 4)
        self.assertEqual(rows["silver.position_snapshots"], 8)
        self.assertEqual(rows["silver.nav_snapshots"], 2)
        self.assertEqual(rows["silver.fx_rates"], 10)
        self.assertEqual(rows["bronze.flex_rows"], 2 * (3 + 2 + 4 + 1 + 5 + 6))
        self.assertEqual(rows["bronze.raw_reports"], 2)
        self.assertIn("silver.instruments", rows)
        self.assertEqual(record._facets["ingestion_run_ids"], [41, 42])

    def test_the_flex_worker_does_nothing_when_recording_is_off(self):
        worker = _load_script("run_institutional_flex_worker.py")
        record = lineage.Run("ibkrv2stg-institutional-daily")
        with patch.object(worker.lineage, "current", return_value=record):
            worker._record_statement({"run_id": 1, "raw_path": "x", "reconciliation": None})
        self.assertEqual(record._datasets, {})

    def test_ingest_returns_the_statement_digest(self):
        text = (ROOT / "src/ibkr_etl/service.py").read_text()
        self.assertIn('"raw_sha256": digest', text)


if __name__ == "__main__":
    unittest.main()
