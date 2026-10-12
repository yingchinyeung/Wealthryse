"""Agent runtime API, called only by the website over the internal network.

The website signs in with its managed identity and names the signed-in
person in ``user_object_id``; this service trusts that only because the
caller is the website's identity. The person's accounts are always checked
again by the tool server.

Run: uvicorn wealthryse_agent.agent_server:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from typing import Any, Callable, Iterator

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .auth import ServiceCaller, TokenSource, Unauthorized, verify_service_token
from .config import DatabaseSettings, ModelSettings, RuntimeSettings, ServiceAuthSettings
from .database import connect
from .runtime import Agent, AgentError
from .store import Store
from .tool_client import ToolCallFailed, ToolClient

LOGGER = logging.getLogger("wealthryse_agent.runtime")
USER_ID = r"^[0-9a-fA-F-]{36}$"


class ChatRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    account_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=36)


class FeedbackRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=1000)


class ArchiveRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)


class DecisionRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    decision: str = Field(pattern="^(read|approved|dismissed)$")


class _LockedStore:
    """One database connection shared by all requests, one call at a time.

    Store calls are short single statements; a broken connection is replaced.
    """

    def __init__(self, factory: Callable[[], Store]) -> None:
        self._factory = factory
        self._store: Store | None = None
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                if self._store is None:
                    self._store = self._factory()
                try:
                    return getattr(self._store, name)(*args, **kwargs)
                except Exception:
                    try:
                        self._store.db.close()
                    except Exception:
                        pass
                    self._store = None
                    raise
        return call


class Services:
    """Process-wide dependencies, replaceable in tests."""

    def __init__(self, *, auth: ServiceAuthSettings, runtime: RuntimeSettings, tenant_id: str,
                 store_factory: Callable[[], Store], agent_factory: Callable[[], Agent],
                 tools_factory: Callable[[str, str], ToolClient]) -> None:
        self.auth = auth
        self.runtime = runtime
        self.tenant_id = tenant_id
        self._store_factory = store_factory
        self._agent_factory = agent_factory
        self.tools_for = tools_factory
        self._store: _LockedStore | None = None
        self._store_lock = threading.Lock()
        self._agent: Agent | None = None

    def store(self) -> Store:
        with self._store_lock:
            if self._store is None:
                self._store = _LockedStore(self._store_factory)
        return self._store  # type: ignore[return-value]

    def agent(self) -> Agent:
        if self._agent is None:
            self._agent = self._agent_factory()
        return self._agent


def _sse(event: dict[str, Any]) -> str:
    return "data: " + json.dumps(event, default=str) + "\n\n"


def create_app(services: Services) -> FastAPI:
    app = FastAPI(title="WealthRyse agent", docs_url=None, redoc_url=None, openapi_url=None)

    def caller(authorization: str | None = Header(default=None)) -> ServiceCaller:
        try:
            return verify_service_token(authorization, services.auth)
        except Unauthorized as error:
            raise HTTPException(status_code=401, detail="unauthorized") from error

    def accounts_for(user: str, correlation_id: str) -> list[str]:
        tools = services.tools_for(user, correlation_id)
        try:
            listed = tools.call("list_accounts", {})
        except ToolCallFailed as error:
            raise HTTPException(status_code=503, detail="Account access could not be checked.") from error
        finally:
            tools.close()
        return [a["account_id"] for a in listed["data"]["accounts"]]

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/chat")
    def chat(body: ChatRequest, _: ServiceCaller = Depends(caller)) -> StreamingResponse:
        correlation_id = str(uuid.uuid4())
        user = body.user_object_id.lower()
        if body.account_id not in accounts_for(user, correlation_id):
            raise HTTPException(status_code=403, detail="You do not have access to that account.")
        store = services.store()
        if store.tokens_used_today() >= services.runtime.daily_token_budget:
            raise HTTPException(status_code=429, detail="Today's analysis budget is used up. Try again tomorrow.")
        if body.conversation_id:
            conversation = store.conversation(body.conversation_id, user)
            if conversation is None or conversation["subject_id"] != body.account_id:
                raise HTTPException(status_code=404, detail="Conversation not found.")
            conversation_id = body.conversation_id
            history = [{"role": m["role"], "content": m["content"]} for m in store.messages(conversation_id)
                       if m["status"] == "ok"]
        else:
            conversation_id = store.create_conversation(user, body.account_id, body.message.strip()[:80])
            history = []
        store.add_message(conversation_id, "user", body.message.strip(), correlation_id=correlation_id)

        def events() -> Iterator[str]:
            yield _sse({"type": "start", "conversation_id": conversation_id, "correlation_id": correlation_id})
            tools = services.tools_for(user, correlation_id)
            try:
                for event in services.agent().run(question=body.message, account_id=body.account_id,
                                                  tools=tools, history=history):
                    if event["type"] in ("answer", "rejected"):
                        turn = event.pop("turn")
                        message_id = store.add_message(
                            conversation_id, "assistant", turn.answer, citations=turn.citations,
                            status="ok" if event["type"] == "answer" else "rejected",
                            correlation_id=correlation_id, usage=turn.usage)
                        event.pop("reason", None)  # validator detail stays in logs
                        event["message_id"] = message_id
                    yield _sse(event)
            except AgentError as error:
                store.add_message(conversation_id, "assistant", error.message, status="failed",
                                  correlation_id=correlation_id)
                yield _sse({"type": "error", "code": error.code, "message": error.message})
            except Exception:
                LOGGER.exception("agent run failed", extra={"correlation_id": correlation_id})
                yield _sse({"type": "error", "code": "internal_error",
                            "message": "Something went wrong. Nothing was saved as an answer."})
            finally:
                tools.close()
            yield _sse({"type": "done"})

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/v1/conversations")
    def conversations(user_object_id: str, account_id: str, _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        return {"conversations": services.store().conversations(user_object_id, account_id)}

    @app.get("/v1/conversations/{conversation_id}")
    def conversation(conversation_id: str, user_object_id: str, _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        store = services.store()
        found = store.conversation(conversation_id, user_object_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return {**found, "messages": store.messages(conversation_id)}

    @app.post("/v1/conversations/{conversation_id}/archive")
    def archive(conversation_id: str, body: ArchiveRequest, _: ServiceCaller = Depends(caller)) -> dict[str, bool]:
        return {"archived": services.store().archive(conversation_id, body.user_object_id)}

    @app.post("/v1/messages/{message_id}/feedback")
    def feedback(message_id: int, body: FeedbackRequest, _: ServiceCaller = Depends(caller)) -> dict[str, bool]:
        if body.rating == 0:
            raise HTTPException(status_code=400, detail="Rating must be 1 or -1.")
        if not services.store().feedback(message_id, body.user_object_id, body.rating, body.comment):
            raise HTTPException(status_code=404, detail="Message not found.")
        return {"saved": True}

    @app.get("/v1/inbox")
    def inbox(user_object_id: str, status: str | None = None, _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        accounts = accounts_for(user_object_id.lower(), str(uuid.uuid4()))
        return {"items": services.store().inbox(accounts, status)}

    @app.post("/v1/inbox/{item_id}/decision")
    def decide(item_id: int, body: DecisionRequest, _: ServiceCaller = Depends(caller)) -> dict[str, bool]:
        user = body.user_object_id.lower()
        accounts = accounts_for(user, str(uuid.uuid4()))
        if not services.store().decide(item_id, accounts, body.decision, hashlib.sha256(user.encode()).hexdigest()):
            raise HTTPException(status_code=404, detail="Item not found.")
        return {"saved": True}

    return app


def _from_env() -> FastAPI:
    db = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    tokens = TokenSource(runtime.managed_identity_client_id, runtime.tool_server_audience)
    services = Services(
        auth=ServiceAuthSettings.from_env("AGENT_API"),
        runtime=runtime,
        tenant_id=db.tenant_id,
        store_factory=lambda: Store(connect(db, search_path="agent"), db.tenant_id),
        agent_factory=lambda: Agent(ModelSettings.from_env()),
        tools_factory=lambda user, cid: ToolClient(runtime.tool_server_url, tokens, user=user, correlation_id=cid),
    )
    return create_app(services)


app = _from_env() if os.environ.get("AGENT_API_AUTOSTART", "1") == "1" else None
