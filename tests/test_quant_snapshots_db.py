"""The gold quant analytics table, its job and its reader on a real PostgreSQL (docs/QUANT_ANALYTICS.md).

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped
otherwise. Builds its own databases with every migration, sets up the logins with
scripts/bootstrap_postgres.py, then runs the two held drafts as their day will number them:
quant_analytics.sql alone (076), then desk_live_views.sql alone (077). Synthetic data only.

QuantAnalyticsTests, one database, in order: the gold table alone with its grants and row security;
the Desk views alone and their rows; the job's full build of two made-up accounts (one of 60
holdings over three years, timed); an unchanged rerun and a one-input change; a bad day held back
whole by the publish gate; the reader; the time budget; lineage.
DraftOrderTests: each draft applied and undone in either order, leaving no trace.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from test_quant_snapshots import synthetic_data
from ibkr_etl import layer_freshness, lineage
from ibkr_etl import publish_gate as gate
from ibkr_etl import quant_snapshots as qs

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
DRAFTS = ROOT / "infra" / "medallion" / "drafts"
TENANT, OTHER_TENANT = layers.TENANT, layers.OTHER_TENANT
SUFFIX = uuid.uuid4().hex[:8]  # the test server is shared: roles of our own
APP, WEB, APPROVAL = f"quant_test_app_{SUFFIX}", f"quant_test_web_{SUFFIX}", f"quant_test_approval_{SUFFIX}"
ROLES = (APP, WEB, APPROVAL)
SMALL, LARGE = "U9100001", "U9100002"  # 8 holdings over 560 sessions; 60 over 756 (three years)
OTHER_ACCOUNT = "U9200001"  # another tenant
AS_OF = "2026-10-16"
BENCHMARK = "GSPC"
# On their day the held files take 076 and 077 (or the next free numbers, if others came first).
NEXT = max(max(int(path.name[:3]) for path in layers.MIGRATIONS.glob("*.sql")) + 1, 76)
QUANT_FILE, DESK_FILE = f"{NEXT:03d}_quant_analytics.sql", f"{NEXT + 1:03d}_desk_live_views.sql"
QUANT_OBJECTS = ("gold.quant_analytics_snapshots", "public.quant_analytics_snapshots")
DESK_OBJECTS = ("silver.subject_live_executions", "public.subject_live_executions",
                "silver.benchmark_live_quotes", "public.benchmark_live_quotes")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


def setUpModule():
    from ibkr_etl import db

    # The held files as their day will number them; nothing else changes.
    directory = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(directory.cleanup)
    migrations = Path(directory.name) / "postgres"
    migrations.mkdir()
    for path in layers.MIGRATIONS.glob("*.sql"):
        shutil.copy(path, migrations / path.name)
    shutil.copy(DRAFTS / "quant_analytics.sql", migrations / QUANT_FILE)
    shutil.copy(DRAFTS / "desk_live_views.sql", migrations / DESK_FILE)
    # CI's database job runs against the installed package: read the schema from this checkout.
    for name, value in (("POSTGRES_MIGRATIONS_DIR", migrations),
                        ("SQLITE_MIGRATION", ROOT / "migrations" / "001_init.sql")):
        patcher = patch.object(db, name, value)
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


def _bootstrap(database: str, *args) -> str:
    environment = {
        "DATABASE_ADMIN_URL": layers._url(database, layers.OWNER), "PORTFOLIO_TENANT_ID": TENANT,
        "DATABASE_APP_ROLE": APP, "DATABASE_APP_PASSWORD": layers.PASSWORD,
        "DATABASE_WEB_ROLE": WEB, "DATABASE_WEB_PASSWORD": layers.PASSWORD,
        "DATABASE_APPROVAL_ROLE": APPROVAL, "DATABASE_APPROVAL_PASSWORD": layers.PASSWORD,
    }
    output = io.StringIO()
    with patch.dict(os.environ, environment), contextlib.redirect_stdout(output):
        layers._script("bootstrap_postgres").main(list(args))
    return output.getvalue()


def _new_database(prefix: str) -> tuple[str, list[str]]:
    import psycopg

    database = f"{prefix}_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        for role in ROLES:
            admin.execute(f"DROP ROLE IF EXISTS {role}")
    applied = layers._build(database)
    # The logins as today's bootstrap leaves them; it applies nothing.
    _bootstrap(database, "--only-migration", applied[-1], "--allow-pending", f"{QUANT_FILE},{DESK_FILE}")
    return database, applied


def _rights(connection, role: str, name: str) -> list[str]:
    return [p for p in PRIVILEGES if connection.execute(
        "SELECT has_table_privilege(%s, %s::regclass, %s)", (role, name, p)).fetchone()[0]]


def _script():
    spec = importlib.util.spec_from_file_location("build_quant_analytics_under_test",
                                                  ROOT / "scripts" / "build_quant_analytics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- the synthetic accounts


def _accounts() -> dict[str, dict]:
    small = synthetic_data(holdings=8, days=560, seed=7)
    large = synthetic_data(holdings=60, days=756, seed=11, trade_every=2)
    for account, data in ((SMALL, small), (LARGE, large)):
        data.update(subject_id=account, benchmark_id=BENCHMARK)
    return {SMALL: small, LARGE: large}


def _shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def _as_loaded(data: dict, classified: dict[str, str]) -> dict:
    """What load_inputs should read back of `data`: the same rows, within its windows."""
    as_of = data["as_of"]
    start = min(_shift(as_of, -qs.MARK_WINDOW_DAYS), _shift(as_of[:4] + "-01-01", -15))
    positions = [row for row in data["positions"] if start <= row["date"] <= as_of]
    conids = {row["conid"] for row in positions}
    return {
        **data,
        "positions": positions,
        "nav": [row for row in data["nav"] if start <= row["date"] <= as_of],
        "trades": [row for row in data["trades"] if _shift(as_of, -qs.TRADE_WINDOW_DAYS) < row["trade_date"] <= as_of],
        "flows": [row for row in data["flows"] if start < row["date"] <= as_of],
        "fx": [row for row in data["fx"] if _shift(start, -qs.FX_TOLERANCE_DAYS - 1) <= row["date"] <= as_of],
        "classifications": {conid: sector for conid, sector in classified.items() if conid in conids},
    }


def _write(owner, accounts: dict[str, dict]) -> dict[str, str]:
    """The accounts' rows as the loads leave them in silver and gold; returns the classifications
    stored (one per instrument, shared by the tenant's accounts)."""
    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
    run = owner.execute("INSERT INTO ingestion_runs (run_type, started_at, completed_at, status) VALUES "
                        "('synthetic', '2026-10-16', '2026-10-16', 'success') RETURNING run_id").fetchone()[0]
    classified: dict[str, str] = {}
    instruments: dict[str, tuple] = {}
    for account, data in accounts.items():
        first = data["nav"][0]["date"]
        owner.execute("INSERT INTO silver.accounts (account_id, base_currency, first_seen_at, last_seen_at, "
                      "source_json) VALUES (%s, 'USD', %s, %s, '{}')", (account, first, AS_OF))
        owner.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, base_currency, "
                      "reporting_currency, history_start_date, lifecycle_status) VALUES (%s, 'physical_account', "
                      "'Synthetic', 'USD', 'USD', %s, 'active')", (account, first))
        owner.execute("INSERT INTO portfolio_benchmark_mapping (subject_id, benchmark_id, mapping_type, "
                      "effective_from, reporting_currency, created_at) VALUES (%s, %s, 'primary', '2020-01-01', "
                      "'USD', '2026-01-01')", (account, BENCHMARK))
        owner.execute("INSERT INTO subject_operating_registrations (registration_id, subject_id, capability, "
                      "operating_state, effective_from, configuration_sha256) VALUES (gen_random_uuid(), %s, "
                      "'benchmark_refresh', 'active', '2020-01-01', %s)", (account, "a" * 64))
        with owner.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO gold.performance_comparison_daily (subject_id, benchmark_id, observation_date, "
                "reporting_currency, portfolio_value, benchmark_level, portfolio_return, benchmark_return, "
                "calculation_version, quality_status, generated_at) VALUES (%s, %s, %s, 'USD', %s, %s, %s, %s, "
                "'benchmark-comparison-v1', 'pass', '2026-10-16T09:00:00+00:00')",
                [(account, BENCHMARK, row["date"], row["portfolio_value"], row["benchmark_level"],
                  row["portfolio_return"], row["benchmark_return"]) for row in data["comparison"]])
            # One statement for the daily marks (row security rules out COPY).
            columns = ("date", "conid", "currency", "quantity", "mark", "market_value")
            cursor.execute(
                "INSERT INTO silver.position_snapshots (account_id, snapshot_date, conid, currency, quantity_text, "
                "mark_price_text, market_value_text, row_sha256, last_seen_run_id) SELECT %s, d, c, cur, q, m, v, "
                "'p', %s FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[]) "
                "AS r(d, c, cur, q, m, v)",
                (account, run, *([row[column] for row in data["positions"]] for column in columns)))
            for row in data["positions"]:
                instruments.setdefault(row["conid"], (row["symbol"], row["currency"]))
            cursor.executemany(
                "INSERT INTO silver.nav_snapshots (account_id, snapshot_date, currency, total_text, row_sha256, "
                "last_seen_run_id) VALUES (%s, %s, 'USD', %s, 'n', %s)",
                [(account, row["date"], row["nav"], run) for row in data["nav"]])
            cursor.executemany(
                "INSERT INTO silver.trades (account_id, source_key, execution_id, trade_id, conid, trade_date, "
                "trade_time, buy_sell, quantity_text, trade_price_text, commission_text, commission_currency, "
                "currency, row_sha256, last_seen_run_id) VALUES (%s, %s, %s, %s, %s, %s, '10:00:00', %s, %s, %s, "
                "%s, %s, %s, 't', %s)",
                [(account, row["execution_id"], row["execution_id"], row["trade_id"], row["conid"],
                  row["trade_date"], row["side"], row["quantity"], row["price"], row["commission"],
                  row["commission_currency"], row["currency"], run) for row in data["trades"]])
            cursor.executemany(
                "INSERT INTO silver.cash_transactions (account_id, source_key, effective_date, transaction_type, "
                "description, amount_text, currency, row_sha256, last_seen_run_id) VALUES (%s, %s, %s, "
                "'Deposits/Withdrawals', 'Synthetic deposit', %s, %s, 'c', %s)",
                [(account, f"deposit-{row['date']}", row["date"], row["amount"], row["currency"], run)
                 for row in data["flows"]])
        for conid, sector in data["classifications"].items():
            classified.setdefault(conid, sector)
    with owner.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO silver.reference_fx_rates (rate_date, from_currency, to_currency, rate, provider, "
            "fetched_at, quality_status) VALUES (%s, %s, %s, %s, 'synthetic', '2026-10-16', 'pass')",
            [(row["date"], row["from"], row["to"], row["rate"]) for row in _fx_for(accounts)])
        cursor.executemany(
            "INSERT INTO silver.instruments (conid, symbol, asset_category, currency, first_seen_at, last_seen_at, "
            "source_json) VALUES (%s, %s, 'STK', %s, '2023-01-01', '2026-10-16', '{}')",
            [(conid, symbol, currency) for conid, (symbol, currency) in sorted(instruments.items())])
        cursor.executemany(
            "INSERT INTO silver.instrument_classifications (conid, provider, symbol, sector, status, quality_status, "
            "license_status, source_url, checked_at) VALUES (%s, 'synthetic', %s, %s, 'classified', 'pass', "
            "'approved', 'synthetic', '2026-10-16')",
            [(conid, instruments[conid][0], sector) for conid, sector in sorted(classified.items())])
    owner.execute("INSERT INTO silver.risk_free_rates (rate_date, series_id, currency, tenor, annual_rate, "
                  "day_count_convention, provider, fetched_at, quality_status) VALUES (%s, 'SYN', 'USD', '3M', 0.04, "
                  "'ACT/360', 'synthetic', '2026-10-16', 'pass')", (AS_OF,))
    # Another tenant's account, for the row security checks.
    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (OTHER_TENANT,))
    owner.execute("INSERT INTO silver.accounts (account_id, base_currency, first_seen_at, last_seen_at, source_json) "
                  "VALUES (%s, 'USD', '2026-01-01', %s, '{}')", (OTHER_ACCOUNT, AS_OF))
    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
    owner.commit()
    return classified


