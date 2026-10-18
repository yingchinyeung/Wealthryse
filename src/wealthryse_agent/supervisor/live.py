"""Live mode: the client analysis team answers website chat, for the people named in a setting.

``AGENT_SUPERVISOR=live`` with ``AGENT_SUPERVISOR_USERS`` (Entra object ids, comma-separated, or
``*`` for everyone) sends those people's chat through the team. Everyone else keeps today's analyst,
with watch-only recording. ``AGENT_SUPERVISOR=watch`` or ``off`` is the undo.

For each question the supervisor:
  1. asks the router model whether it is in scope, as today;
  2. routes it by rules: one specialist takes the fast path and answers straight away with only
     its own tools; otherwise the planner model splits it into parts, one per specialist;
  3. runs the specialists in parallel, each with a share of the request's budget, then writes one
     answer from their findings. Every figure in it is checked against the specialists' tool
     results exactly as today's answers are;
  4. has the answer checker, which sees only the question, the answer and its cited results, object
     to anything unsupported; one objection sends the answer back for one rewrite;
  5. falls back to today's analyst when the planner gives no usable plan.

After the answer is saved, one task tree goes to ``agent.tasks`` (mode 'live'): ids, names, counts
and usage, never the question, the answer or a figure.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Callable, Iterator, Mapping, Sequence

from .. import calculator, guide_tools
from ..config import REASONING_EFFORTS
from ..runtime import OUT_OF_SCOPE, UNVERIFIED, AgentError, Turn, _add_usage
from ..tool_client import ToolCallFailed
from . import prompts, registry, watch
from .routing import Route, agreement, route_chat

LOGGER = logging.getLogger("wealthryse_agent.supervisor")
USERS_SETTING = "AGENT_SUPERVISOR_USERS"
MAX_PARTS = 4
# How much of each cited result the writer and the checker read, so one long series can't crowd out the rest.
RESULT_CHARS = 4000
CHECKER_CHARS = 60000
LABELS = {"holdings_performance": "holdings and performance", "risk": "risk", "modelling": "modelling",
          "metrics_guide": "the Metrics guide"}
NOTHING_FINISHED = ("I couldn't finish checking the data for that question, so I haven't shown an answer. "
                    "Try asking about one thing at a time.")
_CITED = re.compile(r"\[([TF]\d+)\]")
# The least time left for the answer checker (and the one rewrite it may ask for) to be worth starting.
CHECK_SECONDS = 45
# Planning is a short classification and checking a reading task: neither needs the chat's full reasoning.
PLANNER_EFFORT, CHECKER_EFFORT = "low", "medium"


def _at_most(effort: str, cap: str) -> str:
    """The chat's reasoning effort, but no more than ``cap``."""
    order = REASONING_EFFORTS
    return effort if effort in order and order.index(effort) <= order.index(cap) else cap


def live_for(user: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether this person's chat goes through the client analysis team."""
    environ = os.environ if environ is None else environ
    if watch.mode(environ) != "live":
        return False
    named = {part.strip().lower() for part in str(environ.get(USERS_SETTING, "")).split(",") if part.strip()}
    return "*" in named or str(user or "").lower() in named


def _usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0}


def _add(into: dict[str, int], turn: Turn | Mapping[str, int] | None) -> None:
    """Add a run's tokens and tool calls (a Turn, or a usage dict already counted) to ``into``."""
    if turn is None:
        return
    usage = turn.usage if isinstance(turn, Turn) else turn
    for key in ("input_tokens", "output_tokens"):
        into[key] += int(usage.get(key, 0) or 0)
    into["tool_calls"] += int(turn.tool_calls if isinstance(turn, Turn) else usage.get("tool_calls", 0) or 0)


@dataclass
class Step:
    """One specialist's run, as the task record keeps it: names, counts and status only."""
    name: str
    status: str = "failed"  # done, rejected or failed
    error_code: str | None = None
    turn: Turn | None = None
    recorder: watch.Recorder | None = None
    elapsed_ms: int = 0
    # Its own tokens and tool calls, taken before the final answer's turn is given the request's total.
    usage: dict[str, int] = field(default_factory=_usage)


