"""Every scheduled platform job has a failed-execution alert.

On 21 and 22 Oct 2026 the 09:20 UTC crisis-scenarios run failed, and on 23 Oct the 09:05 UTC
certified-figures run ran out of memory. Neither job had an alert, so nobody heard until someone
read the pages. This check makes a new scheduled job in infra/staging-*.bicep come with an alert
that emails the action group when an execution fails, either in its own file or in
infra/staging-additional-job-alerts.bicep. The agent jobs in infra/agent-apps.bicep are outside it.

Plain text checks: no Azure or bicep CLI.
"""
import re
import unittest
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"

# Scheduled jobs with no alert, and why. Each entry must still be scheduled and unalerted.
NOT_ALERTED = {
    "ibkrv2stg-report-design-worker": "Not deployed (no such job in the resource group on 23 Oct 2026); "
                                      "an alert scoped to a missing job fails to deploy.",
}


@lru_cache(maxsize=None)
def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def block(text: str, start: int) -> str:
    """The text from `start` to the brace that closes the first `{` after it."""
    depth = 0
    for end in range(text.index("{", start), len(text)):
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        if depth == 0:
            return text[start:end + 1]
    raise AssertionError("unclosed block")


def param_default(path: Path, name: str) -> str:
    found = re.search(rf"^param {name} string = '([^']+)'$", source(path), re.M)
    if not found:
        raise AssertionError(f"{path.name}: param {name} has no string default")
    return found[1]


def named(path: Path, body: str) -> str:
    """The resource's name: a literal, or a param's default."""
    found = re.search(r"^  name: (?:'([^'$]+)'|(\w+))$", body, re.M)
    if not found:
        raise AssertionError(f"{path.name}: resource has no plain name")
    return found[1] or param_default(path, found[2])


def scheduled_jobs() -> dict:
    """Job name -> Bicep file, for every Microsoft.App/jobs resource with a Schedule trigger."""
    jobs = {}
    for path in sorted(INFRA.glob("staging-*.bicep")):
        text = source(path)
        for header in re.finditer(r"^resource \w+ 'Microsoft\.App/jobs@[^']+' = \{$", text, re.M):
            body = block(text, header.start())
            if "triggerType: 'Schedule'" in body:
                jobs[named(path, body)] = path.name
    return jobs


def array_items(path: Path, array: str) -> list:
    found = re.search(rf"^(?:var {array}|param {array} array) = \[\n(.*?)\n\]$", source(path), re.M | re.S)
    if not found:
        raise AssertionError(f"{path.name}: no array {array}")
    items = []
    for line in found[1].splitlines():
        item = line.strip()
        if not item or item.startswith("//"):
            continue
        items.append(item.strip("'") if item.startswith("'") else param_default(path, item))
    return items


def job_symbols(path: Path) -> dict:
    """Symbolic name -> job name, for the file's Microsoft.App/jobs resources (new or existing)."""
    text = source(path)
    symbols = {}
    for header in re.finditer(r"^resource (\w+) 'Microsoft\.App/jobs@[^']+' (?:existing )?= \{$", text, re.M):
        symbols[header[1]] = named(path, block(text, header.start()))
    return symbols


def failure_alerts() -> dict:
    """Job name -> (Bicep file, alert body), for every `<job>-failed-execution` metric alert."""
    alerts = {}
    for path in sorted(INFRA.glob("*.bicep")):
        text = source(path)
        pattern = r"^resource \w+ 'Microsoft\.Insights/metricAlerts@[^']+' = (?:\[for (\w+) in (\w+): )?\{$"
        for header in re.finditer(pattern, text, re.M):
            body = block(text, header.start())
            name = re.search(r"^  name: '\$\{(\w+)\}-failed-execution'$", body, re.M)
            if not name:
                continue
            if header[1] and name[1] == header[1]:
                jobs = array_items(path, header[2])
            else:
                jobs = [param_default(path, name[1])]
            for job in jobs:
                alerts.setdefault(job, []).append((path, name[1], body))
    return alerts


class JobFailureAlertsTest(unittest.TestCase):
    def test_every_scheduled_job_has_a_failure_alert(self):
        alerts = failure_alerts()
        missing = sorted(job for job in scheduled_jobs() if job not in alerts and job not in NOT_ALERTED)
        self.assertEqual(missing, [], "add these to jobNames in infra/staging-additional-job-alerts.bicep")

    def test_the_certified_and_crisis_jobs_are_covered(self):
        alerts = failure_alerts()
        for job in ("ibkrv2stg-certified-figures", "ibkrv2stg-crisis-scenarios-daily"):
            with self.subTest(job=job):
                self.assertIn(job, scheduled_jobs())
                self.assertIn("staging-additional-job-alerts.bicep", [path.name for path, _, _ in alerts[job]])

    def test_each_alert_fires_on_a_failed_execution_of_its_own_job_and_notifies(self):
        for job, found in failure_alerts().items():
            for path, variable, body in found:
                with self.subTest(job=job, file=path.name):
                    compact = re.sub(r"\s+", "", body)  # one-line and multi-line layouts alike
                    for part in ("enabled:true", "metricName:'Executions'", "values:['Failed']",
                                 "actionGroupId:actionGroup.id"):
                        self.assertIn(part, compact)
                    scope = re.search(r"scopes:\[(.+?)\]", compact)[1]
                    by_id = re.fullmatch(r"(\w+)\.id", scope)
                    if by_id:
                        self.assertEqual(job_symbols(path).get(by_id[1]), job)
                    else:
                        self.assertEqual(scope, f"resourceId('Microsoft.App/jobs',{variable})")

    def test_each_exemption_is_still_scheduled_and_unalerted(self):
        alerts, jobs = failure_alerts(), scheduled_jobs()
        for job in NOT_ALERTED:
            with self.subTest(job=job):
                self.assertIn(job, jobs)
                self.assertNotIn(job, alerts)


if __name__ == "__main__":
    unittest.main()