def _fx_for(accounts: dict[str, dict]) -> list[dict]:
    """The HKD rates as stored, one per day for the tenant: the two accounts' made-up rates differ,
    so the larger account's (its history covers the smaller's) where both have one."""
    fx = {row["date"]: row for row in accounts[SMALL]["fx"]}
    fx.update({row["date"]: row for row in accounts[LARGE]["fx"]})
    return [row for _, row in sorted(fx.items())]


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class QuantAnalyticsTests(unittest.TestCase):
    """One database through every step, in order (the tests share it)."""

    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {
            "IBKR_RUN_MIGRATIONS": "false", gate.MODE_ENV: "off", lineage.SWITCH_ENV: "",
            qs.BUILD_SETTING: "", qs.MINUTES_SETTING: "", "PORTFOLIO_TENANT_ID": TENANT})
        cls.environment.start()
        cls.database = None
        try:
            cls.database, _ = _new_database("quant_test")
            cls.accounts = _accounts()
            for data in cls.accounts.values():
                data["fx"] = _fx_for(cls.accounts)
            with layers._connect(cls.database) as owner:
                cls.classified = _write(owner, cls.accounts)
        except Exception:
            cls.tearDownClass()
            raise
        cls.app_url = layers._url(cls.database, APP)
        cls.web_url = layers._url(cls.database, WEB)

    @classmethod
    def tearDownClass(cls):
        if cls.database:
            layers._drop(cls.database, roles=ROLES)
        cls.environment.stop()

    def setUp(self):
        lineage.reset()

    # --- helpers ---

    def _owner(self, tenant: str = TENANT):
        return layers._connect(self.database, tenant=tenant)

    def _rows(self, sql: str, *parameters, user: str = layers.OWNER, tenant: str = TENANT):
        with layers._connect(self.database, user, tenant) as connection:
            return connection.execute(sql, parameters).fetchall()

    def _job(self, *args, environ: dict | None = None, entry: bool = False) -> tuple[int, dict, list[dict]]:
        """Run scripts/build_quant_analytics.py as the job's app login: (exit code, summary, lines)."""
        script = _script()
        settings = {qs.BUILD_SETTING: "on", "DATABASE_URL": self.app_url, **(environ or {})}
        output = io.StringIO()
        with patch.dict(os.environ, settings), contextlib.redirect_stdout(output):
            code = (script.entry if entry else script.main)(list(args))
        lines = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith("{")]
        return code, lines[-1], lines[:-1]

    def _connect(self, url: str):
        from ibkr_etl.db import connect

        connection = connect(Path("/tmp/unused.sqlite3"), url, TENANT)
        self.addCleanup(connection.close)
        return connection

    def _stored(self, subject: str, as_of: str = AS_OF) -> dict[str, tuple]:
        return {row[0]: row[1:] for row in self._rows(
            "SELECT section, source_data_sha256, payload_sha256, generated_at, quality_status, lineage_run_id "
            "FROM gold.quant_analytics_snapshots WHERE subject_id = %s AND as_of_date = %s", subject, as_of)}

    # --- in order ---

    def _freshness(self) -> dict:
        """The administrator's freshness results, through the approval login."""
        return {r["check_id"]: r for r in layer_freshness.run_read_only(
            layers._dsn(self.database, APPROVAL), TENANT, now=datetime(2026, 10, 16, 10, 30, tzinfo=timezone.utc),
            environ={})}

    def test_1_the_table_applies_alone_with_its_grants_and_row_security(self):
        # Before the migration the freshness rule waits for the table: no result, no incident.
        self.assertNotIn(gate.QUANT, self._freshness())
        output = _bootstrap(self.database, "--only-migration", QUANT_FILE, "--allow-pending", DESK_FILE)
        self.assertIn(f"Migrations applied: {QUANT_FILE}.", output)
        # Empty until the job runs: reported as missing, never late (not required while the job is off).
        self.assertEqual({key: self._freshness()[gate.QUANT][key] for key in ("state", "late")},
                         {"state": "missing", "late": False})
        with self._owner() as owner:
            kinds = dict(owner.execute(
                "SELECT n.nspname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = 'quant_analytics_snapshots'").fetchall())
            self.assertEqual(kinds, {"gold": "r", "public": "v"})
            self.assertEqual(owner.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                                           "WHERE oid = 'gold.quant_analytics_snapshots'::regclass").fetchone(),
                             (True, True))
            self.assertIn("security_invoker=true", owner.execute(
                "SELECT reloptions FROM pg_class WHERE oid = 'public.quant_analytics_snapshots'::regclass"
            ).fetchone()[0])
            self.assertEqual(owner.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = 'gold.quant_analytics_snapshots'::regclass "
                "AND NOT tgisinternal").fetchall(), [("lineage_stamp_run",)])
            self.assertIsNotNone(owner.execute("SELECT to_regclass('gold.quant_analytics_snapshots_lookup')"
                                               ).fetchone()[0])
            for name in QUANT_OBJECTS:
                with self.subTest(name=name):
                    self.assertEqual(_rights(owner, APP, name), ["SELECT", "INSERT", "UPDATE", "DELETE"])
                    self.assertEqual(_rights(owner, WEB, name), ["SELECT"])
                    self.assertEqual(_rights(owner, APPROVAL, name), [])
                    # The approval login counts rows by date (layer quality, freshness) and reads nothing else.
                    for column, allowed in (("as_of_date", True), ("payload_json", False), ("subject_id", False)):
                        self.assertEqual(owner.execute("SELECT has_column_privilege(%s, %s::regclass, %s, 'SELECT')",
                                                       (APPROVAL, name, column)).fetchone()[0], allowed, column)
            # The Desk views are a separate migration: not here yet.
            self.assertEqual(owner.execute("SELECT count(to_regclass(n)) FROM unnest(%s::text[]) n",
                                           (list(DESK_OBJECTS),)).fetchone()[0], 0)
        # Row security: another tenant's row is invisible, and no login writes across tenants.
        payload = {"section": "skill", "model_id": "skill-statistics", "model_version": "x", "as_of": AS_OF,
                   "available": False, "reason": "synthetic", "inputs": {}, "results": {}}
        other = self._connect(layers._url(self.database, layers.OWNER))
        other.execute("SELECT set_config('app.tenant_id', ?, false)", (OTHER_TENANT,))
        qs.store_section(other, tenant_id=OTHER_TENANT, subject_id=OTHER_ACCOUNT, as_of=AS_OF, payload=payload,
                         source_sha256="b" * 64, quality="warn", duration_ms=1.0, timings={})
        other.commit()
        for user in (APP, WEB):
            self.assertEqual(self._rows("SELECT count(*) FROM quant_analytics_snapshots", user=user), [(0,)])
        self.assertEqual(self._rows("SELECT subject_id FROM quant_analytics_snapshots", user=WEB, tenant=OTHER_TENANT),
                         [(OTHER_ACCOUNT,)])
        import psycopg

        app = self._connect(self.app_url)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            qs.store_section(app, tenant_id=OTHER_TENANT, subject_id=OTHER_ACCOUNT, as_of="2026-10-15",
                             payload=payload, source_sha256="b" * 64, quality="warn", duration_ms=1.0, timings={})
        app.rollback()

    def test_2_the_desk_views_apply_alone_and_return_the_right_rows(self):
        output = _bootstrap(self.database, "--only-migration", DESK_FILE)
        self.assertIn(f"Migrations applied: {DESK_FILE}.", output)
        now = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)
        with self._owner() as owner:
            for name in ("subject_live_executions", "benchmark_live_quotes"):
                kinds = dict(owner.execute(
                    "SELECT n.nspname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relname = %s", (name,)).fetchall())
                self.assertEqual(kinds, {"silver": "v", "public": "v"}, name)
                for schema in ("silver", "public"):
                    options = owner.execute("SELECT reloptions FROM pg_class WHERE oid = %s::regclass",
                                            (f"{schema}.{name}",)).fetchone()[0]
                    self.assertIn("security_invoker=true", options)
                    self.assertEqual(_rights(owner, WEB, f"{schema}.{name}"), ["SELECT"])
            for tenant, account, symbol in ((TENANT, SMALL, "SPY"), (OTHER_TENANT, OTHER_ACCOUNT, "QQQ")):
                owner.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
                owner.execute("INSERT INTO bronze.live_contracts (conid, symbol, sec_type, currency, is_benchmark, "
                              "refreshed_at) VALUES ('756733', %s, 'STK', 'USD', true, %s), "
                              "('265598', 'AAPL', 'STK', 'USD', false, %s)", (symbol, now, now))
                for minutes, price in ((0, "580.10"), (5, "581.25")):
                    owner.execute("INSERT INTO bronze.live_quotes (conid, observed_at, symbol, currency, last_price, "
                                  "bid_price, ask_price, prior_close, market_data_type) VALUES ('756733', %s, %s, "
                                  "'USD', %s, %s, %s, '579.00', 3)",
                                  (now + timedelta(minutes=minutes), symbol, price, price, price))
                owner.execute("INSERT INTO bronze.live_quotes (conid, observed_at, symbol, currency, last_price, "
                              "market_data_type) VALUES ('265598', %s, 'AAPL', 'USD', '231.00', 3)",
                              (now + timedelta(minutes=9),))
                for index, side in enumerate(("BUY", "SELL")):
                    owner.execute("INSERT INTO bronze.live_executions (exec_id, account_id, executed_at, conid, "
                                  "symbol, sec_type, currency, side, quantity, price, commission, "
                                  "commission_currency, first_seen_at, updated_at) VALUES (%s, %s, %s, '265598', "
                                  "'AAPL', 'STK', 'USD', %s, 10, 230.5, -1.0, 'USD', %s, %s)",
                                  (f"{account}.{index}", account, now + timedelta(minutes=index), side, now, now))
            owner.commit()
        for schema in ("silver", "public"):
            with self.subTest(schema=schema):
                fills = self._rows(f"SELECT subject_id, exec_id, side, quantity::text, price::text, commission::text "
                                   f"FROM {schema}.subject_live_executions ORDER BY executed_at", user=WEB)
                self.assertEqual(fills, [(SMALL, f"{SMALL}.0", "BUY", "10", "230.5", "-1.0"),
                                         (SMALL, f"{SMALL}.1", "SELL", "10", "230.5", "-1.0")])
                # The benchmark only, at its latest quote; another tenant's stays out of view.
                quotes = self._rows(f"SELECT conid, symbol, last_price::text, prior_close::text, market_data_type, "
                                    f"observed_at FROM {schema}.benchmark_live_quotes", user=WEB)
                self.assertEqual(quotes, [("756733", "SPY", "581.25", "579.00", 3, now + timedelta(minutes=5))])
                self.assertEqual(self._rows(f"SELECT symbol FROM {schema}.benchmark_live_quotes", user=WEB,
                                            tenant=OTHER_TENANT), [("QQQ",)])
        # The gold table is as the first migration left it.
        self.assertEqual(self._rows("SELECT count(*) FROM gold.quant_analytics_snapshots", tenant=OTHER_TENANT),
                         [(1,)])

    def test_3_the_job_builds_every_section_of_both_accounts(self):
        # The loader reads back exactly what was written, within its windows.
        app = self._connect(self.app_url)
        for account, data in self.accounts.items():
            with self.subTest(account=account):
                loaded = qs.load_inputs(app, tenant_id=TENANT, subject_id=account, as_of=AS_OF)
                self.assertEqual(loaded, _as_loaded(data, self.classified))
        app.rollback()

        code, summary, built = self._job()
        self.assertEqual(code, 0, summary)
        self.assertEqual((summary["status"], summary["subjects"], summary["rebuilt"], summary["sections_written"]),
                         ("success", 2, 2, 18))
        self.assertEqual([rule for rule in summary["quality_rules"] if not rule["passed"]], [])
        self.assertEqual(len(summary["quality_rules"]), 9)
        timings = summary["seconds_per_subject_date"]
        print(f"\nquant analytics build, seconds per subject (load, build and write): {timings}", file=sys.stderr)
        self.assertLess(timings[f"{LARGE}@{AS_OF}"], 60.0)  # 60 holdings over three years
        for line in built:
            self.assertEqual(line["errors"], {})
            print(f"  {line['subject_id'][-3:]} sections (ms): "
                  + ", ".join(f"{name} {section['build_ms']:.0f}" for name, section in line["sections"].items()),
                  file=sys.stderr)
        # Each stored payload is what the pure builders give on the loaded rows.
        for account, data in self.accounts.items():
            prepared = qs.prepare_inputs(_as_loaded(data, self.classified))
            stored = self._rows("SELECT section, model_id, model_version, payload_json, payload_sha256, "
                                "source_data_sha256, quality_status FROM quant_analytics_snapshots "
                                "WHERE subject_id = %s AND as_of_date = %s ORDER BY section", account, AS_OF,
                                user=WEB)
            self.assertEqual([row[0] for row in stored], sorted(qs.SECTION_NAMES))
            for section, model_id, model_version, payload, payload_sha, source_sha, quality in stored:
                with self.subTest(account=account, section=section):
                    expected, _ = qs.finite_copy(qs.BUILDERS[section](prepared[section]))
                    self.assertTrue(payload["available"], payload["reason"])
                    self.assertEqual(payload, json.loads(qs.canonical_json(expected)))
                    self.assertEqual((model_id, model_version), (expected["model_id"], expected["model_version"]))
                    self.assertEqual(source_sha, qs.section_fingerprint(section, prepared[section]))
                    self.assertEqual(payload_sha,
                                     hashlib.sha256(qs.canonical_json(expected).encode("utf-8")).hexdigest())
                    self.assertIn(quality, ("pass", "warn"))
        # The public view and the gold table hold the same rows; the other tenant's stays apart.
        self.assertEqual(self._rows("SELECT subject_id, section FROM public.quant_analytics_snapshots ORDER BY 1, 2",
                                    user=WEB),
                         self._rows("SELECT subject_id, section FROM gold.quant_analytics_snapshots ORDER BY 1, 2",
                                    user=WEB))
        self.assertEqual(len(self._rows("SELECT 1 FROM quant_analytics_snapshots", user=WEB)), 18)
        # The publish gate's dry run reads the job's table, and the freshness check finds it fresh,
        # through the approval login's one column.
        with patch.dict(os.environ, {"DATABASE_URL": self.app_url}), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(gate.main(["quant-analytics", "--since", "2026-01-01"]), 0)
        verdict = json.loads(out.getvalue())
        self.assertEqual([reason for reason in verdict["reasons"] if "could not" in reason], [])
        self.assertEqual(verdict["tables"][gate.QUANT]["rows"], 18)
        freshness = self._freshness()
        self.assertEqual((freshness[gate.QUANT]["state"], freshness[gate.QUANT]["observed"]), ("fresh", AS_OF))

    def test_4_an_unchanged_rerun_skips_and_a_changed_input_rebuilds_only_its_sections(self):
        before = {account: self._stored(account) for account in self.accounts}
        code, summary, built = self._job()
        self.assertEqual(code, 0, summary)
        self.assertEqual((summary["rebuilt"], summary["unchanged"], summary["sections_written"]), (0, 2, 0))
        self.assertEqual(built, [])
        self.assertEqual({account: self._stored(account) for account in self.accounts}, before)
        # A new sector for one instrument changes the holdings inputs only.
        with self._owner() as owner:
            owner.execute("UPDATE silver.instrument_classifications SET sector = 'Utilities' WHERE conid = '9001'")
        code, summary, built = self._job()
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary["sections_written"], 6)
        for line in built:
            rebuilt = {name for name, section in line["sections"].items() if section["status"] == "rebuilt"}
            self.assertEqual(rebuilt, {"holdings_risk", "correlation", "concentration"})
        after = {account: self._stored(account) for account in self.accounts}
        for account in self.accounts:
            for section in qs.SECTION_NAMES:
                changed = after[account][section][0] != before[account][section][0]
                self.assertEqual(changed, section in {"holdings_risk", "correlation", "concentration"}, section)

    def test_5_a_failing_day_is_held_back_whole_and_the_next_run_writes_it(self):
        original = qs.BUILDERS["execution"]

        def wrong_date(inputs):  # a payload that does not name its row's date: a contract rule fails
            return {**original(inputs), "as_of": "2000-01-01"}

        enforce = {gate.MODE_ENV: "enforce"}
        with patch.dict(qs.BUILDERS, {"execution": wrong_date}):
            code, summary, _ = self._job("--subject", SMALL, "--since", "2026-10-14", environ=enforce)
        self.assertEqual(code, gate.EXIT_REJECTED, summary)
        self.assertEqual(sorted(item["as_of"] for item in summary["held_back"]), ["2026-10-14", "2026-10-15"])
        self.assertEqual(summary["unchanged"], 1)  # 16 Oct: nothing new, so nothing to hold back
        # Not one of the eight good sections of either day was committed.
        self.assertEqual(self._rows("SELECT count(*) FROM quant_analytics_snapshots WHERE as_of_date < %s", AS_OF),
                         [(0,)])
        code, summary, _ = self._job("--subject", SMALL, "--since", "2026-10-14", environ=enforce)
        self.assertEqual(code, 0, summary)
        self.assertEqual((summary["rebuilt"], summary["sections_written"]), (2, 18))
        self.assertEqual(self._rows("SELECT as_of_date, count(*) FROM quant_analytics_snapshots WHERE subject_id = %s "
                                    "GROUP BY 1 ORDER BY 1", SMALL),
                         [("2026-10-14", 9), ("2026-10-15", 9), (AS_OF, 9)])

    def test_6_the_reader_takes_the_latest_good_section_on_or_before_the_date(self):
        web = self._connect(self.web_url)
        latest = qs.read_section(web, tenant_id=TENANT, subject_id=SMALL, section="tail")
        self.assertEqual((latest["as_of_date"], latest["payload"]["section"]), (AS_OF, "tail"))
        self.assertEqual(qs.read_section(web, tenant_id=TENANT, subject_id=SMALL, section="tail",
                                         as_of="2026-10-15")["as_of_date"], "2026-10-15")
        # A weekend date reads the Friday before; before the first date there is nothing.
        self.assertEqual(qs.read_section(web, tenant_id=TENANT, subject_id=LARGE, section="skill",
                                         as_of="2026-10-18")["as_of_date"], AS_OF)
        self.assertIsNone(qs.read_section(web, tenant_id=TENANT, subject_id=SMALL, section="tail",
                                          as_of="2026-10-13"))
        self.assertIsNone(qs.read_section(web, tenant_id=OTHER_TENANT, subject_id=OTHER_ACCOUNT, section="tail"))
        with self.assertRaises(ValueError):
            qs.read_section(web, tenant_id=TENANT, subject_id=SMALL, section="liquidity")
        # A failed row is never shown: the day before is.
        with self._owner() as owner:
            owner.execute("UPDATE gold.quant_analytics_snapshots SET quality_status = 'fail' WHERE subject_id = %s "
                          "AND as_of_date = %s AND section = 'tail'", (SMALL, AS_OF))
        try:
            self.assertEqual(qs.read_section(web, tenant_id=TENANT, subject_id=SMALL, section="tail")["as_of_date"],
                             "2026-10-15")
        finally:
            with self._owner() as owner:
                owner.execute("UPDATE gold.quant_analytics_snapshots SET quality_status = 'pass' WHERE subject_id = %s "
                              "AND as_of_date = %s AND section = 'tail'", (SMALL, AS_OF))

    def test_7_a_spent_time_budget_starts_nothing_new_and_says_what_it_skipped(self):
        with self._owner() as owner:
            owner.execute("DELETE FROM gold.quant_analytics_snapshots WHERE as_of_date = %s", (AS_OF,))
        code, summary, built = self._job("--minutes", "0.00001")
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary["status"], "budget_spent")
        self.assertEqual(sorted((item["subject_id"], item["as_of"], item["reason"])
                                for item in summary["skipped_for_time"]),
                         [(SMALL, AS_OF, "time budget spent"), (LARGE, AS_OF, "time budget spent")])
        self.assertEqual((built, summary["sections_written"]), ([], 0))
        self.assertEqual(self._rows("SELECT count(*) FROM quant_analytics_snapshots WHERE as_of_date = %s", AS_OF),
                         [(0,)])
        # One worker per subject and date: a date another session holds is left to it.
        other = self._connect(self.app_url)
        with qs.quant_date_lock(other, tenant_id=TENANT, subject_id=SMALL, as_of=AS_OF) as acquired:
            self.assertTrue(acquired)
            code, summary, built = self._job("--subject", SMALL)
        self.assertEqual(code, 1, summary)
        self.assertEqual((summary["status"], summary["blocked"][0]["reason"], built),
                         ("blocked", "another quant worker owns this date", []))
        # The setting, and its default of 8 minutes.
        self.assertEqual(qs.time_limit(environ={}), 8.0)
        self.assertEqual(qs.time_limit(environ={qs.MINUTES_SETTING: "3"}), 3.0)
        with self.assertRaises(ValueError):
            qs.time_limit(environ={qs.MINUTES_SETTING: "0"})

    def test_8_a_recorded_run_stamps_the_rows_it_writes(self):
        code, summary, _ = self._job(environ={lineage.SWITCH_ENV: "true"}, entry=True)
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary["sections_written"], 18)
        runs = self._rows("SELECT DISTINCT lineage_run_id FROM gold.quant_analytics_snapshots WHERE as_of_date = %s",
                          AS_OF)
        self.assertEqual(len(runs), 1)
        self.assertIsNotNone(runs[0][0])
        self.assertEqual(self._rows("SELECT DISTINCT job_name FROM lineage_events WHERE run_id = %s", runs[0][0]),
                         [("ibkrv2stg-quant-analytics",)])
        outputs = self._rows("SELECT name, row_count FROM lineage_io WHERE run_id = %s AND direction = 'output'",
                             runs[0][0])
        self.assertEqual(outputs, [(gate.QUANT, 18)])
        # Off, the job does nothing at all.
        script = _script()
        with patch.dict(os.environ, {qs.BUILD_SETTING: "off"}), \
                patch.object(script.lineage, "run_main", side_effect=AssertionError("lineage")), \
                patch.object(script, "connect", side_effect=AssertionError("connected")), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(script.entry([]), 0)
        self.assertIn('"status": "off"', printed.getvalue())


