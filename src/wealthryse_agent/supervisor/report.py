"""The shadow report: what the supervisor would have done next to today's analyst, over a period.

Reads the supervisor rows in ``agent.tasks`` (counts, names and dates only) and says how often
the routing rules agreed with the tools the analyst actually needed, how much would have taken
the fast path, how often an answer's figures were behind the pinned date or mixed builds, how
often a figure fell back to the old calculation, and what each kind of request cost.

Answers the client analysis team gave in live mode are counted apart, in "live": how each was
led, how often the checker objected, how many specialists did not finish, and what they cost.
The event dispatcher's records are counted apart too, in "events": which jobs new data would
have woken, how long before their fixed times, and in live mode what the runs wrote.

    python -m wealthryse_agent.supervisor.report --from 2026-10-29 --to 2026-11-09
    python -m wealthryse_agent.supervisor.report --from 2026-10-29 --to 2026-11-09 --json

Run it where the agent jobs run (it signs in as the agent writer). It changes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from typing import Any, Iterable

AGREEMENTS = ("agree", "over", "under", "partial", "differ", "undecided", "no_tools")
# A Kiwoom account's subject id (ibkr_etl.kiwoom_collector.platform_account_id): its chat is counted apart,
# account by account, since Korea iStrategy is in won and U.S. iStrategy in dollars.
KIWOOM_PREFIX = "KIWOOM-"


def _percentile(values: list[int], share: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def summarise(roots: Iterable[dict[str, Any]]) -> dict[str, Any]:
    roots = list(roots)
    chat = [r for r in roots if r.get("source", "chat") != "event"]
    watched = [r for r in chat if r.get("mode", "watch") == "watch"]
    summary = _watched(watched)
    summary["kiwoom"] = _kiwoom(watched)
    summary["live"] = _live([r for r in chat if r.get("mode") == "live"])
    summary["events"] = _events([r for r in roots if r.get("source") == "event"])
    return summary


def _kiwoom(roots: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Each Kiwoom account's watched answers: how many, how many no rule placed (a question in Korean
    lands there), how many the rules matched exactly, and the figure check's warnings."""
    by_account: dict[str, dict[str, int]] = {}
    for root in roots:
        subject = str(root.get("subject_id") or "")
        if not subject.startswith(KIWOOM_PREFIX):
            continue
        detail = root.get("detail") or {}
        counts = by_account.setdefault(subject, {"requests": 0, "no_rule_matched": 0, "agree": 0,
                                                 "figure_check_warnings": 0, "rejected": 0})
        counts["requests"] += 1
        counts["no_rule_matched"] += (detail.get("route") or {}).get("lead", "router") == "router"
        counts["agree"] += detail.get("agreement") == "agree"
        counts["figure_check_warnings"] += int(detail.get("figure_warnings") or 0)
        counts["rejected"] += root.get("status") == "rejected"
    return dict(sorted(by_account.items()))


def _events(roots: list[dict[str, Any]]) -> dict[str, Any]:
    """The dispatcher's records: what new data would have woken (watch) or did wake (live)."""
    details = [r.get("detail") or {} for r in roots]
    by_job: dict[str, list[int]] = {}
    for detail in details:
        if isinstance(detail.get("minutes_before_fixed_run"), int):
            by_job.setdefault(str(detail.get("job")), []).append(detail["minutes_before_fixed_run"])
    return {
        "events": len(roots),
        "by_job": dict(Counter(str(d.get("job")) for d in details)),
        "median_minutes_before_fixed_run": {job: _percentile(v, 0.5) for job, v in sorted(by_job.items())},
        "live": dict(Counter(r.get("status") for r in roots if r.get("mode") == "live")),
        "written": sum(int(d.get("written") or 0) for d in details),
    }


def _tokens_by_lead(roots: list[dict[str, Any]]) -> dict[str, list[int]]:
    tokens: dict[str, list[int]] = {}
    for root in roots:
        detail, used = root.get("detail") or {}, root.get("usage") or {}
        if detail.get("usage_partial"):
            continue  # failed before its answer: its tokens are unknown, so it would drag the mean down
        lead = (detail.get("route") or {}).get("lead", "router")
        tokens.setdefault(lead, []).append(int(used.get("input_tokens", 0)) + int(used.get("output_tokens", 0)))
    return tokens


