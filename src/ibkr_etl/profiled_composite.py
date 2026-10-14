"""Composites that report in the Alpha World MS or T design.

A fixed-unit composite whose owner approved a report profile (see
report_profiles) is measured as MS and T are: HKD NAV from its own inception,
the inception-to-date average 1M HIBOR fixing as the risk-free rate, daily
returns only between consecutive sessions of its design's markets, and the same
bootstrap and drawdown simulations. MS and T keep their own modules and
approved contracts; this module reuses their arithmetic for any other composite,
bound to its own approved definition instead of the MS or T reference.

Nothing here writes, fetches or approves anything.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping

from .alpha_world_ms_analytics import PATHS, _analyze, _simulate
from .hibor import BASIS, load_period_average, period_average
from .market_calendar import CALENDAR_VERSION, CALENDARS
from .report_profiles import (
    STYLES, ReportProfile, ReportProfileError, _sha256, built_in_profile, profile_from_metadata,
)
from .subject_onboarding import validate_typed_runtime_contract
from .util import canonical_json

ANALYTICS_VERSION = "alpha-world-design-composite-hkd-hibor-v1"
RUNTIME_VERSION = "alpha-world-design-composite-hkd-risk-runtime-v1"
INTEREST_BASIS = "actual-source-nav-only-no-additional-charge"
CORE_UNAVAILABLE = {
    "benchmark_comparison": "separate-HKD-comparison-qualification-required",
    "security_tail_attribution": "not-qualified-for-fixed-unit-composite",
}
MESSAGE = ("HKD NAV metrics use inception-to-date average 1M HIBOR. One-year simulations use the documented "
           "moving-block bootstrap. Benchmark comparison and security-level tail attribution remain unavailable.")


@dataclass(frozen=True)
class Contract:
    """A composite's approved definition and the report profile bound to it."""

    profile: ReportProfile
    definition: Mapping[str, Any]
    definition_sha256: str

    @property
    def subject_id(self) -> str:
        return self.profile.subject_id


def _rows(connection, statement: str, params: tuple) -> list[dict]:
    return [dict(row) for row in connection.execute(statement, params).fetchall()]


def requested(subject_id: str, subject_type: Any, metadata: Any) -> bool:
    """True when a subject row asks for the Alpha World design; MS and T never do."""

    if built_in_profile(subject_id) is not None or subject_type != "composite_portfolio":
        return False
    if isinstance(metadata, str):
        metadata = json.loads(metadata or "{}")
    return isinstance(metadata, Mapping) and bool(metadata.get("report_profile"))


def is_profiled(connection, subject_id: str, *, tenant_id: str | None = None) -> bool:
    """True for a composite other than MS and T whose subject carries a report profile."""

    if built_in_profile(subject_id) is not None:
        return False
    clause, params = ("tenant_id = ? AND ", (tenant_id,)) if tenant_id else ("", ())
    rows = _rows(connection, "SELECT subject_type, metadata_json FROM platform_subjects WHERE "
                 + clause + "subject_id = ?", params + (subject_id,))
    return len(rows) == 1 and requested(subject_id, rows[0]["subject_type"], rows[0]["metadata_json"])


def require_contract(connection, subject_id: str, *, tenant_id: str | None = None) -> Contract:
    """The approved HKD definition and the profile bound to it, or ValueError."""

    if built_in_profile(subject_id) is not None:
        raise ValueError("Alpha World MS and T use their own approved contracts")
    if not tenant_id:
        raise ValueError("Composite stored-input qualification requires an explicit tenant")
    clause, params = "s.tenant_id = ? AND ", (tenant_id,)
    rows = _rows(connection, """SELECT s.subject_type, s.base_currency, s.reporting_currency,
        s.history_start_date, s.metadata_json, p.base_currency AS derived_currency,
        d.configuration_json, d.configuration_sha256
        FROM platform_subjects s
        LEFT JOIN derived_portfolios p ON p.tenant_id = s.tenant_id AND p.portfolio_id = s.subject_id
        LEFT JOIN subject_calculation_definitions d ON d.tenant_id = s.tenant_id AND d.subject_id = s.subject_id
            AND d.approval_status = 'approved' AND d.effective_to IS NULL
        WHERE """ + clause + "s.subject_id = ?", params + (subject_id,))
    if len(rows) != 1:
        raise ValueError(f"{subject_id} requires exactly one approved current HKD contract")
    row = rows[0]
    if row["configuration_json"] is None or row["configuration_sha256"] is None:
        raise ValueError(f"{subject_id} has no approved current calculation definition")
    try:
        profile = profile_from_metadata(subject_id, row["metadata_json"],
                                        definition_sha256=str(row["configuration_sha256"]))
    except ReportProfileError as error:
        raise ValueError(str(error)) from error
    if profile is None:
        raise ValueError(f"{subject_id} has no approved report profile")
    if (row["subject_type"] != "composite_portfolio"
            or any(row[field] != profile.reporting_currency
                   for field in ("base_currency", "reporting_currency", "derived_currency"))
            or str(row["history_start_date"]) != profile.inception):
        raise ValueError(f"{subject_id} HKD metadata is not ready; results are withheld")
    definition = validate_typed_runtime_contract(subject_id, "composite_portfolio", row["configuration_json"],
                                                 row["configuration_sha256"])
    if (definition["base_currency"] != profile.reporting_currency
            or definition["normalization_date"] != profile.inception):
        raise ValueError(f"{subject_id} definition differs from its report profile")
    return Contract(profile=profile, definition=definition, definition_sha256=str(row["configuration_sha256"]))


