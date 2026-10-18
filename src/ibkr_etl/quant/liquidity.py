"""Liquidity risk: how long holdings take to sell, and VaR that allows for the cost of selling.

Pure standard-library Python, like :mod:`ibkr_etl.risk_forecasting`:

* :func:`average_daily_volume` averages the last ``window`` trading days that had
  volume, skipping zero-volume holidays and missing days, and says how many it used.
* :func:`days_to_liquidate` gives the days needed to sell each holding while trading no
  more than ``participation`` of its average daily volume (20% is a common limit, so
  the selling does not move the price much), and the share of the portfolio's value
  that could be sold within 1, 5 and 20 days.
* :func:`liquidity_adjusted_var` adds the cost of selling through the bid-ask spread to
  VaR, the exogenous liquidity adjustment of Bangia, Diebold, Schuermann and
  Stroughair (1999): half the relative spread at its average plus k standard deviations,
  weighted by value.

Quantities and volumes are in shares (or contracts), values in the portfolio's base
currency, spreads relative to the mid price ((ask - bid) / mid, so 0.001 is 10 basis
points). Every result is hypothetical and says so.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError

MODEL_VERSION = "liquidity-v1"
ADV_WINDOW = 20
DEFAULT_PARTICIPATION = 0.20
LIQUIDATION_HORIZONS = (1, 5, 20)
SPREAD_MULTIPLIER = 3.0


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _number(value: Any, what: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ModellingInputError(f"{what} must be a number.") from None
    if not math.isfinite(number):
        raise ModellingInputError(f"{what} must be a finite number.")
    return number


def average_daily_volume(daily_volumes: Mapping[str, float | None] | Sequence[Any], window: int = ADV_WINDOW) -> dict[str, Any]:
    """Mean volume over the last ``window`` trading days that had volume.

    ``daily_volumes`` is a mapping of ISO date to volume, (date, volume) pairs, or plain
    volumes oldest first. Days with zero volume (exchange holidays) or no figure are
    skipped and counted in ``days_skipped``, so a holiday neither drags the average down
    nor uses up the window. When fewer than ``window`` days had volume, the average uses
    those there are and ``complete`` is False; with none it is None.
    """

    if not (isinstance(window, int) and window >= 1):
        raise ModellingInputError("The volume window must be a whole number of days, at least 1.")
    if isinstance(daily_volumes, Mapping):
        values = [v for _, v in sorted(daily_volumes.items(), key=lambda pair: str(pair[0]))]
    else:
        items = list(daily_volumes)
        dated = bool(items) and isinstance(items[0], (tuple, list))
        values = [v for _, v in sorted(items, key=lambda pair: str(pair[0]))] if dated else items
    used, skipped = [], 0
    for value in reversed(values):
        if len(used) == window:
            break
        if _missing(value):
            skipped += 1
            continue
        volume = _number(value, "A daily volume")
        if volume < 0:
            raise ModellingInputError("Daily volumes must be zero or more.")
        if volume == 0:
            skipped += 1
            continue
        used.append(volume)
    return {
        "average_daily_volume": statistics.fmean(used) if used else None,
        "median_daily_volume": statistics.median(used) if used else None,
        "days_used": len(used),
        "days_skipped": skipped,
        "window": window,
        "complete": len(used) == window,
    }


def _volume(position: Mapping[str, Any]) -> float | None:
    """A position's average daily volume, from a number or an :func:`average_daily_volume` result."""

    value = position.get("average_daily_volume")
    if isinstance(value, Mapping):
        value = value.get("average_daily_volume")
    if _missing(value):
        return None
    volume = _number(value, "An average daily volume")
    return volume if volume > 0 else None


