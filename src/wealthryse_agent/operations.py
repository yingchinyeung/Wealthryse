"""The operations agent: investigates the autonomous administrator's incidents.

The autonomous administrator (ibkr_etl.autonomous_admin) checks the platform
every five minutes, opens an incident for each failing check, and is the only
thing that acts: it may restart the risk-free and benchmark jobs and roll the
website back. This agent adds the investigation it lacks. For each open
incident it has not yet looked at, it reads:

  - the incident and the administrator's own evidence, its recent runs and
    actions, and the last 14 days of import runs (tool server, read-only);
  - Azure telemetry with read-only roles: error lines from the container
    apps' logs in Log Analytics, each job's recent executions, resource
    health, fired alerts, the build VM's metrics, and the resource group's
    daily cost.

The model writes a diagnosis from that evidence only: a summary, the likely
cause, a confidence, the evidence it rests on (each item naming the source it
came from), and the steps for the owner. Code keeps only evidence items whose
source was read and whose numbers appear in that source, and a diagnosis with
none left is marked inconclusive.

When the cause is in this repository's code and pull requests are switched on
(OPS_GITHUB_TOKEN), it writes the fix and a test as a DRAFT pull request on
this repository only. It cannot merge, push to main or deploy: you or a
reviewer merge, and the existing release process ships. It never adds a new
way for the platform to act.

The diagnosis goes to agent.ops_investigations. The administrator posts it in
the admin chat's system updates conversation, and the System health page shows
it beside the open incident (ibkr_etl.ops_agent_posts).

    python -m wealthryse_agent.jobs operations
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from .config import _env, _int
from .month_team.agents import TeamModel, TeamModelError

LOGGER = logging.getLogger("wealthryse_agent.operations")
# The one repository the agent may open pull requests on (Ying, 14 October 2026).
GITHUB_REPO = "yingchinyeung/Wealthryse"
BASE_BRANCH = "main"
BRANCH_PREFIX = "ops-agent/"
# Only application code and its tests; never workflows, infrastructure, migrations, deployment or images.
FIX_PATH = re.compile(r"^(?:src|scripts|tests)/[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|mjs)$")
TEST_PATH = re.compile(r"^tests/test_[A-Za-z0-9_]+\.py$")
MAX_FIX_FILES = 3
MAX_FILE_BYTES = 200_000
SEVERITIES = ("warning", "critical")
SUMMARY_LIMIT = 1000

# Redaction ----------------------------------------------------------------------------------
# Logs can carry credentials by accident. Anything that looks like one is replaced before the
# model or the agent's table sees it.
_SECRETS = (
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}"), r"\1 [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[redacted-jwt]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|accountkey|sharedaccesskey|sig|"
                r"client[_-]?secret)\s*[=:]\s*[^\s;&,'\"]+"), r"\1=[redacted]"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@"), r"\1[redacted]@"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"), "[redacted-github]"),
)


def redact(value: Any, limit: int = 600) -> Any:
    """Strip anything that looks like a credential, and cut long text."""
    if isinstance(value, str):
        for pattern, replacement in _SECRETS:
            value = pattern.sub(replacement, value)
        return value if len(value) <= limit else value[:limit - 1] + "…"
    if isinstance(value, Mapping):
        return {str(k): redact(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, limit) for v in value]
    return value


def looks_secret(text: str) -> bool:
    return redact(text, limit=len(text) + 1) != text


# Settings -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class OpsSettings:
    subscription_id: str
    resource_group: str
    log_workspace_id: str
    vm_name: str
    monthly_token_budget: int
    lookback_hours: int = 24
    github_token: str = ""
    max_pull_requests_per_day: int = 3

    @classmethod
    def from_env(cls) -> "OpsSettings":
        return cls(
            subscription_id=_env("AZURE_SUBSCRIPTION_ID"),
            resource_group=_env("OPS_RESOURCE_GROUP"),
            log_workspace_id=_env("OPS_LOG_WORKSPACE_ID", ""),
            vm_name=_env("OPS_VM_NAME", "ibkrv2stg-private-admin"),
            monthly_token_budget=_int("AGENT_OPS_TOKEN_BUDGET", 5_000_000, 100_000, 100_000_000),
            lookback_hours=_int("OPS_LOOKBACK_HOURS", 24, 1, 168),
            github_token=_env("OPS_GITHUB_TOKEN", ""),
            max_pull_requests_per_day=_int("OPS_MAX_PULL_REQUESTS_PER_DAY", 3, 0, 20),
        )

    def validate(self) -> None:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", self.subscription_id):
            raise ValueError("AZURE_SUBSCRIPTION_ID must be the subscription's id")
        if not re.fullmatch(r"[A-Za-z0-9._()-]{1,90}", self.resource_group):
            raise ValueError("OPS_RESOURCE_GROUP must be a resource group name")
        if self.log_workspace_id and not re.fullmatch(r"[0-9a-fA-F-]{36}", self.log_workspace_id):
            raise ValueError("OPS_LOG_WORKSPACE_ID must be the Log Analytics workspace (customer) id")


# Telemetry ----------------------------------------------------------------------------------


@dataclass
class Source:
    """One piece of telemetry the agent read, or why it could not be read."""
    source_id: str
    title: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def text(self) -> str:
        return json.dumps(self.rows, default=str, sort_keys=True)

    def summary(self) -> dict[str, Any]:
        item = {"source_id": self.source_id, "title": self.title, "rows": len(self.rows),
                "sha256": hashlib.sha256(self.text.encode()).hexdigest()}
        if self.error:
            item["error"] = self.error
        return item


# A request function: (method, url, scope, params, body) -> parsed JSON. The scope picks the token audience.
Request = Callable[[str, str, str, Mapping[str, Any] | None, Any], Any]
ARM = "https://management.azure.com"
LOGS = "https://api.loganalytics.io"

LOG_QUERY = """
union isfuzzy=true ContainerAppConsoleLogs_CL, ContainerAppSystemLogs_CL
| where TimeGenerated > ago({hours}h)
| extend app = tostring(column_ifexists("ContainerAppName_s", column_ifexists("ContainerJobName_s", ""))),
         reason = tostring(column_ifexists("Reason_s", "")), line = tostring(column_ifexists("Log_s", ""))