def methodology_identity(profile: ReportProfile) -> dict[str, Any]:
    return {
        "subject_id": profile.subject_id,
        "report_style": profile.style,
        "analytics_version": ANALYTICS_VERSION,
        "contract_sha256": profile.definition_sha256,
        "profile_sha256": _sha256(profile.as_dict()),
        "reporting_currency": profile.reporting_currency,
        "inception": profile.inception,
        "risk_free_averaging_basis": BASIS,
        "calendar_version": CALENDAR_VERSION,
        "session_calendars": list(profile.markets),
        "interest_basis": INTEREST_BASIS,
    }


def _profile(snapshot: Mapping[str, Any]) -> ReportProfile:
    from .report_profiles import snapshot_profile

    profile = snapshot_profile(snapshot)
    if profile is None or profile.built_in:
        raise ValueError("A composite report profile is required")
    return profile


def _reword(error: Exception) -> ValueError:
    return ValueError(str(error).replace("MS ", "The composite's ", 1))


def analyze(snapshot: Mapping[str, Any]):
    """MS's arithmetic on the composite's own HKD history and design calendars."""

    profile = _profile(snapshot)
    if snapshot.get("reporting_currency") != "HKD":
        raise ValueError("Alpha World design composites report in HKD, not by relabelling another currency")
    if snapshot.get("period_start") != profile.inception:
        raise ValueError("The composite's inception observation is missing; do not silently rebase")
    if snapshot.get("methodology_identity") != methodology_identity(profile):
        raise ValueError("The composite's HKD/HIBOR methodology identity differs")
    # Fail on an uncovered calendar year even where no gap needs checking.
    for row in snapshot["performance"]["series"]:
        day = date.fromisoformat(str(row["date"]))
        for market in profile.markets:
            CALENDARS[market].is_session(day)
    try:
        metrics, daily, evidence = _analyze(snapshot, subject_id=profile.subject_id, version=ANALYTICS_VERSION,
                                            markets=profile.markets)
    except ValueError as error:
        raise _reword(error) from error
    rates = snapshot["risk"]["risk_free"]
    verified = period_average(rates["observations"], date.fromisoformat(evidence["start"]),
                              date.fromisoformat(evidence["end"]))
    if rates.get("rate_date") != verified["rate_date"] or rates.get("quality_status") != "pass":
        raise ValueError("The composite's HIBOR fixing date or quality evidence differs")
    evidence["session_calendars"] = list(profile.markets)
    evidence["report_style"] = profile.style
    return metrics, daily, evidence


def simulate(daily_returns, period_end: str, *, subject_id: str, paths: int = PATHS):
    if any(not math.isfinite(r) or r <= -1 for r in daily_returns):
        raise ValueError("The composite's simulation requires finite returns greater than -100%")
    try:
        return _simulate(daily_returns, period_end, paths=paths, subject_id=subject_id, version=ANALYTICS_VERSION)
    except ValueError as error:
        raise _reword(error) from error


def load_contract_snapshot(connection, contract: Contract, as_of: str, *, tenant_id: str) -> dict[str, Any]:
    """The composite's full HKD NAV from inception with its HIBOR evidence, not yet analysed."""

    subject_id = contract.subject_id
    records = connection.execute(
        "SELECT snapshot_date, currency, total_text FROM subject_nav_series WHERE tenant_id = ? "
        "AND subject_id = ? AND currency = ? AND snapshot_date <= ? ORDER BY snapshot_date",
        (tenant_id, subject_id, "HKD", as_of)).fetchall()
    rows = [{"date": str(r["snapshot_date"]), "currency": str(r["currency"]), "nav": float(r["total_text"])}
            for r in records]
    if not rows or rows[0]["date"] != contract.profile.inception:
        raise ValueError(f"{subject_id} approved HKD inception history is not ready")
    rates = load_period_average(connection, rows[0]["date"], rows[-1]["date"], tenant_id=tenant_id)
    return {"subject_id": subject_id, "period_start": rows[0]["date"], "period_end": as_of,
            "data_as_of": rows[-1]["date"], "reporting_currency": "HKD",
            "methodology_identity": methodology_identity(contract.profile),
            "report_profile": contract.profile.as_dict(),
            "performance": {"series": rows}, "risk": {"risk_free": rates}}


