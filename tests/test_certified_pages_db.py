"""The website's certified reader on a real PostgreSQL (U5, part 5).

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. Builds its own database with every migration in order, as the owner (not a
superuser, so forced row-level security applies as on Azure). When the certified tables'
migration is not in the repository yet, it applies the design's SQL
(docs/MEDALLION_CERTIFIED_FIGURES.md, "How it is stored") instead. Synthetic data only.
"""
from __future__ import annotations

import json
import os
import time
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import test_certified_pages as unit
import test_medallion_layers_db as layers
from ibkr_etl import certified_pages as pages

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
TENANT, OTHER_TENANT = layers.TENANT, layers.OTHER_TENANT
SUBJECT = unit.SUBJECT
SHA = "c" * 64
DESIGN_SQL = """
CREATE TABLE gold.certified_builds (
    tenant_id uuid NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    build_id uuid NOT NULL,
    started_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('shadow', 'published', 'held', 'rolled_back')),
    published_at timestamptz,
    checks jsonb NOT NULL,
    reason text,
    lineage_run_id uuid,
    PRIMARY KEY (tenant_id, build_id)
);
CREATE TABLE gold.certified_figures (
    tenant_id uuid NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    build_id uuid NOT NULL,
    subject_id text NOT NULL,
    figure_id text NOT NULL,
    period text NOT NULL DEFAULT '',
    item text NOT NULL DEFAULT '',
    benchmark_id text NOT NULL DEFAULT '',
    as_of_date date NOT NULL,
    value double precision,
    value_text text,
    unit text NOT NULL CHECK (unit IN ('ratio', 'number', 'money', 'date', 'text')),
    currency text,
    status text NOT NULL CHECK (status IN ('ok', 'unavailable')),
    reason text,
    detail jsonb NOT NULL DEFAULT '{}',
    period_start date, period_end date, observations integer,
    definition_id text NOT NULL,
    definition_version text NOT NULL,
    calculation_version text NOT NULL,
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    source_table text NOT NULL,
    source_key jsonb NOT NULL,
    lineage_run_id uuid,
    written_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subject_id, figure_id, period, item, benchmark_id, as_of_date, build_id),
    FOREIGN KEY (tenant_id, build_id) REFERENCES gold.certified_builds
);
ALTER TABLE gold.certified_builds ENABLE ROW LEVEL SECURITY;
ALTER TABLE gold.certified_builds FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON gold.certified_builds
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE gold.certified_figures ENABLE ROW LEVEL SECURITY;
ALTER TABLE gold.certified_figures FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON gold.certified_figures
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
CREATE VIEW gold.v_certified_figures WITH (security_invoker = true) AS
SELECT DISTINCT ON (f.tenant_id, f.subject_id, f.figure_id, f.period, f.item, f.benchmark_id, f.as_of_date)
       f.*, b.status AS build_status, b.published_at
  FROM gold.certified_figures f
  JOIN gold.certified_builds b ON b.tenant_id = f.tenant_id AND b.build_id = f.build_id
 WHERE b.status = 'published'
 ORDER BY f.tenant_id, f.subject_id, f.figure_id, f.period, f.item, f.benchmark_id, f.as_of_date,
          b.published_at DESC;
"""


def _migration_present() -> bool:
    return any(layers.MIGRATIONS.glob("*_certified_figures.sql"))


def _insert_build(owner, tenant: str, status: str, published: datetime | None, rows: list[dict]) -> str:
    build = str(uuid.uuid4())
    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
    owner.execute("INSERT INTO gold.certified_builds(tenant_id, build_id, started_at, status, published_at, checks) "
                  "VALUES (%s, %s, %s, %s, %s, '{}')",
                  (tenant, build, datetime(2026, 10, 22, 8, tzinfo=timezone.utc), status, published))
    for row in rows:
        owner.execute(
            "INSERT INTO gold.certified_figures(tenant_id, build_id, subject_id, figure_id, period, item, "
            "benchmark_id, as_of_date, value, value_text, unit, status, definition_id, definition_version, "
            "calculation_version, input_sha256, source_table, source_key) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'metric-x', 'abcdef012345', 'v1', %s, "
            "'silver.subject_nav_series', '{}')",
            (tenant, build, row.get("subject", SUBJECT), row["figure_id"], row["period"], row["item"],
             row["benchmark_id"], row["as_of_date"], row["value"], row["value_text"], row["unit"],
             row["status"], SHA))
    owner.commit()
    return build


