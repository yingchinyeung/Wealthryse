"""A Bicep redeploy keeps the Medallion tags that step S4a put on the live resources.

On 17 Oct 2026 step S4a tagged the live v2 resources with the Azure CLI and recorded what it added in
infra/medallion/applied-tags.json. A deployment replaces a resource's tags with the template's, so
every Bicep resource that deploys one of those resources must carry S4a's tags itself. Otherwise the
next redeploy drops them, and the audit policy that wants layer, component and owner flags it again.

Plain text checks: no Azure or bicep CLI.
"""
import json
import re
import unittest
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
APPLIED = json.loads((INFRA / "medallion" / "applied-tags.json").read_text(encoding="utf-8"))["tags"]
LAYERS = {"foundation", "data", "apps", "ai", "ops"}
REQUIRED = ("layer", "component", "owner")
MASK = "…"  # applied-tags.json shortens the account number in two job names to "u…"

# (Bicep file, resource symbolic name, live name as applied-tags.json lists it).
# A live name appears twice when two files deploy the same alert.
MAPPING = [
    ("agent-apps.bicep", "tools", "ibkrv2stg-agent-tools"),
    ("agent-apps.bicep", "agent", "ibkrv2stg-agent"),
    ("agent-apps.bicep", "agentJobs", "ibkrv2stg-agent-holdings"),
    ("agent-apps.bicep", "agentJobs", "ibkrv2stg-agent-month-end"),
    ("agent-apps.bicep", "agentJobs", "ibkrv2stg-agent-data-quality"),
    ("agent-apps.bicep", "agentJobs", "ibkrv2stg-agent-weekly-brief"),
    ("agent-apps.bicep", "agentJobs", "ibkrv2stg-agent-live-market"),
    ("agent-apps.bicep", "agentJobs", "ibkrv2stg-agent-risk-watch"),
    ("agent-apps.bicep", "agentJobs", "ibkrv2stg-agent-export-packs"),
    ("agent-apps.bicep", "agentJobs", "ibkrv2stg-agent-guide-check"),
    ("agent-apps.bicep", "agentJobs", "ibkrv2stg-agent-guide-keeper"),
    ("agent-apps.bicep", "monthTeamJob", "ibkrv2stg-agent-month-team"),
    ("agent-apps.bicep", "onboardingJob", "ibkrv2stg-agent-onboarding"),
    ("agent-apps.bicep", "operationsJob", "ibkrv2stg-agent-ops"),
    ("agent-foundation.bicep", "agentIdentity", "id-wr-agent"),
    ("agent-foundation.bicep", "toolsIdentity", "id-wr-agent-tools"),
    ("agent-foundation.bicep", "jobsIdentity", "id-wr-agent-jobs"),
    ("agent-foundation.bicep", "foundry", "wr-agents-sea"),
    ("agent-foundation.bicep", "project", "wr-agents-sea/wealthryse-agents"),
    ("staging.bicep", "workspace", "ibkrv2stg-logs"),
    ("staging.bicep", "registry", "ibkrv2stgauhecwc4itaoo"),
    ("staging.bicep", "storage", "ibkrv2stgauhecwc4itaoo"),
    ("staging.bicep", "vault", "ibkrv2stg-auhecwc4itaoo"),
    ("staging.bicep", "webIdentity", "ibkrv2stg-web-identity"),
    ("staging.bicep", "ingestionIdentity", "ibkrv2stg-ingestion-identity"),
    ("staging.bicep", "publisherIdentity", "ibkrv2stg-publisher-identity"),
    ("staging.bicep", "diagnosticsIdentity", "ibkrv2stg-diagnostics-identity"),
    ("staging.bicep", "approvedActionsIdentity", "ibkrv2stg-approved-actions-identity"),
    ("staging.bicep", "postgres", "ibkrv2stg-pg-auhecwc4itaoo"),
    ("staging-additional-job-alerts.bicep", "alerts", "ibkrv2stg-derived-daily-failed-execution"),
    ("staging-additional-job-alerts.bicep", "alerts", "ibkrv2stg-risk-snapshots-failed-execution"),
    ("staging-additional-job-alerts.bicep", "alerts", "ibkrv2stg-institutional-daily-failed-execution"),
    ("staging-additional-job-alerts.bicep", "alerts", "ibkrv2stg-flex-discovery-failed-execution"),
    ("staging-additional-job-alerts.bicep", "alerts", "ibkrv2stg-onboard-render-failed-execution"),
    ("staging-additional-job-alerts.bicep", "alerts", "ibkrv2stg-onboard-analytics-failed-execution"),
    ("staging-autonomous-admin.bicep", "supervisor", "ibkrv2stg-autonomous-admin"),
    ("staging-autonomous-admin.bicep", "monthlyAuthorityReview", "ibkrv2stg-autonomy-review"),
    ("staging-autonomous-admin.bicep", "supervisorFailureAlert", "ibkrv2stg-autonomous-admin-failed-execution"),
    ("staging-autonomous-admin.bicep", "monthlyReviewFailureAlert", "ibkrv2stg-autonomy-review-failed-execution"),
    ("staging-benchmark-job.bicep", "job", "ibkrv2stg-benchmark-daily"),
    ("staging-bootstrap-job.bicep", "job", "ibkrv2stg-bootstrap"),
    ("staging-burn-in-monitoring.bicep", "monitorJob", "ibkrv2stg-burn-in-monitor"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-benchmark-daily-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-risk-free-daily-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-sector-classification-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-month-end-factsheet-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-analyst-assurance-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-autonomous-admin-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-autonomy-review-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-derived-daily-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-risk-snapshots-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-institutional-daily-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-flex-discovery-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "failedJobAlerts", "ibkrv2stg-burn-in-monitor-failed-execution"),
    ("staging-burn-in-monitoring.bicep", "webServerErrorAlert", "ibkrv2stg-web-server-errors"),
    ("staging-credential-broker.bicep", "identity", "ibkrv2stg-credential-broker-identity"),
    ("staging-credential-broker.bicep", "broker", "ibkrv2stg-credential-broker"),
    ("staging-crisis-scenarios-job.bicep", "job", "ibkrv2stg-crisis-scenarios-daily"),
    ("staging-derived-refresh.bicep", "job", "ibkrv2stg-derived-daily"),
    ("staging-flex-discovery.bicep", "identity", "ibkrv2stg-flex-discovery-identity"),
    ("staging-flex-discovery.bicep", "job", "ibkrv2stg-flex-discovery"),
    ("staging-import-job.bicep", "job", "ibkrv2stg-import-u…"),
    ("staging-institutional-daily.bicep", "job", "ibkrv2stg-institutional-daily"),
    ("staging-internal-release-job.bicep", "job", "ibkrv2stg-internal-release"),
    ("staging-month-end-factsheet-job.bicep", "job", "ibkrv2stg-month-end-factsheet"),
    ("staging-onboarding-analytics-job.bicep", "job", "ibkrv2stg-onboard-analytics"),
    ("staging-onboarding-report-render-job.bicep", "job", "ibkrv2stg-onboard-render"),
    ("staging-portfolio-analyst-assurance.bicep", "job", "ibkrv2stg-analyst-assurance"),
    ("staging-portfolio-analyst-assurance.bicep", "failureAlert", "ibkrv2stg-analyst-assurance-failed-execution"),
    ("staging-report-snapshot-job.bicep", "job", "ibkrv2stg-report-snapshot"),
    ("staging-review-registration-job.bicep", "job", "ibkrv2stg-review-registration"),
    ("staging-risk-free-job.bicep", "job", "ibkrv2stg-risk-free-daily"),
    ("staging-risk-snapshot-job.bicep", "job", "ibkrv2stg-risk-snapshots"),
    ("staging-sector-classification-job.bicep", "job", "ibkrv2stg-sector-classification"),
    ("staging-sector-classification-job.bicep", "failureAlert", "ibkrv2stg-sector-classification-failed-execution"),
    ("staging-template-intake-job.bicep", "job", "ibkrv2stg-template-intake"),
    ("staging-verify-job.bicep", "job", "ibkrv2stg-verify-u…"),
    ("staging-web.bicep", "web", "ibkrv2stg-web"),
]

