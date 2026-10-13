"""Database rules for the new account onboarding (migration 064).

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN (CI starts one). Skipped otherwise. Creates and drops its own
database and uses synthetic data only.
"""
from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
OWNER_ROLE = "broker_onboarding_test_owner"
TEST_PASSWORD = "broker-onboarding-test-only"  # throwaway role in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
IDENTITY = "a" * 64


def _dsn(base: str, database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(base)
    params["dbname"] = database
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


class _MigratedDatabase(unittest.TestCase):
    """A fresh database with every migration applied, dropped afterwards."""

    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.db import PostgresConnection

        cls.database = "broker_onboarding_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER_ROLE,)).fetchone() is None:
                # Not a superuser, so forced row-level security applies as on Azure.
                admin.execute(f"CREATE ROLE {OWNER_ROLE} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER_ROLE}')
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        connection = PostgresConnection(psycopg.connect(_dsn(ADMIN_DSN, cls.database, OWNER_ROLE)))
        connection.execute("SELECT set_config('app.tenant_id', ?, false)", (TENANT,))
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
                                   "VALUES (?, 'Test', 'active', '2026-01-01')", (TENANT,))
                connection.commit()
        connection.close()
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database), autocommit=True) as admin:
            admin.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                          "VALUES (%s, 'Other', 'active', '2026-01-01')", (OTHER_TENANT,))

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class BrokerOnboardingSchemaTests(_MigratedDatabase):
    def setUp(self):
        import psycopg

        self.db = psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE),
                                  options=f"-c app.tenant_id={TENANT}")
        self.addCleanup(self.db.close)
        self.addCleanup(self.db.rollback)  # every test leaves the database as it found it

    def _connection(self, broker="kiwoom_rest", **overrides):
        connection_id = uuid.uuid4()
        prefix = "ibkr-flex-source-" if broker == "ibkr_flex" else "broker-kiwoom-rest-"
        values = {
            "connection_id": connection_id,
            "broker": broker,
            "environment": "live" if broker == "ibkr_flex" else "mock",
            "display_name": f"Test {connection_id.hex[:8]}",
            "key_vault_secret_name": prefix + connection_id.hex,
            "credential_fingerprint": "0" * 16,
            "credential_expires_on": "2027-10-01" if broker == "ibkr_flex" else None,
            "status": "pending_check",
        }
        values.update(overrides)
        columns = ", ".join(values)
        placeholders = ", ".join(["%s"] * len(values))
        self.db.execute(f"INSERT INTO broker_connections ({columns}) VALUES ({placeholders})", list(values.values()))
        return connection_id

    def _account(self, connection_id, account_id):
        self.db.execute(
            "INSERT INTO broker_connection_accounts (connection_id, broker_account_id, discovery_status) "
            "VALUES (%s, %s, 'discovered')", (connection_id, account_id))

    def _case(self, connection_id, account_id, *, broker="kiwoom_rest", stage="request", status="platform_working",
              subject_id=None, went_live=False):
        case_id = uuid.uuid4()
        self.db.execute(
            "INSERT INTO broker_onboarding_cases (case_id, connection_id, broker, broker_account_id, "
            "target_subject_id, display_name, reporting_currency, history_start_date, subject_id, stage, status, "
            "went_live_at) VALUES (%s, %s, %s, %s, %s, 'Synthetic', 'KRW', '2025-01-02', %s, %s, %s, "
            "CASE WHEN %s THEN now() END)",
            (case_id, connection_id, broker, account_id, "KIWOOM-" + account_id, subject_id, stage, status,
             went_live))
        return case_id

    def _subject(self, subject_id):
        self.db.execute(
            "INSERT INTO platform_subjects (subject_id, subject_type, display_name, lifecycle_status) "
            "VALUES (%s, 'physical_account', 'Synthetic', 'onboarding')", (subject_id,))

    def assertRejected(self, statement, params=()):
        import psycopg

        with self.assertRaises(psycopg.Error):
            with self.db.transaction():
                self.db.execute(statement, params)

    def test_only_one_account_is_onboarded_at_a_time(self):
        import psycopg

        connection_id = self._connection()
        for account in ("50000001", "50000002", "50000003"):
            self._account(connection_id, account)
        first = self._case(connection_id, "50000001")
        with self.assertRaises(psycopg.errors.UniqueViolation), self.db.transaction():
            self._case(connection_id, "50000002")
        self._subject("KIWOOM-50000001")
        self.db.execute("UPDATE broker_onboarding_cases SET stage = 'first_month_end', status = 'live', "
                        "subject_id = 'KIWOOM-50000001', went_live_at = now() WHERE case_id = %s", (first,))
        self._case(connection_id, "50000002")
        with self.assertRaises(psycopg.errors.UniqueViolation), self.db.transaction():
            self._case(connection_id, "50000003")

    def test_an_account_is_never_onboarded_twice(self):
        import psycopg

        first_connection, second_connection = self._connection(), self._connection()
        self._account(first_connection, "50000009")
        self._account(second_connection, "50000009")
        case_id = self._case(first_connection, "50000009")
        self.db.execute("UPDATE broker_onboarding_cases SET status = 'cancelled' WHERE case_id = %s", (case_id,))
        self._case(second_connection, "50000009")
        self._subject("KIWOOM-50000009")
        with self.assertRaises(psycopg.errors.UniqueViolation), self.db.transaction():
            self._case(first_connection, "50000009", stage="first_month_end", status="live",
                       subject_id="KIWOOM-50000009", went_live=True)

    def test_stage_rules_hold_in_the_database(self):
        connection_id = self._connection()
        self._account(connection_id, "50000010")
        base = ("INSERT INTO broker_onboarding_cases (case_id, connection_id, broker, broker_account_id, "
                "target_subject_id, display_name, reporting_currency, history_start_date, stage, status) "
                "VALUES (gen_random_uuid(), %s, 'kiwoom_rest', '50000010', 'KIWOOM-50000010', 'Synthetic', 'KRW', "
                "'2025-01-02', %s, %s)")
        self.assertRejected(base, (connection_id, "build_history", "platform_working"))  # no subject yet
        self.assertRejected(base, (connection_id, "closed", "platform_working"))
        self.assertRejected(base, (connection_id, "request", "closed"))
        self.assertRejected(base, (connection_id, "nowhere", "platform_working"))

    def test_a_derived_case_has_a_definition_and_no_broker(self):
        import psycopg

        connection_id = self._connection()
        self._account(connection_id, "50000011")
        derived = ("INSERT INTO broker_onboarding_cases (case_id, source_kind, connection_id, broker, "
                   "broker_account_id, definition_json, definition_sha256, target_subject_id, description, "
                   "display_name, reporting_currency, history_start_date, stage, status) VALUES "
                   "(gen_random_uuid(), 'composite_portfolio', %s, %s, %s, %s, %s, %s, %s, 'Synthetic', 'HKD', "
                   "'2025-01-20', 'request', 'platform_working')")
        about = "A synthetic long short portfolio."
        self.assertRejected(derived, (None, None, None, None, None, "synthetic-long-short", about))  # no definition
        self.assertRejected(derived, (connection_id, "kiwoom_rest", "50000011", "{}", "d" * 64,
                                      "synthetic-long-short", about))  # a broker account is not a portfolio
        self.assertRejected(derived, (None, None, None, "{}", "d" * 64, "has spaces", about))
        self.assertRejected(derived, (None, None, None, "{}", "d" * 64, "synthetic-long-short", None))
        self.assertRejected(derived, (None, None, None, "{}", "d" * 64, "synthetic-long-short", "Too short"))
        self.db.execute(derived, (None, None, None, "{}", "d" * 64, "synthetic-long-short", about))
        self._subject("other-portfolio")
        self.assertRejected("UPDATE broker_onboarding_cases SET subject_id = 'other-portfolio'")

        self._subject("synthetic-long-short")
        self.db.execute("UPDATE broker_onboarding_cases SET stage = 'first_month_end', status = 'live', "
                        "went_live_at = now(), subject_id = target_subject_id")
        with self.assertRaises(psycopg.errors.UniqueViolation), self.db.transaction():
            self.db.execute(derived, (None, None, None, "{}", "e" * 64, "synthetic-long-short", about))
        self._case(connection_id, "50000011")  # the one-at-a-time slot is free again

    def test_connections_hold_no_secret_and_ibkr_needs_expiry(self):
        self.assertRejected(
            "INSERT INTO broker_connections (connection_id, broker, environment, display_name, "
            "key_vault_secret_name, credential_fingerprint, status) VALUES "
            "(gen_random_uuid(), 'ibkr_flex', 'live', 'No expiry', 'ibkr-flex-source-' || md5('x'), %s, "
            "'pending_check')", ("0" * 16,))
        with self.assertRaises(Exception), self.db.transaction():
            self._connection(broker="kiwoom_rest", key_vault_secret_name="ibkr-flex-source-" + "0" * 32)
        with self.assertRaises(Exception), self.db.transaction():
            self._connection(broker="ibkr_flex", environment="mock")
        with self.assertRaises(Exception), self.db.transaction():
            self._connection(credential_fingerprint="not-a-fingerprint")
        self._connection(broker="ibkr_flex")

    def test_approvals_and_events_cannot_be_rewritten(self):
        connection_id = self._connection()
        self._account(connection_id, "50000020")
        case_id = self._case(connection_id, "50000020", stage="load_and_reconcile", status="waiting_on_owner")
        self.db.execute(
            "INSERT INTO broker_onboarding_approvals (case_id, approval_kind, terms_json, terms_sha256, "
            "approved_by_identity_sha256) VALUES (%s, 'account_terms', '{}', %s, %s)",
            (case_id, "b" * 64, IDENTITY))
        self.db.execute(
            "INSERT INTO broker_onboarding_events (event_id, case_id, event_type, from_stage, to_stage) "
            "VALUES (gen_random_uuid(), %s, 'approved', 'load_and_reconcile', 'load_and_reconcile')", (case_id,))
        self.assertRejected("UPDATE broker_onboarding_approvals SET terms_sha256 = %s", ("c" * 64,))
        self.assertRejected("DELETE FROM broker_onboarding_events")
        self.assertRejected("DELETE FROM broker_onboarding_cases WHERE case_id = %s", (case_id,))

    def test_rows_stay_inside_their_tenant(self):
        connection_id = self._connection()
        self.db.execute("SELECT set_config('app.tenant_id', %s, true)", (OTHER_TENANT,))
        visible = self.db.execute("SELECT count(*) FROM broker_connections WHERE connection_id = %s",
                                  (connection_id,)).fetchone()[0]
        self.assertEqual(visible, 0)

    def test_security_identifiers_are_well_formed(self):
        self.db.execute("INSERT INTO security_master (security_key, display_name, asset_class, currency, status) "
                        "VALUES ('KRX:005930', 'Synthetic Korean share', 'STK', 'KRW', 'active')")
        self.db.execute("INSERT INTO security_identifiers (scheme, identifier, security_key, source) "
                        "VALUES ('krx_code', '005930', 'KRX:005930', 'kiwoom_rest')")
        self.assertRejected("INSERT INTO security_identifiers (scheme, identifier, security_key, source) "
                            "VALUES ('krx_code', '005930', 'KRX:005930', 'owner')")
        self.assertRejected("INSERT INTO security_identifiers (scheme, identifier, security_key, source) "
                            "VALUES ('krx_code', 'SAMSUNG', 'KRX:005930', 'owner')")
        self.assertRejected("INSERT INTO security_master (security_key, display_name, asset_class, currency, "
                            "status) VALUES ('NASDAQ:AAPL', 'Bad key', 'STK', 'USD', 'active')")
        self.db.execute("INSERT INTO security_master (security_key, display_name, asset_class, currency, status) "
                        "VALUES ('US:BRK.B', 'Synthetic US share', 'STK', 'USD', 'active')")
        self.db.execute("INSERT INTO security_identifiers (scheme, identifier, security_key, source) "
                        "VALUES ('us_ticker', 'BRK.B', 'US:BRK.B', 'kiwoom_rest')")
        self.assertRejected("INSERT INTO security_identifiers (scheme, identifier, security_key, source) "
                            "VALUES ('us_ticker', 'BRK B', 'US:BRK.B', 'owner')")
        self.assertRejected("INSERT INTO security_master (security_key, display_name, asset_class, currency, "
                            "status) VALUES ('US:brk', 'Bad key', 'STK', 'USD', 'active')")


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class BrokerOnboardingFlowTests(_MigratedDatabase):
    """The website's database functions, end to end on synthetic accounts."""

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row

        self.db = psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE), row_factory=dict_row,
                                  options=f"-c app.tenant_id={TENANT}")
        self.addCleanup(self.db.close)
        self.addCleanup(self.db.rollback)

    def _kiwoom(self, account="5012345610"):
        from datetime import date

        from ibkr_etl.broker_connections import new_connection_record
        from ibkr_etl.onboarding_v2_web import register_connection

        record = new_connection_record(
            broker="kiwoom_rest", display_name=f"Kiwoom {uuid.uuid4().hex[:6]}", environment="mock",
            credentials={"app_key": "k", "app_secret": "s"}, expires_on=None, ip_locked_to=None,
            today=date(2026, 10, 13))
        register_connection(self.db, TENANT, record=record, stored_fingerprint="0" * 16, query_id=None,
                            account_id=account, actor=IDENTITY)
        return record.connection_id

    def _start(self, connection_id, account="5012345610"):
        from datetime import date

        from ibkr_etl.onboarding_v2_web import start_case

        return start_case(self.db, TENANT, connection_id=connection_id, broker_account_id=account,
                          display_name="Synthetic Korea", reporting_currency="KRW",
                          history_start_date=date(2025, 1, 2), actor=IDENTITY, today=date(2026, 10, 13))

    def test_kiwoom_case_runs_to_approval_one(self):
        from datetime import date

        from ibkr_etl.onboarding_cases import terms_digest
        from ibkr_etl.onboarding_v2_web import (
            OnboardingError, approve_case, case_detail, onboarding_overview, record_platform_check)

        started = self._start(self._kiwoom())
        case_id = started["case_id"]
        self.assertEqual((started["stage"], started["status"]), ("request", "platform_working"))
        with self.assertRaisesRegex(OnboardingError, "still being onboarded"):
            self._start(self._kiwoom("50999999"), "50999999")

        record_platform_check(self.db, TENANT, case_id, "connection_sees_account", "pass", "Answered.")
        for check_id in ("holdings_and_cash_loaded", "securities_mapped", "nav_reconciled"):
            record_platform_check(self.db, TENANT, case_id, check_id, "pass", "Fine.")
        result = record_platform_check(self.db, TENANT, case_id, "transactions_loaded", "warn",
                                       "History starts later than requested.")
        self.assertEqual((result["stage"], result["status"]), ("load_and_reconcile", "waiting_on_owner"))

        detail = case_detail(self.db, TENANT, case_id)
        self.assertEqual(detail["awaiting_approval"], "account_terms")
        self.assertEqual(detail["account"], "5012-3456-10")
        self.assertEqual(detail["go_live_phrase"], "GO LIVE 5012-3456-10")
        with self.assertRaisesRegex(OnboardingError, "changed after this page loaded"):
            approve_case(self.db, TENANT, case_id, approval="account_terms", terms_sha256="f" * 64,
                         acknowledge_warnings=[], confirmation=None, actor=IDENTITY)
        with self.assertRaisesRegex(OnboardingError, "Acknowledge"):
            approve_case(self.db, TENANT, case_id, approval="account_terms",
                         terms_sha256=detail["account_terms_sha256"], acknowledge_warnings=[],
                         confirmation=None, actor=IDENTITY)
        approved = approve_case(self.db, TENANT, case_id, approval="account_terms",
                                terms_sha256=detail["account_terms_sha256"],
                                acknowledge_warnings=["transactions_loaded"], confirmation=None, actor=IDENTITY)
        self.assertEqual((approved["stage"], approved["status"]), ("build_history", "platform_working"))
        subject = self.db.execute("SELECT lifecycle_status, reporting_currency FROM platform_subjects "
                                  "WHERE subject_id = 'KIWOOM-5012345610'").fetchone()
        self.assertEqual(dict(subject), {"lifecycle_status": "onboarding", "reporting_currency": "KRW"})
        preview = self.db.execute("SELECT feature_state FROM subject_feature_states WHERE subject_id = "
                                  "'KIWOOM-5012345610' AND feature_key = 'data_ingestion'").fetchone()
        self.assertEqual(preview["feature_state"], "preview")
        nothing_built = terms_digest({"subject_id": "KIWOOM-5012345610", "display_name": "Synthetic Korea",
                                      "checks": {}})
        with self.assertRaisesRegex(OnboardingError, "must finish without failing"):
            approve_case(self.db, TENANT, case_id, approval="historical_reports", terms_sha256=nothing_built,
                         acknowledge_warnings=[], confirmation=None, actor=IDENTITY)
        events = [row["event_type"] for row in self.db.execute(
            "SELECT event_type FROM broker_onboarding_events WHERE case_id = %s ORDER BY created_at, event_type",
            (case_id,)).fetchall()]
        self.assertIn("approved", events)
        self.assertIn("warning_acknowledged", events)
        overview = onboarding_overview(self.db, TENANT, today=date(2026, 10, 13), platform_ip=None)
        self.assertEqual([c["case_id"] for c in overview["cases"]], [case_id])
        self.assertFalse(overview["can_start"])

    def test_ibkr_account_from_flex_discovery_goes_straight_to_loading(self):
        from datetime import date

        from ibkr_etl.broker_connections import new_connection_record
        from ibkr_etl.onboarding_v2_web import (
            connections_overview, onboarding_overview, register_connection, start_case)

        record = new_connection_record(
            broker="ibkr_flex", display_name="Second login", environment="live",
            credentials={"flex_token": "123"}, expires_on=date(2027, 9, 1), ip_locked_to="20.6.1.10",
            today=date(2026, 10, 13))
        register_connection(self.db, TENANT, record=record, stored_fingerprint="0" * 16, query_id="987",
                            account_id=None, actor=IDENTITY)
        source = self.db.execute("SELECT flex_source_id FROM ibkr_flex_queries WHERE credential_id = %s",
                                 (record.connection_id,)).fetchone()["flex_source_id"]
        self.db.execute("INSERT INTO ibkr_flex_source_accounts (flex_source_id, source_account_id, base_currency, "
                        "account_name, discovery_status) VALUES (%s, 'U0000077', 'USD', 'Synthetic', 'discovered')",
                        (source,))
        today = date(2026, 10, 13)
        listed = connections_overview(self.db, TENANT, today=today, platform_ip="20.6.1.10")
        mine = next(c for c in listed["connections"] if c["connection_id"] == record.connection_id)
        self.assertEqual((mine["health"], mine["ip_lock"], mine["flex_source_id"]),
                         ("not_checked", "locked", str(source)))
        available = onboarding_overview(self.db, TENANT, today=today, platform_ip=None)["available"]
        self.assertIn("U0000077", [a["broker_account_id"] for a in available])
        started = start_case(self.db, TENANT, connection_id=record.connection_id, broker_account_id="u0000077",
                             display_name="Synthetic USD", reporting_currency="usd",
                             history_start_date=date(2025, 1, 20), actor=IDENTITY, today=today)
        self.assertEqual(started["stage"], "load_and_reconcile")
        available = onboarding_overview(self.db, TENANT, today=today, platform_ip=None)["available"]
        self.assertNotIn("U0000077", [a["broker_account_id"] for a in available])

    def test_a_composite_portfolio_runs_to_approval_one_and_cancels_cleanly(self):
        from datetime import date

        from ibkr_etl.onboarding_v2_web import (
            OnboardingError, approve_case, cancel_case, case_detail, check_portfolio, record_platform_check,
            start_derived_case)
        from ibkr_etl.subject_onboarding import fixed_unit_composite_definition

        self.db.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, base_currency, "
                        "lifecycle_status) VALUES ('U0000501', 'physical_account', 'Synthetic tech', 'USD', "
                        "'onboarding')")
        definition = fixed_unit_composite_definition(
            subject_id="synthetic-long-short", display_name="Synthetic long short", base_currency="HKD",
            normalization_date="2025-01-20", constant_cash=58.0,
            components=[
                {"component_id": "U0000501", "component_name": "Synthetic tech", "component_type": "account",
                 "coefficient": 0.45, "source_currency": "USD"},
                {"component_id": "hang-seng-price-index", "component_name": "Hang Seng Index",
                 "component_type": "benchmark", "coefficient": -0.49, "source_currency": "HKD",
                 "price_convention": "price_index_close"},
            ])
        today = date(2026, 10, 13)

        about = "Long synthetic tech, short the Hang Seng Index."

        def start(**changes):
            return start_derived_case(self.db, TENANT, **{"definition": definition, "description": about,
                                                          "actor": IDENTITY, "today": today, **changes})

        def check():
            return check_portfolio(self.db, TENANT, definition=definition, description=about, today=today)

        self.assertEqual(check()["problems"], ["Not live yet: U0000501."])
        with self.assertRaisesRegex(OnboardingError, "Every sleeve must be live.*U0000501"):
            start()
        self.db.execute("UPDATE platform_subjects SET lifecycle_status = 'active' WHERE subject_id = 'U0000501'")
        checked = check()
        self.assertTrue(checked["ok"], checked["problems"])
        self.assertIn("Sleeves in USD are converted to HKD every day.", checked["notes"])
        self.assertIn("Composition of Synthetic long short", checked["svg"])
        with self.assertRaisesRegex(OnboardingError, "10 to 600 characters"):
            start(description="Short")
        with self.assertRaisesRegex(OnboardingError, "letters, digits and hyphens"):
            start(definition={**definition, "subject_id": "synthetic_long_short"})
        with self.assertRaisesRegex(OnboardingError, "future"):
            start(today=date(2025, 1, 19))
        started = start()
        case_id = started["case_id"]
        self.assertEqual((started["stage"], started["status"]), ("load_and_reconcile", "platform_working"))
        with self.assertRaisesRegex(OnboardingError, "portfolio ID already has"):
            start()
        self.assertIn("This portfolio ID already has an onboarding case.", check()["problems"])
        with self.assertRaisesRegex(OnboardingError, "not a check"):
            record_platform_check(self.db, TENANT, case_id, "nav_reconciled", "pass", "Not for portfolios.")
        record_platform_check(self.db, TENANT, case_id, "sleeves_cover_history", "pass", "Covered.")
        record_platform_check(self.db, TENANT, case_id, "calculation_dry_run", "pass", "Ran.")

        detail = case_detail(self.db, TENANT, case_id)
        self.assertEqual((detail["broker_label"], detail["account"], detail["awaiting_approval"]),
                         ("Fixed-unit composite", "synthetic-long-short", "account_terms"))
        self.assertEqual(detail["go_live_phrase"], "GO LIVE SYNTHETIC-LONG-SHORT")
        self.assertIn({"label": "Short sleeve", "value": "0.49 × Hang Seng Index (HKD)"},
                      detail["definition_summary"])
        self.assertEqual(detail["account_terms"]["definition"], definition)
        self.assertEqual((detail["description"], detail["account_terms"]["description"]), (about, about))
        self.assertIn("Composition of Synthetic long short", detail["composition_svg"])
        approve_case(self.db, TENANT, case_id, approval="account_terms", terms_sha256=detail["account_terms_sha256"],
                     acknowledge_warnings=[], confirmation=None, actor=IDENTITY)
        subject = self.db.execute("SELECT subject_type, lifecycle_status, reporting_currency FROM platform_subjects "
                                  "WHERE subject_id = 'synthetic-long-short'").fetchone()
        self.assertEqual(dict(subject), {"subject_type": "composite_portfolio", "lifecycle_status": "onboarding",
                                         "reporting_currency": "HKD"})
        stored = self.db.execute("SELECT approval_status, configuration_sha256 FROM subject_calculation_definitions "
                                 "WHERE subject_id = 'synthetic-long-short'").fetchone()
        self.assertEqual(dict(stored), {"approval_status": "approved",
                                        "configuration_sha256": started["definition_sha256"]})
        sleeves = {(row["depends_on_subject_id"], row["dependency_type"]) for row in self.db.execute(
            "SELECT depends_on_subject_id, dependency_type FROM subject_dependencies "
            "WHERE subject_id = 'synthetic-long-short'").fetchall()}
        self.assertEqual(sleeves, {("U0000501", "portfolio_component"), ("hang-seng-price-index", "benchmark")})

        cancel_case(self.db, TENANT, case_id, reason="Testing the cancel path", actor=IDENTITY)
        states = self.db.execute(
            "SELECT s.lifecycle_status, d.approval_status FROM platform_subjects s "
            "JOIN subject_calculation_definitions d USING (tenant_id, subject_id) "
            "WHERE s.subject_id = 'synthetic-long-short'").fetchone()
        self.assertEqual(dict(states), {"lifecycle_status": "archived", "approval_status": "rejected"})
        with self.assertRaisesRegex(OnboardingError, "already used"):
            start()

    def test_cancel_frees_the_account_and_existing_logins_can_be_tracked(self):
        from datetime import date

        from ibkr_etl.onboarding_v2_web import OnboardingError, cancel_case, track_existing_login

        connection_id = self._kiwoom("50000123")
        case_id = self._start(connection_id, "50000123")["case_id"]
        cancel_case(self.db, TENANT, case_id, reason="Picked the wrong account", actor=IDENTITY)
        status = self.db.execute("SELECT discovery_status FROM broker_connection_accounts WHERE "
                                 "broker_account_id = '50000123'").fetchone()["discovery_status"]
        self.assertEqual(status, "discovered")
        self._start(connection_id, "50000123")

        credential = uuid.uuid4()
        self.db.execute("INSERT INTO ibkr_flex_credentials (credential_id, display_name, key_vault_secret_name, "
                        "token_fingerprint, status) VALUES (%s, 'Main login', %s, %s, 'active')",
                        (credential, "ibkr-flex-source-" + credential.hex, "a" * 16))
        with self.assertRaisesRegex(OnboardingError, "expiry"):
            track_existing_login(self.db, TENANT, credential_id=str(credential), expires_on=None,
                                 ip_locked_to=None, actor=IDENTITY, today=date(2026, 10, 13))
        track_existing_login(self.db, TENANT, credential_id=str(credential), expires_on=date(2027, 6, 30),
                             ip_locked_to=None, actor=IDENTITY, today=date(2026, 10, 13))
        tracked = self.db.execute("SELECT broker, credential_fingerprint FROM broker_connections "
                                  "WHERE connection_id = %s", (credential,)).fetchone()
        self.assertEqual(dict(tracked), {"broker": "ibkr_flex", "credential_fingerprint": "a" * 16})