def _snapshot(connection) -> dict:
    """Everything a draft or its undo could leave behind, outside the data itself."""
    schemas = ["public", "bronze", "silver", "gold"]
    return {
        "relations": connection.execute(
            "SELECT n.nspname, c.relname, c.relkind, c.relacl::text, c.relrowsecurity, c.relforcerowsecurity, "
            "c.reloptions::text, obj_description(c.oid, 'pg_class') FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = ANY(%s) ORDER BY 1, 2", (schemas,)).fetchall(),
        "views": connection.execute("SELECT schemaname, viewname, definition FROM pg_views WHERE schemaname = ANY(%s) "
                                    "ORDER BY 1, 2", (schemas,)).fetchall(),
        "column_grants": connection.execute(
            "SELECT a.attrelid::regclass::text, a.attname, a.attacl::text FROM pg_attribute a JOIN pg_class c "
            "ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = ANY(%s) "
            "AND a.attacl IS NOT NULL ORDER BY 1, 2", (schemas,)).fetchall(),
        "policies": connection.execute("SELECT polrelid::regclass::text, polname FROM pg_policy ORDER BY 1, 2"
                                       ).fetchall(),
        "triggers": connection.execute("SELECT tgrelid::regclass::text, tgname FROM pg_trigger WHERE NOT tgisinternal "
                                       "ORDER BY 1, 2").fetchall(),
        "schemas": connection.execute("SELECT nspname, nspacl::text FROM pg_namespace WHERE nspname = ANY(%s) "
                                      "ORDER BY 1", (schemas,)).fetchall(),
        "default_privileges": connection.execute("SELECT defaclrole::regrole::text, defaclnamespace::regnamespace::text, "
                                                 "defaclobjtype, defaclacl::text FROM pg_default_acl ORDER BY 1, 2, 3"
                                                 ).fetchall(),
        "migrations": connection.execute("SELECT version, sha256 FROM schema_migrations ORDER BY 1").fetchall(),
    }


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class DraftOrderTests(unittest.TestCase):
    """Each held draft applied and undone on its own, in either order; each undo drops only its own
    objects, and both undone leave the database as it was."""

    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {"IBKR_RUN_MIGRATIONS": "false"})
        cls.environment.start()
        cls.database = None
        try:
            cls.database, _ = _new_database("quant_order_test")
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.database:
            layers._drop(cls.database, roles=ROLES)
        cls.environment.stop()

    def _present(self, owner, names) -> list[str]:
        return [name for name in names if owner.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0]]

    def _undo(self, name: str) -> None:
        with layers._connect(self.database) as owner:
            owner.execute((DRAFTS / name).read_text(encoding="utf-8"))

    def test_each_draft_applies_and_undoes_alone_in_either_order(self):
        drafts = {QUANT_FILE: ("quant_analytics_undo.sql", QUANT_OBJECTS),
                  DESK_FILE: ("desk_live_views_undo.sql", DESK_OBJECTS)}
        with layers._connect(self.database) as owner:
            before = _snapshot(owner)
        for first, second in ((QUANT_FILE, DESK_FILE), (DESK_FILE, QUANT_FILE)):
            with self.subTest(first=first):
                self.assertIn(f"Migrations applied: {first}.",
                              _bootstrap(self.database, "--only-migration", first, "--allow-pending", second))
                with layers._connect(self.database) as owner:
                    self.assertEqual(self._present(owner, drafts[first][1]), list(drafts[first][1]))
                    self.assertEqual(self._present(owner, drafts[second][1]), [])
                self.assertIn(f"Migrations applied: {second}.", _bootstrap(self.database, "--only-migration", second))
                # Undo the first: the second's objects stay, and still work for the website login.
                self._undo(drafts[first][0])
                with layers._connect(self.database) as owner:
                    self.assertEqual(self._present(owner, drafts[first][1]), [])
                    self.assertEqual(self._present(owner, drafts[second][1]), list(drafts[second][1]))
                with layers._connect(self.database, WEB) as web:
                    for name in drafts[second][1]:
                        web.execute(f"SELECT count(*) FROM {name}").fetchone()
                # Running an undo twice changes nothing.
                self._undo(drafts[first][0])
                self._undo(drafts[second][0])
                with layers._connect(self.database) as owner:
                    self.assertEqual(self._present(owner, QUANT_OBJECTS + DESK_OBJECTS), [])
                    # As each undo says: the ledger rows go by hand.
                    owner.execute("DELETE FROM schema_migrations WHERE version = ANY(%s)", ([first, second],))
                    owner.commit()
                    self.assertEqual(_snapshot(owner), before)

    def test_before_the_layers_both_drafts_refuse_and_leave_nothing(self):
        # A database before 068: the drafts need the layers (and 071's lineage stamp), so each
        # fails whole, in its own transaction.
        import psycopg

        database = f"quant_early_test_{uuid.uuid4().hex[:10]}"
        try:
            layers._build(database, stop_before_layers=True)
            with layers._connect(database) as owner:
                with self.assertRaisesRegex(psycopg.Error, "lineage_stamp_run"):
                    owner.execute((DRAFTS / "quant_analytics.sql").read_text(encoding="utf-8"))
                owner.rollback()
                with self.assertRaisesRegex(psycopg.Error, "needs the bronze and silver schemas"):
                    owner.execute((DRAFTS / "desk_live_views.sql").read_text(encoding="utf-8"))
                owner.rollback()
                self.assertEqual(self._present(owner, ["public.quant_analytics_snapshots",
                                                       "public.subject_live_executions",
                                                       "public.benchmark_live_quotes"]), [])
        finally:
            layers._drop(database)


if __name__ == "__main__":
    unittest.main()
