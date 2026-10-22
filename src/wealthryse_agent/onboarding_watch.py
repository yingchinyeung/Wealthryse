"""The go-live coordinator's first-month watch.

After Ying types GO LIVE, a case waits in its first month-end stage until that
month is released. Each hour the onboarding job looks at every such account
and posts an inbox card, once, for each thing worth knowing:

  - its first daily data has arrived and the daily checks pass (the handover
    from onboarding to daily running);
  - no daily data has arrived for five days;
  - the daily assurance checks failed;
  - the month-end team does not have access to it yet, with the command that
    gives it (the grant is the owner's step, not the agent's);
  - its first month-end is released, so the case is done.

It is plain code: it reads dates and statuses through the tool server's
``list_live_onboarding_cases`` and calls no model.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable, Mapping

LOGGER = logging.getLogger("wealthryse_agent.onboarding_watch")
KIND = "onboarding_watch"
STALE_DAYS = 5  # a long weekend is not a gap


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def findings(case: Mapping[str, Any], today: date) -> list[tuple[str, str, str]]:
    """(key, title, body) for each card this case warrants today. The key makes each card appear once."""
    name, subject = case["display_name"], case["subject_id"]
    live = _date(case.get("went_live_at"))
    latest = _date(case.get("latest_data_date"))
    assured = _date(case.get("latest_assurance_date"))
    status = case.get("latest_assurance_status")
    cards = []
    if case.get("first_month_end_status") == "pass":
        cards.append(("handed_over", f"{name}: first month-end released",
                      f"{name}'s first month-end is released, so its onboarding is complete. From now on it runs "
                      "like every other account."))
        return cards
    if live and latest and latest >= live and assured and assured >= live and status in ("pass", "warn"):
        cards.append(("first_day", f"{name} is running daily",
                      f"{name}'s first daily data after go-live arrived (data to {latest}), and the daily "
                      f"checks on {assured} came back {'clean' if status == 'pass' else 'with warnings only'}."))
    if live and (today - live).days >= STALE_DAYS and (latest is None or (today - latest).days >= STALE_DAYS):
        since = f"since {latest}" if latest else "since it went live"
        cards.append((f"stale:{latest or 'none'}", f"{name}: no daily data {since}",
                      f"No daily data has arrived for {name} ({subject}) {since}. Check its broker connection "
                      "and the daily import on the System health page."))
    if status == "fail" and assured:
        cards.append((f"assurance_fail:{assured}", f"{name}: daily checks failed on {assured}",
                      f"The daily assurance checks for {name} failed on {assured}. The findings are on the "
                      "System health page."))
    if live and not case.get("month_team_has_access"):
        cards.append(("month_team_access", f"{name}: give the month-end team access",
                      f"The month-end team cannot check {name}'s first close until it has access. To give it, "
                      "run scripts/install_agent_schema.py with --existing-logins and "
                      f"--grant month_team={subject} (your step; the agents cannot grant themselves access)."))
    return cards


def watch_live_cases(store: Any, call: Callable[[str, dict], dict], run_id: int | None,
                     today: date | None = None) -> int:
    """Post each new card for every live first-month case; returns how many were posted."""
    today = today or date.today()
    posted = 0
    for case in call("list_live_onboarding_cases", {})["data"]["cases"]:
        for key, title, body in findings(case, today):
            if store.add_inbox(kind=KIND, account_id=case["subject_id"], period_key=f"{case['case_id']}:{key}",
                               title=title, body=body,
                               citations=[{"source": "broker_onboarding_cases", "case_id": case["case_id"]}],
                               run_id=run_id):
                posted += 1
                LOGGER.info("posted %s for case %s", key, case["case_id"])
    return posted