| where line has_any ("error", "Error", "ERROR", "Traceback", "Exception", "failed", "FAILED", "Killed", "OOM")
   or reason in~ ("Failed", "BackOff", "Error", "ProbeFailed", "ContainerTerminated", "OOMKilled")
| summarize count = count(), first = min(TimeGenerated), last = max(TimeGenerated), sample = take_any(line)
    by app, reason
| top 40 by count desc
"""


def default_request(client_id: str) -> Request:
    import httpx

    from .auth import TokenSource

    tokens = {ARM: TokenSource(client_id, ARM), LOGS: TokenSource(client_id, LOGS)}
    client = httpx.Client(timeout=30)

    def request(method: str, url: str, scope: str, params: Mapping[str, Any] | None, body: Any) -> Any:
        response = client.request(method, url, params=params, json=body, headers=tokens[scope].header())
        response.raise_for_status()
        return response.json()

    return request


class Telemetry:
    """Reads Azure telemetry with the jobs identity's read-only roles. One failing source never stops the others."""

    def __init__(self, settings: OpsSettings, request: Request, *, now: datetime | None = None) -> None:
        self.settings = settings
        self.request = request
        self.now = now or datetime.now(timezone.utc)

    @property
    def _group(self) -> str:
        return f"/subscriptions/{self.settings.subscription_id}/resourceGroups/{self.settings.resource_group}"

    def _arm(self, path: str, api_version: str, params: Mapping[str, Any] | None = None, body: Any = None,
             method: str = "GET") -> Any:
        return self.request(method, ARM + path, ARM, {"api-version": api_version, **(params or {})}, body)

    def collect(self) -> list[Source]:
        sources = []
        for source_id, title, read in (
                ("logs:errors", "Error lines in the container apps' logs", self.log_errors),
                ("jobs:executions", "Each job's recent executions", self.job_executions),
                ("health:resources", "Resources that are not available", self.resource_health),
                ("alerts:fired", "Alerts fired", self.alerts),
                ("vm:metrics", "The build VM's CPU, memory and availability", self.vm_metrics),
                ("cost:daily", "The resource group's daily cost by service", self.daily_cost)):
            source = Source(source_id, title)
            try:
                source.rows = redact(read())[:60]
            except Exception as error:  # recorded and shown to the model, never raised
                source.error = f"{type(error).__name__}: {redact(str(error), 200)}"
                LOGGER.warning("telemetry %s could not be read: %s", source_id, source.error)
            sources.append(source)
        return sources

    def log_errors(self) -> list[dict[str, Any]]:
        if not self.settings.log_workspace_id:
            raise RuntimeError("no Log Analytics workspace is configured")
        result = self.request("POST", f"{LOGS}/v1/workspaces/{self.settings.log_workspace_id}/query", LOGS, None,
                              {"query": LOG_QUERY.format(hours=self.settings.lookback_hours)})
        table = (result.get("tables") or [{}])[0]
        names = [c["name"] for c in table.get("columns", [])]
        return [dict(zip(names, row)) for row in table.get("rows", [])]

    def job_executions(self) -> list[dict[str, Any]]:
        jobs = self._arm(f"{self._group}/providers/Microsoft.App/jobs", "2024-03-01").get("value", [])
        since = self.now - timedelta(hours=self.settings.lookback_hours)
        rows = []
        for job in jobs:
            name = job["name"]
            executions = self._arm(f"{self._group}/providers/Microsoft.App/jobs/{name}/executions",
                                   "2024-03-01").get("value", [])
            recent = []
            for item in executions:
                properties = item.get("properties", {})
                started = _parse_time(properties.get("startTime"))
                if started is None or started >= since:
                    recent.append({"status": properties.get("status"), "start": properties.get("startTime"),
                                   "end": properties.get("endTime")})
            failed = [r for r in recent if str(r["status"]) not in ("Succeeded", "Running", "Processing")]
            rows.append({"job": name, "executions": len(recent), "failed": len(failed),
                         "last": recent[0] if recent else None, "failures": failed[:5]})
        return sorted(rows, key=lambda r: (-r["failed"], r["job"]))

    def resource_health(self) -> list[dict[str, Any]]:
        statuses = self._arm(f"{self._group}/providers/Microsoft.ResourceHealth/availabilityStatuses",
                             "2022-10-01").get("value", [])
        rows = []
        for item in statuses:
            properties = item.get("properties", {})
            if properties.get("availabilityState") == "Available":
                continue
            rows.append({"resource": str(item.get("id", "")).split("/providers/Microsoft.ResourceHealth")[0]
                         .rsplit("/", 1)[-1], "state": properties.get("availabilityState"),
                         "summary": properties.get("summary"), "reason": properties.get("reasonType"),
                         "since": properties.get("occuredTime") or properties.get("occurredTime")})
        return rows

    def alerts(self) -> list[dict[str, Any]]:
        alerts = self.request("GET", f"{ARM}/subscriptions/{self.settings.subscription_id}/providers/"
                              "Microsoft.AlertsManagement/alerts", ARM,
                              {"api-version": "2019-05-05-preview", "targetResourceGroup": self.settings.resource_group,
                               "timeRange": "1d"}, None).get("value", [])
        rows = []
        for item in alerts:
            essentials = item.get("properties", {}).get("essentials", {})
            rows.append({"alert": item.get("name"), "severity": essentials.get("severity"),
                         "state": essentials.get("alertState"), "condition": essentials.get("monitorCondition"),
                         "resource": essentials.get("targetResourceName"), "fired": essentials.get("startDateTime")})
        return rows

    def vm_metrics(self) -> list[dict[str, Any]]:
        vm = f"{self._group}/providers/Microsoft.Compute/virtualMachines/{self.settings.vm_name}"
        result = self._arm(f"{vm}/providers/Microsoft.Insights/metrics", "2023-10-01", {
            "metricnames": "Percentage CPU,Available Memory Bytes,VmAvailabilityMetric",
            "timespan": f"PT{self.settings.lookback_hours}H", "interval": "PT1H", "aggregation": "Average,Maximum,Minimum"})
        rows = []
        for metric in result.get("value", []):
            points = [p for series in metric.get("timeseries", []) for p in series.get("data", [])]
            values = [p.get("maximum", p.get("average")) for p in points if p.get("maximum", p.get("average")) is not None]
            lows = [p.get("minimum", p.get("average")) for p in points if p.get("minimum", p.get("average")) is not None]
            rows.append({"metric": metric.get("name", {}).get("value"), "hours": len(points),
                         "max": round(max(values), 2) if values else None, "min": round(min(lows), 2) if lows else None,
                         "latest": round(values[-1], 2) if values else None})
        return rows

    def daily_cost(self) -> list[dict[str, Any]]:
        end = self.now.date()
        start = end - timedelta(days=7)
        result = self._arm(f"{self._group}/providers/Microsoft.CostManagement/query", "2023-03-01", body={
            "type": "ActualCost", "timeframe": "Custom",
            "timePeriod": {"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"},
            "dataset": {"granularity": "Daily", "aggregation": {"cost": {"name": "Cost", "function": "Sum"}},
                        "grouping": [{"type": "Dimension", "name": "ServiceName"}]}}, method="POST")
        names = [c["name"] for c in result.get("properties", {}).get("columns", [])]
        rows = [dict(zip(names, row)) for row in result.get("properties", {}).get("rows", [])]
        for row in rows:
            if isinstance(row.get("Cost"), float):
                row["Cost"] = round(row["Cost"], 2)
        return rows


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def platform_sources(call: Callable[[str, dict], dict], incident: Mapping[str, Any]) -> list[Source]:
    """The incident itself and the platform's recent runs, from the tool server."""
    history = call("get_operations_history", {})["data"]
    return [
        Source("platform:incident", "The incident and the administrator's evidence", [redact(dict(incident))]),
        Source("platform:imports", "Import runs over the last 14 days", redact(history["ingestion_runs"][:60])),
        Source("platform:admin_runs", "The administrator's runs over the last 7 days",
               redact(history["admin_runs"][:40])),
        Source("platform:admin_actions", "The administrator's actions over the last 7 days",
               redact(history["admin_actions"][:20])),
    ]


