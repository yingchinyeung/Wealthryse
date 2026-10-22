"""Rehearse the monthly reports on certified figures, read-only, on a restored copy (certified
figures, "After month-end" item 2; docs/MEDALLION_CERTIFIED_FIGURES.md, "Monthly reports and
Attribution on certified figures").

For one month and every subject the month-end job reports on (active subjects registered for
month_end_reporting, and any subject with a frozen snapshot for that month end), it makes the
month-end payload exactly as the freeze does (reporting_snapshot.build_month_end_payload) three
times on the same read-only database: with MONTHLY_REPORTS_CERTIFIED off (today's code), fallback
and on.

Subjects that went live through the new onboarding (onboarding_month_end.live_cases) make their
month-end through that path instead (composite_reports or designed_reports render_case_reports,
which freeze the month with the case's own arguments). Each such subject whose month the path
would freeze (a completed month after its inception, by composite_reports.report_months, with no
frozen or approved snapshot yet and its data in) is rehearsed the same way, with exactly the
arguments that path passes: a composite with a report style, its report profile's inception and
reporting currency; any other case, the case's history start date and reporting currency. A
case whose report profile or approved design that path refuses today is counted as not made, by
error type. A subject the month-end job's list already rehearses with the same arguments is not
made twice.

It shows that fallback gives exactly today's payload (the same sha256 the freeze stores) and
counts, field by field, how the report's figures compare with the certified ones: equal, close
(within the certified tolerance), different or missing.

It writes nothing. The session is read-only (SET SESSION CHARACTERISTICS AS TRANSACTION ...
READ ONLY, so any write would fail), every subject's work is rolled back, no reporting_snapshots
row is inserted, nothing is uploaded and no report file is rendered (a rendered report is made
from the frozen payload, so the same payload makes the same report). The summary on stdout has
counts and field ids only; the full comparison, with values and subject ids, goes to the private
file named by --out, which must be outside the repository (written with mode 0600).

    DATABASE_URL=postgresql://... python scripts/compare_monthly_report_figures.py \\
        --month 2026-09 --out /private/folder/monthly-reports-2026-09.json [--subject ID ...]

Exit code 0 when every report that today's code can make came out identical with the setting
off and fallback; 1 otherwise.
"""
from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ibkr_etl import certified_reports  # noqa: E402
from ibkr_etl.util import canonical_json, sha256_text  # noqa: E402

MODES = ("off", "fallback", "on")
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
# Quiet loggers whose lines name subjects (the agent tools' audit line).
QUIET_LOGGERS = ("uvicorn.error.ibkr_agent",)

SUBJECTS_SQL = """
SELECT s.subject_id, s.history_start_date, s.reporting_currency
FROM platform_subjects s
WHERE s.tenant_id = ? AND s.lifecycle_status = 'active'
  AND (EXISTS (SELECT 1 FROM subject_operating_registrations r
               WHERE r.tenant_id = s.tenant_id AND r.subject_id = s.subject_id
                 AND r.capability = 'month_end_reporting'
                 AND r.operating_state = 'active' AND r.effective_to IS NULL)
       OR EXISTS (SELECT 1 FROM reporting_snapshots f
                  WHERE f.tenant_id = s.tenant_id AND f.subject_id = s.subject_id
                    AND f.period_end = ? AND f.status IN ('frozen', 'approved')))
ORDER BY s.subject_id
"""
STORED_SQL = """
SELECT snapshot_id, period_start, reporting_currency, data_sha256, status
FROM reporting_snapshots
WHERE tenant_id = ? AND subject_id = ? AND period_end = ? AND status IN ('frozen', 'approved')
ORDER BY CASE WHEN status = 'approved' THEN 0 ELSE 1 END, created_at DESC
LIMIT 1
"""


class RehearsalError(RuntimeError):
    pass


def month_end(month: str) -> str:
    try:
        first = date.fromisoformat(f"{month}-01")
    except ValueError:
        raise RehearsalError("--month must be YYYY-MM") from None
    return first.replace(day=calendar.monthrange(first.year, first.month)[1]).isoformat()


