"""Decision attribution: which trading decisions made money.

Pure standard-library functions over plain sequences and mappings, like
:mod:`ibkr_etl.modelling`. Trades are IBKR executions (date, conid, symbol, side,
quantity, trade price, commission); the caller converts every price, commission and
NAV into one currency first.

* :func:`trade_value_added` – each buy or sell replayed against not making it: what it
  added or cost 5, 21 and 63 trading days later, net of commission, with totals by
  horizon and by holding.
* :func:`timing_cost` – each fill against the price when the decision was made (the
  previous close, unless the trade carries its own decision price), in money and in
  basis points.
* :func:`skill_profile` – hit rate, average win and loss, payoff ratio, sizing skill
  (did bigger positions do better?) and sell discipline (did the holdings sold do
  worse than the holdings kept?).
* :func:`counterfactual_portfolios` – NAV had the start holdings simply been held,
  rebalanced quarterly to their start weights, or swapped for a benchmark, with the
  same contributions and withdrawals, against the actual NAV.

:func:`results_at_horizon` and :func:`holding_forward_returns` turn the trade replay
and dated holdings into the rows :func:`skill_profile` reads.

Price moves only: dividends and interest on cash are not counted. Counterfactuals are
hypothetical and say so.
"""
from __future__ import annotations

import bisect
import math
import statistics
from collections import defaultdict
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError

MODEL_VERSION = "decision-attribution-v1"
DEFAULT_HORIZONS = (5, 21, 63)
BASIS_POINTS = 10_000.0
SPEARMAN_MIN_OBSERVATIONS = 3


# Shared helpers ------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    """A finite float, or None for None, blanks, text that is not a number, NaN and infinity."""

    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


def _iso_day(value: Any) -> str | None:
    text = str(value or "")[:10]
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def _price_series(series: Mapping[Any, Any] | None) -> tuple[list[str], list[float]]:
    """Dates and positive prices in date order; missing, NaN and non-positive prices are skipped."""

    points = []
    for day, raw in (series or {}).items():
        value = _number(raw)
        text = _iso_day(day)
        if value is not None and value > 0 and text:
            points.append((text, value))
    points.sort()
    return [d for d, _ in points], [v for _, v in points]


def _keyed(mapping: Mapping[Any, Any] | None) -> dict[str, Any]:
    """The same mapping with conids as text, so 8314 and "8314" match."""

    return {str(key): value for key, value in (mapping or {}).items()}


