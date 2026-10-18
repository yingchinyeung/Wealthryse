"""Benchmark and factor attribution: where a portfolio's return came from.

Pure standard-library functions over plain sequences and mappings, like
:mod:`ibkr_etl.modelling`:

* :func:`brinson_fachler` – one period's return over the benchmark split, segment by
  segment (sector, region, ...), into allocation, selection and interaction
  (Brinson and Fachler 1985).
* :func:`link_carino` and :func:`link_menchero` – those effects linked across periods so
  that they add up to the compounded excess return (Carino 1999; Menchero 2000).
* :func:`currency_attribution` – the same split for a portfolio of markets in several
  currencies reported in one base currency, with a currency allocation effect
  (Karnosky and Singer 1994, simplified as its docstring says).
* :func:`factor_attribution` – each factor's part of the return (beta × factor return),
  alpha and the residual, period by period and compounded over the window.
* :func:`style_analysis` – Sharpe's (1992) returns-based style analysis: the long-only mix
  of style indices that tracks the portfolio most closely, over the whole window or a
  rolling one.

Returns are simple returns (0.01 = 1%) and weights are fractions that sum to 1. Missing
or non-numeric values (None, NaN, infinity) are rejected with a message naming them,
never skipped, because skipping would misalign the series.
"""
from __future__ import annotations

import math
import statistics
from itertools import accumulate
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..modelling import DISCLAIMER, TRADING_DAYS, ModellingInputError, _dot, _matvec, ols

MODEL_VERSION = "quant-attribution-v1"
WEIGHT_TOLERANCE = 1e-4   # rounded weights are accepted, then rescaled to sum to exactly 1
LINK_TOLERANCE = 1e-8     # a period's effects must add up to its excess return
MIN_OBSERVATIONS = 12
STYLE_ITERATIONS = 5000
STYLE_TOLERANCE = 1e-10      # largest weight change per step at convergence
BRINSON_EFFECTS = ("allocation", "selection", "interaction")
HEDGE_NOTE = ("Currency weights that differ from the market weights are treated as forward hedges earning the "
              "spot currency move on the difference.")


# Input checks ------------------------------------------------------------------------


