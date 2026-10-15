"""Hypothetical HKD crisis replay for composites in the Alpha World MS or T design.

The composite's dated components replayed through 15 September 2008 to
9 March 2009, the way MS and T are: each account sleeve from its exact broker
holdings (as T's sleeves), each portfolio sleeve from its stored replay rebuilt
from archived prices (as MS's sleeves), the Hang Seng and SPY hedges from the
archived closes, and HKMA FX translating every USD path to HKD before the
signed component values are combined. Constant cash stays flat; no financing
or trading cost is added. Internal and shareholder use only.

Pure arithmetic with an independent Decimal check; the loaders read, never write.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext
import math
from typing import Any, Mapping

from .alpha_world_ms_crisis import (
    MARKET_SHA256, align, database_records, finite, market_sources, require, sha, verify_sleeve,
)
from .alpha_world_t_crisis import _sleeve, capture_records
from .subject_onboarding import configuration_sha256

VERSION = "alpha-world-design-composite-hkd-crisis-replay-v1"
PROXY_VERSION = "alpha-world-design-composite-crisis-identity-proxy-v1"
CASH = "constant-cash"
# The archived hedge closes; another benchmark has no crisis-period series here.
BENCHMARKS = ("hang-seng-price-index", "spy-settlement")
PARAMOUNT_CONID = "804144296"
PARAMOUNT_SOURCE = "https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2025-414"
PROXY_REASON = ("Broad-market SPY proxy, the rule the owner approved for MS and T on 9 October 2026 and "
                "extended to Alpha World design composites on 15 October 2026. Current Paramount Skydance shares "
                "started trading on 7 August 2025; predecessor crisis-period security lineage has not been "
                "verified. Hypothetical crisis stress only. Identity source: " + PARAMOUNT_SOURCE)
LIMITATIONS = ("Hypothetical replay of the composite's current components, not actual 2008-2009 performance. "
               "Archived provisional prices and documented proxies; constant HKD cash; no projected financing "
               "or trading costs. Internal/shareholder use only.")


def identity_proxy(mapping: Mapping[str, Any], *, crisis_start: str) -> dict:
    """The MS and T Paramount rule, for a composite's account sleeves."""

    result = deepcopy(dict(mapping))
    if str(result.get("conid")) != PARAMOUNT_CONID:
        return result
    require(result.get("holding_symbol") in ("PSKY", "SKYD"), "Paramount broker identity has an unexpected ticker")
    if date.fromisoformat(str(crisis_start)) >= date(2025, 8, 7):
        return result
    result.update(source_symbol="SPY", mapping_method="market_proxy", proxy_reason=PROXY_REASON,
                  proxy_policy_version=PROXY_VERSION, proxy_identity_source=PARAMOUNT_SOURCE,
                  original_source_symbol=result.get("original_source_symbol", mapping.get("source_symbol")),
                  original_mapping_method=result.get("original_mapping_method", mapping.get("mapping_method")))
    return result


def _decimal(value, *, positive=False) -> Decimal:
    finite(value, positive=positive)
    return Decimal(str(value))


