"""The phase 3 gold sections on a real PostgreSQL (docs/QUANT_ANALYTICS.md, "Phase 3").

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped
otherwise. Builds its own database with every migration and the held gold table (076) as
tests/test_quant_snapshots_db.py does, writes the same two made-up accounts, then makes the four
phase 3 silver tables from their held drafts (infra/medallion/drafts/<table>.sql, the data changes)
when all four are in the tree, else from this file's own FIXTURE_SQL with the same columns (silver
tables, public security_invoker views, tenant row security), and gives the logins what bootstrap
gives them on the tables' days (the app login read and write, the website login read). Synthetic
data only, with the VIX left out as the macro loader leaves it out by default.

Phase3Tests, one database, in order: switched on before the tables exist (v1 only, the phase 3
sections skipped, the readers None with the transaction still usable); the tables and the readers
(the latest vintage, each filer's latest accession, the other tenant's rows out of sight); the job's
build of the four sections for both accounts (timed), equal to the pure builders; an unchanged
rerun and new silver rows rebuilding only their sections; one section switched on in shadow and
read by the website login; a 13F filing known only from the day it was filed.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
import test_quant_snapshots_db as gold
from test_quant_snapshots import market_from_raw, synthetic_market
from ibkr_etl import lineage, market_inputs
from ibkr_etl import publish_gate as gate
from ibkr_etl import quant_snapshots as qs

ADMIN_DSN = gold.ADMIN_DSN
TENANT, OTHER_TENANT = gold.TENANT, gold.OTHER_TENANT
SMALL, LARGE, AS_OF = gold.SMALL, gold.LARGE, gold.AS_OF
APP, WEB = gold.APP, gold.WEB
ALL_ON = "liquidity=on,factor_attribution=on,macro=on,crowding=on"
TABLES = tuple(market_inputs.SECTION_TABLES.values())
DRAFTS = Path(__file__).resolve().parents[1] / "infra" / "medallion" / "drafts"
REAL_DRAFTS = all((DRAFTS / f"{table}.sql").exists() for table in TABLES)

# The four silver tables as phase3-schemas.md fixes them, made here for the test only.
FIXTURE_SQL = """
CREATE TABLE silver.daily_volumes (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    instrument_id TEXT NOT NULL,
    price_date TEXT NOT NULL,
    volume DOUBLE PRECISION NOT NULL CHECK (volume >= 0),
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, instrument_id, price_date)
);
CREATE TABLE silver.factor_returns (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    region TEXT NOT NULL CHECK (region ~ '^[a-z][a-z0-9_]{0,62}$'),
    factor TEXT NOT NULL,
    return_date TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('kenneth-french', 'etf-proxy')),
    vintage TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, region, factor, return_date, vintage)
);
CREATE TABLE silver.macro_series (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    series_id TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    units TEXT NOT NULL,
    source TEXT NOT NULL,
    vintage TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, series_id, observation_date, vintage)
);
CREATE TABLE silver.institutional_holdings (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    filer_cik TEXT NOT NULL,
    filer_name TEXT NOT NULL,
    period TEXT NOT NULL,
    cusip TEXT NOT NULL,
    issuer TEXT NOT NULL,
    shares DOUBLE PRECISION NOT NULL,
    shares_type TEXT NOT NULL,
    value_usd DOUBLE PRECISION NOT NULL,
    put_call TEXT NOT NULL,
    accession TEXT NOT NULL,
    filed_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, filer_cik, period, cusip, put_call, accession)
);
"""
# What bootstrap_postgres.py grants on each table and its view once the draft has run.
GRANTS_SQL = """
GRANT SELECT ON silver.{name}, public.{name} TO {web};
GRANT SELECT, INSERT, UPDATE, DELETE ON silver.{name}, public.{name} TO {app};
"""
FIXTURE_POLICY_SQL = """
ALTER TABLE silver.{name} ENABLE ROW LEVEL SECURITY;
ALTER TABLE silver.{name} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON silver.{name}
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
CREATE VIEW public.{name} WITH (security_invoker = true) AS SELECT * FROM silver.{name};
"""
COLUMNS = {
    "daily_volumes": ("instrument_id", "price_date", "volume", "source", "source_url", "fetched_at"),
    "factor_returns": ("region", "factor", "return_date", "value", "source", "vintage", "fetched_at"),
    "macro_series": ("series_id", "observation_date", "value", "units", "source", "vintage", "fetched_at"),
    "institutional_holdings": ("filer_cik", "filer_name", "period", "cusip", "issuer", "shares", "shares_type",
                               "value_usd", "put_call", "accession", "filed_at", "fetched_at"),
}


def setUpModule():
    gold.setUpModule()


def _insert(cursor, table: str, rows: list[dict]) -> None:
    columns = COLUMNS[table]
    defaults = {"source": "yahoo-chart", "source_url": "https://example.test/synthetic",
                "fetched_at": "2026-10-16T06:00:00Z"}
    cursor.executemany(f"INSERT INTO silver.{table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                       [tuple(row.get(column, defaults.get(column)) for column in columns) for row in rows])


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class Phase3Tests(unittest.TestCase):
    """One database through every step, in order (the tests share it)."""

    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {
            "IBKR_RUN_MIGRATIONS": "false", gate.MODE_ENV: "off", lineage.SWITCH_ENV: "",
            qs.BUILD_SETTING: "", qs.MINUTES_SETTING: "", market_inputs.PHASE3_SETTING: "",
            "PORTFOLIO_TENANT_ID": TENANT})
        cls.environment.start()
        cls.database = None
        try:
            cls.database, _ = gold._new_database("quant_p3_test")
            gold._bootstrap(cls.database, "--only-migration", gold.QUANT_FILE, "--allow-pending", gold.DESK_FILE)
            cls.accounts = gold._accounts()
            for data in cls.accounts.values():
                data["fx"] = gold._fx_for(cls.accounts)
            with layers._connect(cls.database) as owner:
                cls.classified = gold._write(owner, cls.accounts)
            # One set of market rows for the tenant (the larger account's names cover the smaller's),
            # and one ISIN per instrument for both accounts.
            cls.raw = synthetic_market(cls.accounts[LARGE])
            cls.raw["instruments"].update(synthetic_market(cls.accounts[SMALL])["instruments"])
        except Exception:
            cls.tearDownClass()
            raise
        cls.app_url = layers._url(cls.database, APP)
        cls.web_url = layers._url(cls.database, WEB)

    @classmethod
    def tearDownClass(cls):
        if cls.database:
            layers._drop(cls.database, roles=gold.ROLES)
        cls.environment.stop()

    def setUp(self):
        lineage.reset()

    # --- helpers ---

    def _first_conid(self) -> str:
        """The first holding of the larger account: it has volumes under its conid."""
        return str(sorted(self.raw["instruments"])[0])

    def _owner(self, tenant: str = TENANT):
        return layers._connect(self.database, tenant=tenant)

    def _rows(self, sql: str, *parameters, user: str = layers.OWNER, tenant: str = TENANT):
        with layers._connect(self.database, user, tenant) as connection:
            return connection.execute(sql, parameters).fetchall()

    def _connect(self, url: str):
        from ibkr_etl.db import connect

        connection = connect(Path("/tmp/unused.sqlite3"), url, TENANT)
        self.addCleanup(connection.close)
        return connection

    def _job(self, *args, phase3: str = ALL_ON, environ: dict | None = None) -> tuple[int, dict, list[dict]]:
        script = gold._script()
        settings = {qs.BUILD_SETTING: "on", "DATABASE_URL": self.app_url, market_inputs.PHASE3_SETTING: phase3,
                    **(environ or {})}
        output = io.StringIO()
        with patch.dict(os.environ, settings), contextlib.redirect_stdout(output):
            code = script.main(list(args))
        lines = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith("{")]
        return code, lines[-1], lines[:-1]

    def _phase3_rows(self, subject: str | None = None, as_of: str = AS_OF) -> dict:
        rows = self._rows("SELECT subject_id, section, source_data_sha256, payload_sha256, generated_at, "
                          "quality_status FROM gold.quant_analytics_snapshots WHERE as_of_date = %s "
                          "AND section = ANY(%s) ORDER BY 1, 2", as_of, list(qs.PHASE3_NAMES))
        return {(row[0], row[1]): row[2:] for row in rows if subject in (None, row[0])}

    def _expected(self, account: str, as_of: str = AS_OF) -> dict:
        """The pure builders' payloads on the rows written, as the job should store them."""
        data = gold._as_loaded({**self.accounts[account], "as_of": as_of}, self.classified)
        prepared = qs.prepare_inputs(data)
        inputs = qs.prepare_phase3_inputs(data, prepared, market_from_raw(self.raw, data, as_of=as_of))
        return {name: (qs.finite_copy(qs.BUILDERS[name](inputs[name]))[0], qs.section_fingerprint(name, inputs[name]))
                for name in qs.PHASE3_NAMES}

    # --- in order ---

    def test_1_switched_on_before_the_tables_exist_builds_v1_only(self):
        app = self._connect(self.app_url)
        for table in TABLES:
            self.assertFalse(market_inputs.table_exists(app, table), table)
        readers = (
            lambda: market_inputs.read_daily_volumes(app, tenant_id=TENANT, instrument_ids=["x"], start="2026-01-01",
                                                     end=AS_OF),
            lambda: market_inputs.read_factor_returns(app, tenant_id=TENANT, region="developed", start="2026-01-01",
                                                      end=AS_OF),
            lambda: market_inputs.read_macro_series(app, tenant_id=TENANT, start="2026-01-01", end=AS_OF),
            lambda: market_inputs.read_institutional_holdings(app, tenant_id=TENANT, as_of=AS_OF,
                                                              cusips=["037833100"]),
        )
        for read in readers:
            self.assertIsNone(read())
            # The savepoint rolled back alone: the transaction goes on.
            self.assertEqual(app.execute("SELECT count(*) AS n FROM silver.accounts").fetchone()["n"], 2)
        app.rollback()

        code, summary, built = self._job()
        self.assertEqual(code, 0, summary)
        self.assertEqual((summary["rebuilt"], summary["sections_written"]), (2, 18))
        self.assertEqual([rule for rule in summary["quality_rules"] if not rule["passed"]], [])
        for line in built:
            for name, table in market_inputs.SECTION_TABLES.items():
                self.assertEqual(line["sections"][name], {"status": "skipped", "reason": f"{table} is not loaded yet"})
            self.assertIn("market_load_ms", line)
        self.assertEqual(self._phase3_rows(), {})

    def test_2_the_tables_and_their_readers(self):
        print(f"\nphase 3 tables from {'their held drafts' if REAL_DRAFTS else 'the fixture SQL'}", file=sys.stderr)
        with self._owner() as owner:
            if REAL_DRAFTS:
                for table in TABLES:
                    owner.execute((DRAFTS / f"{table}.sql").read_text())
            else:
                owner.execute(FIXTURE_SQL)
            for table in TABLES:
                if not REAL_DRAFTS:
                    owner.execute(FIXTURE_POLICY_SQL.format(name=table))
                owner.execute(GRANTS_SQL.format(name=table, app=APP, web=WEB))
            with owner.cursor() as cursor:
                for conid, info in sorted(self.raw["instruments"].items()):
                    cursor.execute("UPDATE silver.instruments SET isin = %s WHERE conid = %s", (info["isin"], conid))
                cursor.executemany(
                    "INSERT INTO silver.market_data_prices (instrument_id, price_date, symbol, instrument_name, "
                    "price_type, currency, price, source, source_url, fetched_at) VALUES (%s, %s, %s, %s, 'close', "
                    "'USD', 1.0, 'Yahoo Finance chart API', 'https://example.test', '2026-10-16T06:00:00Z')",
                    [(row["instrument_id"], AS_OF, row["symbol"], row["symbol"]) for row in self.raw["prices"]])
                for table in TABLES:
                    _insert(cursor, table, self.raw[table])
            # Another tenant's rows, which must never be read: a volume for one of our conids, a newer
            # vintage, a filer.
            owner.execute("SELECT set_config('app.tenant_id', %s, false)", (OTHER_TENANT,))
            with owner.cursor() as cursor:
                _insert(cursor, "daily_volumes", [{"instrument_id": self._first_conid(), "price_date": AS_OF,
                                                   "volume": 9e12}])
                _insert(cursor, "factor_returns", [{"region": "developed", "factor": "Mkt-RF", "return_date": AS_OF,
                                                    "value": 0.5, "source": "kenneth-french", "vintage": "2099-01-01"}])
                _insert(cursor, "macro_series", [{"series_id": "DGS10", "observation_date": AS_OF, "value": 99.0,
                                                  "units": "percent", "source": "fred", "vintage": "2099-01-01"}])
                _insert(cursor, "institutional_holdings", [{
                    "filer_cik": "0000999999", "filer_name": "Other Tenant Fund", "period": "2026-06-30",
                    "cusip": "R00000001", "issuer": "x", "shares": 1.0, "shares_type": "SH", "value_usd": 1e6,
                    "put_call": "", "accession": "0000999999-26-000001", "filed_at": "2026-08-01"}])
            owner.commit()
        for table in TABLES:
            with self.subTest(table=table):
                kinds = dict(self._rows("SELECT n.nspname, c.relkind FROM pg_class c JOIN pg_namespace n "
                                        "ON n.oid = c.relnamespace WHERE c.relname = %s", table))
                self.assertEqual(kinds, {"silver": "r", "public": "v"})
                with self._owner() as owner:
                    self.assertEqual(gold._rights(owner, WEB, f"public.{table}"), ["SELECT"])
                self.assertEqual(self._rows(f"SELECT count(*) FROM {table}", user=WEB),
                                 [(len(self.raw[table]),)])
                self.assertEqual(self._rows(f"SELECT count(*) FROM {table}", user=WEB, tenant=OTHER_TENANT), [(1,)])
        # The readers, as the job's app login, give exactly the reference's rows for each account.
        app = self._connect(self.app_url)
        for table in TABLES:
            self.assertTrue(market_inputs.table_exists(app, table))
        for account, data in self.accounts.items():
            with self.subTest(account=account):
                loaded = qs.load_inputs(app, tenant_id=TENANT, subject_id=account, as_of=AS_OF)
                found = qs.load_phase3_inputs(app, tenant_id=TENANT, subject_id=account, as_of=AS_OF, data=loaded,
                                              sections=qs.PHASE3_NAMES)
                expected = market_from_raw(self.raw, data)
                for name in qs.PHASE3_NAMES:
                    self.assertEqual(found[name], expected[name], name)
                # What the reference got right: the newer vintage, Kenneth French only, the amendment.
                factors = found["factor_attribution"]
                self.assertEqual(factors["sources"], {"kenneth-french": sum(len(v) for v in factors["factors"].values())})
                self.assertEqual(factors["vintages"], [qs._shift(AS_OF, -16), qs._shift(AS_OF, -1)])
                filers = {row["filer_name"] + row["filer_cik"][-1]: row for row in found["crowding"]["filings"]["filers"]}
                self.assertEqual(filers["Alpha Capital1"]["accession"], "0000100001-26-000002")
                self.assertEqual(filers["Beta Partners2"]["period"], "2026-06-30")
                # Filed later under a lower accession (another filing agent): the newest filed_at counts.
                self.assertEqual(filers["Delta Advisors5"]["accession"], "0000100005-26-000001")
                self.assertNotIn("Other Tenant Fund9", filers)
                # By conid: the benchmark's chart rows under the first holding's symbol are never read.
                self.assertTrue(all(entry["instrument_id"] == conid
                                    for conid, entry in found["liquidity"]["volumes"].items()))
                self.assertNotIn(7.0, [v for entry in found["liquidity"]["volumes"].values()
                                       for v in entry["volumes"].values()])
                self.assertNotIn("VIXCLS", found["macro"]["series"])
        # With every filing known, each filer's quarter and accession are the 13F loader's own choice
        # (sec_13f.LATEST_SQL: per filer and quarter, the newest filed_at, then the highest accession).
        from ibkr_etl import sec_13f

        latest = {}
        for row in app.execute(sec_13f.LATEST_SQL).fetchall():
            latest.setdefault(row["filer_cik"], set()).add((row["period"], row["accession"]))
        everything = market_inputs.read_institutional_holdings(app, tenant_id=TENANT, as_of="2099-12-31", cusips=[])
        self.assertEqual({row["filer_cik"]: (row["period"], row["accession"]) for row in everything["filers"]},
                         {cik: max(chosen) for cik, chosen in latest.items()})
        app.rollback()

    def test_3_the_job_builds_the_switched_on_sections_of_both_accounts(self):
        code, summary, built = self._job()
        self.assertEqual(code, 0, summary)
        self.assertEqual((summary["rebuilt"], summary["sections_written"]), (2, 8))  # v1 is unchanged
        self.assertEqual([rule for rule in summary["quality_rules"] if not rule["passed"]], [])
        timings = summary["seconds_per_subject_date"]
        print(f"\nphase 3 build, seconds per subject (load, build and write): {timings}", file=sys.stderr)
        self.assertLess(timings[f"{LARGE}@{AS_OF}"], 60.0)
        for line in built:
            self.assertEqual(line["errors"], {})
            print(f"  {line['subject_id'][-3:]} phase 3 sections (ms): market load {line['market_load_ms']:.0f}, "
                  + ", ".join(f"{name} {line['sections'][name]['build_ms']:.0f}" for name in qs.PHASE3_NAMES),
                  file=sys.stderr)
            self.assertEqual({name for name, row in line["sections"].items() if row["status"] == "rebuilt"},
                             set(qs.PHASE3_NAMES))
        for account in self.accounts:
            expected = self._expected(account)
            stored = self._rows("SELECT section, model_id, model_version, payload_json, source_data_sha256, "
                                "quality_status, timings_json FROM quant_analytics_snapshots WHERE subject_id = %s "
                                "AND as_of_date = %s AND section = ANY(%s) ORDER BY section", account, AS_OF,
                                list(qs.PHASE3_NAMES), user=WEB)
            self.assertEqual([row[0] for row in stored], sorted(qs.PHASE3_NAMES))
            for section, model_id, model_version, payload, source_sha, quality, timing in stored:
                with self.subTest(account=account, section=section):
                    payload_expected, fingerprint = expected[section]
                    self.assertTrue(payload["available"], payload["reason"])
                    self.assertEqual(payload, json.loads(qs.canonical_json(payload_expected)))
                    self.assertEqual((model_id, model_version),
                                     (payload_expected["model_id"], payload_expected["model_version"]))
                    self.assertEqual(source_sha, fingerprint)
                    self.assertEqual(quality, "pass")
                    self.assertIn("market_load_ms", timing)
        self.assertEqual(len(self._rows("SELECT 1 FROM quant_analytics_snapshots WHERE as_of_date = %s", AS_OF,
                                        user=WEB)), 26)

    def test_4_an_unchanged_rerun_skips_and_new_silver_rows_rebuild_only_their_sections(self):
        before = self._phase3_rows()
        code, summary, built = self._job()
        self.assertEqual(code, 0, summary)
        self.assertEqual((summary["rebuilt"], summary["unchanged"], summary["sections_written"]), (0, 2, 0))
        self.assertEqual(self._phase3_rows(), before)
        # A newer factor file revises one day, and one holding's latest volume changes.
        day = max(row["return_date"] for row in self.raw["factor_returns"] if row["source"] == "kenneth-french")
        with self._owner() as owner:
            owner.execute("INSERT INTO silver.factor_returns (region, factor, return_date, value, source, vintage, "
                          "fetched_at) VALUES ('developed', 'SMB', %s, 0.0123, 'kenneth-french', %s, %s)",
                          (day, AS_OF, f"{AS_OF}T06:00:00Z"))
            owner.execute("UPDATE silver.daily_volumes SET volume = volume + 1 WHERE instrument_id = %(conid)s "
                          "AND price_date = (SELECT max(price_date) FROM silver.daily_volumes "
                          "WHERE instrument_id = %(conid)s AND volume > 0)", {"conid": self._first_conid()})
        self.raw["factor_returns"].append({"region": "developed", "factor": "SMB", "return_date": day, "value": 0.0123,
                                           "source": "kenneth-french", "vintage": AS_OF,
                                           "fetched_at": f"{AS_OF}T06:00:00Z"})
        latest = max((row for row in self.raw["daily_volumes"] if row["instrument_id"] == self._first_conid()
                      and row["volume"] > 0), key=lambda row: row["price_date"])
        latest["volume"] += 1
        code, summary, built = self._job()
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary["sections_written"], 4)
        for line in built:
            rebuilt = {name for name, row in line["sections"].items() if row["status"] == "rebuilt"}
            self.assertEqual(rebuilt, {"factor_attribution", "liquidity"})
        after = self._phase3_rows()
        for key, row in after.items():
            self.assertEqual(row[0] != before[key][0], key[1] in {"factor_attribution", "liquidity"}, key)
        for account in self.accounts:
            expected = self._expected(account)
            for name in ("factor_attribution", "liquidity"):
                self.assertEqual(after[(account, name)][0], expected[name][1])

    def test_5_one_section_in_shadow_and_the_website_reader(self):
        with self._owner() as owner:
            owner.execute("DELETE FROM gold.quant_analytics_snapshots WHERE subject_id = %s AND section = ANY(%s)",
                          (SMALL, list(qs.PHASE3_NAMES)))
        code, summary, built = self._job("--subject", SMALL, phase3="liquidity=shadow,macro=off")
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary["sections_written"], 1)
        self.assertEqual(set(built[0]["sections"]) - set(qs.SECTION_NAMES), {"liquidity"})
        self.assertEqual(sorted(name for _, name in self._phase3_rows(SMALL)), ["liquidity"])
        # Unset, the job writes nothing more and leaves the phase 3 rows as they are.
        before = self._phase3_rows()
        code, summary, _ = self._job(phase3="")
        self.assertEqual((code, summary["sections_written"]), (0, 0))
        self.assertEqual(self._phase3_rows(), before)
        web = self._connect(self.web_url)
        row = qs.read_section(web, tenant_id=TENANT, subject_id=SMALL, section="liquidity")
        self.assertEqual((row["as_of_date"], row["payload"]["section"], row["model_id"]), (AS_OF, "liquidity",
                                                                                           "liquidity"))
        self.assertIsNone(qs.read_section(web, tenant_id=TENANT, subject_id=SMALL, section="macro"))
        self.assertEqual(qs.read_section(web, tenant_id=TENANT, subject_id=LARGE, section="crowding",
                                         as_of="2026-10-18")["as_of_date"], AS_OF)
        self.assertIsNone(qs.read_section(web, tenant_id=OTHER_TENANT, subject_id=LARGE, section="crowding"))

    def test_6_a_13f_filing_counts_from_the_day_it_was_filed(self):
        code, summary, _ = self._job("--subject", SMALL, "--since", "2026-10-15", phase3="crowding=on")
        self.assertEqual(code, 0, summary)
        funds = {}
        for as_of in ("2026-10-15", AS_OF):
            payload = self._rows("SELECT payload_json FROM quant_analytics_snapshots WHERE subject_id = %s AND "
                                 "as_of_date = %s AND section = 'crowding'", SMALL, as_of, user=WEB)[0][0]
            funds[as_of] = {row["fund"] for row in payload["inputs"]["filers"]}
            expected, _ = self._expected(SMALL, as_of)["crowding"]
            self.assertEqual(payload, json.loads(qs.canonical_json(expected)))
        self.assertEqual(funds[AS_OF] - funds["2026-10-15"], {"Hotel Holdings"})  # filed on 16 October


if __name__ == "__main__":
    unittest.main()
