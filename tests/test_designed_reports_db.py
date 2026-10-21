"""Stage 3 reports from the owner's factsheet design (designed_reports.py), against PostgreSQL.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN (CI starts one). Skipped otherwise. Rendering, freezing and
storage are stubbed; synthetic data only.
"""
from __future__ import annotations

import hashlib
import json
import types
import unittest
import uuid
from datetime import date
from unittest.mock import patch

from test_broker_onboarding_db import ADMIN_DSN, IDENTITY, OWNER_ROLE, TENANT, _dsn, _MigratedDatabase


class _DesignedCase(_MigratedDatabase):
    """A synthetic composite case with no built-in design, on its own migrated database."""

    def _connection(self):
        import psycopg
        from psycopg.rows import dict_row

        from ibkr_etl.db import PostgresConnection

        db = PostgresConnection(psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE), row_factory=dict_row,
                                                options=f"-c app.tenant_id={TENANT}"))
        self.addCleanup(db.close)
        return db

    def _case_with_history(self, db):
        from ibkr_etl.db import transaction
        from ibkr_etl.onboarding_dry_run import record_history_build, run_portfolio_dry_runs
        from ibkr_etl.onboarding_v2_web import approve_case, case_detail, start_derived_case
        from ibkr_etl.subject_onboarding import fixed_unit_composite_definition

        db.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, base_currency, "
                   "lifecycle_status) VALUES ('U0000701', 'physical_account', 'Synthetic source', 'USD', 'active')")
        definition = fixed_unit_composite_definition(
            subject_id="synthetic-own-design", display_name="Synthetic own design", base_currency="USD",
            normalization_date="2025-01-20", constant_cash=58.0,
            components=[{"component_id": "U0000701", "component_name": "Synthetic source",
                         "component_type": "account", "coefficient": 0.42, "source_currency": "USD"}])
        # No report_style: the case reports in the design its owner makes.
        case_id = start_derived_case(db, TENANT, definition=definition, description="A synthetic composite case.",
                                     actor=IDENTITY, today=date(2025, 3, 3))["case_id"]
        db.commit()

        def write_history(connection, subject_id):
            with transaction(connection):
                connection.execute("INSERT INTO accounts (account_id, first_seen_at, last_seen_at, source_json) "
                                   "VALUES ('own-design-source', 'x', 'x', '{}') ON CONFLICT DO NOTHING")
                connection.execute(
                    "INSERT INTO derived_portfolios (portfolio_id, name, source_account_id, base_currency, "
                    "exclusion_rule_json, calculation_version, status, created_at, updated_at) "
                    "VALUES (?, 'Synthetic', 'own-design-source', 'USD', '{}', 'v1', 'active', 'x', 'x')",
                    (subject_id,))
                for day, nav in (("2025-01-20", 100.0), ("2025-01-31", 101.2), ("2025-02-28", 103.4)):
                    connection.execute(
                        "INSERT INTO derived_portfolio_nav (portfolio_id, snapshot_date, currency, total_nav, "
                        "excluded_market_value_base, source_position_count, included_position_count, "
                        "excluded_position_count, generated_at) VALUES (?, ?, 'USD', ?, 0, 2, 2, 0, 'x')",
                        (subject_id, day, nav))

        run_portfolio_dry_runs(db, TENANT, calculate=lambda dry, contract: write_history(
            dry, contract["subject_id"]) or {}, today=date(2025, 3, 3))
        approve_case(db, TENANT, case_id, approval="account_terms",
                     terms_sha256=case_detail(db, TENANT, case_id)["awaiting_terms_sha256"],
                     acknowledge_warnings=[], confirmation=None, actor=IDENTITY)
        db.commit()
        write_history(db, "synthetic-own-design")
        record_history_build(db, TENANT, "synthetic-own-design", error=None, today=date(2025, 3, 3))
        return case_id

    def _submit_design(self, db, title, *, currency="USD", template=b"%PDF synthetic owner template"):
        from ibkr_etl import factsheet_design_store as store
        from ibkr_etl.factsheet_spec.spec import spec_sha256

        spec = {"design_id": "synthetic-" + uuid.uuid4().hex[:6], "title": title, "currencies": [currency]}
        digest = hashlib.sha256(template).hexdigest()
        store.put_file(db, TENANT, digest, template)
        draft_id = store.create_draft(db, TENANT, "synthetic-own-design", title=title, template_sha256=digest,
                                      example_sha256=None, more_examples=[], build={"checks": {}}, spec=spec,
                                      spec_sha256=spec_sha256(spec), actor=IDENTITY, status="review")
        store.set_status(db, TENANT, draft_id, "submitted")
        # Submitted one after another: the newest is the one that counts (timestamps are to the second).
        self.submitted = getattr(self, "submitted", 0) + 1
        db.execute("UPDATE factsheet_design_drafts SET updated_at = ? WHERE draft_id = ?",
                   (f"2030-01-01T00:00:{self.submitted:02d}+00:00", draft_id))
        db.commit()
        return spec, template