def _number(value: Any, what: str) -> float:
    if value is None or isinstance(value, (bool, str, bytes)):
        raise ModellingInputError(f"{what} is missing or not a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ModellingInputError(f"{what} is missing or not a number.") from None
    if not math.isfinite(number):
        raise ModellingInputError(f"{what} is missing or not a number.")
    return number


def _series(values: Iterable[Any], what: str) -> list[float]:
    return [_number(value, f"The {what} at position {index}") for index, value in enumerate(values, start=1)]


def _weights(weights: Mapping[Any, Any], side: str) -> dict[Any, float]:
    """Weights checked to add up to 100% and rescaled to exactly 1, so the effects add up exactly."""

    values = {key: _number(value, f"The {side} weight of {key}") for key, value in weights.items()}
    total = math.fsum(values.values())
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise ModellingInputError(
            f"The {side} weights add up to {total:.2%}, not 100%. Include cash and every other segment."
        )
    return {key: value / total for key, value in values.items()}


def _labels(dates: Sequence[Any] | None, count: int) -> list[Any]:
    if dates is None:
        return list(range(1, count + 1))
    if len(dates) != count:
        raise ModellingInputError(f"There are {len(dates)} dates for {count} returns.")
    return [str(day)[:10] for day in dates]


def _columns(named: Mapping[str, Sequence[Any]], length: int, what: str) -> tuple[list[str], list[list[float]]]:
    if not named:
        raise ModellingInputError(f"Give at least one {what}.")
    names, columns = list(named), []
    for name in names:
        column = _series(named[name], f"{name} return")
        if len(column) != length:
            raise ModellingInputError(
                f"The {name} returns have {len(column)} values but the portfolio has {length}; "
                "align them on the same dates first."
            )
        columns.append(column)
    return names, columns


def _log_slope(a: float, b: float) -> float:
    """(ln(1+a) − ln(1+b)) / (a − b), with its limit 1/(1+a) when a == b; stable when a ≈ b."""

    gap = (a - b) / (1.0 + b)
    return (math.log1p(gap) / gap if gap else 1.0) / (1.0 + b)


# 1. Brinson-Fachler ------------------------------------------------------------------


def brinson_fachler(
    portfolio_weights: Mapping[Any, Any],
    portfolio_returns: Mapping[Any, Any],
    benchmark_weights: Mapping[Any, Any],
    benchmark_returns: Mapping[Any, Any],
) -> dict[str, Any]:
    """One period's excess return split into allocation, selection and interaction by segment.

    Brinson and Fachler (1985), with interaction shown on its own (the usual three-effect form):

    * allocation (wp − wb)(rb − Rb): over- or underweighting segments that beat or trailed
      the whole benchmark;
    * selection wb(rp − rb): doing better than the benchmark inside a segment, at the
      benchmark's weight;
    * interaction (wp − wb)(rp − rb): the extra from holding more of the segments where
      selection worked.

    Each argument maps a segment (sector, region, ...) to a weight or to the period's
    return. Weights on each side must add up to 100% (to within 0.01%; they are then
    rescaled to exactly 1). A segment missing on one side has weight 0 there. A return is
    needed for every segment with a weight. A segment the portfolio does not hold takes
    rp = rb, so its selection and interaction are zero. A segment held but absent from the
    benchmark has no benchmark return unless ``benchmark_returns`` gives one (a sector proxy,
    say); without one it takes rb = Rb, so allocation and selection are zero and its whole
    effect shows as interaction, and ``notes`` says so. The totals add up to Rp − Rb.

    The result's ``portfolio_return``, ``benchmark_return`` and ``effects`` can be passed
    straight to :func:`link_carino` or :func:`link_menchero` as one period.
    """

    wp = _weights(portfolio_weights, "portfolio")
    wb = _weights(benchmark_weights, "benchmark")
    segments = sorted(set(wp) | set(wb), key=str)
    rb: dict[Any, float] = {}
    for segment in segments:
        value = benchmark_returns.get(segment)
        if value is None and not wb.get(segment, 0.0):
            continue  # not in the benchmark and no proxy return: unknown
        rb[segment] = _number(value, f"The benchmark return of {segment}")
    rp = {
        segment: _number(portfolio_returns.get(segment), f"The portfolio return of {segment}")
        for segment in segments if wp.get(segment, 0.0)
    }
    portfolio_total = math.fsum(wp[s] * rp[s] for s in rp)
    benchmark_total = math.fsum(wb[s] * rb[s] for s in segments if wb.get(s, 0.0))

    rows, effects, assumed, not_held = [], {}, [], []
    for segment in segments:
        weight_p, weight_b = wp.get(segment, 0.0), wb.get(segment, 0.0)
        bench = rb.get(segment, benchmark_total)
        port = rp.get(segment, bench)
        if segment not in rb:
            assumed.append(segment)
        if segment not in rp and weight_b:
            not_held.append(segment)
        active = weight_p - weight_b
        effect = {
            "allocation": active * (bench - benchmark_total),
            "selection": weight_b * (port - bench),
            "interaction": active * (port - bench),
        }
        effects[segment] = effect
        rows.append({
            "segment": segment,
            "portfolio_weight": weight_p,
            "benchmark_weight": weight_b,
            "portfolio_return": port,
            "benchmark_return": bench,
            "benchmark_return_assumed": segment not in rb,
            **effect,
            "total": math.fsum(effect.values()),
        })
    totals = {name: math.fsum(e[name] for e in effects.values()) for name in BRINSON_EFFECTS}
    totals["total"] = math.fsum(totals.values())
    notes = [
        f"{segment} is not in the benchmark and has no benchmark return, so the benchmark's total "
        "return stands in for it: all of its effect shows as interaction."
        for segment in assumed
    ]
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "portfolio_return": portfolio_total,
        "benchmark_return": benchmark_total,
        "excess_return": portfolio_total - benchmark_total,
        "segments": rows,
        "totals": totals,
        "effects": effects,
        "segments_not_in_benchmark": assumed,
        "segments_not_held": not_held,
        "notes": notes,
        "method": (
            "Brinson-Fachler (1985) attribution for one period. Allocation is the active weight in each "
            "segment times the segment's benchmark return less the whole benchmark's; selection is the "
            "benchmark weight times the portfolio's return in the segment less the benchmark's; interaction "
            "is the active weight times that same difference. They add up to the portfolio return less the "
            "benchmark return."
        ),
    }


# 2. Multi-period linking -------------------------------------------------------------