def days_to_liquidate(positions: Mapping[str, Mapping[str, Any]], *, participation: float = DEFAULT_PARTICIPATION,
                      horizons: Sequence[int] = LIQUIDATION_HORIZONS) -> dict[str, Any]:
    """Days to sell each holding at ``participation`` of its daily volume, and how much sells how fast.

    ``positions`` maps each holding to ``quantity`` (shares; a short counts by its size),
    ``value`` (market value) and ``average_daily_volume`` (shares a day, or the
    :func:`average_daily_volume` result). Days = |quantity| / (participation × volume).
    Within h days a holding can sell min(1, h / days) of itself, so ``sellable_within``
    is the value-weighted share of the whole portfolio sold by then. Holdings with no
    volume figure count as not sellable and are listed, with their share of value.
    """

    participation = _number(participation, "The participation rate")
    if not 0.0 < participation <= 1.0:
        raise ModellingInputError("The participation rate must be above 0% and at most 100% of daily volume.")
    if not positions:
        raise ModellingInputError("There are no positions to assess.")
    if any(not (isinstance(h, int) and h >= 1) for h in horizons):
        raise ModellingInputError("Each horizon must be a whole number of days, at least 1.")
    rows = []
    for key, position in positions.items():
        quantity = abs(_number(position.get("quantity"), f"The quantity of {key}"))
        value = abs(_number(position.get("value"), f"The value of {key}"))
        volume = _volume(position)
        days = quantity / (participation * volume) if volume is not None else None
        rows.append({"key": key, "quantity": quantity, "value": value, "average_daily_volume": volume, "days": days})
    total = math.fsum(r["value"] for r in rows)
    if total <= 0:
        raise ModellingInputError("The positions have no value, so there is nothing to sell.")
    for row in rows:
        row["share_of_portfolio"] = row["value"] / total
    known = [r for r in rows if r["days"] is not None]
    sellable = {str(h): math.fsum(r["value"] * (1.0 if r["days"] <= h else h / r["days"]) for r in known) / total
                for h in horizons}
    known_value = math.fsum(r["value"] for r in known)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "participation": participation,
        "holdings": sorted(rows, key=lambda r: (r["days"] is None, -(r["days"] or 0.0))),
        "sellable_within": sellable,
        "days_to_sell_everything": max((r["days"] for r in known), default=None) if len(known) == len(rows) else None,
        "value_weighted_days": math.fsum(r["value"] * r["days"] for r in known) / known_value if known_value else None,
        "no_volume_data": [r["key"] for r in rows if r["days"] is None],
        "share_without_volume_data": 1.0 - known_value / total,
        "method": (f"Days to sell each holding while trading at most {participation:.0%} of its average daily volume. "
                   "The shares sold within each horizon assume every holding is sold at that pace at once; holdings "
                   "without volume data count as unsold. Real volume varies, and selling in a crisis is slower."),
        "disclaimer": DISCLAIMER,
    }


def _spreads(series: Any, key: str) -> list[float]:
    if series is None:
        return []
    values = []
    for value in series:
        if _missing(value):
            continue
        spread = _number(value, f"A bid-ask spread of {key}")
        if spread < 0:
            raise ModellingInputError(f"The bid-ask spreads of {key} must be zero or more.")
        values.append(spread)
    return values


def liquidity_adjusted_var(var: float, positions: Mapping[str, Mapping[str, Any]], *,
                           k: float = SPREAD_MULTIPLIER) -> dict[str, Any]:
    """VaR plus the cost of selling through the bid-ask spread (Bangia et al. 1999).

    ``var`` is a VaR as a fraction of portfolio value (0.02 for 2%). ``positions`` maps
    each holding to ``value`` and ``spreads``, a series of relative bid-ask spreads.
    Each holding costs ½(μ + kσ) of its value, where μ and σ are the mean and sample
    standard deviation of its spreads: half the spread is paid crossing from mid to bid,
    and kσ allows for spreads widening when it matters. LVaR = VaR + Σ w·½(μ + kσ) with
    value weights w. Bangia et al. choose k from the empirical spread distribution; the
    default 3 is a common choice for a 99% VaR, above the normal's 2.33 because spreads
    have fat tails, so pass your own if the spreads say otherwise. Holdings without
    spreads add nothing and are listed, so the adjustment is then a floor.
    """

    var = _number(var, "The VaR")
    if var < 0:
        raise ModellingInputError("Give the VaR as a positive loss, such as 0.02 for 2%.")
    k = _number(k, "The spread multiplier")
    if k < 0:
        raise ModellingInputError("The spread multiplier must be zero or more.")
    if not positions:
        raise ModellingInputError("There are no positions to assess.")
    rows = []
    for key, position in positions.items():
        value = abs(_number(position.get("value"), f"The value of {key}"))
        spreads = _spreads(position.get("spreads"), key)
        mean = statistics.fmean(spreads) if spreads else None
        sd = statistics.stdev(spreads) if len(spreads) >= 2 else 0.0 if spreads else None
        rows.append({"key": key, "value": value, "spread_days": len(spreads), "mean_spread": mean,
                     "spread_volatility": sd, "cost": 0.5 * (mean + k * sd) if spreads else None})
    total = math.fsum(r["value"] for r in rows)
    if total <= 0:
        raise ModellingInputError("The positions have no value, so there is nothing to sell.")
    for row in rows:
        row["weight"] = row["value"] / total
        row["contribution"] = row["weight"] * row["cost"] if row["cost"] is not None else None
    cost = math.fsum(r["contribution"] for r in rows if r["contribution"] is not None)
    adjusted = var + cost
    missing = [r for r in rows if r["cost"] is None]
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "var": var,
        "liquidity_cost": cost,
        "liquidity_adjusted_var": adjusted,
        "liquidity_share": cost / adjusted if adjusted > 0 else None,
        "spread_multiplier": k,
        "holdings": sorted(rows, key=lambda r: -(r["contribution"] or 0.0)),
        "no_spread_data": [r["key"] for r in missing],
        "share_without_spread_data": math.fsum(r["weight"] for r in missing),
        "method": ("Bangia, Diebold, Schuermann and Stroughair (1999): VaR plus the cost of selling at the bid instead "
                   f"of the mid price, half of each holding's relative bid-ask spread at its average plus {k:g} standard "
                   "deviations, weighted by value. It assumes the market move and the wide spread arrive together, "
                   "which is cautious, and it ignores the price impact of selling large blocks."),
        "disclaimer": DISCLAIMER,
    }
