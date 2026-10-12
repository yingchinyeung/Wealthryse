import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from ibkr_etl.agent_governance import (REGISTRY, ObservationGrant, ObservationRequest,
                                     planning_inventory, preflight_observation, resource_id)
from ibkr_etl.agent_tools import TOOL_REGISTRY

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
RESOURCE = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/example/providers/Microsoft.App/jobs/job-one"


class AgentGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.grant = ObservationGrant("tenant", "agent", "data_analyst", frozenset({"account-a"}),
            frozenset(), frozenset({"get_portfolio_summary"}), NOW + timedelta(hours=1), enabled=True, background_allowed=True)
        self.request = ObservationRequest("tenant", "agent", "get_portfolio_summary", subject_id="account-a", mode="background")

    def decision(self, grant=None, request=None, **kwargs):
        return preflight_observation(grant or self.grant, request or self.request,
            now=kwargs.pop("now", NOW), kill_switch=kwargs.pop("kill_switch", False), **kwargs)

    def test_six_roles_and_only_existing_analytics_tools(self):
        self.assertEqual(len(REGISTRY), 6)
        for definition in REGISTRY.values():
            self.assertTrue(definition.subject_tools.issubset(TOOL_REGISTRY))

    def test_positive_preflight_never_authorizes_execution(self):
        result = self.decision()
        self.assertTrue(result["eligible_for_adapter_check"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["mutation_authorized"])

    def test_kill_switch_is_default_and_strict(self):
        self.assertFalse(preflight_observation(self.grant, self.request, now=NOW)["eligible_for_adapter_check"])
        for value in (True, None, 0):
            self.assertFalse(self.decision(kill_switch=value)["eligible_for_adapter_check"])

    def test_disabled_expired_changed_policy_and_unknown_role(self):
        for changes in ({"enabled": False}, {"expires_at": NOW}, {"policy_version": "other"}, {"role": "owner"}):
            self.assertFalse(self.decision(grant=replace(self.grant, **changes))["eligible_for_adapter_check"])

    def test_naive_clock_denied(self):
        self.assertFalse(self.decision(now=NOW.replace(tzinfo=None))["eligible_for_adapter_check"])

    def test_wrong_tenant_or_principal_denied(self):
        for changes in ({"tenant_id": "other"}, {"principal_id": "other"}):
            self.assertFalse(self.decision(request=replace(self.request, **changes))["eligible_for_adapter_check"])

    def test_unregistered_and_mutating_tools_denied_even_if_granted(self):
        for name in ("execute_sql", "deploy_image", "approve_report", "request_flex"):
            grant = replace(self.grant, tool_names=frozenset({name}))
            self.assertFalse(self.decision(grant, replace(self.request, tool_name=name))["eligible_for_adapter_check"])

    def test_wrong_account_and_empty_user_entitlements_denied(self):
        self.assertFalse(self.decision(request=replace(self.request, subject_id="account-b"))["eligible_for_adapter_check"])
        self.assertFalse(self.decision(request=replace(self.request, user_subject_ids=frozenset()))["eligible_for_adapter_check"])

    def test_user_and_agent_scopes_intersect(self):
        self.assertTrue(self.decision(request=replace(self.request, user_subject_ids=frozenset({"account-a"})))["eligible_for_adapter_check"])
        grant = replace(self.grant, subject_ids=frozenset({"*"}))
        self.assertFalse(self.decision(grant)["eligible_for_adapter_check"])

    def test_interactive_requires_user_and_background_requires_separate_authority(self):
        self.assertFalse(self.decision(request=replace(self.request, mode="interactive"))["eligible_for_adapter_check"])
        self.assertFalse(self.decision(grant=replace(self.grant, background_allowed=False))["eligible_for_adapter_check"])
        self.assertFalse(self.decision(request=replace(self.request, mode="invented"))["eligible_for_adapter_check"])
        self.assertTrue(self.decision(request=replace(self.request, mode="interactive", user_subject_ids=frozenset({"account-a"})))["eligible_for_adapter_check"])

    def test_ambiguous_and_missing_targets_denied(self):
        for changes in ({"resource_id": RESOURCE}, {"subject_id": None}):
            self.assertFalse(self.decision(request=replace(self.request, **changes))["eligible_for_adapter_check"])

    def test_platform_role_cannot_read_portfolio(self):
        self.assertFalse(self.decision(grant=replace(self.grant, role="platform_operations"))["eligible_for_adapter_check"])

    def test_resource_exact_match_and_user_scope(self):
        grant = replace(self.grant, role="platform_operations", resource_ids=frozenset({RESOURCE}),
                        tool_names=frozenset({"inspect_resource_metadata"}))
        request = replace(self.request, tool_name="inspect_resource_metadata", subject_id=None, resource_id=RESOURCE.upper())
        self.assertTrue(self.decision(grant, request)["eligible_for_adapter_check"])
        for changes in ({"resource_id": RESOURCE + "-other"}, {"user_resource_ids": frozenset()}):
            self.assertFalse(self.decision(grant, replace(request, **changes))["eligible_for_adapter_check"])

    def test_invalid_resource_ids_rejected(self):
        for value in (RESOURCE + "?api-version=x", RESOURCE + "/*", "https://management.azure.com" + RESOURCE,
                      RESOURCE.replace("job-one", "%2e%2e"), RESOURCE.replace("job-one", ".."), RESOURCE + "/"):
            with self.assertRaises(ValueError): resource_id(value)

    def capture(self):
        return {"captured_at": NOW.isoformat(), "read_only": True,
                "resources": {"readable": True, "data": [{"id": RESOURCE, "type": "Microsoft.App/jobs"}]}}

    def inventory(self, capture, now=NOW):
        return planning_inventory(capture, now=now, subscription_id="11111111-1111-1111-1111-111111111111", resource_group="example")

    def test_inventory_never_registers_or_authorizes_agents(self):
        result = self.inventory(self.capture())
        self.assertFalse(result["runtime_deployed"])
        self.assertFalse(result["execution_authorized"])
        self.assertTrue(all(a["state"] == "planned_disabled" for a in result["agents"]))
        self.assertEqual(result["resources"][0]["coverage"], "unreviewed")

    def test_stale_future_failed_duplicate_and_empty_inventory_rejected(self):
        for now in (NOW - timedelta(seconds=1), NOW + timedelta(hours=49)):
            with self.assertRaises(ValueError): self.inventory(self.capture(), now=now)
        for mode in ("failed", "duplicate", "empty"):
            capture = self.capture()
            if mode == "failed": capture["resources"]["readable"] = False
            if mode == "duplicate": capture["resources"]["data"] *= 2
            if mode == "empty": capture["resources"]["data"] = []
            with self.assertRaises(ValueError): self.inventory(capture)

    def test_inventory_rejects_neighbor_resource_group(self):
        capture = self.capture()
        capture["resources"]["data"][0]["id"] = RESOURCE.replace("/example/", "/example-other/")
        with self.assertRaises(ValueError): self.inventory(capture)