def compute(inputs: Mapping[str, Any]) -> dict:
    """The replay from its captured inputs; any missing, extra or inconsistent input fails."""

    definition = inputs["definition"]
    subject_id = str(inputs["subject_id"])
    require(inputs["reporting_currency"] == "HKD" and definition.get("base_currency") == "HKD",
            "The composite's crisis replay reports in HKD")
    require(definition.get("subject_id") == subject_id and definition.get("subject_type") == "composite_portfolio"
            and configuration_sha256(definition) == inputs["contract_sha256"],
            "The composite's crisis replay is not bound to its approved definition")
    day = str(inputs["holdings_as_of"])
    require(date.fromisoformat(day) <= date.fromisoformat(str(inputs["requested_as_of"])),
            "Future components cannot enter the composite's crisis replay")
    plan = {str(c["component_id"]): c for c in definition["components"]}
    values = {str(k): _decimal(v) for k, v in inputs["component_values"].items()}
    require(set(values) == set(plan) | {CASH}, "The composite's crisis components differ from its definition")
    nav = _decimal(inputs["nav"], positive=True)
    require(abs(sum(values.values()) - nav) <= Decimal("1e-9")
            and values[CASH] == _decimal(definition["constant_cash"])
            and all((values[k] > 0) == (float(c["coefficient"]) > 0) and values[k] != 0 for k, c in plan.items()),
            "The composite's signed crisis components do not reconcile to NAV")
    for key, component in plan.items():
        if component["component_type"] == "benchmark":
            require(key in BENCHMARKS, f"No archived crisis-period series for {component['component_name']}")
    market = market_sources()
    days = market["calendar"]
    fx, fx_audit = align(market["fx"]["observations"], days, "rate")
    hsi, hsi_audit = align(market["hedges"]["hsi"]["observations"], days, "price", hong_kong_sessions=True)
    spy, spy_audit = align(market["hedges"]["spy"]["observations"], days, "price")
    records = inputs["records"]
    paths: dict[str, list[Decimal]] = {}
    evidence: dict[str, Any] = {}
    direct = proxy = 0
    rows, peak = [], Decimal(1)
    with localcontext() as context:
        context.prec = 40
        currency = [_decimal(fx[d]) / _decimal(fx[days[0]]) for d in days]
        for key, component in plan.items():
            kind = component["component_type"]
            if key == "hang-seng-price-index":
                paths[key] = [_decimal(hsi[d]) / _decimal(hsi[days[0]]) for d in days]
                evidence[key] = {"method": "archived unadjusted HKD close"}
                direct += 1
            elif key == "spy-settlement":
                paths[key] = [_decimal(spy[d]) / _decimal(spy[days[0]]) * f for d, f in zip(days, currency)]
                evidence[key] = {"method": "archived unadjusted USD close in HKD"}
                direct += 1
            elif kind == "account":
                levels, sleeve = _sleeve(records["accounts"], key, day, days, identity=identity_proxy)
                paths[key] = [level * f for level, f in zip(levels, currency)]
                methods = [r["mapping_method"] for r in sleeve["components"]]
                direct += methods.count("direct")
                proxy += methods.count("market_proxy")
                evidence[key] = {"method": "exact broker holdings", **sleeve}
            else:
                require(kind == "portfolio", f"The composite's {key} sleeve has no crisis method")
                source = verify_sleeve(records["portfolios"], key, day, days)
                require(math.isclose(source["daily"][0]["value"], 1., abs_tol=1e-12),
                        "The composite's sleeve replay does not open at one")
                paths[key] = [_decimal(r["value"], positive=True) * f for r, f in zip(source["daily"], currency)]
                direct += source["direct_components"]
                proxy += source["proxy_components"]
                evidence[key] = {"method": "stored sleeve replay rebuilt from archived prices",
                                 **{k: source[k] for k in ("source_sha256", "direct_components", "proxy_components",
                                                           "legacy_maximum_absolute_difference")}}
        for i, d in enumerate(days):
            level = (values[CASH] + sum(values[k] * paths[k][i] for k in plan)) / nav
            independent = (float(values[CASH]) + sum(float(values[k]) * float(paths[k][i]) for k in plan)) / float(nav)
            require(level > 0 and math.isclose(float(level), independent, abs_tol=2e-12, rel_tol=0),
                    "The composite's crisis arithmetic fails its independent check")
            peak = max(peak, level)
            rows.append({"observation_date": d, "normalized_value": float(level), "drawdown": float(level / peak - 1)})
    require(math.isclose(rows[0]["normalized_value"], 1., abs_tol=1e-10, rel_tol=0),
            "The composite's crisis replay does not open at one")
    return {"subject_id": subject_id, "reporting_currency": "HKD", "calculation_version": VERSION,
            "requested_as_of": inputs["requested_as_of"], "holdings_as_of": day,
            "crisis_start": days[0], "crisis_end": days[-1], "observations": len(days),
            "maximum_drawdown": min(r["drawdown"] for r in rows), "daily": rows,
            "cash_weight": float(values[CASH] / nav), "direct_components": direct, "proxy_components": proxy,
            "quality_status": "warn", "qualification_status": "qualified_internal",
            "independent_decimal_verified": True, "provider_prices_independently_verified": False,
            "publication_status": "blocked_provisional_market_data", "proxy_policy_version": PROXY_VERSION,
            "provider": market["provider"], "market_source_sha256": MARKET_SHA256, "input_sha256": sha(inputs),
            "inputs": deepcopy(dict(inputs)), "sleeve_evidence": evidence,
            "fx_and_hedge_alignment_sha256": sha([fx_audit, hsi_audit, spy_audit]),
            "cash_interest": "none", "projected_financing": "none", "limitations": LIMITATIONS}


def validate(replay, *, as_of=None, holdings=None, nav=None, contract_sha256=None):
    """Recompute a stored replay from its own inputs, then bind it to the snapshot it serves."""

    require(isinstance(replay, dict) and replay.get("calculation_version") == VERSION,
            "A qualified composite HKD crisis replay is required")
    require(compute(replay["inputs"]) == replay, "The composite's crisis replay differs from recomputation")
    if as_of is not None:
        require(replay["holdings_as_of"] == as_of, "The composite's crisis components are from another date")
    if holdings is not None:
        require(all(r.get("currency") == "HKD" for r in holdings)
                and {str(r["conid"]): float(r["market_value_text"]) for r in holdings}
                == {k: float(v) for k, v in replay["inputs"]["component_values"].items()},
                "The composite's crisis replay uses other components")
    if nav is not None:
        require(_decimal(nav) == _decimal(replay["inputs"]["nav"]), "The composite's crisis replay uses another NAV")
    if contract_sha256 is not None:
        require(replay["inputs"]["contract_sha256"] == contract_sha256,
                "The composite's crisis replay uses another definition")
    return replay


