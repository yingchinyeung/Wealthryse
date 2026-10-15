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
import queue
import re
import threading
import uuid
from datetime import date
from typing import Any, Callable, Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import memory
from .auth import ServiceCaller, TokenSource, Unauthorized, verify_service_token
from .config import DatabaseSettings, ModelSettings, RuntimeSettings, ServiceAuthSettings
from .database import connect
from .month_team.store import MonthStore
from .prompts import ANALYST, CASE_ANALYST
from . import calculator
from .runtime import Agent, AgentError
from .store import Store
from .tool_client import ToolCallFailed, ToolClient

LOGGER = logging.getLogger("wealthryse_agent.runtime")
USER_ID = r"^[0-9a-fA-F-]{36}$"
PERIOD = r"^\d{4}-\d{2}-\d{2}$"
MONTH_MODE = r"^(month|replay)$"
# Deep reasoning can think for minutes without an event; a comment line keeps the website's read open.
KEEPALIVE_SECONDS = 15.0


def _with_keepalive(events: Iterator[dict[str, Any]], interval: float) -> Iterator[dict[str, Any] | None]:
    """Yield the agent's events as they come, and None whenever ``interval`` passes without one."""
    box: queue.Queue = queue.Queue()

    def pump() -> None:
        try:
            for event in events:
                box.put(("event", event))
        except BaseException as error:  # handed to the caller's thread
            box.put(("error", error))
        finally:
            box.put(("done", None))

    threading.Thread(target=pump, daemon=True, name="agent-run").start()
    while True:
        try:
            kind, item = box.get(timeout=interval)
        except queue.Empty:
            yield None
            continue
        if kind == "done":
            return
        if kind == "error":
            raise item
        yield item


class ChatRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    account_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=36)


class CaseQuestionRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    case_id: str = Field(pattern=USER_ID)
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=36)
    # Computed by the platform from the case as it is now (the readiness pack and the agents' results).
    case_facts: dict[str, Any]


MAX_CASE_FACTS_BYTES = 200_000
CASE_TOOL = "get_case"


class CaseFacts:
    """A one-tool client over the case facts the website sent, so the usual validator checks every figure."""

    def __init__(self, case_id: str, facts: dict[str, Any]) -> None:
        self.case_id, self.facts = case_id, facts

    def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": CASE_TOOL, "description": "Everything known about this onboarding case right now.",
                 "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name != CASE_TOOL:
            raise ToolCallFailed("unknown_tool", "That tool is not available here.")
        return {"tool": CASE_TOOL, "account_id": None, "as_of": None, "data": self.facts,
                "citations": [{"source": "onboarding_case", "case_id": self.case_id}]}

    def close(self) -> None:
        pass


def case_subject(case_id: str) -> str:
    """Case conversations sit beside account ones, keyed so they never mix."""
    return "case:" + case_id.lower()


class FeedbackRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=1000)


class MemoryRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    value: str = Field(min_length=1, max_length=200)


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
                 tools_factory: Callable[[str, str], ToolClient],
                 month_store_factory: Callable[[], MonthStore] | None = None) -> None:
        self.auth = auth
        self.runtime = runtime
        self.tenant_id = tenant_id
        self._store_factory = store_factory
        self._agent_factory = agent_factory
        self.tools_for = tools_factory
        self._store: _LockedStore | None = None
        self._store_lock = threading.Lock()
        self._month_store_factory = month_store_factory
        self._month_store: _LockedStore | None = None
        self._agent: Agent | None = None
        self.keepalive_seconds = KEEPALIVE_SECONDS

    def store(self) -> Store:
        with self._store_lock:
            if self._store is None:
                self._store = _LockedStore(self._store_factory)
        return self._store  # type: ignore[return-value]

    def month_store(self) -> MonthStore:
        if self._month_store_factory is None:
            raise HTTPException(status_code=503, detail="The month-end team is not configured.")
        with self._store_lock:
            if self._month_store is None:
                self._month_store = _LockedStore(self._month_store_factory)
        return self._month_store  # type: ignore[return-value]

    def agent(self) -> Agent:
        if self._agent is None:
            self._agent = self._agent_factory()
        return self._agent


