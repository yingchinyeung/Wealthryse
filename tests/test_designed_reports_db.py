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


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class DesignedReportRunTests(_MigratedDatabase):
    """A case with no built-in design waits for the owner's design, then renders every month from it."""

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
                patch.object(dr, "month_ready", lambda connection, kind, subject_id, period_end: period_end in ready), \
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


if __name__ == "__main__":
    unittest.main()
