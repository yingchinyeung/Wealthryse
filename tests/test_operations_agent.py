"""The operations agent: telemetry reading, grounded diagnosis, draft pull requests and the job loop.

Synthetic data only; no Azure, GitHub or model calls.
"""
from __future__ import annotations

import base64
import json
import unittest
from datetime import datetime, timezone

from wealthryse_agent.month_team.agents import TeamModelError
from wealthryse_agent.operations import (
    GITHUB_REPO, Investigator, OpsSettings, PullRequestError, Source, Telemetry, check_patch, grounded_evidence,
    investigate_open_incidents, looks_secret, redact,
)

SETTINGS = OpsSettings(subscription_id="11111111-2222-3333-4444-555555555555", resource_group="synthetic-rg",
                       log_workspace_id="66666666-7777-8888-9999-000000000000", vm_name="synthetic-vm",
                       monthly_token_budget=1_000_000, lookback_hours=24, github_token="",
                       max_pull_requests_per_day=3)
INCIDENT = {"incident_id": 7, "incident_key": "risk_free_stale", "check_id": "risk_free_freshness",
            "severity": "warning", "status": "open", "description": "The risk-free rate is 4 days old.",
            "first_seen_at": "2026-10-14T01:00:00Z", "last_seen_at": "2026-10-14T02:00:00Z", "occurrence_count": 12,
            "evidence": {"latest": "2026-10-10"}}
TRACEBACK = 'Traceback: File "src/ibkr_etl/risk_free.py", line 88, in parse: KeyError: \'yield\''


def fake_request(failing=()):
    """Azure answers for each telemetry source; any URL fragment in ``failing`` raises."""
    def request(method, url, scope, params, body):
        for fragment in failing:
            if fragment in url:
                raise RuntimeError(f"403 for {url} with token=abc123secret")
        if "/query" in url and "loganalytics" in url:
            return {"tables": [{"columns": [{"name": "app"}, {"name": "reason"}, {"name": "count"}, {"name": "sample"}],
                                "rows": [["ibkrv2stg-risk-free", "", 3, TRACEBACK + " password=hunter2"]]}]}
        if url.endswith("/executions"):
            return {"value": [
                {"properties": {"status": "Failed", "startTime": "2026-10-14T00:30:00Z", "endTime": "2026-10-14T00:31:00Z"}},
                {"properties": {"status": "Succeeded", "startTime": "2026-10-01T00:30:00Z"}},
            ]}
        if url.endswith("Microsoft.App/jobs"):
            return {"value": [{"name": "ibkrv2stg-risk-free"}]}
        if "availabilityStatuses" in url:
            return {"value": [{"id": "/x/providers/Microsoft.Web/sites/app/providers/Microsoft.ResourceHealth/a",
                               "properties": {"availabilityState": "Available"}}]}
        if "AlertsManagement" in url:
            return {"value": []}
        if "metrics" in url:
            return {"value": [{"name": {"value": "Percentage CPU"}, "timeseries": [{"data": [
                {"average": 10.0, "maximum": 20.0, "minimum": 5.0}, {"average": 30.0, "maximum": 91.5, "minimum": 9.0}]}]}]}
        if "CostManagement" in url:
            return {"properties": {"columns": [{"name": "Cost"}, {"name": "UsageDate"}, {"name": "ServiceName"}],
                                   "rows": [[1.23456, 20261013, "Container Apps"]]}}
        raise AssertionError(url)
    return request


class FakeModel:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def ask(self, *, name, instructions, payload, schema, max_output_tokens):
        self.calls.append((name, payload))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer, {"input_tokens": 100, "output_tokens": 10}


def diagnosis_answer(**overrides):
    answer = {"summary": "The risk-free job failed to parse the feed.", "likely_cause": "A missing yield field.",
              "confidence": "high",
              "evidence": [{"source_id": "jobs:executions", "finding": "ibkrv2stg-risk-free failed at 2026-10-14T00:30:00Z"},
                           {"source_id": "logs:errors", "finding": "KeyError at line 88, seen 3 times"}],
              "owner_steps": ["Review the draft fix."],
              "code_fix": {"needed": False, "reason": "", "files": []}}
    answer.update(overrides)
    return answer


