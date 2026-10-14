"""The connection agent: the first check of a new IBKR login, and what it found.

When the owner adds an IBKR login, nothing contacts IBKR until a check runs.
Each hour the onboarding job:

  - queues the first check of every login never checked (the same one-attempt
    request as the "Check now" button; the Flex discovery job runs it);
  - reports, once per check, what it found: the accounts the login sees, or
    the exact problem and what to do about it;
  - reports a check that has waited too long to run, which means the
    discovery job is not running.

It never retries: IBKR locks a token after repeated failures, so every check
after the first is the owner's. It is plain code and calls no model. Its
reports go to agent.connection_reports, which the platform posts to the
administrator's system updates.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

LOGGER = logging.getLogger("wealthryse_agent.connection_watch")
STUCK_AFTER = timedelta(minutes=30)  # the discovery job runs every 5 minutes

_AGAIN = "press Check now next to the login on the Account onboarding page."
_CHECK_NOW = _AGAIN[0].upper() + _AGAIN[1:]

# IBKR Flex Web Service error codes the discovery job records.
_IBKR = {
    "1003": ("IBKR had no statement for the dates asked", ["Wait a day, then " + _AGAIN]),
    "1004": ("IBKR's statement was incomplete", ["Wait an hour, then " + _AGAIN]),
    "1009": ("IBKR's servers were too busy", ["Wait an hour, then " + _AGAIN]),
    "1010": ("the query is a legacy Flex query, which IBKR no longer serves",
             ["Create the query again from the recipe on the Account onboarding page.",
              "Replace the login with the new query ID."]),
    "1011": ("IBKR says the account's Flex service is inactive",
             ["Sign in to IBKR Client Portal and turn on the Flex Web Service.", "Then " + _AGAIN]),
    "1012": ("the token has expired",
             ["Generate a new Flex Web Service token in IBKR Client Portal.", "Replace the login with it."]),
    "1013": ("IBKR refused the platform's address: the token is IP-locked to a different address",
             ["In IBKR Client Portal, set the token's IP restriction to the platform address shown on the "
              "Account onboarding page, or remove it.", "Then " + _AGAIN]),
    "1014": ("IBKR says the query ID is not valid for this token",
             ["Check the query ID in IBKR Client Portal under Flex Queries.", "Replace the login with the right ID."]),
    "1015": ("IBKR says the token is not valid",
             ["Copy the token again from IBKR Client Portal; it may have been regenerated.", "Replace the login with it."]),
    "1016": ("IBKR says the account is in an invalid state (for example closed or restricted)",
             ["Check the account's status in IBKR Client Portal."]),
    "1018": ("IBKR received too many requests for this token",
             ["Wait at least an hour before checking again."]),
    "1020": ("IBKR could not validate the request",
             ["Check that the token and query ID belong to the same IBKR user.", "Then " + _AGAIN]),
}
_PLATFORM = {
    "credential_operation_active": ("another check of the same token was running at the time",
                                    ["Wait a few minutes, then " + _AGAIN]),
    "credential_used_too_recently": ("the token had been used seconds before", [_CHECK_NOW]),
    "credential_cooling_down": ("the token is cooling down after an earlier failure",
                                ["Wait 30 minutes, then " + _AGAIN]),
    "credential_manual_reset_required": (
        "the token is held after IBKR refused it, so the platform will not try it again",
        ["Fix the token in IBKR Client Portal (or generate a new one) and replace the login.",
         "Then ask in the project for the hold to be cleared; no page clears it yet."]),
    "source_not_validatable": ("the login was paused or retired before the check ran", []),
    "invalid_discovered_account_count": (
        "the statement IBKR returned had no accounts in it",
        ["In IBKR Client Portal, open the Flex query and select the accounts it should cover.",
         "Then " + _AGAIN]),
}


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def problem(code: str | None, phase: str | None) -> tuple[str, list[str]]:
    """The failure in plain words, and the owner's steps."""
    code = str(code or "").strip()
    if code in _IBKR:
        words, steps = _IBKR[code]
        return f"{words} (IBKR error {code})", list(steps)
    if code in _PLATFORM:
        words, steps = _PLATFORM[code]
        return words, list(steps)
    if code in ("1001", "1005", "1006", "1007", "1008", "1019", "1021"):
        return (f"IBKR was still preparing the statement when the check gave up (IBKR error {code})",
                ["Wait an hour, then " + _AGAIN])
    if code == "flex_error" and phase in ("send_request", "get_statement"):
        return ("the platform could not get an answer from IBKR's Flex service",
                ["Check IBKR's system status, then " + _AGAIN])
    if code.startswith("Flex token") or code.startswith("Missing required environment variable"):
        return ("the discovery job could not read the token from Key Vault",
                ["Check the Flex discovery job's access to the Flex token vault in Azure."])
    detail = f" (code {code})" if code else ""
    return (f"the check failed in its {phase or 'unknown'} step{detail}",
            ["Look at the Flex discovery job's runs (ibkrv2stg-flex-discovery) in Azure."])