def private_file(path: str) -> Path:
    """The full comparison's file: never inside the repository checkout."""
    target = Path(path).expanduser().resolve()
    if target == ROOT or ROOT in target.parents:
        raise RehearsalError("--out must be outside the repository checkout")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return target


def write_private(target: Path, text: str) -> None:
    os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
    os.chmod(target, 0o600)
    target.write_text(text, encoding="utf-8")


def _day(value: Any) -> str | None:
    return None if value in (None, "") else str(value)[:10]


def subjects(connection, *, tenant_id: str, period_end: str, only: Iterable[str] = ()) -> list[dict]:
    wanted = set(only)
    rows = connection.execute(SUBJECTS_SQL, (tenant_id, period_end)).fetchall()
    return [{"subject_id": str(row["subject_id"]), "history_start_date": _day(row["history_start_date"]),
             "reporting_currency": str(row["reporting_currency"] or "")}
            for row in rows if not wanted or str(row["subject_id"]) in wanted]


def _month_end_job_arguments(connection, *, tenant_id: str, subject: dict, period_end: str) -> tuple:
    """The freeze's arguments as the month-end job's rehearsal takes them: the stored snapshot's,
    else the subject's history start and reporting currency. Returns (stored row, start, currency)."""
    stored = connection.execute(STORED_SQL, (tenant_id, subject["subject_id"], period_end)).fetchone()
    period_start = _day(stored["period_start"]) if stored else subject["history_start_date"]
    currency = str(stored["reporting_currency"]) if stored else subject["reporting_currency"]
    return stored, period_start, currency


def _onboarding_path(connection, *, tenant_id: str, case: dict) -> tuple[str, str, str]:
    """What onboarding_month_end.render_month_end checks before it freezes any month, raising as
    it does, and the arguments it then freezes with: (design, period_start, reporting_currency)."""
    from ibkr_etl import composite_reports, designed_reports
    from ibkr_etl.report_profiles import ReportProfileError

    subject_id = str(case["target_subject_id"])
    if case.get("report_style"):  # composite_reports.render_case_reports
        style = str(case["report_style"])
        try:
            profile = composite_reports.load_profile(connection, subject_id, tenant_id=tenant_id)
        except ReportProfileError as error:
            raise composite_reports.CompositeReportError(str(error)) from error
        if profile is None or profile.built_in or profile.style != style:
            raise composite_reports.CompositeReportError(f"{subject_id} has no {style} report profile")
        if profile.definition_sha256 != str(case["definition_sha256"]):
            raise composite_reports.CompositeReportError("The report profile is not bound to this case's approved "
                                                         "definition")
        composite_reports.design_assets(connection, style)
        return "composite", profile.inception, profile.reporting_currency
    # designed_reports.render_case_reports, in the design approved with the historical reports
    design = designed_reports.approved_design(connection, tenant_id, str(case["case_id"]), subject_id)
    currency = str(case["reporting_currency"])
    if currency not in tuple(design["spec"].get("currencies") or ()):
        raise designed_reports.DesignedReportError(f"The approved design does not report in {currency}")
    return "designed", date.fromisoformat(str(case["history_start_date"])[:10]).isoformat(), currency


