"""Migration 073 (certified figures, medallion upgrade U5) on a real PostgreSQL.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. Builds its own database with every other migration in order and some
synthetic rows, then applies 073 as production will (bootstrap_postgres.py --only-migration,
with its own throwaway logins), as a non-superuser owner so forced row-level security applies
as on Azure. Synthetic data only.
"""
from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from ibkr_etl import lineage
from ibkr_etl.db import connect

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
MIGRATION = "073_certified_figures.sql"
TENANT, OTHER_TENANT = layers.TENANT, layers.OTHER_TENANT
APP, WEB, APPROVAL = "certified_test_app", "certified_test_web", "certified_test_approval"
ROLES = (APP, WEB, APPROVAL)
NEW = {"gold.certified_builds", "gold.certified_figures", "gold.v_certified_figures",
       "public.certified_builds", "public.certified_figures", "public.v_certified_figures"}
SCHEMAS = ("public", "bronze", "silver", "gold", "agent")
SHA = "c" * 64


def setUpModule():
    layers.setUpModule()


def _bootstrap(database: str, *args: str) -> None:
    import contextlib
    import io

    environment = {
        "DATABASE_ADMIN_URL": layers._url(database, layers.OWNER), "PORTFOLIO_TENANT_ID": TENANT,
        "DATABASE_APP_ROLE": APP, "DATABASE_APP_PASSWORD": layers.PASSWORD,
        "DATABASE_WEB_ROLE": WEB, "DATABASE_WEB_PASSWORD": layers.PASSWORD,
        "DATABASE_APPROVAL_ROLE": APPROVAL, "DATABASE_APPROVAL_PASSWORD": layers.PASSWORD,
    }
    with patch.dict(os.environ, environment), contextlib.redirect_stdout(io.StringIO()):
        layers._script("bootstrap_postgres").main(list(args))


def _relations(connection) -> dict[str, dict]:
    """Everything a reader of each relation sees: its shape, rights, rules and rows."""
    found = {}
    rows = connection.execute(
        "SELECT n.nspname, c.relname, c.oid, c.relkind, c.relacl::text, c.reloptions, c.relrowsecurity, "
        "c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p', 'v', 'm') ORDER BY 1, 2", (list(SCHEMAS),)).fetchall()
    connection.commit()
    for schema, name, oid, kind, acl, options, rls, forced in rows:
        entry = {
            "kind": kind, "acl": acl, "options": options, "rls": [rls, forced],
            "columns": connection.execute(
                "SELECT attname, format_type(atttypid, atttypmod), attnotnull, attacl::text FROM pg_attribute "
                "WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped ORDER BY attnum", (oid,)).fetchall(),
            "constraints": connection.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s ORDER BY 1",
                (oid,)).fetchall(),
            "triggers": connection.execute(
                "SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid = %s AND NOT tgisinternal "
                "ORDER BY 1", (oid,)).fetchall(),
            "policies": connection.execute(
                "SELECT polname, pg_get_expr(polqual, polrelid) FROM pg_policy WHERE polrelid = %s ORDER BY 1",
                (oid,)).fetchall(),
            "definition": connection.execute("SELECT pg_get_viewdef(%s)", (oid,)).fetchone()[0]
            if kind in ("v", "m") else None,
        }
        connection.commit()
        try:
            entry["rows"] = connection.execute(
                f'SELECT count(*), md5(coalesce(string_agg(t::text, \'|\' ORDER BY t::text), \'\')) '
                f'FROM "{schema}"."{name}" t').fetchone()
        except Exception as error:  # noqa: BLE001 - the same failure before and after is fine
            entry["rows"] = type(error).__name__
        connection.rollback()
        found[f"{schema}.{name}"] = entry
    return found


