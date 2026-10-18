"""Watch-only mode: the supervisor records what it would do beside every website chat answer.

``AGENT_SUPERVISOR=watch`` turns it on; ``live`` keeps it on for everyone not named in
``AGENT_SUPERVISOR_USERS`` (live.py). Off (the default, and any other value) leaves chat
exactly as it was. Even when on, nobody sees a change: today's analyst still answers, and only
after the answer is saved does a background thread write one task tree to ``agent.tasks``:

  supervisor   the real outcome, the data it would pin, and what the analyst actually used
  lead         fast_path (one specialist), planner (several) or router (no rule placed it)
  specialist   one row per specialist the route picked, with its tool list (status 'shadow')
  checker      the answer checker, when the planner would combine several specialists

The thread never raises and never delays the answer. It does nothing until the table exists.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Any, Callable, Mapping

from ..runtime import OUT_OF_SCOPE
from . import registry
from .routing import Route, agreement, route_chat

LOGGER = logging.getLogger("wealthryse_agent.supervisor")
SETTING = "AGENT_SUPERVISOR"
MODES = ("off", "watch", "live")
PIN_TOOL = "get_data_freshness"
TABLE_RECHECK_SECONDS = 600.0
# Trees refused in a row before recording pauses until the next table check.
REFUSALS_BEFORE_PAUSE = 3


def mode(environ: Mapping[str, str] | None = None) -> str:
    """off, watch or live. Anything unrecognised is off, so a typo can never switch more on."""
    environ = os.environ if environ is None else environ
    value = str(environ.get(SETTING, "")).strip().lower()
    return value if value in MODES else "off"


def fallback_count(result: Any) -> int:
    """How many values in a tool result were served from the old calculation, not the certified one.

    Medallion's 28 Oct fallback marks them with a map from JSON pointer to "fallback", under
    ``figures_source`` at the top level of the result (or inside ``data``). Absent means none.
    """
    if not isinstance(result, dict):
        return 0
    count = 0
    for holder in (result, result.get("data")):
        marks = holder.get("figures_source") if isinstance(holder, dict) else None
        if isinstance(marks, dict):
            count += sum(1 for value in marks.values() if value == "fallback")
    return count


def _freshness(result: Any) -> dict[str, Any]:
    """The pin from a get_data_freshness result: its latest valuation date and lag, no values."""
    data = (result or {}).get("data") or {}
    return {"as_of": data.get("latest_valuation_date"), "source": "freshness",
            "lag_days": data.get("days_since_valuation")}


class Recorder:
    """A tool client that passes every call straight through and notes which tools were used.

    It never changes an argument or a result. It keeps names, counts and the freshness dates
    the analyst already read (so the pin needs no second call), never values.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.used: list[str] = []  # every call, in order
        self.ok: list[str] = []  # the calls that returned
        self.failed: list[str] = []
        self.fallback: dict[str, int] = {}
        self.freshness: dict[str, dict[str, Any]] = {}  # account id -> pin, from the analyst's own call

    def list_tools(self) -> list[dict[str, Any]]:
        return self.inner.list_tools()

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.used.append(name)
        try:
            result = self.inner.call(name, arguments)
        except Exception:
            self.failed.append(name)
            raise
        self.ok.append(name)
        try:
            flagged = fallback_count(result)
            if name == PIN_TOOL and isinstance(arguments, dict) and arguments.get("account_id"):
                self.freshness.setdefault(str(arguments["account_id"]), _freshness(result))
        except Exception:  # noting must never break the call
            flagged = 0
        if flagged:
            self.fallback[name] = self.fallback.get(name, 0) + flagged
        return result

    def close(self) -> None:
        self.inner.close()