def recipe_gaps(recipe: Any, account_count: int) -> tuple[str, list[str]]:
    """What the Flex query lacks against the recipe, in words, and the owner's steps; empty when nothing."""
    if not isinstance(recipe, Mapping):
        return "", []
    problems = [p for p in recipe.get("problems") or [] if isinstance(p, Mapping)]
    must = [p for p in problems if p.get("status") == "fail"]
    may = [p for p in problems if p.get("status") == "warn"]
    if not must and not may:
        return "", []
    parts = []
    for item in must + may:
        accounts = list(item.get("accounts") or [])
        where = f" (for {', '.join(map(str, accounts))})" if accounts and len(accounts) < account_count else ""
        parts.append(f"{item.get('label')}: {item.get('message')}{where}")
    lead = "Must fix: " if must else "Worth checking: "
    text = lead + " ".join(f"{n}. {part}" for n, part in enumerate(parts[:8], 1))
    steps = ["In IBKR Client Portal open Performance & Reports, then Flex Queries, and edit this login's query."]
    steps += [f"{p.get('label')}: {p.get('message')}" for p in (must + may)[:5]]
    steps.append("Save, then " + _AGAIN)
    return text, steps


def findings(login: Mapping[str, Any], now: datetime) -> list[dict[str, Any]]:
    """The reports this login warrants now. Each key makes a report appear once."""
    name = login["display_name"]
    request = login.get("latest_request_id")
    status = login.get("latest_status")
    if not request:
        return []
    out = []
    if status == "succeeded":
        count = login.get("latest_account_count") or login.get("accounts_found") or 0
        accounts = f"{count} account{'s' if count != 1 else ''}"
        gaps, steps = recipe_gaps(login.get("latest_recipe"), count)
        if gaps:
            out.append({"key": f"{request}:done", "state": "found_accounts",
                        "title": f"{name}: IBKR login works, but its Flex query needs changes",
                        "body": (f"The check of {name} succeeded and IBKR's statement covered {accounts}, but the "
                                 f"Flex query does not match the platform's recipe. {gaps} Until it is fixed, "
                                 "onboarding those accounts will stop at these gaps."),
                        "owner_steps": steps})
        else:
            out.append({"key": f"{request}:done", "state": "found_accounts",
                        "title": f"{name}: IBKR login works and sees {accounts}",
                        "body": (f"The check of {name} succeeded. IBKR's statement covered {accounts}; they are "
                                 "listed under Available on the Account onboarding page, ready to start a case."),
                        "owner_steps": []})
    elif status in ("failed", "blocked"):
        words, steps = problem(login.get("latest_error_code"), login.get("latest_error_phase"))
        held = " The platform will not use this token again until an owner resets it." if (
            login.get("requires_manual_reset")) else ""
        out.append({"key": f"{request}:{status}", "state": status,
                    "title": f"{name}: IBKR login check failed",
                    "body": f"The check of {name} failed because {words}.{held}",
                    "owner_steps": steps})
    elif status in ("pending", "running"):
        created = _when(login.get("latest_created_at"))
        if created and now - created >= STUCK_AFTER:
            minutes = int((now - created).total_seconds() // 60)
            out.append({"key": f"{request}:stuck", "state": "stuck",
                        "title": f"{name}: the check has not run",
                        "body": (f"The check of {name} has been {status} for {minutes} minutes. The Flex "
                                 "discovery job (ibkrv2stg-flex-discovery) runs every 5 minutes, so it is probably "
                                 "stopped or failing."),
                        "owner_steps": ["Look at the Flex discovery job's runs (ibkrv2stg-flex-discovery) in Azure."]})
    return out


def eligible(login: Mapping[str, Any], now: datetime) -> bool:
    """Whether the agent may queue this login's first check (the database checks again)."""
    blocked = _when(login.get("blocked_until"))
    return (not login.get("checks_requested") and login.get("source_status") == "pending_validation"
            and login.get("credential_status") not in ("suspended", "expired", "revoked")
            and login.get("connection_status") not in ("suspended", "retired")
            and not login.get("requires_manual_reset") and not (blocked and blocked > now))


def watch_connections(store: Any, call: Callable[[str, dict], dict], run_id: int | None,
                      now: datetime | None = None) -> int:
    """Queue first checks and record new reports; returns how many reports were written."""
    now = now or datetime.now(timezone.utc)
    written = 0
    for login in call("list_broker_logins", {})["data"]["logins"]:
        reports = findings(login, now)
        if eligible(login, now):
            request = store.queue_first_connection_check(login["flex_source_id"])
            if request:
                LOGGER.info("queued the first check of login %s", login["connection_id"])
                reports.append({"key": f"{request}:queued", "state": "queued",
                                "title": f"{login['display_name']}: checking the new IBKR login",
                                "body": (f"{login['display_name']} has not been checked yet, so I queued its first "
                                         "check. The Flex discovery job runs it within a few minutes; I will say "
                                         "what it finds."),
                                "owner_steps": [], "request": request})
        for report in reports:
            if store.add_connection_report(connection_id=login["connection_id"],
                                           flex_source_id=login["flex_source_id"],
                                           validation_request_id=report.pop("request", None)
                                           or login.get("latest_request_id"),
                                           run_id=run_id, **report):
                written += 1
    return written
