"""Disabled-by-default observation broker for the specialist-agent rollout.

Only trusted gateway code may construct Caller or provide AuthoritySource.
Neither is a request-body schema. Production registration/authentication and
deployment remain separate rollout gates; there is no environment-owner fallback.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

from .agent_governance import POLICY_VERSION, ObservationGrant, ObservationRequest, preflight_observation
from .agent_tools import AgentToolContext, AgentToolError, TOOL_REGISTRY, execute_tool
from .authorization import resolve_authorized_identity, subject_access_levels
from .db import PostgresConnection
from .util import canonical_json
from .agent_observation_audit import SCHEMA as AUDIT_SCHEMA,verify_receipt

LOGGER = logging.getLogger("uvicorn.error.ibkr_observation")


class ObservationDenied(RuntimeError):
    """Safe, fixed error code; never includes database errors or tool payloads."""


@dataclass(frozen=True)
class Caller:
    tenant_id: str
    agent_principal_id: str
    user_object_id: str
    user_email: str


@dataclass(frozen=True)
class AuthorityState:
    grant: ObservationGrant | None = None
    kill_switch: bool = True


class AuthoritySource(Protocol):
    def load_current(self, *, tenant_id: str, principal_id: str) -> AuthorityState:
        """Read committed server authority on EVERY call; never use model claims."""
        ...


class DisabledAuthority:
    def load_current(self, **kwargs) -> AuthorityState:
        return AuthorityState()


@contextmanager
def observation_database(database_url: str, tenant_id: str):
    """Dedicated short-lived PostgreSQL reader; cannot reuse a caller transaction.

The configured credential must be a least-privilege reader. A read-only
transaction is defense in depth, not a replacement for database grants/RLS.
"""
    import psycopg
    from psycopg.rows import dict_row

    if str(uuid.UUID(tenant_id)) != tenant_id:
        raise ObservationDenied("invalid_tenant")
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5) as raw:
        raw.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY")
        raw.execute("SET LOCAL statement_timeout = '15s'")
        raw.execute("SET LOCAL lock_timeout = '2s'")
        raw.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        yield PostgresConnection(raw)


def _arguments(tool_name: str, supplied: dict) -> dict:
    if not isinstance(tool_name,str):
        raise ObservationDenied('tool_not_registered')
    definition = TOOL_REGISTRY.get(tool_name)
    if definition is None:
        raise ObservationDenied("tool_not_registered")
    if not isinstance(supplied, dict):
        raise ObservationDenied("invalid_arguments")
    args = dict(supplied)
    if set(args) - set(definition.input_schema["properties"]):
        raise ObservationDenied("unexpected_arguments")
    subject = args.get("subject_id")
    if (not isinstance(subject, str) or not subject or subject != subject.strip()
            or len(subject) > 120 or any(ord(c) < 32 for c in subject)):
        raise ObservationDenied("invalid_subject")
    if args.get("as_of") is not None:
        value = args["as_of"]
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ObservationDenied("invalid_date")
    if "limit" in args and (type(args["limit"]) is not int or not 1 <= args["limit"] <= 200):
        raise ObservationDenied("invalid_limit")
    if args.get("benchmark_id") is not None:
        value = args["benchmark_id"]
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > 120:
            raise ObservationDenied("invalid_benchmark")
    return args


def _guard_database(connection, tenant_id: str) -> None:
    row = connection.execute("""
        SELECT current_setting('transaction_read_only') AS read_only,
               current_setting('app.tenant_id', true) AS tenant_id,
               current_setting('statement_timeout') AS statement_timeout,
               current_setting('lock_timeout') AS lock_timeout,
               current_setting('transaction_isolation') AS isolation,
               rolsuper, rolbypassrls
        FROM pg_roles WHERE rolname = current_user
    """).fetchone()
    if (not row or row["read_only"] != "on" or row["tenant_id"] != tenant_id
            or row["rolsuper"] is not False or row["rolbypassrls"] is not False
            or row["isolation"] != "read committed"
            or row["lock_timeout"] != "2s"
            or row["statement_timeout"] != "15s"):
        raise ObservationDenied("database_isolation_unverified")


def _authorize_user(connection, caller: Caller, subject_id: str) -> None:
    identity = resolve_authorized_identity(connection, tenant_id=caller.tenant_id,
        object_id=caller.user_object_id, email=caller.user_email)
    if identity is None:
        raise ObservationDenied("user_not_authorized")
    row = connection.execute("""
        SELECT subject.subject_id
        FROM platform_subjects subject
        JOIN subject_feature_states feature ON feature.tenant_id = subject.tenant_id
          AND feature.subject_id = subject.subject_id
        WHERE subject.tenant_id = ? AND subject.subject_id = ?
          AND subject.lifecycle_status = 'active'
          AND feature.feature_key = 'ai_analyst' AND feature.feature_state = 'active'
    """, (caller.tenant_id, subject_id)).fetchone()
    if row is None:
        raise ObservationDenied("subject_not_available")
    if not set(identity.roles) & {"owner", "platform_admin"}:
        levels = subject_access_levels(connection, tenant_id=caller.tenant_id,
            object_id=identity.object_id, subject_id=subject_id)
        if not levels & {"read", "report", "analyze", "approve", "admin"}:
            raise ObservationDenied("user_scope_denied")


class ObservationBroker:
    """Interactive subject observations only; no background or resource execution.