# Diagnosis ----------------------------------------------------------------------------------

_DIAGNOSIS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "likely_cause", "confidence", "evidence", "owner_steps", "code_fix"],
    "properties": {
        "summary": {"type": "string"},
        "likely_cause": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "evidence": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["source_id", "finding"],
            "properties": {"source_id": {"type": "string"}, "finding": {"type": "string"}}}},
        "owner_steps": {"type": "array", "items": {"type": "string"}},
        "code_fix": {"type": "object", "additionalProperties": False, "required": ["needed", "reason", "files"],
                     "properties": {"needed": {"type": "boolean"}, "reason": {"type": "string"},
                                    "files": {"type": "array", "items": {"type": "string"}}}},
    },
}

DIAGNOSIS_INSTRUCTIONS = """You are the operations agent of a portfolio reporting platform on Azure. The autonomous
administrator opened the incident in the input; the other sources are telemetry read just now. Find the most likely
cause, using only the sources given.

- summary: two or three plain sentences for the owner: what is wrong, why, and what happens next.
- likely_cause: one sentence. If the sources do not show a cause, say so plainly.
- confidence: high only when a source shows the cause directly; low when you are inferring.
- evidence: each item names the source_id it comes from and states what that source shows. Quote numbers, times
  and names exactly as they appear in that source. Never cite a source that has an error instead of rows.
- owner_steps: at most four short steps the owner can take, in order. The administrator's only automatic actions are
  restarting the risk-free and benchmark jobs and rolling the website back; never suggest that you or it act
  otherwise. Production changes go through the owner.
- code_fix.needed: true only when a source points at a defect in this repository's application code (a Python
  traceback naming a file under src/ or scripts/, for example). files: the repository paths to change, at most
  three, under src/, scripts/ or tests/. Never infrastructure, workflows, migrations or configuration.
Write in plain English, no jargon, no markdown."""