def _live(roots: list[dict[str, Any]]) -> dict[str, Any]:
    """The team's own answers: fast_path, planner, analyst (no usable plan) or router (out of scope)."""
    details = [r.get("detail") or {} for r in roots]
    tokens = _tokens_by_lead(roots)
    elapsed = [int(d["elapsed_ms"]) for d in details if isinstance(d.get("elapsed_ms"), int)]
    return {
        "requests": len(roots),
        "outcomes": dict(Counter(r.get("status") for r in roots)),
        "lead": dict(Counter((d.get("route") or {}).get("lead", "router") for d in details)),
        "specialists_not_finished": sum(len(set(d.get("plan") or []) - set(d.get("used_specialists") or []))
                                        for d in details),
        "checker": {"objected": sum(1 for d in details if d.get("objections")),
                    "revised": sum(1 for d in details if d.get("revised"))},
        "figure_check_warnings": sum(int(d.get("figure_warnings") or 0) for d in details),
        "with_fallback": sum(1 for d in details if d.get("fallback")),
        "behind_pin": sum(1 for d in details if d.get("behind_pin")),
        "cost": {"tokens": sum(sum(v) for v in tokens.values()),
                 "mean_tokens_by_lead": {lead: round(sum(v) / len(v)) for lead, v in tokens.items() if v}},
        "elapsed_ms": {"p50": _percentile(elapsed, 0.5), "p90": _percentile(elapsed, 0.9)},
    }


def _watched(roots: list[dict[str, Any]]) -> dict[str, Any]:
    details = [r.get("detail") or {} for r in roots]
    usage = [r.get("usage") or {} for r in roots]
    leads = Counter((d.get("route") or {}).get("lead", "router") for d in details)
    tokens_by_lead = _tokens_by_lead(roots)
    layers: Counter[str] = Counter()
    for detail in details:
        layers.update({layer for layer, n in (detail.get("layers") or {}).items() if n})
    elapsed = [int(d["elapsed_ms"]) for d in details if isinstance(d.get("elapsed_ms"), int)]
    return {
        "requests": len(roots),
        "outcomes": dict(Counter(r.get("status") for r in roots)),
        "route": {"fast_path": leads.get("fast_path", 0), "planner": leads.get("planner", 0),
                  "router": leads.get("router", 0),
                  "advice": sum(1 for d in details if (d.get("route") or {}).get("advice")),
                  "declined_as_advice": sum(1 for d in details if d.get("out_of_scope"))},
        "agreement": {kind: sum(1 for d in details if d.get("agreement") == kind) for kind in AGREEMENTS},
        "specialists_planned": dict(Counter(s for d in details for s in (d.get("route") or {}).get("specialists", []))),
        "specialists_used": dict(Counter(s for d in details for s in d.get("used_specialists", []))),
        # Requests that read each layer at least once.
        "layers": dict(layers),
        "figures": {"answers_citing_certified": sum(1 for d in details if d.get("figure_refs")),
                    "answers_citing_only_tools": sum(1 for d in details if d.get("tool_refs") and not d.get("figure_refs")),
                    "with_fallback": sum(1 for d in details if d.get("fallback")),
                    "figure_check_warnings": sum(int(d.get("figure_warnings") or 0) for d in details)},
        "pin": {"unavailable": sum(1 for d in details if (d.get("pin") or {}).get("source") == "unavailable"),
                "behind_pin": sum(1 for d in details if d.get("behind_pin")),
                "mixed_builds": sum(1 for d in details if d.get("mixed_builds"))},
        "cost": {"tokens": sum(sum(v) for v in tokens_by_lead.values()),
                 "tool_calls": sum(int(u.get("tool_calls", 0)) for u in usage),
                 "mean_tokens_by_lead": {lead: round(sum(v) / len(v)) for lead, v in tokens_by_lead.items() if v}},
        "elapsed_ms": {"p50": _percentile(elapsed, 0.5), "p90": _percentile(elapsed, 0.9)},
    }


def _share(part: int, whole: int) -> str:
    return f"{part} ({part / whole:.0%})" if whole else "0"