connection_factory(tenant_id) must yield a fresh guarded reader. AuthoritySource
must be an authenticated server repository. Both are trusted composition, never
chosen by a user or model. No enabled authority source is shipped here.
"""
    def __init__(self, connection_factory, *, authority: AuthoritySource | None = None,
                 audit=None, enabled: bool = False, clock=None):
        self.connection_factory = connection_factory
        self.authority = authority or DisabledAuthority()
        self.enabled = enabled
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.audit = audit

    def _preflight(self, caller: Caller, tool_name: str, subject_id: str):
        state = self.authority.load_current(tenant_id=caller.tenant_id,
            principal_id=caller.agent_principal_id)
        if state.grant is None:
            raise ObservationDenied("agent_not_registered")
        decision = preflight_observation(state.grant,
            ObservationRequest(caller.tenant_id, caller.agent_principal_id, tool_name,
                subject_id=subject_id, user_subject_ids=frozenset({subject_id})),
            now=self.clock(), kill_switch=state.kill_switch)
        if not decision["eligible_for_adapter_check"]:
            raise ObservationDenied(decision["reason"])
        return state.grant.role

    def observe(self, caller: Caller, tool_name: str, arguments: dict) -> dict:
        correlation = str(uuid.uuid4())
        status, code = "denied", "broker_disabled"
        request_hash = response_hash = None
        detail=None
        intent_recorded=False
        attempted=False
        def record(phase,error_code=None):
            evidence={**detail,'phase':phase,'code':error_code,'response_sha256':response_hash,
                      'tool_attempted':attempted}
            try:
                verify_receipt(self.audit.record(evidence),evidence)
            except Exception:
                raise ObservationDenied('audit_unavailable') from None
        def failed(phase,error_code):
            nonlocal status,code
            if intent_recorded and error_code!='audit_unavailable':
                try: record(phase,error_code)
                except ObservationDenied:
                    status,code='failed','audit_unavailable'
                    raise
        def recheck(connection,subject,role):
            if self._preflight(caller,tool_name,subject)!=role:
                raise ObservationDenied('agent_role_changed')
            _authorize_user(connection,caller,subject)
        try:
            if self.enabled is not True:
                raise ObservationDenied(code)
            if self.audit is None or not callable(getattr(self.audit,'record',None)):
                raise ObservationDenied('protected_audit_required')
            if not all(isinstance(value,str) and value for value in
                       (caller.user_object_id,caller.user_email,caller.agent_principal_id,caller.tenant_id)):
                raise ObservationDenied("authenticated_interactive_caller_required")
            args = _arguments(tool_name, arguments)
            request_hash = hashlib.sha256(canonical_json(args).encode()).hexdigest()
            subject = args["subject_id"]
            role=self._preflight(caller, tool_name, subject)
            with self.connection_factory(caller.tenant_id) as connection:
                _guard_database(connection, caller.tenant_id)
                _authorize_user(connection, caller, subject)
                recheck(connection,subject,role)
                detail={'schema':AUDIT_SCHEMA,'tenant_id':caller.tenant_id,'operation_id':correlation,
                    'role':role,'action':'inspect_and_analyze','phase':'started','tool_name':tool_name,
                    'subject_id':subject,'agent_sha256':hashlib.sha256(caller.agent_principal_id.encode()).hexdigest(),
                    'user_sha256':hashlib.sha256(caller.user_object_id.encode()).hexdigest(),
                    'request_sha256':request_hash,'response_sha256':None,'code':None,'policy_version':POLICY_VERSION,
                    'tool_attempted':False,'result_delivery_confirmed':False}
                record('started');intent_recorded=True
                # Authority may change while protected evidence is being stored.
                recheck(connection,subject,role)
                attempted=True
                result = execute_tool(connection, AgentToolContext(caller.tenant_id,
                    caller.user_object_id, frozenset({subject}), correlation), tool_name, args)
                # Do not deliver a result after an authority/user revocation during work.
                recheck(connection,subject,role)
                if result.get("subject_id") != subject or result.get("tool_name") != tool_name:
                    raise ObservationDenied("result_scope_mismatch")
                if len(json.dumps(result, default=str).encode("utf-8")) > 2_000_000:
                    raise ObservationDenied("result_size_limit")
                response_hash = hashlib.sha256(canonical_json(result).encode()).hexdigest()
                # Completion records computation, not client receipt. A later
                # revocation gets a separate denied event and no result delivery.
                record('succeeded')
                recheck(connection,subject,role)
            status, code = "success", None
            return result
        except ObservationDenied as error:
            code = str(error)
            failed('denied',code)
            raise
        except AgentToolError:
            status, code = "failed", "tool_unavailable"
            failed('failed',code)
            raise ObservationDenied(code) from None
        except (ValueError, TypeError):
            status, code = "failed", "invalid_request_or_authority"
            failed('failed',code)
            raise ObservationDenied(code) from None
        except Exception:
            status, code = "failed", "observation_unavailable"
            failed('failed',code)
            raise ObservationDenied(code) from None
        finally:
            # Never log prompts, arguments, results, email or exception text.
            def fingerprint(value):
                return hashlib.sha256(value.encode()).hexdigest() if isinstance(value,str) else None
            event = {"event_type": "specialist_observation", "correlation_id": correlation,
                "status": status, "code": code,
                "agent_sha256": fingerprint(caller.agent_principal_id),
                "user_sha256": fingerprint(caller.user_object_id),
                "tenant_sha256": fingerprint(caller.tenant_id),
                "request_sha256": request_hash, "response_sha256": response_hash,
                "policy_version": POLICY_VERSION,
                "tool_name": tool_name if isinstance(tool_name,str) and tool_name in TOOL_REGISTRY else None,
                "mutation_authorized": False}
            LOGGER.info("SPECIALIST_OBSERVATION %s", json.dumps(event, sort_keys=True))