_NUMBER = re.compile(r"-?\d+(?:[.,:]\d+)*")


def grounded_evidence(items: Sequence[Mapping[str, Any]], sources: Mapping[str, Source]) -> list[dict[str, str]]:
    """Keep evidence whose source was read and whose every number appears in that source."""
    kept = []
    for item in items:
        source = sources.get(str(item.get("source_id")))
        finding = " ".join(str(item.get("finding") or "").split())
        if source is None or source.error or not source.rows or not finding:
            continue
        # Whole numbers only: "4" must appear as a number in the source, not inside "2026-10-14".
        numbers = {n.lstrip("-") for n in _NUMBER.findall(source.text)}
        if all(n.lstrip("-") in numbers for n in _NUMBER.findall(finding)):
            kept.append({"source_id": source.source_id, "finding": finding[:500]})
    return kept


def _fit(text: Any, limit: int = SUMMARY_LIMIT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


@dataclass
class Investigation:
    status: str
    diagnosis: dict[str, Any]
    sources: list[dict[str, Any]]
    pull_request: dict[str, Any] | None = None
    usage: dict[str, int] = field(default_factory=dict)


class Diagnoser:
    def __init__(self, model: Callable[[], TeamModel]) -> None:
        self._model = model

    def diagnose(self, incident: Mapping[str, Any], sources: Sequence[Source]) -> tuple[dict[str, Any], dict[str, int]]:
        by_id = {s.source_id: s for s in sources}
        payload = {"incident": redact(dict(incident)),
                   "sources": [{"source_id": s.source_id, "title": s.title, **({"error": s.error} if s.error
                                                                             else {"rows": s.rows})} for s in sources]}
        answer, used = self._model().ask(name="ops_diagnosis", instructions=DIAGNOSIS_INSTRUCTIONS, payload=payload,
                                         schema=_DIAGNOSIS_SCHEMA, max_output_tokens=6000)
        evidence = grounded_evidence(answer.get("evidence") or [], by_id)
        fix = answer.get("code_fix") or {}
        files = [p for p in (fix.get("files") or []) if isinstance(p, str) and FIX_PATH.fullmatch(p)
                 and ".." not in p][:MAX_FIX_FILES]
        diagnosis = {
            "summary": _fit(redact(answer.get("summary"), SUMMARY_LIMIT)),
            "likely_cause": _fit(redact(answer.get("likely_cause"), 500), 500),
            "confidence": answer.get("confidence") if evidence else "low",
            "evidence": evidence,
            "owner_steps": [_fit(redact(s, 300), 300) for s in (answer.get("owner_steps") or [])[:4]],
            "code_fix": {"needed": bool(fix.get("needed")) and bool(files) and bool(evidence),
                         "reason": _fit(redact(fix.get("reason"), 500), 500), "files": files},
            "unread_sources": [s.source_id for s in sources if s.error],
        }
        return diagnosis, used


# Draft pull requests --------------------------------------------------------------------------

_PATCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "explanation", "changes", "test"],
    "properties": {
        "title": {"type": "string"},
        "explanation": {"type": "string"},
        "changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["path", "content"],
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}},
        "test": {"type": "object", "additionalProperties": False, "required": ["path", "content"],
                 "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
    },
}