@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class PortfolioDryRunTests(_MigratedDatabase):
    """The daily job's checks commit, so they get a database of their own."""

    def test_a_portfolio_runs_from_its_dry_run_to_go_live(self):
        from datetime import date

        import psycopg
        from psycopg.rows import dict_row

        from ibkr_etl.db import PostgresConnection, transaction
        from ibkr_etl.onboarding import FEATURE_KEYS
        from ibkr_etl.onboarding_dry_run import record_history_build, run_portfolio_dry_runs
        from ibkr_etl.onboarding_v2_web import (
            OnboardingError, approve_case, case_detail, record_platform_check, start_derived_case)
        from ibkr_etl.subject_onboarding import fixed_unit_composite_definition

        db = PostgresConnection(psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE), row_factory=dict_row,
                                                options=f"-c app.tenant_id={TENANT}"))
        self.addCleanup(db.close)
        db.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, base_currency, "
                   "lifecycle_status) VALUES ('U0000501', 'physical_account', 'Synthetic tech', 'USD', 'active')")
        definition = fixed_unit_composite_definition(
            subject_id="synthetic-long-short", display_name="Synthetic long short", base_currency="HKD",
            normalization_date="2025-01-20", constant_cash=58.0,
            components=[{"component_id": "U0000501", "component_name": "Synthetic tech",
                         "component_type": "account", "coefficient": 0.42, "source_currency": "USD"}])
        case_id = start_derived_case(db, TENANT, definition=definition, description="A synthetic long short case.",
                                     actor=IDENTITY, today=date(2025, 3, 3))["case_id"]
        db.commit()
        calls = []

        def write_history(connection, subject_id):
            with transaction(connection):  # the real refresh commits like this
                connection.execute("INSERT INTO accounts (account_id, first_seen_at, last_seen_at, source_json) "
                                   "VALUES ('composite-source', 'x', 'x', '{}')")
                connection.execute(
                    "INSERT INTO derived_portfolios (portfolio_id, name, source_account_id, base_currency, "
                    "exclusion_rule_json, calculation_version, status, created_at, updated_at) "
                    "VALUES (?, 'Synthetic', 'composite-source', 'HKD', '{}', 'v1', 'active', 'x', 'x')",
                    (subject_id,))
                for day, nav in (("2025-01-20", 100.0), ("2025-01-21", 99.1), ("2025-02-28", 103.4)):
                    connection.execute(
                        "INSERT INTO derived_portfolio_nav (portfolio_id, snapshot_date, currency, total_nav, "
                        "excluded_market_value_base, source_position_count, included_position_count, "
                        "excluded_position_count, generated_at) VALUES (?, ?, 'HKD', ?, 0, 2, 2, 0, 'x')",
                        (subject_id, day, nav))

        def calculate(dry, contract):
            calls.append(contract["subject_id"])
            if len(calls) == 1:
                raise RuntimeError("Composite account sleeve does not exist: U0000501")
            write_history(dry, contract["subject_id"])
            return {"nav_dates": 3}

        failed, = run_portfolio_dry_runs(db, TENANT, calculate=calculate, today=date(2025, 3, 3))
        self.assertEqual((failed["checks"], failed["status"]),
                         ({"sleeves_cover_history": "fail", "calculation_dry_run": "fail"}, "blocked"))
        checks = {k["check_id"]: k["message"] for k in case_detail(db, TENANT, case_id)["checks"]}
        self.assertIn("Composite account sleeve does not exist: U0000501", checks["calculation_dry_run"])

        passed, = run_portfolio_dry_runs(db, TENANT, calculate=calculate, today=date(2025, 3, 3))
        self.assertEqual((passed["checks"], passed["status"]),
                         ({"sleeves_cover_history": "pass", "calculation_dry_run": "pass"}, "waiting_on_owner"))
        detail = case_detail(db, TENANT, case_id)
        self.assertEqual(detail["awaiting_approval"], "account_terms")
        self.assertIn("3 daily values from 2025-01-20 to 2025-02-28", {k["check_id"]: k["message"] for k in
                                                                       detail["checks"]}["calculation_dry_run"])
        for table, column in (("derived_portfolio_nav", "portfolio_id"), ("derived_portfolios", "portfolio_id"),
                              ("accounts", "account_id")):
            kept = db.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} IN "
                              "('synthetic-long-short', 'composite-source')").fetchone()["n"]
            self.assertEqual(kept, 0, table)
        self.assertEqual(run_portfolio_dry_runs(db, TENANT, calculate=calculate, today=date(2025, 3, 3)), [])
        self.assertEqual(calls, ["synthetic-long-short", "synthetic-long-short"])

        # Approval 1 registers the portfolio with only the owner's private preview switched on.
        approve_case(db, TENANT, case_id, approval="account_terms", terms_sha256=detail["awaiting_terms_sha256"],
                     acknowledge_warnings=[], confirmation=None, actor=IDENTITY)
        db.commit()

        def features():
            return {row["feature_key"]: row["feature_state"] for row in db.execute(
                "SELECT feature_key, feature_state FROM subject_feature_states "
                "WHERE subject_id = 'synthetic-long-short'").fetchall()}

        self.assertEqual(features(), {key: "preview" if key == "data_ingestion" else "disabled"
                                      for key in FEATURE_KEYS})
        detail = case_detail(db, TENANT, case_id)
        self.assertEqual((detail["stage"], detail["status"], detail["preview_page"]),
                         ("build_history", "platform_working", "/accounts/synthetic-long-short/overview"))

        # Stage 3: the daily refresh now keeps the history and records it once.
        write_history(db, "synthetic-long-short")

        def history(error=None):
            return record_history_build(db, TENANT, "synthetic-long-short", error=error, today=date(2025, 3, 3))

        self.assertEqual(history(), {"case_id": case_id, "check": "pass", "recorded": True})
        self.assertEqual(history()["recorded"], False)
        self.assertEqual(history("Every account and portfolio sleeve must have NAV on 2025-01-20")["check"], "fail")
        self.assertEqual(case_detail(db, TENANT, case_id)["status"], "blocked")
        self.assertEqual(history()["check"], "pass")
        reports = [
            {"period_end": "2025-01-31", "report_type": "factsheet", "file_name": "jan.pdf", "sha256": "1" * 64,
             "download_path": "/api/onboarding-v2/reports/jan.pdf"},
            {"period_end": "2025-02-28", "report_type": "presentation", "file_name": "feb.pptx",
             "sha256": "2" * 64, "download_path": "https://example.com/feb.pptx"},
            {"period_end": "2025-02-28", "report_type": "factsheet", "file_name": "feb.pdf",
             "sha256": "3" * 64, "download_path": "/\\example.com/feb.pdf"},
        ]
        record_platform_check(db, TENANT, case_id, "reports_rendered", "pass", "3 reports rendered.",
                              {"reports": reports})
        record_platform_check(db, TENANT, case_id, "agent_review_complete", "warn", "One note on February.",
                              {"review_id": "first"})
        db.commit()
        detail = case_detail(db, TENANT, case_id)
        self.assertEqual(detail["awaiting_approval"], "historical_reports")
        self.assertEqual([(r["period_end"], r["report_type"], r["download_path"]) for r in detail["reports"]],
                         [("2025-02-28", "presentation", ""), ("2025-02-28", "factsheet", ""),
                          ("2025-01-31", "factsheet", "/api/onboarding-v2/reports/jan.pdf")])

        def approve(approval, terms, **extra):
            return approve_case(db, TENANT, case_id, approval=approval, terms_sha256=terms,
                                **{"acknowledge_warnings": [], "confirmation": None, "actor": IDENTITY, **extra})

        with self.assertRaisesRegex(OnboardingError, "Acknowledge each warning"):
            approve("historical_reports", detail["awaiting_terms_sha256"])
        db.rollback()
        record_platform_check(db, TENANT, case_id, "agent_review_complete", "pass", "No findings.",
                              {"review_id": "second"})
        with self.assertRaisesRegex(OnboardingError, "changed after this page loaded"):
            approve("historical_reports", detail["awaiting_terms_sha256"])
        db.rollback()
        record_platform_check(db, TENANT, case_id, "agent_review_complete", "pass", "No findings.",
                              {"review_id": "second"})
        approve("historical_reports", case_detail(db, TENANT, case_id)["awaiting_terms_sha256"])
        db.commit()

        # Approval 3 needs the typed phrase, then publishes the portfolio and starts its daily jobs.
        detail = case_detail(db, TENANT, case_id)
        self.assertEqual((detail["awaiting_approval"], detail["go_live_phrase"]),
                         ("go_live", "GO LIVE SYNTHETIC-LONG-SHORT"))
        with self.assertRaisesRegex(OnboardingError, "exact confirmation phrase"):
            approve("go_live", detail["awaiting_terms_sha256"], confirmation="GO LIVE")
        db.rollback()
        done = approve("go_live", detail["awaiting_terms_sha256"], confirmation="GO LIVE SYNTHETIC-LONG-SHORT")
        db.commit()
        self.assertEqual(done, {"case_id": case_id, "stage": "first_month_end", "status": "live",
                                "next_page": "/accounts/synthetic-long-short/overview"})
        lifecycle = db.execute("SELECT lifecycle_status FROM platform_subjects "
                               "WHERE subject_id = 'synthetic-long-short'").fetchone()["lifecycle_status"]
        self.assertEqual(lifecycle, "active")
        self.assertEqual(set(features().values()), {"active"})
        registered = {row["capability"]: (row["operating_state"], row["configuration_json"]) for row in db.execute(
            "SELECT capability, operating_state, configuration_json FROM subject_operating_registrations "
            "WHERE subject_id = 'synthetic-long-short'").fetchall()}
        self.assertEqual(set(registered), {"daily_ingestion", "benchmark_refresh", "sector_refresh",
                                           "month_end_reporting", "analyst_assurance", "viewer_directory"})
        self.assertEqual(registered["daily_ingestion"],
                         ("active", {"source": "derived_calculation", "start_date": "2025-01-20"}))
        self.assertIsNone(history())
        went_live = db.execute("SELECT went_live_at IS NOT NULL AS live FROM broker_onboarding_cases "
                               "WHERE case_id = %s", (case_id,)).fetchone()["live"]
        self.assertTrue(went_live)

if __name__ == "__main__":
    unittest.main()
