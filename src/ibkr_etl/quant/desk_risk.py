"""Live risk like a trading desk: today's P&L, where it came from, and limits.

Pure standard-library Python, like :mod:`ibkr_etl.modelling`. The inputs are the rows
the live collector writes (``live_positions``, ``live_quotes``, ``live_executions``,
``live_account_values``); nothing here reads them itself.

* :func:`intraday_pnl` – today's P&L of each holding against the prior close, in its
  own currency and in the reporting currency, with the FX part split from the price
  part for foreign holdings. Quotes that are missing, stale or unusable are flagged
  with their age and left out of the totals, never filled in.
* :func:`pnl_explain` – a period's portfolio return split into the market, each style
  or macro factor, FX and the stock-specific rest, through the holdings' factor betas
  (from :func:`ibkr_etl.quant.holdings_risk.factor_risk_model`). The parts add up to
  the total exactly.
* :func:`limit_check` – live metrics (gross and net exposure, largest position, ex-ante
  volatility, VaR, factor exposures, days to liquidate) against limits, with each
  limit's usage, a green, amber or red status and the breaches, worst first.
* :func:`desk_snapshot` – the three together for the Desk page, with the as-of time
  and how fresh the quotes are.

Money is in each holding's currency unless named as reporting currency; FX rates are
units of the reporting currency per unit of the holding's currency. Times are ISO
strings or datetimes; a time without a zone is read as UTC.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError

MODEL_VERSION = "desk-risk-v1"
DEFAULT_MAX_QUOTE_AGE_SECONDS = 900.0   # 15 minutes: the collector snapshots every 1 to 5 minutes
DEFAULT_AMBER_AT = 0.8
DELAYED_DATA_TYPES = (3, 4)             # IBKR market data types: 1 live, 2 frozen, 3 delayed, 4 delayed frozen
MARKET_FACTOR_NAMES = ("Mkt-RF", "MKT", "Mkt", "Market", "market")
TOLERANCE = 1e-12
# Left None on a holding whose quote cannot be used, so every row has the same keys.
PNL_FIELDS = ("return", "position_pnl_local", "trading_pnl_local", "pnl_local", "opening_value_local",
              "position_pnl_reporting", "trading_pnl_reporting", "price_pnl_reporting", "fx_pnl_reporting",
              "opening_value_reporting", "pnl_reporting")

EXCLUSION_REASONS = {
    "no_quote": "There is no quote for this holding.",
    "no_quote_time": "The quote has no time, so its age is unknown.",
    "quote_after_as_of": "The only quotes are later than the as-of time.",
    "stale": "The quote is older than the freshness limit.",
    "no_price": "The quote has no last price and no usable bid and ask.",
    "no_prior_close": "The quote has no prior close to measure today's move from.",
    "currency_mismatch": "The quote is in a different currency from the position.",
    "no_fx_rate": "There is no current and prior-close FX rate for this currency; counted in its own currency only.",
}


# Shared helpers -----------------------------------------------------------------------


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


def _field(row: Mapping[str, Any], *names: str) -> Any:
    """The first of ``names`` present in ``row``: collector column names and short aliases both work."""

    for name in names:
        if not _missing(row.get(name)):
            return row[name]
    return None


def _positive(row: Mapping[str, Any], what: str, *names: str) -> float | None:
    value = _optional(_field(row, *names), what)
    return value if value is not None and value > 0 else None


def _timestamp(value: Any, what: str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        try:
            moment = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        except ValueError:
            raise ModellingInputError(f"{what} {value!r} is not a time; use ISO times such as "
                                      "2026-10-16T14:30:00+00:00.") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat() if moment else None


def _currency(value: Any, what: str) -> str:
    text = str(value or "").strip().upper()
    if len(text) != 3 or not text.isalpha():
        raise ModellingInputError(f"{what} must be a three-letter currency code such as HKD or USD.")
    return text


def _side(row: Mapping[str, Any], where: str) -> float:
    text = str(row.get("side") or "").strip().upper()
    if text in ("BUY", "BOT"):
        return 1.0
    if text in ("SELL", "SLD"):
        return -1.0
    raise ModellingInputError(f"{where} must be a BUY or a SELL.")


# 1. Intraday P&L -----------------------------------------------------------------------


def _multiplier(row: Mapping[str, Any], quantity: float, conid: str) -> float:
    """The contract multiplier: given, else implied by the collector's market value = quantity × price × multiplier."""

    given = _optional(row.get("multiplier"), f"The multiplier of {conid}")
    if given is not None:
        if given <= 0:
            raise ModellingInputError(f"The multiplier of {conid} must be positive.")
        return given
    value = _optional(row.get("market_value"), f"The market value of {conid}")
    price = _optional(row.get("market_price"), f"The market price of {conid}")
    if value and price and quantity:
        implied = round(value / (quantity * price), 6)
        if implied > 0:
            return implied
    return 1.0