def _sse(event: dict[str, Any]) -> str:
    return "data: " + json.dumps(event, default=str) + "\n\n"


def _remember(store: Store, user: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Save what the person asked the analyst to remember; anything that fails the checks is dropped."""
    saved = []
    for item in items:
        try:
            value = memory.clean(str(item.get("key")), item.get("value"))
        except memory.MemoryError:
            continue
        store.remember(user, str(item["key"]), value, "chat")
        saved.append({"key": item["key"], "label": memory.KEYS[str(item["key"])][0], "value": value})
    return saved


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

        instructions = ANALYST.format(account_id=body.account_id, today=date.today().isoformat()) \
            + memory.prompt_block(store.memories(user))

        def events() -> Iterator[str]:
            yield _sse({"type": "start", "conversation_id": conversation_id, "correlation_id": correlation_id})
            tools = services.tools_for(user, correlation_id)
            try:
                run = services.agent().run(question=body.message, account_id=body.account_id, tools=tools, history=history,
                                           instructions=instructions)
                yield from _answers(run, store, user, conversation_id, correlation_id, remember=True)
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

    def _answers(run: Iterator[dict[str, Any]], store: Store, user: str, conversation_id: str,
                 correlation_id: str, *, remember: bool) -> Iterator[str]:
        """The run's events as server-sent events, saving the final answer (and any memories) on the way."""
        for event in _with_keepalive(run, services.keepalive_seconds):
            if event is None:
                yield ": keepalive\n\n"
                continue
            if event["type"] in ("answer", "rejected"):
                turn = event.pop("turn")
                # Charts and tables ride in the citations column, so no schema change is needed.
                message_id = store.add_message(
                    conversation_id, "assistant", turn.answer,
                    citations=list(turn.citations) + [{"visual": v} for v in turn.visuals],
                    status="ok" if event["type"] == "answer" else "rejected",
                    correlation_id=correlation_id, usage=turn.usage)
                event.pop("reason", None)  # validator detail stays in logs
                event["message_id"] = message_id
                remembered = _remember(store, user, turn.remember) if remember and event["type"] == "answer" else []
                yield _sse(event)
                if remembered:
                    yield _sse({"type": "remembered", "memories": remembered})
                continue
            yield _sse(event)

    @app.post("/v1/case-question")
    def case_question(body: CaseQuestionRequest, _: ServiceCaller = Depends(caller)) -> StreamingResponse:
        """The owner's question about one onboarding case, answered from the facts the website sent."""
        correlation_id = str(uuid.uuid4())
        user = body.user_object_id.lower()
        if len(json.dumps(body.case_facts, default=str)) > MAX_CASE_FACTS_BYTES:
            raise HTTPException(status_code=413, detail="This case has too much to read at once.")
        store = services.store()
        if store.tokens_used_today() >= services.runtime.daily_token_budget:
            raise HTTPException(status_code=429, detail="Today's analysis budget is used up. Try again tomorrow.")
        subject = case_subject(body.case_id)
        if body.conversation_id:
            conversation = store.conversation(body.conversation_id, user)
            if conversation is None or conversation["subject_id"] != subject:
                raise HTTPException(status_code=404, detail="Conversation not found.")
            conversation_id = body.conversation_id
            history = [{"role": m["role"], "content": m["content"]} for m in store.messages(conversation_id)
                       if m["status"] == "ok"]
        else:
            conversation_id = store.create_conversation(user, subject, body.question.strip()[:80])
            history = []
        store.add_message(conversation_id, "user", body.question.strip(), correlation_id=correlation_id)
        instructions = CASE_ANALYST.format(case_id=body.case_id.lower(), today=date.today().isoformat())

        def events() -> Iterator[str]:
            yield _sse({"type": "start", "conversation_id": conversation_id, "correlation_id": correlation_id})
            try:
                # The portfolio router would turn case questions away; the instructions keep it on the case.
                run = services.agent().run(question=body.question, account_id=None, tools=CaseFacts(body.case_id, body.case_facts),
                                           history=history, allowed_tools=[CASE_TOOL, calculator.NAME],
                                           instructions=instructions, skip_router=True)
                yield from _answers(run, store, user, conversation_id, correlation_id, remember=False)
            except AgentError as error:
                store.add_message(conversation_id, "assistant", error.message, status="failed",
                                  correlation_id=correlation_id)
                yield _sse({"type": "error", "code": error.code, "message": error.message})
            except Exception:
                LOGGER.exception("case question failed", extra={"correlation_id": correlation_id})
                yield _sse({"type": "error", "code": "internal_error",
                            "message": "Something went wrong. Nothing was saved as an answer."})
            yield _sse({"type": "done"})

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/v1/memory")
    def memories(user_object_id: str = Query(pattern=USER_ID), _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        return {"memories": memory.listed(services.store().memories(user_object_id)),
                "keys": [{"key": k, "label": label, "choices": list(choices) if choices else None, "max_length": limit}
                         for k, (label, choices, limit) in memory.KEYS.items()]}

    @app.put("/v1/memory/{key}")
    def set_memory(key: str, body: MemoryRequest, _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        try:
            value = memory.clean(key, body.value)
        except memory.MemoryError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        store = services.store()
        store.remember(body.user_object_id, key, value, "settings")
        return {"memories": memory.listed(store.memories(body.user_object_id))}

    @app.delete("/v1/memory")
    def forget_all(user_object_id: str = Query(pattern=USER_ID), _: ServiceCaller = Depends(caller)) -> dict[str, int]:
        return {"deleted": services.store().forget(user_object_id)}

    @app.delete("/v1/memory/{key}")
    def forget(key: str, user_object_id: str = Query(pattern=USER_ID), _: ServiceCaller = Depends(caller)) -> dict[str, int]:
        if key not in memory.KEYS:
            raise HTTPException(status_code=404, detail="Unknown preference.")
        return {"deleted": services.store().forget(user_object_id, key)}

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

    @app.get("/v1/month-end/runs")
    def month_runs(user_object_id: str, period_end: str | None = Query(default=None, pattern=PERIOD),
                   mode: str = Query(default="month", pattern=MONTH_MODE),
                   replay_round: int | None = Query(default=None, alias="round", ge=1, le=999),
                   _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        if replay_round is not None and mode != "replay":
            raise HTTPException(status_code=400, detail="Only a replay has a round.")
        accounts = accounts_for(user_object_id.lower(), str(uuid.uuid4()))
        store = services.month_store()
        return {"runs": store.runs(accounts, period_end, mode, replay_round), "team": store.team_status()}

    @app.get("/v1/month-end/rounds")
    def month_rounds(user_object_id: str, _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        """Each replay round side by side, over the accounts this person may see."""
        accounts = accounts_for(user_object_id.lower(), str(uuid.uuid4()))
        return {"rounds": services.month_store().round_summary(accounts)}

    @app.get("/v1/month-end/runs/{account_id}/{period_end}")
    def month_run(account_id: str, period_end: str, user_object_id: str,
                  mode: str = Query(default="month", pattern=MONTH_MODE),
                  replay_round: int | None = Query(default=None, alias="round", ge=1, le=999),
                  _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        if replay_round is not None and mode != "replay":
            raise HTTPException(status_code=400, detail="Only a replay has a round.")
        if not re.match(PERIOD, period_end):
            raise HTTPException(status_code=400, detail="period_end must be YYYY-MM-DD.")
        if account_id not in accounts_for(user_object_id.lower(), str(uuid.uuid4())):
            raise HTTPException(status_code=403, detail="You do not have access to that account.")
        found = services.month_store().run_detail(account_id, period_end, mode, replay_round)
        if found is None:
            raise HTTPException(status_code=404, detail="The team has not worked on that month yet.")
        return found

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
        month_store_factory=lambda: MonthStore(connect(db, search_path="agent"), db.tenant_id),
    )
    return create_app(services)


app = _from_env() if os.environ.get("AGENT_API_AUTOSTART", "1") == "1" else None
