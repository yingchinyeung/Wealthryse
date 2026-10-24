"""The code that checks a resource's whole tag map accepts the Medallion tags, and only those.

Each check passes with the resource's tags as before (not yet tagged) and with exactly its Medallion
tags added, and refuses a resource that carries only some of them or other values.
"""
from copy import deepcopy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ibkr_etl import medallion_tags as M  # noqa: E402
from ibkr_etl import monitor_release as release  # noqa: E402
from ibkr_etl import resource_cost_workbook as workbook  # noqa: E402
import deploy_operational_findings_alert as findings  # noqa: E402
import test_monitor_release as monitor_tests  # noqa: E402
import test_resource_cost_workbook as workbook_tests  # noqa: E402

# The other modules' fixtures, through an instance (importing their classes would run their tests twice).
MONITOR = monitor_tests.MonitorReleaseTests("test_wrapper_digest_and_compile_required")
COSTS = workbook_tests.CostWorkbookTests("test_currencies_are_separate_credits_retained")

BASE = {"application": "ibkr-portfolio-platform", "purpose": "p", "owner": "portfolio-platform"}


def partly(name):
    """The resource's Medallion tags with one left out."""
    tags = dict(M.EXACT[name])
    tags.pop(sorted(tags)[0])
    return tags


def other_values(name):
    return {key: value + "-other" for key, value in M.EXACT[name].items()}


class HelperTests(unittest.TestCase):
    def test_every_entry_names_a_layer_and_a_component_and_never_the_owner(self):
        for name, tags in M.EXACT.items():
            with self.subTest(name=name):
                self.assertIn(tags["layer"], {"foundation", "data", "apps", "ai", "ops"})
                self.assertTrue(tags["component"])
                self.assertNotIn("owner", tags)

    def test_same_tags_with_or_without_the_medallion_tags_only(self):
        for name in M.EXACT:
            with self.subTest(name=name):
                tagged = M.with_medallion(name, BASE)
                for expected in (BASE, tagged):
                    self.assertTrue(M.same_tags(name, BASE, expected))
                    self.assertTrue(M.same_tags(name, tagged, expected))
                    self.assertFalse(M.same_tags(name, {**BASE, **partly(name)}, expected))
                    self.assertFalse(M.same_tags(name, {**BASE, **other_values(name)}, expected))
                    self.assertFalse(M.same_tags(name, {**tagged, "extra": "x"}, expected))
                    self.assertFalse(M.same_tags(name, {**BASE, "owner": "someone"}, expected))
                    self.assertFalse(M.same_tags(name, None, expected))


class MetadataMonitorTests(unittest.TestCase):
    def baseline(self):
        return MONITOR.baseline()

    def test_the_monitor_passes_untagged_and_tagged(self):
        release.validate_baseline(self.baseline())
        job = self.baseline()
        job["tags"].update(M.EXACT[release.JOB])
        release.validate_baseline(job)

    def test_the_monitor_refuses_partial_or_other_medallion_tags(self):
        for extra in (partly(release.JOB), other_values(release.JOB), {**M.EXACT[release.JOB], "extra": "x"}):
            with self.subTest(extra=extra):
                job = self.baseline()
                job["tags"].update(extra)
                with self.assertRaises(ValueError):
                    release.validate_baseline(job)

    def test_a_source_release_keeps_the_medallion_tags(self):
        job = self.baseline()
        job["tags"].update(M.EXACT[release.JOB])
        payload = MONITOR.candidate()
        value = release.source_only_definition(job, payload)
        for key, tag in M.EXACT[release.JOB].items():
            self.assertEqual(value["tags"][key], tag)


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        self.expected = workbook.workbook(COSTS.qualify(), location="eastasia")
        self.rid = workbook.SCOPE + "/providers/Microsoft.Insights/workbooks/new"

    def actual(self, tags):
        value = deepcopy(self.expected)
        value["id"] = self.rid
        value["tags"] = tags
        return value

    def test_a_new_workbook_carries_the_medallion_tags(self):
        for key, tag in M.EXACT[workbook.TAGS_KEY].items():
            self.assertEqual(self.expected["tags"][key], tag)

    def test_the_live_workbook_passes_untagged_and_tagged(self):
        untagged = M.without_medallion(workbook.TAGS_KEY, self.expected["tags"])
        for tags in (untagged, self.expected["tags"]):
            with self.subTest(tags=tags):
                workbook.verify_workbook(self.actual(dict(tags)), self.expected, self.rid)
                # The plan archived before this change holds the untagged map.
                old = deepcopy(self.expected)
                old["tags"] = dict(untagged)
                workbook.verify_workbook(self.actual(dict(tags)), old, self.rid)

    def test_the_workbook_refuses_partial_or_other_medallion_tags(self):
        untagged = M.without_medallion(workbook.TAGS_KEY, self.expected["tags"])
        for extra in (partly(workbook.TAGS_KEY), other_values(workbook.TAGS_KEY)):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    workbook.verify_workbook(self.actual({**untagged, **extra}), self.expected, self.rid)


class OperationalFindingsTests(unittest.TestCase):
    def current(self, tags):
        value = findings.definition()
        value["id"] = findings.BASE + "Microsoft.Insights/scheduledQueryRules/" + findings.RULE
        value["tags"] = tags
        return value

    def test_a_deploy_writes_the_medallion_tags(self):
        for key, tag in M.EXACT[findings.RULE].items():
            self.assertEqual(findings.definition()["tags"][key], tag)

    def test_the_live_rule_passes_untagged_and_tagged(self):
        tagged = findings.definition()["tags"]
        for tags in (M.without_medallion(findings.RULE, tagged), tagged):
            with self.subTest(tags=tags):
                findings.verify(self.current(dict(tags)))

    def test_the_rule_refuses_partial_or_other_medallion_tags(self):
        untagged = M.without_medallion(findings.RULE, findings.definition()["tags"])
        for extra in (partly(findings.RULE), other_values(findings.RULE)):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    findings.verify(self.current({**untagged, **extra}))


if __name__ == "__main__":
    unittest.main()
