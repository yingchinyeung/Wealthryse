"""Execution analysis: what trading cost, and how to trade a large order.

Pure standard-library functions, like :mod:`ibkr_etl.modelling`. Prices, commissions
and costs are in one currency, which the caller converts to first. Throughout, a
positive cost means the execution was worse for the account, for buys and sells.

* :func:`implementation_shortfall` – Perold's (1988) implementation shortfall of an
  order, split into delay cost, trading cost, commission and the opportunity cost of
  any quantity left unfilled, in money and basis points.
* :func:`vwap_slippage` – the average fill against the market's volume-weighted price
  over the order's interval.
* :func:`spread_paid` – the effective half-spread of a fill against the quote midpoint.
* :func:`square_root_impact` – the square-root law of market impact,
  impact ≈ Y·σ·√(Q/V) (Almgren, Thum, Hauptmann and Li 2005; Tóth et al. 2011).
* :func:`fit_impact_coefficient` – Y estimated from the account's own measured costs.
* :func:`almgren_chriss_schedule` – the Almgren and Chriss (2000) optimal schedule for
  working a large order, with its expected cost and variance against an even (TWAP)
  schedule.

Impact estimates and schedules are hypothetical models and say so.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError

MODEL_VERSION = "execution-analysis-v1"
BASIS_POINTS = 10_000.0
IMPACT_MIN_TRADES = 10
SQUARE_ROOT_TESTED_PARTICIPATION = 0.1  # the law is best measured below a tenth of daily volume


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any, label: str) -> float:
    number = _number(value)
    if number is None or number <= 0:
        raise ModellingInputError(f"{label} must be a positive number.")
    return number


def _non_negative(value: Any, label: str) -> float:
    number = _number(value)
    if number is None or number < 0:
        raise ModellingInputError(f"{label} must be zero or a positive number.")
    return number


def _side_sign(side: Any) -> float:
    text = str(side or "").strip().upper()
    if text.startswith("BUY"):
        return 1.0
    if text.startswith("SELL"):
        return -1.0
    raise ModellingInputError("The side must be BUY or SELL.")


def _amount(value: float, base: float) -> dict[str, float]:
    return {"amount": value, "bps": value / base * BASIS_POINTS}


def _fills(fills: Sequence[Mapping[str, Any]]) -> tuple[float, float, float]:
    """Filled quantity, money spent or received, and commission (as a cost) of the fills."""

    filled = traded = commission = 0.0
    for index, fill in enumerate(fills, start=1):
        quantity, price = _number(fill.get("quantity")), _number(fill.get("price"))
        if not quantity or price is None or price <= 0:
            raise ModellingInputError(f"Fill {index} needs a quantity and a positive price.")
        if fill.get("commission") is not None:
            charge = _number(fill.get("commission"))
            if charge is None:
                raise ModellingInputError(f"Fill {index} has a commission that is not a number.")
            commission += abs(charge)  # IBKR reports commission as a negative amount
        filled += abs(quantity)
        traded += abs(quantity) * price
    return filled, traded, commission


# 1. Implementation shortfall -----------------------------------------------------------


def implementation_shortfall(
    order: Mapping[str, Any],
    fills: Sequence[Mapping[str, Any]],
    *,
    decision_price: float,
    arrival_price: float,
    final_price: float,
) -> dict[str, Any]:
    """Perold (1988) implementation shortfall, in the expanded form of Kissell (2006).

    ``order`` has ``side`` and the intended ``quantity`` (and optionally an
    order-level ``commission``); each fill has ``quantity``, ``price`` and optionally
    ``commission``. With s = +1 for a buy and −1 for a sale, X ordered, Q filled at
    prices pⱼ, decision price P_d, arrival price P_a and final price P_f:

    * delay cost = s·Q·(P_a − P_d), the move between deciding and reaching the market;
    * trading cost = s·Σqⱼ(pⱼ − P_a), the fills against the arrival price;
    * commission;
    * opportunity cost = s·(X − Q)·(P_f − P_d), the move missed on what was not filled.

    They sum to the total: the paper portfolio traded in full at P_d less the real
    one, s·(Σqⱼpⱼ + (X − Q)·P_f − X·P_d) + commission. Basis points are of X·P_d.
    """

    sign = _side_sign(order.get("side"))
    ordered = _positive(order.get("quantity"), "The order quantity")
    decision = _positive(decision_price, "The decision price")
    arrival = _positive(arrival_price, "The arrival price")
    final = _positive(final_price, "The final price")
    filled, traded, commission = _fills(fills)
    if order.get("commission") is not None:
        charge = _number(order["commission"])
        if charge is None:
            raise ModellingInputError("The order commission is not a number.")
        commission += abs(charge)
    if filled > ordered * (1.0 + 1e-9):
        raise ModellingInputError("The fills add up to more than the order quantity.")
    unfilled = ordered - filled
    paper = ordered * decision
    delay = sign * filled * (arrival - decision)
    trading = sign * (traded - filled * arrival)
    opportunity = sign * unfilled * (final - decision)
    total = sign * (traded + unfilled * final - ordered * decision) + commission
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Implementation shortfall (Perold 1988) is the gain of a paper portfolio that traded the whole order at "
            "the decision price less the gain of the real one, valued at the final price. It splits into delay cost "
            "(decision to arrival on the quantity filled), trading cost (the fills against the arrival price), "
            "commission, and opportunity cost (the move from the decision price to the final price on the quantity "
            "not filled). Positive means cost, for buys and sells. Basis points are of the order's value at the "
            "decision price."
        ),
        "side": "BUY" if sign > 0 else "SELL",
        "ordered_quantity": ordered,
        "filled_quantity": filled,
        "unfilled_quantity": max(0.0, unfilled),
        "fill_rate": filled / ordered,
        "average_fill_price": traded / filled if filled else None,
        "decision_price": decision,
        "arrival_price": arrival,
        "final_price": final,
        "paper_value": paper,
        "components": {
            "delay": _amount(delay, paper),
            "trading": _amount(trading, paper),
            "commission": _amount(commission, paper),
            "opportunity": _amount(opportunity, paper),
        },
        "total": _amount(total, paper),
    }


# 2. Slippage against VWAP and the spread -------------------------------------------------


def vwap_slippage(
    fills: Sequence[Mapping[str, Any]],
    interval_vwap: float,
    *,
    side: str | None = None,
) -> dict[str, Any]:
    """Average fill price against the market VWAP over the order's interval.

    The side comes from ``side`` or, when every fill carries the same one, from the
    fills. Slippage is s·(average fill − VWAP), in money over the filled quantity and
    in basis points of VWAP; positive means the fills were worse than VWAP.
    """

    vwap = _positive(interval_vwap, "The interval VWAP")
    if not fills:
        raise ModellingInputError("VWAP slippage needs at least one fill.")
    if side is None:
        sides = {_side_sign(fill.get("side")) for fill in fills}
        if len(sides) != 1:
            raise ModellingInputError("The fills are not all on the same side; pass the side.")
        sign = sides.pop()
    else:
        sign = _side_sign(side)
    filled, traded, commission = _fills(fills)
    average = traded / filled
    slippage = sign * (average - vwap)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "side": "BUY" if sign > 0 else "SELL",
        "filled_quantity": filled,
        "average_fill_price": average,
        "interval_vwap": vwap,
        "slippage_amount": slippage * filled,
        "slippage_bps": slippage / vwap * BASIS_POINTS,
        "commission": commission,
        "method": (
            "Average fill price against the market's volume-weighted average price over the same interval: what a "
            "buyer paid above it, or a seller received below it. Positive means worse than VWAP."
        ),
    }


def spread_paid(fill_price: float, bid: float, ask: float, side: str) -> dict[str, Any]:
    """Effective half-spread of one fill: s·(fill − mid)/mid, against the quoted half-spread.

    The effective spread is the standard measure of Lee and Ready (1991) and
    Bessembinder (2003); this returns its half, the cost of crossing from the
    midpoint. A crossed quote (bid above ask) is refused.
    """

    sign = _side_sign(side)
    price = _positive(fill_price, "The fill price")
    bid, ask = _positive(bid, "The bid"), _positive(ask, "The ask")
    if bid > ask:
        raise ModellingInputError("The bid is above the ask; the quote is crossed.")
    mid = (bid + ask) / 2.0
    effective = sign * (price - mid) / mid
    quoted = (ask - bid) / 2.0 / mid
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "side": "BUY" if sign > 0 else "SELL",
        "mid": mid,
        "effective_half_spread": effective,
        "effective_half_spread_bps": effective * BASIS_POINTS,
        "quoted_half_spread": quoted,
        "quoted_half_spread_bps": quoted * BASIS_POINTS,
        "share_of_quoted": effective / quoted if quoted > 0 else None,
    }


# 3. Market impact ----------------------------------------------------------------------


def square_root_impact(
    quantity: float,
    average_daily_volume: float,
    daily_volatility: float,
    *,
    coefficient: float = 1.0,
    price: float | None = None,
) -> dict[str, Any]:
    """Expected price impact of trading ``quantity``: Y·σ·√(Q/V), as a fraction of price.

    σ is the daily return volatility (0.02 = 2%), V the average daily volume in the
    same units as Q, and Y the coefficient, of order 1 across markets (Almgren et al.
    2005; Tóth et al. 2011); :func:`fit_impact_coefficient` fits it to the account's
    own trades. With ``price`` the cost is also given in money for the whole quantity.
    """

    if _number(quantity) is None:
        raise ModellingInputError("The quantity must be a number.")
    size = abs(_number(quantity))
    volume = _positive(average_daily_volume, "The average daily volume")
    sigma = _non_negative(daily_volatility, "The daily volatility")
    y = _non_negative(coefficient, "The impact coefficient")
    participation = size / volume
    impact = y * sigma * math.sqrt(participation)
    warnings = []
    if participation > SQUARE_ROOT_TESTED_PARTICIPATION:
        warnings.append(
            f"The order is {participation:.0%} of a day's volume. The square-root law is best measured below "
            f"{SQUARE_ROOT_TESTED_PARTICIPATION:.0%}; spread a larger order over several days."
        )
    cost = None
    if price is not None:
        cost = impact * _positive(price, "The price") * size
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Square-root law of market impact: the price moves by about Y × daily volatility × √(order size ÷ "
            "average daily volume) (Almgren et al. 2005; Tóth et al. 2011)."
        ),
        "participation": participation,
        "coefficient": y,
        "impact": impact,
        "impact_bps": impact * BASIS_POINTS,
        "cost_amount": cost,
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
    }


def fit_impact_coefficient(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Least-squares Y from measured costs: cost = Y·σ√(Q/V), a line through the origin.

    Each record has ``cost`` (the measured trading cost as a fraction of price,
    positive for a cost, for example the fill against the arrival price),
    ``quantity``, ``average_daily_volume`` and ``daily_volatility``. Records with a
    missing or NaN value or a volume that is not positive are skipped and counted.
    Fewer than 10 usable trades are refused. R² is the uncentred one, as the line
    has no intercept.
    """

    xs, costs, skipped = [], [], 0
    for record in records:
        cost = _number(record.get("cost"))
        quantity = _number(record.get("quantity"))
        volume = _number(record.get("average_daily_volume"))
        sigma = _number(record.get("daily_volatility"))
        if cost is None or quantity is None or volume is None or volume <= 0 or sigma is None or sigma < 0:
            skipped += 1
            continue
        xs.append(sigma * math.sqrt(abs(quantity) / volume))
        costs.append(cost)
    n = len(xs)
    if n < IMPACT_MIN_TRADES:
        raise ModellingInputError(
            f"Fitting the impact coefficient needs at least {IMPACT_MIN_TRADES} trades with a measured cost; "
            f"there are {n}."
        )
    sxx = sum(x * x for x in xs)
    if sxx <= 0:
        raise ModellingInputError("None of the trades had any size or volatility, so impact cannot be fitted.")
    y = sum(x * c for x, c in zip(xs, costs)) / sxx
    residuals = [c - y * x for x, c in zip(xs, costs)]
    ssr = sum(r * r for r in residuals)
    error = math.sqrt(ssr / (n - 1) / sxx)
    total = sum(c * c for c in costs)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Least squares through the origin of each trade's measured cost on daily volatility × √(size ÷ average "
            "daily volume). The standard error assumes independent trades with equal noise."
        ),
        "coefficient": y,
        "standard_error": error,
        "t_statistic": y / error if error > 0 else None,
        "observations": n,
        "skipped": skipped,
        "r_squared_uncentred": 1.0 - ssr / total if total > 0 else None,
        "residual_volatility": statistics.pstdev(residuals) if n > 1 else None,
    }


