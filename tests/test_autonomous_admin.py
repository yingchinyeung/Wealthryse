from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from ibkr_etl.ai_analyst import AIAnalystConfig, AIAnalystError
from ibkr_etl.autonomous_admin import (
    AutonomyControl,
    Finding,
    RecoveryContext,
    diagnose_findings,
    evaluate_supervisor_state,
    plan_safe_repairs,
)


class _Responses:
    def __init__(self, payload):
        self.payload = payload

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            status="completed",
            output_text=json.dumps(self.payload),
            usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 10}),
        )


class _Client:
    def __init__(self, payload):
        self.responses = _Responses(payload)


class AutonomousAdminTests(unittest.TestCase):
    def _state(self):
        return {
            "latest_nav_date": "2026-09-22",
            "latest_gspc_date": "2026-09-22",
            "latest_ixic_date": "2026-09-22",
            "latest_risk_free_date": "2026-09-22",
            "latest_import": {"status": "success"},
            "latest_snapshot": {"status": "frozen", "quality_status": "pass"},
            "ready_report_types": ["factsheet", "presentation"],
            "client_approval_count": 0,
        }

    def test_healthy_state_has_no_findings(self) -> None:
        self.assertEqual(
            evaluate_supervisor_state(self._state(), today_hkt=date(2026, 9, 24)), []
        )

    def test_only_market_data_findings_receive_autonomous_actions(self) -> None:
        state = self._state()
        state.update(
            latest_nav_date="2026-09-01",
            latest_gspc_date="2026-09-01",
            latest_risk_free_date="2026-09-01",
            latest_import={"status": "failed"},
        )
        findings = evaluate_supervisor_state(state, today_hkt=date(2026, 9, 24))
        actions = {finding.check_id: finding.autonomous_action for finding in findings}
        self.assertIsNone(actions["portfolio_nav_freshness"])
        self.assertIsNone(actions["account_import_status"])
        self.assertEqual(actions["sp500_benchmark_freshness"], "refresh_provisional_benchmarks")
        self.assertEqual(actions["risk_free_freshness"], "refresh_risk_free_rate")

    def test_flex_feed_accepts_yesterdays_pull_and_flags_a_missed_day(self) -> None:
        def flex(completed):
            run = {"status": "success", "completed_at": completed}
            state = self._state()
            state.update(
                daily_feed="ibkr_flex",
                latest_import=run,
                latest_nav_run={**run, "run_type": "institutional-flex-daily"},
            )
            return state

        # Runs every five minutes: on the 22nd it still accepts the 21st's pull.
        findings = evaluate_supervisor_state(flex("2026-10-21T08:31:00+00:00"), today_hkt=date(2026, 10, 22))
        self.assertNotIn("account_import_status", {finding.check_id for finding in findings})
        findings = evaluate_supervisor_state(flex("2026-10-20T08:31:00+00:00"), today_hkt=date(2026, 10, 22))
        imports = [finding for finding in findings if finding.check_id == "account_import_status"]
        self.assertEqual(len(imports), 1)
        self.assertEqual(imports[0].severity, "critical")
        self.assertIsNone(imports[0].autonomous_action)
        self.assertIn("Institutional Flex import has not completed", imports[0].description)
        # The v1.1 import status is no longer what decides it.
        state = flex("2026-10-21T08:31:00+00:00")
        state["latest_import"] = {"status": "failed", "completed_at": "2026-10-21T08:31:00+00:00"}
        findings = evaluate_supervisor_state(state, today_hkt=date(2026, 10, 21))
        self.assertIn("account_import_status", {finding.check_id for finding in findings})

    def test_policy_allows_at_most_one_action_and_honors_cooldown(self) -> None:
        now = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
        findings = [
            Finding("benchmark", "benchmark", "warning", "stale", {}, "refresh_provisional_benchmarks"),
            Finding("risk-free", "risk-free", "warning", "stale", {}, "refresh_risk_free_rate"),
        ]
        control = AutonomyControl(
            True,
            False,
            production_active=True,
            monthly_review_due_at=now + timedelta(days=10),
        )
        plans = plan_safe_repairs(findings, control, recent_action_at={}, now=now)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].target_job, "ibkrv2stg-benchmark-daily")
        cooled = plan_safe_repairs(
            findings,
            control,
            recent_action_at={"refresh_provisional_benchmarks": now - timedelta(minutes=10)},
            now=now,
        )
        self.assertEqual(cooled[0].action_type, "refresh_risk_free_rate")

    def test_disabled_killed_or_unknown_policy_cannot_act(self) -> None:
        finding = Finding("x", "x", "warning", "stale", {}, "refresh_risk_free_rate")
        for control in (
            AutonomyControl(False, False),
            AutonomyControl(True, True),
            AutonomyControl(True, False, policy_version="unapproved"),
        ):
            self.assertEqual(plan_safe_repairs([finding], control, recent_action_at={}), [])

    def test_exact_rollback_requires_three_failures_and_registered_different_revision(self) -> None:
        now = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
        control = AutonomyControl(
            True,
            False,
            production_active=True,
            automatic_rollback_enabled=True,
            monthly_review_due_at=now + timedelta(days=10),
        )
        finding = Finding(
            "portal_auth_boundary", "portal_auth_boundary", "critical", "portal failed", {}
        )
        recovery = RecoveryContext("ibkrv2stg-web", 3, "revision-42", "revision-41")
        plans = plan_safe_repairs(
            [finding], control, recent_action_at={}, now=now, recovery=recovery
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].action_type, "rollback_web_to_last_known_good")
        self.assertEqual(plans[0].target_revision, "revision-41")
        for blocked in (
            RecoveryContext("ibkrv2stg-web", 2, "revision-42", "revision-41"),
            RecoveryContext("ibkrv2stg-web", 3, "revision-41", "revision-41"),
            RecoveryContext("ibkrv2stg-web", 3, None, "revision-41"),
        ):
            self.assertEqual(
                plan_safe_repairs(
                    [finding], control, recent_action_at={}, now=now, recovery=blocked
                ),
                [],
            )

    def test_overdue_monthly_review_pauses_all_repairs(self) -> None:
        now = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
        control = AutonomyControl(
            True,
            False,
            production_active=True,
            automatic_rollback_enabled=True,
            monthly_review_due_at=now - timedelta(seconds=1),
        )
        finding = Finding("x", "x", "warning", "stale", {}, "refresh_risk_free_rate")
        self.assertEqual(
            plan_safe_repairs([finding], control, recent_action_at={}, now=now), []
        )

    def test_model_can_explain_but_cannot_invent_incidents(self) -> None:
        config = AIAnalystConfig(True, "https://example.test", "gpt", "southeastasia", "apac-data-zone", "identity")
        finding = Finding("risk_free_freshness", "risk_free_freshness", "warning", "stale", {})
        payload = {
            "summary": "One stale series.",
            "operator_message": "The policy broker will decide.",
            "priorities": [{"incident_key": "risk_free_freshness", "assessment": "Review freshness."}],
        }
        result, usage = diagnose_findings(config, [finding], client_factory=lambda _: _Client(payload))
        self.assertEqual(result["summary"], "One stale series.")
        self.assertEqual(usage["total_tokens"], 10)
        payload["priorities"][0]["incident_key"] = "invented"
        with self.assertRaises(AIAnalystError) as raised:
            diagnose_findings(config, [finding], client_factory=lambda _: _Client(payload))
        self.assertEqual(raised.exception.code, "invented_admin_incident")


if __name__ == "__main__":
    unittest.main()
