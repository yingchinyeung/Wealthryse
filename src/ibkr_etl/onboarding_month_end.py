"""Month-end factsheets for subjects that went live through the new onboarding.

The month-end package (scripts/generate_month_end_package.py) is the original
onboarding's: it needs the wizard's approved presentation identity and the
subject's own registered template files, which the new onboarding never
creates. So a subject that went live from a new-onboarding case renders each
month the way its historical reports were rendered and approved at approval 2:

* a composite with a built-in design (Alpha World MS or T) through
  ``composite_reports``, from that design's approved files;
* every other case through ``designed_reports``, from the exact factsheet
  design version approved with the historical reports. A design submitted
  after go-live does not change the monthly factsheets.

Every completed month since inception is listed; months already rendered from
the same snapshot and design are reused, so each run adds only the month that
has just ended. New factsheets wait for the owner's release, like the package's.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Mapping

from . import composite_reports, designed_reports
from .factsheet_spec import engine
from .monthly_artifacts import render_factsheet

LIVE_STAGES = ("first_month_end", "closed")


def live_cases(connection, tenant_id: str) -> list[dict]:
    """New-onboarding cases whose subject went live, one per subject (the latest)."""

    if connection.execute("SELECT to_regclass('public.broker_onboarding_cases') AS present").fetchone()[
            "present"] is None:
        return []
    rows = connection.execute(
        """
        SELECT DISTINCT ON (c.target_subject_id)
               c.case_id, c.source_kind, c.target_subject_id, c.reporting_currency, c.history_start_date,
               c.report_style, c.definition_sha256
        FROM broker_onboarding_cases c
        WHERE c.tenant_id = ? AND c.stage IN ('first_month_end', 'closed') AND c.status <> 'cancelled'
          AND c.target_subject_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM broker_onboarding_approvals a WHERE a.tenant_id = c.tenant_id
                        AND a.case_id = c.case_id AND a.approval_kind = 'go_live')
        ORDER BY c.target_subject_id, c.created_at DESC
        """,
        (tenant_id,),
    ).fetchall()
    return [{**dict(row), "case_id": str(row["case_id"]), "target_subject_id": str(row["target_subject_id"])}
            for row in rows]


def render_month_end(connection, tenant_id: str, case: Mapping[str, Any], *, container: str, today: date,
                     fetch: Callable[[str, str], bytes], store: Callable[[str, str, bytes, str], None],
                     render_style: Callable[..., Mapping[str, Any]] = render_factsheet,
                     render_design: Callable[..., Mapping[str, Any]] = engine.render) -> dict:
    """Render the live subject's completed months in its approved design."""

    if case.get("report_style"):
        return composite_reports.render_case_reports(connection, tenant_id, case, container=container, today=today,
                                                     fetch=fetch, store=store, render=render_style)
    design = designed_reports.approved_design(connection, tenant_id, str(case["case_id"]),
                                              str(case["target_subject_id"]))
    return designed_reports.render_case_reports(connection, tenant_id, case, container=container, today=today,
                                                fetch=fetch, store=store, render=render_design, design=design)


def run_live_month_end(connection, tenant_id: str, *, container: str, today: date,
                       fetch: Callable[[str, str], bytes], store: Callable[[str, str, bytes, str], None],
                       render_style: Callable[..., Mapping[str, Any]] = render_factsheet,
                       render_design: Callable[..., Mapping[str, Any]] = engine.render) -> list[dict]:
    """Every live new-onboarding subject's month-end. One subject's failure never stops the others."""

    results = []
    for case in live_cases(connection, tenant_id):
        subject_id = case["target_subject_id"]
        try:
            evidence = render_month_end(connection, tenant_id, case, container=container, today=today, fetch=fetch,
                                        store=store, render_style=render_style, render_design=render_design)
        except Exception as error:  # reported per subject; the job's result says a subject failed
            connection.rollback()
            results.append({"subject_id": subject_id, "status": "failed",
                            "reason": " ".join(str(error).split())[:400]})
            continue
        reports = evidence.get("reports") or []
        results.append({"subject_id": subject_id, "status": "processed",
                        "latest_month": reports[-1]["period_end"] if reports else None,
                        "waiting_for": evidence.get("waiting_for")})
    return results
