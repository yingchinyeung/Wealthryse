"""Phase 3 part A on a real PostgreSQL: the two held drafts, their loads and their undos
(docs/PHASE3_DATA.md).

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped
otherwise. Builds its own databases with every migration, sets up the logins with
scripts/bootstrap_postgres.py, then runs each held draft alone as its day will number it.
Synthetic data only; every capture and download is a fake, and the network is shut.

Phase3TablesTests, one database, in order: both jobs held (exit 65) before the migrations; the
volumes table alone, with the grants market_data_prices has and row security; its load from
captures (idempotent, the newest capture winning, an older one never replacing a row, backfill
dates); the factor table alone; its load with vintages kept and an unchanged rerun writing
nothing; the ETF stand-ins only with the flag and only after the library's latest day; row
security, constraints and contract rules on both; lineage.
DraftUndoTests: each draft applied and undone alone in either order, leaving no trace, and both
drafts on a database without the layers.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from test_phase3_volumes_factors import (FIVE_FACTOR_CSV, MOMENTUM_CSV, _Bronze, _Exploding, _Uploads, chart,
                                         download, french_zip)
from ibkr_etl import daily_volumes as dv
from ibkr_etl import factor_returns as fr
from ibkr_etl import fama_french, layer_freshness, lineage, raw_capture

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
DRAFTS = ROOT / "infra" / "medallion" / "drafts"
CONTRACTS = ROOT / "infra" / "medallion" / "contracts"
TENANT, OTHER_TENANT = layers.TENANT, layers.OTHER_TENANT
SUFFIX = uuid.uuid4().hex[:8]  # the test server is shared: roles of our own
APP, WEB, APPROVAL = f"p3a_test_app_{SUFFIX}", f"p3a_test_web_{SUFFIX}", f"p3a_test_approval_{SUFFIX}"
ROLES = (APP, WEB, APPROVAL)
# On their days the held files take the next free numbers (after 076 and 077, the quant drafts).
NEXT = max(max(int(path.name[:3]) for path in layers.MIGRATIONS.glob("*.sql")) + 1, 78)
VOLUMES_FILE, FACTORS_FILE = f"{NEXT:03d}_daily_volumes.sql", f"{NEXT + 1:03d}_factor_returns.sql"
VOLUMES_OBJECTS = ("silver.daily_volumes", "public.daily_volumes")
FACTORS_OBJECTS = ("silver.factor_returns", "public.factor_returns")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
CONTAINER = "bronze-market-data"
SPY, HSI = "spy-settlement", "hang-seng-price-index"
NOW = datetime(2026, 10, 19, 6, 0, tzinfo=timezone.utc)  # the factor job's Saturday-morning run, pinned
US_FIVE, US_MOMENTUM = (fama_french.BASE_URL + name for name in fama_french.REGIONS["us"][1:])


def setUpModule():
    from ibkr_etl import db

    for target in ("urllib.request.urlopen", "ibkr_etl.cloud_storage.container_client"):
        patcher = patch(target, side_effect=AssertionError(f"network: {target}"))
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)
    # The held files as their day will number them; nothing else changes.
    directory = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(directory.cleanup)
    migrations = Path(directory.name) / "postgres"
    migrations.mkdir()
    for path in layers.MIGRATIONS.glob("*.sql"):
        shutil.copy(path, migrations / path.name)
    shutil.copy(DRAFTS / "daily_volumes.sql", migrations / VOLUMES_FILE)
    shutil.copy(DRAFTS / "factor_returns.sql", migrations / FACTORS_FILE)
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


def _new_database(prefix: str) -> str:
    import psycopg

    database = f"{prefix}_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        for role in ROLES:
            admin.execute(f"DROP ROLE IF EXISTS {role}")
    applied = layers._build(database)
    # The logins as today's bootstrap leaves them; it applies nothing.
    _bootstrap(database, "--only-migration", applied[-1], "--allow-pending", f"{VOLUMES_FILE},{FACTORS_FILE}")
    return database


def _rights(connection, role: str, name: str) -> list[str]:
    return [p for p in PRIVILEGES if connection.execute(
        "SELECT has_table_privilege(%s, %s::regclass, %s)", (role, name, p)).fetchone()[0]]


def _column(connection, role: str, name: str, column: str) -> bool:
    return connection.execute("SELECT has_column_privilege(%s, %s::regclass, %s, 'SELECT')",
                              (role, name, column)).fetchone()[0]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_db_under_test", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(connection) -> dict:
    """Everything a draft or its undo could leave behind, outside the data itself."""
    schemas = ["public", "bronze", "silver", "gold"]
    return {
        "relations": connection.execute(
            "SELECT n.nspname, c.relname, c.relkind, c.relacl::text, c.relrowsecurity, c.relforcerowsecurity, "
            "c.reloptions::text, obj_description(c.oid, 'pg_class') FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = ANY(%s) ORDER BY 1, 2", (schemas,)).fetchall(),
        "column_grants": connection.execute(
            "SELECT a.attrelid::regclass::text, a.attname, a.attacl::text FROM pg_attribute a JOIN pg_class c "
            "ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = ANY(%s) "
            "AND a.attacl IS NOT NULL ORDER BY 1, 2", (schemas,)).fetchall(),
        "constraints": connection.execute("SELECT conrelid::regclass::text, conname FROM pg_constraint "
                                          "ORDER BY 1, 2").fetchall(),
        "policies": connection.execute("SELECT polrelid::regclass::text, polname FROM pg_policy ORDER BY 1, 2"
                                       ).fetchall(),
        "triggers": connection.execute("SELECT tgrelid::regclass::text, tgname FROM pg_trigger WHERE NOT tgisinternal "
                                       "ORDER BY 1, 2").fetchall(),
        "types": connection.execute("SELECT typnamespace::regnamespace::text, typname FROM pg_type t JOIN "
                                    "pg_namespace n ON n.oid = t.typnamespace WHERE n.nspname = ANY(%s) "
                                    "ORDER BY 1, 2", (schemas,)).fetchall(),
        "migrations": connection.execute("SELECT version, sha256 FROM schema_migrations ORDER BY 1").fetchall(),
    }


def _present(connection, names) -> list[str]:
    return [name for name in names if connection.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0]]


# ---------------------------------------------------------------- synthetic inputs


def _seed_prices(owner) -> None:
    """Chart rows of market_data_prices as fetch_daily_closes leaves them (and two that must be ignored)."""
    rows = (
        (TENANT, SPY, "SPY", "SPDR S&P 500 ETF Trust", "unadjusted_close", "USD", dv.CHART_SOURCE),
        (TENANT, HSI, "^HSI", "Hang Seng Index", "price_index_close", "HKD", dv.CHART_SOURCE),
        (TENANT, "manual-fund", "MANU", "Manual fund", "nav", "USD", "manual upload"),
        (OTHER_TENANT, "other-spy", "SPY", "SPDR S&P 500 ETF Trust", "unadjusted_close", "USD", dv.CHART_SOURCE),
    )
    for tenant, instrument, symbol, name, price_type, currency, source in rows:
        owner.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        for day, price in (("2026-10-14", 579.5), ("2026-10-15", 582.0)):
            owner.execute("INSERT INTO silver.market_data_prices (instrument_id, price_date, symbol, instrument_name, "
                          "price_type, currency, price, source, source_url, fetched_at) VALUES (%s, %s, %s, %s, %s, "
                          "%s, %s, %s, 'https://query1.finance.yahoo.com/v8/finance/chart/x', "
                          "'2026-10-16T08:50:00+00:00')", (instrument, day, symbol, name, price_type, currency,
                                                          price, source))
    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
    owner.commit()


def _at(day: str, hour: int = 8, minute: int = 50) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=timezone.utc)


def _spy(bars) -> bytes:
    return chart([(f"{day}T13:30:00", 580.0, volume) for day, volume in bars])


def _hsi(bars) -> bytes:
    return chart([(f"{day}T01:30:00", 25_000.0, volume) for day, volume in bars], symbol="^HSI", currency="HKD",
                 zone="Asia/Hong_Kong")


def _files(five: str, momentum: str, last_modified: str) -> dict[str, fr.Download]:
    return {US_FIVE: download(US_FIVE, french_zip(five), last_modified=last_modified, requested_at=NOW),
            US_MOMENTUM: download(US_MOMENTUM, french_zip(momentum, "F-F_Momentum_Factor_daily.CSV"),
                                  last_modified=last_modified, requested_at=NOW)}


V1 = "Fri, 02 Oct 2026 21:40:12 GMT"
V2 = "Tue, 13 Oct 2026 22:00:00 GMT"
# The September file: one value of 31 August revised (Mkt-RF 0.07 -> 0.09) and 30 September added.
FIVE_V2 = FIVE_FACTOR_CSV.replace("20260831,   0.07", "20260831,   0.09").replace(
    "\n\n Annual", "\n20260930,   0.40,  -0.10,   0.20,   0.10,   0.00,  0.016\n\n Annual")
MOMENTUM_V2 = MOMENTUM_CSV.replace("20260831, -99.99\n", "20260831, -99.99\n20260930,   0.12\n")
# Published the same day the stand-ins were stored (their vintage), covering two of their days.
FIVE_V3 = FIVE_V2.replace("\n\n Annual", "\n20261001,   0.30,   0.20,  -0.10,   0.00,   0.05,  0.016\n"
                                         "20261002,  -0.20,   0.10,   0.00,   0.10,   0.00,  0.016\n\n Annual")
V3 = "Mon, 19 Oct 2026 05:00:00 GMT"


def _proxies(calls: list[str]):
    def proxies(region):
        calls.append(region)
        row = {"Mkt-RF": 0.0031, "SMB": 0.0019, "HML": -0.0011, "RMW": 0.0002, "CMA": 0.0004,
               "RF": 0.016 / 100, "MOM": 0.0008}
        return {"factors": {"2026-09-29": dict(row), "2026-09-30": dict(row),
                            "2026-10-01": {**row, "XYZ": 1.0}, "2026-10-02": dict(row)}}
    return proxies


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class Phase3TablesTests(unittest.TestCase):
    """One database through every step, in order (the tests share it)."""

    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {
            "IBKR_RUN_MIGRATIONS": "false", lineage.SWITCH_ENV: "", dv.SETTING: "", fr.SETTING: "",
            "PORTFOLIO_TENANT_ID": TENANT, raw_capture.CONTAINER_ENV: "", raw_capture.ACCOUNT_URL_ENV: ""})
        cls.environment.start()
        cls.database = None
        try:
            cls.database = _new_database("p3a_test")
            with layers._connect(cls.database) as owner:
                _seed_prices(owner)
        except Exception:
            cls.tearDownClass()
            raise
        cls.app_url = layers._url(cls.database, APP)
        cls.bronze = _Bronze()
        cls.today = datetime.now(timezone.utc).date()

    @classmethod
    def tearDownClass(cls):
        if cls.database:
            layers._drop(cls.database, roles=ROLES)
        cls.environment.stop()

    def setUp(self):
        lineage.reset()
        self.uploads = _Uploads()
        raw_capture.reset(raw_capture.RawCapture(container=CONTAINER, account_url="https://example.blob.core.windows.net",
                                                 uploader_factory=self.uploads.factory))
        self.addCleanup(raw_capture.reset)

    # --- helpers ---

    def _rows(self, sql: str, *parameters, user: str = layers.OWNER, tenant: str = TENANT):
        with layers._connect(self.database, user, tenant) as connection:
            return connection.execute(sql, parameters).fetchall()

    def _job(self, name: str, *args, **kwargs):
        """Run a loader as its job's app login: (exit code, summary, the lines before it)."""
        script = _script(name)
        setting = dv.SETTING if name == "load_daily_volumes" else fr.SETTING
        output = io.StringIO()
        with patch.dict(os.environ, {setting: "on", "DATABASE_URL": self.app_url}), \
                patch.object(raw_capture, "utc_now", return_value=NOW), contextlib.redirect_stdout(output):
            code = script.main(list(args), **kwargs)
        lines = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith("{")]
        return code, lines[-1], lines[:-1]

    def _volumes(self, *args, **kwargs):
        return self._job("load_daily_volumes", *args, bronze=self.bronze, **kwargs)

    def _factors(self, files: dict[str, fr.Download], *args, **kwargs):
        fetched = []

        def fetch(url):
            fetched.append(url)
            return files[url]

        result = self._job("load_factor_returns", "--region", "us", *args, fetch=fetch, **kwargs)
        self.assertEqual(fetched, [US_FIVE, US_MOMENTUM])
        return result

    def _freshness(self) -> dict:
        return {r["check_id"]: r for r in layer_freshness.run_read_only(
            layers._dsn(self.database, APPROVAL), TENANT, now=datetime(2026, 10, 19, 10, 30, tzinfo=timezone.utc),
            environ={})}

    def _applies_alone(self, file: str, objects: tuple[str, str], mirror: str, date_column: str, *pending: str):
        output = _bootstrap(self.database, "--only-migration", file, *(("--allow-pending", ",".join(pending))
                                                                       if pending else ()))
        self.assertIn(f"Migrations applied: {file}.", output)
        name = objects[0].split(".")[1]
        with layers._connect(self.database) as owner:
            kinds = dict(owner.execute("SELECT n.nspname, c.relkind FROM pg_class c JOIN pg_namespace n ON "
                                       "n.oid = c.relnamespace WHERE c.relname = %s", (name,)).fetchall())
            self.assertEqual(kinds, {"silver": "r", "public": "v"})
            self.assertEqual(owner.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = "
                                           "%s::regclass", (objects[0],)).fetchone(), (True, True))
            self.assertIn("security_invoker=true", owner.execute(
                "SELECT reloptions FROM pg_class WHERE oid = %s::regclass", (objects[1],)).fetchone()[0])
            self.assertEqual(owner.execute("SELECT polname FROM pg_policy WHERE polrelid = %s::regclass",
                                           (objects[0],)).fetchall(), [("tenant_isolation",)])
            # The grants the market-data silver table has, from bootstrap; the draft gives none. The
            # approval login reads market_data_prices whole for the reference-market approvals (a grant
            # bootstrap names it in); here it gets only the count column every silver table gives it.
            for schema in ("silver", "public"):
                for role in (APP, WEB):
                    with self.subTest(object=f"{schema}.{name}", role=role):
                        self.assertEqual(_rights(owner, role, f"{schema}.{name}"),
                                         _rights(owner, role, f"{schema}.{mirror}"))
                with self.subTest(object=f"{schema}.{name}", role=APPROVAL):
                    self.assertEqual(_rights(owner, APPROVAL, f"{schema}.{name}"), [])
                    self.assertEqual(_column(owner, APPROVAL, f"{schema}.{name}", date_column),
                                     _column(owner, APPROVAL, f"{schema}.{mirror}", "price_date"))
            self.assertEqual(_rights(owner, APP, objects[0]), ["SELECT", "INSERT", "UPDATE", "DELETE"])
            self.assertEqual(_rights(owner, WEB, objects[0]), ["SELECT"])
            self.assertEqual(_rights(owner, APPROVAL, objects[0]), [])
            self.assertTrue(_column(owner, APPROVAL, objects[0], date_column))
            self.assertFalse(_column(owner, APPROVAL, objects[0], "value" if name == "factor_returns" else "volume"))
        self.assertEqual(_bootstrap(self.database, "--only-migration", file, *(
            ("--allow-pending", ",".join(pending)) if pending else ())).count("Migrations applied: none"), 1)

    def _contract_rules(self, name: str) -> None:
        from ibkr_etl import medallion_contracts as mc

        obj = mc.table_object(mc.load(mc.contract_path(CONTRACTS, "silver", name)))
        rules = list(obj.get("quality") or [])
        for prop in obj["properties"]:
            rules += prop.get("quality") or []
        self.assertTrue(rules)
        with layers._connect(self.database) as owner:
            for rule in rules:
                with self.subTest(table=name, rule=rule["name"]):
                    self.assertEqual(owner.execute(rule["query"]).fetchone()[0], rule["mustBe"])

    # --- in order ---

    def test_1_before_the_migrations_both_jobs_are_held_having_read_nothing(self):
        code, summary, lines = self._job("load_daily_volumes", bronze=_Exploding())
        self.assertEqual((code, summary["status"], lines), (65, "held", []))
        code, summary, lines = self._job("load_factor_returns", "--etf-proxy", fetch=_Exploding(),
                                         proxies=_Exploding())
        self.assertEqual((code, summary["status"], lines), (65, "held", []))
        self.assertEqual(self.uploads.calls, [])
        # The freshness rules wait for their tables: no result, no incident.
        found = self._freshness()
        self.assertNotIn("silver.daily_volumes", found)
        self.assertNotIn("silver.factor_returns", found)

    def test_2_the_volumes_table_applies_alone_with_its_grants_and_row_security(self):
        self._applies_alone(VOLUMES_FILE, VOLUMES_OBJECTS, "market_data_prices", "price_date", FACTORS_FILE)
        with layers._connect(self.database) as owner:
            self.assertEqual(_present(owner, FACTORS_OBJECTS), [])
        # Empty until the job runs: missing, never late (not required while the job is off).
        self.assertEqual({key: self._freshness()["silver.daily_volumes"][key] for key in ("state", "late")},
                         {"state": "missing", "late": False})
        # The factor job is still held.
        code, summary, _ = self._job("load_factor_returns", fetch=_Exploding())
        self.assertEqual((code, summary["status"]), (65, "held"))

    def test_3_volumes_load_from_the_captures_and_a_rerun_writes_nothing(self):
        bronze = self.bronze
        bronze.add("2026/10/15", "SPY_2025-01-13_2026-10-15_1d",
                   _spy([("2026-10-13", 100_000), ("2026-10-14", 200_000)]), _at("2026-10-15"))
        newer = bronze.add("2026/10/16", "SPY_2025-01-13_2026-10-16_1d",
                           _spy([("2026-10-14", 210_000), ("2026-10-15", 300_000)]), _at("2026-10-16"))
        bronze.add("2026/10/16", "HSI_2025-01-13_2026-10-16_1d",
                   _hsi([("2026-10-15", 2_100_000_000), ("2026-10-16", None)]), _at("2026-10-16"))
        bronze.add("2026/10/16", "SPYG_2025-01-13_2026-10-16_1d",
                   chart([("2026-10-15T13:30:00", 70.0, 5)], symbol="SPYG"), _at("2026-10-16"))
        code, summary, lines = self._volumes("--from", "2026-10-01")
        self.assertEqual(code, 0, summary)
        self.assertEqual((summary["status"], summary["mode"], summary["rows"], summary["written"]),
                         ("success", "backfill", 4, 4))
        # Only this tenant's chart instruments; the manual row and the other tenant's are not read.
        self.assertEqual(sorted(line["instrument_id"] for line in lines), [HSI, SPY])
        self.assertEqual({line["instrument_id"]: (line["captures"], line["skipped_days"]) for line in lines},
                         {SPY: (2, 0), HSI: (1, 1)})
        stored = self._rows("SELECT instrument_id, price_date, volume, source, fetched_at FROM daily_volumes "
                            "ORDER BY 1, 2", user=WEB)
        self.assertEqual(stored, [
            (HSI, "2026-10-15", 2_100_000_000.0, "yahoo-chart", "2026-10-16T08:50:00+00:00"),
            (SPY, "2026-10-13", 100_000.0, "yahoo-chart", "2026-10-15T08:50:00+00:00"),
            (SPY, "2026-10-14", 210_000.0, "yahoo-chart", "2026-10-16T08:50:00+00:00"),  # the newer capture
            (SPY, "2026-10-15", 300_000.0, "yahoo-chart", "2026-10-16T08:50:00+00:00")])
        self.assertEqual(self._rows("SELECT source_url FROM daily_volumes WHERE price_date = '2026-10-15' AND "
                                    "instrument_id = %s", SPY),
                         [(f"https://query1.finance.yahoo.com/v8/finance/chart/{newer[-30:]}",)])
        # The same captures again: nothing changes.
        code, summary, _ = self._volumes("--from", "2026-10-01")
        self.assertEqual((code, summary["rows"], summary["written"]), (0, 4, 0))
        # A newer capture: one day revised, one added.
        bronze.add("2026/10/17", "SPY_2025-01-13_2026-10-17_1d",
                   _spy([("2026-10-15", 305_000), ("2026-10-16", 280_000)]), _at("2026-10-17"))
        code, summary, _ = self._volumes("--from", "2026-10-15", "--to", "2026-10-16", "--instrument", SPY)
        self.assertEqual((code, summary["rows"], summary["written"], summary["since"], summary["until"]),
                         (0, 2, 2, "2026-10-15", "2026-10-16"))
        self.assertEqual(self._rows("SELECT price_date, volume FROM daily_volumes WHERE instrument_id = %s "
                                    "AND price_date >= '2026-10-15' ORDER BY 1", SPY),
                         [("2026-10-15", 305_000.0), ("2026-10-16", 280_000.0)])
        # The daily run reads today's folder. A capture requested earlier than the row it would
        # replace never replaces it, whatever folder it sits in.
        bronze.add(f"{self.today:%Y/%m/%d}", "SPY_2025-01-13_stale_1d", _spy([("2026-10-14", 1)]),
                   _at("2026-10-10"))
        code, summary, lines = self._volumes("--days", "1")
        self.assertEqual((code, summary["mode"], summary["rows"], summary["written"]), (0, "daily", 1, 0))
        self.assertEqual(self._rows("SELECT volume FROM daily_volumes WHERE instrument_id = %s AND "
                                    "price_date = '2026-10-14'", SPY), [(210_000.0,)])
        self.assertEqual(summary["without_captures"], [HSI])
        # No capture at all says so, and is not a failure.
        code, summary, _ = self._volumes("--days", "1", "--instrument", HSI)
        self.assertEqual((code, summary["status"]), (0, "no_captures"))
        # An unreadable capture is reported and fails the run; the others are still written.
        bronze.add(f"{self.today:%Y/%m/%d}", "HSI_2025-01-13_broken_1d", b"<html>busy</html>", _at("2026-10-18"))
        code, summary, lines = self._volumes("--days", "1")
        self.assertEqual((code, summary["status"], summary["unreadable_captures"]), (1, "failed", 1))

    def test_4_volumes_row_security_and_constraints(self):
        import psycopg

        with layers._connect(self.database, tenant=OTHER_TENANT) as owner:
            owner.execute("INSERT INTO daily_volumes (instrument_id, price_date, volume, source, source_url, "
                          "fetched_at) VALUES ('other-spy', '2026-10-15', 1, 'yahoo-chart', 'x', '2026-10-16T08:50')")
        self.assertEqual(self._rows("SELECT instrument_id FROM daily_volumes", user=WEB, tenant=OTHER_TENANT),
                         [("other-spy",)])
        self.assertNotIn(("other-spy",), self._rows("SELECT instrument_id FROM daily_volumes", user=WEB))
        self.assertEqual(self._rows("SELECT count(*) FROM daily_volumes", user=WEB, tenant=str(uuid.uuid4())), [(0,)])
        with layers._connect(self.database, APP) as app:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                app.execute("INSERT INTO daily_volumes (tenant_id, instrument_id, price_date, volume, source, "
                            "source_url, fetched_at) VALUES (%s, 'x', '2026-10-15', 1, 's', 'x', '2026-10-16T08:50')",
                            (OTHER_TENANT,))
        for column, value in (("volume", -1), ("volume", float("nan")), ("volume", float("inf")),
                              ("price_date", "2026/10/15"), ("fetched_at", "yesterday")):
            with self.subTest(column=column, value=value), layers._connect(self.database) as owner:
                row = {"instrument_id": "x", "price_date": "2026-10-15", "volume": 1, "source": "s",
                       "source_url": "x", "fetched_at": "2026-10-16T08:50", column: value}
                with self.assertRaises(psycopg.errors.CheckViolation):
                    owner.execute("INSERT INTO daily_volumes (instrument_id, price_date, volume, source, source_url, "
                                  "fetched_at) VALUES (%(instrument_id)s, %(price_date)s, %(volume)s, %(source)s, "
                                  "%(source_url)s, %(fetched_at)s)", row)
        self._contract_rules("daily_volumes")
        self.assertEqual(self._freshness()["silver.daily_volumes"]["observed"], "2026-10-16")

    def test_5_the_factor_table_applies_alone(self):
        self._applies_alone(FACTORS_FILE, FACTORS_OBJECTS, "market_data_prices", "return_date")
        with layers._connect(self.database) as owner:
            self.assertEqual(_present(owner, VOLUMES_OBJECTS), list(VOLUMES_OBJECTS))  # untouched
        self.assertEqual({key: self._freshness()["silver.factor_returns"][key] for key in ("state", "late")},
                         {"state": "missing", "late": False})

    def test_6_factor_vintages_are_kept_and_an_unchanged_file_writes_nothing(self):
        code, summary, [region] = self._factors(_files(FIVE_FACTOR_CSV, MOMENTUM_CSV, V1))
        self.assertEqual(code, 0, summary)
        # 17 five-factor values (31 Aug's HML is the library's missing marker) and 2 momentum.
        self.assertEqual((summary["written"], region["rows"], region["written"]), (19, 19, 19))
        self.assertEqual([(f["vintage"], f["first_date"], f["latest_date"]) for f in region["files"]],
                         [("2026-10-02", "2026-08-27", "2026-08-31"), ("2026-10-02", "2026-08-27", "2026-08-28")])
        # Each file was stored first, as received, with its licence; the run names them.
        self.assertEqual([call["name"].split("/")[:2] for call in self.uploads.calls],
                         [["kenneth-french", "history"], ["kenneth-french", "history"]])
        self.assertEqual({call["metadata"]["dataLicense"] for call in self.uploads.calls},
                         {"kenneth-french-terms-unchecked-internal-only"})
        self.assertEqual(summary["raw_capture"]["written"], 2)
        self.assertEqual(self._rows("SELECT factor, value, source, vintage, fetched_at FROM factor_returns WHERE "
                                    "return_date = '2026-08-27' AND factor IN ('Mkt-RF', 'MOM') ORDER BY 1", user=WEB),
                         [("MOM", 0.33 / 100, "kenneth-french", "2026-10-02", "2026-10-19T06:00:00+00:00"),
                          ("Mkt-RF", 0.52 / 100, "kenneth-french", "2026-10-02", "2026-10-19T06:00:00+00:00")])
        # The same file again: nothing written.
        code, summary, _ = self._factors(_files(FIVE_FACTOR_CSV, MOMENTUM_CSV, V1))
        self.assertEqual((code, summary["written"]), (0, 0))
        # The next month's file: the new vintage holds only what it added or changed.
        code, summary, [region] = self._factors(_files(FIVE_V2, MOMENTUM_V2, V2))
        self.assertEqual((code, summary["written"], region["new_or_changed"]), (0, 8, 8))
        self.assertEqual(self._rows("SELECT vintage, count(*) FROM factor_returns GROUP BY 1 ORDER BY 1"),
                         [("2026-10-02", 19), ("2026-10-13", 8)])
        latest = "SELECT DISTINCT ON (factor, return_date) value FROM factor_returns WHERE region = 'us' AND " \
                 "factor = 'Mkt-RF' AND return_date = '2026-08-31' AND vintage <= %s ORDER BY factor, return_date, " \
                 "vintage DESC"
        self.assertEqual(self._rows(latest, "9999-12-31", user=WEB), [(0.09 / 100,)])
        self.assertEqual(self._rows(latest, "2026-10-02", user=WEB), [(0.07 / 100,)])  # as first published
        self.assertEqual(self._rows("SELECT count(*) FROM (SELECT DISTINCT factor, return_date FROM factor_returns) d"),
                         [(26,)])
        code, summary, _ = self._factors(_files(FIVE_V2, MOMENTUM_V2, V2))
        self.assertEqual((code, summary["written"]), (0, 0))
        # The older file published again changes nothing either.
        code, summary, _ = self._factors(_files(FIVE_FACTOR_CSV, MOMENTUM_CSV, V1))
        self.assertEqual((code, summary["written"]), (0, 0))

    def test_7_the_stand_ins_fill_only_the_days_after_the_library_and_only_when_asked(self):
        calls = []
        code, summary, [region] = self._factors(_files(FIVE_V2, MOMENTUM_V2, V2), proxies=_proxies(calls))
        self.assertEqual((code, calls, summary["etf_proxy"]), (0, [], False))
        self.assertNotIn("etf_proxy", region)
        code, summary, [region] = self._factors(_files(FIVE_V2, MOMENTUM_V2, V2), "--etf-proxy",
                                                proxies=_proxies(calls))
        self.assertEqual((code, calls), (0, ["us"]), summary)
        # After 30 September, the library's latest: two days of the seven factors (XYZ dropped).
        self.assertEqual({key: region["etf_proxy"][key] for key in ("after", "days", "written")},
                         {"after": "2026-09-30", "days": 2, "written": 14})
        self.assertEqual(self._rows("SELECT return_date, source, vintage, count(*) FROM factor_returns WHERE "
                                    "source = 'etf-proxy' GROUP BY 1, 2, 3 ORDER BY 1"),
                         [("2026-10-01", "etf-proxy", "2026-10-19", 7), ("2026-10-02", "etf-proxy", "2026-10-19", 7)])
        # Again the same day: nothing new.
        code, summary, [region] = self._factors(_files(FIVE_V2, MOMENTUM_V2, V2), "--etf-proxy",
                                                proxies=_proxies(calls))
        self.assertEqual((code, region["etf_proxy"]["written"]), (0, 0))
        # The library's file for those days, published the day the stand-ins were stored (the same
        # vintage): its rows replace them, an equal value (RF) included, so none is left as a proxy.
        code, summary, [region] = self._factors(_files(FIVE_V3, MOMENTUM_V2, V3), "--etf-proxy",
                                                proxies=_proxies(calls))
        self.assertEqual(code, 0, summary)
        self.assertEqual(region["written"], 12)
        self.assertEqual(region["etf_proxy"]["after"], "2026-10-02")
        self.assertEqual(region["etf_proxy"]["days"], 0)
        # Only momentum is left as a stand-in: the library's momentum file does not cover those days yet.
        self.assertEqual(self._rows("SELECT return_date, factor FROM factor_returns WHERE source = 'etf-proxy' "
                                    "ORDER BY 1, 2"), [("2026-10-01", "MOM"), ("2026-10-02", "MOM")])
        self.assertEqual(self._rows("SELECT value, source FROM factor_returns WHERE return_date = '2026-10-01' AND "
                                    "factor = 'RF'"), [(0.016 / 100, "kenneth-french")])

    def test_8_factor_row_security_constraints_and_contract_rules(self):
        import psycopg

        with layers._connect(self.database, tenant=OTHER_TENANT) as owner:
            owner.execute("INSERT INTO factor_returns (region, factor, return_date, value, source, vintage, "
                          "fetched_at) VALUES ('us', 'SMB', '2026-08-27', 0.004321, 'kenneth-french', '2026-10-02', "
                          "'2026-10-19T06:00')")
        self.assertEqual(self._rows("SELECT count(*) FROM factor_returns", user=WEB, tenant=OTHER_TENANT), [(1,)])
        self.assertEqual(self._rows("SELECT count(*) FROM factor_returns WHERE value = 0.004321", user=WEB), [(0,)])
        with layers._connect(self.database, APP) as app:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                app.execute("INSERT INTO factor_returns (tenant_id, region, factor, return_date, value, source, "
                            "vintage, fetched_at) VALUES (%s, 'us', 'SMB', '2026-08-28', 0.001, 'kenneth-french', "
                            "'2026-10-02', '2026-10-19T06:00')", (OTHER_TENANT,))
        good = {"region": "us", "factor": "SMB", "return_date": "2026-07-01", "value": 0.001,
                "source": "kenneth-french", "vintage": "2026-10-02", "fetched_at": "2026-10-19T06:00"}
        for column, value in (("factor", "Size"), ("source", "manual"), ("region", "US"), ("value", float("nan")),
                              ("return_date", "20260701"), ("vintage", "2026-10"), ("fetched_at", "today")):
            with self.subTest(column=column, value=value), layers._connect(self.database) as owner:
                with self.assertRaises(psycopg.errors.CheckViolation):
                    owner.execute("INSERT INTO factor_returns (region, factor, return_date, value, source, vintage, "
                                  "fetched_at) VALUES (%(region)s, %(factor)s, %(return_date)s, %(value)s, %(source)s, "
                                  "%(vintage)s, %(fetched_at)s)", {**good, column: value})
        self._contract_rules("factor_returns")
        self.assertEqual(self._freshness()["silver.factor_returns"]["observed"], "2026-10-02")

    def test_9_recorded_runs_name_what_they_read_and_wrote(self):
        bronze_name = self.bronze.add(f"{self.today:%Y/%m/%d}", "SPY_2025-01-13_lineage_1d",
                                      _spy([("2026-10-19", 250_000)]), _at("2026-10-19", 21))
        for name, args, kwargs, job, table, written in (
                ("load_daily_volumes", ("--days", "1", "--instrument", SPY), {"bronze": self.bronze},
                 "ibkrv2stg-daily-volumes", "silver.daily_volumes", 1),
                ("load_factor_returns", ("--region", "us"), {"fetch": _files(FIVE_V3, MOMENTUM_V2, V3).__getitem__},
                 "ibkrv2stg-factor-returns", "silver.factor_returns", 0)):
            with self.subTest(job=job):
                lineage.reset()
                script = _script(name)
                setting = dv.SETTING if script.JOB == "ibkrv2stg-daily-volumes" else fr.SETTING
                original = script.main
                output = io.StringIO()
                with patch.dict(os.environ, {setting: "on", "DATABASE_URL": self.app_url, lineage.SWITCH_ENV: "true"}), \
                        patch.object(script, "main", side_effect=lambda argv: original(argv, **kwargs)), \
                        patch.object(raw_capture, "utc_now", return_value=NOW), contextlib.redirect_stdout(output):
                    code = script.entry(list(args))
                self.assertEqual(code, 0, output.getvalue())
                [(run_id,)] = self._rows("SELECT run_id FROM lineage_events WHERE job_name = %s AND event_type = "
                                         "'COMPLETE'", job)
                io_rows = {(d, ns, n): (rows, sha) for d, ns, n, rows, sha in self._rows(
                    "SELECT direction, namespace, name, row_count, sha256 FROM lineage_io WHERE run_id = %s", run_id)}
                self.assertEqual(io_rows[("output", "postgres", table)][0], written)
                blobs = {n: sha for (d, ns, n), (_, sha) in io_rows.items() if d == "input" and ns == "blob"}
                if name == "load_daily_volumes":
                    self.assertIn(("input", "postgres", "silver.market_data_prices"), io_rows)
                    self.assertEqual(blobs[f"{CONTAINER}/{bronze_name}"],
                                     hashlib.sha256(self.bronze.files[bronze_name][0]).hexdigest())
                else:
                    self.assertEqual(sorted(blobs), sorted(f"{CONTAINER}/{call['name']}" for call in self.uploads.calls))
                    self.assertEqual({call["metadata"]["job_run_id"] for call in self.uploads.calls}, {str(run_id)})


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class DraftUndoTests(unittest.TestCase):
    """Each held draft applied and undone on its own, in either order; each undo drops only its own
    objects, and both undone leave the database as it was."""

    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {"IBKR_RUN_MIGRATIONS": "false"})
        cls.environment.start()
        cls.database = None
        try:
            cls.database = _new_database("p3a_undo_test")
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.database:
            layers._drop(cls.database, roles=ROLES)
        cls.environment.stop()

    def _undo(self, database: str, name: str) -> None:
        with layers._connect(database) as owner:
            owner.execute((DRAFTS / name).read_text(encoding="utf-8"))

    def test_each_draft_applies_and_undoes_alone_in_either_order(self):
        drafts = {VOLUMES_FILE: ("daily_volumes_undo.sql", VOLUMES_OBJECTS),
                  FACTORS_FILE: ("factor_returns_undo.sql", FACTORS_OBJECTS)}
        with layers._connect(self.database) as owner:
            before = _snapshot(owner)
        for first, second in ((VOLUMES_FILE, FACTORS_FILE), (FACTORS_FILE, VOLUMES_FILE)):
            with self.subTest(first=first):
                self.assertIn(f"Migrations applied: {first}.",
                              _bootstrap(self.database, "--only-migration", first, "--allow-pending", second))
                with layers._connect(self.database) as owner:
                    self.assertEqual(_present(owner, drafts[first][1]), list(drafts[first][1]))
                    self.assertEqual(_present(owner, drafts[second][1]), [])
                self.assertIn(f"Migrations applied: {second}.", _bootstrap(self.database, "--only-migration", second))
                # Rows in both, then undo the first: the second's objects and rows stay, for every login.
                with layers._connect(self.database) as owner:
                    owner.execute("INSERT INTO daily_volumes (instrument_id, price_date, volume, source, source_url, "
                                  "fetched_at) VALUES ('x', '2026-10-15', 1, 's', 'x', '2026-10-16T08:50')")
                    owner.execute("INSERT INTO factor_returns (region, factor, return_date, value, source, vintage, "
                                  "fetched_at) VALUES ('us', 'RF', '2026-08-27', 0.0001, 'kenneth-french', "
                                  "'2026-10-02', '2026-10-19T06:00')")
                self._undo(self.database, drafts[first][0])
                with layers._connect(self.database) as owner:
                    self.assertEqual(_present(owner, drafts[first][1]), [])
                    self.assertEqual(_present(owner, drafts[second][1]), list(drafts[second][1]))
                for user in (WEB, APP):
                    with layers._connect(self.database, user) as login:
                        for name in drafts[second][1]:
                            self.assertEqual(login.execute(f"SELECT count(*) FROM {name}").fetchone(), (1,))
                # Running an undo twice changes nothing.
                self._undo(self.database, drafts[first][0])
                self._undo(self.database, drafts[second][0])
                self._undo(self.database, drafts[second][0])
                with layers._connect(self.database) as owner:
                    self.assertEqual(_present(owner, VOLUMES_OBJECTS + FACTORS_OBJECTS), [])
                    # As each undo says: the ledger rows go by hand.
                    owner.execute("DELETE FROM schema_migrations WHERE version = ANY(%s)", ([first, second],))
                    owner.commit()
                    self.assertEqual(_snapshot(owner), before)

    def test_without_the_layers_each_stays_in_public_and_its_undo_removes_it(self):
        database = f"p3a_early_test_{uuid.uuid4().hex[:10]}"
        try:
            layers._build(database, stop_before_layers=True)
            with layers._connect(database) as owner:
                before = _snapshot(owner)
            for name in ("daily_volumes", "factor_returns"):
                with self.subTest(draft=name):
                    with layers._connect(database) as owner:
                        owner.execute((DRAFTS / f"{name}.sql").read_text(encoding="utf-8"))
                    with layers._connect(database) as owner:
                        self.assertEqual(owner.execute(
                            "SELECT n.nspname, c.relkind, c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n "
                            "ON n.oid = c.relnamespace WHERE c.relname = %s", (name,)).fetchall(),
                            [("public", "r", True)])
                    self._undo(database, f"{name}_undo.sql")
                    self._undo(database, f"{name}_undo.sql")
            with layers._connect(database) as owner:
                self.assertEqual(_snapshot(owner), before)
        finally:
            layers._drop(database)


if __name__ == "__main__":
    unittest.main()