PATCH_INSTRUCTIONS = """You fix one defect in a Python repository, from an operations diagnosis and the current text of
the files named. Return the complete new text of each file you change (only files given in the input), and one test
file under tests/ (a new tests/test_<name>.py, or a file given in the input) whose test fails before your change and
passes after it. Keep the change as small as the defect needs, in the style of the surrounding code. Use synthetic
values only in tests: no account numbers, names, tokens or real figures. The title is a short sentence saying what
the change fixes. The explanation says, in two or three plain sentences, what was wrong and what changed."""


class PullRequestError(Exception):
    pass


GitHubRequest = Callable[[str, str, Any], Any]


def default_github(token: str) -> GitHubRequest:
    import httpx

    client = httpx.Client(base_url="https://api.github.com", timeout=30, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})

    def request(method: str, path: str, body: Any) -> Any:
        if not path.startswith(f"/repos/{GITHUB_REPO}/"):
            raise PullRequestError("The agent may only call this repository.")
        response = client.request(method, path, json=body)
        if response.status_code == 404 and method == "GET":
            return None
        response.raise_for_status()
        return response.json() if response.content else {}

    return request


def check_patch(patch: Mapping[str, Any], originals: Mapping[str, str]) -> list[tuple[str, str]]:
    """The files to write, after every check; raises PullRequestError with the reason otherwise."""
    files: list[tuple[str, str]] = []
    for change in patch.get("changes") or []:
        path, content = str(change.get("path")), str(change.get("content"))
        if path not in originals:
            raise PullRequestError(f"The fix changes {path}, which the diagnosis did not name.")
        if content == originals[path]:
            continue
        files.append((path, content))
    if not files:
        raise PullRequestError("The fix changes nothing.")
    test = patch.get("test") or {}
    test_path, test_content = str(test.get("path")), str(test.get("content"))
    if not TEST_PATH.fullmatch(test_path):
        raise PullRequestError("The fix has no test under tests/test_*.py.")
    if test_path in originals and test_content == originals[test_path]:
        raise PullRequestError("The fix's test is unchanged.")
    files.append((test_path, test_content))
    for path, content in files:
        if not FIX_PATH.fullmatch(path) or ".." in path:
            raise PullRequestError(f"{path} is outside the code the agent may change.")
        if len(content.encode()) > MAX_FILE_BYTES:
            raise PullRequestError(f"{path} is too large.")
        if looks_secret(content) and not (path in originals and looks_secret(originals[path])):
            raise PullRequestError(f"{path} contains something that looks like a credential.")
        if path.endswith(".py"):
            try:
                ast.parse(content, filename=path)
            except SyntaxError as error:
                raise PullRequestError(f"{path} is not valid Python: line {error.lineno}.") from None
    return files