# 4. Optimal execution ------------------------------------------------------------------


def _acosh_one_plus(z: float) -> float:
    """acosh(1 + z) without losing precision when z is tiny."""

    return math.log1p(z + math.sqrt(z * (z + 2.0)))


def _sinh_ratio(a: float, b: float) -> float:
    """sinh(a)/sinh(b) for 0 ≤ a ≤ b, without overflow for large b."""

    if b > 20.0:
        return math.exp(a - b) * (-math.expm1(-2.0 * a)) / (-math.expm1(-2.0 * b))
    return math.sinh(a) / math.sinh(b)


def _schedule_cost(
    holdings: Sequence[float], tau: float, sigma: float, eta_tilde: float, gamma: float
) -> tuple[float, float]:
    trades = [a - b for a, b in zip(holdings, holdings[1:])]
    quantity = holdings[0]
    expected = 0.5 * gamma * quantity * quantity + eta_tilde / tau * sum(n * n for n in trades)
    variance = sigma * sigma * tau * sum(x * x for x in holdings[1:])
    return expected, variance


def almgren_chriss_schedule(
    quantity: float,
    periods: int,
    *,
    volatility: float,
    temporary_impact: float,
    permanent_impact: float,
    risk_aversion: float,
    period_length: float = 1.0,
) -> dict[str, Any]:
    """Almgren and Chriss (2000) optimal schedule to sell ``quantity`` over ``periods``.

    The model is arithmetic: ``volatility`` σ is the price's standard deviation in
    money per share per √(unit of time) (for daily periods, price × daily return
    volatility); ``temporary_impact`` η is money per share per (share per unit of
    time); ``permanent_impact`` γ is money per share per share traded; and
    ``risk_aversion`` λ weighs variance against expected cost (minimising E + λV).
    The fixed half-spread term is left out. With τ the period length, T = Nτ,
    η̃ = η − γτ/2 and κ solving 2(cosh κτ − 1)/τ² = λσ²/η̃, the holdings are
    xₖ = sinh(κ(T − tₖ))/sinh(κT)·X; λ → 0 gives the even (TWAP) schedule. For a
    purchase the same schedule applies with the trades read as buys.
    """

    size = _positive(quantity, "The quantity")
    if isinstance(periods, bool) or int(periods) != periods or periods < 1:
        raise ModellingInputError("The number of periods must be a whole number of at least 1.")
    n = int(periods)
    tau = _positive(period_length, "The period length")
    sigma = _non_negative(volatility, "The volatility")
    eta = _positive(temporary_impact, "The temporary impact")
    gamma = _non_negative(permanent_impact, "The permanent impact")
    lam = _non_negative(risk_aversion, "The risk aversion")
    eta_tilde = eta - 0.5 * gamma * tau
    if eta_tilde <= 0:
        raise ModellingInputError(
            "The temporary impact must be larger than half the permanent impact × the period length."
        )
    horizon = n * tau
    kappa = _acosh_one_plus(lam * sigma * sigma / eta_tilde * tau * tau / 2.0) / tau
    times = [k * tau for k in range(n + 1)]
    if kappa * horizon < 1e-9:
        holdings = [size * (horizon - t) / horizon for t in times]
    else:
        holdings = [size * _sinh_ratio(kappa * (horizon - t), kappa * horizon) for t in times]
    holdings[-1] = 0.0
    trades = [a - b for a, b in zip(holdings, holdings[1:])]
    expected, variance = _schedule_cost(holdings, tau, sigma, eta_tilde, gamma)
    even = [size * (n - k) / n for k in range(n + 1)]
    even_expected, even_variance = _schedule_cost(even, tau, sigma, eta_tilde, gamma)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Almgren and Chriss (2000): the schedule that minimises expected impact cost plus risk aversion × the "
            "variance of the cost, with linear temporary and permanent impact and an arithmetic random walk for the "
            "price. More risk aversion trades faster at the start; none gives an even (TWAP) schedule."
        ),
        "quantity": size,
        "periods": n,
        "period_length": tau,
        "kappa": kappa,
        "time_scale": 1.0 / kappa if kappa > 0 else None,
        "times": times,
        "holdings": holdings,
        "trades": trades,
        "expected_cost": expected,
        "variance": variance,
        "standard_deviation": math.sqrt(variance),
        "objective": expected + lam * variance,
        "twap": {
            "expected_cost": even_expected,
            "variance": even_variance,
            "standard_deviation": math.sqrt(even_variance),
            "objective": even_expected + lam * even_variance,
        },
        "disclaimer": DISCLAIMER,
    }
