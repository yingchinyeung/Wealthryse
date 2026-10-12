"""Owner Codex readiness UI and credential-free Azure provider configuration.

This is not the administrator narrative chat or an execution service. No task
can be dispatched by this module. A validated provider configuration proves
syntax and destination selection only, never authentication or compatibility.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AzureCodexProvider:
    endpoint: str
    deployment: str
    api_version: str
    allowed_hosts: tuple[str, ...]

    def __post_init__(self) -> None:
        # Exact destinations come from trusted server configuration, not a form,
        # prompt, uploaded document, or model-provided URL. No HTTP redirects.
        host_pattern = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.openai\.azure\.com"
        if (not self.allowed_hosts or any(
                not re.fullmatch(host_pattern, host) for host in self.allowed_hosts)):
            raise ValueError("Invalid Azure destination allowlist")
        parsed = urlsplit(self.endpoint)
        if (parsed.scheme != "https" or parsed.netloc not in self.allowed_hosts
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or self.endpoint != f"https://{parsed.netloc}{parsed.path}"):
            raise ValueError("Unapproved Azure endpoint")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.deployment):
            raise ValueError("Invalid Azure deployment")
        if self.api_version != "v1" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-preview)?", self.api_version):
            raise ValueError("Invalid Azure API version")
        # A syntactically plausible but nonexistent calendar date is invalid too.
        if self.api_version != "v1":
            datetime.strptime(self.api_version[:10], "%Y-%m-%d")

    @property
    def base_url(self) -> str:
        return self.endpoint.rstrip("/") + ("/openai/v1" if self.api_version == "v1" else "/openai")

    @property
    def responses_url(self) -> str:
        suffix = "" if self.api_version == "v1" else "?api-version=" + self.api_version
        return self.base_url + "/responses" + suffix

    @property
    def fingerprint(self) -> str:
        payload = {"endpoint": self.endpoint.rstrip("/"),
                   "deployment": self.deployment, "api_version": self.api_version,
                   "wire_api": "responses"}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def isolated_config(self) -> str:
        """Return TOML for a future isolated service home, without reading secrets.

        The broker environment reference is deliberately fixed, never supplied
        by a browser. Do not launch a credential-bearing coding worker from this
        configuration until credential isolation has passed acceptance.
        """
        quote = json.dumps
        return (
            f'model = {quote(self.deployment)}\nmodel_provider = "azure"\n'
            'sandbox_mode = "read-only"\napproval_policy = "never"\n'
            '[model_providers.azure]\nname = "Azure"\n'
            f'base_url = {quote(self.base_url)}\n'
            'env_key = "CODEX_AZURE_BROKER_TOKEN"\nwire_api = "responses"\n'
            + ("" if self.api_version == "v1" else
               f'query_params = {{ "api-version" = {quote(self.api_version)} }}\n')
        )


def provider_from_environment(environment: Mapping[str, str]) -> AzureCodexProvider | None:
    keys = ("CODEX_AZURE_ENDPOINT", "CODEX_AZURE_DEPLOYMENT",
            "CODEX_AZURE_API_VERSION", "CODEX_AZURE_ALLOWED_HOSTS")
    values = [environment.get(key, "") for key in keys]
    if not any(values):
        return None
    if not all(values):
        raise ValueError("Incomplete Azure provider configuration")
    return AzureCodexProvider(*values[:3], tuple(values[3].split(",")))


def workspace_status(environment: Mapping[str, str]) -> dict:
    """Report this web release's capabilities; no live probes or task creation.

    Do not accept environment booleans, browser claims or model output as proof
    of an installed, protected worker. Future activation requires a real adapter
    and independently verified readiness, not setting CODEX_ENABLED=true.
    """
    fingerprint = None
    try:
        provider = provider_from_environment(environment)
        configuration_state = "configured_unverified" if provider else "not_configured"
        if provider:
            fingerprint = provider.fingerprint
    except (ValueError, TypeError):
        configuration_state = "invalid"
    configuration_messages = {
        "configured_unverified": "Destination selected. Azure authentication, inference and Codex compatibility still need verification.",
        "not_configured": "A dedicated Codex provider has not been configured in this web release.",
        "invalid": "The server's Codex provider configuration needs correction. No connection was attempted.",
    }
    return {
        "schema_version": 1,
        "state": "setup_required",
        "execution_available": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "check_scope": "installed_web_capabilities_not_live_azure_probe",
        "configuration_fingerprint": fingerprint,
        "summary": "Your personal Codex workspace is being prepared. No Codex task has been started.",
        "checks": [
            {"id": "provider", "title": "Azure OpenAI connection",
             "state": configuration_state, "detail": configuration_messages[configuration_state]},
            {"id": "worker", "title": "Interactive Codex worker", "state": "not_integrated",
             "detail": "A protected worker, saved conversations, reconnect and cancellation are not yet connected to this website."},
            {"id": "protection", "title": "Source protection and audit", "state": "not_integrated",
             "detail": "Execution must verify IBKR source protection and independent action logging before accepting a task."},
            {"id": "delivery", "title": "Daily activity report", "state": "not_integrated",
             "detail": "Website and email delivery at 18:00 Hong Kong time are approved; end-to-end delivery is not yet verified."},
        ],
    }


WORKSPACE_CONTENT = """
<div class="page-view active" data-section="ai-operations">
  <div class="card wide">
    <div class="label">Direct owner access</div><h2>Your Codex workspace</h2>
    <p>Use Codex directly here once its Azure connection and protected worker are ready.
    This is separate from administrator chat and from background specialist activity.</p>
    <div class="warning" id="codex-summary" role="status" aria-live="polite">
      Checking workspace setup. Task submission is unavailable.</div>
    <p class="muted" id="codex-updated">No status has been retrieved yet.</p>
    <div class="actions"><button class="button" id="codex-refresh" type="button">Check status</button>
    <a class="button" href="/administration">Open existing administrator chat</a></div>
  </div>
  <div class="card wide"><h2>What remains before your first conversation</h2>
    <div id="codex-checks" class="onboarding-checks" aria-busy="true"></div>
    <noscript>JavaScript is required for automatic status updates. Codex execution is not enabled.</noscript>
  </div>
  <div class="card wide"><h2>Owner workspace requirements</h2>
    <p>The completed workspace will support direct conversations, scoped tasks, saved history,
    live results and cancellation. Those controls are not available in this release.</p>
    <p>Original IBKR Flex data must remain unchanged. Direct owner work and specialist work
    must both be logged and included in the daily activity report at 18:00 Hong Kong time.</p>
    <p class="muted">Status checks do not run Codex, contact IBKR, call a model or change Azure resources.</p>
  </div>