# Resources deployed after S4a, so not in applied-tags.json, with the Medallion tags they must carry.
# (Bicep file, resource symbolic name, live name, layer, component); hop comes from the catalog.
PLANNED = [
    ("staging-certified-figures-job.bicep", "job", "ibkrv2stg-certified-figures", "apps", "daily-pipeline"),
    ("agent-apps.bicep", "dispatcherJob", "ibkrv2stg-agent-dispatcher", "ai", "agents"),
]
CATALOG_JOBS = json.loads((INFRA / "medallion" / "catalog.json").read_text(encoding="utf-8"))["jobs"]

# Resources S4a left untagged that the tag line of 24 Oct 2026 tags, deployed by these files:
# (Bicep file, resource symbolic name, layer, component). The tag line writes the same values.
TAGGED_LATER = [
    ("platform-schedule-watchdogs.bicep", "alerts", "ops", "alerts"),
    # The network resources the tag rule excluded until they were tagged on 24 Oct 2026.
    ("staging.bicep", "vnet", "foundation", "network"),
    ("staging.bicep", "privateDnsZone", "foundation", "network"),
    ("staging.bicep", "privateDnsLink", "foundation", "network"),
    ("agent-foundation.bicep", "privateEndpoint", "foundation", "network"),
    ("agent-foundation.bicep", "zones", "foundation", "network"),
    ("agent-foundation.bicep", "zoneLinks", "foundation", "network"),
]