def tools(incidents=(INCIDENT,)):
    def call(name, arguments):
        if name == "list_open_incidents":
            return {"data": {"incidents": list(incidents)}}
        if name == "get_operations_history":
            return {"data": {"ingestion_runs": [{"run_id": 1, "status": "failed", "error_message": "token=zzz9999"}],
                             "admin_runs": [], "admin_actions": []}}
        raise AssertionError(name)
    return call


class FakeGitHub:
    def __init__(self, files):
        self.files = files
        self.calls = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        assert path.startswith(f"/repos/{GITHUB_REPO}/")
        if method == "GET" and "/contents/" in path:
            name = path.split("/contents/", 1)[1].split("?", 1)[0]
            if name not in self.files:
                return None
            return {"type": "file", "size": len(self.files[name]), "sha": "blob-" + name,
                    "content": base64.b64encode(self.files[name].encode()).decode()}
        if method == "GET" and "/git/ref/heads/main" in path:
            return {"object": {"sha": "base-sha"}}
        if method == "POST" and path.endswith("/pulls"):
            return {"number": 501, "html_url": f"https://github.com/{GITHUB_REPO}/pull/501"}
        return {}


class FakeStore:
    def __init__(self, pull_requests_today=0):
        self.saved = {}
        self.today = pull_requests_today

    def done(self, incident):
        return (incident["incident_id"], incident["first_seen_at"]) in self.saved

    def pull_requests_today(self):
        return self.today

    def save(self, incident, result, correlation_id):
        self.saved[(incident["incident_id"], incident["first_seen_at"])] = result


class RedactionTests(unittest.TestCase):
    def test_credentials_are_removed_from_text_and_nested_values(self):
        value = redact({"a": ["Bearer abcdefghijklmnop", "postgresql://user:pw@host/db", "password=hunter2",
                              "ghp_" + "a" * 30]})
        text = json.dumps(value)
        for secret in ("abcdefghijklmnop", "pw@", "hunter2", "a" * 30):
            self.assertNotIn(secret, text)
        self.assertTrue(looks_secret("token=abc"))
        self.assertFalse(looks_secret("the job failed 3 times"))


class TelemetryTests(unittest.TestCase):
    def test_each_source_is_read_redacted_and_summarised(self):
        sources = {s.source_id: s for s in Telemetry(SETTINGS, fake_request(),
                                                     now=datetime(2026, 10, 14, 3, tzinfo=timezone.utc)).collect()}
        self.assertEqual(set(sources), {"logs:errors", "jobs:executions", "health:resources", "alerts:fired",
                                        "vm:metrics", "cost:daily"})
        self.assertNotIn("hunter2", sources["logs:errors"].text)
        jobs = sources["jobs:executions"].rows[0]
        self.assertEqual((jobs["job"], jobs["executions"], jobs["failed"]), ("ibkrv2stg-risk-free", 1, 1))
        self.assertEqual(sources["health:resources"].rows, [])
        self.assertEqual(sources["vm:metrics"].rows[0]["max"], 91.5)
        self.assertEqual(sources["cost:daily"].rows[0]["Cost"], 1.23)

    def test_a_source_that_cannot_be_read_is_recorded_without_stopping_the_others(self):
        sources = {s.source_id: s for s in Telemetry(SETTINGS, fake_request(failing=("CostManagement",))).collect()}
        self.assertIn("RuntimeError", sources["cost:daily"].error)
        self.assertNotIn("abc123secret", sources["cost:daily"].error)
        self.assertTrue(sources["logs:errors"].rows)
        self.assertIn("error", sources["cost:daily"].summary())


class GroundingTests(unittest.TestCase):
    def test_evidence_must_name_a_read_source_and_quote_its_numbers(self):
        read = Source("jobs:executions", "t", [{"failed": 3, "start": "2026-10-14T00:30:00Z"}])
        broken = Source("cost:daily", "t", error="403")
        kept = grounded_evidence([
            {"source_id": "jobs:executions", "finding": "failed 3 times since 2026-10-14T00:30:00Z"},
            {"source_id": "jobs:executions", "finding": "failed 4 times"},          # number not in the source
            {"source_id": "cost:daily", "finding": "cost rose"},                    # unread source
            {"source_id": "logs:missing", "finding": "anything"},                   # unknown source
        ], {"jobs:executions": read, "cost:daily": broken})
        self.assertEqual([k["finding"] for k in kept], ["failed 3 times since 2026-10-14T00:30:00Z"])


