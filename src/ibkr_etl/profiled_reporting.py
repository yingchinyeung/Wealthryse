"""Month-end reporting inputs for composites in the Alpha World MS or T design.

The same inputs T's reports use (alpha_world_t_reporting), built from the
composite's own approved definition instead of T's fixed sleeves: full HKD NAV
from inception, every dated component reconciled to NAV, and the month's
contribution of each sleeve. Read-only.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import math
from typing import Any, Mapping, Sequence

from .profiled_composite import Contract, _qualified_source, load_contract_snapshot, require_contract

VERSION = "alpha-world-design-composite-reporting-inputs-v1"
CASH = "constant-cash"
TABLES = ["subject_nav_series", "synthetic_portfolio_components", "risk_free_rates"]


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def sleeves(definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Each sleeve's name, category, coefficient and direction, in definition order, then cash."""

    rows = []
    for component in definition["components"]:
        coefficient = float(component["coefficient"])
        direction = "long" if coefficient > 0 else "short"
        source = "price index" if component["component_type"] == "benchmark" else "account NAV"
        rows.append({"component_id": str(component["component_id"]),
                     "name": str(component.get("component_name") or component["component_id"]),
                     "category": f"{direction.capitalize()} {source}", "coefficient": coefficient,
                     "direction": direction})
    rows.append({"component_id": CASH, "name": "Constant cash", "category": "Constant cash",
                 "coefficient": float(definition["constant_cash"]), "direction": "cash"})
    return rows


