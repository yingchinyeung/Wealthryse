"""Pre-trade check: what a set of orders would do to the portfolio before they are placed.

Pure standard-library Python, like :mod:`ibkr_etl.modelling`. One function:

* :func:`pre_trade_check` applies proposed orders to today's holdings, with cash taking
  up the difference, and reports before and after: ex-ante volatility, the largest
  holding, top-5 concentration, the effective number of holdings, historical VaR and
  expected tail loss over the same history, factor exposures, days to liquidate, the
  estimated cost of trading (half the bid-ask spread plus square-root market impact),
  and any limit the new portfolio would break.

It reuses the engines that already exist: :mod:`ibkr_etl.quant.holdings_risk` for the
covariance and factor exposures, :mod:`ibkr_etl.quant.liquidity` for days to
liquidate, :func:`ibkr_etl.quant.execution_analysis.square_root_impact` for impact,
:func:`ibkr_etl.quant.crowding.concentration` and
:func:`ibkr_etl.quant.desk_risk.limit_check`. Money is in the NAV's currency.

It places nothing. The result is a hypothetical check, not advice, and says so.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError
from ..risk_forecasting import _tail
from .crowding import concentration
from .desk_risk import DEFAULT_AMBER_AT, limit_check
from .execution_analysis import BASIS_POINTS, square_root_impact
from .holdings_risk import _clean_columns, ex_ante_risk, factor_risk_decomposition, ledoit_wolf_covariance
from .liquidity import DEFAULT_PARTICIPATION, _volume, days_to_liquidate

MODEL_VERSION = "pre-trade-v1"
DEFAULT_VAR_LEVEL = 0.95
NOTICE = ("Hypothetical pre-trade check of orders that have not been placed. It sends nothing to the broker and is "
          "not advice.")
LIMIT_METRICS = ("gross_exposure", "net_exposure", "cash_weight", "largest_position_weight", "top_5_weight",
                 "ex_ante_volatility", "var_1d", "expected_tail_loss_1d", "factor_exposures", "liquidity_days")


def _missing(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, float) and math.isnan(value))


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool):
        raise ModellingInputError(f"{what} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ModellingInputError(f"{what} is missing or not a number.") from None
    if not math.isfinite(number):
        raise ModellingInputError(f"{what} is missing or not a number.")
    return number


def _optional(value: Any, what: str) -> float | None:
    return None if _missing(value) else _number(value, what)


def _positive_or_none(value: Any, what: str) -> float | None:
    number = _optional(value, what)
    if number is not None and number <= 0:
        raise ModellingInputError(f"{what} must be positive.")
    return number


# Inputs ----------------------------------------------------------------------------------


def _book(current_positions: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """holding → quantity, price and multiplier; the price is None when neither price nor value is given."""

    book = {}
    for key, row in current_positions.items():
        name = str(key)
        quantity = _number(row.get("quantity"), f"The quantity of {name}")
        scale = _positive_or_none(row.get("multiplier"), f"The multiplier of {name}") or 1.0
        price = _positive_or_none(row.get("price"), f"The price of {name}")
        value = _optional(row.get("value"), f"The value of {name}")
        if price is None and value is not None and quantity:
            price = value / (quantity * scale)
            if price <= 0:
                raise ModellingInputError(f"The value of {name} has the opposite sign to its quantity.")
        book[name] = {"quantity": quantity, "price": price, "multiplier": scale}
    return book


def _signed_quantity(order: Mapping[str, Any], index: int) -> float:
    quantity = _number(order.get("quantity"), f"The quantity of order {index}")
    side = str(order.get("side") or "").strip().upper()
    if side:
        if side not in ("BUY", "SELL"):
            raise ModellingInputError(f"Order {index}'s side must be BUY or SELL.")
        quantity = abs(quantity) * (1.0 if side == "BUY" else -1.0)
    if quantity == 0:
        raise ModellingInputError(f"Order {index} has no quantity.")
    return quantity


def _orders(proposed_orders: Sequence[Mapping[str, Any]], book: dict[str, dict[str, Any]]
            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Priced orders, and refused ones with the reason. A new holding takes its price from its order."""

    if not proposed_orders:
        raise ModellingInputError("There are no orders to check.")
    accepted, refused = [], []
    for index, order in enumerate(proposed_orders, start=1):
        holding = str(order.get("holding") or "").strip()
        if not holding:
            raise ModellingInputError(f"Order {index} does not say which holding it is for.")
        quantity = _signed_quantity(order, index)
        held = book.get(holding, {})
        price = _positive_or_none(order.get("price"), f"The price of order {index}") or held.get("price")
        scale = (_positive_or_none(order.get("multiplier"), f"The multiplier of order {index}")
                 or held.get("multiplier") or 1.0)
        row = {"index": index, "holding": holding, "side": "BUY" if quantity > 0 else "SELL", "quantity": quantity}
        if price is None:
            refused.append({**row, "reason": f"There is no price for {holding}, so the order cannot be valued."})
            continue
        accepted.append({**row, "price": price, "multiplier": scale, "value": quantity * price * scale})
        if not held or held.get("price") is None:
            book[holding] = {"quantity": held.get("quantity", 0.0), "price": price, "multiplier": scale}
    return accepted, refused