def _normalise_trade(trade: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """One trade in a common shape, or the reason it cannot be used."""

    day = _iso_day(_first(trade, "trade_date", "date"))
    if not day:
        return None, "no ISO trade date"
    conid = _first(trade, "conid")
    if conid in (None, ""):
        return None, "no holding (conid)"
    quantity = _number(_first(trade, "quantity", "quantity_text"))
    if not quantity:
        return None, "no quantity"
    side_text = str(_first(trade, "side", "buy_sell") or "").strip().upper()
    if side_text.startswith("BUY"):
        side = "BUY"
    elif side_text.startswith("SELL"):
        side = "SELL"
    elif not side_text:
        side = "BUY" if quantity > 0 else "SELL"  # IBKR signs quantity: negative is a sale
    else:
        return None, f"side {side_text!r} is neither BUY nor SELL"
    price = _number(_first(trade, "price", "trade_price", "trade_price_text"))
    if price is None or price <= 0:
        return None, "no positive trade price"
    raw_commission = _first(trade, "commission", "commission_text")
    commission = 0.0 if raw_commission is None else _number(raw_commission)
    if commission is None:
        return None, "commission is not a number"
    decision = _number(trade.get("decision_price"))
    return {
        "trade_id": _first(trade, "trade_id", "id", "execution_id"),
        "trade_date": day,
        "conid": str(conid),
        "symbol": str(trade.get("symbol") or conid),
        "side": side,
        "quantity": abs(quantity),
        "price": price,
        # IBKR reports commission as a negative amount; it is a cost whatever its sign.
        "commission": abs(commission),
        "decision_price": decision if decision is not None and decision > 0 else None,
    }, None


def _prepare_trades(trades: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prepared, skipped = [], []
    for index, trade in enumerate(trades):
        row, reason = _normalise_trade(trade)
        if row is None:
            skipped.append({"index": index, "reason": reason})
        else:
            prepared.append({"index": index, **row})
    return prepared, skipped


def _sign(side: str) -> float:
    return 1.0 if side == "BUY" else -1.0


def _bps(amount: float, base: float) -> float | None:
    return amount / base * BASIS_POINTS if base else None


def _horizons(horizons: Sequence[int]) -> list[int]:
    cleaned = sorted({int(h) for h in horizons if isinstance(h, int) and not isinstance(h, bool)})
    if not cleaned or len(cleaned) != len(list(horizons)) or cleaned[0] < 1:
        raise ModellingInputError("Horizons must be distinct whole numbers of trading days, each at least 1.")
    return cleaned


# 1. Value added by each trade -----------------------------------------------------------


def trade_value_added(
    trades: Iterable[Mapping[str, Any]],
    closes: Mapping[Any, Mapping[str, float]],
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> dict[str, Any]:
    """Each trade against not making it, over each horizon of trading days.

    A buy earns (Pₜ₊ₕ − fill) × quantity − commission and a sell earns
    (fill − Pₜ₊ₕ) × quantity − commission, where Pₜ₊ₕ is the holding's close h
    trading days (of its own close series in ``closes``, conid → date → close) after
    the trade date. A horizon that runs past the last close is marked unavailable.
    Trades without a date, holding, side, quantity or positive price are skipped and
    listed; so are closes that are missing, NaN or not positive.
    """

    horizon_list = _horizons(horizons)
    prepared, skipped = _prepare_trades(trades)
    if not prepared:
        raise ModellingInputError("There are no trades with a date, holding, side, quantity and price to assess.")
    keyed = _keyed(closes)
    series = {conid: _price_series(keyed.get(conid)) for conid in {t["conid"] for t in prepared}}
    rows = []
    for trade in prepared:
        dates, prices = series[trade["conid"]]
        sign, notional = _sign(trade["side"]), trade["price"] * trade["quantity"]
        base = bisect.bisect_right(dates, trade["trade_date"]) - 1
        outcomes = []
        for horizon in horizon_list:
            index = base + horizon
            if index >= len(dates):
                reason = "no closing prices for this holding" if not dates else "the horizon runs past the last close"
                outcomes.append({"horizon": horizon, "available": False, "reason": reason})
                continue
            end = prices[index]
            gross = sign * (end - trade["price"]) * trade["quantity"]
            outcomes.append({
                "horizon": horizon,
                "available": True,
                "end_date": dates[index],
                "end_price": end,
                "price_return": end / trade["price"] - 1.0,
                "decision_return": gross / notional,
                "gross_value_added": gross,
                "value_added": gross - trade["commission"],
            })
        rows.append({**{k: v for k, v in trade.items() if k != "decision_price"},
                     "notional": notional, "horizons": outcomes})
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Each trade is replayed against not making it. A buy earns the move from its fill price to the "
            "holding's close h trading days later, times the quantity, less commission; a sell earns the move it "
            "avoided (fill price minus the later close). Decision return is that gain before commission divided by "
            "the traded value. Price moves only: dividends and interest on the cash are not counted. Horizons that "
            "run past the last close are marked unavailable and left out of the totals."
        ),
        "horizons": horizon_list,
        "trades": rows,
        "totals": [_horizon_total(rows, h) for h in horizon_list],
        "by_holding": _by_holding(rows, horizon_list),
        "skipped": skipped,
        "disclaimer": DISCLAIMER,
    }


def _outcome(row: Mapping[str, Any], horizon: int) -> Mapping[str, Any] | None:
    for outcome in row["horizons"]:
        if outcome["horizon"] == horizon:
            return outcome if outcome["available"] else None
    return None


def _horizon_total(rows: Sequence[Mapping[str, Any]], horizon: int) -> dict[str, Any]:
    measured = [(row, _outcome(row, horizon)) for row in rows]
    done = [(row, outcome) for row, outcome in measured if outcome]
    by_side = {side: [o["value_added"] for r, o in done if r["side"] == side] for side in ("BUY", "SELL")}
    return {
        "horizon": horizon,
        "trades": len(done),
        "unavailable": len(rows) - len(done),
        "value_added": sum(o["value_added"] for _, o in done),
        "gross_value_added": sum(o["gross_value_added"] for _, o in done),
        "commission": sum(r["commission"] for r, _ in done),
        "buys": {"trades": len(by_side["BUY"]), "value_added": sum(by_side["BUY"])},
        "sells": {"trades": len(by_side["SELL"]), "value_added": sum(by_side["SELL"])},
    }


def _by_holding(rows: Sequence[Mapping[str, Any]], horizons: Sequence[int]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["conid"]].append(row)
    result = []
    for conid, group in groups.items():
        per_horizon = []
        for horizon in horizons:
            outcomes = [o for o in (_outcome(r, horizon) for r in group) if o]
            per_horizon.append({"horizon": horizon, "trades": len(outcomes),
                                "value_added": sum(o["value_added"] for o in outcomes)})
        result.append({"conid": conid, "symbol": group[0]["symbol"], "trades": len(group), "horizons": per_horizon})
    return sorted(result, key=lambda item: (item["symbol"], item["conid"]))


def results_at_horizon(attribution: Mapping[str, Any], horizon: int) -> list[dict[str, Any]]:
    """Flat rows, one per trade measured at ``horizon``, from :func:`trade_value_added`."""

    if horizon not in attribution.get("horizons", ()):
        raise ModellingInputError(f"The trade replay has no {horizon}-day horizon.")
    rows = []
    for row in attribution["trades"]:
        outcome = _outcome(row, horizon)
        if outcome is None:
            continue
        rows.append({
            **{k: row[k] for k in ("index", "trade_id", "trade_date", "conid", "symbol", "side",
                                   "quantity", "price", "notional", "commission")},
            "horizon": horizon,
            **{k: outcome[k] for k in ("end_date", "price_return", "decision_return",
                                       "gross_value_added", "value_added")},
        })
    return rows


# 2. Timing cost --------------------------------------------------------------------------


def timing_cost(
    trades: Iterable[Mapping[str, Any]],
    decision_prices: Mapping[Any, Mapping[str, float]] | None,
) -> dict[str, Any]:
    """Fill price against the price when the decision was made, per trade and in total.

    The decision price is a trade's own ``decision_price`` when it carries one, else
    the last price in ``decision_prices`` (conid → date → price, normally closes)
    strictly before the trade date: the previous close. Positive cost means the fill
    was worse than the decision price (a buy paid more, a sell received less). Basis
    points are of the value at the decision price. Trades with no decision price are
    skipped and listed.
    """

    prepared, skipped = _prepare_trades(trades)
    keyed = _keyed(decision_prices)
    series = {conid: _price_series(keyed.get(conid)) for conid in {t["conid"] for t in prepared}}
    rows = []
    for trade in prepared:
        decision, decision_date, source = trade["decision_price"], None, "trade"
        if decision is None:
            dates, prices = series[trade["conid"]]
            index = bisect.bisect_left(dates, trade["trade_date"]) - 1
            if index < 0:
                skipped.append({"index": trade["index"], "reason": "no price before the trade date"})
                continue
            decision, decision_date, source = prices[index], dates[index], "previous close"
        sign, quantity = _sign(trade["side"]), trade["quantity"]
        base = decision * quantity
        cost = sign * (trade["price"] - decision) * quantity
        rows.append({
            **{k: trade[k] for k in ("index", "trade_id", "trade_date", "conid", "symbol", "side",
                                     "quantity", "price", "commission")},
            "decision_price": decision,
            "decision_date": decision_date,
            "decision_source": source,
            "decision_value": base,
            "timing_cost": cost,
            "timing_cost_bps": _bps(cost, base),
            "commission_bps": _bps(trade["commission"], base),
            "total_cost": cost + trade["commission"],
            "total_cost_bps": _bps(cost + trade["commission"], base),
        })
    if not rows:
        raise ModellingInputError("No trade has a decision price, so timing cannot be measured.")
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Timing cost is the fill price minus the decision price, times the quantity, for a buy (the reverse "
            "for a sell), so a positive number is a cost. The decision price is the trade's own when given, else "
            "the previous close. Basis points are of the value at the decision price; totals in basis points are "
            "weighted by that value."
        ),
        "trades": rows,
        "totals": {
            "all": _cost_total(rows),
            "buys": _cost_total([r for r in rows if r["side"] == "BUY"]),
            "sells": _cost_total([r for r in rows if r["side"] == "SELL"]),
        },
        "skipped": skipped,
    }


