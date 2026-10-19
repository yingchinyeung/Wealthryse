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
import time
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
from .exports import MAX_FACTS_BYTES as MAX_EXPORT_FACTS_BYTES
from .exports import check_note, plan_export
from .factsheet_design import MAX_FACTS_BYTES as MAX_DESIGN_FACTS_BYTES
from .factsheet_design import build_step, check_design, edit_design
from .month_team.store import MonthStore
from .prompts import (ANALYST, CASE_ANALYST, GUIDE_ACCOUNT_STEPS, GUIDE_ANALYST, GUIDE_NO_ACCOUNT_STEPS,
                      METRIC_EXPLAINER)
from . import calculator, guide_tools
from ibkr_etl.metrics_guide import METRIC_ANCHORS
from .runtime import Agent, AgentError
from .store import Store
from .supervisor import watch as supervisor
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


class ExplainMetricRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    account_id: str = Field(min_length=1, max_length=64)
    metric: str = Field(min_length=1, max_length=120)
    conversation_id: str | None = Field(default=None, max_length=36)


class GuideQuestionRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    question: str = Field(min_length=1, max_length=2000)
    account_id: str | None = Field(default=None, min_length=1, max_length=64)
    conversation_id: str | None = Field(default=None, max_length=36)


def guide_subject(account_id: str | None) -> str:
    """Guide conversations sit beside account ones, keyed so they never mix."""
    return "guide:" + (account_id or "general")


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


class ExportPlanRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    account_id: str = Field(min_length=1, max_length=64)
    request: str = Field(min_length=1, max_length=1000)
    # Computed by the website: the account's datasets, columns, cash activity types, currencies and dates.
    facts: dict[str, Any]


class FactsheetDesignRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    # The account or portfolio the design is for; the website has already checked the person owns it.
    subject_id: str = Field(min_length=1, max_length=64)
    # Built by the website: the spec, the reader's fields, the comparison, the checks or the request.
    facts: dict[str, Any]


class ExportCheckRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    account_id: str = Field(min_length=1, max_length=64)
    plan: dict[str, Any]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = Field(pattern="^(pass|review|fail)$")
    # Computed by the website from the built files: row counts, totals and every check's result.
    facts: dict[str, Any]
    pack_item_id: int | None = Field(default=None, ge=1)


class ExportPackRequest(BaseModel):
    user_object_id: str = Field(pattern=USER_ID)
    account_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    schedule: str = Field(pattern="^(monthly|quarterly|yearly)$")
    plan: dict[str, Any]


MAX_PLAN_BYTES = 20_000


def _owner_digest(user: str) -> str:
    return hashlib.sha256(user.lower().encode()).hexdigest()