def render(summary: dict[str, Any], since: str, until: str) -> str:
    n = summary["requests"]
    route, agree, figures, pin = summary["route"], summary["agreement"], summary["figures"], summary["pin"]
    lines = [f"Supervisor shadow report, {since} to {until} (UTC), {n} chat answers",
             "",
             f"Routing: fast path {_share(route['fast_path'], n)}, planner {_share(route['planner'], n)}, "
             f"no rule matched {_share(route['router'], n)}; advice asked {route['advice']}, declined by the "
             f"analyst {route['declined_as_advice']}.",
             "Against what the analyst used: " + ", ".join(f"{k} {_share(v, n)}" for k, v in agree.items() if v) + ".",
             f"Figures: certified [F#] cited in {_share(figures['answers_citing_certified'], n)}, only tool results "
             f"in {_share(figures['answers_citing_only_tools'], n)}, old-calculation fallback in "
             f"{_share(figures['with_fallback'], n)}, figure-check warnings {figures['figure_check_warnings']}.",
             f"Pinning: figures behind the pinned date in {_share(pin['behind_pin'], n)}, mixed builds in "
             f"{_share(pin['mixed_builds'], n)}, pin unavailable {pin['unavailable']}.",
             "Layers read: " + (", ".join(f"{k} {_share(v, n)}" for k, v in sorted(summary["layers"].items())) or "none") + ".",
             f"Cost: {summary['cost']['tokens']} tokens, {summary['cost']['tool_calls']} tool calls; mean tokens by "
             "route (finished answers only): " + (", ".join(f"{k} {v}" for k, v in sorted(summary["cost"]["mean_tokens_by_lead"].items()))
                          or "none") + ".",
             f"Time to answer: median {summary['elapsed_ms']['p50']} ms, 90th percentile {summary['elapsed_ms']['p90']} ms."]
    for subject, counts in (summary.get("kiwoom") or {}).items():
        k = counts["requests"]
        lines.append(f"Kiwoom account ending {subject[-4:]}: {k} answers, no rule matched {_share(counts['no_rule_matched'], k)}, "
                     f"agree {_share(counts['agree'], k)}, rejected {counts['rejected']}, "
                     f"figure-check warnings {counts['figure_check_warnings']}.")
    events = summary.get("events") or {}
    if events.get("events"):
        lines += ["",
                  f"Events: new data would have woken {events['events']} job run(s): "
                  + ", ".join(f"{k} {v}" for k, v in sorted(events["by_job"].items())) + "; median minutes before "
                  "the fixed time: " + ", ".join(f"{k} {v}" for k, v in events["median_minutes_before_fixed_run"].items())
                  + "."]
        if events["live"]:
            lines.append("Runs started by events: " + ", ".join(f"{k} {v}" for k, v in sorted(events["live"].items()))
                         + f"; {events['written']} inbox card(s) written.")
    team = summary.get("live") or {}
    if team.get("requests"):
        m = team["requests"]
        lines += ["",
                  f"Live answers from the client analysis team: {m}, "
                  + ", ".join(f"{k} {v}" for k, v in sorted(team["outcomes"].items())) + ".",
                  "Led by: " + ", ".join(f"{k} {_share(v, m)}" for k, v in sorted(team["lead"].items())) + ".",
                  f"Checker objected to {_share(team['checker']['objected'], m)}, rewritten "
                  f"{team['checker']['revised']}; specialists that did not finish {team['specialists_not_finished']}; "
                  f"figure-check warnings {team['figure_check_warnings']}; behind the pinned date "
                  f"{team['behind_pin']}; old-calculation fallback in {team['with_fallback']}.",
                  "Mean tokens by lead: " + (", ".join(f"{k} {v}" for k, v in sorted(team["cost"]["mean_tokens_by_lead"].items()))
                                             or "none")
                  + f"; time to answer median {team['elapsed_ms']['p50']} ms, 90th percentile {team['elapsed_ms']['p90']} ms."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="since", required=True, type=date.fromisoformat, help="first day (UTC)")
    parser.add_argument("--to", dest="until", required=True, type=date.fromisoformat, help="day after the last (UTC)")
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON")
    args = parser.parse_args(argv)
    if args.until <= args.since:
        parser.error("--to must be after --from")
    from ..config import DatabaseSettings
    from ..database import connect
    from ..store import Store

    db = DatabaseSettings.from_env()
    store = Store(connect(db, search_path="agent"), db.tenant_id)
    if not store.tasks_ready():
        print("agent.tasks is not installed yet.", file=sys.stderr)
        return 1
    summary = summarise(store.task_roots(args.since.isoformat(), args.until.isoformat()))
    print(json.dumps(summary, indent=2) if args.json else render(summary, args.since.isoformat(), args.until.isoformat()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