def _holdings(positions: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Positions summed by instrument across accounts, so a composite is one book."""

    items = positions.items() if isinstance(positions, Mapping) else ((None, row) for row in positions)
    book: dict[str, dict[str, Any]] = {}
    for key, row in items:
        conid = str(_field(row, "conid") or key or "").strip()
        if not conid:
            raise ModellingInputError("Every position needs a conid (or a key in a mapping of positions).")
        quantity = _number(row.get("quantity"), f"The quantity of {conid}")
        scale = _multiplier(row, quantity, conid)
        currency = _currency(row.get("currency"), f"The currency of {conid}")
        held = book.setdefault(conid, {"key": conid, "symbol": row.get("symbol") or conid, "currency": currency,
                                       "multiplier": scale, "quantity": 0.0, "accounts": []})
        if held["currency"] != currency or held["multiplier"] != scale:
            raise ModellingInputError(f"{conid} is held in two currencies or with two multipliers.")
        held["quantity"] += quantity
        if row.get("account_id") and row["account_id"] not in held["accounts"]:
            held["accounts"].append(row["account_id"])
    return book


def _quote_lists(quotes: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None) -> dict[str, list[Mapping[str, Any]]]:
    """Quotes by conid; a mapping holds one quote or a list of them per conid."""

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    if quotes is None:
        return grouped
    if isinstance(quotes, Mapping):
        for conid, value in quotes.items():
            rows = [value] if isinstance(value, Mapping) else list(value)
            grouped.setdefault(str(conid), []).extend(rows)
    else:
        for row in quotes:
            conid = str(_field(row, "conid") or "").strip()
            if not conid:
                raise ModellingInputError("Every quote in a list needs its conid.")
            grouped.setdefault(conid, []).append(row)
    return grouped


def _latest_quote(rows: Sequence[Mapping[str, Any]], as_of: datetime | None, conid: str
                  ) -> tuple[Mapping[str, Any] | None, datetime | None, str | None]:
    """The newest quote at or before ``as_of``, its time, and why none can be used."""

    if not rows:
        return None, None, "no_quote"
    timed = [(_timestamp(row["observed_at"], f"The time of a quote for {conid}"), row)
             for row in rows if not _missing(row.get("observed_at"))]
    if not timed:
        return rows[-1], None, "no_quote_time"
    usable = [pair for pair in timed if as_of is None or pair[0] <= as_of]
    if not usable:
        moment, row = min(timed, key=lambda pair: pair[0])
        return row, moment, "quote_after_as_of"
    moment, row = max(usable, key=lambda pair: pair[0])
    return row, moment, None


def _live_price(quote: Mapping[str, Any], conid: str) -> tuple[float | None, str | None]:
    """Last trade, else the bid-ask midpoint, as the collector marks positions; never the prior close."""

    last = _positive(quote, f"The last price of {conid}", "last_price", "last")
    if last is not None:
        return last, "last"
    bid = _positive(quote, f"The bid of {conid}", "bid_price", "bid")
    ask = _positive(quote, f"The ask of {conid}", "ask_price", "ask")
    if bid is not None and ask is not None and bid <= ask:
        return (bid + ask) / 2.0, "mid"
    return None, None


def _fx(fx_rates: Mapping[str, Any] | None, reporting: str) -> dict[str, tuple[float, float]]:
    """Currency → (prior-close rate, current rate) in reporting currency per unit."""

    rates = {reporting: (1.0, 1.0)}
    for currency, raw in (fx_rates or {}).items():
        code = _currency(currency, "An FX rate's currency")
        if code == reporting:
            continue
        if not isinstance(raw, Mapping):
            raise ModellingInputError(f"Give the {code} rate as {{'rate': ..., 'prior_close': ...}}.")
        current = _optional(raw.get("rate"), f"The {code} FX rate")
        prior = _optional(raw.get("prior_close"), f"The {code} prior-close FX rate")
        if current is None or prior is None:
            continue
        if current <= 0 or prior <= 0:
            raise ModellingInputError(f"The {code} FX rates must be positive.")
        rates[code] = (prior, current)
    return rates


def _trades(executions: Sequence[Mapping[str, Any]] | None
            ) -> tuple[dict[str, list[tuple[float, float]]], dict[str, Mapping[str, Any]]]:
    """conid → [(signed quantity, price)] for today's fills, and conid → its first fill."""

    trades: dict[str, list[tuple[float, float]]] = {}
    first: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(executions or [], start=1):
        conid = str(_field(row, "conid") or "").strip()
        if not conid:
            raise ModellingInputError(f"Execution {index} has no conid.")
        quantity = abs(_number(row.get("quantity"), f"The quantity of execution {index}"))
        price = _number(row.get("price"), f"The price of execution {index}")
        trades.setdefault(conid, []).append((_side(row, f"Execution {index}") * quantity, price))
        first.setdefault(conid, row)
    return trades, first


def _pnl_row(held: dict[str, Any], quote: Mapping[str, Any] | None, moment: datetime | None, problem: str | None,
             as_of: datetime | None, max_age: float, fills: list[tuple[float, float]],
             rates: Mapping[str, tuple[float, float]]) -> dict[str, Any]:
    conid, scale, quantity = held["key"], held["multiplier"], held["quantity"]
    row = {**held, "price": None, "price_source": None, "prior_close": None, "quote_observed_at": _iso(moment),
           "quote_age_seconds": (as_of - moment).total_seconds() if as_of and moment and moment <= as_of else None,
           "market_data_type": None, "delayed": None, "status": "ok", **dict.fromkeys(PNL_FIELDS)}
    if quote is not None:
        row["price"], row["price_source"] = _live_price(quote, conid)
        row["prior_close"] = _positive(quote, f"The prior close of {conid}", "prior_close", "close")
        data_type = quote.get("market_data_type")
        row["market_data_type"] = data_type
        row["delayed"] = data_type in DELAYED_DATA_TYPES if data_type is not None else None
        if quote.get("currency") and _currency(quote["currency"], f"The quote currency of {conid}") != held["currency"]:
            problem = problem or "currency_mismatch"
    needs_price = quantity != 0      # a holding sold out today needs only its prior close
    if problem is None and needs_price and (row["quote_age_seconds"] or 0.0) > max_age:
        problem = "stale"
    if problem is None and needs_price and row["price"] is None:
        problem = "no_price"
    if problem is None and row["prior_close"] is None:
        problem = "no_prior_close"
    price, close = row["price"] if needs_price else row["prior_close"], row["prior_close"]
    prior_rate, rate = rates.get(held["currency"], (None, None))
    row.update({"fx_prior_close": prior_rate, "fx_rate": rate,
                "fx_return": rate / prior_rate - 1.0 if rate is not None else None,
                "market_value_local": quantity * scale * price if price is not None else None})
    row["market_value_reporting"] = (row["market_value_local"] * rate
                                     if row["market_value_local"] is not None and rate is not None else None)
    if problem is None:
        position = quantity * scale * (price - close)
        trading = math.fsum(q * scale * (close - p) for q, p in fills)
        row.update({"return": price / close - 1.0, "position_pnl_local": position, "trading_pnl_local": trading,
                    "pnl_local": position + trading, "opening_value_local": quantity * scale * close})
        if rate is None:
            problem = "no_fx_rate"
        else:
            row.update({"position_pnl_reporting": position * rate, "trading_pnl_reporting": trading * rate,
                        "price_pnl_reporting": (position + trading) * rate,
                        "fx_pnl_reporting": quantity * scale * close * (rate - prior_rate),
                        "opening_value_reporting": quantity * scale * close * prior_rate})
            row["pnl_reporting"] = row["price_pnl_reporting"] + row["fx_pnl_reporting"]
    row["status"] = problem or "ok"
    row["reason"] = EXCLUSION_REASONS.get(problem) if problem else None
    return row


def _freshness(rows: Sequence[Mapping[str, Any]], as_of: datetime | None, max_age: float) -> dict[str, Any]:
    ages = sorted(r["quote_age_seconds"] for r in rows if r["quote_age_seconds"] is not None)
    quoted = [r for r in rows if r["quote_observed_at"] is not None]
    return {
        "as_of": _iso(as_of),
        "max_quote_age_seconds": max_age,
        "holdings": len(rows),
        "quoted": len(quoted),
        "fresh": sum(1 for r in rows if r["quote_age_seconds"] is not None and r["quote_age_seconds"] <= max_age),
        "stale": sum(1 for r in rows if r["status"] == "stale"),
        "missing": sum(1 for r in rows if r["status"] in ("no_quote", "no_quote_time", "quote_after_as_of")),
        "delayed": sum(1 for r in rows if r["delayed"]),
        "oldest_age_seconds": ages[-1] if ages else None,
        "median_age_seconds": statistics.median(ages) if ages else None,
        "newest_age_seconds": ages[0] if ages else None,
    }


def intraday_pnl(
    positions: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
    quotes: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    fx_rates: Mapping[str, Mapping[str, float]] | None = None,
    reporting_currency: str,
    as_of: Any = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    executions: Sequence[Mapping[str, Any]] | None = None,
    cash: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Today's P&L of each holding against the prior close, and in total.

    ``positions`` are ``live_positions`` rows (``conid``, ``currency``, ``quantity``,
    optional ``multiplier`` and ``account_id``), summed by conid across accounts.
    ``quotes`` are ``live_quotes`` rows (``conid``, ``observed_at``, ``last_price``,
    ``bid_price``, ``ask_price``, ``prior_close``, ``market_data_type``); the newest at
    or before ``as_of`` is used, and ``as_of`` defaults to the newest quote time.
    The live price is the last trade, else the bid-ask midpoint.

    For quantity q, multiplier m, price P, prior close C and FX rates X₀ (prior close)
    and X₁ (now): P&L in the holding's currency is q·m·(P − C); in the reporting
    currency the price part is q·m·(P − C)·X₁ and the FX part q·m·C·(X₁ − X₀), which
    add up to q·m·(P·X₁ − C·X₀) (local P&L at today's rate plus FX on the opening
    value). With ``executions`` (today's ``live_executions``), each fill of signed
    quantity s·Q at price p adds s·Q·m·(C − p), so a position bought or sold today
    earns from its fill price, not the prior close; fills are converted at today's
    rate and commissions are left out. ``cash`` (currency → balance) adds the FX
    revaluation of foreign cash, balance × (X₁ − X₀).

    A holding is left out of the totals, with the reason and its quote's age, when its
    quote is missing, older than ``max_quote_age_seconds``, later than ``as_of``, in
    another currency, or lacks a live price or prior close. The collector writes a
    quote only when it changes, so the age is the time since it last changed; outside
    market hours every quote ages and the limit should be raised or the result read
    as of the close.
    """

    reporting = _currency(reporting_currency, "The reporting currency")
    max_age = _number(max_quote_age_seconds, "The quote age limit")
    if max_age <= 0:
        raise ModellingInputError("The quote age limit must be a positive number of seconds.")
    book = _holdings(positions)
    grouped = _quote_lists(quotes)
    fills, first_fill = _trades(executions)
    for conid in fills:
        if conid not in book:   # sold out today: no position left, but the fills made P&L
            fill = first_fill[conid]
            currency = fill.get("currency") or next(
                (q.get("currency") for q in grouped.get(conid, []) if q.get("currency")), None)
            if currency is None:
                raise ModellingInputError(f"Execution in {conid} has no position or quote to give its currency.")
            book[conid] = {"key": conid, "symbol": fill.get("symbol") or conid, "accounts": [], "quantity": 0.0,
                           "currency": _currency(currency, f"The currency of {conid}"),
                           "multiplier": _multiplier(fill, 0.0, conid)}
    rates = _fx(fx_rates, reporting)
    source = "given" if as_of is not None else None
    moment = _timestamp(as_of, "The as-of time") if as_of is not None else None
    if moment is None:
        times = [_timestamp(q["observed_at"], "A quote time") for rows in grouped.values() for q in rows
                 if not _missing(q.get("observed_at"))]
        moment, source = (max(times), "newest quote") if times else (None, None)
    rows = []
    for conid, held in sorted(book.items(), key=lambda item: str(item[1]["symbol"])):
        quote, quoted_at, problem = _latest_quote(grouped.get(conid, []), moment, conid)
        rows.append(_pnl_row(held, quote, quoted_at, problem, moment, max_age, fills.get(conid, []), rates))
    by_currency: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["status"] in ("ok", "no_fx_rate"):
            entry = by_currency.setdefault(row["currency"], {"pnl": 0.0, "position_pnl": 0.0, "trading_pnl": 0.0,
                                                             "holdings": 0})
            entry["pnl"] += row["pnl_local"]
            entry["position_pnl"] += row["position_pnl_local"]
            entry["trading_pnl"] += row["trading_pnl_local"]
            entry["holdings"] += 1
    counted = [r for r in rows if r["status"] == "ok"]
    cash_rows, cash_missing = [], []
    for currency, raw in (cash or {}).items():
        code = _currency(currency, "A cash currency")
        balance = _number(raw, f"The {code} cash balance")
        if code == reporting:
            continue
        if code not in rates:
            cash_missing.append(code)
            continue
        prior_rate, rate = rates[code]
        cash_rows.append({"currency": code, "balance": balance, "fx_pnl_reporting": balance * (rate - prior_rate)})
    total = {
        "price_pnl": math.fsum(r["price_pnl_reporting"] for r in counted),
        "position_pnl": math.fsum(r["position_pnl_reporting"] for r in counted),
        "trading_pnl": math.fsum(r["trading_pnl_reporting"] for r in counted),
        "fx_pnl": math.fsum(r["fx_pnl_reporting"] for r in counted),
        "cash_fx_pnl": math.fsum(r["fx_pnl_reporting"] for r in cash_rows),
    }
    total["pnl"] = total["price_pnl"] + total["fx_pnl"] + total["cash_fx_pnl"]
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "as_of": _iso(moment),
        "as_of_source": source,
        "reporting_currency": reporting,
        "holdings": rows,
        "totals_by_currency": dict(sorted(by_currency.items())),
        "total": total,
        "counted": len(counted),
        "excluded": [{"key": r["key"], "symbol": r["symbol"], "status": r["status"], "reason": r["reason"],
                      "quote_age_seconds": r["quote_age_seconds"]} for r in rows if r["status"] != "ok"],
        "cash": cash_rows,
        "cash_without_fx_rate": cash_missing,
        "freshness": _freshness(rows, moment, max_age),
        "method": (
            "Each holding's P&L since the prior close is quantity × multiplier × (live price − prior close), the live "
            "price being the last trade or else the bid-ask midpoint. In the reporting currency the price part is "
            "converted at today's FX rate and the FX part is the opening value × the change in the FX rate, so they add "
            "up to the whole. Fills today earn from their fill price, before commissions. Holdings whose quotes are "
            "missing, stale or unusable are listed with the reason and left out of the totals."
        ),
    }


# 2. P&L explain ------------------------------------------------------------------------


def _beta_table(exposures: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """holding → factor → beta, from a factor_risk_model result or a nested mapping."""

    if isinstance(exposures, Mapping) and {"names", "factors", "betas"} <= set(exposures):
        factors = [str(f) for f in exposures["factors"]]
        return {str(name): {f: _number(b, f"The beta of {name} to {f}") for f, b in zip(factors, row)}
                for name, row in zip(exposures["names"], exposures["betas"])}
    return {str(name): {str(f): _number(b, f"The beta of {name} to {f}") for f, b in (row or {}).items()}
            for name, row in exposures.items()}


def _market(used: Sequence[str], known: set[str], market_factor: str | None) -> str | None:
    """The market factor among those with betas; one named but carrying no beta adds nothing."""

    if market_factor is not None:
        if market_factor not in known:
            raise ModellingInputError(f"The market factor {market_factor!r} is not among the factors.")
        return market_factor if market_factor in used else None
    return next((name for name in MARKET_FACTOR_NAMES if name in used), None)


def pnl_explain(
    holding_returns: Mapping[str, float],
    exposures: Mapping[str, Any],
    factor_returns: Mapping[str, float],
    *,
    weights: Mapping[str, float],
    fx_returns: Mapping[str, float] | None = None,
    market_factor: str | None = None,
) -> dict[str, Any]:
    """Split a period's portfolio return into market, each factor, FX and stock-specific parts.

    Holding i has weight wᵢ (fraction of NAV at the start), return rᵢ in its own
    currency, FX return fᵢ (its currency against the reporting currency; 0 when not
    given) and betas βᵢₖ to factors with returns Fₖ. Its reporting-currency return
    (1 + rᵢ)(1 + fᵢ) − 1 splits, as in :func:`intraday_pnl`, into an FX part fᵢ and a
    local part rᵢ(1 + fᵢ), and the local part into Σₖ βᵢₖFₖ(1 + fᵢ) for the factors and
    the rest, (rᵢ − Σₖ βᵢₖFₖ)(1 + fᵢ), as stock-specific (it includes any alpha). The
    portfolio's parts are the weighted sums, so market + factors + FX + specific is
    Σ wᵢ[(1 + rᵢ)(1 + fᵢ) − 1] exactly; cash earns nothing here. The factor betas are
    those of a time-series factor model (Grinold and Kahn 2000), such as
    :func:`ibkr_etl.quant.holdings_risk.factor_risk_model` fitted on local returns.
    ``market_factor`` names the market factor; by default the first of Mkt-RF, MKT,
    Mkt, Market or market present. A holding with no betas is all specific.
    """

    w = {str(k): _number(v, f"The weight of {k}") for k, v in weights.items()}
    w = {k: v for k, v in w.items() if v != 0}
    if not w:
        raise ModellingInputError("Give at least one holding with a weight.")
    returns = {str(k): v for k, v in holding_returns.items()}
    lacking = sorted(k for k in w if _missing(returns.get(k)))
    if lacking:
        raise ModellingInputError(f"These weighted holdings have no return: {', '.join(lacking[:5])}.")
    fx = {str(k): _number(v, f"The FX return of {k}") for k, v in (fx_returns or {}).items() if not _missing(v)}
    betas = _beta_table(exposures)
    moves = {str(k): _number(v, f"The return of factor {k}") for k, v in factor_returns.items()}
    used = sorted({f for k in w for f, b in betas.get(k, {}).items() if b != 0})
    unmoved = [f for f in used if f not in moves]
    if unmoved:
        raise ModellingInputError(f"These factors have betas but no return for the period: {', '.join(unmoved[:5])}.")
    market = _market(used, set(moves) | {f for row in betas.values() for f in row}, market_factor)
    rows = []
    for name, weight in sorted(w.items(), key=lambda item: (-abs(item[1]), item[0])):
        r = _number(returns[name], f"The return of {name}")
        f = fx.get(name, 0.0)
        if r < -1.0 or f <= -1.0:
            raise ModellingInputError(f"The return of {name} is below -100%.")
        scale = weight * (1.0 + f)
        parts = {k: scale * b * moves[k] for k, b in betas.get(name, {}).items() if k in used}
        explained = math.fsum(b * moves[k] for k, b in betas.get(name, {}).items() if k in used)
        rows.append({"name": name, "weight": weight, "local_return": r, "fx_return": f,
                     "return": (1.0 + r) * (1.0 + f) - 1.0, "contribution": weight * ((1.0 + r) * (1.0 + f) - 1.0),
                     "factor_parts": parts, "fx": weight * f, "specific": scale * (r - explained),
                     "has_betas": name in betas})
    total = math.fsum(r["contribution"] for r in rows)
    factor_rows = []
    for factor in used:
        exposure = math.fsum(w[r["name"]] * (1.0 + r["fx_return"]) * betas[r["name"]].get(factor, 0.0)
                             for r in rows if r["name"] in betas)
        contribution = math.fsum(r["factor_parts"].get(factor, 0.0) for r in rows)
        factor_rows.append({"factor": factor, "is_market": factor == market, "exposure": exposure,
                            "factor_return": moves[factor], "contribution": contribution})
    fx_part = math.fsum(r["fx"] for r in rows)
    specific = math.fsum(r["specific"] for r in rows)
    parts = [{"part": f["factor"], "kind": "market" if f["is_market"] else "factor", "contribution": f["contribution"]}
             for f in sorted(factor_rows, key=lambda f: (not f["is_market"], -abs(f["contribution"])))]
    parts += [{"part": "FX", "kind": "fx", "contribution": fx_part},
              {"part": "Stock-specific", "kind": "specific", "contribution": specific}]
    for part in parts:
        part["share"] = part["contribution"] / total if abs(total) > TOLERANCE else None
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "total_return": total,
        "market": next((f["contribution"] for f in factor_rows if f["is_market"]), None),
        "market_factor": market,
        "factors": factor_rows,
        "fx": fx_part,
        "specific": specific,
        "parts": parts,
        "residual": total - math.fsum(p["contribution"] for p in parts),
        "holdings": rows,
        "cash_weight": 1.0 - math.fsum(w.values()),
        "no_factor_betas": sorted(k for k in w if k not in betas),
        "unused_factor_returns": sorted(set(moves) - set(used)),
        "method": (
            "Each holding's return is split into its betas × the factors' returns, its currency's move, and the rest "
            "(stock-specific, including any alpha); local parts are converted at the period-end FX rate and the FX "
            "part is the currency's move on the opening value. The portfolio's parts are the weighted sums and add up "
            "to its return exactly. The betas come from history, so the split between factors and stock-specific is "
            "a model."
        ),
        "disclaimer": DISCLAIMER,
    }


# 3. Limits -------------------------------------------------------------------------------


def _flatten(values: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Nested mappings become dotted names: {"factor_exposures": {"HML": 0.2}} → "factor_exposures.HML"."""

    flat = {}
    for key, value in values.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, f"{name}."))
        else:
            flat[name] = value
    return flat


def _bounds(raw: Any, name: str) -> tuple[str, float, float]:
    if isinstance(raw, (list, tuple)):
        if len(raw) != 2:
            raise ModellingInputError(f"The range limit for {name} must be a (lowest, highest) pair.")
        low, high = _number(raw[0], f"The lowest {name}"), _number(raw[1], f"The highest {name}")
        if low > high:
            raise ModellingInputError(f"The range limit for {name} has its lowest value above its highest.")
        return "range", low, high
    limit = _number(raw, f"The limit for {name}")
    if limit < 0:
        raise ModellingInputError(f"The limit for {name} must be zero or more; use a (lowest, highest) pair for a range.")
    return "maximum", -limit, limit


def _usage(value: float, low: float, high: float) -> float | None:
    """Share of the limit used: from zero to the bound on the value's side when the range holds zero,
    otherwise from the middle of the range to its edge. None means past a bound of zero."""

    if low <= 0.0 <= high:
        if abs(value) <= TOLERANCE:
            return 0.0
        bound = high if value > 0 else low
        return value / bound if bound != 0 else None
    middle, half = (low + high) / 2.0, (high - low) / 2.0
    if half <= 0:
        return 0.0 if abs(value - low) <= TOLERANCE else None
    return abs(value - middle) / half


def limit_check(metrics: Mapping[str, Any], limits: Mapping[str, Any], *,
                amber_at: float = DEFAULT_AMBER_AT) -> dict[str, Any]:
    """Each live metric against its limit: usage, a green, amber or red status, and the breaches.

    ``metrics`` and ``limits`` share names such as ``gross_exposure``,
    ``net_exposure``, ``largest_position_weight``, ``ex_ante_volatility``, ``var_1d``
    and ``liquidity_days``; nested mappings such as ``{"factor_exposures": {"HML":
    0.3}}`` are read as ``factor_exposures.HML``, and a ``"*"`` key in a nested limit
    applies to every name under it that has no limit of its own. A limit is a number
    L, the most the metric may be either way (|value| ≤ L, so a maximum for
    exposures, volatility and VaR and ±L for a signed exposure), or a (lowest,
    highest) range. Usage is |value| / L for a maximum; for a range it is measured
    from zero to the bound on the value's side when the range includes zero, and from
    the middle of the range to its edge otherwise. Red is outside the limit, amber is
    usage of ``amber_at`` (80%) or more, green is below. Breaches are sorted worst
    first by usage; one past a bound of zero has no usage and sorts first.
    """

    amber_at = _number(amber_at, "The amber level")
    if not 0.0 < amber_at <= 1.0:
        raise ModellingInputError("The amber level must be above 0 and at most 1, such as 0.8 for 80% of a limit.")
    flat_metrics = _flatten(metrics)
    flat_limits = {name: raw for name, raw in _flatten(limits).items() if not _missing(raw)}   # None: no limit
    defaults = {name[:-1]: raw for name, raw in flat_limits.items() if name.endswith("*")}
    named = {name: raw for name, raw in flat_limits.items() if not name.endswith("*")}
    checks, missing = [], []
    for name in sorted(set(named) | {m for m in flat_metrics if any(m.startswith(p) for p in defaults)}):
        raw_limit = named.get(name)
        if raw_limit is None:
            raw_limit = next(raw for prefix, raw in sorted(defaults.items(), key=lambda p: -len(p[0]))
                             if name.startswith(prefix))
        kind, low, high = _bounds(raw_limit, name)
        if _missing(flat_metrics.get(name)):
            missing.append(name)
            continue
        value = _number(flat_metrics[name], f"The metric {name}")
        breached = value > high + TOLERANCE or value < low - TOLERANCE
        usage = _usage(value, low, high)
        status = "red" if breached else "amber" if usage is not None and usage >= amber_at - TOLERANCE else "green"
        checks.append({"metric": name, "value": value, "limit_type": kind, "lower": low, "upper": high,
                       "usage": usage, "status": status,
                       "excess": value - high if value > high else value - low if value < low else 0.0})
    breaches = sorted((c for c in checks if c["status"] == "red"),
                      key=lambda c: (c["usage"] is not None, -(c["usage"] or 0.0), c["metric"]))
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in ("green", "amber", "red")}
    limited = set(named) | {c["metric"] for c in checks}
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "status": "red" if counts["red"] else "amber" if counts["amber"] else "green",
        "amber_at": amber_at,
        "counts": counts,
        "checks": checks,
        "breaches": breaches,
        "missing_metrics": missing,
        "metrics_without_limits": sorted(m for m in flat_metrics if m not in limited),
        "method": (
            "Each metric against its limit. A single number is the most the metric may be either way; a pair is the "
            "lowest and highest allowed. Usage is how much of the limit is used, from zero to the limit (or, for a "
            f"range that excludes zero, from its middle to its edge). Red is outside the limit, amber is {amber_at:.0%} "
            "or more of it, green is below that. A limit with no metric to check is listed as missing."
        ),
    }