class InvestigationTests(unittest.TestCase):
    def investigator(self, model, github=None):
        return Investigator(SETTINGS, lambda: model, request=fake_request(), github=github)

    def test_a_grounded_diagnosis_is_saved_once_per_episode(self):
        model, store, usage = FakeModel(diagnosis_answer()), FakeStore(), {}
        investigator = self.investigator(model)
        self.assertEqual(investigate_open_incidents(store, tools(), investigator, usage, "c1"), 1)
        result = store.saved[(7, "2026-10-14T01:00:00Z")]
        self.assertEqual(result.status, "diagnosed")
        self.assertEqual(len(result.diagnosis["evidence"]), 2)
        self.assertEqual(usage, {"input_tokens": 100, "output_tokens": 10})
        self.assertTrue(all("sha256" in s for s in result.sources))
        # The payload the model saw carried no credentials.
        self.assertNotIn("zzz9999", json.dumps(model.calls[0][1]))
        self.assertNotIn("hunter2", json.dumps(model.calls[0][1]))
        # Seen already: not investigated again in this episode.
        self.assertEqual(investigate_open_incidents(store, tools(), investigator, usage, "c2"), 0)
        # Info incidents are left to the administrator.
        info = dict(INCIDENT, incident_id=8, severity="info")
        self.assertEqual(investigate_open_incidents(FakeStore(), tools([info]), investigator, {}, "c3"), 0)

    def test_ungrounded_evidence_makes_the_diagnosis_inconclusive_and_low_confidence(self):
        model = FakeModel(diagnosis_answer(evidence=[{"source_id": "jobs:executions", "finding": "failed 99 times"}]))
        store = FakeStore()
        investigate_open_incidents(store, tools(), self.investigator(model), {}, None)
        result = next(iter(store.saved.values()))
        self.assertEqual((result.status, result.diagnosis["confidence"]), ("inconclusive", "low"))

    def test_a_model_failure_saves_nothing_so_the_next_run_retries(self):
        store = FakeStore()
        model = FakeModel(TeamModelError("model_incomplete", "cut off"))
        self.assertEqual(investigate_open_incidents(store, tools(), self.investigator(model), {}, None), 0)
        self.assertEqual(store.saved, {})

    def test_a_code_fix_is_opened_as_a_draft_pull_request_on_this_repository(self):
        original = "def parse(row):\n    return row['yield']\n"
        fixed = "def parse(row):\n    return row.get('yield')\n"
        test = "def test_missing_yield():\n    assert True\n"
        github = FakeGitHub({"src/ibkr_etl/risk_free.py": original})
        model = FakeModel(
            diagnosis_answer(code_fix={"needed": True, "reason": "KeyError at line 88",
                                       "files": ["src/ibkr_etl/risk_free.py", "infra/main.bicep", "../etc/passwd"]}),
            {"title": "Handle a missing yield", "explanation": "The parser assumed the field.",
             "changes": [{"path": "src/ibkr_etl/risk_free.py", "content": fixed}],
             "test": {"path": "tests/test_risk_free_missing_yield.py", "content": test}})
        store = FakeStore()
        investigate_open_incidents(store, tools(), self.investigator(model, github), {}, None)
        result = next(iter(store.saved.values()))
        self.assertEqual(result.diagnosis["code_fix"]["files"], ["src/ibkr_etl/risk_free.py"])
        self.assertEqual(result.pull_request["number"], 501)
        pull = [c for c in github.calls if c[1].endswith("/pulls")][0][2]
        self.assertTrue(pull["draft"])
        self.assertEqual(pull["base"], "main")
        self.assertTrue(pull["head"].startswith("ops-agent/"))
        written = [c[1].split("/contents/")[1] for c in github.calls if c[0] == "PUT"]
        self.assertEqual(written, ["src/ibkr_etl/risk_free.py", "tests/test_risk_free_missing_yield.py"])
        self.assertEqual([c[2].get("sha") for c in github.calls if c[0] == "PUT"],
                         ["blob-src/ibkr_etl/risk_free.py", None])

    def test_pull_requests_respect_the_daily_limit_and_a_refused_patch_keeps_the_diagnosis(self):
        fix = diagnosis_answer(code_fix={"needed": True, "reason": "r", "files": ["src/ibkr_etl/risk_free.py"]})
        github = FakeGitHub({"src/ibkr_etl/risk_free.py": "x = 1\n"})
        store = FakeStore(pull_requests_today=3)
        investigate_open_incidents(store, tools(), self.investigator(FakeModel(fix), github), {}, None)
        result = next(iter(store.saved.values()))
        self.assertIsNone(result.pull_request)
        self.assertIn("limit", result.diagnosis["code_fix"]["pull_request_skipped"])
        bad_patch = {"title": "t", "explanation": "e", "changes": [{"path": "src/ibkr_etl/risk_free.py",
                                                                     "content": "x = (\n"}],
                     "test": {"path": "tests/test_x.py", "content": "def test_x():\n    pass\n"}}
        store = FakeStore()
        investigate_open_incidents(store, tools(), self.investigator(FakeModel(fix, bad_patch), github), {}, None)
        result = next(iter(store.saved.values()))
        self.assertEqual(result.status, "diagnosed")
        self.assertIn("not valid Python", result.diagnosis["code_fix"]["pull_request_skipped"])
        self.assertFalse([c for c in github.calls if c[0] in ("POST", "PUT")])

    def test_without_a_token_pull_requests_are_off(self):
        fix = diagnosis_answer(code_fix={"needed": True, "reason": "r", "files": ["src/ibkr_etl/risk_free.py"]})
        store = FakeStore()
        investigate_open_incidents(store, tools(), self.investigator(FakeModel(fix)), {}, None)
        self.assertIn("switched off", next(iter(store.saved.values())).diagnosis["code_fix"]["pull_request_skipped"])