def _cost_total(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    base = sum(r["decision_value"] for r in rows)
    timing = sum(r["timing_cost"] for r in rows)
    commission = sum(r["commission"] for r in rows)
    return {
        "trades": len(rows),
        "decision_value": base,
        "timing_cost": timing,
        "timing_cost_bps": _bps(timing, base),
        "commission": commission,
        "commission_bps": _bps(commission, base),
        "total_cost": timing + commission,
        "total_cost_bps": _bps(timing + commission, base),
    }


# 3. Skill profile ------------------------------------------------------------------------


def holding_forward_returns(
    positions: Mapping[str, Mapping[Any, float]],
    closes: Mapping[Any, Mapping[str, float]],
    *,
    horizon: int,
) -> list[dict[str, Any]]:
    """Each dated holding's size and its price return over the next ``horizon`` trading days.

    ``positions`` maps date → conid → size (market value or weight). The return runs
    from the holding's close on that date (or the last one before it) to the close
    ``horizon`` trading days later. Holdings with a size that is not positive, with no
    price on the date, or whose horizon runs past the last close are left out.
    """

    (horizon,) = _horizons([horizon])
    keyed = _keyed(closes)
    cache: dict[str, tuple[list[str], list[float]]] = {}
    rows = []
    for raw_day, holdings in positions.items():
        day = _iso_day(raw_day)
        if not day:
            continue
        for raw_conid, raw_size in holdings.items():
            size, conid = _number(raw_size), str(raw_conid)
            if size is None or size <= 0:
                continue
            if conid not in cache:
                cache[conid] = _price_series(keyed.get(conid))
            dates, prices = cache[conid]
            base = bisect.bisect_right(dates, day) - 1
            if base < 0 or base + horizon >= len(dates):
                continue
            rows.append({"date": day, "conid": conid, "size": size, "horizon": horizon,
                         "return": prices[base + horizon] / prices[base] - 1.0})
    return sorted(rows, key=lambda r: (r["date"], r["conid"]))


def _ranks(values: Sequence[float]) -> list[float]:
    """Ranks from 1, ties sharing their average rank."""

    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for position in range(start, end + 1):
            ranks[order[position]] = (start + end) / 2.0 + 1.0
        start = end + 1
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    mean_left, mean_right = statistics.fmean(left), statistics.fmean(right)
    sxx = sum((a - mean_left) ** 2 for a in left)
    syy = sum((b - mean_right) ** 2 for b in right)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Spearman rank correlation (Pearson correlation of average ranks); None if too short or flat."""

    if len(left) != len(right) or len(left) < SPEARMAN_MIN_OBSERVATIONS:
        return None
    return _pearson(_ranks(left), _ranks(right))


def _win_loss(values: Sequence[float]) -> dict[str, Any]:
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    average_win = statistics.fmean(wins) if wins else None
    average_loss = statistics.fmean(losses) if losses else None
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "hit_rate": len(wins) / len(values) if values else None,
        "average_win": average_win,
        "average_loss": average_loss,
        "payoff_ratio": average_win / -average_loss if wins and losses else None,
        "average_value_added": statistics.fmean(values) if values else None,
        "total_value_added": sum(values),
    }


def _sizing_skill(panel: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_date: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in panel:
        by_date[row["date"]].append((row["size"], row["return"]))
    per_date = []
    for day in sorted(by_date):
        pairs = by_date[day]
        value = spearman([s for s, _ in pairs], [r for _, r in pairs])
        if value is not None:
            per_date.append(value)
    return {
        "spearman": statistics.fmean(per_date) if per_date else None,
        "dates": len(per_date),
        "positive_share": sum(1 for v in per_date if v > 0) / len(per_date) if per_date else None,
        "pooled_spearman": spearman([r["size"] for r in panel], [r["return"] for r in panel]),
        "observations": len(panel),
    }


def _sell_discipline(sells: Sequence[Mapping[str, Any]], panel: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_date: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in panel:
        by_date[row["date"]].append(row)
    sold, kept, unassessed = [], [], 0
    for sale in sells:
        sold_return = _number(sale.get("price_return"))
        others = [r for r in by_date.get(str(sale.get("trade_date"))[:10], ()) if r["conid"] != str(sale.get("conid"))]
        if sold_return is None or not others:
            unassessed += 1
            continue
        total = sum(r["size"] for r in others)
        sold.append(sold_return)
        kept.append(sum(r["size"] * r["return"] for r in others) / total)
    advantages = [k - s for s, k in zip(sold, kept)]
    return {
        "sells": len(sold),
        "unassessed": unassessed,
        "average_sold_return": statistics.fmean(sold) if sold else None,
        "average_kept_return": statistics.fmean(kept) if kept else None,
        "average_advantage": statistics.fmean(advantages) if advantages else None,
        "hit_rate": sum(1 for a in advantages if a > 0) / len(advantages) if advantages else None,
    }


def skill_profile(
    trade_results: Sequence[Mapping[str, Any]],
    position_sizes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Hit rate, win and loss size, payoff ratio, sizing skill and sell discipline.

    ``trade_results`` are rows from :func:`results_at_horizon` (``value_added``,
    ``side``, ``conid``, ``trade_date`` and ``price_return``). ``position_sizes`` are
    rows from :func:`holding_forward_returns` (``date``, ``conid``, ``size`` and the
    holding's ``return`` over the same horizon). Rows with a missing or NaN value are
    left out.

    Sizing skill is the Spearman rank correlation between position size and the
    holding's subsequent return, computed across holdings on each date and averaged
    over dates (the pooled correlation is given too). Sell discipline compares each
    sold holding's return after the sale with the size-weighted return of the holdings
    kept on the sale date over the same horizon; a positive advantage means the sells
    left behind holdings that went on to do worse.
    """

    values = [(row, _number(row.get("value_added"))) for row in trade_results]
    measured = [(row, v) for row, v in values if v is not None]
    if not measured:
        raise ModellingInputError("The skill profile needs at least one trade with a measured result.")
    panel = []
    for row in position_sizes:
        size, value = _number(row.get("size")), _number(row.get("return"))
        if size is not None and size > 0 and value is not None and row.get("date") and row.get("conid") is not None:
            panel.append({**row, "date": str(row["date"])[:10], "conid": str(row["conid"]),
                          "size": size, "return": value})
    trade_horizons = {row.get("horizon") for row, _ in measured} - {None}
    panel_horizons = {row.get("horizon") for row in panel} - {None}
    if len(trade_horizons | panel_horizons) > 1:
        raise ModellingInputError("The trade results and the holdings must be measured over the same horizon.")
    all_values = [v for _, v in measured]
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Hit rate is the share of trades that added value after commission over the horizon; payoff ratio is "
            "the average win divided by the average loss. Sizing skill is the Spearman rank correlation between "
            "each holding's size and its return over the next horizon, across holdings on each date, averaged over "
            "dates: above zero means bigger positions did better. Sell discipline compares each sold holding's "
            "return after the sale with the size-weighted return of the holdings kept that day; a positive "
            "advantage means the sales were of holdings that went on to do worse."
        ),
        "horizon": next(iter(trade_horizons | panel_horizons), None),
        **_win_loss(all_values),
        "by_side": {
            "buys": _win_loss([v for row, v in measured if row.get("side") == "BUY"]),
            "sells": _win_loss([v for row, v in measured if row.get("side") == "SELL"]),
        },
        "sizing_skill": _sizing_skill(panel),
        "sell_discipline": _sell_discipline([row for row, _ in measured if row.get("side") == "SELL"], panel),
        "disclaimer": DISCLAIMER,
    }


