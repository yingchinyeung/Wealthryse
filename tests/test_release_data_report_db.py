"""The release data report (scripts/release_data_report.py) on real PostgreSQL databases.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts
one). Skipped otherwise. Builds a database with every migration (so the tables are in
their silver and gold schemas), seeds synthetic rows, copies it, changes some rows in
the copy as a candidate image's jobs would, and checks the report. Synthetic data only.
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations" / "postgres"
TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
OWNER = "release_report_test_owner"
PASSWORD = "release-report-test-only-password"  # throwaway role in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
ACCOUNTS = ("U9300001", "U9300002")  # synthetic
COMPOSITE = "synthetic-composite"
NAMES = ("Jane Example", "John Example")
DATES = [f"2026-10-{day:02d}" for day in range(5, 16)]
PAYLOAD = {"metrics": {"volatility": 0.12, "max_drawdown": -0.2}, "generated_at": "2026-10-16T09:00:00+00:00",
           "holdings": {"ZZTEST": {"weight": 0.5}, "U9300001": {"weight": 0.5}},
           "series": [{"date": day, "value": 1 + index / 100} for index, day in enumerate(DATES)]}
_MODULE = {}


def _report():
    if "module" not in _MODULE:
        spec = importlib.util.spec_from_file_location("release_data_report",
                                                      ROOT / "scripts" / "release_data_report.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULE["module"] = module
    return _MODULE["module"]


def _dsn(database: str, user: str | None = OWNER, tenant: str | None = TENANT) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(ADMIN_DSN)
    params["dbname"] = database
    if user:
        params["user"], params["password"] = user, PASSWORD
    if tenant:
        params["options"] = f"-c app.tenant_id={tenant}"
    return make_conninfo(**params)


def _admin(sql: str) -> None:
    import psycopg

    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(sql)


def _build(database: str, *, layers: bool = True) -> None:
    """Every migration in order as the owner, who is not a superuser, so row security applies."""
    import psycopg

    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER,)).fetchone() is None:
            admin.execute(f"CREATE ROLE {OWNER} LOGIN PASSWORD '{PASSWORD}'")
        admin.execute(f'CREATE DATABASE "{database}" OWNER {OWNER}')
    with psycopg.connect(_dsn(database, None), autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    with psycopg.connect(_dsn(database, tenant=None)) as owner:
        owner.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, "
                      "applied_at TEXT NOT NULL)")
        owner.commit()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if not layers and "_medallion_" in path.name:
                continue
            text = path.read_text(encoding="utf-8")
            try:
                owner.execute(text)
                owner.commit()
            except Exception:
                owner.rollback()
                if path.name not in TOLERATED_MIGRATIONS:
                    raise
            owner.execute("INSERT INTO schema_migrations VALUES (%s, %s, '2026-10-01T00:00:00+00:00')",
                          (path.name, hashlib.sha256(text.encode("utf-8")).hexdigest()))
            if owner.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                    owner.execute("SELECT 1 FROM tenants").fetchone() is None:
                for tenant in (OTHER_TENANT, TENANT):
                    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
                    owner.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                  "VALUES (%s, 'Test', 'active', '2026-01-01')", (tenant,))
                owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
            owner.commit()


def _seed(connection) -> None:
    run = connection.execute(
        "INSERT INTO ingestion_runs(run_type, started_at, completed_at, status) "
        "VALUES ('synthetic-flex', '2026-10-16T08:01:00+00:00', '2026-10-16T08:03:00+00:00', 'success') "
        "RETURNING run_id").fetchone()[0]
    for account, name in zip(ACCOUNTS, NAMES):
        connection.execute(
            "INSERT INTO accounts(account_id, alias, base_currency, first_seen_at, last_seen_at, source_json) "
            "VALUES (%s, %s, 'USD', '2026-01-01', '2026-10-16', %s)", (account, name, json.dumps({"name": name})))
        for index, day in enumerate(DATES):
            connection.execute(
                "INSERT INTO nav_snapshots(account_id, snapshot_date, currency, total_text, cash_text, row_sha256, "
                "last_seen_run_id) VALUES (%s, %s, 'USD', %s, '1000.25', %s, %s)",
                (account, day, f"{1000000.5 + index * 10:.2f}", "a" * 64, run))
    connection.execute(
        "INSERT INTO benchmark_master(benchmark_id, symbol, name, currency, return_convention, created_at, updated_at) "
        "VALUES ('SYN', '^SYN', 'Synthetic', 'USD', 'price', '2026-01-01', '2026-01-01')")
    for index, day in enumerate(DATES):
        connection.execute(
            "INSERT INTO benchmark_prices(benchmark_id, observation_date, level, currency, observation_status, "
            "provider, fetched_at, quality_status) VALUES ('SYN', %s, %s, 'USD', 'official_close', 'p', "
            "'2026-10-16T08:31:00+00:00', 'pass')", (day, 100 + index))
        for subject in (*ACCOUNTS, COMPOSITE):
            connection.execute(
                "INSERT INTO performance_comparison_daily(subject_id, benchmark_id, observation_date, "
                "reporting_currency, portfolio_value, benchmark_level, normalized_portfolio, normalized_benchmark, "
                "portfolio_return, benchmark_return, excess_return, calculation_version, quality_status, "
                "generated_at) VALUES (%s, 'SYN', %s, 'USD', %s, %s, %s, %s, %s, %s, %s, "
                "'benchmark-comparison-v1', 'pass', '2026-10-16T08:35:00+00:00')",
                (subject, day, 1000000 + index, 100 + index, 1 + index / 100, 1 + index / 50,
                 index / 100, index / 50, index / 100 - index / 50))
    for subject in ACCOUNTS:
        text = json.dumps(PAYLOAD, sort_keys=True)
        connection.execute(
            "INSERT INTO risk_analytics_snapshots(subject_id, as_of_date, analytics_version, comparison_version, "
            "source_data_sha256, payload_sha256, payload_json, quality_status, generated_at, duration_ms, "
            "timings_json) VALUES (%s, '2026-10-15', 'risk-v1', 'benchmark-comparison-v1', %s, %s, %s::jsonb, "
            "'pass', '2026-10-16T08:56:00+00:00', 12.5, '{\"total\": 12.5}'::jsonb)",
            (subject, "b" * 64, hashlib.sha256(text.encode()).hexdigest(), text))
    payload = json.dumps({"nav": 1000100.5, "account": ACCOUNTS[0]})
    connection.execute(
        "INSERT INTO reporting_snapshots(snapshot_id, subject_id, period_start, period_end, as_of_at, "
        "reporting_currency, formula_version, source_manifest_json, data_sha256, quality_status, status, "
        "created_at, snapshot_payload_json) VALUES (%s, %s, '2026-09-01', '2026-09-30', "
        "'2026-10-01T09:00:00+00:00', 'USD', 'f1', '{}', %s, 'pass', 'frozen', '2026-10-01T09:00:00+00:00', %s)",
        (str(uuid.uuid4()), ACCOUNTS[0], hashlib.sha256(payload.encode()).hexdigest(), payload))
    # Another tenant's row: a login under row security reading the default tenant never sees it.
    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (OTHER_TENANT,))
    connection.execute(
        "INSERT INTO benchmark_master(benchmark_id, symbol, name, currency, return_convention, created_at, updated_at) "
        "VALUES ('SYN', '^SYN', 'Synthetic', 'USD', 'price', '2026-01-01', '2026-01-01')")
    connection.execute(
        "INSERT INTO benchmark_prices(benchmark_id, observation_date, level, currency, observation_status, "
        "provider, fetched_at, quality_status) VALUES ('SYN', '2026-10-15', 50, 'USD', 'official_close', 'p', "
        "'2026-10-16T08:31:00+00:00', 'pass')")
    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT,))


def _candidate_run(connection) -> None:
    """What a candidate image's jobs might change on the copy."""
    connection.execute("UPDATE performance_comparison_daily SET generated_at = '2026-10-18T08:35:00+00:00'")
    connection.execute("UPDATE performance_comparison_daily SET portfolio_return = portfolio_return + 0.05 "
                       "WHERE subject_id = %s AND observation_date = '2026-10-15'", (ACCOUNTS[0],))
    connection.execute("UPDATE performance_comparison_daily SET portfolio_return = portfolio_return + 0.001 "
                       "WHERE subject_id = %s AND observation_date IN ('2026-10-12', '2026-10-13')", (ACCOUNTS[1],))
    connection.execute("UPDATE performance_comparison_daily SET quality_status = 'warn' "
                       "WHERE subject_id = %s AND observation_date = '2026-10-05'", (COMPOSITE,))
    connection.execute("DELETE FROM performance_comparison_daily WHERE subject_id = %s AND observation_date = "
                       "'2026-10-06'", (COMPOSITE,))
    connection.execute(
        "INSERT INTO performance_comparison_daily(subject_id, benchmark_id, observation_date, reporting_currency, "
        "portfolio_value, benchmark_level, calculation_version, quality_status, generated_at) VALUES "
        "(%s, 'SYN', '2026-10-16', 'USD', 1000020, 111, 'benchmark-comparison-v1', 'pass', "
        "'2026-10-18T08:35:00+00:00')", (ACCOUNTS[0],))
    connection.execute("UPDATE nav_snapshots SET total_text = '1000100.50' WHERE account_id = %s "
                       "AND snapshot_date = '2026-10-05'", (ACCOUNTS[1],))
    connection.execute("UPDATE accounts SET alias = 'Jane Q Example', last_seen_at = '2026-10-18' "
                       "WHERE account_id = %s", (ACCOUNTS[0],))
    changed = json.loads(json.dumps(PAYLOAD))
    changed["metrics"]["volatility"] = 0.13
    changed["generated_at"] = "2026-10-18T09:00:00+00:00"
    changed["series"][3]["value"] = 2.0
    changed["holdings"]["ZZTEST"]["weight"] = 0.25
    connection.execute("UPDATE risk_analytics_snapshots SET payload_json = %s::jsonb, duration_ms = 99, "
                       "generated_at = '2026-10-18T08:56:00+00:00' WHERE subject_id = %s",
                       (json.dumps(changed), ACCOUNTS[0]))
    connection.execute("UPDATE risk_analytics_snapshots SET generated_at = '2026-10-18T08:56:00+00:00', "
                       "duration_ms = 40 WHERE subject_id = %s", (ACCOUNTS[1],))