class PullRequestDrafter:
    """Writes a fix and its test and opens it as a draft pull request on this repository only."""

    def __init__(self, model: Callable[[], TeamModel], github: GitHubRequest) -> None:
        self._model = model
        self.github = github

    def _file(self, path: str) -> tuple[str, str] | None:
        found = self.github("GET", f"/repos/{GITHUB_REPO}/contents/{path}?ref={BASE_BRANCH}", None)
        if not found or found.get("type") != "file":
            return None
        if int(found.get("size") or 0) > MAX_FILE_BYTES:
            raise PullRequestError(f"{path} is too large to change.")
        return base64.b64decode(found["content"]).decode("utf-8"), str(found["sha"])

    def draft(self, incident: Mapping[str, Any], diagnosis: Mapping[str, Any],
              investigation_key: str) -> tuple[dict[str, Any], dict[str, int]]:
        originals: dict[str, str] = {}
        blob_shas: dict[str, str] = {}
        for path in diagnosis["code_fix"]["files"]:
            found = self._file(path)
            if found is not None:
                originals[path], blob_shas[path] = found
        if not any(not TEST_PATH.fullmatch(p) for p in originals):
            raise PullRequestError("None of the files the diagnosis named exist on main.")
        payload = {"diagnosis": {k: diagnosis[k] for k in ("summary", "likely_cause", "evidence")},
                   "fix_reason": diagnosis["code_fix"]["reason"], "files": originals}
        patch, used = self._model().ask(name="ops_patch", instructions=PATCH_INSTRUCTIONS, payload=payload,
                                        schema=_PATCH_SCHEMA, max_output_tokens=16000)
        files = check_patch(patch, originals)
        for path, _ in files:
            if path not in blob_shas:
                found = self._file(path)  # a test the model chose that already exists
                if found is not None:
                    blob_shas[path] = found[1]
        base = self.github("GET", f"/repos/{GITHUB_REPO}/git/ref/heads/{BASE_BRANCH}", None)
        if not base:
            raise PullRequestError("The main branch could not be read.")
        branch = BRANCH_PREFIX + re.sub(r"[^a-z0-9-]+", "-", investigation_key.lower()).strip("-")[:60]
        self.github("POST", f"/repos/{GITHUB_REPO}/git/refs", {"ref": f"refs/heads/{branch}",
                                                              "sha": base["object"]["sha"]})
        title = _fit(redact(patch.get("title"), 120), 120) or f"Fix {incident['incident_key']}"
        for path, content in files:
            body = {"message": title, "branch": branch, "content": base64.b64encode(content.encode()).decode()}
            if path in blob_shas:
                body["sha"] = blob_shas[path]
            self.github("PUT", f"/repos/{GITHUB_REPO}/contents/{path}", body)
        description = "\n\n".join([
            f"Before: {diagnosis['summary']}",
            f"After: {_fit(redact(patch.get('explanation'), 1500), 1500)}",
            "How: the operations agent read the incident "
            f"`{incident['incident_key']}` and its telemetry, and changed "
            + ", ".join(f"`{p}`" for p, _ in files) + ".",
            "Evidence:\n" + "\n".join(f"- {e['source_id']}: {e['finding']}" for e in diagnosis["evidence"]),
            "Opened by the operations agent as a draft. It has not been run; CI runs on this pull request. "
            "A person reviews and merges it, and the usual release process ships it.",
        ])
        pull = self.github("POST", f"/repos/{GITHUB_REPO}/pulls", {
            "title": title, "head": branch, "base": BASE_BRANCH, "body": description, "draft": True})
        return {"number": pull["number"], "url": pull["html_url"], "branch": branch, "title": title,
                "files": [p for p, _ in files]}, used