class _NoTools:
    """The writer's tool client: it may only calculate on the findings, which the runtime does itself."""

    def list_tools(self) -> list[dict[str, Any]]:
        return []

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ToolCallFailed("unknown_tool", "That tool is not available here.")

    def close(self) -> None:
        pass


def renumber(value: Any, mapping: Mapping[str, str]) -> Any:
    """A copy of a tool result or figure with its own refs (``ref``, ``tool_call``) renumbered."""
    if isinstance(value, dict):
        return {k: (mapping.get(v, v) if k in ("ref", "tool_call") and isinstance(v, str) else renumber(v, mapping))
                for k, v in value.items()}
    if isinstance(value, list):
        return [renumber(v, mapping) for v in value]
    return value


def merge(steps: Sequence[Step]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, str]]:
    """The finished specialists' results and figures under one numbering, and each one's findings
    with its citations renumbered to match."""
    results: dict[str, Any] = {}
    figures: dict[str, dict[str, Any]] = {}
    findings: dict[str, str] = {}
    for step in steps:
        turn = step.turn
        if turn is None:
            continue
        mapping: dict[str, str] = {}
        for old in turn.results:
            mapping[old] = f"T{len(results) + len(mapping) + 1}"
        offset = len(figures)
        for index, old in enumerate(turn.figures, start=1):
            mapping[old] = f"F{offset + index}"
        for old, result in turn.results.items():
            results[mapping[old]] = renumber(result, mapping)
        for old, figure in turn.figures.items():
            figures[mapping[old]] = renumber(figure, mapping)
        findings[step.name] = _CITED.sub(lambda m: f"[{mapping.get(m.group(1), m.group(1))}]", turn.answer)
    return results, figures, findings


def _cited_results(text: str, results: Mapping[str, Any], figures: Mapping[str, Any], limit: int) -> str:
    """The results and figures a text cites, as compact JSON lines, each cut to ``limit`` characters."""
    refs = list(dict.fromkeys(_CITED.findall(text)))
    lines = []
    for ref in refs:
        item = results.get(ref) if ref.startswith("T") else figures.get(ref)
        if item is not None:
            lines.append(f"{ref}: {json.dumps(item, default=str, separators=(',', ':'))[:limit]}")
    return "\n".join(lines)