# 4. The Desk snapshot ---------------------------------------------------------------------


def _live_exposures(rows: Sequence[Mapping[str, Any]], nav: float,
                    betas: Mapping[str, Mapping[str, float]] | None) -> dict[str, Any]:
    """Gross, net and largest weight at live prices; a stale holding keeps its last price rather than vanish."""

    valued = [r for r in rows if r["market_value_reporting"] is not None and r["quantity"] != 0]
    weights = {r["key"]: r["market_value_reporting"] / nav for r in valued}
    largest = max(weights.items(), key=lambda item: (abs(item[1]), item[0]), default=(None, None))
    factors = None
    if betas:
        names = sorted({f for k in weights for f in betas.get(k, {})})
        factors = {f: math.fsum(w * betas.get(k, {}).get(f, 0.0) for k, w in weights.items()) for f in names}
    return {
        "gross_exposure": math.fsum(abs(w) for w in weights.values()),
        "net_exposure": math.fsum(weights.values()),
        "largest_position_weight": abs(largest[1]) if largest[1] is not None else None,
        "largest_position": largest[0],
        "factor_exposures": factors,
        "weights": weights,
        "valued_at_stale_prices": sorted(r["key"] for r in valued if r["status"] != "ok"),
        "not_valued": sorted(r["key"] for r in rows if r["market_value_reporting"] is None and r["quantity"] != 0),
        "factor_betas_missing": sorted(k for k in weights if betas is not None and k not in betas),
    }


