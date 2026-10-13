"""Stage 2 of a portfolio's onboarding case: a dry run of its calculation.

The daily self-service derived refresh calls run_portfolio_dry_runs for every
filtered or composite portfolio case waiting on its stage 2 checks. The real
calculation runs on the case's exact definition, its stored result is measured,
and then every write it made is rolled back, so nothing is kept for a portfolio
the owner has not approved yet. The two checks it records decide whether the
owner is asked for approval 1.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Mapping

from .onboarding_v2_web import OnboardingError, record_platform_check
from .subject_onboarding import validate_typed_runtime_contract

# A portfolio's first value may fall a few days after its start date when the
# start is a weekend or holiday; anything later means a sleeve starts late.
FIRST_VALUE_GRACE_DAYS = 7
# The latest value should be recent; older means a sleeve or price stopped.
STALE_AFTER_DAYS = 10


class _DiscardingConnection:
    """The job's connection, except that nothing the calculation does is committed."""

    backend = "postgresql"

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, parameters: Any = None) -> Any:
        return self._connection.execute(sql, parameters)

    def commit(self) -> None:
        """Held back: the dry run's writes are rolled back when it ends."""

    def rollback(self) -> None:
        self._connection.rollback()


def pending_portfolio_cases(connection: Any, tenant_id: str) -> list[dict]:
    """Portfolio cases in stage 2 whose checks have not both passed."""

    rows = connection.execute(
        """
        SELECT c.case_id, c.source_kind, c.target_subject_id, c.definition_json, c.definition_sha256,
               c.history_start_date
        FROM broker_onboarding_cases c
        WHERE c.tenant_id = ? AND c.source_kind IN ('filtered_portfolio', 'composite_portfolio')
          AND c.stage = 'load_and_reconcile' AND c.status IN ('platform_working', 'blocked')
          AND (SELECT COUNT(*) FROM broker_onboarding_checks k
               WHERE k.tenant_id = c.tenant_id AND k.case_id = c.case_id AND k.status = 'pass'
                 AND k.check_id IN ('sleeves_cover_history', 'calculation_dry_run')) < 2
        ORDER BY c.created_at
        """,
        (tenant_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _measure(connection: Any, subject_id: str, currency: str) -> dict:
    row = connection.execute(
        """
        SELECT MIN(snapshot_date) AS first_date, MAX(snapshot_date) AS latest_date,
               COUNT(DISTINCT snapshot_date) AS observations, MIN(total_nav) AS lowest_nav
        FROM derived_portfolio_nav WHERE portfolio_id = ? AND currency = ?
        """,
        (subject_id, currency),
    ).fetchone()
    latest = connection.execute(
        "SELECT total_nav FROM derived_portfolio_nav WHERE portfolio_id = ? AND currency = ? "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (subject_id, currency),
    ).fetchone()
    return {
        "first_date": str(row["first_date"]) if row and row["first_date"] else None,
        "latest_date": str(row["latest_date"]) if row and row["latest_date"] else None,
        "observations": int(row["observations"] or 0) if row else 0,
        "lowest_nav": float(row["lowest_nav"]) if row and row["lowest_nav"] is not None else None,
        "latest_nav": float(latest["total_nav"]) if latest and latest["total_nav"] is not None else None,
    }


def evaluate(measured: Mapping[str, Any], *, start: date, currency: str, today: date
             ) -> dict[str, tuple[str, str]]:
    """The two stage 2 checks, as (status, message), from what the dry run stored."""

    first, latest = measured.get("first_date"), measured.get("latest_date")
    if not first or not latest or not measured.get("observations"):
        return {
            "sleeves_cover_history": ("fail", "The dry run produced no values; a sleeve or price has no history "
                                              f"from {start.isoformat()}."),
            "calculation_dry_run": ("fail", "The dry run produced no values."),
        }
    first_day, latest_day = date.fromisoformat(first[:10]), date.fromisoformat(latest[:10])
    if (first_day - start).days > FIRST_VALUE_GRACE_DAYS:
        cover = ("fail", f"The first value is on {first_day.isoformat()}, not near {start.isoformat()}: a sleeve "
                         "or price series starts later. Choose a later start date.")
    else:
        cover = ("pass", f"Every sleeve and price covers {first_day.isoformat()} to {latest_day.isoformat()}.")
    count, lowest, nav = int(measured["observations"]), measured.get("lowest_nav"), measured.get("latest_nav")
    summary = f"{count} daily values from {first_day.isoformat()} to {latest_day.isoformat()}"
    if lowest is None or lowest <= 0:
        run = ("fail", f"{summary}, but the value falls to {lowest or 0:,.2f} {currency}; it must stay positive.")
    elif (today - latest_day).days > STALE_AFTER_DAYS:
        run = ("warn", f"{summary}. The latest value is from {latest_day.isoformat()}; a sleeve or price has "
                       "stopped updating.")
    else:
        run = ("pass", f"{summary}, positive throughout; {nav:,.2f} {currency} on {latest_day.isoformat()}.")
    return {"sleeves_cover_history": cover, "calculation_dry_run": run}


def dry_run_case(connection: Any, tenant_id: str, case: Mapping[str, Any], *,
                 calculate: Callable[[Any, dict], Mapping[str, Any]], today: date) -> dict:
    """Run one case's calculation, roll it back, and record its stage 2 checks."""

    subject_id = str(case["target_subject_id"])
    error, measured = None, {}
    try:
        definition = validate_typed_runtime_contract(subject_id, str(case["source_kind"]), case["definition_json"],
                                                     str(case["definition_sha256"]))
    except ValueError as failure:
        definition, error = None, f"the stored definition is not valid: {failure}"
    if definition is not None:
        currency = str(definition["base_currency"]).upper()
        start = date.fromisoformat(str(definition.get("history_start_date") or definition["normalization_date"]))
        connection.commit()
        try:
            calculate(_DiscardingConnection(connection), definition)
            measured = _measure(connection, subject_id, currency)
        except Exception as failure:  # the case shows the reason; the job goes on to the next case
            error = " ".join(str(failure).split())[:400] or type(failure).__name__
        finally:
            connection.rollback()
    if error:
        results = {
            "sleeves_cover_history": ("fail", "Not measured, because the dry run failed."),
            "calculation_dry_run": ("fail", f"The calculation failed: {error}"),
        }
    else:
        results = evaluate(measured, start=start, currency=currency, today=today)
    outcome = {}
    for check_id, (status, message) in results.items():
        outcome = record_platform_check(connection, tenant_id, str(case["case_id"]), check_id, status, message,
                                        {"dry_run": measured, "error": error,
                                         "definition_sha256": case["definition_sha256"]})
    connection.commit()
    return {"case_id": str(case["case_id"]), "subject_id": subject_id,
            "checks": {key: value[0] for key, value in results.items()}, "status": outcome.get("status")}


def run_portfolio_dry_runs(connection: Any, tenant_id: str, *,
                           calculate: Callable[[Any, dict], Mapping[str, Any]], today: date) -> list[dict]:
    """Dry-run every waiting portfolio case. One case's failure never stops the others."""

    results = []
    for case in pending_portfolio_cases(connection, tenant_id):
        try:
            results.append(dry_run_case(connection, tenant_id, case, calculate=calculate, today=today))
        except (OnboardingError, ValueError) as error:
            connection.rollback()
            results.append({"case_id": str(case["case_id"]), "subject_id": str(case["target_subject_id"]),
                            "error": str(error)[:400]})
    return results