# 4. Counterfactual portfolios --------------------------------------------------------------


def _carried(dates: Sequence[str], values: Sequence[float], calendar: Sequence[str]) -> list[float | None]:
    """The last value on or before each calendar date (None before the first)."""

    result, pointer, last = [], 0, None
    for day in calendar:
        while pointer < len(dates) and dates[pointer] <= day:
            last = values[pointer]
            pointer += 1
        result.append(last)
    return result


def _flows_by_index(flows: Iterable[Any], calendar: Sequence[str]) -> tuple[dict[int, float], list[dict[str, Any]]]:
    """Flows on the first calendar date on or after their own date; those outside the window are listed."""

    placed: dict[int, float] = defaultdict(float)
    ignored = []
    for item in flows:
        try:
            day, amount = (item.get("date"), item.get("amount")) if isinstance(item, Mapping) else tuple(item)
        except (TypeError, ValueError):
            day, amount = None, None
        day, amount = _iso_day(day), _number(amount)
        if not day or amount is None:
            ignored.append({"date": day, "amount": amount, "reason": "no ISO date or amount"})
        elif day <= calendar[0] or day > calendar[-1]:
            ignored.append({"date": day, "amount": amount, "reason": "outside the window after the start date"})
        else:
            placed[bisect.bisect_left(calendar, day)] += amount
    return dict(placed), ignored