def _functions(connection) -> dict[str, str]:
    rows = connection.execute(
        "SELECT p.oid::regprocedure::text, md5(pg_get_functiondef(p.oid)) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = ANY(%s) AND p.prokind = 'f'",
        (list(SCHEMAS),)).fetchall()
    connection.rollback()
    return dict(rows)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class CertifiedFiguresMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = f"certified_test_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in ROLES:
                admin.execute(f"DROP ROLE IF EXISTS {role}")
        try:
            applied = layers._build(cls.database, skip=(MIGRATION,))
            with layers._connect(cls.database) as owner:
                layers._seed(owner)
            # The logins as an earlier bootstrap left them; it applies nothing.
            _bootstrap(cls.database, "--only-migration", applied[-1], "--allow-pending", MIGRATION)
            cls.before = {tenant: cls._readers(tenant) for tenant in (TENANT, OTHER_TENANT)}
            cls.functions_before = cls._owner_read(_functions)
            _bootstrap(cls.database, "--only-migration", MIGRATION)
            cls.after = {tenant: cls._readers(tenant) for tenant in (TENANT, OTHER_TENANT)}
            cls.functions_after = cls._owner_read(_functions)
        except Exception:
            layers._drop(cls.database, ROLES)
            raise
        cls.url = layers._url(cls.database, layers.OWNER)

    @classmethod
    def tearDownClass(cls):
        layers._drop(cls.database, ROLES)

    @classmethod
    def _owner_read(cls, reader, tenant=TENANT):
        with layers._connect(cls.database, tenant=tenant) as connection:
            return reader(connection)

    @classmethod
    def _readers(cls, tenant):
        """What the owner and each login see, for one tenant."""
        seen = {"owner": cls._owner_read(_relations, tenant)}
        for role in (APP, WEB):
            with layers._connect(cls.database, role, tenant) as connection:
                seen[role] = {name: entry["rows"] for name, entry in _relations(connection).items()}
        return seen

    def setUp(self):
        lineage.reset()
        self.environment = patch.dict(os.environ, {"LINEAGE_RECORDING": "true", "DATABASE_URL": self.url,
                                                   "PORTFOLIO_TENANT_ID": TENANT})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        lineage.reset()

    def _owner(self, tenant=TENANT):
        return layers._connect(self.database, tenant=tenant)

    def _login(self, role, tenant=TENANT):
        connection = layers._connect(self.database, role, tenant)
        self.addCleanup(connection.close)
        return connection

    @staticmethod
    def _build(connection, status="shadow", published_at=None, reason=None) -> str:
        build_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO gold.certified_builds(build_id, run_kind, started_at, status, published_at, reason) "
            "VALUES (%s, 'daily', now(), 'shadow', NULL, NULL)", (build_id,))
        if status != "shadow":
            connection.execute("UPDATE gold.certified_builds SET status = %s, published_at = %s, reason = %s "
                               "WHERE build_id = %s", (status, published_at, reason, build_id))
        return build_id

    @staticmethod
    def _figure(connection, build_id, subject, value, *, figure_id="risk.sharpe_ratio", as_of="2026-10-16",
                source_table="gold.risk_analytics_snapshots", source_key=None, **columns):
        import json

        row = {"build_id": build_id, "subject_id": subject, "figure_id": figure_id, "as_of_date": as_of,
               "value": value, "unit": "number", "status": "ok", "definition_id": "metric-sharpe-ratio",
               "definition_version": "0123456789ab", "calculation_version": "risk-analytics-v2+benchmark-comparison-v1",
               "input_sha256": SHA, "source_table": source_table,
               "source_key": json.dumps(source_key or {"subject_id": subject, "as_of_date": as_of}), **columns}
        names = ", ".join(row)
        connection.execute(f"INSERT INTO gold.certified_figures({names}) VALUES ({', '.join(['%s'] * len(row))})",
                           tuple(row.values()))

    # -- the migration changes nothing else ------------------------------------------------------

    def test_applying_it_changes_nothing_readers_of_existing_tables_see(self):
        for tenant in (TENANT, OTHER_TENANT):
            for reader in ("owner", APP, WEB):
                with self.subTest(tenant=tenant, reader=reader):
                    before, after = self.before[tenant][reader], self.after[tenant][reader]
                    self.assertEqual(set(after) - set(before), NEW)
                    changed = sorted(name for name in before if after[name] != before[name])
                    self.assertEqual(changed, ["public.schema_migrations"])  # it gained 073
        # The seeded rows were really read, so the comparison covered data, not just empty tables.
        rows = self.before[TENANT]["owner"]
        self.assertGreater(rows["bronze.flex_rows"]["rows"][0], 0)
        self.assertGreater(rows["silver.nav_snapshots"]["rows"][0], 0)
        self.assertGreater(rows["gold.reporting_snapshots"]["rows"][0], 0)
        self.assertEqual(rows["public.nav_snapshots"]["rows"], rows["silver.nav_snapshots"]["rows"])
        new_functions = set(self.functions_after) - set(self.functions_before)
        self.assertEqual(new_functions, {"certified_builds_guard()", "certified_figures_into_shadow_build()",
                                         "certified_figures_refuse_change()"})
        self.assertEqual({name: self.functions_after[name] for name in self.functions_before}, self.functions_before)
        with self._owner() as owner:
            ledger = [row[0] for row in owner.execute("SELECT version FROM schema_migrations ORDER BY 1")]
        self.assertEqual(ledger[-1], MIGRATION)

    def test_the_tables_are_gold_with_forced_tenant_isolation_and_compatibility_views(self):
        after = self.after[TENANT]["owner"]
        for name in ("certified_builds", "certified_figures"):
            with self.subTest(name):
                self.assertEqual(after[f"gold.{name}"]["kind"], "r")
                self.assertEqual(after[f"gold.{name}"]["rls"], [True, True])
                self.assertEqual([p[0] for p in after[f"gold.{name}"]["policies"]], ["tenant_isolation"])
                self.assertIn("lineage_stamp_run", [t[0] for t in after[f"gold.{name}"]["triggers"]])
        for name in ("certified_builds", "certified_figures", "v_certified_figures"):
            with self.subTest(view=name):
                self.assertEqual(after[f"public.{name}"]["kind"], "v")
                self.assertIn("security_invoker=true", after[f"public.{name}"]["options"])
                self.assertEqual([column[:2] for column in after[f"public.{name}"]["columns"]],
                                 [column[:2] for column in after[f"gold.{name}"]["columns"]])
                self.assertEqual(after[f"public.{name}"]["acl"], after[f"gold.{name}"]["acl"])
        self.assertIn("security_invoker=true", after["gold.v_certified_figures"]["options"])

    def test_each_login_has_the_rights_it_needs_and_no_more(self):
        import psycopg

        app, web = self._login(APP), self._login(WEB)
        build_id = self._build(app)
        self._figure(app, build_id, "U9100001", 1.25)
        app.execute("UPDATE gold.certified_builds SET status = 'published', published_at = now() WHERE build_id = %s",
                    (build_id,))
        app.commit()
        self.assertEqual(web.execute("SELECT value FROM gold.v_certified_figures WHERE build_id = %s",
                                     (build_id,)).fetchall(), [(1.25,)])
        web.execute("SET search_path = gold, public")  # what bootstrap gives runtime logins
        self.assertEqual(web.execute("SELECT count(*) FROM v_certified_figures WHERE build_id = %s",
                                     (build_id,)).fetchone()[0], 1)
        web.rollback()
        rights = ("SELECT has_table_privilege(%s, %s, 'INSERT') OR has_table_privilege(%s, %s, 'UPDATE') "
                  "OR has_table_privilege(%s, %s, 'DELETE')")
        for table in ("gold.certified_builds", "gold.certified_figures", "public.certified_figures"):
            with self.subTest(table=table):
                self.assertFalse(web.execute(rights, (WEB, table) * 3).fetchone()[0])
        approval = self._login(APPROVAL)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            approval.execute("SELECT count(*) FROM gold.v_certified_figures")

    # -- the rules the tables keep ---------------------------------------------------------------

    def test_figures_are_append_only_and_builds_are_never_removed(self):
        import psycopg

        with self._owner() as owner:
            build_id = self._build(owner)
            self._figure(owner, build_id, "append-a", 1.0)
            owner.commit()
            for statement in ("UPDATE gold.certified_figures SET value = 2", "DELETE FROM gold.certified_figures",
                              "TRUNCATE gold.certified_figures CASCADE", "DELETE FROM gold.certified_builds",
                              "TRUNCATE gold.certified_builds CASCADE",
                              "UPDATE public.certified_figures SET value = 2"):
                with self.subTest(statement=statement), self.assertRaises(psycopg.Error):
                    owner.execute(statement)
                owner.rollback()

    def test_a_build_moves_only_forward_and_takes_rows_only_in_shadow(self):
        import psycopg

        with self._owner() as owner:
            build_id = self._build(owner)
            owner.commit()
            refused = (
                ("UPDATE gold.certified_builds SET status = 'rolled_back', reason = 'x' WHERE build_id = %s", "go from"),
                ("UPDATE gold.certified_builds SET status = 'published' WHERE build_id = %s", "check"),
                ("UPDATE gold.certified_builds SET status = 'held' WHERE build_id = %s", "check"),
                ("UPDATE gold.certified_builds SET run_kind = 'backfill' WHERE build_id = %s", "can change"),
            )
            for statement, message in refused:
                with self.subTest(statement=statement), self.assertRaisesRegex(psycopg.Error, message):
                    owner.execute(statement, (build_id,))
                owner.rollback()
            owner.execute("UPDATE gold.certified_builds SET status = 'published', published_at = now() "
                          "WHERE build_id = %s", (build_id,))
            owner.commit()
            with self.assertRaisesRegex(psycopg.Error, "only to a build in shadow, not one that is published"):
                self._figure(owner, build_id, "late-row", 1.0)
            owner.rollback()
            for statement in ("UPDATE gold.certified_builds SET status = 'shadow' WHERE build_id = %s",
                              "UPDATE gold.certified_builds SET published_at = now() - interval '1 day' "
                              "WHERE build_id = %s"):
                with self.subTest(statement=statement), self.assertRaises(psycopg.Error):
                    owner.execute(statement, (build_id,))
                owner.rollback()
            owner.execute("UPDATE gold.certified_builds SET status = 'rolled_back', reason = 'Wrong FX rate.' "
                          "WHERE build_id = %s", (build_id,))
            owner.commit()

    def test_values_must_fit_their_unit_and_status(self):
        import psycopg

        with self._owner() as owner:
            build_id = self._build(owner)
            owner.commit()
            bad = (
                {"value": float("nan")}, {"value": float("inf")}, {"value": None},
                {"value": None, "status": "unavailable"}, {"value": 1.0, "status": "unavailable", "reason": "x"},
                {"value": None, "unit": "date", "value_text": "21 Oct 2026"},
                {"value": 1.0, "unit": "percent"}, {"value": 1.0, "input_sha256": "NOT-HEX"},
                {"value": 1.0, "definition_version": "short"}, {"value": 1.0, "figure_id": "Sharpe"},
                {"value": 1.0, "source_table": "public.trades"},
            )
            for columns in bad:
                with self.subTest(columns=columns), self.assertRaises(psycopg.Error):
                    value = columns.pop("value")
                    self._figure(owner, build_id, "bad", value, **columns)
                owner.rollback()
            self._figure(owner, build_id, "good", None, status="unavailable", reason="No validated rate.")
            self._figure(owner, build_id, "good", None, figure_id="overview.as_of", unit="date",
                         value_text="2026-10-16")
            owner.commit()

    def test_readers_see_the_newest_published_build_and_fall_back_when_it_is_rolled_back(self):
        subject = "view-" + uuid.uuid4().hex[:6]
        with self._owner() as owner:
            first = self._build(owner)
            self._figure(owner, first, subject, 1.0)
            self._figure(owner, first, subject, 0.5, as_of="2026-10-15")
            owner.execute("UPDATE gold.certified_builds SET status = 'published', published_at = now() - "
                          "interval '1 hour' WHERE build_id = %s", (first,))
            second = self._build(owner)
            self._figure(owner, second, subject, 1.1)  # only the value that changed
            owner.execute("UPDATE gold.certified_builds SET status = 'published', published_at = now() "
                          "WHERE build_id = %s", (second,))
            held = self._build(owner)
            self._figure(owner, held, subject, 9.0)
            owner.execute("UPDATE gold.certified_builds SET status = 'held', reason = 'Same inputs, new answer.' "
                          "WHERE build_id = %s", (held,))
            shadow = self._build(owner)
            self._figure(owner, shadow, subject, 8.0)
            owner.commit()
            query = ("SELECT as_of_date::text, value, build_status FROM gold.v_certified_figures "
                     "WHERE subject_id = %s ORDER BY as_of_date")
            self.assertEqual(owner.execute(query, (subject,)).fetchall(),
                             [("2026-10-15", 0.5, "published"), ("2026-10-16", 1.1, "published")])
            self.assertEqual(owner.execute("SELECT value FROM public.v_certified_figures WHERE subject_id = %s "
                                           "AND as_of_date = '2026-10-16'", (subject,)).fetchall(), [(1.1,)])
            owner.execute("UPDATE gold.certified_builds SET status = 'rolled_back', reason = 'Undone.' "
                          "WHERE build_id = %s", (second,))
            owner.commit()
            self.assertEqual(owner.execute(query, (subject,)).fetchall(),
                             [("2026-10-15", 0.5, "published"), ("2026-10-16", 1.0, "published")])
        with self._owner(OTHER_TENANT) as other:
            for table in ("gold.certified_builds", "gold.certified_figures", "gold.v_certified_figures",
                          "public.v_certified_figures"):
                with self.subTest(table=table):
                    self.assertEqual(other.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_contract_rules_hold(self):
        from ibkr_etl import medallion_contracts as mc

        contracts = Path(__file__).resolve().parents[1] / "infra" / "medallion" / "contracts" / "gold"
        with self._owner() as owner:
            build_id = self._build(owner)
            self._figure(owner, build_id, "rules", -0.2, figure_id="stats.maximum_drawdown",
                         definition_id="metric-maximum-drawdown-since-inception", unit="ratio")
            owner.execute("UPDATE gold.certified_builds SET status = 'published', published_at = now() "
                          "WHERE build_id = %s", (build_id,))
            owner.commit()
            for name in ("certified_builds", "certified_figures", "v_certified_figures"):
                for rule in mc.table_object(mc.load(contracts / f"{name}.yaml")).get("quality", []):
                    with self.subTest(table=name, rule=rule["name"]):
                        self.assertEqual(owner.execute(rule["query"]).fetchone()[0], rule["mustBe"])

    # -- lineage -----------------------------------------------------------------------------------

    def test_rows_name_their_run_and_trace_reaches_the_source_row(self):
        with lineage.run("ibkrv2stg-crisis-scenarios-daily") as crisis:
            session = connect(Path("/tmp/unused.sqlite3"), self.url, TENANT)
            try:
                crisis.bind(session)
                session.execute(
                    "INSERT INTO crisis_scenario_results(subject_id, holdings_as_of, scenario_id, calculation_version, "
                    "reporting_currency, crisis_start, crisis_end, available, maximum_drawdown, result_json, "
                    "generated_at) VALUES ('trace-c', '2026-10-16', 'lehman_2008', 'holdings-crisis-scenarios-v1', "
                    "'USD', '2008-09-15', '2009-03-09', true, -0.31, '{}'::jsonb, '2026-10-16T09:20:00Z')")
                session.commit()
                crisis.output("gold.crisis_scenario_results", rows=1)
            finally:
                session.close()
        source_key = {"subject_id": "trace-c", "holdings_as_of": "2026-10-16", "scenario_id": "lehman_2008",
                      "calculation_version": "holdings-crisis-scenarios-v1"}
        with lineage.run("ibkrv2stg-certified-figures") as build:
            session = connect(Path("/tmp/unused.sqlite3"), self.url, TENANT)
            try:
                build.bind(session)
                raw = session._connection
                build_id = self._build(raw)
                self._figure(raw, build_id, "trace-c", -0.31, figure_id="risk.crisis_scenario_max_drawdown",
                             item="lehman_2008", unit="ratio", definition_id="metric-crisis-scenario-maximum-drawdown",
                             source_table="gold.crisis_scenario_results", source_key=source_key)
                raw.execute("UPDATE gold.certified_builds SET status = 'published', published_at = now() "
                            "WHERE build_id = %s", (build_id,))
                raw.commit()
                build.input("gold.crisis_scenario_results")
                build.output("gold.certified_figures", rows=1)
            finally:
                session.close()
        with self._owner() as owner:
            stamped = owner.execute("SELECT lineage_run_id::text FROM gold.certified_builds WHERE build_id = %s",
                                    (build_id,)).fetchone()[0]
            self.assertEqual(stamped, build.run_id)
            key = {"subject_id": "trace-c", "figure_id": "risk.crisis_scenario_max_drawdown", "item": "lehman_2008",
                   "as_of_date": "2026-10-16"}
            found = lineage.trace(owner, "v_certified_figures", key)
            self.assertEqual(found["reference"], build.run_id)
            self.assertEqual([(step["run_id"], step["basis"]) for step in found["runs"]],
                             [(build.run_id, "row"), (crisis.run_id, "dataset-time")])
            self.assertEqual(found["source"]["table"], "gold.crisis_scenario_results")
            self.assertEqual(found["source"]["trace"]["reference"], crisis.run_id)
            by_build = lineage.trace(owner, "gold.certified_figures", {**key, "build_id": build_id})
            self.assertEqual(by_build["reference"], build.run_id)
            with self.assertRaises(ValueError):
                lineage.trace(owner, "gold.certified_figures", key)  # needs the build
        with self._owner(OTHER_TENANT) as other:
            self.assertFalse(lineage.trace(other, "v_certified_figures", key)["found"])


if __name__ == "__main__":
    unittest.main()