def onboarding_subjects(connection, *, tenant_id: str, period_end: str, only: Iterable[str] = (),
                        listed: Iterable[dict] = ()) -> tuple[list[dict], dict]:
    """The live new-onboarding subjects whose month the onboarding path would freeze, each with
    exactly the arguments that path passes (onboarding_month_end.render_month_end), and counts of
    the others by reason. ``listed`` are the month-end job's subjects, to make each report once."""
    from ibkr_etl import composite_reports, designed_reports, onboarding_month_end
    from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id

    wanted, end = set(only), date.fromisoformat(period_end)
    by_subject = {subject["subject_id"]: subject for subject in listed}
    found: list[dict] = []
    skipped: Counter = Counter()
    live = [case for case in onboarding_month_end.live_cases(connection, tenant_id)
            if not wanted or str(case["target_subject_id"]) in wanted]
    for case in live:
        subject_id = str(case["target_subject_id"])
        entry: dict[str, Any] = {"subject_id": subject_id, "source": "onboarding", "case_id": str(case["case_id"]),
                                 "design": "composite" if case.get("report_style") else "designed"}
        try:
            entry["design"], inception, currency = _onboarding_path(connection, tenant_id=tenant_id, case=case)
        except Exception as error:  # noqa: BLE001 - that path fails the same way today; counted by type
            connection.rollback()
            found.append({**entry, "error": {"type": type(error).__name__, "message": str(error)[:500]}})
            continue
        # The path makes every completed month after inception up to its "today", oldest first; here
        # "today" is the day after the rehearsal's month end, so that month is the latest completed one.
        months = composite_reports.report_months(date.fromisoformat(str(inception)[:10]), end + timedelta(days=1))
        if not months or months[-1] != period_end:
            skipped["no_completed_month"] += 1
            continue
        reason = None
        for month in months:
            if active_month_end_snapshot_id(connection, tenant_id=tenant_id, subject_id=subject_id,
                                            period_end=month) is not None:
                if month == period_end:
                    reason = "frozen_already"  # the path reuses it; the month-end job's list rehearses it
                continue
            ready = (composite_reports.month_end_ready(connection, subject_id, month) if entry["design"] == "composite"
                     else designed_reports.month_ready(connection, str(case["source_kind"]), subject_id, month))
            if not ready:
                reason = "waiting_for_data"  # the path stops at the first month whose data are not in
                break
        if reason:
            skipped[reason] += 1
            continue
        if subject_id in by_subject:
            _, start, listed_currency = _month_end_job_arguments(connection, tenant_id=tenant_id,
                                                                 subject=by_subject[subject_id], period_end=period_end)
            if (start, listed_currency) == (inception, currency):
                skipped["same_as_month_end_job"] += 1
                continue
        found.append({**entry, "period_start": inception, "reporting_currency": currency})
    connection.rollback()
    return found, {"live": len(live), "rehearsed": sum("error" not in item for item in found),
                   "profile_errors": sum("error" in item for item in found), **dict(sorted(skipped.items()))}


def rehearse_subject(connection, *, tenant_id: str, subject: dict, period_end: str,
                     build: Callable | None = None) -> dict:
    """One subject's report three ways, rolled back. Returns the full record (with values).

    A live new-onboarding subject (source "onboarding") is made with the arguments its path
    passes, which it carries; any other with the stored snapshot's or the subject's."""
    if build is None:
        from ibkr_etl.reporting_snapshot import build_month_end_payload as build
    subject_id = subject["subject_id"]
    source = subject.get("source", "month_end_job")
    if source == "onboarding":
        stored = connection.execute(STORED_SQL, (tenant_id, subject_id, period_end)).fetchone()
        period_start, currency = subject.get("period_start"), subject.get("reporting_currency")
    else:
        stored, period_start, currency = _month_end_job_arguments(connection, tenant_id=tenant_id, subject=subject,
                                                                   period_end=period_end)
    record: dict[str, Any] = {"subject_id": subject_id, "source": source, "period_start": period_start,
                              "reporting_currency": currency, "digests": {}, "errors": {}, "log_lines": {}}
    if source == "onboarding":
        record.update(case_id=subject.get("case_id"), design=subject.get("design"))
    if subject.get("error"):  # the onboarding path cannot make it today either
        record["errors"]["off"] = subject["error"]
    for selected in MODES if not subject.get("error") else ():
        lines: list[str] = []
        try:
            payload, quality, allowed, _, comparison = build(
                connection, tenant_id=tenant_id, subject_id=subject_id, period_start=period_start,
                period_end=period_end, reporting_currency=currency,
                environ={certified_reports.SETTING: selected}, out=lines.append)
        except Exception as error:  # noqa: BLE001 - counted by type; the message stays in the private file
            record["errors"][selected] = {"type": type(error).__name__, "message": str(error)[:500]}
            connection.rollback()
            if selected == "off":
                break
            continue
        finally:
            if lines:
                record["log_lines"][selected] = lines
        record["digests"][selected] = sha256_text(canonical_json(payload))
        if selected == "off":
            record.update(data_as_of=payload.get("data_as_of"), quality_status=quality, publication_allowed=allowed,
                          freeze_would_reject=quality == "fail"
                          or not (payload.get("monthly_reporting") or {}).get("available"))
        elif selected == "fallback" and comparison is not None:
            record["comparison"] = comparison
        elif selected == "on" and comparison is not None:
            record["on_used"] = comparison.get("used", 0)
    connection.rollback()
    digests = record["digests"]
    record["made_today"] = "off" in digests
    record["identical_fallback"] = "off" in digests and digests.get("fallback") == digests["off"]
    record["identical_on"] = "off" in digests and digests.get("on") == digests["off"]
    record["stored_snapshot"] = None if stored is None else {
        "snapshot_id": str(stored["snapshot_id"]), "status": str(stored["status"]),
        "same_as_off": digests.get("off") == str(stored["data_sha256"])}
    return record