def _value(units: Mapping[str, float], cash: float, prices: Mapping[str, float]) -> float:
    return cash + sum(quantity * prices[conid] for conid, quantity in units.items())


def _add_pro_rata(units: dict[str, float], cash: float, value: float, flow: float) -> tuple[dict[str, float], float]:
    """Add a contribution (or take a withdrawal) in proportion to what is held."""

    if not flow:
        return units, cash
    if value <= 0 or value + flow <= 0:
        return {conid: 0.0 for conid in units}, value + flow
    scale = (value + flow) / value
    return {conid: quantity * scale for conid, quantity in units.items()}, cash * scale


def _quarter(day: str) -> tuple[int, int]:
    return int(day[:4]), (int(day[5:7]) - 1) // 3


def counterfactual_portfolios(
    start_date: str,
    start_holdings: Mapping[Any, float],
    closes: Mapping[Any, Mapping[str, float]],
    actual_nav: Mapping[str, float],
    *,
    flows: Iterable[Any] = (),
    benchmark: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """NAV of buy-and-hold, quarterly rebalancing and the benchmark against the actual NAV.

    ``start_holdings`` maps conid → quantity held at the close of ``start_date``;
    whatever part of that day's NAV they do not account for is cash, carried at a 0%
    return. Each path starts from the actual NAV on ``start_date`` and runs on the
    actual NAV's dates. Buy-and-hold keeps the start quantities; the quarterly path
    resets to the start weights (cash included) at the last NAV date of each calendar
    quarter, without costs; the benchmark path (date → index level) buys the
    benchmark. ``flows`` are (date, amount) pairs or {"date", "amount"} mappings,
    positive for contributions; each is added at that day's close (or the next NAV
    date) in proportion to what each path holds, the same way the actual NAV received
    it. Prices missing on a date are carried forward; NAV dates whose value is
    missing, NaN or not positive are skipped. Holdings with no price on or before the
    start date, or with a quantity that is not positive, are left out and listed, so
    their value counts as cash.
    """

    start = _iso_day(start_date)
    nav_dates, nav_values = _price_series(actual_nav)
    calendar = [d for d in nav_dates if start and d >= start]
    if not start or not calendar or calendar[0] != start:
        raise ModellingInputError(f"There is no NAV on the start date {start_date}.")
    if len(calendar) < 2:
        raise ModellingInputError("Counterfactual portfolios need NAV on at least two dates from the start date.")
    actual = nav_values[len(nav_dates) - len(calendar):]
    keyed = _keyed(closes)
    paths: dict[str, list[float | None]] = {}
    quantities: dict[str, float] = {}
    left_out = []
    for conid, raw_quantity in _keyed(start_holdings).items():
        quantity = _number(raw_quantity)
        if quantity is None or quantity <= 0:
            left_out.append({"conid": conid, "reason": "quantity is missing or not positive"})
            continue
        carried = _carried(*_price_series(keyed.get(conid)), calendar)
        if carried[0] is None:
            left_out.append({"conid": conid, "reason": "no price on or before the start date"})
            continue
        paths[conid], quantities[conid] = carried, quantity
    start_nav = actual[0]
    start_prices = {conid: series[0] for conid, series in paths.items()}
    cash = start_nav - _value(quantities, 0.0, start_prices)
    weights = {conid: quantities[conid] * start_prices[conid] / start_nav for conid in paths}
    cash_weight = cash / start_nav
    bench = None
    if benchmark is not None:
        bench = _carried(*_price_series(benchmark), calendar)
        if bench[0] is None:
            raise ModellingInputError("The benchmark has no level on or before the start date.")
    flow_at, ignored = _flows_by_index(flows, calendar)

    hold_units, hold_cash = dict(quantities), cash
    rebalance_units, rebalance_cash = dict(quantities), cash
    bench_units = start_nav / bench[0] if bench else 0.0
    rebalances = 0
    series = [_row(calendar[0], start_nav, start_nav, start_nav, start_nav if bench else None)]
    for k in range(1, len(calendar)):
        prices = {conid: path[k] for conid, path in paths.items()}
        flow = flow_at.get(k, 0.0)
        hold_value = _value(hold_units, hold_cash, prices)
        hold_units, hold_cash = _add_pro_rata(hold_units, hold_cash, hold_value, flow)
        rebalance_value = _value(rebalance_units, rebalance_cash, prices)
        rebalance_units, rebalance_cash = _add_pro_rata(rebalance_units, rebalance_cash, rebalance_value, flow)
        rebalance_value += flow
        if k < len(calendar) - 1 and _quarter(calendar[k]) != _quarter(calendar[k + 1]) and rebalance_value > 0:
            rebalance_units = {c: weights[c] * rebalance_value / prices[c] for c in paths}
            rebalance_cash = cash_weight * rebalance_value
            rebalances += 1
        bench_value = None
        if bench:
            bench_units += flow / bench[k]
            bench_value = bench_units * bench[k]
        series.append(_row(calendar[k], actual[k], hold_value + flow, rebalance_value, bench_value))

    end = series[-1]
    names = ["buy_and_hold", "quarterly_rebalance"] + (["benchmark"] if bench else [])
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Every path starts from the actual NAV on the start date. Buy-and-hold keeps the start quantities; "
            "quarterly rebalance resets to the start weights (cash included) at each calendar quarter's last NAV "
            "date, without trading costs; the benchmark path holds only the benchmark. Contributions and "
            "withdrawals are added on the day the actual NAV received them, in proportion to what each path holds. "
            "Cash earns nothing and dividends are not counted. A positive actual-minus figure means the decisions "
            "actually taken did better than that alternative."
        ),
        "start_date": start,
        "end_date": calendar[-1],
        "start_nav": start_nav,
        "start_weights": {**weights, "cash": cash_weight},
        "total_flows": sum(flow_at.values()),
        "rebalances": rebalances,
        "summary": {
            name: {"end_value": end[name], "actual_minus_counterfactual": end[f"actual_minus_{name}"]}
            for name in names
        },
        "actual_end_value": end["actual"],
        "series": series,
        "left_out_holdings": left_out,
        "ignored_flows": ignored,
        "disclaimer": DISCLAIMER,
    }


def _row(day: str, actual: float, hold: float, rebalance: float, bench: float | None) -> dict[str, Any]:
    row = {
        "date": day,
        "actual": actual,
        "buy_and_hold": hold,
        "quarterly_rebalance": rebalance,
        "actual_minus_buy_and_hold": actual - hold,
        "actual_minus_quarterly_rebalance": actual - rebalance,
    }
    if bench is not None:
        row["benchmark"] = bench
        row["actual_minus_benchmark"] = actual - bench
    return row
