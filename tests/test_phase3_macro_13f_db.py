"""Phase 3 data, part B, on a real PostgreSQL: the two held drafts, their loaders and their undos
(docs/PHASE3_DATA_MACRO_13F.md).

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped
otherwise. Builds its own databases with every migration, sets up the logins with
scripts/bootstrap_postgres.py (roles of its own), then runs each held draft alone as its day will
number it. Synthetic data only; no network (FRED, Yahoo and SEC are fakes).

MacroAndHoldingsTests, one database, in order: both jobs exit held before their migration; the
macro draft alone, with its grants and row security; the macro loader end to end (idempotent
rerun, a revision as a new vintage, a same-day upsert, tenant isolation); the 13F draft alone; the
13F loader end to end (thousands and dollars, a restatement, a new-holdings amendment, an amendment
skipped, a rerun that skips stored accessions, a refresh that writes the same rows); the contract
rules on what was loaded. DraftOrderTests: each draft applied and undone in either order, leaving
nothing.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_medallion_layers_db as layers
from test_phase3_macro_13f import AGENT, _Captured, _line, _load_script, cover, information_table
from test_quant_snapshots_db import _rights, _snapshot
from ibkr_etl import layer_freshness, medallion_contracts, raw_capture
from ibkr_etl import macro_series as ms
from ibkr_etl import sec_13f

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
DRAFTS = ROOT / "infra" / "medallion" / "drafts"
TENANT, OTHER_TENANT = layers.TENANT, layers.OTHER_TENANT
SUFFIX = uuid.uuid4().hex[:8]  # the test server is shared: roles of our own
APP, WEB, APPROVAL = f"phase3b_app_{SUFFIX}", f"phase3b_web_{SUFFIX}", f"phase3b_approval_{SUFFIX}"
ROLES = (APP, WEB, APPROVAL)
# On their day each takes the next free number; after the drafts already held (074 to 077) and part
# A's two tables, 080 and 081 are the likely ones. Nothing here depends on the number.
NEXT = max(max(int(path.name[:3]) for path in layers.MIGRATIONS.glob("*.sql")) + 1, 80)
MACRO_FILE, HOLDINGS_FILE = f"{NEXT:03d}_macro_series.sql", f"{NEXT + 1:03d}_institutional_holdings.sql"
MACRO_OBJECTS = ("silver.macro_series", "public.macro_series")
HOLDINGS_OBJECTS = ("silver.institutional_holdings", "public.institutional_holdings")
CIK = "0000000001"
NOW = datetime(2026, 11, 26, 22, 30, tzinfo=timezone.utc)


def setUpModule():
    if not ADMIN_DSN:
        return
    from ibkr_etl import db

    directory = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(directory.cleanup)
    migrations = Path(directory.name) / "postgres"
    migrations.mkdir()
    for path in layers.MIGRATIONS.glob("*.sql"):
        shutil.copy(path, migrations / path.name)
    shutil.copy(DRAFTS / "macro_series.sql", migrations / MACRO_FILE)
    shutil.copy(DRAFTS / "institutional_holdings.sql", migrations / HOLDINGS_FILE)
    for name, value in (("POSTGRES_MIGRATIONS_DIR", migrations),
                        ("SQLITE_MIGRATION", ROOT / "migrations" / "001_init.sql")):
        patcher = patch.object(db, name, value)
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)
    environment = patch.dict(os.environ, {"IBKR_RUN_MIGRATIONS": "false"})
    environment.start()
    unittest.addModuleCleanup(environment.stop)


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
    _bootstrap(database, "--only-migration", applied[-1], "--allow-pending", f"{MACRO_FILE},{HOLDINGS_FILE}")
    return database


def _app(database: str, tenant: str = TENANT):
    from ibkr_etl.db import connect

    return connect(Path("/nonexistent"), layers._url(database, APP), tenant)


def _present(owner, names) -> list[str]:
    return [name for name in names if owner.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0]]


def _contract_rules(name: str) -> list[dict]:
    contract = medallion_contracts.load(ROOT / "infra" / "medallion" / "contracts" / "silver" / f"{name}.yaml")
    table = medallion_contracts.table_object(contract)
    rules = list(table.get("quality") or [])
    for prop in table["properties"]:
        rules += prop.get("quality") or []
    return rules


# ---------------------------------------------------------------- fakes


def _fred_csv(series_id: str, values: dict[str, str]) -> bytes:
    return ("DATE," + series_id + "\n" + "".join(f"{day},{value}\n" for day, value in values.items())).encode()


FRED_HISTORY = {
    "DGS10": {"2026-11-20": "4.10", "2026-11-23": "4.12", "2026-11-24": "4.14", "2026-11-25": "4.15",
              "2026-11-26": "."},
    "DGS2": {"2026-11-20": "3.60", "2026-11-23": "3.61", "2026-11-24": "3.63", "2026-11-25": "3.65",
             "2026-11-26": "."},
    "DTWEXBGS": {"2026-11-20": "121.1", "2026-11-23": "121.3", "2026-11-24": "121.2", "2026-11-25": "121.0",
                 "2026-11-26": ""},
    "DCOILBRENTEU": {"2026-11-20": "74.2", "2026-11-23": "75.0", "2026-11-24": "75.4", "2026-11-25": "76.1",
                     "2026-11-26": "."},
    "VIXCLS": {"2026-11-25": "15.2"},
    "DEXKOUS": {"2026-11-20": "1384.20", "2026-11-23": "1386.55", "2026-11-24": "1381.10",
                "2026-11-25": "1379.80", "2026-11-26": "."},
}
CNH_HISTORY = [("2026-11-20", 7.12), ("2026-11-23", 7.13), ("2026-11-24", 7.11)]


class _Fred:
    def __init__(self, history):
        self.history = {key: dict(value) for key, value in history.items()}
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        from urllib.parse import parse_qs, urlsplit

        series_id = parse_qs(urlsplit(url).query)["id"][0]
        return _fred_csv(series_id, self.history[series_id])


class _Edgar:
    """SEC EDGAR as a dictionary of URLs; every response goes through SecClient as a real one would."""

    def __init__(self, filings: list[dict]):
        self.documents: dict[str, bytes] = {}
        self.requests: list[str] = []
        recent = {"accessionNumber": [], "form": [], "filingDate": [], "reportDate": []}
        for filing in filings:
            recent["accessionNumber"].append(filing["accession"])
            recent["form"].append(filing["form"])
            recent["filingDate"].append(filing["filed_at"])
            recent["reportDate"].append(filing["period"])
            if "table" not in filing:
                continue
            folder = f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{filing['accession'].replace('-', '')}"
            self.documents[f"{folder}/index.json"] = json.dumps({"directory": {"item": [
                {"name": "primary_doc.xml"}, {"name": "infotable.xml"},
                {"name": f"{filing['accession']}.txt"}]}}).encode()
            self.documents[f"{folder}/primary_doc.xml"] = filing["cover"]
            self.documents[f"{folder}/infotable.xml"] = filing["table"]
        self.documents[f"https://data.sec.gov/submissions/CIK{CIK}.json"] = json.dumps(
            {"cik": CIK, "name": "TEST FILER LLC", "filings": {"recent": recent, "files": []}}).encode()

    def __call__(self, request, timeout):
        import urllib.error

        url = request.full_url
        self.requests.append(url)
        if url not in self.documents:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return self.documents[url]


def _filings() -> list[dict]:
    apple, msft, put, bond = "037833100", "594918104", "38259P508", "912810TM0"
    return [
        # 2022-09-30, filed in 2022: values in thousands.
        {"accession": "0000000001-22-000001", "form": "13F-HR", "filed_at": "2022-11-14", "period": "2022-09-30",
         "cover": cover("09-30-2022"),
         "table": information_table(_line(apple, "APPLE INC", 150, 1000), _line(apple, "APPLE INC", 75, 500),
                                    _line(msft, "MICROSOFT CORP", 30, 100))},
        # A restatement filed in 2023: complete, and in dollars.
        {"accession": "0000000001-23-000001", "form": "13F-HR/A", "filed_at": "2023-02-01",
         "period": "2022-09-30", "cover": cover("09-30-2022", amendment="RESTATEMENT"),
         "table": information_table(_line(apple, "APPLE INC", 230000, 1500),
                                    _line(bond, "UNITED STATES TREAS BD", 990000, 1000000, kind="PRN"))},
        # 2024-09-30: the original, then a new-holdings amendment, then one that says neither.
        {"accession": "0000000001-24-000001", "form": "13F-HR", "filed_at": "2024-11-14", "period": "2024-09-30",
         "cover": cover("09-30-2024"),
         "table": information_table(_line(apple, "APPLE INC", 230000, 1000),
                                    _line(apple, "APPLE INC", 9000, 100, put_call="Call"))},
        {"accession": "0000000001-24-000002", "form": "13F-HR/A", "filed_at": "2024-12-02", "period": "2024-09-30",
         "cover": cover("09-30-2024", amendment="NEW HOLDINGS"),
         "table": information_table(_line(apple, "APPLE INC", 23000, 100),
                                    _line(put, "GOOGLE INC", 4000, 20, put_call="Put"))},
        {"accession": "0000000001-24-000003", "form": "13F-HR/A", "filed_at": "2024-12-03", "period": "2024-09-30",
         "cover": cover("09-30-2024", amendment=" "),
         "table": information_table(_line(msft, "MICROSOFT CORP", 1, 1))},
        # Not a holdings report: never read.
        {"accession": "0000000001-24-000004", "form": "13F-NT", "filed_at": "2024-11-14", "period": "2024-09-30"},
    ]


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _client(edgar) -> sec_13f.SecClient:
    clock = _Clock()
    return sec_13f.SecClient(AGENT, opener=edgar, clock=clock, sleep=clock.sleep)


def _settings(database):
    return SimpleNamespace(database_url=layers._url(database, APP), database_path=Path("/nonexistent"),
                           tenant_id=TENANT)


# ---------------------------------------------------------------- tests


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class MacroAndHoldingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = None
        try:
            cls.database = _new_database("phase3b_test")
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.database:
            layers._drop(cls.database, roles=ROLES)

    def setUp(self):
        self.capture = _Captured()
        raw_capture.reset(self.capture)
        self.addCleanup(raw_capture.reset)
        guard = patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests"))
        guard.start()
        self.addCleanup(guard.stop)

    def _run_script(self, script, **environment):
        module = _load_script(script)
        output = io.StringIO()
        with patch.dict(os.environ, environment), patch.object(module, "_now", return_value=NOW), \
                patch.object(module, "load_settings", return_value=_settings(self.database)), \
                patch.object(sec_13f, "load_filers", return_value=[sec_13f.Filer(CIK, "TEST FILER LLC")]), \
                contextlib.redirect_stdout(output):
            code = module.main([])
        return code, [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]

    def _grants(self, name: str, column: str) -> None:
        silver, public = f"silver.{name}", f"public.{name}"
        with layers._connect(self.database) as owner:
            kinds = dict(owner.execute(
                "SELECT n.nspname || '.' || c.relname, c.relkind FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE c.relname = %s", (name,)).fetchall())
            self.assertEqual(kinds, {silver: "r", public: "v"})
            security = owner.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                                     "WHERE oid = %s::regclass", (silver,)).fetchone()
            self.assertEqual(security, (True, True))
            options = owner.execute("SELECT reloptions::text FROM pg_class WHERE oid = %s::regclass",
                                    (public,)).fetchone()[0]
            self.assertIn("security_invoker=true", options)
            for relation in (silver, public):
                self.assertEqual(_rights(owner, APP, relation), ["SELECT", "INSERT", "UPDATE", "DELETE"])
                self.assertEqual(_rights(owner, WEB, relation), ["SELECT"])
                self.assertEqual(_rights(owner, APPROVAL, relation), [])
                self.assertTrue(owner.execute("SELECT has_column_privilege(%s, %s::regclass, %s, 'SELECT')",
                                              (APPROVAL, relation, column)).fetchone()[0])
                self.assertFalse(owner.execute("SELECT has_column_privilege(%s, %s::regclass, 'fetched_at', "
                                               "'SELECT')", (APPROVAL, relation)).fetchone()[0])
            self.assertTrue(owner.execute("SELECT has_schema_privilege(%s, 'silver', 'USAGE')",
                                          (APPROVAL,)).fetchone()[0])
        # The freshness check's own query runs as the approval login.
        rule = next(r for r in layer_freshness.RULES if r.target == public)
        with layers._connect(self.database, APPROVAL) as approval:
            approval.execute(rule.query).fetchone()

    def test_1_both_jobs_exit_held_before_their_migration(self):
        with layers._connect(self.database) as owner:
            self.assertEqual(_present(owner, MACRO_OBJECTS + HOLDINGS_OBJECTS), [])
        code, lines = self._run_script("load_macro_series.py", MACRO_SERIES_LOAD="on")
        self.assertEqual((code, lines[-1]["status"]), (65, "held"))
        code, lines = self._run_script("load_institutional_holdings.py", INSTITUTIONAL_HOLDINGS_LOAD="on",
                                       SEC_USER_AGENT=AGENT)
        self.assertEqual((code, lines[-1]["status"]), (65, "held"))
        self.assertEqual(self.capture.calls, [], "nothing fetched")

    def test_2_the_macro_table_applies_alone_with_its_grants_and_row_security(self):
        output = _bootstrap(self.database, "--only-migration", MACRO_FILE, "--allow-pending", HOLDINGS_FILE)
        self.assertIn(f"Migrations applied: {MACRO_FILE}.", output)
        self.assertIn("Migrations applied: none.",
                      _bootstrap(self.database, "--only-migration", MACRO_FILE, "--allow-pending", HOLDINGS_FILE))
        with layers._connect(self.database) as owner:
            self.assertEqual(_present(owner, HOLDINGS_OBJECTS), [])
        self._grants("macro_series", "observation_date")

    def test_3_the_macro_loader_end_to_end(self):
        fred = _Fred(FRED_HISTORY)
        cnh = [list(CNH_HISTORY)]
        start = date(2026, 11, 1)

        def run(today, environ=None):
            connection = _app(self.database)
            try:
                return ms.load(connection, start=start, today=today, environ=environ or {}, getter=fred,
                               cnh_fetch=lambda instrument, first, last: ("https://query1.finance.yahoo.com/x",
                                                                          [r for r in cnh[0] if r[0] <= last.isoformat()]))
            finally:
                connection.close()

        def rows():
            with layers._connect(self.database) as owner:
                return owner.execute("SELECT series_id, observation_date, value, units, source, vintage "
                                     "FROM macro_series ORDER BY 1, 2, 6").fetchall()

        def latest(series_id):
            with layers._connect(self.database) as owner:
                return dict(owner.execute(
                    "SELECT DISTINCT ON (observation_date) observation_date, value FROM macro_series "
                    "WHERE series_id = %s ORDER BY observation_date, vintage DESC", (series_id,)).fetchall())

        first = run(date(2026, 11, 26))
        self.assertEqual(first["failed"], [])
        self.assertEqual(first["rows_stored"], 5 * 4 + 3)
        self.assertEqual({s["series_id"]: s["skipped_missing"] for s in first["series"]},
                         {"DGS10": 1, "DGS2": 1, "DTWEXBGS": 1, "DCOILBRENTEU": 1, "DEXKOUS": 1, "USDCNH": 0})
        stored = rows()
        self.assertEqual(len(stored), 23)
        self.assertEqual({row[5] for row in stored}, {"2026-11-26"})
        self.assertNotIn("VIXCLS", {row[0] for row in stored})
        self.assertEqual({(row[0], row[3], row[4]) for row in stored if row[0] in ("DGS10", "DEXKOUS", "USDCNH")},
                         {("DGS10", "percent", "fred"), ("DEXKOUS", "krw_per_usd", "fred"),
                          ("USDCNH", "cnh_per_usd", "yahoo-chart")})
        self.assertEqual([c["feed"] for c in self.capture.calls], ["fred-series"] * 5)

        # The same day again: nothing new to store, nothing changes.
        again = run(date(2026, 11, 26))
        self.assertEqual(again["rows_stored"], 0)
        self.assertEqual(rows(), stored)

        # The next day: FRED revised 25 Nov and added 27 Nov; Yahoo added 25 Nov. Only those are stored.
        fred.history["DGS10"].update({"2026-11-25": "4.16", "2026-11-27": "4.18"})
        cnh[0].append(("2026-11-25", 7.14))
        second = run(date(2026, 11, 27))
        counts = {s["series_id"]: (s["new"], s["revised"], s["unchanged"]) for s in second["series"]}
        self.assertEqual(counts["DGS10"], (1, 1, 3))
        self.assertEqual(counts["USDCNH"], (1, 0, 3))
        self.assertEqual(counts["DGS2"], (0, 0, 4))
        self.assertEqual(len(rows()), 26)
        self.assertEqual(latest("DGS10")["2026-11-25"], 4.16)
        with layers._connect(self.database) as owner:
            history = owner.execute("SELECT vintage, value FROM macro_series WHERE series_id = 'DGS10' AND "
                                    "observation_date = '2026-11-25' ORDER BY 1").fetchall()
        self.assertEqual(history, [("2026-11-26", 4.15), ("2026-11-27", 4.16)], "every vintage kept")

        # Revised again the same day: the day's vintage is updated in place (an upsert), not added.
        fred.history["DGS10"]["2026-11-25"] = "4.17"
        run(date(2026, 11, 27))
        self.assertEqual(len(rows()), 26)
        self.assertEqual(latest("DGS10")["2026-11-25"], 4.17)

        # A licensed VIX is kept once confirmed.
        third = run(date(2026, 11, 27), {ms.LICENSE_SETTING: "VIXCLS"})
        self.assertIn("VIXCLS", [s["series_id"] for s in third["series"]])
        self.assertEqual(latest("VIXCLS"), {"2026-11-25": 15.2})

        # Row security: another tenant sees nothing and cannot write this tenant's rows.
        import psycopg

        with layers._connect(self.database, APP, OTHER_TENANT) as other:
            self.assertEqual(other.execute("SELECT count(*) FROM macro_series").fetchone()[0], 0)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                other.execute("INSERT INTO macro_series (tenant_id, series_id, observation_date, value, units, "
                              "source, vintage, fetched_at) VALUES (%s, 'DGS10', '2026-11-20', 1, 'percent', "
                              "'fred', '2026-11-27', '2026-11-27T22:30:00+00:00')", (TENANT,))
        with layers._connect(self.database, WEB) as web:
            self.assertEqual(web.execute("SELECT count(*) FROM macro_series").fetchone()[0], 27)
            # The website login is read-only twice over: its sessions, and its grants (checked above).
            with self.assertRaises((psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)):
                web.execute("DELETE FROM macro_series")
        # The CHECK constraints refuse what the loader never writes.
        connection = _app(self.database)
        self.addCleanup(connection.close)
        for column, value in (("units", "'percent_points'"), ("value", "'NaN'::float8"),
                              ("observation_date", "'26/11/2026'"), ("series_id", "'dgs10'")):
            values = {"series_id": "'DGS10'", "observation_date": "'2026-11-19'", "value": "1",
                      "units": "'percent'", "source": "'fred'", "vintage": "'2026-11-27'",
                      "fetched_at": "'2026-11-27T22:30:00+00:00'", column: value}
            with self.subTest(column=column), self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(f"INSERT INTO macro_series ({', '.join(values)}) "
                                   f"VALUES ({', '.join(values.values())})")
            connection.rollback()

    def test_4_the_13f_table_applies_alone(self):
        output = _bootstrap(self.database, "--only-migration", HOLDINGS_FILE)
        self.assertIn(f"Migrations applied: {HOLDINGS_FILE}.", output)
        self._grants("institutional_holdings", "period")
        with layers._connect(self.database) as owner:
            self.assertEqual(_present(owner, MACRO_OBJECTS), list(MACRO_OBJECTS))
            self.assertIsNotNone(owner.execute("SELECT to_regclass('silver.institutional_holdings_by_cusip')"
                                               ).fetchone()[0])

    def test_5_the_13f_loader_end_to_end(self):
        edgar = _Edgar(_filings())
        filers = [sec_13f.Filer(CIK, "Test Filer")]
        lines = []

        def run(refresh=False):
            connection = _app(self.database)
            try:
                return sec_13f.load(connection, _client(edgar), filers, since="2022-06-30", refresh=refresh,
                                    emit=lines.append)
            finally:
                connection.close()

        def holdings(accession):
            with layers._connect(self.database) as owner:
                return {(r[0], r[1]): (r[2], r[3], r[4], r[5]) for r in owner.execute(
                    "SELECT cusip, put_call, shares, shares_type, value_usd, filer_name FROM institutional_holdings "
                    "WHERE accession = %s", (accession,)).fetchall()}

        result = run()
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["stored"], 4)
        self.assertEqual([s["accession"] for s in result["skipped"]], ["0000000001-24-000003"])
        self.assertIn("neither RESTATEMENT nor NEW HOLDINGS", result["skipped"][0]["reason"])
        self.assertEqual([line["status"] for line in lines], ["stored"] * 4 + ["skipped"])
        self.assertEqual({line["accession"]: line["kind"] for line in lines if line["status"] == "stored"},
                         {"0000000001-22-000001": "original", "0000000001-23-000001": "restatement",
                          "0000000001-24-000001": "original", "0000000001-24-000002": "new_holdings"})
        # Thousands before 3 Jan 2023, summed per CUSIP; the restatement is in dollars.
        self.assertEqual(holdings("0000000001-22-000001"), {
            ("037833100", ""): (1500.0, "SH", 225000.0, "TEST FILER LLC"),
            ("594918104", ""): (100.0, "SH", 30000.0, "TEST FILER LLC")})
        self.assertEqual(holdings("0000000001-23-000001"), {
            ("037833100", ""): (1500.0, "SH", 230000.0, "TEST FILER LLC"),
            ("912810TM0", ""): (1000000.0, "PRN", 990000.0, "TEST FILER LLC")})
        # The new-holdings amendment is stored complete: the original plus its lines.
        self.assertEqual(holdings("0000000001-24-000002"), {
            ("037833100", ""): (1100.0, "SH", 253000.0, "TEST FILER LLC"),
            ("037833100", "CALL"): (100.0, "SH", 9000.0, "TEST FILER LLC"),
            ("38259P508", "PUT"): (20.0, "SH", 4000.0, "TEST FILER LLC")})
        self.assertEqual(holdings("0000000001-24-000003"), {})
        self.assertEqual(next(line for line in lines if line["accession"] == "0000000001-24-000002")
                         ["base_accession"], "0000000001-24-000001")
        # Readers take each filer's latest accession for a quarter.
        with layers._connect(self.database) as owner:
            latest = owner.execute("SELECT DISTINCT period, accession FROM (" + sec_13f.LATEST_SQL + ") l "
                                   "ORDER BY 1").fetchall()
        self.assertEqual(latest, [("2022-09-30", "0000000001-23-000001"), ("2024-09-30", "0000000001-24-000002")])
        # Every response was captured under sec-13f, the filing documents once under history/.
        feeds = {(c["feed"], c["history"]) for c in self.capture.calls}
        self.assertEqual(feeds, {("sec-13f", False), ("sec-13f", True)})
        self.assertEqual(len(self.capture.calls), result["requests"])
        self.assertFalse(any("000000000124000004" in url for url in edgar.requests), "the 13F-NT is not read")

        # A rerun skips what is stored (and reads the skipped one again).
        with layers._connect(self.database) as owner:
            before = owner.execute("SELECT * FROM institutional_holdings ORDER BY accession, cusip, put_call"
                                   ).fetchall()
        edgar.requests.clear()
        again = run()
        self.assertEqual((again["stored"], again["already_stored"], len(again["skipped"])), (0, 4, 1))
        self.assertEqual(len(edgar.requests), 1 + 3)
        # A refresh writes the same rows again: the new-holdings amendment composes from the same base.
        refreshed = run(refresh=True)
        self.assertEqual(refreshed["stored"], 4)
        with layers._connect(self.database) as owner:
            after = owner.execute("SELECT * FROM institutional_holdings ORDER BY accession, cusip, put_call"
                                  ).fetchall()
        drop_fetched = lambda rows: [tuple(r[:-1]) for r in rows]  # noqa: E731 - fetched_at is re-stamped
        self.assertEqual(drop_fetched(after), drop_fetched(before))

        # A new-holdings amendment whose report is not stored is skipped, never stored half.
        orphan = _Edgar([{"accession": "0000000001-25-000001", "form": "13F-HR/A", "filed_at": "2025-08-20",
                          "period": "2025-06-30", "cover": cover("06-30-2025", amendment="NEW HOLDINGS"),
                          "table": information_table(_line("037833100", "APPLE INC", 1, 1))}])
        connection = _app(self.database)
        try:
            result = sec_13f.load(connection, _client(orphan), filers, since="2025-06-30")
        finally:
            connection.close()
        self.assertEqual(result["stored"], 0)
        self.assertIn("earlier report is not stored", result["skipped"][0]["reason"])
        self.assertEqual(holdings("0000000001-25-000001"), {})

        # Row security, as for the macro table.
        import psycopg

        with layers._connect(self.database, APP, OTHER_TENANT) as other:
            self.assertEqual(other.execute("SELECT count(*) FROM institutional_holdings").fetchone()[0], 0)
            self.assertEqual(other.execute("UPDATE institutional_holdings SET shares = 0 RETURNING 1").fetchall(), [])
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                other.execute("INSERT INTO institutional_holdings (tenant_id, filer_cik, filer_name, period, "
                                  "cusip, issuer, shares, shares_type, value_usd, accession, filed_at, fetched_at) "
                              "VALUES (%s, %s, 'X', '2024-09-30', '037833100', 'X', 1, 'SH', 1, "
                              "'0000000001-24-000009', '2024-11-14', '2026-11-26T22:30:00+00:00')",
                              (TENANT, CIK))
        with layers._connect(self.database, WEB) as web:
            self.assertEqual(web.execute("SELECT count(*) FROM institutional_holdings").fetchone()[0], len(after))

    def test_6_the_contract_rules_pass_on_what_was_loaded(self):
        with layers._connect(self.database) as owner:
            for name in ("macro_series", "institutional_holdings"):
                rules = _contract_rules(name)
                self.assertGreaterEqual(len(rules), 9)
                for rule in rules:
                    with self.subTest(table=name, rule=rule["name"]):
                        self.assertEqual(owner.execute(rule["query"]).fetchone()[0], rule["mustBe"])
            self.assertGreater(owner.execute("SELECT count(*) FROM silver.institutional_holdings").fetchone()[0], 0)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class DraftOrderTests(unittest.TestCase):
    """Each held draft applied and undone on its own, in either order; each undo drops only its own
    objects, an undo run twice changes nothing, and both undone leave the database as it was."""

    @classmethod
    def setUpClass(cls):
        cls.database = None
        try:
            cls.database = _new_database("phase3b_order_test")
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.database:
            layers._drop(cls.database, roles=ROLES)

    def _undo(self, name: str) -> None:
        with layers._connect(self.database) as owner:
            owner.execute((DRAFTS / name).read_text(encoding="utf-8"))

    def test_each_draft_applies_and_undoes_alone_in_either_order(self):
        drafts = {MACRO_FILE: ("macro_series_undo.sql", MACRO_OBJECTS),
                  HOLDINGS_FILE: ("institutional_holdings_undo.sql", HOLDINGS_OBJECTS)}
        with layers._connect(self.database) as owner:
            before = _snapshot(owner)
        for first, second in ((MACRO_FILE, HOLDINGS_FILE), (HOLDINGS_FILE, MACRO_FILE)):
            with self.subTest(first=first):
                self.assertIn(f"Migrations applied: {first}.",
                              _bootstrap(self.database, "--only-migration", first, "--allow-pending", second))
                with layers._connect(self.database) as owner:
                    self.assertEqual(_present(owner, drafts[first][1]), list(drafts[first][1]))
                    self.assertEqual(_present(owner, drafts[second][1]), [])
                self.assertIn(f"Migrations applied: {second}.", _bootstrap(self.database, "--only-migration", second))
                self._undo(drafts[first][0])
                with layers._connect(self.database) as owner:
                    self.assertEqual(_present(owner, drafts[first][1]), [])
                    self.assertEqual(_present(owner, drafts[second][1]), list(drafts[second][1]))
                with layers._connect(self.database, WEB) as web:
                    for name in drafts[second][1]:
                        web.execute(f"SELECT count(*) FROM {name}").fetchone()
                self._undo(drafts[first][0])
                self._undo(drafts[second][0])
                self._undo(drafts[second][0])
                with layers._connect(self.database) as owner:
                    self.assertEqual(_present(owner, MACRO_OBJECTS + HOLDINGS_OBJECTS), [])
                    owner.execute("DELETE FROM schema_migrations WHERE version = ANY(%s)", ([first, second],))
                    owner.commit()
                    self.assertEqual(_snapshot(owner), before)

    def test_without_the_layers_each_draft_stays_in_public_and_its_undo_removes_it(self):
        database = f"phase3b_early_test_{uuid.uuid4().hex[:10]}"
        try:
            layers._build(database, stop_before_layers=True)
            with layers._connect(database) as owner:
                for name in ("macro_series", "institutional_holdings"):
                    owner.execute((DRAFTS / f"{name}.sql").read_text(encoding="utf-8"))
                    owner.commit()
                    self.assertEqual(owner.execute("SELECT relkind FROM pg_class WHERE oid = %s::regclass",
                                                   (f"public.{name}",)).fetchone()[0], "r")
                    owner.execute((DRAFTS / f"{name}_undo.sql").read_text(encoding="utf-8"))
                    owner.commit()
                    self.assertIsNone(owner.execute("SELECT to_regclass(%s)", (f"public.{name}",)).fetchone()[0])
        finally:
            layers._drop(database)

if __name__ == "__main__":
    unittest.main()
