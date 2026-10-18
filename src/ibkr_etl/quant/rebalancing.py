"""Rebalancing policy: when to trade, not just what to hold.

Pure standard-library functions, like :mod:`ibkr_etl.modelling`:

* :func:`no_trade_bands` – how far each holding may drift from its target before a
  trade pays for itself, by the cube-root rule for proportional costs (Leland 1999;
  Rogers 2001; Janeček and Shreve 2004), with how soon each band is likely to be hit
  and what keeping to the bands costs a year.
* :func:`rebalance_plan` – the trades that bring holdings outside their band back to
  the band's edge (the optimal policy under proportional costs), in shares and value,
  their cost, the drift left, and the yearly cost of dividend withholding tax.

Weights are shares of NAV. Everything here is hypothetical.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError, _matvec
from .optimisers import _cost_rate, _finite, _positive, _square_matrix, _sumprod

MODEL_VERSION = "quant-rebalancing-v1"
TOLERANCE = 1e-12


def no_trade_bands(
    covariance: Sequence[Sequence[float]],
    weights: Sequence[float] | Mapping[Any, float],
    *,
    cost_bps: float,
    risk_aversion: float,
) -> dict[str, Any]:
    """Band half-width per holding from the cube-root rule for proportional costs.

    The rule (Rogers 2001; Janeček and Shreve 2004; the small-cost limit of Leland 1999)
    for a holding with target weight w, one-way cost ε = cost_bps / 10,000 of the value
    traded, and risk aversion γ in U = E[r] − (γ/2)·Var[r] is

        half-width δ = (3ε · w²(1 − w)² / (2γ))^(1/3).

    It balances the trading a band causes, s²/(2δ) of NAV a year for weight drift of
    variance s² = w²·Var(r_i − r_p) a year, against the risk of being off target, with
    the deviation offset pro rata in the rest of the portfolio. That variance cancels
    from the width, as volatility does in the one-asset rule, so the covariance sets
    how fast weights drift: the expected years from target to the band's edge, δ²/s²,
    and the expected yearly cost ε·s²/(2δ). Holdings are treated one at a time.

    ``weights`` may be a mapping (its key order matches the covariance rows) or a
    sequence; they are target shares of NAV and add up to at most 1 (the rest is cash).
    """

    cov = _square_matrix(covariance)
    n = len(cov)
    keys = list(weights) if isinstance(weights, Mapping) else list(range(len(weights)))
    values = [_finite(weights[k]) for k in keys] if isinstance(weights, Mapping) else [_finite(v) for v in weights]
    if len(values) != n:
        raise ModellingInputError(f"Target weights need one value per covariance row ({n}); got {len(values)}.")
    if any(v is None or v < 0 for v in values):
        raise ModellingInputError("Target weights must be zero or positive numbers.")
    if sum(values) > 1.0 + 1e-9:
        raise ModellingInputError("Target weights add up to more than 100% of NAV; borrowing is not modelled.")
    cost = _cost_rate(cost_bps)
    gamma = _positive(risk_aversion, "risk aversion")
    sigma_w = _matvec(cov, values)
    portfolio_variance = _sumprod(values, sigma_w)
    rows, bands, yearly_cost = [], {}, 0.0
    for key, w, i in zip(keys, values, range(n)):
        half = (3.0 * cost * w * w * (1.0 - w) ** 2 / (2.0 * gamma)) ** (1.0 / 3.0)
        drift = w * w * max(0.0, cov[i][i] - 2.0 * sigma_w[i] + portfolio_variance)
        turnover = drift / (2.0 * half) if half > 0 else (0.0 if drift == 0 else None)
        holding_cost = cost * turnover if turnover is not None else 0.0
        yearly_cost += holding_cost
        bands[key] = half
        rows.append({
            "holding": key,
            "target_weight": w,
            "half_width": half,
            "lower": max(0.0, w - half),
            "upper": min(1.0, w + half),
            "drift_volatility": math.sqrt(drift),
            "expected_years_to_edge": half * half / drift if drift > 0 else None,
            "expected_turnover_per_year": turnover,
            "expected_cost_per_year": holding_cost,
        })
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "cost_bps": cost * 10_000.0,
        "risk_aversion": gamma,
        "holdings": rows,
        "bands": bands,
        "expected_cost_per_year": yearly_cost,
        "method": (
            "No-trade band half-width δ = (3ε·w²(1 − w)²/(2γ))^(1/3) for each holding, the cube-root rule for "
            f"proportional costs (Rogers 2001; Janeček and Shreve 2004), with one-way cost ε = {cost * 10_000:g} bp "
            f"and risk aversion γ = {gamma:g}. Trade only when a weight leaves its band, and then only back to the "
            "edge. Drift speed comes from the covariance: the expected time from target to the edge is δ² divided "
            "by the yearly variance of the weight's drift, and the expected trading is that variance over 2δ a "
            "year. Holdings are treated one at a time."
        ),
        "disclaimer": DISCLAIMER,
    }


def _withholding_terms(value: Any, key: Any) -> tuple[float, float]:
    if isinstance(value, Mapping):
        rate, dividend_yield = value.get("rate"), value.get("yield")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        rate, dividend_yield = value
    else:
        raise ModellingInputError(f"Withholding for {key} needs a rate and an expected dividend yield.")
    rate, dividend_yield = _finite(rate), _finite(dividend_yield)
    if rate is None or not 0.0 <= rate <= 1.0 or dividend_yield is None or not 0.0 <= dividend_yield <= 1.0:
        raise ModellingInputError(f"Withholding for {key} needs a rate and a yield between 0% and 100%.")
    return rate, dividend_yield


def _shares(mapping: Mapping[Any, Any], label: str) -> dict[Any, float]:
    out = {}
    for key, value in mapping.items():
        number = _finite(value)
        if number is None or number < 0:
            raise ModellingInputError(f"The {label} of {key} must be zero or a positive number.")
        out[key] = number
    return out


def rebalance_plan(
    current_weights: Mapping[Any, float],
    target_weights: Mapping[Any, float],
    bands: Mapping[Any, float],
    *,
    nav: float,
    prices: Mapping[Any, float],
    cost_bps: float,
    dividend_withholding: Mapping[Any, Any] | None = None,
) -> dict[str, Any]:
    """Trades that bring every holding outside its band back to the band's edge.

    Weights are shares of NAV; whatever they leave over is cash, which takes the net of
    the trades and their cost. A holding in only one of the two weight mappings has
    weight 0 in the other; a holding with no band is traded straight to its target.
    ``prices`` are per share in the NAV's currency and are needed for every holding
    traded. Shares are exact, before rounding to whole shares or board lots.
    ``dividend_withholding`` maps holdings to ``{"rate": 0.30, "yield": 0.015}`` (or a
    pair) and reports the tax withheld each year on the holdings after the trades.
    """

    current = _shares(current_weights, "current weight")
    target = _shares(target_weights, "target weight")
    half_widths = _shares(bands, "band")
    value = _finite(nav)
    if value is None or value <= 0:
        raise ModellingInputError("NAV must be a positive amount.")
    cost = _cost_rate(cost_bps)
    keys = list(dict.fromkeys([*current, *target]))
    holdings, trades, traded_value, full_value = [], [], 0.0, 0.0
    after: dict[Any, float] = {}
    for key in keys:
        now, goal, half = current.get(key, 0.0), target.get(key, 0.0), half_widths.get(key, 0.0)
        lower, upper = max(0.0, goal - half), goal + half
        new = upper if now > upper + TOLERANCE else lower if now < lower - TOLERANCE else now
        after[key] = new
        full_value += abs(goal - now) * value
        change = new - now
        row = {"holding": key, "current_weight": now, "target_weight": goal, "lower": lower, "upper": upper,
               "weight_after": new, "drift_after": new - goal,
               "action": "sell to band edge" if change < 0 else "buy to band edge" if change > 0 else "inside band"}
        holdings.append(row)
        if change == 0:
            continue
        price = _finite(prices.get(key))
        if price is None or price <= 0:
            raise ModellingInputError(f"A price per share is needed for {key}, which is outside its band.")
        amount = change * value
        traded_value += abs(amount)
        trades.append({"holding": key, "side": "sell" if amount < 0 else "buy", "value": amount,
                       "shares": amount / price, "price": price, "cost": abs(amount) * cost})
    trading_cost = traded_value * cost
    cash_before = 1.0 - sum(current.values())
    cash_after = cash_before - sum(after[k] - current.get(k, 0.0) for k in keys) - trading_cost / value
    drift = [abs(after[k] - target.get(k, 0.0)) for k in keys]
    warnings = []
    if cash_after < -1e-9:
        warnings.append("The buys need more cash than the sells and the cash balance provide; "
                        "fund the difference or scale the buys back.")
    result = {
        "available": True,
        "model_version": MODEL_VERSION,
        "nav": value,
        "cost_bps": cost * 10_000.0,
        "trades": trades,
        "holdings": holdings,
        "traded_value": traded_value,
        "expected_cost": trading_cost,
        "full_rebalance_cost": full_value * cost,
        "cost_saved": full_value * cost - trading_cost,
        "cash_weight_before": cash_before,
        "cash_weight_after": cash_after,
        "remaining_drift": {"total": sum(drift), "largest": max(drift) if drift else 0.0},
        "withholding": None,
        "warnings": warnings,
        "method": (
            "Holdings inside their no-trade band are left alone; a holding outside it is traded back to the band's "
            "nearest edge, not to its target, which is the optimal policy when costs are proportional to the "
            f"value traded. Cost is {cost * 10_000:g} bp of each trade's value. Drift left is the weight after the "
            "trades minus the target. Cash takes the net of the trades and their cost."
        ),
        "disclaimer": DISCLAIMER,
    }
    if dividend_withholding:
        rows, total = [], 0.0
        for key, terms in dividend_withholding.items():
            rate, dividend_yield = _withholding_terms(terms, key)
            held = after.get(key, 0.0) * value
            withheld = held * dividend_yield * rate
            total += withheld
            rows.append({"holding": key, "value_held": held, "dividend_yield": dividend_yield,
                         "withholding_rate": rate, "withheld_per_year": withheld})
        result["withholding"] = {"holdings": rows, "withheld_per_year": total, "share_of_nav": total / value,
                                 "method": "Value held after the trades × expected dividend yield × withholding rate."}
    return result