@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class DesignedReportRunTests(_DesignedCase):
    """A case with no built-in design waits for the owner's design, then renders every month from it."""

    def test_the_case_waits_for_a_design_then_renders_every_month_from_it(self):
        from ibkr_etl import designed_reports as dr
        from ibkr_etl import onboarding_v2_web
        from ibkr_etl.factsheet_spec.spec import spec_sha256
        from ibkr_etl.onboarding_v2_web import case_detail

        db = self._connection()
        case_id = self._case_with_history(db)
        self.assertEqual([c["case_id"] for c in dr.eligible_cases(db, TENANT)], [case_id])

        blobs: dict = {}
        rendered, ready = [], {"2025-01-31", "2025-02-28"}

        def fetch(container, blob):
            return blobs[(container, blob)]

        def store(container, blob, content, content_type):
            self.assertEqual(content_type, "application/pdf")
            blobs.setdefault((container, blob), content)

        def freeze(connection, *, tenant_id, subject_id, period_start, period_end, reporting_currency,
                   reject_quality_fail):
            self.assertEqual((period_start, reporting_currency, reject_quality_fail), ("2025-01-20", "USD", True))
            payload = json.dumps({"subject_id": subject_id, "period_end": period_end,
                                  "reporting_currency": "USD"}, sort_keys=True)
            snapshot_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO reporting_snapshots (snapshot_id, tenant_id, subject_id, period_start, period_end, "
                "as_of_at, reporting_currency, formula_version, source_manifest_json, snapshot_payload_json, "
                "data_sha256, quality_status, status, created_at) VALUES (?, ?, ?, ?, ?, 'x', 'USD', 'v1', '{}', ?, "
                "?, 'warn', 'frozen', 'x')",
                (snapshot_id, tenant_id, subject_id, period_start, period_end, payload,
                 hashlib.sha256(payload.encode()).hexdigest()))
            connection.commit()
            return types.SimpleNamespace(snapshot_id=snapshot_id)

        designs = []

        def render(spec, payload, *, template_path, example_path, output_path):
            self.assertEqual((spec, template_path.read_bytes(), example_path), (designs[-1][0], designs[-1][1], None))
            rendered.append((spec["title"], payload["period_end"]))
            output_path.write_bytes(f"%PDF {spec['title']} {payload['period_end']}".encode())
            return {}

        def run(today=date(2025, 3, 3)):
            return dr.run_designed_report_cases(db, TENANT, container="internal-reports", today=today,
                                                fetch=fetch, store=store, render=render)

        def check():
            return next(k for k in case_detail(db, TENANT, case_id)["checks"] if k["check_id"] == "reports_rendered")

        with patch.object(dr, "freeze_month_end_snapshot", freeze), \
                patch.object(dr, "month_ready", lambda connection, kind, subject_id, period_end, broker=None: period_end in ready), \
                patch.object(onboarding_v2_web, "factsheet_design_enabled", lambda: True):
            # Without a design, the check waits and the case page sends the owner to the designer.
            self.assertEqual(run()[0]["check"], "pending")
            self.assertEqual(check()["message"], dr.WAITING_FOR_DESIGN)
            detail = case_detail(db, TENANT, case_id)
            step = detail["factsheet_design"]
            self.assertEqual((step["open"], step["designs"], step["submitted"], step["designer_page"]),
                             (True, [], None, "/accounts/synthetic-own-design/factsheet-designer"))
            self.assertIn("factsheet designer", detail["next_owner_action"])
            self.assertEqual(run()[0]["recorded"], False)  # unchanged, so not recorded again

            designs.append(self._submit_design(db, "Own design"))
            self.assertEqual(run()[0], {"case_id": case_id, "check": "pass", "recorded": True, "reports": 2})
            self.assertEqual(check()["message"], "Factsheets for 2 months from 2025-01-31 to 2025-02-28 in your "
                                                 "Own design design, each read back and checked.")
            self.assertEqual(rendered, [("Own design", "2025-01-31"), ("Own design", "2025-02-28")])
            detail = case_detail(db, TENANT, case_id)
            self.assertEqual(detail["factsheet_design"]["submitted"]["title"], "Own design")
            self.assertIsNone(detail["factsheet_design"]["owner_action"])
            self.assertEqual([r["period_end"] for r in detail["reports"]], ["2025-02-28", "2025-01-31"])
            versions = {row["template_version"] for row in db.execute(
                "SELECT r.template_version FROM report_generation_runs r JOIN reporting_snapshots s "
                "USING (tenant_id, snapshot_id) WHERE s.subject_id = 'synthetic-own-design'").fetchall()}
            self.assertEqual(versions, {f"spec:{spec_sha256(designs[-1][0])}"})

            # Nothing new: nothing renders and approval 2's evidence is left alone.
            self.assertEqual(run()[0]["recorded"], False)
            self.assertEqual(len(rendered), 2)

            # A newer design renders every month again from the same frozen snapshots.
            designs.append(self._submit_design(db, "Second design", template=b"%PDF second template"))
            self.assertEqual(run()[0]["check"], "pass")
            self.assertEqual(rendered[2:], [("Second design", "2025-01-31"), ("Second design", "2025-02-28")])
            self.assertEqual(db.execute("SELECT count(*) AS n FROM reporting_snapshots WHERE subject_id = "
                                        "'synthetic-own-design'").fetchone()["n"], 2)

            # A design in another currency cannot render this account's reports.
            designs.append(self._submit_design(db, "Hong Kong dollar design", currency="HKD"))
            self.assertEqual(run()[0]["check"], "fail")
            self.assertIn("Submit a design in USD", check()["message"])