def _history(returns_history: Mapping[str, Any]) -> dict[str, Sequence[float]]:
    """holding → daily returns, from a mapping or a modelling.holding_return_matrix result."""

    if isinstance(returns_history.get("returns"), Mapping) and "dates" in returns_history:
        return {str(k): v for k, v in returns_history["returns"].items()}
    return {str(k): v for k, v in returns_history.items()}


def _spread(raw: Any, holding: str) -> float | None:
    """A relative spread (ask − bid)/mid, given as that number or as a quote with bid and ask."""

    if _missing(raw):
        return None
    if isinstance(raw, Mapping):
        bid = _positive_or_none(raw.get("bid", raw.get("bid_price")), f"The bid of {holding}")
        ask = _positive_or_none(raw.get("ask", raw.get("ask_price")), f"The ask of {holding}")
        if bid is None or ask is None:
            return None
        if bid > ask:
            raise ModellingInputError(f"The quote for {holding} is crossed: its bid is above its ask.")
        return (ask - bid) / ((ask + bid) / 2.0)
    spread = _number(raw, f"The spread of {holding}")
    if spread < 0:
        raise ModellingInputError(f"The spread of {holding} must be zero or more.")
    return spread


# Measures of one state ---------------------------------------------------------------------


def _risk_model(covariance: Any, factor_model: Any, columns: Mapping[str, list[float]]) -> tuple[Any, str]:
    if covariance is not None:
        if not isinstance(covariance, Mapping) or "names" not in covariance:
            raise ModellingInputError("Pass the covariance as a result of ledoit_wolf_covariance or factor_risk_model, "
                                      "so its holdings are named.")
        return covariance, "the covariance given"
    if factor_model is not None:
        return factor_model, "the factor model given (B F Bᵀ + D)"
    if not columns:
        return None, "none: no holding has a return history"
    return ledoit_wolf_covariance(columns), "Ledoit-Wolf shrinkage covariance of the return history"