# In a tags expression: a `key: 'value'` pair, a lookup such as `medallionAlertTags[jobName]`,
# or the file's own `tags` object. Read left to right, a later key wins, as in union().
TOKEN = re.compile(
    r"(?P<key>\w+|'[^']+')\s*:\s*'(?P<value>[^']*)'"
    r"|(?P<lookup>\w+)\[(?P<loop>\w+)\]"
    r"|(?P<base>\btags\b)"
)


@lru_cache(maxsize=None)
def source(file: str) -> str:
    return (INFRA / file).read_text(encoding="utf-8")


def pairs(text: str) -> dict:
    """The `key: 'value'` pairs of an object literal, later keys winning."""
    return {m["key"].strip("'"): m["value"] for m in TOKEN.finditer(text) if m["key"]}


def resolve_params(file: str, text: str) -> str:
    """Replace `${param}` with the param's default value."""
    def default(match):
        found = re.search(rf"^param {match[1]} string = '([^']*)'$", source(file), re.M)
        if not found:
            raise AssertionError(f"{file}: param {match[1]} has no string default")
        return found[1]
    return re.sub(r"\$\{(\w+)\}", default, text)


def resource(file: str, symbol: str) -> tuple:
    """The resource's lower-cased type and the text of its `= ... { ... }` block."""
    text = source(file)
    header = re.search(rf"^resource {symbol} '([^'@]+)@[^']+' = [^\n]*\{{$", text, re.M)
    if not header:
        raise AssertionError(f"{file}: no top-level resource {symbol}")
    depth = 0
    for end in range(header.end() - 1, len(text)):
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        if depth == 0:
            return header[1].lower(), text[header.start():end + 1]
    raise AssertionError(f"{file}: resource {symbol} is not closed")


def tags_expression(file: str, symbol: str) -> str:
    """Everything after the resource's own `tags:`, up to the end of that expression."""
    block = resource(file, symbol)[1]
    start = re.search(r"^  tags: ", block, re.M)
    if not start:
        raise AssertionError(f"{file}: resource {symbol} sets no tags")
    depth = 0
    for end in range(start.end(), len(block)):
        char = block[end]
        depth += 1 if char in "({[" else -1 if char in ")}]" else 0
        if char == "\n" and depth == 0:
            break
    return block[start.end():end]


def base_tags(file: str) -> dict:
    """The file's `var tags` or `param tags object` default."""
    found = re.search(r"^(?:var tags|param tags object) = \{\n(.*?)\n\}", source(file), re.M | re.S)
    if not found:
        raise AssertionError(f"{file}: the tags expression uses `tags`, but the file defines none")
    return pairs(found[1])