class _TableCheck:
    """Remembers whether agent.tasks exists, so a missing table costs one query every ten minutes."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.ready: bool | None = None
        self.checked = 0.0
        self.refusals = 0
        self.lock = threading.Lock()

    def __call__(self, store: Any) -> bool:
        with self.lock:
            if self.ready or (self.ready is False and self.clock() - self.checked < TABLE_RECHECK_SECONDS):
                return bool(self.ready)
            self.ready, self.checked = bool(store.tasks_ready()), self.clock()
            if not self.ready:
                LOGGER.warning("supervisor watch-only mode is on but agent.tasks is not installed; recording nothing")
            return self.ready

    def written(self) -> None:
        with self.lock:
            self.refusals = 0

    def refused(self) -> None:
        """A tree the database refused. Several in a row pause recording until the next check."""
        with self.lock:
            self.refusals += 1
            if self.refusals >= REFUSALS_BEFORE_PAUSE:
                self.ready, self.checked, self.refusals = False, self.clock(), 0
                LOGGER.warning("supervisor task trees keep being refused; pausing recording for ten minutes")


TABLE = _TableCheck()


def pin(tools_for: Callable[[str, str], Any], user: str, account_id: str) -> dict[str, Any]:
    """The data the supervisor would pin the request to: the account's latest valuation date.

    Read through the tool server as the person, under its own correlation id so it never mixes
    with the analyst's calls. The newest published certified build joins this once Medallion's
    contracted view for it exists.
    """
    client = None
    try:
        client = tools_for(user, str(uuid.uuid4()))
        return _freshness(client.call(PIN_TOOL, {"account_id": account_id}))
    except Exception as error:
        return {"as_of": None, "source": "unavailable", "error": type(error).__name__}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _citation_facts(citations: list[dict[str, Any]]) -> dict[str, Any]:
    """Refs, dates and builds from an answer's citations; no values."""
    certified = [c for c in citations if isinstance(c, dict) and c.get("certified")]
    builds = sorted({str((c.get("figure") or {}).get("build_id")) for c in certified
                     if (c.get("figure") or {}).get("build_id")})
    dates = sorted({str(c["as_of"]) for c in citations if isinstance(c, dict) and c.get("as_of")})
    return {"tool_refs": sum(1 for c in citations if isinstance(c, dict) and not c.get("certified")),
            "figure_refs": len(certified), "build_ids": builds, "as_of_cited": dates}


