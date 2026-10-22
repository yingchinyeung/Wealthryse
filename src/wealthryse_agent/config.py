"""Settings read from the environment. Nothing here is a secret."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

APPROVED_REGION = "southeastasia"
# Global processing for the agent model was approved on 13 October 2026; see
# docs/AI_DATA_PROCESSING_APPROVAL_2026-09-22.md.
APPROVED_PROCESSING_SCOPES = frozenset({"apac-data-zone", "global"})
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
# Pro runs the same model for longer on each call, at the same token rates (docs/AGENTS.md).
REASONING_MODES = ("standard", "pro")
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _csv(name: str) -> frozenset[str]:
    return frozenset(v.strip().lower() for v in _env(name).split(",") if v.strip())


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = _env(name)
    value = int(raw) if raw else default
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    name: str
    user: str
    managed_identity_client_id: str
    tenant_id: str
    # Local development and tests only; Azure always uses Entra tokens.
    dsn_override: str = ""

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        return cls(
            host=_env("AGENT_DB_HOST"),
            name=_env("AGENT_DB_NAME", "ibkr"),
            user=_env("AGENT_DB_USER"),
            managed_identity_client_id=_env("AZURE_CLIENT_ID"),
            tenant_id=_env("AGENT_TENANT_ID", DEFAULT_TENANT_ID),
            dsn_override=_env("AGENT_DB_DSN"),
        )


@dataclass(frozen=True)
class ServiceAuthSettings:
    """Who may call a service: Entra app audience plus allowed caller identities."""

    entra_tenant_id: str
    audience: str
    allowed_callers: frozenset[str] = field(default_factory=frozenset)
    disabled_for_local_tests: bool = False

    @classmethod
    def from_env(cls, prefix: str) -> "ServiceAuthSettings":
        return cls(
            entra_tenant_id=_env("AZURE_TENANT_ID"),
            audience=_env(f"{prefix}_AUDIENCE"),
            allowed_callers=_csv(f"{prefix}_ALLOWED_CALLERS"),
            disabled_for_local_tests=_env("AGENT_LOCAL_INSECURE") == "1",
        )


@dataclass(frozen=True)
class ModelSettings:
    endpoint: str
    deployment: str
    router_deployment: str
    region: str
    processing_scope: str
    managed_identity_client_id: str
    max_tool_calls: int = 8
    max_output_tokens: int = 2500
    timeout_seconds: int = 60
    reasoning_effort: str = "low"
    # Website chat (/v1/chat) only; every other model call runs in standard mode.
    chat_reasoning_mode: str = "standard"

    @classmethod
    def from_env(cls) -> "ModelSettings":
        return cls(
            endpoint=_env("AGENT_MODEL_ENDPOINT"),
            deployment=_env("AGENT_MODEL_DEPLOYMENT"),
            router_deployment=_env("AGENT_ROUTER_DEPLOYMENT"),
            region=_env("AGENT_MODEL_REGION"),
            processing_scope=_env("AGENT_MODEL_PROCESSING_SCOPE"),
            managed_identity_client_id=_env("AZURE_CLIENT_ID"),
            max_tool_calls=_int("AGENT_MAX_TOOL_CALLS", 8, 1, 24),
            max_output_tokens=_int("AGENT_MAX_OUTPUT_TOKENS", 2500, 500, 64000),
            timeout_seconds=_int("AGENT_TIMEOUT_SECONDS", 60, 10, 600),
            reasoning_effort=(_env("AGENT_REASONING_EFFORT") or "low").lower(),
            chat_reasoning_mode=(_env("AGENT_CHAT_REASONING_MODE") or "standard").lower(),
        )

    def validate(self) -> None:
        if self.region.lower() != APPROVED_REGION:
            raise ValueError("The model region is outside the approved Southeast Asia boundary")
        if self.processing_scope.lower() not in APPROVED_PROCESSING_SCOPES:
            raise ValueError("The model processing scope is not an approved one")
        if not self.endpoint.startswith("https://") or not self.deployment:
            raise ValueError("The model endpoint or deployment is not configured")
        if self.reasoning_effort not in REASONING_EFFORTS:
            raise ValueError("The reasoning effort is not one the model accepts")
        if self.chat_reasoning_mode not in REASONING_MODES:
            raise ValueError("The chat reasoning mode is not one the model accepts")


@dataclass(frozen=True)
class RuntimeSettings:
    tool_server_url: str
    tool_server_audience: str
    managed_identity_client_id: str
    daily_token_budget: int
    retention_days: int

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        return cls(
            tool_server_url=_env("AGENT_TOOL_SERVER_URL").rstrip("/"),
            tool_server_audience=_env("AGENT_TOOL_SERVER_AUDIENCE"),
            managed_identity_client_id=_env("AZURE_CLIENT_ID"),
            daily_token_budget=_int("AGENT_DAILY_TOKEN_BUDGET", 2_000_000, 10_000, 100_000_000),
            retention_days=_int("AGENT_RETENTION_DAYS", 90, 1, 3650),
        )