def lookup_entry(file: str, symbol: str, table: str, loop: str, live: str) -> dict:
    """The entry of `var <table>` that the loop item deploying `live` reads."""
    found = re.search(rf"^var {table} = \{{\n(.*?)\n\}}", source(file), re.M | re.S)
    if not found:
        raise AssertionError(f"{file}: no var {table}")
    name = re.search(r"^  name: '([^']+)'$", resource(file, symbol)[1], re.M)[1]
    for key, body in re.findall(r"^\s*'([^']+)': (\{[^}\n]*\})$", found[1], re.M):
        item = resolve_params(file, key)
        if resolve_params(file, name.replace("${%s}" % loop, item)) == live:
            return pairs(body)
    raise AssertionError(f"{file}: {table} has no entry for {live}")


def evaluate(file: str, symbol: str, live: str, with_base: bool) -> dict:
    """The tags a deploy sets, read from the tags expression; `tags` counts only with with_base."""
    tags = {}
    for m in TOKEN.finditer(tags_expression(file, symbol)):
        if m["key"]:
            tags[m["key"].strip("'")] = m["value"]
        elif m["lookup"]:
            tags.update(lookup_entry(file, symbol, m["lookup"], m["loop"], live))
        elif with_base:
            tags.update(base_tags(file))
    return tags


def applied(resource_type: str, live: str) -> dict:
    return APPLIED.get(resource_type, {}).get(live)


class BicepMedallionTagsTest(unittest.TestCase):
    def test_every_mapped_resource_is_in_applied_tags_under_its_type(self):
        for file, symbol, live in MAPPING:
            with self.subTest(file=file, symbol=symbol, live=live):
                resource_type = resource(file, symbol)[0]
                self.assertIsNotNone(applied(resource_type, live), f"{resource_type} {live}")

    def test_each_masked_job_name_matches_exactly_one_bicep_file(self):
        for file, symbol, live in MAPPING:
            if not live.endswith(MASK):
                continue
            with self.subTest(live=live):
                prefix = "'" + live[:-len(MASK)]
                files = sorted(p.name for p in INFRA.glob("*.bicep") if prefix in p.read_text(encoding="utf-8"))
                self.assertEqual(files, [file])
                default = re.search(r"^param jobName string = '([^']+)'$", source(file), re.M)[1]
                self.assertTrue(default.startswith(prefix[1:]))
                self.assertIn("name: jobName", resource(file, symbol)[1])

    def test_the_tags_expression_carries_every_tag_s4a_added(self):
        for file, symbol, live in MAPPING:
            with self.subTest(file=file, symbol=symbol, live=live):
                s4a = applied(resource(file, symbol)[0], live)
                own = evaluate(file, symbol, live, with_base=False)
                self.assertEqual({key: own.get(key) for key in s4a}, s4a)
                # union() puts S4a last, so its values are what a deploy sets.
                deployed = evaluate(file, symbol, live, with_base=True)
                self.assertEqual({key: deployed.get(key) for key in s4a}, s4a)

    def test_every_mapped_resource_ends_up_with_layer_component_and_owner(self):
        for file, symbol, live in MAPPING:
            with self.subTest(file=file, symbol=symbol, live=live):
                deployed = evaluate(file, symbol, live, with_base=True)
                for key in REQUIRED:
                    self.assertTrue(deployed.get(key), f"no {key}")
                self.assertIn(deployed["layer"], LAYERS)

    def test_resources_added_after_s4a_carry_their_tags_and_the_catalog_hop(self):
        for file, symbol, live, layer, component in PLANNED:
            with self.subTest(file=file, live=live):
                self.assertIsNone(applied(resource(file, symbol)[0], live))
                deployed = evaluate(file, symbol, live, with_base=True)
                self.assertEqual((deployed.get("layer"), deployed.get("component")), (layer, component))
                self.assertTrue(deployed.get("owner"))
                self.assertIn(layer, LAYERS)
                self.assertEqual(deployed.get("hop"), "+".join(CATALOG_JOBS[live]["hops"]))

    def test_resources_tagged_later_carry_layer_component_and_owner(self):
        for file, symbol, layer, component in TAGGED_LATER:
            with self.subTest(file=file, symbol=symbol):
                deployed = evaluate(file, symbol, "", with_base=True)
                self.assertEqual((deployed.get("layer"), deployed.get("component")), (layer, component))
                self.assertTrue(deployed.get("owner"))

    def test_the_table_lists_each_resource_once(self):
        self.assertEqual(len(MAPPING), len(set(MAPPING)))


if __name__ == "__main__":
    unittest.main()