def _flatten(effects: Mapping[Any, Any], path: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    for key, value in effects.items():
        if isinstance(value, Mapping):
            yield from _flatten(value, (*path, key))
        else:
            yield (*path, key), value


def _nest(flat: Mapping[tuple, float]) -> dict[Any, Any]:
    nested: dict[Any, Any] = {}
    for path, value in flat.items():
        node = nested
        for key in path[:-1]:
            node = node.setdefault(key, {})
            if not isinstance(node, dict):
                raise ModellingInputError("The effects do not have the same layout in every period.")
        if isinstance(node.get(path[-1]), dict):
            raise ModellingInputError("The effects do not have the same layout in every period.")
        node[path[-1]] = value
    return nested


def _periods(periods: Iterable[Mapping[str, Any]]) -> list[tuple[Any, float, float, dict[tuple, float]]]:
    rows = []
    for index, period in enumerate(periods, start=1):
        label = period.get("period", index)
        rp = _number(period.get("portfolio_return"), f"The portfolio return of period {label}")
        rb = _number(period.get("benchmark_return"), f"The benchmark return of period {label}")
        if rp <= -1.0 or rb <= -1.0:
            raise ModellingInputError(f"Period {label} has a return of -100% or worse, which cannot be linked.")
        effects = {
            path: _number(value, f"The effect {' / '.join(map(str, path))} in period {label}")
            for path, value in _flatten(period.get("effects") or {})
        }
        added = math.fsum(effects.values())
        if abs(added - (rp - rb)) > LINK_TOLERANCE:
            raise ModellingInputError(
                f"In period {label} the effects add up to {added:.6%} but the excess return is {rp - rb:.6%}; "
                "linking needs effects that add up to each period's excess return."
            )
        rows.append((label, rp, rb, effects))
    if not rows:
        raise ModellingInputError("Linking needs at least one period.")
    return rows


def _compounded(returns: Iterable[float]) -> float:
    """∏(1 + r) − 1, through logs so small returns keep their relative precision."""

    return math.expm1(math.fsum(math.log1p(r) for r in returns))


def _compound(rows: Sequence[tuple]) -> tuple[float, float]:
    return _compounded(row[1] for row in rows), _compounded(row[2] for row in rows)


def _linked(rows: Sequence[tuple], coefficients: Sequence[float], method: str) -> dict[str, Any]:
    terms: dict[tuple, list[float]] = {}
    for (_, _, _, effects), coefficient in zip(rows, coefficients):
        for path, value in effects.items():
            terms.setdefault(path, []).append(coefficient * value)
    linked = {path: math.fsum(values) for path, values in terms.items()}
    by_effect: dict[Any, list[float]] = {}
    for path, value in linked.items():
        by_effect.setdefault(path[-1], []).append(value)
    portfolio, benchmark = _compound(rows)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "periods": len(rows),
        "portfolio_return": portfolio,
        "benchmark_return": benchmark,
        "excess_return": portfolio - benchmark,
        "arithmetic_excess_return": math.fsum(row[1] - row[2] for row in rows),
        "coefficients": [{"period": row[0], "coefficient": c} for row, c in zip(rows, coefficients)],
        "effects": _nest(linked),
        "totals": {name: math.fsum(values) for name, values in by_effect.items()},
        "total": math.fsum(linked.values()),
        "method": method,
    }