def _visible(item: dict[str, Any], user: str) -> bool:
    """An export pack's card belongs to the person who saved the pack, not everyone who sees the account."""
    if item.get("kind") != "export_pack":
        return True
    citations = item.get("citations") or []
    if isinstance(citations, str):
        citations = json.loads(citations)
    return any(isinstance(c, dict) and c.get("owner_sha256") == _owner_digest(user) for c in citations)


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
        # The supervisor's watch-only recorder; it runs on a background thread (replaced in tests).
        self.watch_chat: Callable[..., Any] = supervisor.watch_chat

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
        started = time.monotonic()
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
        # Watch-only: today's analyst answers as before; the supervisor only records what it would have done.
        watching = supervisor.mode() == "watch"

        def events() -> Iterator[str]:
            yield _sse({"type": "start", "conversation_id": conversation_id, "correlation_id": correlation_id})
            tools = guide_tools.GuideTools(services.tools_for(user, correlation_id))
            recorder = supervisor.Recorder(tools) if watching else None

            def observe(outcome: str, turn: Any = None, error_code: str | None = None) -> None:
                if not watching:
                    return
                try:
                    services.watch_chat(store=store, tools_for=services.tools_for, user=user,
                                        account_id=body.account_id, question=body.message,
                                        correlation_id=correlation_id, conversation_id=conversation_id,
                                        outcome=outcome, turn=turn, recorder=recorder, error_code=error_code,
                                        elapsed_ms=int((time.monotonic() - started) * 1000))
                except Exception:
                    LOGGER.warning("supervisor watch did not start", extra={"correlation_id": correlation_id})

            try:
                agent = services.agent()
                run = agent.run(question=body.message, account_id=body.account_id, tools=recorder or tools,
                                history=history, instructions=instructions,
                                reasoning_mode=agent.settings.chat_reasoning_mode)
                yield from _answers(run, store, user, conversation_id, correlation_id, remember=True,
                                    on_turn=observe if watching else None)
            except AgentError as error:
                store.add_message(conversation_id, "assistant", error.message, status="failed",
                                  correlation_id=correlation_id)
                observe("failed", error_code=error.code)
                yield _sse({"type": "error", "code": error.code, "message": error.message})
            except Exception:
                LOGGER.exception("agent run failed", extra={"correlation_id": correlation_id})
                observe("failed", error_code="internal_error")
                yield _sse({"type": "error", "code": "internal_error",
                            "message": "Something went wrong. Nothing was saved as an answer."})
            finally:
                tools.close()
            yield _sse({"type": "done"})

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    def _answers(run: Iterator[dict[str, Any]], store: Store, user: str, conversation_id: str,
                 correlation_id: str, *, remember: bool,
                 on_turn: Callable[[str, Any], None] | None = None) -> Iterator[str]:
        """The run's events as server-sent events, saving the final answer (and any memories) on the way.

        ``on_turn`` hears each saved answer's outcome and turn (the supervisor's watch-only recorder).
        """
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
                links = guide_tools.guide_links(turn.citations) if event["type"] == "answer" else []
                if links:
                    event["guide_links"] = links
                remembered = _remember(store, user, turn.remember) if remember and event["type"] == "answer" else []
                if on_turn is not None:
                    on_turn(event["type"], turn)
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

    def _open_conversation(store: Store, user: str, subject: str, first_text: str,
                           conversation_id: str | None) -> tuple[str, list[dict[str, Any]]]:
        if store.tokens_used_today() >= services.runtime.daily_token_budget:
            raise HTTPException(status_code=429, detail="Today's analysis budget is used up. Try again tomorrow.")
        if conversation_id:
            conversation = store.conversation(conversation_id, user)
            if conversation is None or conversation["subject_id"] != subject:
                raise HTTPException(status_code=404, detail="Conversation not found.")
            return conversation_id, [{"role": m["role"], "content": m["content"]}
                                     for m in store.messages(conversation_id) if m["status"] == "ok"]
        return store.create_conversation(user, subject, first_text[:80]), []

    def _stream(run: Callable[[], Iterator[dict[str, Any]]], store: Store, user: str, conversation_id: str,
                correlation_id: str, tools: Any) -> StreamingResponse:
        def events() -> Iterator[str]:
            yield _sse({"type": "start", "conversation_id": conversation_id, "correlation_id": correlation_id})
            try:
                yield from _answers(run(), store, user, conversation_id, correlation_id, remember=False)
            except AgentError as error:
                store.add_message(conversation_id, "assistant", error.message, status="failed",
                                  correlation_id=correlation_id)
                yield _sse({"type": "error", "code": error.code, "message": error.message})
            except Exception:
                LOGGER.exception("guide answer failed", extra={"correlation_id": correlation_id})
                yield _sse({"type": "error", "code": "internal_error",
                            "message": "Something went wrong. Nothing was saved as an answer."})
            finally:
                tools.close()
            yield _sse({"type": "done"})

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/v1/explain-metric")
    def explain_metric(body: ExplainMetricRequest, _: ServiceCaller = Depends(caller)) -> StreamingResponse:
        """Work one Metrics guide definition through with the account's own numbers."""
        if body.metric not in METRIC_ANCHORS:
            raise HTTPException(status_code=404, detail="The Metrics guide has no definition called that.")
        correlation_id = str(uuid.uuid4())
        user = body.user_object_id.lower()
        if body.account_id not in accounts_for(user, correlation_id):
            raise HTTPException(status_code=403, detail="You do not have access to that account.")
        store = services.store()
        question = f"Work through {body.metric} for this account with its own numbers."
        conversation_id, history = _open_conversation(store, user, body.account_id, question, body.conversation_id)
        store.add_message(conversation_id, "user", question, correlation_id=correlation_id)
        link = f"/metrics-guide#{METRIC_ANCHORS[body.metric]}"
        instructions = METRIC_EXPLAINER.format(metric=body.metric, account_id=body.account_id, link=link,
                                               today=date.today().isoformat())
        allowed = [*guide_tools.NAMES, *guide_tools.tools_for_metric(body.metric), calculator.NAME]
        tools = guide_tools.GuideTools(services.tools_for(user, correlation_id))
        # The question is fixed by the website, so the router has nothing to decide.
        return _stream(lambda: services.agent().run(
            question=question, account_id=body.account_id, tools=tools, history=history, allowed_tools=allowed,
            instructions=instructions, skip_router=True), store, user, conversation_id, correlation_id, tools)

    @app.post("/v1/guide-question")
    def guide_question(body: GuideQuestionRequest, _: ServiceCaller = Depends(caller)) -> StreamingResponse:
        """A plain-language question about the metrics, answered from the guide and, with an account, its figures."""
        correlation_id = str(uuid.uuid4())
        user = body.user_object_id.lower()
        if body.account_id and body.account_id not in accounts_for(user, correlation_id):
            raise HTTPException(status_code=403, detail="You do not have access to that account.")
        store = services.store()
        question = body.question.strip()
        conversation_id, history = _open_conversation(store, user, guide_subject(body.account_id), question,
                                                      body.conversation_id)
        store.add_message(conversation_id, "user", question, correlation_id=correlation_id)
        if body.account_id:
            instructions = GUIDE_ANALYST.format(account_clause=f" and account {body.account_id}'s own figures",
                                                account_steps=GUIDE_ACCOUNT_STEPS.format(account_id=body.account_id),
                                                today=date.today().isoformat())
            tools = guide_tools.GuideTools(services.tools_for(user, correlation_id))
            allowed = [*guide_tools.NAMES, *sorted({t for names in guide_tools.SECTION_TOOLS.values() for t in names}),
                       calculator.NAME]
        else:
            instructions = GUIDE_ANALYST.format(account_clause="", account_steps=GUIDE_NO_ACCOUNT_STEPS,
                                                today=date.today().isoformat())
            tools = guide_tools.GuideTools()
            allowed = [*guide_tools.NAMES]
        # Definition questions ("what is CVaR?") are not portfolio questions, so the router is skipped;
        # the instructions keep the answer on the guide.
        return _stream(lambda: services.agent().run(
            question=question, account_id=body.account_id, tools=tools, history=history, allowed_tools=allowed,
            instructions=instructions, skip_router=True), store, user, conversation_id, correlation_id, tools)

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
        # Cards about the whole website come back too; the website shows them to owners only.
        accounts = [*accounts_for(user_object_id.lower(), str(uuid.uuid4())), guide_tools.PLATFORM_SUBJECT]
        return {"items": [i for i in services.store().inbox(accounts, status) if _visible(i, user_object_id)]}

    @app.post("/v1/inbox/{item_id}/decision")
    def decide(item_id: int, body: DecisionRequest, _: ServiceCaller = Depends(caller)) -> dict[str, bool]:
        user = body.user_object_id.lower()
        accounts = [*accounts_for(user, str(uuid.uuid4())), guide_tools.PLATFORM_SUBJECT]
        store = services.store()
        hidden = [i for i in store.inbox(accounts) if i["item_id"] == item_id and not _visible(i, user)]
        if hidden or not store.decide(item_id, accounts, body.decision, hashlib.sha256(user.encode()).hexdigest()):
            raise HTTPException(status_code=404, detail="Item not found.")
        return {"saved": True}

    # Export agent ------------------------------------------------------------------------------

    def _export_account(user: str, account_id: str, correlation_id: str) -> None:
        if account_id not in accounts_for(user, correlation_id):
            raise HTTPException(status_code=403, detail="You do not have access to that account.")

    def _record_usage(store: Store, job: str, correlation_id: str, usage: dict[str, int], ok: bool, code: str | None = None) -> None:
        """Planner and checker tokens count against the daily budget with chat and the jobs."""
        run_id = store.start_run(job, correlation_id)
        store.finish_run(run_id, status="succeeded" if ok else "failed", items=1 if ok else 0, usage=usage,
                         error_code=code)

    @app.post("/v1/exports/plan")
    def export_plan(body: ExportPlanRequest, _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        correlation_id = str(uuid.uuid4())
        user = body.user_object_id.lower()
        _export_account(user, body.account_id, correlation_id)
        if len(json.dumps(body.facts, default=str)) > MAX_EXPORT_FACTS_BYTES:
            raise HTTPException(status_code=413, detail="This account has too much to describe at once.")
        store = services.store()
        if store.tokens_used_today() >= services.runtime.daily_token_budget:
            raise HTTPException(status_code=429, detail="Today's analysis budget is used up. Try again tomorrow.")
        try:
            plan, turn = plan_export(services.agent(), account_id=body.account_id, request=body.request.strip(),
                                     facts=body.facts)
        except AgentError as error:
            _record_usage(store, "export_planner", correlation_id, {}, False, error.code)
            raise HTTPException(status_code=422, detail=error.message) from error
        _record_usage(store, "export_planner", correlation_id, turn.usage, True)
        return {"plan": plan, "correlation_id": correlation_id}

    @app.post("/v1/exports/checks")
    def export_check(body: ExportCheckRequest, _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        """The checker's cover note over the website's check results, saved as the export's check record."""
        correlation_id = str(uuid.uuid4())
        user = body.user_object_id.lower()
        _export_account(user, body.account_id, correlation_id)
        if len(json.dumps(body.facts, default=str)) > MAX_EXPORT_FACTS_BYTES:
            raise HTTPException(status_code=413, detail="This export has too much to check at once.")
        if len(json.dumps(body.plan, default=str)) > MAX_PLAN_BYTES:
            raise HTTPException(status_code=413, detail="This export plan is too large.")
        store = services.store()
        if body.pack_item_id is not None and not any(
                i["item_id"] == body.pack_item_id and i["kind"] == "export_pack" and _visible(i, user)
                for i in store.inbox([body.account_id])):
            raise HTTPException(status_code=404, detail="Export pack card not found.")
        note, concerns, note_checked = "", [], False
        if store.tokens_used_today() < services.runtime.daily_token_budget:
            try:
                note, concerns, note_checked, turn = check_note(services.agent(), account_id=body.account_id,
                                                                facts=body.facts)
                _record_usage(store, "export_checker", correlation_id, turn.usage, note_checked)
            except AgentError as error:
                _record_usage(store, "export_checker", correlation_id, {}, False, error.code)
            except Exception:
                # The fixed checks stand on their own; without a note the website writes one from them.
                LOGGER.exception("export checker failed", extra={"correlation_id": correlation_id})
        # The checker can only make an export stricter: a concern turns a pass into a review.
        status = "review" if body.status == "pass" and concerns else body.status
        check_id = store.add_export_check(
            user=user, account_id=body.account_id, plan=body.plan, fingerprint=body.fingerprint, status=status,
            note=note, note_checked=note_checked, concerns=concerns, checks=body.facts.get("checks") or [],
            pack_item_id=body.pack_item_id, correlation_id=correlation_id)
        return {"check_id": check_id, "status": status, "note": note, "note_checked": note_checked,
                "concerns": concerns}

    @app.get("/v1/exports/checks/{check_id}")
    def export_check_record(check_id: str, user_object_id: str = Query(pattern=USER_ID),
                            _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        found = services.store().export_check(check_id, user_object_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Export not found.")
        return found

    @app.post("/v1/exports/checks/{check_id}/release")
    def release_export(check_id: str, body: ArchiveRequest, _: ServiceCaller = Depends(caller)) -> dict[str, bool]:
        if not services.store().release_export_check(check_id, body.user_object_id):
            raise HTTPException(status_code=409, detail="This export can't be released.")
        return {"released": True}

    @app.get("/v1/exports/packs")
    def export_packs(user_object_id: str = Query(pattern=USER_ID), account_id: str | None = None,
                     _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        return {"packs": services.store().export_packs(user_object_id, account_id)}

    @app.post("/v1/exports/packs")
    def save_export_pack(body: ExportPackRequest, _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        user = body.user_object_id.lower()
        _export_account(user, body.account_id, str(uuid.uuid4()))
        if len(json.dumps(body.plan, default=str)) > MAX_PLAN_BYTES:
            raise HTTPException(status_code=413, detail="This export plan is too large.")
        store = services.store()
        if len(store.export_packs(user)) >= 50:
            raise HTTPException(status_code=409, detail="You have 50 saved export packs. Delete one first.")
        pack_id = store.save_export_pack(user=user, account_id=body.account_id, name=body.name.strip(),
                                         schedule=body.schedule, plan=body.plan)
        return {"pack": store.export_pack(pack_id, user)}

    @app.get("/v1/exports/packs/{pack_id}")
    def export_pack(pack_id: str, user_object_id: str = Query(pattern=USER_ID),
                    _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        found = services.store().export_pack(pack_id, user_object_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Export pack not found.")
        return found

    @app.delete("/v1/exports/packs/{pack_id}")
    def delete_export_pack(pack_id: str, user_object_id: str = Query(pattern=USER_ID),
                           _: ServiceCaller = Depends(caller)) -> dict[str, bool]:
        if not services.store().archive_export_pack(pack_id, user_object_id):
            raise HTTPException(status_code=404, detail="Export pack not found.")
        return {"deleted": True}

    # Factsheet design agents ------------------------------------------------------------------

    def _design_call(job: str, body: FactsheetDesignRequest, fn: Callable[..., Any]) -> dict[str, Any]:
        """One builder, checker or editor answer; the website applies and checks whatever comes back."""
        correlation_id = str(uuid.uuid4())
        if len(json.dumps(body.facts, default=str)) > MAX_DESIGN_FACTS_BYTES:
            raise HTTPException(status_code=413, detail="This design has too much to describe at once.")
        store = services.store()
        if store.tokens_used_today() >= services.runtime.daily_token_budget:
            raise HTTPException(status_code=429, detail="Today's analysis budget is used up. Try again tomorrow.")
        try:
            answer, turn = fn(services.agent(), body.facts)
        except AgentError as error:
            _record_usage(store, job, correlation_id, {}, False, error.code)
            raise HTTPException(status_code=422, detail=error.message) from error
        _record_usage(store, job, correlation_id, turn.usage, True)
        LOGGER.info("factsheet design agent answered", extra={"correlation_id": correlation_id, "job": job,
                                                               "subject_id": body.subject_id})
        return {"answer": answer, "correlation_id": correlation_id}

    @app.post("/v1/factsheet-designs/build-step")
    def factsheet_build_step(body: FactsheetDesignRequest, _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        return _design_call("factsheet_builder", body, build_step)

    @app.post("/v1/factsheet-designs/check")
    def factsheet_check(body: FactsheetDesignRequest, _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        return _design_call("factsheet_checker", body, check_design)

    @app.post("/v1/factsheet-designs/edit")
    def factsheet_edit(body: FactsheetDesignRequest, _: ServiceCaller = Depends(caller)) -> dict[str, Any]:
        return _design_call("factsheet_editor", body, edit_design)

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