def summarize(records: list[dict]) -> dict:
    made = [record for record in records if record["made_today"]]
    statuses = Counter(record.get("comparison", {}).get("status", "none") for record in made)
    by_field: dict[tuple, Counter] = defaultdict(Counter)
    written: dict[tuple, bool] = {}
    for record in made:
        for entry in record.get("comparison", {}).get("fields", []):
            key = (entry["field_id"], entry["figure_id"])
            by_field[key][entry["status"]] += 1
            written[key] = entry["write"]
    totals = Counter()
    for counts in by_field.values():
        totals.update(counts)
    errors = Counter(error["type"] for record in records for error in record["errors"].values())
    stored = [record["stored_snapshot"] for record in made if record["stored_snapshot"]]
    onboarding = [record for record in records if record.get("source") == "onboarding"]
    return {
        "subjects": len(records), "made": len(made), "not_made_today": len(records) - len(made),
        "onboarding": {"made": sum(record["made_today"] for record in onboarding),
                       "identical_fallback": sum(record["identical_fallback"] for record in onboarding)},
        "failed_only_with_setting": sum("fallback" in record["errors"] for record in made),
        "identical_fallback": sum(record["identical_fallback"] for record in made),
        "identical_on": sum(record["identical_on"] for record in made),
        "stored_same": sum(item["same_as_off"] for item in stored), "stored": len(stored),
        "comparison_status": dict(sorted(statuses.items())),
        "fields": sum(totals.values()), **{name: totals.get(name, 0) for name in certified_reports.STATUSES},
        "by_field": [{"field_id": field_id, "figure_id": figure_id, "written_by_on": written[(field_id, figure_id)],
                      **{name: counts.get(name, 0) for name in certified_reports.STATUSES}}
                     for (field_id, figure_id), counts in sorted(by_field.items())],
        "errors": dict(sorted(errors.items())),
    }