def _label(account: str) -> str:
    module = _report()
    digests = sorted(module._digest(item) for item in ACCOUNTS)
    return f"u…#{digests.index(module._digest(account)) + 1}"


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class ReleaseDataReportDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        suffix = uuid.uuid4().hex[:8]
        cls.before, cls.after, cls.tampered = (f"release_report_{name}_{suffix}"
                                               for name in ("before", "after", "tampered"))
        cls.public = f"release_report_public_{suffix}"
        _build(cls.before)
        with psycopg.connect(_dsn(cls.before)) as owner:
            _seed(owner)
        for copy in (cls.after, cls.tampered):
            _admin(f'CREATE DATABASE "{copy}" TEMPLATE "{cls.before}" OWNER {OWNER}')
        with psycopg.connect(_dsn(cls.after)) as owner:
            _candidate_run(owner)
        with psycopg.connect(_dsn(cls.tampered)) as owner:
            # A frozen month-end snapshot rewritten, which the trigger normally refuses.
            owner.execute("ALTER TABLE gold.reporting_snapshots DISABLE TRIGGER reporting_snapshot_immutable")
            owner.execute("UPDATE reporting_snapshots SET snapshot_payload_json = '{\"nav\": 1}'")
            owner.execute("ALTER TABLE gold.reporting_snapshots ENABLE TRIGGER reporting_snapshot_immutable")
        _build(cls.public, layers=False)
        cls.work = tempfile.TemporaryDirectory(prefix="release-report-test-")
        cls.folder = Path(cls.work.name)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()
        for database in (cls.before, cls.after, cls.tampered, cls.public):
            _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        _admin(f"DROP ROLE IF EXISTS {OWNER}")

    def _run(self, *arguments, env=None):
        output = io.StringIO()
        with patch.dict(os.environ, env or {}), contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(output):
            code = _report().main(list(arguments))
        return code, output.getvalue()

    def _compare(self, before: str, after: str, env: dict, name: str):
        markdown, report = self.folder / f"{name}.md", self.folder / f"{name}.json"
        code, output = self._run("compare", "--before", before, "--after", after, "--markdown", str(markdown),
                                 "--json", str(report), env=env)
        self.assertNotIn("ERROR", output)
        return code, output, markdown.read_text(encoding="utf-8"), json.loads(report.read_text(encoding="utf-8"))

    def _database_report(self):
        if not hasattr(self, "_cached"):
            type(self)._cached = self._compare("env:RELEASE_BEFORE", "env:RELEASE_AFTER",
                                               {"RELEASE_BEFORE": _dsn(self.before, tenant=None),
                                                "RELEASE_AFTER": _dsn(self.after, tenant=None)}, "databases")
        return self._cached

    def test_report_on_two_databases_names_what_changed(self):
        code, output, markdown, report = self._database_report()
        self.assertEqual(code, 1)
        summary = report["summary"]
        self.assertEqual(summary["changed"], ["gold.performance_comparison_daily", "gold.risk_analytics_snapshots",
                                              "silver.accounts", "silver.nav_snapshots"])
        self.assertIn("silver.benchmark_prices", summary["unchanged"])
        self.assertIn("gold.reporting_snapshots", summary["unchanged"])
        self.assertEqual(summary["attention"], [])
        self.assertEqual(summary["frozen_snapshots"]["checked"], 1)
        self.assertEqual(summary["new"] + summary["gone"], [])
        self.assertEqual(summary["tables"], len(summary["changed"]) + len(summary["unchanged"]))
        self.assertEqual(report["before"]["rows_read"], "the default tenant")

        performance = report["tables"]["gold.performance_comparison_daily"]
        self.assertEqual(performance["location"], {"before": "gold", "after": "gold"})
        self.assertEqual(performance["rows"], {"before": 33, "after": 33, "added": 1, "removed": 1, "changed": 4})
        self.assertEqual(performance["dates"], {"added": ["2026-10-16", "2026-10-16"],
                                                "removed": ["2026-10-06", "2026-10-06"],
                                                "changed": ["2026-10-05", "2026-10-15"]})
        self.assertEqual(performance["not_compared"], ["generated_at"])  # rewritten on every row, not a change
        returns = performance["columns"]["portfolio_return"]
        self.assertEqual(returns["changed"], 3)
        self.assertAlmostEqual(returns["max_abs"]["delta"], 0.05)
        self.assertEqual((returns["max_abs"]["subject"], returns["max_abs"]["date"]),
                         (_label(ACCOUNTS[0]), "2026-10-15"))
        self.assertAlmostEqual(returns["max_rel"]["ratio"], 0.5)  # 0.10 to 0.15
        self.assertEqual(performance["transitions"], {"quality_status": {"pass → warn": 1}})
        self.assertEqual(performance["categories"]["quality_status"], {"pass": [33, 32], "warn": [0, 1]})
        self.assertEqual(set(performance["groups"]), {_label(ACCOUNTS[0]), _label(ACCOUNTS[1]), "s…#1"})
        self.assertEqual(performance["groups"]["s…#1"],
                         {"before": 11, "after": 10, "added": 0, "removed": 1, "changed": 1, "max_abs": None})

        nav = report["tables"]["silver.nav_snapshots"]
        self.assertEqual(nav["rows"]["changed"], 1)
        self.assertAlmostEqual(nav["columns"]["total_text"]["max_abs"]["delta"], 100.0)
        self.assertEqual(report["tables"]["silver.accounts"]["columns"]["alias"]["changed"], 1)
        self.assertNotIn("last_seen_at", report["tables"]["silver.accounts"]["columns"])

        risk = report["tables"]["gold.risk_analytics_snapshots"]
        self.assertEqual(risk["rows"]["changed"], 1)  # the other row's duration and time are not compared
        paths = risk["columns"]["payload_json"]["paths"]
        self.assertEqual(set(paths), {"payload_json:metrics.volatility", "payload_json:series[].value",
                                      "payload_json:holdings.*.weight"})
        self.assertAlmostEqual(paths["payload_json:metrics.volatility"]["max_rel"]["ratio"], 0.01 / 0.12)
        self.assertNotIn("structure", risk["columns"]["payload_json"])  # generated_at inside is ignored

        top = markdown.split("<details>", 1)[0]
        self.assertIn("**4 of ", top)
        self.assertIn("| gold.performance_comparison_daily | 33 → 33 | 1 | 1 | 4 | 2026-10-05 to 2026-10-16 |", top)
        self.assertIn(f"Δ up to +0.05 ({_label(ACCOUNTS[0])}, 2026-10-15)", top)
        self.assertIn("Frozen month-end snapshots: 1 checked (1 frozen or approved), unchanged.", top)
        self.assertIn(top.strip(), output)

    def test_report_holds_no_account_numbers_names_or_text_values(self):
        _, output, markdown, report = self._database_report()
        text = markdown + json.dumps(report, ensure_ascii=False) + output
        for secret in (*ACCOUNTS, *NAMES, "Jane Q", "ZZTEST", COMPOSITE, "1000100.50", "1000000.50"):
            self.assertNotIn(secret, text)
        self.assertNotRegex(text, r"[A-Z]{1,2}\d{5,}")

    def test_snapshot_files_give_the_same_report(self):
        files = {}
        for name, database in (("before", self.before), ("after", self.after)):
            files[name] = self.folder / f"{name}.jsonl.gz"
            code, output = self._run("snapshot", "--url-env", "RELEASE_SOURCE", "--out", str(files[name]),
                                     env={"RELEASE_SOURCE": _dsn(database, tenant=None)})
            self.assertEqual(code, 0, output)
            self.assertGreater(json.loads(output)["rows"], 0)
            self.assertEqual(files[name].stat().st_mode & 0o777, 0o600)
        snapshot_text = gzip.decompress(files["before"].read_bytes()).decode()
        for secret in (*ACCOUNTS, *NAMES):
            self.assertNotIn(secret, snapshot_text)  # digests only
        code, _, markdown, report = self._compare(str(files["before"]), str(files["after"]), {}, "files")
        _, _, _, direct = self._database_report()
        self.assertEqual(code, 1)
        self.assertEqual(report["tables"], direct["tables"])
        self.assertEqual(report["summary"], direct["summary"])
        self.assertEqual(report["before"]["source"], "snapshot before.jsonl.gz")
        self.assertEqual(direct["before"]["source"], "database (RELEASE_BEFORE)")

    def test_same_database_twice_reports_no_change(self):
        dsn = _dsn(self.before, tenant=None)
        code, _, markdown, report = self._compare("env:A", "env:B", {"A": dsn, "B": dsn}, "same")
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"]["changed"], [])
        self.assertIn("**No figures changed.**", markdown)

    def test_a_rewritten_frozen_snapshot_needs_attention(self):
        code, _, markdown, report = self._compare(
            "env:A", "env:B", {"A": _dsn(self.before, tenant=None), "B": _dsn(self.tampered, tenant=None)}, "frozen")
        self.assertEqual(code, 1)
        attention = report["summary"]["attention"]
        self.assertEqual(len(attention), 2, attention)
        self.assertRegex(attention[0], r"^reporting_snapshots row [0-9a-f-]{36} changed or disappeared$")
        self.assertEqual(attention[1], "frozen or approved snapshots that no longer match their stored sha256: 1")
        self.assertEqual(report["summary"]["changed"], ["gold.reporting_snapshots"])
        self.assertIn("**Needs attention:**", markdown)

    def test_superuser_reads_every_tenant(self):
        dsn = _dsn(self.before, user=None, tenant=None)
        _, _, _, report = self._compare("env:A", "env:B", {"A": dsn, "B": _dsn(self.before, tenant=None)}, "rls")
        self.assertEqual(report["before"]["rows_read"], "all tenants")
        self.assertIn("different row-level security", report["summary"]["attention"][0])
        prices = report["tables"]["silver.benchmark_prices"]
        self.assertEqual((prices["rows"]["before"], prices["rows"]["after"]), (len(DATES) + 1, len(DATES)))

    def test_a_copy_from_before_the_layer_moves_reads_public(self):
        source = _report().database_source(_dsn(self.public, tenant=None), TENANT)
        header, tables = source
        entries = {entry["table"]: entry for entry in tables}
        self.assertEqual(entries["gold.performance_comparison_daily"]["location"], "public")
        self.assertEqual(entries["silver.nav_snapshots"]["key"],
                         ["account_id", "snapshot_date", "currency", "asset_class"])
        self.assertNotIn("070_medallion_silver_layer.sql", header["migrations"])

    def test_missing_variable_or_file_exits_2_without_writing(self):
        code, output = self._run("compare", "--before", "env:RELEASE_UNSET_VARIABLE", "--after", "missing.jsonl",
                                 "--markdown", str(self.folder / "unset.md"), "--json", str(self.folder / "unset.json"))
        self.assertEqual(code, 2)
        self.assertIn("RELEASE_UNSET_VARIABLE is not set", output)
        self.assertFalse((self.folder / "unset.md").exists())


if __name__ == "__main__":
    unittest.main()