def _row(figure_id: str, value, as_of: str, *, period: str = "", benchmark_id: str = "", unit: str = "money") -> dict:
    return {"figure_id": figure_id, "period": period, "item": "", "benchmark_id": benchmark_id, "as_of_date": as_of,
            "value": None if unit in ("date", "text") else value,
            "value_text": value if unit in ("date", "text") else None, "unit": unit, "status": "ok"}


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class CertifiedReaderDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        layers.setUpModule()
        cls.database = f"certified_pages_test_{uuid.uuid4().hex[:10]}"
        cls.empty = f"certified_pages_empty_{uuid.uuid4().hex[:10]}"
        try:
            cls.applied = layers._build(cls.database)
            with layers._connect(cls.database) as owner:
                if not _migration_present():
                    owner.execute(DESIGN_SQL)
                    owner.commit()
                cls._seed(owner)
            import psycopg
            with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
                admin.execute(f'CREATE DATABASE "{cls.empty}" OWNER {layers.OWNER}')
        except Exception:
            layers._drop(cls.database)
            layers._drop(cls.empty)
            raise
        cls.url = layers._url(cls.database, layers.OWNER)

    @classmethod
    def tearDownClass(cls):
        layers._drop(cls.database)
        layers._drop(cls.empty)

    @classmethod
    def _seed(cls, owner):
        at = lambda hour, day=22: datetime(2026, 10, day, hour, tzinfo=timezone.utc)  # noqa: E731
        summary = unit.certified_rows("summary", unit._summary(None))
        cls.first = _insert_build(owner, TENANT, "published", at(9, 21), [
            _row("overview.nav", 100.0, "2026-10-20"), _row("overview.nav", 110.0, "2026-10-21"),
            _row("overview.twr", 0.05, "2026-10-21", period="ytd", unit="ratio"),
            _row("benchmark.beta", 0.9, "2026-10-21", benchmark_id="GSPC", unit="number"),
            _row("benchmark.beta", 1.1, "2026-10-21", benchmark_id="HSI", unit="number"),
            *[{**row, "subject": "U9100003"} for row in summary]])
        # A later published build corrects one value for 21 Oct.
        cls.second = _insert_build(owner, TENANT, "published", at(9), [_row("overview.nav", 111.0, "2026-10-21")])
        # Held and rolled-back builds are never read.
        _insert_build(owner, TENANT, "held", None, [_row("overview.nav", 120.0, "2026-10-22")])
        _insert_build(owner, TENANT, "rolled_back", at(10), [_row("overview.nav", 999.0, "2026-10-21")])
        _insert_build(owner, TENANT, "shadow", None, [_row("overview.nav", 998.0, "2026-10-21")])
        # Another tenant's figures for the same subject id.
        _insert_build(owner, OTHER_TENANT, "published", at(11), [_row("overview.nav", 555.0, "2026-10-21")])

    def connection(self, tenant=TENANT):
        from psycopg.rows import dict_row

        return layers._connect(self.database, tenant=tenant, row_factory=dict_row)

    def read(self, figure_ids, *, tenant=TENANT, as_of=None, benchmark_id=""):
        with self.connection(tenant) as connection:
            return pages.read_certified(connection, tenant_id=tenant, subject_id=SUBJECT, figure_ids=figure_ids,
                                        as_of=as_of, benchmark_id=benchmark_id)

    def test_the_view_comes_from_the_migration_or_the_design(self):
        with self.connection() as connection:
            found = connection.execute("SELECT to_regclass('gold.v_certified_figures') AS view").fetchone()
        self.assertIsNotNone(found["view"])

    def test_newest_published_figures_only(self):
        found = self.read(["overview.nav", "overview.twr"])
        self.assertEqual(found.as_of, "2026-10-21")
        self.assertEqual(found.values[("overview.nav", "", "", "")], 111.0)
        self.assertEqual(found.values[("overview.twr", "ytd", "", "")], 0.05)
        self.assertEqual(found.build_id, self.second)

    def test_on_or_before_the_asked_date(self):
        found = self.read(["overview.nav"], as_of="2026-10-20")
        self.assertEqual((found.as_of, found.values[("overview.nav", "", "", "")]), ("2026-10-20", 100.0))
        self.assertIsNone(self.read(["overview.nav"], as_of="2026-10-19"))

    def test_one_benchmark_at_a_time(self):
        found = self.read(["benchmark.beta"], benchmark_id="HSI")
        self.assertEqual(found.values, {("benchmark.beta", "", "", "HSI"): 1.1})

    def test_tenants_see_only_their_own_figures(self):
        found = self.read(["overview.nav"], tenant=OTHER_TENANT)
        self.assertEqual(found.values[("overview.nav", "", "", "")], 555.0)

    def test_a_page_shows_the_certified_figures_through_its_own_connection(self):
        with patch.dict(os.environ, {"DATABASE_URL": self.url}):
            shown = pages.serve("summary", tenant_id=TENANT, subject_id="U9100003", requested_as_of=None,
                                build=unit._summary, environ={pages.register.PAGES_SETTING: "overview=on"})
        self.assertEqual(shown["certified"]["build_id"], self.first)
        self.assertEqual(shown["certified"]["as_of"], unit.LATEST)
        self.assertEqual(shown["latest_nav"], unit._summary(None)["latest_nav"])

    def test_a_missing_view_leaves_todays_page(self):
        empty = layers._url(self.empty, layers.OWNER)
        with patch.dict(os.environ, {"DATABASE_URL": empty}), \
                self.assertLogs(unit.LOGGER, level="INFO") as captured:
            shown = pages.serve("summary", tenant_id=TENANT, subject_id=SUBJECT, requested_as_of=None,
                                build=unit._summary, environ={pages.register.PAGES_SETTING: "overview=shadow"})
        self.assertEqual(shown, unit._summary(None))
        line = json.loads(captured.output[0].split(pages.LOG_PREFIX + " ", 1)[1])
        self.assertEqual((line["status"], line["error"]), ("error", "UndefinedTable"))

    def test_a_slow_read_is_cut_short(self):
        slow = pages.READ_SQL.replace("SELECT figure_id,", "SELECT pg_sleep(3), figure_id,", 1)
        started = time.perf_counter()
        with patch.dict(os.environ, {"DATABASE_URL": self.url}), patch.object(pages, "READ_SQL", slow), \
                self.assertLogs(unit.LOGGER, level="INFO") as captured:
            shown = pages.serve("summary", tenant_id=TENANT, subject_id="U9100003", requested_as_of=None,
                                build=unit._summary, environ={pages.register.PAGES_SETTING: "overview=on"})
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertEqual(shown, unit._summary(None))
        self.assertIn('"error":"QueryCanceled"', captured.output[0])


if __name__ == "__main__":
    unittest.main()