class ClientTeam:
    """One website chat question answered by the client analysis team.

    ``tools_for(user, correlation_id)`` opens a tool client acting for the person; each specialist
    gets its own, under the request's correlation id.
    """

    def __init__(self, agent: Any, tools_for: Callable[[str, str], Any], *, user: str, account_id: str,
                 correlation_id: str, analyst_instructions: str, memory_block: str = "",
                 today: str | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.agent = agent
        self.tools_for = tools_for
        self.user = user
        self.account_id = account_id
        self.correlation_id = correlation_id
        self.analyst_instructions = analyst_instructions
        self.memory_block = memory_block
        self.today = today or date.today().isoformat()
        self.clock = clock
        self.route: Route | None = None
        # fast_path, planner, analyst (the planner gave no usable plan) or router (out of scope)
        self.lead = "fast_path"
        self.plan: list[tuple[str, str]] = []
        self.steps: list[Step] = []
        self.lead_usage = _usage()  # the router, planner and writer calls, and today's analyst as fallback
        self.checker: dict[str, Any] = {}
        self.out_of_scope = False
        self.analyst: watch.Recorder | None = None  # today's analyst's tool client, when it answered instead
        # The whole request shares the chat's time limit, so it ends inside the website's request timeout.
        self.deadline = self.clock() + float(agent.settings.timeout_seconds)

    # --- helpers -------------------------------------------------------------------------------

    def _left(self) -> float:
        return self.deadline - self.clock()

    def _agent(self, *, share: int = 1, writer: bool = False) -> Any:
        """A runtime with this request's model client, a share of its tool calls and at most the time left."""
        settings = self.agent.settings
        calls, seconds = settings.max_tool_calls, settings.timeout_seconds
        if writer:
            calls, seconds = max(2, calls // 4), seconds // 3
        elif share > 1:
            calls, seconds = max(3, calls // share), seconds // 2
        seconds = max(5, min(seconds, int(self._left())))
        if (calls, seconds) == (settings.max_tool_calls, settings.timeout_seconds):
            return self.agent
        return type(self.agent)(replace(settings, max_tool_calls=calls, timeout_seconds=seconds),
                                model_client=self.agent.client, clock=self.agent.clock)

    def _client(self) -> watch.Recorder:
        return watch.Recorder(guide_tools.GuideTools(self.tools_for(self.user, self.correlation_id)))

    def _instructions(self, name: str, *, writes_answer: bool) -> str:
        text = prompts.specialist(name, account_id=self.account_id, today=self.today, writes_answer=writes_answer)
        return text + (self.memory_block if writes_answer else "")

    def recorders(self) -> list[watch.Recorder]:
        """Every tool client the request used: the specialists', or today's analyst's."""
        return [s.recorder for s in self.steps if s.recorder] + ([self.analyst] if self.analyst else [])

    def total(self) -> dict[str, int]:
        """Tokens and tool calls for the whole request so far."""
        total = _usage()
        _add(total, self.lead_usage)
        _add(total, self.checker.get("usage"))
        for step in self.steps:
            _add(total, step.usage)
        return total

    def _final(self, event: dict[str, Any]) -> dict[str, Any]:
        """The final event, its turn carrying the whole request's usage (every run already counted)."""
        turn: Turn = event["turn"]
        total = self.total()
        turn.usage = {"input_tokens": total["input_tokens"], "output_tokens": total["output_tokens"]}
        turn.tool_calls = total["tool_calls"]
        return event

    # --- the request ---------------------------------------------------------------------------

    def answer(self, question: str, history: Sequence[Mapping[str, str]] = ()) -> Iterator[dict[str, Any]]:
        self.deadline = self.clock() + float(self.agent.settings.timeout_seconds)
        router = Turn()
        in_scope = self.agent.in_scope(question, router)
        _add(self.lead_usage, router)
        if not in_scope:
            self.out_of_scope, self.lead = True, "router"
            turn = Turn(answer=OUT_OF_SCOPE)
            yield self._final({"type": "answer", "answer": OUT_OF_SCOPE, "citations": [], "turn": turn})
            return
        self.route = route_chat(question)
        if self.route.fast_path:
            self.plan = [(self.route.specialists[0], question)]
        else:
            self.lead = "planner"
            yield {"type": "status", "text": "Planning the answer"}
            self.plan = self._plan(question, history)
            if not self.plan:
                self.lead = "analyst"
                yield from self._analyst(question, history)
                return
        if len(self.plan) == 1:
            yield from self._fast(self.plan[0][0], question, history)
            return
        yield {"type": "status", "text": "Asking specialists: " + ", ".join(LABELS[name] for name, _ in self.plan)}
        self._parallel()
        done = [s for s in self.steps if s.status == "done"]
        if not done:
            turn = Turn(answer=NOTHING_FINISHED, status="rejected")
            yield self._final({"type": "rejected", "answer": NOTHING_FINISHED, "reason": "no specialist finished",
                               "turn": turn})
            return
        yield {"type": "status", "text": "Writing the answer"}
        yield from self._write(question, history, done)

    def _plan(self, question: str, history: Sequence[Mapping[str, str]]) -> list[tuple[str, str]]:
        """The planner's parts, at most one per specialist; empty when it gave nothing usable."""
        suggested = list(self.route.specialists) if self.route else []
        conversation = [{"role": m["role"], "content": str(m["content"])[:2000]}
                        for m in list(history)[-6:] if m.get("role") in ("user", "assistant")]
        conversation.append({"role": "user", "content": question})
        settings = self.agent.settings
        try:
            response = self.agent._create(
                model=settings.deployment, store=False, input=conversation,
                instructions=prompts.planner(account_id=self.account_id, today=self.today, suggested=suggested),
                reasoning={"effort": _at_most(settings.reasoning_effort, PLANNER_EFFORT)}, max_output_tokens=4000,
                text={"format": {"type": "json_schema", "name": "plan", "strict": True,
                                 "schema": prompts.PLAN_SCHEMA}})
            planned = Turn()
            _add_usage(planned, response)
            _add(self.lead_usage, planned)
            parts = json.loads(response.output_text)["parts"]
        except AgentError:
            raise
        except Exception:
            LOGGER.warning("supervisor planner gave no usable plan", extra={"correlation_id": self.correlation_id})
            return []
        plan: list[tuple[str, str]] = []
        for part in parts if isinstance(parts, list) else []:
            name = part.get("specialist") if isinstance(part, dict) else None
            text = str(part.get("question") or "").strip()[:1000] if isinstance(part, dict) else ""
            if name in registry.CLIENT_SPECIALISTS and text and name not in {n for n, _ in plan}:
                plan.append((name, text))
        return plan[:MAX_PARTS]

    def _run(self, step: Step, run_kwargs: dict[str, Any], agent: Any) -> Iterator[dict[str, Any]]:
        """One specialist's run, yielding its events and filling in ``step``."""
        started = self.clock()
        step.recorder = self._client()
        try:
            for event in agent.run(tools=step.recorder, account_id=self.account_id, skip_router=True,
                                   allowed_tools=list(registry.BY_NAME[step.name].tools or ()), **run_kwargs):
                if event["type"] in ("answer", "rejected"):
                    step.turn = event["turn"]
                    step.status = "done" if event["type"] == "answer" else "rejected"
                    _add(step.usage, step.turn)
                yield event
        except AgentError as error:
            step.status, step.error_code = "failed", error.code
            raise
        finally:
            step.elapsed_ms = int((self.clock() - started) * 1000)
            try:
                step.recorder.close()
            except Exception:
                pass

    def _fast(self, name: str, question: str, history: Sequence[Mapping[str, str]]) -> Iterator[dict[str, Any]]:
        step = Step(name)
        self.steps.append(step)
        for event in self._run(step, {"question": question, "history": history,
                                      "instructions": self._instructions(name, writes_answer=True)}, self._agent()):
            yield self._final(event) if event["type"] in ("answer", "rejected") else event

    def _analyst(self, question: str, history: Sequence[Mapping[str, str]]) -> Iterator[dict[str, Any]]:
        """Today's analyst, with every tool, when the planner gave no usable plan."""
        client = self.analyst = self._client()
        try:
            for event in self._agent().run(question=question, account_id=self.account_id, tools=client,
                                           history=history, instructions=self.analyst_instructions,
                                           skip_router=True):
                if event["type"] in ("answer", "rejected"):
                    _add(self.lead_usage, event["turn"])
                    yield self._final(event)
                else:
                    yield event
        finally:
            client.close()

    def _parallel(self) -> None:
        share = len(self.plan)
        self.steps = [Step(name) for name, _ in self.plan]

        def work(step: Step, part: str) -> None:
            try:
                for _ in self._run(step, {"question": part,
                                          "instructions": self._instructions(step.name, writes_answer=False)},
                                   self._agent(share=share)):
                    pass
            except AgentError:
                pass  # recorded on the step
            except Exception:
                step.status, step.error_code = "failed", "internal_error"
                LOGGER.warning("supervisor specialist failed", exc_info=True,
                               extra={"correlation_id": self.correlation_id})

        with ThreadPoolExecutor(max_workers=share, thread_name_prefix="specialist") as pool:
            for future in [pool.submit(work, step, part) for step, (_, part) in zip(self.steps, self.plan)]:
                future.result()

    def _write(self, question: str, history: Sequence[Mapping[str, str]],
               done: Sequence[Step]) -> Iterator[dict[str, Any]]:
        results, figures, findings = merge(done)
        cited = {name: text + "\n\nCited results:\n" + _cited_results(text, results, figures, RESULT_CHARS)
                 for name, text in findings.items()}
        failed = [s.name for s in self.steps if s.status != "done"]
        instructions = prompts.writer(account_id=self.account_id, today=self.today, findings=cited,
                                      failed=failed) + self.memory_block
        first = self._compose(question, history, instructions, results, figures)
        if first["type"] != "answer":
            yield self._final(first)
            return
        if self._left() < CHECK_SECONDS:
            # Too little time left to check and rewrite: the figure check has passed, as for today's answers.
            self.checker = {"status": "skipped", "objections": 0, "usage": _usage()}
            yield self._final(first)
            return
        objections = self._check(question, first["turn"], results, figures)
        if objections:
            yield {"type": "status", "text": "Double-checking the answer"}
            retry = self._compose(question, history, instructions + "\n\nA checker read your first draft:\n\n"
                                  + first["turn"].answer + "\n\nand objected:\n" + "\n".join(f"- {o}" for o in objections)
                                  + "\n\nWrite the answer again, fixing these and keeping everything else.",
                                  results, figures)
            self.checker["revised"] = retry["type"] == "answer"
            if retry["type"] == "answer":
                yield self._final(retry)
                return
        yield self._final(first)

    def _compose(self, question: str, history: Sequence[Mapping[str, str]], instructions: str,
                 results: Mapping[str, Any], figures: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        final: dict[str, Any] = {}
        for event in self._agent(writer=True).run(
                question=question, account_id=self.account_id, tools=_NoTools(), history=history,
                allowed_tools=[calculator.NAME], instructions=instructions, skip_router=True,
                evidence=(results, figures)):
            if event["type"] in ("answer", "rejected"):
                final = event
                _add(self.lead_usage, event["turn"])
        return final or {"type": "rejected", "answer": UNVERIFIED, "reason": "no answer", "turn": Turn(answer=UNVERIFIED)}

    def _check(self, question: str, turn: Turn, results: Mapping[str, Any],
               figures: Mapping[str, Mapping[str, Any]]) -> list[str]:
        """The cold checker's objections. A checker that fails objects to nothing; the figure check stands."""
        payload = ("Question:\n" + question + "\n\nAnswer:\n" + turn.answer + "\n\nCited results:\n"
                   + _cited_results(turn.answer, results, figures, RESULT_CHARS))[:CHECKER_CHARS]
        settings = self.agent.settings
        check = Turn()
        try:
            response = self.agent._create(
                model=settings.deployment, store=False, input=payload,
                instructions=prompts.CHECKER.format(account_id=self.account_id),
                reasoning={"effort": _at_most(settings.reasoning_effort, CHECKER_EFFORT)}, max_output_tokens=6000,
                text={"format": {"type": "json_schema", "name": "objections", "strict": True,
                                 "schema": prompts.CHECKER_SCHEMA}})
            _add_usage(check, response)
            objections = [str(o).strip()[:300] for o in json.loads(response.output_text)["objections"]
                          if str(o).strip()][:5]
            self.checker = {"status": "done", "objections": len(objections), "usage": dict(check.usage)}
        except Exception as error:
            self.checker = {"status": "failed", "error": type(error).__name__, "objections": 0,
                            "usage": dict(check.usage)}
            objections = []
        return objections

    # --- the task record -----------------------------------------------------------------------

    def tree(self, *, outcome: str, turn: Any, pinned: Mapping[str, Any], conversation_id: str,
             elapsed_ms: int, error_code: str | None = None) -> list[dict[str, Any]]:
        """The rows for this request, root first: what the team did, in ids, names and counts."""
        route = self.route or Route(team="client_analysis", specialists=(), decided_by="router")
        used = [t for r in self.recorders() for t in r.used]
        ok = [t for r in self.recorders() for t in r.ok]
        citations = list(getattr(turn, "citations", None) or [])
        facts = watch._citation_facts(citations)
        cited = facts["as_of_cited"]
        pinned_as_of = pinned.get("as_of")
        planned = [name for name, _ in self.plan]
        root_id = str(uuid.uuid4())
        common = {"root_id": root_id, "mode": "live", "source": "chat", "subject_id": self.account_id,
                  "correlation_id": self.correlation_id, "conversation_id": conversation_id}
        status = {"answer": "done", "rejected": "rejected"}.get(outcome, "failed")
        usage = ({**(getattr(turn, "usage", None) or {}), "tool_calls": int(getattr(turn, "tool_calls", 0) or 0)}
                 if turn is not None else {"tool_calls": len(used)})
        fallback: dict[str, int] = {}
        for recorder in self.recorders():
            for name, count in recorder.fallback.items():
                fallback[name] = fallback.get(name, 0) + count
        root = {
            **common, "task_id": root_id, "parent_id": None, "level": "supervisor", "team": None,
            "role": "supervisor", "autonomy": "answer", "status": status, "pinned_as_of": pinned_as_of,
            "build_ids": facts["build_ids"][:20], "tools_used": used[:60], "usage": usage,
            "detail": {
                "route": {"team": route.team, "lead": self.lead, "specialists": list(route.specialists),
                          "decided_by": route.decided_by, "advice": route.advice},
                "plan": planned,
                "used_specialists": sorted({s.name for s in self.steps if s.status == "done"}),
                "agreement": agreement(route.specialists, planned) if self.lead != "analyst" else "undecided",
                "layers": registry.layers(ok),
                "tools_failed": [t for r in self.recorders() for t in r.failed][:20],
                "fallback": fallback,
                "pin": {k: v for k, v in pinned.items() if k != "as_of"},
                "as_of_cited": cited[:20],
                "behind_pin": bool(pinned_as_of and cited and max(cited) < str(pinned_as_of)),
                "mixed_builds": len(facts["build_ids"]) > 1,
                "tool_refs": facts["tool_refs"], "figure_refs": facts["figure_refs"],
                "figure_warnings": len(getattr(turn, "figure_warnings", None) or []),
                "elapsed_ms": elapsed_ms,
                **({"out_of_scope": True} if self.out_of_scope else {}),
                **({"objections": int(self.checker.get("objections") or 0),
                    "revised": bool(self.checker.get("revised"))}
                   if self.checker.get("status") in ("done", "failed") else {}),
                **({"checker_skipped": True} if self.checker.get("status") == "skipped" else {}),
                **({"error_code": error_code} if error_code else {}),
                **({"usage_partial": True} if turn is None else {}),
            },
        }
        rows = [root]
        lead_id = str(uuid.uuid4())
        rows.append({**common, "task_id": lead_id, "parent_id": root_id, "level": "lead", "team": route.team,
                     "role": self.lead, "autonomy": "answer", "status": status, "usage": dict(self.lead_usage)})
        for step in self.steps:
            member = registry.BY_NAME[step.name]
            allowed = list(member.tools or ())
            rows.append({
                **common, "task_id": str(uuid.uuid4()), "parent_id": lead_id, "level": "specialist",
                "team": route.team, "role": step.name, "autonomy": member.autonomy, "status": step.status,
                "pinned_as_of": pinned_as_of, "tools_allowed": allowed,
                "tools_used": (step.recorder.used if step.recorder else [])[:60],
                "usage": dict(step.usage),
                "detail": {"layers": registry.layers(step.recorder.ok if step.recorder else []),
                           "elapsed_ms": step.elapsed_ms,
                           **({"error_code": step.error_code} if step.error_code else {})}})
        if self.checker.get("status") in ("done", "failed"):
            rows.append({**common, "task_id": str(uuid.uuid4()), "parent_id": lead_id, "level": "checker",
                         "team": route.team, "role": "answer_checker", "autonomy": "answer",
                         "status": "done" if self.checker.get("status") == "done" else "failed",
                         "usage": dict(self.checker.get("usage") or {}),
                         "detail": {"objections": int(self.checker.get("objections") or 0),
                                    **({"revised": bool(self.checker["revised"])} if "revised" in self.checker else {}),
                                    **({"error": self.checker["error"]} if self.checker.get("error") else {})}})
        return rows


def record(store: Any, team: ClientTeam, *, outcome: str, turn: Any = None, conversation_id: str,
           elapsed_ms: int = 0, error_code: str | None = None,
           table: Callable[[Any], bool] = watch.TABLE) -> bool:
    """Write the team's tree for one request. Never raises; returns whether a tree was written."""
    try:
        if not table(store):
            return False
        pinned = next((r.freshness[team.account_id] for r in team.recorders() if team.account_id in r.freshness),
                      None)
        if pinned is None:
            pinned = watch.pin(team.tools_for, team.user, team.account_id)
        rows = team.tree(outcome=outcome, turn=turn, pinned=pinned, conversation_id=conversation_id,
                         elapsed_ms=elapsed_ms, error_code=error_code)
        if store.add_tasks(rows):
            getattr(table, "written", lambda: None)()
            return True
        getattr(table, "refused", lambda: None)()
        LOGGER.warning("supervisor task tree refused", extra={"correlation_id": team.correlation_id})
    except Exception:
        LOGGER.warning("supervisor record failed", exc_info=True, extra={"correlation_id": team.correlation_id})
    return False


def record_later(**kwargs: Any) -> threading.Thread:
    """record on a daemon thread, so the answer is never held up."""
    thread = threading.Thread(target=record, kwargs=kwargs, daemon=True, name="supervisor-record")
    thread.start()
    return thread
