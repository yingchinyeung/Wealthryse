"""Read-only tool server, spoken over MCP (JSON-RPC 2.0 over HTTP).

The only agent component that reads portfolio data. Callers are the agent
runtime (acting for a signed-in person named in X-WR-User) and the scheduled
jobs (named in X-WR-Job). The person's or job's accounts are looked up in the
database on every call; nothing the model says can widen them.

Run: uvicorn wealthryse_agent.tool_server:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import access
from .auth import Unauthorized, verify_service_token
from .config import DatabaseSettings, ServiceAuthSettings
from .database import connect, scoped
from .tools import REGISTRY, TOOLS, ToolError, run_tool

LOGGER = logging.getLogger("wealthryse_agent.tools")
PROTOCOL_VERSION = "2025-06-18"
POOL_SIZE = 4


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class ConnectionPool:
    def __init__(self, settings: DatabaseSettings, size: int = POOL_SIZE) -> None:
        self._settings = settings
        self._idle: queue.LifoQueue = queue.LifoQueue(maxsize=size)
        for _ in range(size):
            self._idle.put(None)

    @contextmanager
    def connection(self) -> Iterator[Any]:
        conn = self._idle.get(timeout=10)
        healthy = True
        try:
            if conn is None:
                conn = connect(self._settings, search_path="agent")
            yield conn
        except Exception:
            healthy = False
            raise
        finally:
            if not healthy and conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
            self._idle.put(conn)


class ToolService:
    def __init__(self, db: DatabaseSettings, auth: ServiceAuthSettings,
                 user_callers: frozenset[str], job_callers: frozenset[str]) -> None:
        self.db = db
        self.auth = auth
        self.user_callers = user_callers
        self.job_callers = job_callers
        self.pool = ConnectionPool(db)

    def _caller_scope(self, conn, caller_id: str, user: str | None, job: str | None) -> tuple[str, str, list[str]]:
        local = self.auth.disabled_for_local_tests
        if user and (local or caller_id in self.user_callers):
            return "user", user.lower(), access.user_accounts(conn, user)
        if job and (local or caller_id in self.job_callers):
            return "service", job, access.job_accounts(conn, job)
        raise Unauthorized("caller may not act for that principal")

    def _audit(self, conn, *, correlation_id: str, caller_kind: str, principal: str, tool: str,
               subject: str | None, request_sha: str, response_sha: str | None, status: str,
               error_code: str | None, started: float) -> None:
        try:
            conn.execute(
                "INSERT INTO agent.tool_calls(tenant_id, correlation_id, caller_kind, caller_sha256, tool_name, "
                "subject_id, request_sha256, response_sha256, status, error_code, duration_ms) "
                "VALUES (?::uuid, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.db.tenant_id, correlation_id, caller_kind, hashlib.sha256(principal.encode()).hexdigest(), tool,
                 subject, request_sha, response_sha, status, error_code, int((time.monotonic() - started) * 1000)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            LOGGER.exception("tool audit write failed")
            raise

    def call(self, *, caller_id: str, user: str | None, job: str | None, correlation_id: str,
             name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        request_sha = _sha({"tool": name, "arguments": arguments})
        with self.pool.connection() as conn:
            kind, principal, allowed = self._caller_scope(conn, caller_id, user, job)
            subject = arguments.get("account_id") if isinstance(arguments, dict) else None
            spec = REGISTRY.get(name)
            try:
                if spec is None:
                    raise ToolError("unknown_tool", "That tool does not exist.", 404)
                if spec.needs_account:
                    if not isinstance(subject, str) or subject not in allowed:
                        raise ToolError("account_not_allowed", "You do not have access to that account.", 403)
                    scope = [subject]
                else:
                    scope = allowed
                with scoped(conn, scope):
                    result = run_tool(conn, name, arguments)
            except ToolError as error:
                self._audit(conn, correlation_id=correlation_id, caller_kind=kind, principal=principal, tool=name,
                            subject=subject if isinstance(subject, str) else None, request_sha=request_sha,
                            response_sha=None, status="denied" if error.status == 403 else "failed",
                            error_code=error.code, started=started)
                raise
            self._audit(conn, correlation_id=correlation_id, caller_kind=kind, principal=principal, tool=name,
                        subject=subject if isinstance(subject, str) else None, request_sha=request_sha,
                        response_sha=_sha(result), status="success", error_code=None, started=started)
            return result


def tool_list() -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description, "inputSchema": t.parameters,
             "annotations": {"readOnlyHint": True, "openWorldHint": False}} for t in TOOLS]


def _rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def create_app(service: ToolService) -> FastAPI:
    app = FastAPI(title="WealthRyse tool server", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/mcp")
    async def mcp(request: Request) -> JSONResponse:
        try:
            caller = verify_service_token(request.headers.get("authorization"), service.auth)
        except Unauthorized as error:
            LOGGER.warning("service token rejected: %s (%s)", error, type(error.__cause__).__name__)
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            message = await request.json()
        except ValueError:
            return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return JSONResponse(_rpc_error(None, -32600, "Invalid request"), status_code=400)
        request_id, method = message.get("id"), message.get("method")
        params = message.get("params") or {}
        if method == "notifications/initialized":
            return JSONResponse(None, status_code=202)
        if method == "initialize":
            return JSONResponse(_rpc_result(request_id, {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "wealthryse-tools", "version": "1.0.0"}}))
        if method == "ping":
            return JSONResponse(_rpc_result(request_id, {}))
        if method == "tools/list":
            return JSONResponse(_rpc_result(request_id, {"tools": tool_list()}))
        if method != "tools/call":
            return JSONResponse(_rpc_error(request_id, -32601, "Method not found"))
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        correlation_id = request.headers.get("x-wr-correlation") or str(uuid.uuid4())
        from starlette.concurrency import run_in_threadpool

        try:
            result = await run_in_threadpool(
                service.call, caller_id=caller.object_id, user=request.headers.get("x-wr-user"),
                job=request.headers.get("x-wr-job"), correlation_id=correlation_id[:64], name=name,
                arguments=arguments if isinstance(arguments, dict) else {"_invalid": True})
        except Unauthorized:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        except ToolError as error:
            payload = {"error": {"code": error.code, "message": error.message}}
            return JSONResponse(_rpc_result(request_id, {
                "content": [{"type": "text", "text": json.dumps(payload)}], "structuredContent": payload, "isError": True}))
        except Exception:
            LOGGER.exception("tool call failed", extra={"tool": name, "correlation_id": correlation_id})
            payload = {"error": {"code": "internal_error", "message": "The tool failed. No data was returned."}}
            return JSONResponse(_rpc_result(request_id, {
                "content": [{"type": "text", "text": json.dumps(payload)}], "structuredContent": payload, "isError": True}))
        return JSONResponse(_rpc_result(request_id, {
            "content": [{"type": "text", "text": json.dumps(result)}], "structuredContent": result, "isError": False}))

    return app


def _from_env() -> FastAPI:
    split = lambda name: frozenset(v.strip().lower() for v in os.environ.get(name, "").split(",") if v.strip())
    auth = ServiceAuthSettings.from_env("AGENT_TOOLS")
    users, jobs = split("AGENT_TOOLS_USER_CALLERS"), split("AGENT_TOOLS_JOB_CALLERS")
    auth = ServiceAuthSettings(auth.entra_tenant_id, auth.audience, users | jobs, auth.disabled_for_local_tests)
    return create_app(ToolService(DatabaseSettings.from_env(), auth, users, jobs))


app = _from_env() if os.environ.get("AGENT_TOOLS_AUTOSTART", "1") == "1" else None