def _state(weights: Mapping[str, float], quantities: Mapping[str, float], values: Mapping[str, float], *,
           model: Any, columns: Mapping[str, list[float]], factor_model: Any, volumes: Mapping[str, Any] | None,
           participation: float, level: float) -> dict[str, Any]:
    held = {k: w for k, w in weights.items() if w != 0}
    modelled = set(model["names"]) if model is not None else set()
    risk_weights = {k: w for k, w in held.items() if k in modelled}
    in_history = [k for k in held if k in columns]
    volatility, var, etl = (None, None, None) if held else (0.0, 0.0, 0.0)   # all cash: no risk
    if risk_weights:
        volatility = ex_ante_risk(risk_weights, model)["volatility"]
    if in_history:
        length = len(columns[in_history[0]])
        series = [math.fsum(held[k] * columns[k][t] for k in in_history) for t in range(length)]
        var, etl = _tail(sorted(series), level)
    exposures = factor_share = None
    if factor_model is not None:
        known = {k: w for k, w in held.items() if k in set(factor_model["names"])}
        split = factor_risk_decomposition(known, factor_model)
        exposures = {row["factor"]: row["exposure"] for row in split["factors"]}
        factor_share = split["factor_share"]
    liquidity = None
    if volumes is not None and any(quantities.get(k) for k in held):
        liquidity = days_to_liquidate(
            {k: {"quantity": quantities[k], "value": values[k], "average_daily_volume": volumes.get(k)}
             for k in held if quantities.get(k)}, participation=participation)
    shape = concentration(held) if held else None
    largest = shape["largest"] if shape else None
    return {
        "gross_exposure": math.fsum(abs(w) for w in held.values()),
        "net_exposure": math.fsum(held.values()),
        "cash_weight": 1.0 - math.fsum(held.values()),
        "largest_position": largest,
        "largest_position_weight": abs(held[largest]) if largest else 0.0,
        "top_5_weight": shape["top_weights"]["5"] if shape else 0.0,
        "effective_holdings": shape["effective_holdings"] if shape else None,
        "herfindahl": shape["herfindahl"] if shape else None,
        "ex_ante_volatility": volatility,
        "var_1d": var,
        "expected_tail_loss_1d": etl,
        "factor_exposures": exposures,
        "factor_share_of_variance": factor_share,
        "liquidity_days": max((r["days"] for r in liquidity["holdings"] if r["days"] is not None), default=None)
        if liquidity else None,
        "value_weighted_liquidity_days": liquidity["value_weighted_days"] if liquidity else None,
        "sellable_within": liquidity["sellable_within"] if liquidity else None,
        "no_volume_data": liquidity["no_volume_data"] if liquidity else [],
        "unmodelled": sorted(k for k in held if k not in modelled),
        "unmodelled_weight": math.fsum(held[k] for k in held if k not in modelled),
        "no_return_history": sorted(k for k in held if k not in columns),
    }


