"""The v1.1-to-Flex cutover on a fresh database with every migration applied.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN (CI starts one). Skipped otherwise. Creates and drops its own
database and uses synthetic accounts, secret names and statements only; no IBKR
or Key Vault request is made.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import quote
from zoneinfo import ZoneInfo

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
FIRST_TENANT = "00000000-0000-0000-0000-000000000001"
OWNER_ROLE = "flex_cutover_test_owner"
APPROVAL_ROLE = "flex_cutover_test_approval"
TEST_PASSWORD = "flex-cutover-test-only"  # throwaway roles in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
QUERY_ID = "908172635"
APPROVER = "a" * 64
START = "2025-01-20"
# What the website's approval login may do on the tables the cutover touches: no DELETE.
APPROVAL_GRANTS = (
    "GRANT SELECT, INSERT, UPDATE ON TABLE platform_subjects, ibkr_flex_credentials, ibkr_flex_queries, "
    "ibkr_flex_source_accounts, subject_flex_source_bindings, subject_operating_registrations, "
    "institutional_work_items TO {role}",
    "GRANT INSERT ON TABLE audit_events TO {role}",
    "GRANT USAGE, SELECT ON SEQUENCE audit_events_audit_event_id_seq TO {role}",
)


def _dsn(base: str, database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(base)
    params["dbname"] = database
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


def _url(database: str, user: str) -> str:
    """The scripts only accept a postgresql:// URL."""
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(ADMIN_DSN)
    host, port = params.get("host") or "localhost", params.get("port") or "5432"
    credentials = f"{quote(user)}:{quote(TEST_PASSWORD)}"
    if host.startswith("/"):
        return f"postgresql://{credentials}@/{quote(database)}?host={quote(host)}&port={port}"
    return f"postgresql://{credentials}@{host}:{port}/{quote(database)}"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"flex_cutover_test_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def statement(report_date: date, *accounts: str) -> bytes:
    """A small synthetic daily statement: account details, cash and NAV."""
    day = report_date.strftime("%Y%m%d")
    blocks = []
    for index, account in enumerate(accounts):
        nav = 10000 + index
        blocks.append(f"""
    <FlexStatement accountId="{account}" fromDate="{day}" toDate="{day}" period="LastBusinessDay">
      <AccountInformation accountId="{account}" accountAlias="Synthetic" currency="USD" accountType="Individual" />
      <CashReport>
        <CashReportCurrency accountId="{account}" reportDate="{day}" currency="USD" endingCash="{nav}"
          settledCash="{nav}" fxRateToBase="1" />
      </CashReport>
      <NetAssetValue>
        <NetAssetValueByAssetClass accountId="{account}" reportDate="{day}" currency="USD" assetClass="TOTAL"
          total="{nav}" cash="{nav}" stock="0" />
      </NetAssetValue>
    </FlexStatement>""")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<FlexQueryResponse queryName="Synthetic daily" type="AF">\n'
        f'  <FlexStatements count="{len(accounts)}">{"".join(blocks)}\n  </FlexStatements>\n'
        "</FlexQueryResponse>\n"
    ).encode()