def link_carino(periods: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Carino (1999) logarithmic linking of single-period effects.

    Each period is a mapping with ``portfolio_return``, ``benchmark_return`` and ``effects``
    (a mapping, possibly nested, such as segment -> effect name -> value, whose values add
    up to that period's excess return), and optionally a ``period`` label; a
    :func:`brinson_fachler` or :func:`currency_attribution` result works as it is. Each
    period's effects are scaled by k_t / K, where k_t = [ln(1+Rp_t) − ln(1+Rb_t)] / (Rp_t − Rb_t)
    and K is the same for the compounded returns (both 1/(1+R) in the limit Rp = Rb), so the
    linked effects add up to (∏(1+Rp_t) − 1) − (∏(1+Rb_t) − 1). The result keeps the layout of
    ``effects`` and adds ``totals`` by effect name (the innermost key).
    """

    rows = _periods(periods)
    portfolio, benchmark = _compound(rows)
    scale = _log_slope(portfolio, benchmark)
    coefficients = [_log_slope(rp, rb) / scale for _, rp, rb, _ in rows]
    return _linked(rows, coefficients, (
        f"Carino (1999) logarithmic linking of {len(rows)} periods: each period's effects are scaled by the "
        "ratio of its log excess return per unit of excess return to the same ratio for the whole span, so "
        "they add up to the compounded portfolio return less the compounded benchmark return."
    ))


def link_menchero(periods: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Menchero (2000) optimised linking of single-period effects.

    Same input and output as :func:`link_carino`. Every period's effects are scaled by
    M + α_t, where M = [(Rp − Rb)/T] / [(1+Rp)^(1/T) − (1+Rb)^(1/T)] for the compounded returns
    over T periods (limit (1+Rp)^((T−1)/T) when Rp = Rb), and
    α_t = [(Rp − Rb) − M Σ(Rp_s − Rb_s)] (Rp_t − Rb_t) / Σ(Rp_s − Rb_s)² is the smallest correction
    that makes the linked effects add up to the compounded excess return. When every period's
    excess return is zero, α_t = 0.
    """

    rows = _periods(periods)
    portfolio, benchmark = _compound(rows)
    count = len(rows)
    # M through logs: A = (1+Rp)^(1/T), B = (1+Rb)^(1/T), L = ln(A/B); M = B^(T-1) (e^(TL) - 1) / (T (e^L - 1)).
    log_gap = math.log1p((portfolio - benchmark) / (1.0 + benchmark)) / count
    growth = math.exp(math.log1p(benchmark) / count)
    scale = growth ** (count - 1) * (math.expm1(count * log_gap) / (count * math.expm1(log_gap)) if log_gap else 1.0)
    excess = [rp - rb for _, rp, rb, _ in rows]
    squares = math.fsum(d * d for d in excess)
    shortfall = (portfolio - benchmark) - scale * math.fsum(excess)
    coefficients = [scale + (shortfall * d / squares if squares else 0.0) for d in excess]
    return _linked(rows, coefficients, (
        f"Menchero (2000) optimised linking of {count} periods: each period's effects are scaled by a common "
        "factor (the compounded excess return over the excess return of the average period) plus the smallest "
        "period-by-period correction that makes them add up to the compounded portfolio return less the "
        "compounded benchmark return."
    ))


# 3. Currency attribution -------------------------------------------------------------


def _code(currency: Any) -> str:
    return str(currency).strip().upper()


def _local_returns(
    returns: Mapping[Any, Any], currency_of: Mapping[Any, str], moves: Mapping[str, float], side: str
) -> tuple[dict[Any, float], dict[Any, float]]:
    """Base-currency returns, and the local returns they imply: (1 + base) / (1 + currency) − 1."""

    base, local = {}, {}
    for segment, value in returns.items():
        if segment not in currency_of or value is None:
            continue
        number = _number(value, f"The {side} return of {segment}")
        if number <= -1.0:
            raise ModellingInputError(f"The {side} return of {segment} is -100% or worse.")
        base[segment] = number
        local[segment] = (1.0 + number) / (1.0 + moves[currency_of[segment]]) - 1.0
    return base, local


def _currency_weights(
    weights: Mapping[Any, Any] | None, implied: Mapping[str, float], moves: Mapping[str, float], side: str
) -> dict[str, float]:
    if weights is None:
        return dict(implied)
    values = _weights({_code(code): value for code, value in weights.items()}, f"{side} currency")
    for code in values:
        if code not in moves:
            raise ModellingInputError(f"There is no return for {code}, which the {side} currency weights include.")
    return values


def currency_attribution(
    portfolio_weights: Mapping[Any, Any],
    portfolio_returns: Mapping[Any, Any],
    benchmark_weights: Mapping[Any, Any],
    benchmark_returns: Mapping[Any, Any],
    segment_currencies: Mapping[Any, Any],
    currency_returns: Mapping[Any, Any],
    *,
    base_currency: str,
    portfolio_currency_weights: Mapping[Any, Any] | None = None,
    benchmark_currency_weights: Mapping[Any, Any] | None = None,
) -> dict[str, Any]:
    """Excess return in the base currency split into market, selection and currency effects.

    After Karnosky and Singer (1994). Segments are markets (for example "HK equities",
    "US equities", "USD cash"); ``segment_currencies`` gives each one's currency, and
    ``currency_returns`` each currency's return against ``base_currency`` over the period
    (+0.004 means that currency rose 0.4% against the base; the base itself is 0). Weights
    are market weights summing to 1 on each side; returns are in the base currency. Each
    segment's base return is split into its local return, (1 + base)/(1 + currency) − 1, and
    the currency part. Then:

    * allocation, selection and interaction are :func:`brinson_fachler` on local returns;
    * currency allocation for each currency is (Wp − Wb) × its currency return, where W are
      currency weights. By default they are the market weights added up by currency
      (unhedged); ``portfolio_currency_weights`` / ``benchmark_currency_weights`` (currency ->
      weight, summing to 1) set them after hedges, and the difference from the market
      weights is treated as a forward overlay that earns the spot currency move;
    * currency compounding for each segment is (wp × rp_local − wb × rb_local) × its currency
      return: the currency move on the local gain or loss.

    The five effects add up to the base-currency portfolio return less the benchmark's.

    Simplifications: (1) there is no forward-premium (cash-rate) split. Karnosky and Singer
    measure market effects on local returns in excess of local cash rates and credit the
    interest differential to currency; here market effects use total local returns and
    currency effects the spot move only, and a hedge is assumed to earn exactly the spot move
    (forward rate = spot). With HKD pegged to USD the currency effects are small anyway; for
    other pairs the interest differential sits in the market effects instead of currency,
    and the total is unchanged. (2) Returns are simple, so the base return is
    (1 + local)(1 + currency) − 1 and the cross product local × currency, which a log-return
    version would not have, is reported on its own as currency compounding rather than
    hidden inside another effect.
    """

    base = _code(base_currency)
    moves = {base: 0.0}
    for code, value in currency_returns.items():
        code = _code(code)
        move = _number(value, f"The return of {code} against {base}")
        if code == base and move:
            raise ModellingInputError(f"{base} is the base currency, so its own currency return must be 0.")
        if move <= -1.0:
            raise ModellingInputError(f"The return of {code} against {base} is -100% or worse.")
        moves[code] = move
    wp = _weights(portfolio_weights, "portfolio")
    wb = _weights(benchmark_weights, "benchmark")
    currency_of = {}
    for segment in sorted(set(wp) | set(wb), key=str):
        if segment_currencies.get(segment) is None:
            raise ModellingInputError(f"Say which currency {segment} is priced in.")
        code = _code(segment_currencies[segment])
        if code not in moves:
            raise ModellingInputError(f"There is no return for {code} against {base}, which {segment} is priced in.")
        currency_of[segment] = code
    base_p, local_p = _local_returns(portfolio_returns, currency_of, moves, "portfolio")
    base_b, local_b = _local_returns(benchmark_returns, currency_of, moves, "benchmark")
    market = brinson_fachler(wp, local_p, wb, local_b)

    implied_p: dict[str, float] = {}
    implied_b: dict[str, float] = {}
    for segment, code in currency_of.items():
        implied_p[code] = implied_p.get(code, 0.0) + wp.get(segment, 0.0)
        implied_b[code] = implied_b.get(code, 0.0) + wb.get(segment, 0.0)
    held_p = _currency_weights(portfolio_currency_weights, implied_p, moves, "portfolio")
    held_b = _currency_weights(benchmark_currency_weights, implied_b, moves, "benchmark")
    codes = sorted(set(held_p) | set(held_b) | set(implied_p))

    currencies, currency_effects = [], {}
    for code in codes:
        effect = (held_p.get(code, 0.0) - held_b.get(code, 0.0)) * moves[code]
        currency_effects[code] = {"currency_allocation": effect}
        currencies.append({"currency": code, "portfolio_weight": held_p.get(code, 0.0),
                           "benchmark_weight": held_b.get(code, 0.0), "currency_return": moves[code],
                           "currency_allocation": effect})
    segments, segment_effects = [], {}
    for row in market["segments"]:
        segment = row["segment"]
        move = moves[currency_of[segment]]
        compounding = (row["portfolio_weight"] * row["portfolio_return"]
                       - row["benchmark_weight"] * row["benchmark_return"]) * move
        effect = {name: row[name] for name in BRINSON_EFFECTS} | {"currency_compounding": compounding}
        segment_effects[segment] = effect
        segments.append({
            "segment": segment, "currency": currency_of[segment], "currency_return": move,
            "portfolio_weight": row["portfolio_weight"], "benchmark_weight": row["benchmark_weight"],
            "portfolio_local_return": row["portfolio_return"], "benchmark_local_return": row["benchmark_return"],
            "benchmark_return_assumed": row["benchmark_return_assumed"],
            **effect, "total": math.fsum(effect.values()),
        })

    def overlay(held: Mapping[str, float], implied: Mapping[str, float]) -> float:
        return math.fsum((held.get(c, 0.0) - implied.get(c, 0.0)) * moves[c] for c in codes)

    portfolio_total = math.fsum(wp[s] * base_p[s] for s in wp if wp[s]) + overlay(held_p, implied_p)
    benchmark_total = math.fsum(wb[s] * base_b[s] for s in wb if wb[s]) + overlay(held_b, implied_b)
    totals = {name: market["totals"][name] for name in BRINSON_EFFECTS}
    totals["currency_allocation"] = math.fsum(e["currency_allocation"] for e in currency_effects.values())
    totals["currency_compounding"] = math.fsum(e["currency_compounding"] for e in segment_effects.values())
    totals["total"] = math.fsum(totals.values())
    hedged = portfolio_currency_weights is not None or benchmark_currency_weights is not None
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "base_currency": base,
        "portfolio_return": portfolio_total,
        "benchmark_return": benchmark_total,
        "excess_return": portfolio_total - benchmark_total,
        "portfolio_local_return": market["portfolio_return"],
        "benchmark_local_return": market["benchmark_return"],
        "segments": segments,
        "currencies": currencies,
        "totals": totals,
        "effects": {"segments": segment_effects, "currencies": currency_effects},
        "segments_not_in_benchmark": market["segments_not_in_benchmark"],
        "segments_not_held": market["segments_not_held"],
        "notes": market["notes"] + ([HEDGE_NOTE] if hedged else []),
        "method": (
            f"Karnosky-Singer (1994) style attribution in {base}. Each market's {base} return is split into its "
            "local return and the currency's move. Allocation, selection and interaction are Brinson-Fachler on "
            "local returns; currency allocation is the difference in currency weights times each currency's "
            "return; currency compounding is each currency's move on the local gain or loss. Cash-rate "
            "differences are not split out: hedges earn the spot move and market effects use total local returns."
        ),
    }


# 4. Factor attribution ---------------------------------------------------------------


def factor_attribution(
    portfolio_excess_returns: Sequence[Any],
    factor_returns: Mapping[str, Sequence[Any]],
    *,
    periods_per_year: int = TRADING_DAYS,
    dates: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Return explained by each factor, alpha and the residual, per period and over the window.

    ``portfolio_excess_returns`` are the portfolio's returns less the risk-free rate;
    ``factor_returns`` maps factor name to a sequence on the same periods (Fama-French
    factors are already excess returns). Exposures come from an OLS regression with an
    intercept (:func:`ibkr_etl.modelling.ols`). Each period's excess return y_t is split
    exactly into Σ beta_k × f_k,t, alpha and the residual y_t − alpha − Σ beta_k × f_k,t. Over the
    window, the compounded excess return ∏(1 + y_t) − 1 is split by Carino (1999) linking
    against zero: each period's parts are scaled by [ln(1 + y_t)/y_t] / [ln(1 + Y)/Y], so the
    cumulative parts add up to the compounded total. ``contribution_annual`` is the average
    part per period times ``periods_per_year`` (arithmetic, like the factor model page).
    """

    y = _series(portfolio_excess_returns, "portfolio excess return")
    names, columns = _columns(factor_returns, len(y), "factor")
    for name, column in zip(names, columns):
        if max(column) == min(column):
            raise ModellingInputError(f"The {name} factor does not move over this window, so no exposure can be fitted.")
    n, k = len(y), len(names)
    if n < max(MIN_OBSERVATIONS, k + 3):
        raise ModellingInputError(
            f"Factor attribution with {k} factors needs at least {max(MIN_OBSERVATIONS, k + 3)} returns; there are {n}."
        )
    if min(y) <= -1.0:
        raise ModellingInputError("An excess return of -100% or worse cannot be compounded.")
    labels = _labels(dates, n)
    fit = ols(y, columns)
    alpha, betas = fit["coefficients"][0], fit["coefficients"][1:]

    rows = []
    for t in range(n):
        parts = {name: beta * column[t] for name, beta, column in zip(names, betas, columns)}
        explained = math.fsum(parts.values())
        rows.append({"period": labels[t], "total": y[t], "alpha": alpha,
                     "residual": y[t] - alpha - explained, "factors": parts})
    compounded = _compounded(y)
    scale = _log_slope(compounded, 0.0)
    links = [_log_slope(value, 0.0) / scale for value in y]

    def cumulative(values: Sequence[float]) -> float:
        return math.fsum(c * v for c, v in zip(links, values))

    exposures = []
    for index, name in enumerate(names):
        mean_factor = statistics.fmean(columns[index])
        exposures.append({
            "factor": name,
            "beta": betas[index],
            "t_statistic": fit["t_statistics"][index + 1],
            "factor_return_annual": mean_factor * periods_per_year,
            "contribution_annual": betas[index] * mean_factor * periods_per_year,
            "cumulative_contribution": cumulative([row["factors"][name] for row in rows]),
        })
    residuals = [row["residual"] for row in rows]
    alpha_cumulative = cumulative([alpha] * n)
    residual_cumulative = cumulative(residuals)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "observations": n,
        "window_start": labels[0],
        "window_end": labels[-1],
        "periods_per_year": periods_per_year,
        "cumulative_excess_return": compounded,
        "average_excess_return_annual": statistics.fmean(y) * periods_per_year,
        "exposures": exposures,
        "alpha": {
            "per_period": alpha,
            "annual": alpha * periods_per_year,
            "t_statistic": fit["t_statistics"][0],
            "cumulative_contribution": alpha_cumulative,
        },
        "residual": {
            "cumulative_contribution": residual_cumulative,
            "volatility_annual": (fit["residual_volatility"] * math.sqrt(periods_per_year)
                                  if fit["residual_volatility"] is not None else None),
        },
        "factors_cumulative": math.fsum(x["cumulative_contribution"] for x in exposures),
        "r_squared": fit["r_squared"],
        "adjusted_r_squared": fit["adjusted_r_squared"],
        "periods": rows,
        "method": (
            f"OLS regression of {n} excess returns on {k} factor returns with an intercept. Each period's return "
            "is split into beta times each factor's return, alpha (the intercept) and the residual left over. "
            "The compounded excess return over the window is split the same way with Carino (1999) linking, so "
            "the parts add up to the total."
        ),
        "disclaimer": DISCLAIMER,
    }


# 5. Returns-based style analysis -----------------------------------------------------


def _project_simplex(values: Sequence[float]) -> list[float]:
    """Closest point with weights ≥ 0 summing to 1 (exact, by sorting; Held et al. 1974)."""

    total, shift = 0.0, 0.0
    for count, value in enumerate(sorted(values, reverse=True), start=1):
        total += value
        candidate = (total - 1.0) / count
        if value <= candidate:
            break
        shift = candidate
    return [max(0.0, v - shift) for v in values]


def _simplex_least_squares(
    cov: Sequence[Sequence[float]], target: Sequence[float], start: Sequence[float] | None = None
) -> list[float]:
    """Weights ≥ 0 summing to 1 minimising w'Σw − 2c'w: accelerated projected gradient with restarts."""

    k = len(target)
    bound = max(sum(abs(v) for v in row) for row in cov)  # Gershgorin: at least the largest eigenvalue
    weights = list(start) if start else [1.0 / k] * k
    if k == 1 or bound <= 0:
        return [1.0] if k == 1 else weights
    step = 1.0 / (2.0 * bound)
    point, t = list(weights), 1.0
    for _ in range(STYLE_ITERATIONS):
        gradient = [2.0 * (g - c) for g, c in zip(_matvec(cov, point), target)]
        updated = _project_simplex([p - step * g for p, g in zip(point, gradient)])
        change = [u - w for u, w in zip(updated, weights)]
        if max(abs(v) for v in change) < STYLE_TOLERANCE:
            return updated
        if _dot([p - u for p, u in zip(point, updated)], change) > 0:
            point, t = updated, 1.0  # momentum is pointing uphill: restart (O'Donoghue and Candès 2015)
        else:
            t_next = (1.0 + math.sqrt(1.0 + 4.0 * t * t)) / 2.0
            point = [u + (t - 1.0) / t_next * d for u, d in zip(updated, change)]
            t = t_next
        weights = updated
    return weights


def _prefix_moments(columns: Sequence[Sequence[float]]) -> tuple[list[float], list[list[float]], dict]:
    """Running sums of each centred column and of each pair's products, so any window costs O(k²)."""

    centres = [statistics.fmean(c) for c in columns]
    centred = [[v - m for v in c] for c, m in zip(columns, centres)]
    singles = [list(accumulate(c, initial=0.0)) for c in centred]
    pairs = {
        (i, j): list(accumulate((a * b for a, b in zip(centred[i], centred[j])), initial=0.0))
        for i in range(len(columns)) for j in range(i, len(columns))
    }
    return centres, singles, pairs


def _window_moments(prefix: tuple, start: int, end: int) -> tuple[list[float], list[list[float]]]:
    centres, singles, pairs = prefix
    n = end - start
    sums = [s[end] - s[start] for s in singles]
    means = [c + s / n for c, s in zip(centres, sums)]
    cov = [[0.0] * len(sums) for _ in sums]
    for (i, j), running in pairs.items():
        cov[i][j] = cov[j][i] = (running[end] - running[start] - sums[i] * sums[j] / n) / (n - 1)
    return means, cov


def _style_fit(prefix: tuple, start: int, end: int, initial: Sequence[float] | None) -> dict[str, Any]:
    means, cov = _window_moments(prefix, start, end)
    k = len(cov) - 1  # the portfolio is the last column
    styles = [row[:k] for row in cov[:k]]
    target = [cov[i][k] for i in range(k)]
    weights = _simplex_least_squares(styles, target, initial)
    variance = cov[k][k]
    tracking = max(0.0, variance - 2.0 * _dot(target, weights) + _dot(weights, _matvec(styles, weights)))
    return {
        "weights": weights,
        "r_squared": 1.0 - tracking / variance if variance > 0 else None,
        "tracking_variance": tracking,
        "selection_return": means[k] - _dot(weights, means[:k]),
    }


def style_analysis(
    portfolio_returns: Sequence[Any],
    style_index_returns: Mapping[str, Sequence[Any]],
    window: int | None = None,
    *,
    step: int | None = None,
    dates: Sequence[Any] | None = None,
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, Any]:
    """Sharpe (1992) returns-based style analysis.

    Finds the weights on the style indices (each ≥ 0, summing to 1) that minimise the
    variance of the portfolio's return less the weighted index return (the tracking
    variance), by accelerated projected gradient on the simplex. R² = 1 − tracking variance
    / portfolio variance is the share of the portfolio's variance the style mix explains;
    the selection return is the average of the difference, annualised by
    ``periods_per_year``. With ``window``, the fit is repeated on rolling windows of that many
    periods, every ``step`` periods (default window // 20, at least 1), always including the
    window that ends on the last period; each rolling fit starts from the previous one's
    weights. A fit costs about a millisecond per few indices, so a step of a week or more
    keeps ten years of daily data well under a second. A constant index (cash at a fixed
    rate) is allowed.
    """

    y = _series(portfolio_returns, "portfolio return")
    names, columns = _columns(style_index_returns, len(y), "style index")
    n, k = len(y), len(names)
    minimum = max(MIN_OBSERVATIONS, k + 2)
    if n < minimum:
        raise ModellingInputError(f"Style analysis with {k} indices needs at least {minimum} returns; there are {n}.")
    if all(max(column) == min(column) for column in columns):
        raise ModellingInputError("None of the style indices moves over this window, so there is no style to fit.")
    labels = _labels(dates, n)
    prefix = _prefix_moments([*columns, y])
    full = _style_fit(prefix, 0, n, None)

    def described(fit: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "weights": dict(zip(names, fit["weights"])),
            "r_squared": fit["r_squared"],
            "tracking_error_annual": math.sqrt(fit["tracking_variance"] * periods_per_year),
            "selection_return_annual": fit["selection_return"] * periods_per_year,
        }

    rolling = None
    if window is not None:
        window = int(window)
        if not minimum <= window <= n:
            raise ModellingInputError(f"The rolling window must be between {minimum} and {n} periods.")
        step = max(1, int(step) if step else window // 20)
        ends = list(range(window, n + 1, step))
        if ends[-1] != n:
            ends.append(n)
        fits, previous = [], full["weights"]
        for end in ends:
            fit = _style_fit(prefix, end - window, end, previous)
            previous = fit["weights"]
            fits.append({"start": labels[end - window], "end": labels[end - 1], **described(fit)})
        rolling = {"window": window, "step": step, "fits": fits}
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "observations": n,
        "window_start": labels[0],
        "window_end": labels[-1],
        "styles": names,
        **described(full),
        "rolling": rolling,
        "method": (
            f"Sharpe (1992) returns-based style analysis over {n} periods: the long-only, fully invested mix of "
            f"{k} style indices whose return tracks the portfolio's most closely (smallest variance of the "
            "difference), found by projected gradient on the weights. R² is the share of the portfolio's variance "
            "the mix explains; the selection return is the average difference, annualised."
            + (f" Rolling fits use {rolling['window']}-period windows every {rolling['step']} periods." if rolling else "")
        ),
        "disclaimer": DISCLAIMER,
    }