def _change(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    change = {}
    for key, value in after.items():
        old = before.get(key)
        if isinstance(value, (int, float)) and isinstance(old, (int, float)) and not isinstance(value, bool):
            change[key] = value - old
        elif isinstance(value, Mapping) and isinstance(old, Mapping):
            change[key] = {k: value.get(k, 0.0) - old.get(k, 0.0) for k in sorted(set(value) | set(old))
                           if isinstance(value.get(k, 0.0), (int, float)) and isinstance(old.get(k, 0.0), (int, float))}
    return change


def _costs(orders: list[dict[str, Any]], columns: Mapping[str, list[float]], volumes: Mapping[str, Any] | None,
           spreads: Mapping[str, Any] | None, coefficient: float, nav: float) -> dict[str, Any]:
    """Half the relative spread plus square-root impact Y·σ·√(Q/V), each times the order's value."""

    rows = []
    for order in orders:
        holding, size = order["holding"], abs(order["value"])
        spread = _spread((spreads or {}).get(holding), holding)
        volume = _volume({"average_daily_volume": (volumes or {}).get(holding)})
        history = columns.get(holding)
        sigma = statistics.stdev(history) if history and len(history) > 1 else None
        impact = (square_root_impact(abs(order["quantity"]), volume, sigma, coefficient=coefficient)
                  if volume is not None and sigma is not None else None)
        spread_cost = 0.5 * spread * size if spread is not None else None
        impact_cost = impact["impact"] * size if impact else None
        known = [c for c in (spread_cost, impact_cost) if c is not None]
        order.update({
            "relative_spread": spread, "spread_cost": spread_cost, "daily_volatility": sigma,
            "average_daily_volume": volume, "participation": impact["participation"] if impact else None,
            "impact": impact["impact"] if impact else None, "impact_cost": impact_cost,
            "cost": math.fsum(known) if known else None,
            "cost_bps": math.fsum(known) / size * BASIS_POINTS if known else None,
            "missing_cost_data": [label for label, part in (("spread", spread_cost), ("impact", impact_cost))
                                  if part is None],
            "warnings": impact["warnings"] if impact else [],
        })
        rows.append(order)
    traded = math.fsum(abs(o["value"]) for o in rows)
    spread_total = math.fsum(o["spread_cost"] for o in rows if o["spread_cost"] is not None)
    impact_total = math.fsum(o["impact_cost"] for o in rows if o["impact_cost"] is not None)
    total = spread_total + impact_total
    return {
        "traded_value": traded,
        "spread": spread_total,
        "impact": impact_total,
        "total": total,
        "bps_of_traded_value": total / traded * BASIS_POINTS if traded else None,
        "share_of_nav": total / nav,
        "orders_missing_cost_data": [o["index"] for o in rows if o["missing_cost_data"]],
        "impact_coefficient": coefficient,
    }


def _limits(before: Mapping[str, Any], after: Mapping[str, Any], limits: Mapping[str, Any] | None,
            amber_at: float) -> dict[str, Any] | None:
    if limits is None:
        return None

    def metrics(state: Mapping[str, Any]) -> dict[str, Any]:
        return {k: state[k] for k in LIMIT_METRICS if state.get(k) is not None}

    was, now = limit_check(metrics(before), limits, amber_at=amber_at), limit_check(metrics(after), limits,
                                                                                    amber_at=amber_at)
    earlier = {c["metric"]: c for c in was["checks"]}
    for breach in now["breaches"]:
        old = earlier.get(breach["metric"])
        breach["new"] = old is None or old["status"] != "red"
    return {
        "status_before": was["status"],
        "status_after": now["status"],
        "after": now,
        "new_breaches": [b for b in now["breaches"] if b["new"]],
        "breaches_after": now["breaches"],
        "breaches_cleared": [c["metric"] for c in was["breaches"]
                             if c["metric"] not in {b["metric"] for b in now["breaches"]}],
    }


# The check ---------------------------------------------------------------------------------


def pre_trade_check(
    current_positions: Mapping[str, Mapping[str, Any]],
    proposed_orders: Sequence[Mapping[str, Any]],
    *,
    returns_history: Mapping[str, Any],
    covariance: Mapping[str, Any] | None = None,
    factor_model: Mapping[str, Any] | None = None,
    limits: Mapping[str, Any] | None = None,
    average_daily_volumes: Mapping[str, Any] | None = None,
    spreads: Mapping[str, Any] | None = None,
    nav: float,
    impact_coefficient: float = 1.0,
    participation: float = DEFAULT_PARTICIPATION,
    var_level: float = DEFAULT_VAR_LEVEL,
    amber_at: float = DEFAULT_AMBER_AT,
) -> dict[str, Any]:
    """Before and after of applying ``proposed_orders`` to ``current_positions``; nothing is placed.

    ``current_positions`` maps holding → ``quantity`` and ``price`` (or ``value``) in
    the NAV's currency, with an optional ``multiplier``. Each order has ``holding``,
    ``quantity`` (signed, or positive with ``side`` BUY or SELL) and optionally
    ``price``, which values a holding the portfolio does not yet price. An order for
    a holding with no price is refused and listed with the reason; the others are
    checked. Weights are value / ``nav``; NAV is unchanged by the orders, cash taking
    up the difference (negative cash is borrowing).

    * Ex-ante volatility √(wᵀΣw) (:func:`ibkr_etl.quant.holdings_risk.ex_ante_risk`)
      under ``covariance``, else ``factor_model``'s B F Bᵀ + D, else a Ledoit-Wolf
      covariance of ``returns_history`` (holding → aligned daily returns).
    * Historical VaR and expected tail loss at ``var_level`` (95%) of each state's
      weights applied to the same daily history (a holding with no history counts at
      0% and is listed as unmodelled).
    * Concentration (:func:`ibkr_etl.quant.crowding.concentration`), factor exposures
      Bᵀw with ``factor_model``, and days to liquidate at ``participation`` of
      ``average_daily_volumes`` (:func:`ibkr_etl.quant.liquidity.days_to_liquidate`).
    * Trading cost per order: half the relative spread (``spreads``: a number
      (ask − bid)/mid or a quote with bid and ask) times its value, plus square-root
      impact Y·σ·√(Q/V) (Almgren et al. 2005; Tóth et al. 2011) with σ the holding's
      daily volatility from the history and Y = ``impact_coefficient``.
    * Limits: :func:`ibkr_etl.quant.desk_risk.limit_check` before and after, with the
      breaches the orders would cause marked new.
    """

    nav = _number(nav, "The NAV")
    if nav <= 0:
        raise ModellingInputError("The NAV must be positive.")
    level = _number(var_level, "The VaR level")
    if not 0.5 <= level < 1.0:
        raise ModellingInputError("The VaR level must be at least 0.5 and below 1, such as 0.95.")
    coefficient = _number(impact_coefficient, "The impact coefficient")
    if coefficient < 0:
        raise ModellingInputError("The impact coefficient must be zero or more.")
    book = _book(current_positions)
    orders, refused = _orders(proposed_orders, book)
    if not orders:
        raise ModellingInputError("None of the orders can be checked: " + " ".join(r["reason"] for r in refused))
    unpriced = sorted(k for k, v in book.items() if v["price"] is None and v["quantity"])
    before_q = {k: v["quantity"] for k, v in book.items()}
    after_q = dict(before_q)
    for order in orders:
        after_q[order["holding"]] = after_q.get(order["holding"], 0.0) + order["quantity"]
    priced = {k: v for k, v in book.items() if v["price"] is not None}

    def valued(quantities: Mapping[str, float]) -> dict[str, float]:
        return {k: quantities.get(k, 0.0) * v["price"] * v["multiplier"] for k, v in priced.items()}

    before_v, after_v = valued(before_q), valued(after_q)
    before_w = {k: v / nav for k, v in before_v.items()}
    after_w = {k: v / nav for k, v in after_v.items()}
    history = _history(returns_history)
    needed = sorted(k for k in priced if k in history and (before_w[k] or after_w[k]))
    columns = dict(zip(*_clean_columns({k: history[k] for k in needed}))) if needed else {}
    model, model_label = _risk_model(covariance, factor_model, columns)
    common = {"model": model, "columns": columns, "factor_model": factor_model, "volumes": average_daily_volumes,
              "participation": participation, "level": level}
    before = _state(before_w, before_q, before_v, **common)
    after = _state(after_w, after_q, after_v, **common)
    costs = _costs(orders, columns, average_daily_volumes, spreads, coefficient, nav)
    for order in orders:   # at the holding's mark, like the weights; the order's value is at its own price
        mark = book[order["holding"]]
        order["weight_change"] = order["quantity"] * mark["price"] * mark["multiplier"] / nav
    names = sorted(set(before_w) | set(after_w), key=lambda k: (-abs(after_w.get(k, 0.0)), k))
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "notice": NOTICE,
        "nav": nav,
        "orders": orders,
        "refused_orders": refused,
        "all_orders_checked": not refused,
        "unpriced_positions": unpriced,
        "before": before,
        "after": after,
        "change": _change(before, after),
        "weights": [{"holding": k, "before": before_w.get(k, 0.0), "after": after_w.get(k, 0.0),
                     "change": after_w.get(k, 0.0) - before_w.get(k, 0.0)} for k in names
                    if before_w.get(k) or after_w.get(k)],
        "trading_cost": costs,
        "limits": _limits(before, after, limits, amber_at),
        "risk_model": model_label,
        "var_level": level,
        "history_days": len(next(iter(columns.values()))) if columns else 0,
        "method": (
            "The orders are applied to today's quantities at today's prices and cash takes up the difference, so NAV "
            f"is unchanged. Ex-ante volatility is from {model_label}; VaR and expected tail loss at {level:.0%} replay "
            "each state's weights over the same daily history; concentration, factor exposures and days to liquidate "
            "are measured on each state. Trading cost is half the bid-ask spread plus square-root market impact "
            "(impact coefficient × daily volatility × √(order ÷ average daily volume)), times the order's value."
        ),
        "disclaimer": DISCLAIMER,
    }