def chat_tree(*, route: Route, account_id: str, correlation_id: str, conversation_id: str, outcome: str,
              turn: Any, recorder: Recorder | None, pinned: dict[str, Any], elapsed_ms: int,
              error_code: str | None = None) -> list[dict[str, Any]]:
    """The rows for one chat request, root first."""
    used = list(recorder.used) if recorder else []
    ok = list(recorder.ok) if recorder else []
    citations = list(getattr(turn, "citations", None) or [])
    facts = _citation_facts(citations)
    # Only calls that returned show what the answer needed; failed ones stay in tools_used and tools_failed.
    needed = {name for name in (registry.owner(t) for t in ok) if name}
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        if citation.get("certified"):
            # A certified figure belongs to the specialist for its website page.
            name = registry.figure_owner(str((citation.get("figure") or {}).get("figure_id") or ""))
        else:
            # Guide definitions are answered in-process; a cited one counts even if the recorder missed it.
            name = registry.owner(str(citation.get("tool") or ""))
        if name:
            needed.add(name)
    used_specialists = sorted(needed)
    pinned_as_of = pinned.get("as_of")
    cited = facts["as_of_cited"]
    root_id = str(uuid.uuid4())
    common = {"root_id": root_id, "mode": "watch", "source": "chat", "subject_id": account_id,
              "correlation_id": correlation_id, "conversation_id": conversation_id}
    status = {"answer": "done", "rejected": "rejected"}.get(outcome, "failed")
    if turn is not None:
        usage = {**(getattr(turn, "usage", None) or {}), "tool_calls": int(getattr(turn, "tool_calls", 0) or 0)}
    else:
        # A run that failed before its answer: tokens are unknown, so the report leaves it out of cost means.
        usage = {"tool_calls": len(used)}
    root = {
        **common, "task_id": root_id, "parent_id": None, "level": "supervisor", "team": None, "role": "supervisor",
        "autonomy": "answer", "status": status, "pinned_as_of": pinned_as_of, "build_ids": facts["build_ids"][:20],
        "tools_used": used[:60], "usage": usage,
        "detail": {
            "route": {"team": route.team, "lead": route.lead,
                      "specialists": list(route.specialists), "decided_by": route.decided_by,
                      "advice": route.advice},
            "used_specialists": used_specialists,
            "agreement": agreement(route.specialists, used_specialists),
            "layers": registry.layers(ok),
            "tools_failed": (recorder.failed if recorder else [])[:20],
            "fallback": dict(recorder.fallback) if recorder else {},
            "pin": {k: v for k, v in pinned.items() if k != "as_of"},
            "as_of_cited": cited[:20],
            "behind_pin": bool(pinned_as_of and cited and max(cited) < str(pinned_as_of)),
            "mixed_builds": len(facts["build_ids"]) > 1,
            "tool_refs": facts["tool_refs"], "figure_refs": facts["figure_refs"],
            "figure_warnings": len(getattr(turn, "figure_warnings", None) or []),
            "elapsed_ms": elapsed_ms,
            **({"out_of_scope": True} if getattr(turn, "answer", None) == OUT_OF_SCOPE else {}),
            **({"error_code": error_code} if error_code else {}),
            **({"usage_partial": True} if turn is None else {}),
        },
    }
    rows = [root]
    lead_id = str(uuid.uuid4())
    rows.append({**common, "task_id": lead_id, "parent_id": root_id, "level": "lead", "team": route.team,
                 "role": route.lead, "autonomy": "answer", "status": "shadow"})
    for name in route.specialists:
        member = registry.BY_NAME[name]
        allowed = list(member.tools or ())
        rows.append({**common, "task_id": str(uuid.uuid4()), "parent_id": lead_id, "level": "specialist",
                     "team": route.team, "role": name, "autonomy": member.autonomy, "status": "shadow",
                     "pinned_as_of": pinned_as_of, "tools_allowed": allowed,
                     "tools_used": [t for t in used if registry.owner(t) == name][:60],
                     "detail": {"layers": sorted({registry.TOOL_LAYERS[t] for t in allowed})}})
    if len(route.specialists) > 1:
        rows.append({**common, "task_id": str(uuid.uuid4()), "parent_id": lead_id, "level": "checker",
                     "team": route.team, "role": "answer_checker", "autonomy": "answer", "status": "shadow"})
    return rows


def record_chat(store: Any, tools_for: Callable[[str, str], Any], *, user: str, account_id: str, question: str,
                correlation_id: str, conversation_id: str, outcome: str, turn: Any = None,
                recorder: Recorder | None = None, elapsed_ms: int = 0, error_code: str | None = None,
                table: Callable[[Any], bool] = TABLE) -> bool:
    """Write the tree for one chat request. Never raises; returns whether a tree was written."""
    try:
        if not table(store):
            return False
        # The analyst usually read freshness already; only ask again when it didn't.
        pinned = (recorder.freshness.get(str(account_id)) if recorder else None) or pin(tools_for, user, account_id)
        rows = chat_tree(route=route_chat(question), account_id=account_id, correlation_id=correlation_id,
                         conversation_id=conversation_id, outcome=outcome, turn=turn, recorder=recorder,
                         pinned=pinned, elapsed_ms=elapsed_ms, error_code=error_code)
        if store.add_tasks(rows):
            getattr(table, "written", lambda: None)()
            return True
        getattr(table, "refused", lambda: None)()
        LOGGER.warning("supervisor task tree refused", extra={"correlation_id": correlation_id})
    except Exception:
        LOGGER.warning("supervisor watch failed", exc_info=True, extra={"correlation_id": correlation_id})
    return False


def watch_chat(**kwargs: Any) -> threading.Thread:
    """record_chat on a daemon thread, so the answer is never held up."""
    thread = threading.Thread(target=record_chat, kwargs=kwargs, daemon=True, name="supervisor-watch")
    thread.start()
    return thread
