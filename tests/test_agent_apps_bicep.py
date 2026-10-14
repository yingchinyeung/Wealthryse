import re
import unittest
from pathlib import Path

BICEP = Path(__file__).resolve().parents[1] / "infra" / "agent-apps.bicep"
FOUNDATION = BICEP.with_name("agent-foundation.bicep")


class AgentAppsBicepTest(unittest.TestCase):
    def test_job_names_fit_container_apps_limit(self):
        names = re.findall(r"\{\s*name:\s*'([^']+)',\s*job:", BICEP.read_text())
        self.assertEqual(len(names), 5)
        for name in names:
            self.assertTrue(2 <= len(name) <= 32, f"{name} is {len(name)} characters")

    def test_apps_stay_awake(self):
        replicas = re.findall(r"minReplicas:\s*(\d+)", BICEP.read_text())
        self.assertEqual(replicas, ["1", "1"])

    def test_agent_uses_gpt_6_astra_on_global_standard(self):
        foundation, apps = FOUNDATION.read_text(), BICEP.read_text()
        self.assertIn("param modelName string = 'gpt-6-astra'", foundation)
        self.assertIn("param modelVersion string = '2026-09-03'", foundation)
        self.assertIn("param modelSku string = 'GlobalStandard'", foundation)
        self.assertIn("param modelDeployment string = 'wr-analyst-astra'", apps)
        self.assertIn("value: modelProcessingScope", apps)

    def test_chat_reasons_harder_than_the_jobs(self):
        apps = BICEP.read_text()
        self.assertIn("param chatReasoningEffort string = 'xhigh'", apps)
        self.assertEqual(apps.count("AGENT_REASONING_EFFORT"), 1)  # set on the chat app only
        timeout = int(re.search(r"param chatTimeoutSeconds int = (\d+)", apps).group(1))
        self.assertLess(timeout, 240)

    def test_month_team_is_off_until_approved_and_stays_in_the_apac_data_zone(self):
        apps = BICEP.read_text()
        self.assertIn("param deployMonthTeam bool = false", apps)
        job = apps[apps.index("resource monthTeamJob"):]
        name = re.search(r"name: '([^']+)'", job).group(1)
        self.assertTrue(2 <= len(name) <= 32, f"{name} is {len(name)} characters")
        self.assertIn("= if (deployMonthTeam)", job.splitlines()[0])
        self.assertIn("{ name: 'AGENT_MODEL_PROCESSING_SCOPE', value: 'apac-data-zone' }", job)
        self.assertIn("param reportModelDeployment string = 'wr-reports-apac'", apps)
        self.assertIn("value: reportModelDeployment", job)
        reader = apps[apps.index("resource monthTeamReader"):apps.index("resource monthTeamJob")]
        self.assertIn("if (deployMonthTeam)", reader)
        self.assertIn("scope: reportContainer", reader)  # one container, read only

    def test_onboarding_agents_are_off_until_approved_and_stay_in_the_apac_data_zone(self):
        apps = BICEP.read_text()
        self.assertIn("param deployOnboarding bool = false", apps)
        job = apps[apps.index("resource onboardingJob"):apps.index("// The operations agent")]
        name = re.search(r"name: '([^']+)'", job).group(1)
        self.assertEqual(name, "ibkrv2stg-agent-onboarding")
        self.assertTrue(2 <= len(name) <= 32)
        # It reads review files through the month-end team's one-container reader, so it needs that too.
        self.assertIn("= if (deployOnboarding && deployMonthTeam)", job.splitlines()[0])
        self.assertIn("monthTeamReader", job)
        self.assertIn("{ name: 'AGENT_MODEL_PROCESSING_SCOPE', value: 'apac-data-zone' }", job)
        self.assertIn("value: reportModelDeployment", job)
        self.assertIn("args: ['onboarding']", job)
        # The intake agent reads the case documents: read-only, on the intake container only, and only with the job.
        reader = apps[apps.index("resource onboardingIntakeReader"):apps.index("resource monthTeamJob")]
        self.assertIn("= if (deployOnboarding && deployMonthTeam)", reader.splitlines()[0])
        self.assertIn("scope: intakeContainer", reader)
        self.assertIn("roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', "
                      "storageBlobDataReaderRole)", reader)
        self.assertIn("param intakeContainerName string = 'onboarding-intake'", apps)
        self.assertIn("onboardingIntakeReader", job)
        self.assertIn("{ name: 'ONBOARDING_INTAKE_CONTAINER', value: intakeContainerName }", job)
        self.assertEqual(apps.count("ONBOARDING_INTAKE_CONTAINER"), 1)

    def test_operations_agent_is_off_until_approved_reads_only_and_stays_in_the_apac_data_zone(self):
        apps = BICEP.read_text()
        self.assertIn("param deployOperations bool = false", apps)
        block = apps[apps.index("// The operations agent"):apps.index("output agentApiUrl")]
        job = block[block.index("resource operationsJob"):]
        name = re.search(r"name: '([^']+)'", job).group(1)
        self.assertEqual(name, "ibkrv2stg-agent-ops")
        self.assertTrue(2 <= len(name) <= 32)
        self.assertIn("= if (deployOperations)", job.splitlines()[0])
        self.assertIn("args: ['operations']", job)
        self.assertIn("{ name: 'AGENT_MODEL_PROCESSING_SCOPE', value: 'apac-data-zone' }", job)
        self.assertIn("value: reportModelDeployment", job)
        # Read-only roles only: Reader, Cost Management Reader, Log Analytics Reader.
        roles = set(re.findall(r"var \w+Role = '([0-9a-f-]{36})'", block))
        self.assertEqual(roles, {"acdd72a7-3385-48ef-bd42-f606fba81ae7", "72fafb9e-0641-4937-9268-a91bfd8191a3",
                                 "73c42c96-874c-492b-b04d-ab87d138a893"})
        self.assertEqual(block.count("if (deployOperations)"), 3)
        # The GitHub token is optional and only ever a Key Vault reference.
        self.assertIn("param opsGithubTokenSecretUri string = ''", apps)
        self.assertIn("secretRef: 'ops-github-token'", block)
        self.assertNotIn("value: opsGithubTokenSecretUri", block)


if __name__ == "__main__":
    unittest.main()