def build(definition: Mapping[str, Any], snapshot: Mapping[str, Any], records: Mapping[str, Any]) -> dict:
    """The replay for a snapshot's dated components and NAV, from captured records."""

    holdings = snapshot["holdings"]["holdings"]
    nav = snapshot["portfolio_summary"]["latest_nav"]["total_text"]
    inputs = {"subject_id": snapshot["subject_id"], "reporting_currency": "HKD",
              "requested_as_of": snapshot["period_end"], "holdings_as_of": snapshot["data_as_of"],
              "contract_sha256": snapshot["methodology_identity"]["contract_sha256"],
              "definition": deepcopy(dict(definition)), "nav": str(nav),
              "component_values": {str(r["conid"]): str(r["market_value_text"]) for r in holdings},
              "records": records}
    return validate(compute(inputs), as_of=snapshot["data_as_of"], holdings=holdings, nav=nav)


def capture(connection, definition: Mapping[str, Any], holdings_as_of: str, *, tenant_id: str) -> dict:
    """Exact dated sleeve evidence for every sleeve type; no refreshes or writes."""

    require(bool(tenant_id), "The composite's crisis replay requires an explicit tenant")
    kinds: dict[str, list[str]] = {"account": [], "portfolio": []}
    for component in definition["components"]:
        if component["component_type"] in kinds:
            kinds[component["component_type"]].append(str(component["component_id"]))
    records: dict[str, Any] = {"accounts": None, "portfolios": None}
    if kinds["account"]:
        records["accounts"] = capture_records(connection, tenant_id=tenant_id, holdings_as_of=holdings_as_of,
                                              accounts=kinds["account"])
    if kinds["portfolio"]:
        subjects = kinds["portfolio"]
        among = "subject_id IN (" + ",".join("?" for _ in subjects) + ") AND "

        def rows(table, where, args, order):
            return database_records(connection.execute(
                "SELECT * FROM " + table + " WHERE tenant_id = ? AND " + where + " ORDER BY " + order,
                (tenant_id, *args)).fetchall())

        records["portfolios"] = {
            "source_nav": rows("subject_nav_series", among + "snapshot_date = ?", [*subjects, holdings_as_of],
                               "subject_id, snapshot_date"),
            "source_positions": rows("subject_position_series", among + "snapshot_date = ?",
                                     [*subjects, holdings_as_of], "subject_id, snapshot_date, conid"),
            "mappings": rows("crisis_replay_components", among + "holdings_as_of = ?", [*subjects, holdings_as_of],
                             "subject_id, holdings_as_of, conid"),
            "replays": rows("crisis_replay_daily", among + "holdings_as_of = ?", [*subjects, holdings_as_of],
                            "subject_id, holdings_as_of, observation_date"),
            "prices": rows("market_data_prices", "instrument_id LIKE ? AND price_date BETWEEN ? AND ?",
                           ["crisis-replay-%", "2008-09-05", "2009-03-09"], "symbol, price_date"),
            "source_fx": rows("reference_fx_rates",
                              "from_currency = 'HKD' AND to_currency = 'USD' AND rate_date BETWEEN ? AND ?",
                              [(date.fromisoformat(holdings_as_of) - timedelta(days=4)).isoformat(), holdings_as_of],
                              "rate_date, provider")}
    return records


def load(connection, snapshot: Mapping[str, Any], *, tenant_id: str) -> dict:
    """The replay for a composite snapshot, reading its components where the snapshot has none."""

    from .profiled_composite import require_contract

    contract = require_contract(connection, snapshot["subject_id"], tenant_id=tenant_id)
    require(contract.definition_sha256 == snapshot["methodology_identity"]["contract_sha256"],
            "The composite's snapshot is from another definition")
    source = dict(snapshot)
    if "holdings" not in source:
        rows = connection.execute(
            "SELECT component_id, contribution, base_currency FROM synthetic_portfolio_components "
            "WHERE tenant_id = ? AND portfolio_id = ? AND snapshot_date = ? ORDER BY component_id",
            (tenant_id, snapshot["subject_id"], snapshot["data_as_of"])).fetchall()
        source["holdings"] = {"holdings": [{"conid": str(r["component_id"]), "currency": str(r["base_currency"]),
                                            "market_value_text": str(r["contribution"])} for r in rows]}
        source["portfolio_summary"] = {"latest_nav": {"total_text": str(snapshot["performance"]["series"][-1]["nav"])}}
    records = capture(connection, contract.definition, str(snapshot["data_as_of"]), tenant_id=tenant_id)
    return build(contract.definition, source, records)
