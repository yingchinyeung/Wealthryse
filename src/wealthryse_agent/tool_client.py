"""MCP client for the tool server, used by the agent runtime and the jobs."""
from __future__ import annotations

import itertools
import json
from typing import Any

import httpx

from .auth import TokenSource


class ToolCallFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolClient:
    def __init__(self, base_url: str, tokens: TokenSource, *, user: str | None = None, job: str | None = None,
                 correlation_id: str, transport: httpx.BaseTransport | None = None) -> None:
        if bool(user) == bool(job):
            raise ValueError("A tool client acts for exactly one person or one job")
        self._url = base_url.rstrip("/") + "/mcp"
        self._tokens = tokens
        self._principal = {"X-WR-User": user} if user else {"X-WR-Job": str(job)}
        self._correlation_id = correlation_id
        self._ids = itertools.count(1)
        self._http = httpx.Client(timeout=30.0, transport=transport)

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        headers = {**self._tokens.header(), **self._principal, "X-WR-Correlation": self._correlation_id,
                   "Accept": "application/json, text/event-stream"}
        response = self._http.post(self._url, headers=headers,
                                   json={"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params})
        if response.status_code in (401, 403):
            raise ToolCallFailed("tool_server_denied", "The tool server refused this caller.")
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            raise ToolCallFailed("tool_protocol_error", str(body["error"].get("message")))
        return body["result"]

    def list_tools(self) -> list[dict[str, Any]]:
        return self._rpc("tools/list", {})["tools"]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        payload = result.get("structuredContent")
        if payload is None:
            payload = json.loads(result["content"][0]["text"])
        if result.get("isError"):
            error = payload.get("error", {})
            raise ToolCallFailed(str(error.get("code", "tool_error")), str(error.get("message", "The tool failed.")))
        return payload

    def close(self) -> None:
        self._http.close()