class CutoverHelperTests(unittest.TestCase):
    def test_registration_digest_matches_the_stored_format(self):
        from ibkr_etl.flex_cutover import registration_configuration
        from ibkr_etl.operating_registry import configuration_sha256

        configuration, digest = registration_configuration(START, "existing_v11")
        # The digest migration 051 stored for the pilot's existing_v11 registration.
        self.assertEqual(digest, "1043acbd3ff44ed1b1c4041734912c2bbc559ec4bf3bf6db6bd951fcf4458041")
        self.assertEqual(digest, configuration_sha256(json.loads(configuration)))

    def test_dispatcher_asks_for_yesterday_only_when_told(self):
        dispatcher = _load_script("dispatch_institutional_daily_work")
        today = date(2026, 10, 21)
        self.assertEqual(dispatcher.default_run_date(today, "current_hkt_day"), today)
        self.assertEqual(dispatcher.default_run_date(today, "previous_hkt_day"), date(2026, 10, 20))
        with self.assertRaises(RuntimeError):
            dispatcher.default_run_date(today, "next_hkt_day")


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class FlexCutoverDatabaseTests(unittest.TestCase):
    """Each test has its own tenant, so what account-sync imports differs per test."""

    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.db import PostgresConnection

        cls.database = "flex_cutover_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in (OWNER_ROLE, APPROVAL_ROLE):
                if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
                    # Not superusers, so forced row-level security applies as on Azure.
                    admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER_ROLE}')
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        connection = PostgresConnection(psycopg.connect(_dsn(ADMIN_DSN, cls.database, OWNER_ROLE)))
        connection.execute("SELECT set_config('app.tenant_id', ?, false)", (FIRST_TENANT,))
        connection.commit()
        for path in sorted((ROOT / "migrations" / "postgres").glob("*.sql")):
            try:
                connection.execute(path.read_text(encoding="utf-8"))
                connection.commit()
            except Exception:
                connection.rollback()
                if path.name not in TOLERATED_MIGRATIONS:
                    raise
            if connection.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                    connection.execute("SELECT 1 FROM tenants").fetchone() is None:
                connection.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                   "VALUES (?, 'Test', 'active', '2026-01-01')", (FIRST_TENANT,))
                connection.commit()
        connection.close()
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database), autocommit=True) as admin:
            admin.execute(f"GRANT USAGE ON SCHEMA public TO {APPROVAL_ROLE}")
            for grant in APPROVAL_GRANTS:
                admin.execute(grant.format(role=APPROVAL_ROLE))

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')
            admin.execute(f"DROP OWNED BY {APPROVAL_ROLE}")

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row

        self.tenant = str(uuid.uuid4())
        with psycopg.connect(_dsn(ADMIN_DSN, self.database), autocommit=True) as admin:
            admin.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                          "VALUES (%s, 'Synthetic', 'active', '2026-01-01')", (self.tenant,))
        options = f"-c app.tenant_id={self.tenant}"
        # The owner reads and sets up; the cutover itself runs as the approval login.
        self.db = psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE), row_factory=dict_row,
                                  options=options)
        self.addCleanup(self.db.close)
        self.approval = psycopg.connect(_dsn(ADMIN_DSN, self.database, APPROVAL_ROLE), row_factory=dict_row,
                                        options=options)
        self.addCleanup(self.approval.close)
        self.secret_name = "ibkr-flex-source-" + uuid.uuid4().hex

    # Helpers

    def _subject(self, subject_id, *, state="active", source="existing_v11", effective_from=START,
                 registered=True):
        from ibkr_etl.flex_cutover import registration_configuration

        self.db.execute(
            "INSERT INTO platform_subjects (subject_id, subject_type, display_name, source_account_id, "
            "base_currency, lifecycle_status) VALUES (%s, 'physical_account', 'Synthetic', %s, 'USD', %s)",
            (subject_id, subject_id, "active" if state == "active" else "onboarding"))
        if registered:
            configuration, digest = registration_configuration(START, source)
            self.db.execute(
                "INSERT INTO subject_operating_registrations (registration_id, subject_id, capability, "
                "operating_state, effective_from, configuration_json, configuration_sha256) "
                "VALUES (%s, %s, 'daily_ingestion', %s, %s, %s::jsonb, %s)",
                (uuid.uuid4(), subject_id, state, effective_from, configuration, digest))
        self.db.commit()

    def _request(self, effective_date, content, **changes):
        from ibkr_etl.flex_cutover import CutoverRequest

        values = {"tenant_id": self.tenant, "effective_date": effective_date,
                  "token_secret_name": self.secret_name, "query_id": QUERY_ID, "statement": content,
                  "approver_sha256": APPROVER}
        values.update(changes)
        return CutoverRequest(**values)

    def _apply(self, request, today):
        from ibkr_etl.flex_cutover import apply_cutover

        result = apply_cutover(self.approval, request, today=today)
        self.approval.commit()
        return result

    def _snapshot(self):
        """Every row the cutover or its revert can write, for this tenant."""
        queries = {
            "registrations": "SELECT subject_id, operating_state, effective_from, effective_to, "
                             "configuration_sha256 FROM subject_operating_registrations ORDER BY 1, 3",
            "bindings": "SELECT subject_id, flex_source_id, effective_from, effective_to, status "
                        "FROM subject_flex_source_bindings ORDER BY 1, 3",
            "credentials": "SELECT credential_id, status, display_name FROM ibkr_flex_credentials ORDER BY 1",
            "queries": "SELECT flex_source_id, status, schedule_enabled FROM ibkr_flex_queries ORDER BY 1",
            "accounts": "SELECT source_account_id, discovery_status FROM ibkr_flex_source_accounts ORDER BY 1",
            "audit": "SELECT event_type FROM audit_events WHERE actor_id = 'v11-flex-cutover' "
                     "ORDER BY audit_event_id",
            "work": "SELECT idempotency_key, status FROM institutional_work_items ORDER BY 1",
        }
        result = {name: [tuple(row.values()) for row in self.db.execute(sql).fetchall()]
                  for name, sql in queries.items()}
        self.db.rollback()
        return result

    def _bindings(self, as_of):
        from ibkr_etl.db import PostgresConnection
        from ibkr_etl.operating_registry import load_active_flex_bindings

        bindings = load_active_flex_bindings(PostgresConnection(self.db), tenant_id=self.tenant, as_of=as_of)
        self.db.rollback()
        return bindings

    def _environment(self, directory, **extra):
        values = {
            "DEPLOYMENT_ENVIRONMENT": "internal-production",
            "DATABASE_URL": _url(self.database, OWNER_ROLE),
            "PORTFOLIO_TENANT_ID": self.tenant,
            "INSTITUTIONAL_DISPATCH_RUN_DATE": "previous_hkt_day",
            "INSTITUTIONAL_FLEX_LOOKBACK_DAYS": "14",
            "FLEX_TOKEN_VAULT_URI": "https://synthetic.vault.azure.net",
            "AZURE_CLIENT_ID": "00000000-0000-0000-0000-0000000000c1",
            "IBKR_RUN_MIGRATIONS": "false",
            "RAW_REPORT_DIR": str(Path(directory) / "raw"),
            "DATABASE_PATH": str(Path(directory) / "unused.sqlite3"),
            "AZURE_STORAGE_ACCOUNT_URL": "",
        }
        values.update(extra)
        return values

    def _run_dispatcher(self, **extra):
        dispatcher = _load_script("dispatch_institutional_daily_work")
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, self._environment(directory, **extra)), \
                contextlib.redirect_stdout(output):
            if "WORK_RUN_DATE" not in extra:
                os.environ.pop("WORK_RUN_DATE", None)
            self.assertEqual(dispatcher.main(), 0)
        return json.loads(output.getvalue())

    def _run_worker(self, client):
        worker = _load_script("run_institutional_flex_worker")
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, self._environment(directory)), \
                mock.patch.object(worker, "_secret_value", return_value="synthetic-token"), \
                mock.patch.object(worker, "FlexClient", client), \
                contextlib.redirect_stdout(io.StringIO()):
            return worker.main()

    # Tests

    def test_dry_run_apply_and_rerun_move_exactly_the_v11_accounts(self):
        from ibkr_etl.flex_cutover import _v11_targets, plan_cutover, registration_configuration
        from ibkr_etl.operating_registry import plan_flex_work

        x, today = date(2026, 10, 20), date(2026, 10, 21)
        self._subject("U9100001")
        self._subject("U9100002")
        self._subject("U9100003", state="preview")
        self._subject("U9100004", source="kiwoom_rest")  # not fed by v1.1, so left alone
        request = self._request(x, statement(x, "U9100001", "U9100002", "U9100003", "U9100009"))
        before = self._snapshot()

        plan = plan_cutover(self.approval, request, today=today)
        self.approval.rollback()
        self.assertEqual(plan["status"], "ready", plan["problems"])
        self.assertEqual(self._snapshot(), before)
        self.assertEqual([item["subject_id"] for item in plan["subjects"]], ["U9100001", "U9100002", "U9100003"])
        self.assertEqual((plan["credential"]["action"], plan["query"]["action"]), ("insert", "insert"))
        self.assertEqual((plan["closes_existing_v11_on"], plan["first_dispatch_day"]["previous_hkt_day"]),
                         ("2026-10-19", "2026-10-21"))
        self.assertNotIn(QUERY_ID, json.dumps(plan, default=str))
        self.assertTrue(any(w.startswith("U9100003: preview subjects move too") for w in plan["warnings"]))
        self.assertTrue(any(w.endswith("these are not moved: U9100009") for w in plan["warnings"]))

        applied = self._apply(request, today)
        self.assertEqual(applied["status"], "applied")
        source = applied["query"]["flex_source_id"]

        # The dispatcher now finds exactly the moved active subjects, from the effective date only.
        bindings = self._bindings(x)
        self.assertEqual([b.subject_id for b in bindings], ["U9100001", "U9100002"])
        self.assertEqual({(b.flex_source_id, b.source_account_id[:3], b.start_date) for b in bindings},
                         {(source, "U91", date(2025, 1, 20))})
        self.assertEqual(self._bindings(x - timedelta(days=1)), [])
        self.assertEqual([item["work_kind"] for item in plan_flex_work(bindings, run_date=x)],
                         ["flex_statement_request"]
                         + ["source_partition", "account_import", "analytics_rebuild"] * 2)
        self.assertEqual(_v11_targets(self.db, self.tenant), [])  # account-sync has nothing left to import
        self.db.rollback()

        _, flex_digest = registration_configuration(START, "ibkr_flex")
        rows = self.db.execute(
            "SELECT subject_id, operating_state, effective_from, effective_to, configuration_json, "
            "configuration_sha256 FROM subject_operating_registrations ORDER BY subject_id, effective_from"
        ).fetchall()
        start, closed = date(2025, 1, 20), date(2026, 10, 19)
        self.assertEqual(
            [(r["subject_id"], r["operating_state"], r["effective_from"], r["effective_to"],
              r["configuration_json"]["source"]) for r in rows],
            [("U9100001", "active", start, closed, "existing_v11"), ("U9100001", "active", x, None, "ibkr_flex"),
             ("U9100002", "active", start, closed, "existing_v11"), ("U9100002", "active", x, None, "ibkr_flex"),
             ("U9100003", "preview", start, closed, "existing_v11"), ("U9100003", "preview", x, None, "ibkr_flex"),
             ("U9100004", "active", start, None, "kiwoom_rest")])
        self.assertEqual({r["configuration_sha256"] for r in rows if r["effective_from"] == x}, {flex_digest})
        self.assertEqual(
            {r["subject_id"]: (r["status"], r["is_primary"], r["effective_from"]) for r in self.db.execute(
                "SELECT subject_id, status, is_primary, effective_from FROM subject_flex_source_bindings")},
            {"U9100001": ("active", True, x), "U9100002": ("active", True, x), "U9100003": ("pending", True, x)})
        self.assertEqual(
            {r["source_account_id"]: r["discovery_status"] for r in self.db.execute(
                "SELECT source_account_id, discovery_status FROM ibkr_flex_source_accounts")},
            {"U9100001": "selected", "U9100002": "selected", "U9100003": "selected", "U9100009": "discovered"})
        credential = self.db.execute(
            "SELECT credential_id::text AS credential_id, key_vault_secret_name, status, "
            "last_validated_at IS NOT NULL AS validated, approved_by_identity_sha256 FROM ibkr_flex_credentials"
        ).fetchone()
        self.assertEqual(credential, {"credential_id": str(uuid.UUID(self.secret_name[-32:])),
                                      "key_vault_secret_name": self.secret_name, "status": "active",
                                      "validated": True, "approved_by_identity_sha256": APPROVER})
        query = self.db.execute("SELECT flex_source_id::text AS id, query_id, status, schedule_enabled "
                                "FROM ibkr_flex_queries").fetchone()
        self.assertEqual(query, {"id": source, "query_id": QUERY_ID, "status": "active", "schedule_enabled": True})
        self.db.rollback()
        self.assertEqual(self._snapshot()["audit"], [("v11_accounts_moved_to_flex",)])

        # A re-run finds everything in place and writes nothing, not even an audit event.
        after = self._snapshot()
        again = plan_cutover(self.approval, request, today=today)
        self.approval.rollback()
        self.assertEqual(again["status"], "already_applied", again["problems"])
        self.assertEqual({item["action"] for item in again["subjects"]}, {"already_moved"})
        self.assertEqual(self._apply(request, today)["status"], "already_applied")
        self.assertEqual(self._snapshot(), after)

    def test_refusals_write_nothing(self):
        from ibkr_etl.flex_cutover import CutoverRefused, apply_cutover, plan_cutover

        x, today = date(2026, 10, 20), date(2026, 10, 21)
        self._subject("U9200001")
        self._subject("U9200002", source="kiwoom_rest")
        self._subject("U9200003", effective_from=x)
        self._subject("U9200004")
        self._subject("U9200005", registered=False)
        content = statement(x, "U9200001", "U9200002", "U9200003", "U9200004", "U9200005")

        def refused(expected, *subjects, **changes):
            request = self._request(changes.pop("effective_date", x), changes.pop("content", content),
                                    subject_ids=subjects, **changes)
            before = self._snapshot()
            plan = plan_cutover(self.approval, request, today=today)
            self.approval.rollback()
            self.assertEqual(plan["status"], "refused")
            self.assertTrue(any(expected in problem for problem in plan["problems"]), plan["problems"])
            with self.assertRaises(CutoverRefused):
                apply_cutover(self.approval, request, today=today)
            self.approval.rollback()
            self.assertEqual(self._snapshot(), before)

        refused("account U9200001 is not in the statement", "U9200001", content=statement(x, "U9200002"))
        refused("The statement cannot be read", "U9200001", content=b"<not-a-statement")
        refused("U9299999: the subject does not exist", "U9299999")
        refused("The effective date is in the future", "U9200001", effective_date=today + timedelta(days=1))
        refused("must look like ibkr-flex-source-<32 hex>", "U9200001", token_secret_name="ibkr-flex-token")
        refused("digits only", "U9200001", query_id="12a")
        refused("does not come from the v1.1 export (kiwoom_rest)", "U9200002")
        refused("starts on or after the effective date", "U9200003")
        refused("no single current daily_ingestion registration", "U9200005")

        applied = self._apply(self._request(x, content, subject_ids=("U9200001",)), today)
        self.assertEqual([item["subject_id"] for item in applied["subjects"]], ["U9200001"])
        # A second cutover on another date, or with another token, does not touch a moved account.
        refused("U9200001: already on a Flex registration with other settings", "U9200001",
                effective_date=x - timedelta(days=1))
        refused("Another credential already uses this display name", "U9200004",
                token_secret_name="ibkr-flex-source-" + uuid.uuid4().hex)
        # An account bound to a Flex source by other means is not moved again.
        self.db.execute(
            "INSERT INTO subject_flex_source_bindings (subject_id, flex_source_id, source_account_id, "
            "effective_from, is_primary, status) VALUES ('U9200004', %s, 'U9200004', '2026-10-01', true, 'active')",
            (applied["query"]["flex_source_id"],))
        self.db.commit()
        refused("U9200004: it already has an open primary Flex binding", "U9200004")
        self.db.execute("UPDATE ibkr_flex_credentials SET status = 'revoked'")
        self.db.commit()
        refused("The credential is revoked", "U9200001")

    def test_revert_restores_the_export_and_the_cutover_applies_again(self):
        from ibkr_etl.burn_in import collect_import_state
        from ibkr_etl.db import PostgresConnection
        from ibkr_etl.flex_cutover import _v11_targets, apply_revert, plan_cutover, plan_revert
        from ibkr_etl.operating_registry import enqueue_work_plan, plan_flex_work

        x, today = date(2026, 10, 20), date(2026, 10, 21)
        self._subject("U9300001")
        self._subject("U9300002")
        request = self._request(x, statement(x, "U9300001", "U9300002"))
        nothing = plan_revert(self.approval, tenant_id=self.tenant, effective_date=x)
        self.approval.rollback()
        self.assertEqual(nothing["problems"], ["No subject was moved to the Flex path on this effective date"])

        source = self._apply(request, today)["query"]["flex_source_id"]
        enqueue_work_plan(PostgresConnection(self.db), tenant_id=self.tenant,
                          work_plan=plan_flex_work(self._bindings(x), run_date=x))
        self.db.commit()
        self.assertEqual(collect_import_state(self.db, "U9300001", as_of=today)["daily_feed"], "ibkr_flex")
        self.db.rollback()

        before = self._snapshot()
        plan = plan_revert(self.approval, tenant_id=self.tenant, effective_date=x)
        self.approval.rollback()
        self.assertEqual((plan["status"], plan["flex_source_ids"]), ("ready", [source]))
        self.assertEqual(self._snapshot(), before)

        reverted = apply_revert(self.approval, tenant_id=self.tenant, effective_date=x, approver_sha256=APPROVER)
        self.approval.commit()
        self.assertEqual((reverted["status"], reverted["schedules_stopped"]), ("reverted", [source]))
        rows = self.db.execute(
            "SELECT subject_id, operating_state, effective_from, effective_to, configuration_json ->> 'source' AS "
            "source FROM subject_operating_registrations ORDER BY subject_id, effective_from").fetchall()
        start = date(2025, 1, 20)
        self.assertEqual([tuple(r.values()) for r in rows],
                         [("U9300001", "active", start, None, "existing_v11"),
                          ("U9300001", "disabled", x, x, "ibkr_flex"),
                          ("U9300002", "active", start, None, "existing_v11"),
                          ("U9300002", "disabled", x, x, "ibkr_flex")])
        self.assertEqual({(r["status"], r["effective_to"]) for r in self.db.execute(
            "SELECT status, effective_to FROM subject_flex_source_bindings")}, {("retired", x)})
        self.assertFalse(self.db.execute("SELECT schedule_enabled FROM ibkr_flex_queries").fetchone()
                         ["schedule_enabled"])
        self.assertEqual({(r["status"], r["error_code"]) for r in self.db.execute(
            "SELECT status, error_code FROM institutional_work_items")}, {("cancelled", "cutover_reverted")})
        self.assertEqual(_v11_targets(self.db, self.tenant), ["U9300001", "U9300002"])
        self.assertEqual(collect_import_state(self.db, "U9300001", as_of=today), {"latest_import": None})
        self.db.rollback()
        self.assertEqual(self._bindings(x), [])
        self.assertEqual(self._snapshot()["audit"],
                         [("v11_accounts_moved_to_flex",), ("v11_accounts_returned_to_export",)])

        # Reverting again changes nothing.
        after = self._snapshot()
        again = apply_revert(self.approval, tenant_id=self.tenant, effective_date=x, approver_sha256=APPROVER)
        self.approval.commit()
        self.assertEqual(again["status"], "already_reverted")
        self.assertEqual(self._snapshot(), after)

        # The cutover applies again on the same rows.
        reapplied = self._apply(request, today)
        self.assertEqual((reapplied["status"], reapplied["credential"]["action"], reapplied["query"]["action"]),
                         ("applied", "keep", "activate"))
        self.assertEqual([b.subject_id for b in self._bindings(x)], ["U9300001", "U9300002"])
        snapshot = self._snapshot()
        self.assertEqual(len(snapshot["registrations"]), 4)
        self.assertEqual({row[4] for row in snapshot["bindings"]}, {"active"})
        self.assertEqual(plan_cutover(self.approval, request, today=today)["status"], "already_applied")
        self.approval.rollback()

    def test_first_pull_asks_up_to_yesterday_and_satisfies_the_burn_in(self):
        from ibkr_etl.burn_in import collect_import_state, flex_import_failures
        from ibkr_etl.flex_client import FlexReport

        today = datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
        x = today - timedelta(days=1)  # switch day D = today, effective date D - 1
        self._subject("U9400001")
        self._subject("U9400002")
        content = statement(x, "U9400001", "U9400002")
        self._apply(self._request(x, content), today)
        calls = []

        class FakeClient:
            def __init__(self, settings):
                calls.append({"query_id": settings.flex_query_id, "token": settings.flex_token})

            def request_report(self, start, end):
                calls[-1]["window"] = (start, end)
                return FlexReport(content=content, content_type="application/xml", reference_code="SYNTHETIC-1")

        summary = self._run_dispatcher()
        self.assertEqual(
            (summary["run_date"], summary["run_date_mode"], summary["subject_count"], summary["work_item_count"]),
            (x.isoformat(), "previous_hkt_day", 2, 7))
        self.assertEqual(self._run_worker(FakeClient), 0)
        self.assertEqual(calls, [{"query_id": QUERY_ID, "token": "synthetic-token",
                                  "window": (x - timedelta(days=13), x)}])

        # Nothing claims analytics_rebuild yet; the daily jobs after the pull do that work.
        self.assertEqual([(r["work_kind"], r["status"]) for r in self.db.execute(
            "SELECT work_kind, status FROM institutional_work_items ORDER BY work_kind, subject_id")],
            [("account_import", "succeeded")] * 2 + [("analytics_rebuild", "queued")] * 2
            + [("flex_statement_request", "succeeded")] + [("source_partition", "succeeded")] * 2)
        run = self.db.execute("SELECT run_id, status, requested_from, requested_to FROM ingestion_runs "
                              "WHERE run_type = 'institutional-flex-daily'").fetchall()
        self.assertEqual([(r["status"], r["requested_from"], r["requested_to"]) for r in run],
                         [("success", (x - timedelta(days=13)).isoformat(), x.isoformat())])
        nav = {r["account_id"]: r["last_seen_run_id"] for r in self.db.execute(
            "SELECT account_id, last_seen_run_id FROM nav_snapshots WHERE asset_class = 'TOTAL'")}
        self.assertEqual(nav, {"U9400001": run[0]["run_id"], "U9400002": run[0]["run_id"]})
        circuit = self.db.execute("SELECT last_send_at IS NOT NULL AS sent, requires_manual_reset, "
                                  "consecutive_failures FROM ibkr_credential_circuit_state").fetchone()
        self.assertEqual(circuit, {"sent": True, "requires_manual_reset": False, "consecutive_failures": 0})

        # The burn-in and the administrator now judge the account by this pull.
        state = collect_import_state(self.db, "U9400001", as_of=today)
        self.db.rollback()
        self.assertEqual((state["daily_feed"], state["latest_nav_run"]["run_id"]), ("ibkr_flex", run[0]["run_id"]))
        self.assertEqual(flex_import_failures(state, today_hkt=today), [])

    def test_a_lockout_is_recorded_and_the_next_run_leaves_it_alone(self):
        from ibkr_etl.flex_client import FlexError
        from ibkr_etl.flex_discovery import ValidationBlocked

        x, today = date(2026, 10, 20), date(2026, 10, 21)
        self._subject("U9500001")
        credential_id = self._apply(self._request(x, statement(x, "U9500001")), today)["credential"]["credential_id"]

        class LockedOut:
            def __init__(self, settings):
                pass

            def request_report(self, start, end):
                raise FlexError("Too many failed attempts", code="1018", phase="send")

        class MustNotSend:
            def __init__(self, settings):
                raise AssertionError("A credential that needs a manual reset sent a request")

        def circuit():
            row = self.db.execute(
                "SELECT requires_manual_reset, blocked_reason, last_error_code, consecutive_failures, last_send_at "
                "FROM ibkr_credential_circuit_state WHERE credential_id = %s", (credential_id,)).fetchone()
            self.db.rollback()
            return row

        self._run_dispatcher(WORK_RUN_DATE=x.isoformat())
        with self.assertRaises(FlexError):
            self._run_worker(LockedOut)
        locked = circuit()
        self.assertEqual(
            (locked["requires_manual_reset"], locked["blocked_reason"], locked["last_error_code"],
             locked["consecutive_failures"]),
            (True, "lockout-or-credentials", "1018", 1))

        self._run_dispatcher(WORK_RUN_DATE=(x + timedelta(days=1)).isoformat())
        with self.assertRaises(ValidationBlocked):
            self._run_worker(MustNotSend)
        self.assertEqual(circuit(), locked)
        requests = [(r["run_date"], r["status"], r["error_code"]) for r in self.db.execute(
            "SELECT payload_json ->> 'run_date' AS run_date, status, error_code FROM institutional_work_items "
            "WHERE work_kind = 'flex_statement_request' ORDER BY 1")]
        self.assertEqual(requests, [(x.isoformat(), "dead_letter", "1018"),
                                    ((x + timedelta(days=1)).isoformat(), "dead_letter", "ValidationBlocked")])

    def test_the_command_dry_runs_applies_and_reverts(self):
        from ibkr_etl.flex_cutover import identity_sha256

        cli = _load_script("cutover_v11_accounts_to_flex")
        today = datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
        x = today - timedelta(days=1)
        self._subject("U9600001")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "v11-statement.xml"
        path.write_bytes(statement(x, "U9600001"))
        environment = {"DATABASE_URL": _url(self.database, APPROVAL_ROLE), "PORTFOLIO_TENANT_ID": self.tenant,
                       "CUTOVER_FLEX_QUERY_ID": QUERY_ID}

        def run(*argv):
            output = io.StringIO()
            with mock.patch.dict(os.environ, environment), contextlib.redirect_stdout(output):
                code = cli.main(list(argv))
            self.assertNotIn(QUERY_ID, output.getvalue())
            return code, json.loads(output.getvalue())

        cutover = ["--effective-date", x.isoformat(), "--token-secret-name", self.secret_name,
                   "--statement", str(path)]
        approver = ["--approved-by", "owner@example.com"]
        before = self._snapshot()
        code, dry = run(*cutover)
        self.assertEqual((code, dry["mode"], dry["status"]), (0, "dry_run", "ready"))
        self.assertEqual(self._snapshot(), before)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main([*cutover, "--apply"])  # an apply needs an approver
        code, applied = run(*cutover, "--apply", *approver)
        self.assertEqual((code, applied["mode"], applied["status"]), (0, "apply", "applied"))
        code, again = run(*cutover, "--apply", *approver)
        self.assertEqual((code, again["status"]), (0, "already_applied"))
        code, refused = run("--effective-date", (today + timedelta(days=1)).isoformat(),
                            "--token-secret-name", self.secret_name, "--statement", str(path), "--apply", *approver)
        self.assertEqual((code, refused["status"]), (2, "refused"))
        code, reverted = run("--revert", "--effective-date", x.isoformat(), "--apply", *approver)
        self.assertEqual((code, reverted["status"]), (0, "reverted"))
        details = [r["details_json"] for r in self.db.execute(
            "SELECT details_json FROM audit_events WHERE actor_id = 'v11-flex-cutover' ORDER BY audit_event_id")]
        self.db.rollback()
        self.assertEqual([json.loads(d)["approved_by_identity_sha256"] if isinstance(d, str)
                          else d["approved_by_identity_sha256"] for d in details],
                         [identity_sha256("owner@example.com")] * 2)


if __name__ == "__main__":
    unittest.main()