class PatchCheckTests(unittest.TestCase):
    ORIGINALS = {"src/a.py": "x = 1\n"}
    TEST = {"path": "tests/test_a.py", "content": "def test_a():\n    pass\n"}

    def check(self, changes, test=None):
        return check_patch({"changes": changes, "test": test or self.TEST}, self.ORIGINALS)

    def test_only_named_application_files_with_a_test_and_no_credentials(self):
        self.assertEqual([p for p, _ in self.check([{"path": "src/a.py", "content": "x = 2\n"}])],
                         ["src/a.py", "tests/test_a.py"])
        for changes, test, reason in (
                ([{"path": "src/b.py", "content": "y = 1\n"}], None, "did not name"),
                ([{"path": "src/a.py", "content": "x = 1\n"}], None, "changes nothing"),
                ([{"path": "src/a.py", "content": "x = 2\n"}], {"path": "src/test_a.py", "content": ""}, "no test"),
                ([{"path": "src/a.py", "content": "x = 'password=hunter2'\n"}], None, "credential"),
                ([{"path": "src/a.py", "content": "x = (\n"}], None, "not valid Python")):
            with self.subTest(reason=reason), self.assertRaisesRegex(PullRequestError, reason):
                self.check(changes, test)

    def test_workflows_and_infrastructure_are_out_of_reach(self):
        with self.assertRaises(PullRequestError):
            check_patch({"changes": [{"path": ".github/workflows/ci.yml", "content": "x"}], "test": self.TEST},
                        {".github/workflows/ci.yml": ""})


class AgentPostTests(unittest.TestCase):
    def test_the_admin_chat_message_carries_the_diagnosis_steps_and_draft_link(self):
        from ibkr_etl.ops_agent_posts import investigation_message

        row = {"investigation_id": 3, "incident_id": 7, "incident_key": "risk_free_stale", "status": "diagnosed",
               "diagnosis": json.dumps({"summary": "S.", "likely_cause": "C.", "confidence": "high",
                                        "evidence": [{"source_id": "logs:errors", "finding": "F"}],
                                        "owner_steps": ["Review it.", "Merge it."], "unread_sources": ["cost:daily"]}),
               "pull_request": {"number": 501, "url": f"https://github.com/{GITHUB_REPO}/pull/501"}}
        content, response = investigation_message(row)
        self.assertIn("S.", content)
        self.assertIn("1. Review it. 2. Merge it.", content)
        self.assertIn("/pull/501", content)
        self.assertEqual(response["investigation_id"], 3)
        self.assertEqual(response["status_points"], [{"text": "F", "source_id": "logs:errors"}])
        self.assertFalse(response["requires_owner_approval"])
        self.assertTrue(any("cost:daily" in item for item in response["limitations"]))
        _, inconclusive = investigation_message(dict(row, status="inconclusive", pull_request=None))
        self.assertEqual(inconclusive["title"], "Incident diagnosis inconclusive")


if __name__ == "__main__":
    unittest.main()