def load_risk_snapshot(connection, subject_id: str, as_of: str, *, tenant_id: str | None = None):
    """The composite's full HKD NAV from inception with its HIBOR evidence and metrics."""

    contract = require_contract(connection, subject_id, tenant_id=tenant_id)
    snapshot = load_contract_snapshot(connection, contract, as_of, tenant_id=tenant_id)
    metrics, _, evidence = analyze(snapshot)
    return snapshot, metrics, evidence


# Risk runtime ---------------------------------------------------------------


def _qualified_source(snapshot: Mapping[str, Any]) -> tuple[dict, dict, tuple, dict]:
    """Only the fields the calculation consumes, covering the latest required session."""

    profile = _profile(snapshot)
    cutoff = date.fromisoformat(snapshot["period_end"])
    latest_required = cutoff
    while not any(CALENDARS[market].is_session(latest_required) for market in profile.markets):
        latest_required -= timedelta(days=1)
    if not latest_required <= date.fromisoformat(snapshot["data_as_of"]) <= cutoff:
        raise ValueError("The composite's HKD NAV does not cover the latest required market session")
    source = deepcopy({key: snapshot[key] for key in (
        "subject_id", "period_start", "period_end", "data_as_of", "reporting_currency",
        "methodology_identity", "report_profile", "performance")})
    source["risk"] = {"risk_free": deepcopy(snapshot["risk"]["risk_free"])}
    metrics, daily, evidence = analyze(source)
    return source, metrics, daily, evidence


def validate_inputs(inputs: Mapping[str, Any]) -> dict:
    source, _, _, _ = _qualified_source(inputs["profiled_hkd_snapshot"])
    if (inputs["subject_id"] != source["subject_id"] or inputs["subject_type"] != "composite_portfolio"
            or inputs["as_of"] != source["period_end"] or inputs["risk_free"] != source["risk"]["risk_free"]
            or inputs["benchmark_id"] is not None or inputs["rows"] or inputs["positions"]):
        raise ValueError("The composite's risk input identity or methodology differs")
    if inputs.get("crisis_replay") is not None:
        raise ValueError("Crisis replay is not yet qualified for Alpha World design composites")
    return source


def build_payload(inputs: Mapping[str, Any]) -> dict:
    from .alpha_world_ms_runtime import extended_metrics

    source = validate_inputs(inputs)
    _, metrics, daily, evidence = _qualified_source(source)
    if evidence["excluded_intervals"]:
        raise ValueError("The composite's daily sample has unresolved gaps")
    extra = dict(extended_metrics(tuple(daily)))
    unavailable = {**CORE_UNAVAILABLE, "crisis_replay": "not-yet-qualified-for-design-composites"}
    if not extra["drawdown_simulation_paths"]:
        unavailable["one_year_drawdown"] = "fewer-than-20-validated-return-intervals"
    return {
        "account_id": source["subject_id"], "as_of": inputs["as_of"], "benchmark_id": None,
        "analytics_version": "risk-analytics-v2", "runtime_version": RUNTIME_VERSION,
        "methodology_identity": deepcopy(source["methodology_identity"]),
        "report_style": source["report_profile"]["style"],
        "unavailable_capabilities": unavailable, "reporting_currency": "HKD",
        "quality_status": "warn", "publication_allowed": False,
        "metrics": {**metrics, **extra, "daily_expected_tail_loss": metrics["cvar_95_daily"],
                    "return_observations": len(daily), "crisis_maximum_drawdown": None},
        "risk_free": deepcopy(source["risk"]["risk_free"]), "crisis_replay": None,
        "expected_tail_loss_contributors": {"top": [], "bottom": []},
        "source_snapshot": source, "analytics_evidence": evidence, "message": MESSAGE,
    }


def validate_payload(payload: Mapping[str, Any]) -> bool:
    """Recompute a stored payload from its own source; any difference fails."""

    if payload.get("runtime_version") != RUNTIME_VERSION:
        raise ValueError("The composite's risk runtime version differs")
    source = payload["source_snapshot"]
    if payload.get("account_id") != source.get("subject_id"):
        raise ValueError("The composite's risk payload belongs to another subject")
    inputs = {"subject_id": source["subject_id"], "subject_type": "composite_portfolio", "as_of": payload["as_of"],
              "benchmark_id": None, "rows": [], "positions": [], "profiled_hkd_snapshot": source,
              "risk_free": payload["risk_free"], "crisis_replay": payload["crisis_replay"]}
    if canonical_json(build_payload(inputs)) != canonical_json(payload):
        raise ValueError("The composite's cached risk arithmetic or evidence differs")
    return True


def styles() -> tuple[str, ...]:
    return tuple(STYLES)