def enrich(core: Mapping[str, Any], components: Sequence[Mapping[str, Any]],
           definition: Mapping[str, Any], crisis: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate every dated component and derive monthly reporting; never mutate inputs."""

    _, metrics, _, evidence = _qualified_source(core)
    subject_id = core["subject_id"]
    snapshot = deepcopy(dict(core))
    series = snapshot["performance"]["series"]
    plan = {row["component_id"]: row for row in sleeves(definition)}
    cash = plan[CASH]["coefficient"]
    by_day: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for raw in components:
        row = dict(raw)
        day, key = str(row["snapshot_date"]), str(row["component_id"])
        _require(row["portfolio_id"] == subject_id and row["base_currency"] == "HKD"
                 and key in plan and key not in by_day[day],
                 "The composite's component identity or currency is duplicated or incorrect")
        fields = {k: float(row[k]) for k in ("coefficient", "source_value", "fx_rate_to_base", "base_value",
                                             "normalized_value", "contribution")}
        _require(all(math.isfinite(v) for v in fields.values()) and fields["fx_rate_to_base"] > 0,
                 "The composite's component valuation is invalid")
        _require(fields["coefficient"] == plan[key]["coefficient"],
                 "The composite's fixed-unit coefficient differs from its approved definition")
        _require(math.isclose(fields["source_value"] * fields["fx_rate_to_base"], fields["base_value"],
                              abs_tol=1e-8, rel_tol=1e-12)
                 and math.isclose(fields["coefficient"] * fields["normalized_value"], fields["contribution"],
                                  abs_tol=1e-10, rel_tol=1e-12),
                 "The composite's component valuation identity differs")
        _require(row["direction"] == plan[key]["direction"], "The composite's component direction differs")
        direction = plan[key]["direction"]
        _require((direction == "cash" and fields["contribution"] == cash)
                 or (direction == "long" and fields["contribution"] > 0)
                 or (direction == "short" and fields["contribution"] < 0),
                 "The composite's component sign or constant cash differs")
        by_day[day][key] = row
    _require(set(by_day) == {r["date"] for r in series},
             "The composite's component history dates differ from its NAV history")
    for point in series:
        rows = by_day[point["date"]]
        _require(set(rows) == set(plan) and math.isclose(
            sum(float(r["contribution"]) for r in rows.values()), float(point["nav"]), abs_tol=1e-9, rel_tol=0),
            "The composite's dated components do not reconcile to NAV")
    months: dict[str, Mapping[str, Any]] = {}
    for row in series:
        months[row["date"][:7]] = row
    monthly_rows, previous = [], float(series[0]["nav"])
    for row in months.values():
        value = float(row["nav"])
        monthly_rows.append({"date": row["date"], "portfolio": value, "portfolio_monthly_return": value / previous - 1,
                             "benchmark_monthly_return": None})
        previous = value
    start = monthly_rows[-2]["date"] if len(monthly_rows) > 1 else series[0]["date"]
    start_nav = monthly_rows[-2]["portfolio"] if len(monthly_rows) > 1 else float(series[0]["nav"])
    before, after = by_day[start], by_day[core["data_as_of"]]
    holdings, contributions = [], []
    sector: dict[str, float] = defaultdict(float)
    sector_effect: dict[str, float] = defaultdict(float)
    for key, item in plan.items():
        value = float(after[key]["contribution"])
        effect = (value - float(before[key]["contribution"])) / start_nav
        holdings.append({"conid": key, "symbol": item["name"], "description": item["name"], "currency": "HKD",
                         "market_value_text": str(value), "quantity_text": None, "mark_price_text": None,
                         "holding_kind": "synthetic_exposure",
                         "asset_category": "cash" if key == CASH else "component",
                         "valuation_basis": "coefficient-times-normalized-source",
                         "quantity_basis": "not-applicable-no-security-units"})
        contributions.append({"conid": key, "symbol": item["name"], "description": item["name"],
                              "contribution": effect})
        sector[item["category"]] += abs(value)
        sector_effect[item["category"]] += effect
    gross = sum(sector.values())
    _require(math.isclose(sum(r["contribution"] for r in contributions), monthly_rows[-1]["portfolio_monthly_return"],
                          abs_tol=1e-10, rel_tol=0), "The composite's monthly contribution bridge differs")
    profile = snapshot["report_profile"]
    snapshot.update(
        snapshot_version=VERSION, formula_version=evidence["version"], quality_status="warn",
        publication_allowed=False,
        publication_blockers=["primary_benchmark:not_licensed_for_publication",
                              "secondary_1_benchmark:not_licensed_for_publication"],
        month_end_performance=monthly_rows,
        portfolio_summary={"account": {"account_id": subject_id, "alias": profile["display_name"],
                                       "base_currency": "HKD"},
                           "latest_nav": {"snapshot_date": core["data_as_of"], "currency": "HKD",
                                          "total_text": str(series[-1]["nav"])}},
        holdings={"holdings": holdings},
        sector_allocation={"available": True, "publication_allowed": True,
                           "classification_basis": "composite sleeve type", "classification_coverage_pct": 100,
                           "gross_market_value": gross,
                           "allocation": [{"sector": k, "weight": v / gross, "market_value": v}
                                          for k, v in sector.items()]},
        monthly_reporting={"available": True, "period_start": start, "period_end": core["data_as_of"],
                           "reporting_currency": "HKD",
                           "method_version": "fixed-unit-component-delta-over-prior-month-nav-v1",
                           "portfolio_monthly_return": monthly_rows[-1]["portfolio_monthly_return"],
                           "security_contributions": contributions,
                           "sector_contributions": [{"sector": k, "contribution": v}
                                                    for k, v in sector_effect.items()],
                           "equity_contribution": sum(r["contribution"] for r in contributions),
                           "residual_contribution": 0, "cash_activity": {"dividends_by_symbol": []}},
        performance_comparison={"available": False, "series": [], "metrics": {}, "publication_allowed": False},
        secondary_performance_comparison={"available": False, "series": [], "metrics": {},
                                          "publication_allowed": False},
    )
    snapshot["risk"].update(metrics=metrics, analytics_evidence=evidence, crisis_replay=deepcopy(crisis))
    if crisis:
        from .profiled_crisis import validate
        validate(crisis, as_of=core["data_as_of"], holdings=holdings,
                 nav=snapshot["portfolio_summary"]["latest_nav"]["total_text"],
                 contract_sha256=snapshot["methodology_identity"]["contract_sha256"])
    else:
        snapshot["publication_blockers"].append("risk:crisis_replay_unavailable")
    return snapshot


def load(connection, subject_id: str, as_of: str, *, tenant_id: str) -> dict[str, Any]:
    contract: Contract = require_contract(connection, subject_id, tenant_id=tenant_id)
    core = load_contract_snapshot(connection, contract, as_of, tenant_id=tenant_id)
    components = connection.execute(
        "SELECT * FROM synthetic_portfolio_components WHERE tenant_id = ? AND portfolio_id = ? "
        "AND snapshot_date <= ? ORDER BY snapshot_date, component_id",
        (tenant_id, subject_id, core["data_as_of"])).fetchall()
    snapshot = enrich(core, components, contract.definition)
    from .profiled_crisis import build, capture
    # As MS and T: without exact-date sleeve evidence the replay is shown as unavailable.
    try:
        crisis = build(contract.definition, snapshot,
                       capture(connection, contract.definition, core["data_as_of"], tenant_id=tenant_id))
    except (ValueError, KeyError, TypeError):
        return snapshot
    return enrich(core, components, contract.definition, crisis)


def tool_results(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The month-end snapshot inputs in the shape the agent tools return them."""

    def result(data, quality="pass"):
        return {"as_of": snapshot["data_as_of"], "quality_status": quality, "data": deepcopy(data),
                "citations": [{"subject_id": snapshot["subject_id"], "currency": "HKD",
                               "through": snapshot["data_as_of"], "tables": list(TABLES),
                               "calculation_version": snapshot["formula_version"]}]}

    return {
        "summary": result(snapshot["portfolio_summary"]), "performance": result(snapshot["performance"]),
        "holdings": result(snapshot["holdings"]), "risk": result(snapshot["risk"], "warn"),
        "sector_allocation": result(snapshot["sector_allocation"]),
        "monthly_reporting": result(snapshot["monthly_reporting"]),
        "primary_benchmark": result(snapshot["performance_comparison"], "warn"),
        "secondary_benchmark": result(snapshot["secondary_performance_comparison"], "warn"),
        "freshness": result({"portfolio": {"nav_date": snapshot["data_as_of"],
                                           "position_date": snapshot["data_as_of"]},
                             "risk_free_date": snapshot["risk"]["risk_free"]["rate_date"]}),
        "report_profile": result({"report_profile": snapshot["report_profile"],
                                  "methodology_identity": snapshot["methodology_identity"]}),
    }