def _explain(pnl: Mapping[str, Any], nav: float, exposures: Mapping[str, Any],
             factor_returns: Mapping[str, float], market_factor: str | None) -> dict[str, Any]:
    counted = [r for r in pnl["holdings"] if r["status"] == "ok" and r["quantity"] != 0]
    if not counted:
        return {"available": False, "reason": "No holding has a fresh quote and prior close to explain."}
    result = pnl_explain({r["key"]: r["return"] for r in counted}, exposures, factor_returns,
                         weights={r["key"]: r["opening_value_reporting"] / nav for r in counted},
                         fx_returns={r["key"]: r["fx_return"] for r in counted}, market_factor=market_factor)
    explained = result["total_return"] * nav
    total = pnl["total"]
    result["amounts"] = {p["part"]: p["contribution"] * nav for p in result["parts"]}
    result["reconciliation"] = {
        "explained_position_pnl": explained,
        "trading_pnl": total["trading_pnl"],
        "cash_fx_pnl": total["cash_fx_pnl"],
        "total_pnl": total["pnl"],
        "difference": total["pnl"] - (explained + total["trading_pnl"] + total["cash_fx_pnl"]),
    }
    return result


def desk_snapshot(
    positions: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
    quotes: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    reporting_currency: str,
    nav: float | None = None,
    as_of: Any = None,
    fx_rates: Mapping[str, Mapping[str, float]] | None = None,
    executions: Sequence[Mapping[str, Any]] | None = None,
    cash: Mapping[str, float] | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    exposures: Mapping[str, Any] | None = None,
    factor_returns: Mapping[str, float] | None = None,
    market_factor: str | None = None,
    metrics: Mapping[str, Any] | None = None,
    limits: Mapping[str, Any] | None = None,
    amber_at: float = DEFAULT_AMBER_AT,
) -> dict[str, Any]:
    """Everything the Desk page shows, as one result.

    Runs :func:`intraday_pnl` on the live rows. With ``nav`` (reporting currency, at
    the prior close) it adds live gross and net exposure and the largest position as
    fractions of NAV, valuing a holding with a stale quote at its last price so it
    still counts, and, with factor ``exposures`` (betas), the portfolio's factor
    exposures. With ``factor_returns`` for today too (for example index moves from
    the benchmark quotes) it runs :func:`pnl_explain` on the holdings counted in the
    P&L, weighted by their prior-close value, and reconciles the explained P&L with
    the total. With ``limits`` it runs :func:`limit_check` on the live metrics plus
    ``metrics`` (such as ``ex_ante_volatility``, ``var_1d`` and ``liquidity_days``
    from the risk models), which override live metrics of the same name.
    """

    pnl = intraday_pnl(positions, quotes, fx_rates=fx_rates, reporting_currency=reporting_currency, as_of=as_of,
                       max_quote_age_seconds=max_quote_age_seconds, executions=executions, cash=cash)
    if nav is not None:
        nav = _number(nav, "The NAV")
        if nav <= 0:
            raise ModellingInputError("The NAV must be positive.")
    betas = _beta_table(exposures) if exposures is not None else None
    live = _live_exposures(pnl["holdings"], nav, betas) if nav is not None else None
    explain = None
    if factor_returns is not None and exposures is not None:
        explain = (_explain(pnl, nav, exposures, factor_returns, market_factor) if nav is not None
                   else {"available": False, "reason": "The P&L explain needs the NAV to weight the holdings."})
    combined = {}
    if live is not None:
        combined = {k: live[k] for k in ("gross_exposure", "net_exposure", "largest_position_weight")}
        if live["factor_exposures"] is not None:
            combined["factor_exposures"] = live["factor_exposures"]
    combined.update(metrics or {})
    checked = limit_check(combined, limits, amber_at=amber_at) if limits is not None else None
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "as_of": pnl["as_of"],
        "reporting_currency": pnl["reporting_currency"],
        "nav": nav,
        "freshness": pnl["freshness"],
        "pnl": pnl,
        "pnl_return": pnl["total"]["pnl"] / nav if nav is not None else None,
        "exposures": live,
        "metrics": combined,
        "explain": explain,
        "limits": checked,
        "method": (
            "Today's P&L from live positions and quotes against the prior close, with stale or missing quotes listed "
            "and left out; exposures at live prices as fractions of NAV; a factor explain of the P&L from the "
            "holdings' betas; and every metric against its limit."
        ),
        "disclaimer": DISCLAIMER,
    }