</div>
"""

WORKSPACE_SCRIPT = r"""
<script>
(() => {
  const summary = document.querySelector('#codex-summary');
  const updated = document.querySelector('#codex-updated');
  const checks = document.querySelector('#codex-checks');
  const refresh = document.querySelector('#codex-refresh');
  let timer = null, stopped = false, loading = false, failures = 0;
  async function loadStatus() {
    clearTimeout(timer);
    if (stopped || loading || document.hidden) return;
    loading = true; refresh.disabled = true; checks.setAttribute('aria-busy', 'true');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/api/codex/status', {
        cache: 'no-store', credentials: 'same-origin', signal: controller.signal
      });
      if (response.status === 401 || response.status === 403) {
        stopped = true;
        throw new Error('Your owner access is unavailable. Sign in again or ask an administrator to check your access.');
      }
      if (!response.ok || !(response.headers.get('content-type') || '').includes('application/json'))
        throw new Error('Status is temporarily unavailable. No task was started.');
      const data = await response.json();
      // A changed API contract must not accidentally expose unverified controls.
      if (data.schema_version !== 1 || data.execution_available !== false || !Array.isArray(data.checks))
        throw new Error('Workspace status changed. Reload this page before continuing.');
      const rows = data.checks.map(check => {
        const row = document.createElement('div'); row.className = 'onboarding-check';
        const title = document.createElement('strong'); title.textContent = check.title;
        const detail = document.createElement('span'); detail.textContent = check.detail;
        row.append(title, detail); return row;
      });
      checks.replaceChildren(...rows);
      summary.textContent = data.summary;
      updated.textContent = 'Setup status checked at ' + new Date(data.checked_at).toLocaleTimeString() +
        '. Updates automatically while this page is visible. This is not a live Azure connectivity test.';
      failures = 0;
    } catch (error) {
      failures += 1;
      checks.replaceChildren();
      summary.textContent = error.name === 'AbortError' ? 'Status check timed out. No task was started.' : error.message;
      updated.textContent = stopped ? 'Automatic checks stopped.' : 'Unable to confirm current status. Retrying automatically.';
    } finally {
      clearTimeout(timeout); loading = false; refresh.disabled = stopped;
      checks.setAttribute('aria-busy', 'false');
      if (!stopped && !document.hidden) timer = setTimeout(loadStatus, Math.min(120000, 30000 * 2 ** Math.min(failures, 2)));
    }
  }
  refresh.addEventListener('click', loadStatus);
  document.addEventListener('visibilitychange', () => {
    clearTimeout(timer); if (!document.hidden) loadStatus();
  });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); });
  loadStatus();
})();
</script>
"""