# The investigation ----------------------------------------------------------------------------


class Investigator:
    def __init__(self, settings: OpsSettings, model: Callable[[], TeamModel], *, request: Request | None = None,
                 github: GitHubRequest | None = None, client_id: str = "") -> None:
        self.settings = settings
        self.diagnoser = Diagnoser(model)
        self._request = request
        self._client_id = client_id
        self.drafter = None
        if github is not None or settings.github_token:
            self.drafter = PullRequestDrafter(model, github or default_github(settings.github_token))
        self._telemetry: list[Source] | None = None

    def telemetry(self) -> list[Source]:
        # Read once per run: every incident in the run is diagnosed against the same picture.
        if self._telemetry is None:
            request = self._request or default_request(self._client_id)
            self._telemetry = Telemetry(self.settings, request).collect()
        return self._telemetry

    def investigate(self, call: Callable[[str, dict], dict], incident: Mapping[str, Any], *,
                    may_open_pull_request: bool) -> Investigation:
        sources = platform_sources(call, incident) + self.telemetry()
        usage: dict[str, int] = {}
        diagnosis, used = self.diagnoser.diagnose(incident, sources)
        _add(usage, used)
        pull_request = None
        fix = diagnosis["code_fix"]
        if fix["needed"]:
            if self.drafter is None:
                fix["pull_request_skipped"] = "pull requests are switched off"
            elif not may_open_pull_request:
                fix["pull_request_skipped"] = "today's limit of draft pull requests is reached"
            else:
                key = f"{incident['incident_key']}-{incident['incident_id']}-{_episode(incident)[:10]}"
                try:
                    pull_request, used = self.drafter.draft(incident, diagnosis, key)
                    _add(usage, used)
                except PullRequestError as error:
                    fix["pull_request_skipped"] = str(error)
                except TeamModelError as error:
                    fix["pull_request_skipped"] = error.message
                except Exception as error:  # a GitHub failure must not lose the diagnosis
                    fix["pull_request_skipped"] = f"GitHub refused the pull request ({type(error).__name__})"
                    LOGGER.warning("draft pull request for %s failed: %s", incident["incident_key"],
                                   redact(str(error), 300))
        status = "diagnosed" if diagnosis["evidence"] else "inconclusive"
        return Investigation(status, diagnosis, [s.summary() for s in sources], pull_request, usage)