def print_summary(summary: dict, *, month: str, period_end: str, out_path: Path,
                  out: Callable[[str], None] = print) -> None:
    """Counts and field ids only: no subject, value or date of a holding."""
    made = summary["made"]
    out(f"Monthly reports on certified figures, rehearsal for {month} (month end {period_end}). Nothing was written.")
    out(f"Subjects: {summary['subjects']}; made with today's code: {made}; could not be made today either: "
        f"{summary['not_made_today']}; failed only with the setting: {summary['failed_only_with_setting']}.")
    live = summary.get("onboarding_cases")
    if live is not None:
        onboarding = summary["onboarding"]
        out(f"Live new-onboarding subjects (onboarding_month_end): {live['live']}; made with that path's arguments: "
            f"{onboarding['made']} (identical off and fallback: {onboarding['identical_fallback']}); its report "
            f"profile or design fails today: {live['profile_errors']}; not made by that path for this month: "
            f"already frozen {live.get('frozen_already', 0)}, waiting for data {live.get('waiting_for_data', 0)}, "
            f"no completed month {live.get('no_completed_month', 0)}; the same as the month-end job's: "
            f"{live.get('same_as_month_end_job', 0)}.")
    out(f"Identical with MONTHLY_REPORTS_CERTIFIED off and fallback: {summary['identical_fallback']} of {made}.")
    out(f"Identical with off and on (certified values written): {summary['identical_on']} of {made}.")
    out(f"Today's payload equals the stored frozen snapshot: {summary['stored_same']} of {summary['stored']} "
        f"subjects that have one.")
    out("Certified comparison per report: " + (", ".join(f"{name} {count}" for name, count
                                                          in summary["comparison_status"].items()) or "none"))
    out(f"Fields compared: {summary['fields']}: equal {summary['equal']}, close {summary['close']}, "
        f"different {summary['different']}, missing {summary['missing']}.")
    for written, title in ((True, "Written by on (fallback keeps today's unless equal)"),
                           (False, "Compared only (another basis; never written)")):
        rows = [row for row in summary["by_field"] if row["written_by_on"] is written]
        if rows:
            out(f"{title}: equal / close / different / missing")
            for row in rows:
                out(f"  {row['field_id']} = {row['figure_id']}: {row['equal']} / {row['close']} / "
                    f"{row['different']} / {row['missing']}")
    if summary["errors"]:
        out("Errors by type: " + ", ".join(f"{name} {count}" for name, count in summary["errors"].items()))
    out(f"Full comparison (values and subject ids, private): {out_path}")


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Make each month-end report three ways, read-only, and compare.")
    parser.add_argument("--month", required=True, help="The report month, YYYY-MM (for example 2026-09).")
    parser.add_argument("--out", required=True, help="Private file for the full comparison, outside the repository.")
    parser.add_argument("--subject", action="append", default=[], help="Only this subject (repeatable).")
    return parser.parse_args(argv)


def _connect(database_url: str, tenant_id: str):
    from ibkr_etl.db import connect

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    # Read-only for the whole session: any write fails instead of happening.
    connection.execute("SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    connection.commit()
    return connection


def main(argv: list[str] | None = None, *, connect: Callable | None = None,
         out: Callable[[str], None] = print) -> int:
    args = _arguments(argv)
    period_end = month_end(args.month)
    target = private_file(args.out)
    database_url = os.environ.get("DATABASE_URL", "")
    if connect is None and not database_url.startswith(("postgres://", "postgresql://")):
        raise RehearsalError("DATABASE_URL must be a PostgreSQL connection URL (the restored copy)")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    connection = (connect or _connect)(database_url, tenant_id)
    try:
        found = subjects(connection, tenant_id=tenant_id, period_end=period_end, only=args.subject)
        connection.rollback()
        live, live_counts = onboarding_subjects(connection, tenant_id=tenant_id, period_end=period_end,
                                                only=args.subject, listed=found)
        records = [rehearse_subject(connection, tenant_id=tenant_id, subject=subject, period_end=period_end)
                   for subject in [*found, *live]]
    finally:
        connection.rollback()
        connection.close()
    summary = summarize(records)
    summary["onboarding_cases"] = live_counts
    write_private(target, json.dumps({"month": args.month, "period_end": period_end, "summary": summary,
                                      "subjects": records}, indent=1, sort_keys=True, default=str))
    print_summary(summary, month=args.month, period_end=period_end, out_path=target, out=out)
    identical = summary["identical_fallback"] == summary["made"] and not summary["failed_only_with_setting"]
    return 0 if identical else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RehearsalError as error:
        print(f"Not run: {error}", file=sys.stderr)
        raise SystemExit(2)