@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class LiveMonthEndTests(_DesignedCase):
    """After go-live, the month-end renders each new month in the design approved at approval 2."""

    def test_after_go_live_each_month_renders_in_the_approved_design(self):
        from ibkr_etl import designed_reports as dr
        from ibkr_etl import onboarding_month_end as live
        from ibkr_etl.onboarding_v2_web import approve_case, case_detail, record_platform_check

        db = self._connection()
        case_id = self._case_with_history(db)
        blobs: dict = {}
        rendered, ready = [], {"2025-01-31", "2025-02-28"}
        designs = []

        def fetch(container, blob):
            return blobs[(container, blob)]

        def store(container, blob, content, content_type):
            blobs.setdefault((container, blob), content)

        def freeze(connection, *, tenant_id, subject_id, period_start, period_end, reporting_currency,
                   reject_quality_fail):
            payload = json.dumps({"subject_id": subject_id, "period_end": period_end,
                                  "reporting_currency": "USD"}, sort_keys=True)
            snapshot_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO reporting_snapshots (snapshot_id, tenant_id, subject_id, period_start, period_end, "
                "as_of_at, reporting_currency, formula_version, source_manifest_json, snapshot_payload_json, "
                "data_sha256, quality_status, status, created_at) VALUES (?, ?, ?, ?, ?, 'x', 'USD', 'v1', '{}', ?, "
                "?, 'warn', 'frozen', 'x')",
                (snapshot_id, tenant_id, subject_id, period_start, period_end, payload,
                 hashlib.sha256(payload.encode()).hexdigest()))
            connection.commit()
            return types.SimpleNamespace(snapshot_id=snapshot_id)

        def render(spec, payload, *, template_path, example_path, output_path):
            rendered.append((spec["title"], payload["period_end"]))
            output_path.write_bytes(f"%PDF {spec['title']} {payload['period_end']}".encode())
            return {}

        def approve(approval, **extra):
            terms = case_detail(db, TENANT, case_id)["awaiting_terms_sha256"]
            result = approve_case(db, TENANT, case_id, approval=approval, terms_sha256=terms,
                                  **{"acknowledge_warnings": [], "confirmation": None, "actor": IDENTITY, **extra})
            db.commit()
            return result

        with patch.object(dr, "freeze_month_end_snapshot", freeze), \
                patch.object(dr, "month_ready", lambda connection, kind, subject_id, period_end, broker=None: period_end in ready):
            designs.append(self._submit_design(db, "Approved design"))
            dr.run_designed_report_cases(db, TENANT, container="internal-reports", today=date(2025, 3, 3),
                                         fetch=fetch, store=store, render=render)
            self.assertEqual(live.live_cases(db, TENANT), [])  # not live yet
            record_platform_check(db, TENANT, case_id, "agent_review_complete", "pass", "No findings.",
                                  {"review_id": "synthetic"})
            db.commit()
            approve("historical_reports")
            approve("go_live", confirmation=case_detail(db, TENANT, case_id)["go_live_phrase"])
            self.assertEqual([c["case_id"] for c in live.live_cases(db, TENANT)], [case_id])

            # A design submitted after go-live does not change the monthly factsheets.
            designs.append(self._submit_design(db, "Later design", template=b"%PDF later template"))
            ready.add("2025-03-31")
            results = live.run_live_month_end(db, TENANT, container="internal-reports", today=date(2025, 4, 2),
                                              fetch=fetch, store=store, render_design=render)
        self.assertEqual(results, [{"subject_id": "synthetic-own-design", "status": "processed",
                                    "latest_month": "2025-03-31", "waiting_for": None}])
        self.assertEqual(rendered, [("Approved design", "2025-01-31"), ("Approved design", "2025-02-28"),
                                    ("Approved design", "2025-03-31")])
        march = db.execute(
            "SELECT r.status, r.template_id FROM report_generation_runs r JOIN reporting_snapshots s "
            "USING (tenant_id, snapshot_id) WHERE s.subject_id = 'synthetic-own-design' "
            "AND s.period_end = '2025-03-31'").fetchall()
        self.assertEqual([(m["status"], m["template_id"]) for m in march],
                         [("awaiting_approval", "synthetic-own-design-monthly-factsheet")])

        # A stored design that no longer matches what was approved is refused, not rendered.
        db.execute("UPDATE factsheet_design_versions SET spec_sha256 = repeat('0', 64)")
        db.commit()
        with self.assertRaisesRegex(dr.DesignedReportError, "integrity"):
            dr.approved_design(db, TENANT, case_id, "synthetic-own-design")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class KiwoomMonthReadyTests(_DesignedCase):
    """A Kiwoom month waits until its last NAV row is the history pass's official close."""

    def test_the_month_unlocks_only_after_the_history_pass(self):
        from ibkr_etl import designed_reports as dr

        db = self._connection()
        db.execute("INSERT INTO accounts (account_id, first_seen_at, last_seen_at, source_json) "
                   "VALUES ('K1', '2025-01-01', '2025-02-03', '{}')")

        def run(run_type):
            return db.execute("INSERT INTO ingestion_runs (run_type, started_at, status) VALUES (?, '2025-02-03', 'success') "
                              "RETURNING run_id", (run_type,)).fetchone()["run_id"]

        def day(snapshot_date, run_id):
            for table, column, value in (("nav_snapshots", "currency", "KRW"), ("position_snapshots", "conid", "005930")):
                db.execute(f"INSERT INTO {table} (account_id, snapshot_date, {column}, row_sha256, last_seen_run_id) "
                           "VALUES ('K1', ?, ?, 'x', ?)", (snapshot_date, value, run_id))

        def ready(broker="kiwoom_rest"):
            return dr.month_ready(db, "broker_account", "K1", "2025-01-31", broker)

        live = run("kiwoom_rest")
        day("2025-01-31", live)
        self.assertFalse(ready())  # the after-market row only
        day("2025-02-03", live)
        self.assertFalse(ready())  # the next day is in, but the month end is not rewritten yet
        history = run("kiwoom_rest_history")
        db.execute("UPDATE nav_snapshots SET last_seen_run_id = ? WHERE account_id = 'K1' AND snapshot_date = '2025-01-31'",
                   (history,))
        self.assertTrue(ready())
        self.assertTrue(ready("ibkr_flex"))
        db.rollback()