def _episode(incident: Mapping[str, Any]) -> str:
    return str(incident.get("first_seen_at") or "")


def _add(total: dict[str, int], used: Mapping[str, int]) -> None:
    for key, value in used.items():
        total[key] = total.get(key, 0) + int(value)


class OpsStore:
    """agent.ops_investigations, written by the agent_writer role."""

    def __init__(self, connection: Any, tenant_id: str) -> None:
        self.db = connection
        self.tenant_id = tenant_id

    def done(self, incident: Mapping[str, Any]) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM agent.ops_investigations WHERE tenant_id = ?::uuid AND incident_id = ? "
            "AND episode_started_at = ?", (self.tenant_id, int(incident["incident_id"]), _episode(incident))).fetchone()
        self.db.rollback()
        return row is not None

    def tokens_used_this_month(self, today: date | None = None) -> int:
        first = (today or date.today()).replace(day=1)
        row = self.db.execute(
            "SELECT COALESCE(SUM((usage->>'input_tokens')::bigint + (usage->>'output_tokens')::bigint), 0) AS used "
            "FROM agent.ops_investigations WHERE tenant_id = ?::uuid AND created_at >= ?", (self.tenant_id, first)
        ).fetchone()
        self.db.rollback()
        return int(row["used"] or 0)

    def pull_requests_today(self) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM agent.ops_investigations WHERE tenant_id = ?::uuid "
            "AND pull_request IS NOT NULL AND created_at >= now() - interval '1 day'", (self.tenant_id,)).fetchone()
        self.db.rollback()
        return int(row["n"] or 0)

    def save(self, incident: Mapping[str, Any], result: Investigation, correlation_id: str | None) -> None:
        self.db.execute(
            "INSERT INTO agent.ops_investigations(tenant_id, incident_id, incident_key, episode_started_at, status, "
            "diagnosis, sources, pull_request, usage, correlation_id) VALUES (?::uuid, ?, ?, ?, ?, ?::jsonb, ?::jsonb, "
            "?::jsonb, ?::jsonb, ?) ON CONFLICT (tenant_id, incident_id, episode_started_at) DO NOTHING",
            (self.tenant_id, int(incident["incident_id"]), str(incident["incident_key"]), _episode(incident),
             result.status, json.dumps(result.diagnosis, default=str), json.dumps(result.sources, default=str),
             json.dumps(result.pull_request) if result.pull_request else None, json.dumps(result.usage), correlation_id),
        )
        self.db.commit()


def investigate_open_incidents(store: OpsStore, call: Callable[[str, dict], dict], investigator: Investigator,
                               usage: dict[str, int], correlation_id: str | None = None) -> int:
    """Investigate each warning or critical incident once per episode; returns how many were investigated."""
    done = 0
    for incident in call("list_open_incidents", {})["data"]["incidents"]:
        if incident.get("severity") not in SEVERITIES or store.done(incident):
            continue
        may_open = store.pull_requests_today() < investigator.settings.max_pull_requests_per_day
        try:
            result = investigator.investigate(call, incident, may_open_pull_request=may_open)
        except TeamModelError as error:
            LOGGER.warning("incident %s not diagnosed: %s", incident["incident_key"], error.code)
            continue  # nothing saved, so the next run tries again
        _add(usage, result.usage)
        store.save(incident, result, correlation_id)
        LOGGER.info("incident %s: %s%s", incident["incident_key"], result.status,
                    f", draft pull request {result.pull_request['number']}" if result.pull_request else "")
        done += 1
    return done
