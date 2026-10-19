"""Gold quant analytics: one stored payload per subject, date and section.

Wiring design sections 1 and 2; docs/QUANT_ANALYTICS.md has the whole picture. The job
scripts/build_quant_analytics.py (ibkrv2stg-quant-analytics, 09:30 UTC) writes the sections into
gold.quant_analytics_snapshots and the website reads them with read_section(). Nothing here runs
unless QUANT_ANALYTICS_BUILD=on, and the table exists only once the held migration
infra/medallion/drafts/quant_analytics.sql has run (076 on its day).

Inputs come from silver and gold only, the same reads the risk snapshot and the Modelling page
make: performance_comparison_daily on the primary benchmark, risk_free_rates,
subject_position_series (weights and daily marks), instrument_classifications, trades (physical
accounts), reference_fx_rates, subject_nav_series and the external flows in
subject_cash_activity_series. Settings and subject lists come from platform_subjects and
portfolio_benchmark_mapping, as for the risk snapshot.

load_inputs() reads the rows once per subject and date; prepare_inputs() turns them into one plain
input dict per section; build_<section>(inputs) is pure and returns the payload:

    section, model_id, model_version, as_of, available, reason, inputs, results

`inputs` is a short summary (observations, window, holdings) and `results` holds each engine's
own output, unchanged, under the name of the call. An engine's ModellingInputError makes that
call (or the whole section) unavailable, with its message as the reason. Alpha World MS and T and
profiled composites have their own HKD pipelines, so their sections are stored as unavailable.
Each section's source_data_sha256 is the sha256 of its canonical inputs and versions, so an
unchanged section is skipped on a rerun.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import logging
import math
import os
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import model_cards, publish_gate
from .fama_french import DEFAULT_REGION as DEFAULT_FACTOR_REGION, REGIONS as FACTOR_REGIONS
from .modelling import MODEL_VERSION as MODELLING_VERSION, ModellingInputError, holding_return_matrix
from .util import canonical_json, utc_now_iso

LOGGER = logging.getLogger(__name__)

QUANT_ANALYTICS_VERSION = "quant-analytics-v1"
BUILD_SETTING = "QUANT_ANALYTICS_BUILD"  # off (default) or on
TABLE = "gold.quant_analytics_snapshots"
FINGERPRINT_VERSION = 1
# The 09:30 job ends by 09:40, before the second certified run at 09:45: it starts no new
# subject-date once these minutes are spent (the Bicep replicaTimeout is 10 minutes).
MINUTES_SETTING = "QUANT_ANALYTICS_MINUTES"
DEFAULT_MINUTES = 8.0

# Windows and settings (wiring design section 2).
MARK_WINDOW_DAYS = 400        # daily marks: a year of sessions for the return matrix, and the YTD start
TRADE_WINDOW_DAYS = 365       # trades of the last year
HOLDING_OBSERVATIONS = 252    # daily returns per holding for the risk sections
FX_TOLERANCE_DAYS = 4         # as the risk snapshot: an FX rate at most four days old
SKILL_LEVEL = 0.90
SKILL_SAMPLES = 2000
DECISION_HORIZONS = (5, 21, 63)
PROFILE_HORIZON = 21
TAIL_LEVELS = (0.99, 0.995)
BACKTEST_LEVELS = (0.975, 0.99)
REGIME_HISTORY = 1260
STATISTICAL_FACTORS = 3
CORRELATED_HOLDINGS = 5       # the five largest holdings: 10 pairs
CLUSTER_THRESHOLD = 0.7
MAX_EXECUTION_ORDERS = 400    # the most recent orders of the trade window
OWN_PIPELINES = ("alpha-world-ms", "alpha-world-t")
CASH = "Cash"
BENCHMARK = "Benchmark"


@dataclass(frozen=True)
class Section:
    name: str
    page: str  # where the website shows it
    model_id: str  # its model card (ibkr_etl.model_cards)


SECTIONS: tuple[Section, ...] = (
    Section("skill", "performance", "skill-statistics"),
    Section("decisions", "performance", "decision-attribution"),
    Section("currency", "performance", "attribution"),
    Section("tail", "risk", "tail-risk"),
    Section("regime", "risk", "stress-engine"),
    Section("holdings_risk", "risk", "holdings-risk"),
    Section("correlation", "risk", "dynamic-correlation"),
    Section("concentration", "risk", "crowding"),
    Section("execution", "modelling", "execution-analysis"),
)
SECTION_BY_NAME = {section.name: section for section in SECTIONS}
SECTION_NAMES = tuple(SECTION_BY_NAME)

# Phase 3: built only when QUANT_ANALYTICS_PHASE3 sets the section to shadow or on AND its silver
# table exists (ibkr_etl.market_inputs). SECTION_NAMES and section_names() stay the nine v1 ones.
PHASE3_SECTIONS: tuple[Section, ...] = (
    Section("liquidity", "risk", "liquidity"),
    Section("factor_attribution", "performance", "attribution"),
    Section("macro", "risk", "macro-factors"),
    Section("crowding", "risk", "crowding"),
)
SECTION_BY_NAME.update({section.name: section for section in PHASE3_SECTIONS})
PHASE3_NAMES = tuple(section.name for section in PHASE3_SECTIONS)
LIQUIDITY_PARTICIPATION = 0.20   # days to liquidate trading at most 20% of a day's volume
LIQUIDITY_VAR_LEVEL = 0.99       # the tail section's one-day 99% VaR
ADV_SESSIONS = 20                # average daily volume over the last 20 sessions with volume
VOLUME_WINDOW_DAYS = 45          # calendar days of volumes read for those 20 sessions
FACTOR_REGION = DEFAULT_FACTOR_REGION   # the Modelling page's factor model default (Developed markets)
MACRO_YEARS = 3
MACRO_CURVE = "DGS10-DGS2"
MACRO_FACTORS = ("DGS10", MACRO_CURVE, "DTWEXBGS", "DCOILBRENTEU", "VIXCLS", "USDCNH")
MACRO_TRANSFORMS = {"DGS10": "diff_bp", MACRO_CURVE: "diff_bp", "DTWEXBGS": "log_return",
                    "DCOILBRENTEU": "log_return", "VIXCLS": "log_return", "USDCNH": "log_return"}
# Standard shocks (basis points for the yield and the curve, a fraction of the level otherwise).
MACRO_SHOCKS = {
    "rates_up_100bp": {"DGS10": 100.0},
    "curve_steeper_50bp": {MACRO_CURVE: 50.0},
    "dollar_up_5pct": {"DTWEXBGS": 0.05},
    "oil_down_20pct": {"DCOILBRENTEU": -0.20},
    "volatility_up_50pct": {"VIXCLS": 0.50},
    "yuan_down_5pct": {"USDCNH": 0.05},
    "risk_off": {"DGS10": -50.0, "DTWEXBGS": 0.05, "DCOILBRENTEU": -0.20, "VIXCLS": 0.50, "USDCNH": 0.03},
}
# Series the macro loader keeps only once their licence is confirmed (ibkr_etl.macro_series): until
# then the series is left out of the model, with this said, and so are the shocks to it alone.
MACRO_LICENSED = {
    "VIXCLS": "VIXCLS is Cboe copyright, so the macro series loader keeps it only when "
              "MACRO_SERIES_LICENSE_CONFIRMED=VIXCLS.",
}


def build_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """QUANT_ANALYTICS_BUILD=on. Anything else, a typo included, is off."""
    environ = os.environ if environ is None else environ
    return str(environ.get(BUILD_SETTING, "")).strip().lower() == "on"


def section_names(sections: Iterable[str] | None = None) -> tuple[str, ...]:
    if sections is None:
        return SECTION_NAMES
    chosen = tuple(dict.fromkeys(str(name) for name in sections))
    unknown = sorted(set(chosen) - set(SECTION_NAMES))
    if unknown:
        raise ValueError(f"Unknown quant section: {', '.join(unknown)}")
    return tuple(name for name in SECTION_NAMES if name in chosen)


# ---------------------------------------------------------------- small helpers


def _number(value: Any) -> float | None:
    """A finite float, or None for None, blanks, text that is not a number, NaN and infinity."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _day(value: Any) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def _on_or_before(series: Mapping[str, float], day: str) -> tuple[str, float] | None:
    """The last (date, value) of a date -> value mapping on or before `day`."""
    dates = sorted(series)
    index = bisect.bisect_right(dates, day) - 1
    return (dates[index], series[dates[index]]) if index >= 0 else None


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


class FxRates:
    """Reference FX rates (from -> to, per date), read at most FX_TOLERANCE_DAYS old.
    A pair not stored directly is read inverted, or through USD."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]):
        found: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
        for row in rows:
            day, rate = _day(row.get("date")), _number(row.get("rate"))
            pair = (str(row.get("from") or "").upper(), str(row.get("to") or "").upper())
            if day and rate and rate > 0 and day not in found[pair]:
                found[pair][day] = rate
        self._series = {pair: (sorted(values), [values[d] for d in sorted(values)])
                        for pair, values in found.items()}

    def _lookup(self, source: str, target: str, day: str) -> float | None:
        dates, rates = self._series.get((source, target), ((), ()))
        index = bisect.bisect_right(dates, day) - 1
        if index < 0 or dates[index] < _shift(day, -FX_TOLERANCE_DAYS):
            return None
        return rates[index]

    def rate(self, currency: str | None, target: str, day: str) -> float | None:
        source, target = str(currency or "").upper(), str(target or "").upper()
        if not source or not target:
            return None
        if source == target:
            return 1.0
        direct = self._lookup(source, target, day)
        if direct:
            return direct
        inverse = self._lookup(target, source, day)
        if inverse:
            return 1.0 / inverse
        if "USD" not in (source, target):
            to_usd, target_usd = self.rate(source, "USD", day), self.rate(target, "USD", day)
            if to_usd and target_usd:
                return to_usd / target_usd
        return None


def finite_copy(value: Any) -> tuple[Any, int]:
    """The value with every NaN or infinity replaced by None, and how many were replaced."""
    replaced = 0

    def walk(item: Any) -> Any:
        nonlocal replaced
        if isinstance(item, float) and not math.isfinite(item):
            replaced += 1
            return None
        if isinstance(item, Mapping):
            return {str(k): walk(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(v) for v in item]
        return item

    return walk(value), replaced


def _unavailable_result(error: Exception) -> dict[str, Any]:
    return {"available": False, "reason": str(error)}


def _call(results: dict[str, Any], name: str, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one engine call into results[name]; its ModellingInputError makes it unavailable."""
    try:
        output = function(*args, **kwargs)
    except ModellingInputError as error:
        results[name] = _unavailable_result(error)
        return None
    results[name] = output
    return output


def _payload(section: str, inputs: Mapping[str, Any], *, summary: Mapping[str, Any] | None = None,
             results: Mapping[str, Any] | None = None, reason: str | None = None) -> dict[str, Any]:
    """The section payload. Available when at least one engine call worked."""
    spec = SECTION_BY_NAME[section]
    results = dict(results or {})
    worked = [value for value in results.values()
              if not (isinstance(value, Mapping) and value.get("available") is False)]
    available = bool(worked) and reason is None
    if not available and reason is None:
        reasons = [value.get("reason") for value in results.values()
                   if isinstance(value, Mapping) and value.get("reason")]
        reason = reasons[0] if reasons else "Nothing could be computed for this section."
    return {
        "section": section, "model_id": spec.model_id, "model_version": model_cards.version(spec.model_id),
        "as_of": inputs.get("as_of"), "available": available, "reason": None if available else reason,
        "inputs": dict(summary or {}), "results": results,
    }


def _not_covered(section: str, inputs: Mapping[str, Any]) -> dict[str, Any] | None:
    reason = inputs.get("unavailable")
    return _payload(section, inputs, reason=str(reason)) if reason else None


# ---------------------------------------------------------------- builders (pure)


def build_skill(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """skill_summary: daily portfolio returns against the primary benchmark, net of the daily risk-free rate."""
    from .quant.skill_statistics import skill_summary

    stopped = _not_covered("skill", inputs)
    if stopped:
        return stopped
    portfolio, benchmark = dict(inputs.get("portfolio") or {}), dict(inputs.get("benchmark") or {})
    days = sorted(portfolio)
    summary = {"observations": len(days), "window": [days[0], days[-1]] if days else None,
               "benchmark_id": inputs.get("benchmark_id"), "benchmark_observations": len(benchmark),
               "risk_free_annual_rate": inputs.get("risk_free_annual_rate"), "trials": 1,
               "level": SKILL_LEVEL, "samples": SKILL_SAMPLES}
    results: dict[str, Any] = {}
    _call(results, "skill_summary", skill_summary, portfolio, benchmark or None,
          risk_free_daily=float(inputs.get("risk_free_daily") or 0.0), trials=1, level=SKILL_LEVEL,
          samples=SKILL_SAMPLES)
    return _payload("skill", inputs, summary=summary, results=results)


def build_decisions(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """trade_value_added, skill_profile, timing_cost and counterfactual_portfolios, in the
    reporting currency."""
    from .quant.decision_attribution import (counterfactual_portfolios, holding_forward_returns,
                                             results_at_horizon, skill_profile, timing_cost,
                                             trade_value_added)

    stopped = _not_covered("decisions", inputs)
    if stopped:
        return stopped
    trades, closes = list(inputs.get("trades") or []), dict(inputs.get("closes") or {})
    results: dict[str, Any] = {}
    no_trades = inputs.get("no_trades")
    if no_trades:
        for name in ("trade_value_added", "skill_profile", "timing_cost"):
            results[name] = {"available": False, "reason": str(no_trades)}
    else:
        attribution = _call(results, "trade_value_added", trade_value_added, trades, closes,
                            horizons=DECISION_HORIZONS)
        if attribution is None:
            results["skill_profile"] = results["trade_value_added"]
        else:
            try:
                rows = results_at_horizon(attribution, PROFILE_HORIZON)
                panel = holding_forward_returns(inputs.get("sizes") or {}, closes, horizon=PROFILE_HORIZON)
                results["skill_profile"] = skill_profile(rows, panel)
            except ModellingInputError as error:
                results["skill_profile"] = _unavailable_result(error)
        _call(results, "timing_cost", timing_cost, trades, closes)
    counterfactual = dict(inputs.get("counterfactual") or {})
    if counterfactual.get("unavailable"):
        results["counterfactual_portfolios"] = {"available": False, "reason": counterfactual["unavailable"]}
    else:
        _call(results, "counterfactual_portfolios", counterfactual_portfolios, counterfactual["start_date"],
              counterfactual.get("start_holdings") or {}, closes, counterfactual.get("nav") or {},
              flows=[tuple(flow) for flow in counterfactual.get("flows") or []],
              benchmark=counterfactual.get("benchmark") or None)
    days = sorted(str(trade["trade_date"]) for trade in trades)
    summary = {"trades": len(trades), "window": [days[0], days[-1]] if days else None,
               "horizons": list(DECISION_HORIZONS), "profile_horizon": PROFILE_HORIZON,
               "currency": inputs.get("currency"), "trades_left_out": inputs.get("trades_left_out", 0),
               "counterfactual_start": counterfactual.get("start_date")}
    return _payload("decisions", inputs, summary=summary, results=results)


def _period_attribution(period: Mapping[str, Any], base: str) -> dict[str, Any]:
    """currency_attribution of one period: the opening holdings by currency, held to the end,
    against the benchmark as one segment in the reporting currency."""
    from .quant.attribution import currency_attribution

    if period.get("unavailable"):
        raise ModellingInputError(str(period["unavailable"]))
    nav = _number(period.get("start_nav"))
    if not nav or nav <= 0:
        raise ModellingInputError("There is no positive NAV at the start of the period.")
    start, end = _number(period.get("benchmark_start")), _number(period.get("benchmark_end"))
    if not start or not end or start <= 0:
        raise ModellingInputError("The benchmark has no level at the start and the end of the period.")
    begin: dict[str, float] = defaultdict(float)
    finish: dict[str, float] = defaultdict(float)
    for holding in period.get("holdings") or []:
        currency = str(holding["currency"]).upper()
        begin[currency] += float(holding["start_value"])
        finish[currency] += float(holding["end_value"])
    weights, returns, currencies = {}, {}, {CASH: base, BENCHMARK: base}
    for currency in sorted(begin):
        if begin[currency] == 0:
            continue
        segment = f"{currency} holdings"
        weights[segment] = begin[currency] / nav
        returns[segment] = finish[currency] / begin[currency] - 1.0
        currencies[segment] = currency
    weights[CASH] = 1.0 - math.fsum(weights.values())
    returns[CASH] = 0.0
    moves = {code: float(move) for code, move in (period.get("currency_moves") or {}).items() if code != base}
    return currency_attribution(weights, returns, {BENCHMARK: 1.0}, {BENCHMARK: end / start - 1.0},
                                currencies, moves, base_currency=base)


def build_currency(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """currency_attribution for month to date (mtd) and year to date (ytd)."""
    stopped = _not_covered("currency", inputs)
    if stopped:
        return stopped
    base = str(inputs.get("base_currency") or "").upper()
    periods = dict(inputs.get("periods") or {})
    results: dict[str, Any] = {}
    for name in ("mtd", "ytd"):
        _call(results, name, _period_attribution, periods.get(name) or {"unavailable": "No period inputs."}, base)
    summary = {"base_currency": base, "method": "opening holdings by currency, held to the end of the period",
               **{name: {key: (periods.get(name) or {}).get(key) for key in ("start_date", "end_date")}
                  | {"holdings": len((periods.get(name) or {}).get("holdings") or [])}
                  for name in ("mtd", "ytd")}}
    return _payload("currency", inputs, summary=summary, results=results)


def build_tail(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """evt_var_es (99%, 99.5%), tail_backtest_report (97.5%, 99) and today's one-day forecasts."""
    from .quant.tail_risk import evt_var_es, tail_backtest_report

    stopped = _not_covered("tail", inputs)
    if stopped:
        return stopped
    daily = [(str(day), float(value)) for day, value in inputs.get("returns") or []]
    results: dict[str, Any] = {}
    fitted = _call(results, "evt_var_es", evt_var_es, [value for _, value in daily], levels=TAIL_LEVELS)
    _call(results, "tail_backtest_report", tail_backtest_report, daily, levels=BACKTEST_LEVELS)
    if fitted is not None:
        basis = "conditional" if fitted.get("conditional") else "unconditional"
        source = fitted[basis]
        results["forecasts"] = {
            "as_of": inputs.get("as_of"), "horizon_days": 1, "basis": basis,
            "next_day_volatility": source.get("next_day_volatility"),
            "levels": {key: {"level": row.get("level"), "var": row.get("var"),
                             "expected_shortfall": row.get("expected_shortfall")}
                       for key, row in (source.get("levels") or {}).items()},
        }
    summary = {"observations": len(daily), "window": [daily[0][0], daily[-1][0]] if daily else None,
               "levels": list(TAIL_LEVELS), "backtest_levels": list(BACKTEST_LEVELS)}
    return _payload("tail", inputs, summary=summary, results=results)


def build_regime(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """regime_risk, with the state probabilities of risk_forecasting's regime model."""
    from .quant.stress_engine import regime_risk
    from .risk_forecasting import REGIME_LABELS, choose_regimes

    stopped = _not_covered("regime", inputs)
    if stopped:
        return stopped
    daily = [(str(day), float(value)) for day, value in (inputs.get("returns") or [])[-REGIME_HISTORY:]]
    values = [value for _, value in daily]
    summary = {"observations": len(daily), "window": [daily[0][0], daily[-1][0]] if daily else None}
    results: dict[str, Any] = {}
    try:
        model = choose_regimes(values)
    except ModellingInputError as error:
        results["regime_model"] = _unavailable_result(error)
        results["regime_risk"] = _unavailable_result(error)
        return _payload("regime", inputs, summary=summary, results=results)
    states = len(model["means"])
    labels = list(REGIME_LABELS.get(states) or [f"Regime {i + 1}" for i in range(states)])
    results["regime_model"] = {
        "available": True, "states": states, "labels": labels,
        **{key: model[key] for key in ("means", "sds", "transition", "shares", "bic", "log_likelihood")},
        "today": dict(zip(labels, model["filtered"][-1])) if model.get("filtered") else None,
    }
    if _call(results, "regime_risk", regime_risk, values, model["filtered"], labels=labels) is None:
        # The model alone is not the section: it is there to weight the days.
        return _payload("regime", inputs, summary=summary, results=results, reason=results["regime_risk"]["reason"])
    return _payload("regime", inputs, summary=summary, results=results)


def _holding_matrix(inputs: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, float]]:
    """The holdings' daily return matrix (modelling.holding_return_matrix) and their weights."""
    holdings = list(inputs.get("holdings") or [])
    if not holdings:
        raise ModellingInputError(str(inputs.get("no_holdings") or "There are no holdings on this date."))
    prices = {str(h["name"]): (inputs.get("prices") or {}).get(str(h["name"])) or {} for h in holdings}
    matrix = holding_return_matrix(prices, observations=HOLDING_OBSERVATIONS)
    weights = {str(h["name"]): float(h["weight"]) for h in holdings}
    return matrix, weights


def _holdings_summary(inputs: Mapping[str, Any], matrix: Mapping[str, Any] | None) -> dict[str, Any]:
    holdings = list(inputs.get("holdings") or [])
    summary: dict[str, Any] = {
        "holdings_date": inputs.get("holdings_date"), "holdings": len(holdings),
        "invested_weight": math.fsum(float(h["weight"]) for h in holdings),
        "names": {str(h["name"]): {"conid": h["conid"], "weight": h["weight"], "sector": h.get("sector"),
                                   "currency": h.get("currency")} for h in holdings},
    }
    if matrix is not None:
        dates = matrix.get("dates") or []
        summary.update(observations=len(dates), window=[dates[0], dates[-1]] if dates else None,
                       modelled=len(matrix.get("returns") or {}), excluded=list(matrix.get("excluded") or []),
                       excluded_price_breaks=matrix.get("excluded_price_breaks", 0))
    return summary


def build_holdings_risk(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """ledoit_wolf_covariance -> ex_ante_risk (contributions by holding), and statistical_factors (k=3)."""
    from .quant.holdings_risk import ex_ante_risk, ledoit_wolf_covariance, statistical_factors

    stopped = _not_covered("holdings_risk", inputs)
    if stopped:
        return stopped
    try:
        matrix, weights = _holding_matrix(inputs)
    except ModellingInputError as error:
        return _payload("holdings_risk", inputs, summary=_holdings_summary(inputs, None), reason=str(error))
    columns = matrix["returns"]
    results: dict[str, Any] = {}
    covariance = _call(results, "ledoit_wolf_covariance", ledoit_wolf_covariance, columns) if columns else None
    if not columns:
        results["ledoit_wolf_covariance"] = {"available": False,
                                             "reason": "No holding has enough daily marks over the last year."}
    if covariance is None:
        results["ex_ante_risk"] = results["ledoit_wolf_covariance"]
    else:
        _call(results, "ex_ante_risk", ex_ante_risk, {name: weights[name] for name in columns}, covariance)
    if columns:
        _call(results, "statistical_factors", statistical_factors, columns, STATISTICAL_FACTORS)
    else:
        results["statistical_factors"] = results["ledoit_wolf_covariance"]
    return _payload("holdings_risk", inputs, summary=_holdings_summary(inputs, matrix), results=results)


def build_correlation(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """dcc_correlation on the five largest holdings with a year of returns (10 pairs)."""
    from .quant.dynamic_correlation import dcc_correlation

    stopped = _not_covered("correlation", inputs)
    if stopped:
        return stopped
    try:
        matrix, weights = _holding_matrix(inputs)
    except ModellingInputError as error:
        return _payload("correlation", inputs, summary=_holdings_summary(inputs, None), reason=str(error))
    columns = matrix["returns"]
    largest = sorted(columns, key=lambda name: (-abs(weights[name]), name))[:CORRELATED_HOLDINGS]
    summary = {**_holdings_summary(inputs, matrix), "correlated": largest}
    results: dict[str, Any] = {}
    _call(results, "dcc_correlation", dcc_correlation, {name: columns[name] for name in largest},
          weights={name: weights[name] for name in largest}, dates=matrix["dates"])
    return _payload("correlation", inputs, summary=summary, results=results)


def build_concentration(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """concentration by holding and by sector, and correlation_clusters (threshold 0.7)."""
    from .quant.crowding import concentration, correlation_clusters

    stopped = _not_covered("concentration", inputs)
    if stopped:
        return stopped
    holdings = list(inputs.get("holdings") or [])
    if not holdings:
        reason = str(inputs.get("no_holdings") or "There are no holdings on this date.")
        return _payload("concentration", inputs, summary=_holdings_summary(inputs, None), reason=reason)
    weights = {str(h["name"]): float(h["weight"]) for h in holdings}
    sectors: dict[str, float] = defaultdict(float)
    for holding in holdings:
        sectors[str(holding.get("sector") or "Unclassified")] += float(holding["weight"])
    results: dict[str, Any] = {}
    _call(results, "concentration", concentration, weights)
    _call(results, "concentration_by_sector", concentration, dict(sorted(sectors.items())))
    matrix = None
    try:
        matrix, _ = _holding_matrix(inputs)
        columns = matrix["returns"]
        if not columns:
            raise ModellingInputError("No holding has enough daily marks over the last year.")
        _call(results, "correlation_clusters", correlation_clusters, columns, threshold=CLUSTER_THRESHOLD,
              weights={name: weights[name] for name in columns})
    except ModellingInputError as error:
        results["correlation_clusters"] = _unavailable_result(error)
    return _payload("concentration", inputs, summary=_holdings_summary(inputs, matrix), results=results)


def _orders(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Fills grouped into orders: one per trade date, holding and side."""
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for trade in trades:
        side = str(trade.get("side") or "").upper()
        key = (str(trade["trade_date"]), str(trade["conid"]), side)
        order = grouped.setdefault(key, {"trade_date": key[0], "conid": key[1], "side": side,
                                         "symbol": trade.get("symbol") or key[1], "fills": []})
        order["fills"].append({"quantity": abs(float(trade["quantity"])), "price": float(trade["price"]),
                               "commission": abs(float(trade.get("commission") or 0.0))})
    return [grouped[key] for key in sorted(grouped)]


def build_execution(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """implementation_shortfall per order (decision and arrival = the previous close, final = the
    day's close), summarised by month."""
    from .quant.execution_analysis import implementation_shortfall

    stopped = _not_covered("execution", inputs)
    if stopped:
        return stopped
    if inputs.get("no_trades"):
        return _payload("execution", inputs, summary={"orders": 0}, reason=str(inputs["no_trades"]))
    closes = dict(inputs.get("closes") or {})
    orders = _orders(list(inputs.get("trades") or []))
    left_out = max(0, len(orders) - MAX_EXECUTION_ORDERS)
    orders = orders[left_out:]
    measured, skipped = [], []
    months: dict[str, dict[str, float]] = {}
    for order in orders:
        series = closes.get(order["conid"]) or {}
        dates = sorted(series)
        index = bisect.bisect_left(dates, order["trade_date"]) - 1
        if index < 0:
            skipped.append({**{k: order[k] for k in ("trade_date", "conid", "symbol", "side")},
                            "reason": "no close before the trade date"})
            continue
        decision = float(series[dates[index]])
        final = float(series.get(order["trade_date"], decision))
        quantity = math.fsum(fill["quantity"] for fill in order["fills"])
        try:
            result = implementation_shortfall({"side": order["side"], "quantity": quantity}, order["fills"],
                                              decision_price=decision, arrival_price=decision, final_price=final)
        except ModellingInputError as error:
            skipped.append({**{k: order[k] for k in ("trade_date", "conid", "symbol", "side")}, "reason": str(error)})
            continue
        measured.append({**{k: order[k] for k in ("trade_date", "conid", "symbol", "side")},
                         "fills": len(order["fills"]), "decision_date": dates[index],
                         "implementation_shortfall": result})
        month = months.setdefault(order["trade_date"][:7], {"orders": 0, "paper_value": 0.0, "delay": 0.0,
                                                            "trading": 0.0, "commission": 0.0,
                                                            "opportunity": 0.0, "total": 0.0})
        month["orders"] += 1
        month["paper_value"] += result["paper_value"]
        for part in ("delay", "trading", "commission", "opportunity"):
            month[part] += result["components"][part]["amount"]
        month["total"] += result["total"]["amount"]
    by_month = [{"month": key, **value,
                 "total_bps": value["total"] / value["paper_value"] * 10_000 if value["paper_value"] else None}
                for key, value in sorted(months.items())]
    results: dict[str, Any] = {}
    if measured:
        results = {"implementation_shortfall": measured, "by_month": by_month, "skipped": skipped}
    summary = {"orders": len(measured), "orders_skipped": len(skipped), "orders_left_out": left_out,
               "window": [orders[0]["trade_date"], orders[-1]["trade_date"]] if orders else None,
               "currency": inputs.get("currency"),
               "prices": "decision and arrival: the previous close; final: the close on the trade date"}
    reason = None if measured else "No order has a close before its trade date to measure it against."
    return _payload("execution", inputs, summary=summary, results=results, reason=reason)


BUILDERS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "skill": build_skill, "decisions": build_decisions, "currency": build_currency, "tail": build_tail,
    "regime": build_regime, "holdings_risk": build_holdings_risk, "correlation": build_correlation,
    "concentration": build_concentration, "execution": build_execution,
}


# ---------------------------------------------------------------- phase 3 builders (pure)
#
# Built only for the sections QUANT_ANALYTICS_PHASE3 sets to shadow or on, and only once the
# section's silver table exists (ibkr_etl.market_inputs). docs/QUANT_ANALYTICS.md, "Phase 3".


def build_liquidity(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """average_daily_volume per holding (its last 20 sessions with volume), days_to_liquidate at
    20% participation, and liquidity_adjusted_var on the one-day 99% VaR the tail section
    forecasts (evt_var_es: conditional when the GARCH fit worked, else unconditional).

    A holding without volumes in silver is listed under not_covered and counts as not sellable
    (days_to_liquidate's no_volume_data). A holding's volumes are those stored under its conid.
    With none covered (daily_volumes holds only the benchmarks until the holdings' own fetch,
    DAILY_VOLUMES_FETCH, has run for them) the volume figures are unavailable and
    the section still gives liquidity_adjusted_var, so it is never held back for volumes."""
    from .quant.liquidity import average_daily_volume, days_to_liquidate, liquidity_adjusted_var
    from .quant.tail_risk import evt_var_es

    stopped = _not_covered("liquidity", inputs)
    if stopped:
        return stopped
    holdings = list(inputs.get("holdings") or [])
    volumes = dict(inputs.get("volumes") or {})
    summary: dict[str, Any] = {
        "holdings_date": inputs.get("holdings_date"), "holdings": len(holdings),
        "participation": LIQUIDITY_PARTICIPATION, "volume_window_sessions": ADV_SESSIONS,
        "with_volumes": sorted(name for name in volumes if volumes[name]),
        "volume_instruments": dict(inputs.get("volume_instruments") or {}),
        "var_level": LIQUIDITY_VAR_LEVEL, "var_horizon_days": 1, "var_basis": None,
        "spreads": "No bid-ask spreads are stored in silver, so every holding is listed under no_spread_data "
                   "and the spread cost is 0: liquidity-adjusted VaR equals VaR until spreads are loaded.",
    }
    if not holdings:
        return _payload("liquidity", inputs, summary=summary,
                        reason=str(inputs.get("no_holdings") or "There are no holdings on this date."))
    results: dict[str, Any] = {}
    adv: dict[str, Any] = {}
    for holding in holdings:
        series = volumes.get(str(holding["name"]))
        if series:
            try:
                adv[str(holding["name"])] = average_daily_volume(series, window=ADV_SESSIONS)
            except ModellingInputError as error:
                adv[str(holding["name"])] = _unavailable_result(error)
    known = {name: row for name, row in adv.items() if row.get("average_daily_volume") is not None}
    missing = (f"No daily volume in silver (daily_volumes) in the {VOLUME_WINDOW_DAYS} days to "
               f"{inputs.get('holdings_date')}.")
    not_covered = [{"name": h["name"], "conid": h.get("conid"), "weight": h.get("weight"), "reason": missing}
                   for h in holdings if str(h["name"]) not in known]
    summary.update(not_covered=not_covered,
                   not_covered_weight=math.fsum(float(h.get("weight") or 0.0) for h in not_covered))
    no_volume = None if known else (
        f"No holding has a daily volume in silver (daily_volumes) in the {VOLUME_WINDOW_DAYS} days to "
        f"{inputs.get('holdings_date')}.")
    if no_volume:
        results["average_daily_volume"] = {"available": False, "reason": no_volume}
        results["days_to_liquidate"] = {"available": False, "reason": no_volume}
    else:
        results["average_daily_volume"] = adv
        positions = {str(h["name"]): {"quantity": float(h.get("quantity") or 0.0), "value": float(h["market_value"]),
                                      "average_daily_volume": (known.get(str(h["name"])) or {}).get(
                                          "average_daily_volume")}
                     for h in holdings}
        _call(results, "days_to_liquidate", days_to_liquidate, positions, participation=LIQUIDITY_PARTICIPATION)
    daily = [float(value) for _, value in inputs.get("returns") or []]
    if inputs.get("no_returns"):
        results["liquidity_adjusted_var"] = {"available": False, "reason": str(inputs["no_returns"])}
    else:
        try:
            fitted = evt_var_es(daily, levels=TAIL_LEVELS)
            basis = "conditional" if fitted.get("conditional") else "unconditional"
            var = fitted[basis]["levels"][f"{round(LIQUIDITY_VAR_LEVEL * 100, 4):g}"]["var"]
            summary.update(var_basis=basis, var_observations=fitted.get("observations"))
            _call(results, "liquidity_adjusted_var", liquidity_adjusted_var, var,
                  {str(h["name"]): {"value": float(h["market_value"]), "spreads": None} for h in holdings})
        except ModellingInputError as error:
            results["liquidity_adjusted_var"] = _unavailable_result(error)
    return _payload("liquidity", inputs, summary=summary, results=results)


def _attribution_days(inputs: Mapping[str, Any], start: str, names: Sequence[str]) -> list[str]:
    """Days from `start` to as_of with a portfolio return, every factor and the risk-free rate."""
    returns, factors = inputs.get("returns") or {}, inputs.get("factors") or {}
    needed = (*names, "RF")
    return [day for day in sorted(returns) if start <= day <= str(inputs["as_of"])
            and all(name in (factors.get(day) or {}) for name in needed)]


def build_factor_attribution(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """factor_attribution of the daily portfolio returns, less the factor file's RF, on the French
    factors of the Modelling page's default region (Developed markets): year to date (ytd, from 1
    January) and the past year (past_year, the 365 days to as_of)."""
    from .modelling import FACTOR_NAMES
    from .quant.attribution import factor_attribution

    stopped = _not_covered("factor_attribution", inputs)
    if stopped:
        return stopped
    factors = dict(inputs.get("factors") or {})
    names = [name for name in FACTOR_NAMES if any(name in row for row in factors.values())]
    as_of = str(inputs["as_of"])
    starts = {"ytd": date.fromisoformat(as_of).replace(month=1, day=1).isoformat(),
              "past_year": _shift(as_of, -365)}
    summary: dict[str, Any] = {
        "region": inputs.get("region"), "region_label": inputs.get("region_label"), "factors": names,
        "factor_sources": inputs.get("sources"), "factor_vintages": inputs.get("vintages"),
        "factor_data_through": max(factors) if factors else None, "returns": "daily, less the daily RF",
    }
    if "Mkt-RF" not in names:
        return _payload("factor_attribution", inputs, summary=summary,
                        reason=f"There are no {inputs.get('region_label') or 'region'} market factor returns in "
                               f"silver (factor_returns) for the year to {as_of}.")
    results: dict[str, Any] = {}
    for period, start in starts.items():
        days = _attribution_days(inputs, start, names)
        summary[period] = {"start": start, "observations": len(days),
                           "window": [days[0], days[-1]] if days else None}
        excess = [float(inputs["returns"][day]) - float(factors[day]["RF"]) for day in days]
        _call(results, period, factor_attribution, excess,
              {name: [float(factors[day][name]) for day in days] for name in names}, dates=days)
    return _payload("factor_attribution", inputs, summary=summary, results=results)


def _weekly_dates(days: Iterable[str]) -> list[str]:
    """The last of the given days in each ISO week."""
    weeks: dict[tuple[int, int], str] = {}
    for day in sorted(days):
        weeks[date.fromisoformat(day).isocalendar()[:2]] = day
    return sorted(weeks.values())


def _macro_missing_reason(name: str, levels: Mapping[str, Any]) -> str:
    """Why a macro series is not in the model (it has no level in silver macro_series for the window)."""
    if name == MACRO_CURVE:
        absent = [part for part in ("DGS10", "DGS2") if not levels.get(part)]
        return (f"Needs DGS10 and DGS2; {' and '.join(absent)} not in silver macro_series for the window."
                if absent else "DGS10 and DGS2 have no day in common in the window.")
    return " ".join(["Not in silver macro_series for the window.", MACRO_LICENSED.get(name, "")]).strip()


def build_macro(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """macro_exposures of weekly portfolio returns on the US 10-year yield (DGS10), the 10y-2y curve,
    the broad dollar (DTWEXBGS), Brent (DCOILBRENTEU), the VIX (VIXCLS) and USD/CNH, over up to
    three years, and macro_sensitivity for each of MACRO_SHOCKS.

    A series with no levels (the VIX until its licence is confirmed, MACRO_LICENSED) is left out of
    the model and named with its reason in inputs.factors_missing_reasons; a shock to such series
    alone is left out too (inputs.shocks_left_out), and a shock in part to them, the risk-off week,
    is worked out on the rest (its result's factors_not_in_model). The section never fails for it."""
    from .quant.macro_factors import align_series, macro_exposures, macro_sensitivity

    stopped = _not_covered("macro", inputs)
    if stopped:
        return stopped
    levels = {name: dict(values) for name, values in (inputs.get("levels") or {}).items()}
    if levels.get("DGS10") and levels.get("DGS2"):
        levels[MACRO_CURVE] = {day: levels["DGS10"][day] - levels["DGS2"][day]
                               for day in levels["DGS10"] if day in levels["DGS2"]}
    factors = {name: levels[name] for name in MACRO_FACTORS if levels.get(name)}
    missing = [name for name in MACRO_FACTORS if name not in factors]
    returns = {str(day): float(value) for day, value in (inputs.get("returns") or {}).items()}
    summary: dict[str, Any] = {
        "frequency": "weekly: the last day in each week with a portfolio return and every series",
        "years": MACRO_YEARS, "factors": list(factors), "factors_missing": missing,
        "factors_missing_reasons": {name: _macro_missing_reason(name, levels) for name in missing},
        "transforms": {name: MACRO_TRANSFORMS[name] for name in factors},
        "units": inputs.get("units"), "sources": inputs.get("sources"),
        "shocks": {name: dict(shocks) for name, shocks in MACRO_SHOCKS.items()},
        "shocks_left_out": {},
    }
    if not factors:
        return _payload("macro", inputs, summary=summary,
                        reason="None of the macro series is in silver (macro_series) for the window.")
    if not returns:
        return _payload("macro", inputs, summary=summary,
                        reason=str(inputs.get("no_returns") or "There are no daily portfolio returns."))
    common = set(returns).intersection(*(set(series) for series in factors.values()))
    selected = _weekly_dates(common)
    results: dict[str, Any] = {}
    try:
        if len(selected) < 2:
            raise ModellingInputError("The portfolio and the macro series have fewer than two weeks in common.")
        aligned = align_series({day: value for day, value in returns.items() if day >= selected[0]},
                               {name: {day: series[day] for day in selected} for name, series in factors.items()},
                               transforms={name: MACRO_TRANSFORMS[name] for name in factors})
    except ModellingInputError as error:
        results["macro_exposures"] = _unavailable_result(error)
        results["macro_sensitivity"] = results["macro_exposures"]
        return _payload("macro", inputs, summary=summary, results=results)
    summary.update(observations=aligned["observations"], window=[aligned["dates"][0], aligned["dates"][-1]],
                   multi_session_observations=aligned["multi_session_observations"],
                   skipped_levels=aligned["skipped_levels"])
    fitted = _call(results, "macro_exposures", macro_exposures, aligned["portfolio"], aligned["macro_changes"],
                   dates=aligned["dates"], transforms=aligned["transforms"])
    if fitted is None:
        results["macro_sensitivity"] = results["macro_exposures"]
    else:
        in_model = {row["factor"] for row in fitted.get("exposures") or []}
        scenarios: dict[str, Any] = {}
        for name, shocks in MACRO_SHOCKS.items():
            if not in_model.intersection(shocks):
                why = " ".join(summary["factors_missing_reasons"].get(factor, "") for factor in shocks)
                summary["shocks_left_out"][name] = " ".join([
                    f"Not worked out: {' and '.join(shocks)} {'is' if len(shocks) == 1 else 'are'} not in the model.",
                    why]).strip()
                continue
            _call(scenarios, name, macro_sensitivity, fitted, shocks)
        results["macro_sensitivity"] = scenarios
    return _payload("macro", inputs, summary=summary, results=results)


def _rest_of_filing(rest_value: float, rest_squares: float, rest_positions: int) -> list[float]:
    """Stand-in stakes for the part of a filing outside the portfolio's names.

    As few values as there can be with the same sum and the same sum of squares as those stakes
    (k - 1 equal values and one smaller, k = ceil(sum² / sum of squares), never more than the
    stakes they stand for). A fund's total, its weights in our names and the length of its weight
    vector are then exactly the full filing's, and those are all ownership_overlap's similarity
    and shares use, so reading every row of every filing is not needed.
    """
    if rest_value <= 0 or rest_squares <= 0:
        return []
    count = max(1, min(max(1, int(rest_positions)), math.ceil(rest_value * rest_value / rest_squares - 1e-9)))
    if count == 1:
        return [rest_value]
    root = math.sqrt(max(0.0, (count - 1) * (count * rest_squares - rest_value * rest_value)))
    large = (rest_value * (count - 1) + root) / ((count - 1) * count)
    small = rest_value - (count - 1) * large
    return [large] * (count - 1) + ([small] if small > 0 else [])


def build_crowding(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """ownership_overlap of the US-listed holdings (a US ISIN, so a CUSIP) with the 13F filers'
    latest filings known on the date (value stakes), and the most similar funds. Holdings without a
    US ISIN are listed as not covered."""
    from .quant.crowding import ownership_overlap

    stopped = _not_covered("crowding", inputs)
    if stopped:
        return stopped
    covered = list(inputs.get("covered") or [])
    not_covered = list(inputs.get("not_covered") or [])
    filers = list(inputs.get("filers") or [])
    weights: dict[str, float] = defaultdict(float)
    names: dict[str, list[str]] = defaultdict(list)
    for holding in covered:
        weights[str(holding["cusip"])] += float(holding["weight"])
        names[str(holding["cusip"])].append(str(holding["name"]))
    funds: dict[str, dict[str, float]] = {}
    labels: dict[str, str] = {}
    counts: dict[str, int] = defaultdict(int)
    for filer in filers:
        counts[str(filer["filer_name"])] += 1
    for filer in filers:
        name = str(filer["filer_name"])
        labels[str(filer["filer_cik"])] = name if counts[name] == 1 else f"{name} ({filer['filer_cik']})"
    stakes: dict[str, dict[str, float]] = defaultdict(dict)
    for row in inputs.get("stakes") or []:
        value = _number(row.get("value_usd"))
        if value and value > 0 and str(row["filer_cik"]) in labels:
            stakes[str(row["filer_cik"])][str(row["cusip"])] = value
    for filer in filers:
        cik = str(filer["filer_cik"])
        fund = dict(stakes.get(cik) or {})
        for index, value in enumerate(_rest_of_filing(float(filer.get("rest_value") or 0.0),
                                                      float(filer.get("rest_squares") or 0.0),
                                                      int(filer.get("rest_positions") or 0)), start=1):
            fund[f"(other holdings {index})"] = value
        funds[labels[cik]] = fund
    summary = {
        "holdings_date": inputs.get("holdings_date"), "unit": "value",
        "covered": len(covered), "covered_weight": math.fsum(float(h["weight"]) for h in covered),
        "not_covered": not_covered, "not_covered_weight": math.fsum(float(h["weight"]) for h in not_covered),
        "securities": {cusip: " / ".join(sorted(found)) for cusip, found in sorted(names.items())},
        "filers": [{"fund": labels[str(f["filer_cik"])], **{key: f.get(key) for key in (
            "filer_cik", "period", "filed_at", "accession", "positions", "total_value")}} for f in filers],
        "periods": sorted({str(f["period"]) for f in filers}),
        "shares_outstanding": "Not in silver, so ownership and the crowding score are not computed; "
                              "coverage is 0 and the overlap and similarity figures stand alone.",
    }
    if not covered:
        return _payload("crowding", inputs, summary=summary,
                        reason="No holding has a US ISIN, so none is in the 13F filings.")
    results: dict[str, Any] = {}
    _call(results, "ownership_overlap", ownership_overlap, dict(weights), funds, unit="value")
    return _payload("crowding", inputs, summary=summary, results=results)


PHASE3_BUILDERS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "liquidity": build_liquidity, "factor_attribution": build_factor_attribution, "macro": build_macro,
    "crowding": build_crowding,
}
BUILDERS.update(PHASE3_BUILDERS)


def quality_status(payload: Mapping[str, Any], replaced: int = 0) -> str:
    """pass: every call worked. warn: the section or one of its calls is unavailable, or a number
    that was not finite was stored as null. fail is never written here: it marks a row not to show."""
    if replaced or not payload.get("available"):
        return "warn"
    results = payload.get("results") or {}
    partial = any(isinstance(value, Mapping) and value.get("available") is False for value in results.values())
    return "warn" if partial else "pass"


# ---------------------------------------------------------------- fingerprint


def section_fingerprint(section: str, inputs: Mapping[str, Any]) -> str:
    """sha256 of the section's canonical inputs and the versions that compute it."""
    spec = SECTION_BY_NAME[section]
    manifest = {
        "fingerprint_version": FINGERPRINT_VERSION, "analytics_version": QUANT_ANALYTICS_VERSION,
        "section": section, "model_id": spec.model_id, "model_version": model_cards.version(spec.model_id),
        "modelling_version": MODELLING_VERSION, "inputs": inputs,
    }
    serialized = canonical_json(manifest)
    # Reject NaN and infinity rather than give an invalid input a cache key.
    json.loads(serialized, parse_constant=lambda value: (_ for _ in ()).throw(
        ValueError(f"Non-finite quant input: {value}")))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- preparing the inputs (pure)


def _labels(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """conid -> a display name: the symbol, with the conid when two holdings share a symbol."""
    symbols: dict[str, str] = {}
    for row in rows:
        symbols.setdefault(str(row["conid"]), str(row.get("symbol") or row["conid"]))
    counts: dict[str, int] = defaultdict(int)
    for symbol in symbols.values():
        counts[symbol] += 1
    return {conid: symbol if counts[symbol] == 1 else f"{symbol} ({conid})" for conid, symbol in symbols.items()}


def _positions(data: Mapping[str, Any], fx: FxRates, base: str) -> dict[str, Any]:
    """Positions summed per date and holding, with marks in the holding's and the reporting currency."""
    summed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in data.get("positions") or []:
        day, conid = _day(row.get("date")), str(row.get("conid") or "")
        if not day or not conid:
            continue
        item = summed.setdefault((day, conid), {"date": day, "conid": conid, "symbol": row.get("symbol"),
                                                "currency": str(row.get("currency") or base).upper(),
                                                "quantity": 0.0, "market_value": 0.0, "mark": None})
        item["quantity"] += _number(row.get("quantity")) or 0.0
        item["market_value"] += _number(row.get("market_value")) or 0.0
        mark = _number(row.get("mark"))
        if item["mark"] is None and mark and mark > 0:
            item["mark"] = mark
    local: dict[str, dict[str, float]] = defaultdict(dict)
    reporting: dict[str, dict[str, float]] = defaultdict(dict)
    values: dict[str, dict[str, float]] = defaultdict(dict)
    quantities: dict[str, dict[str, float]] = defaultdict(dict)
    missing_fx = 0
    for (day, conid), item in sorted(summed.items()):
        rate = fx.rate(item["currency"], base, day)
        if item["mark"]:
            local[conid][day] = item["mark"]
            if rate:
                reporting[conid][day] = item["mark"] * rate
        if rate is None:
            missing_fx += 1
            continue
        if item["quantity"]:
            quantities[day][conid] = item["quantity"]
            values[day][conid] = item["market_value"] * rate
    return {"rows": summed, "local": local, "reporting": reporting, "values": values,
            "quantities": quantities, "missing_fx": missing_fx, "labels": _labels(summed.values())}


def _nav(data: Mapping[str, Any]) -> dict[str, float]:
    found = {}
    for row in data.get("nav") or []:
        day, value = _day(row.get("date")), _number(row.get("nav"))
        if day and value is not None:
            found[day] = value
    return found


def _period_start(nav: Mapping[str, float], first_day: str, as_of: str) -> str | None:
    """The last NAV date before the period, else its first NAV date before as_of."""
    before = [day for day in nav if day < first_day]
    if before:
        return max(before)
    inside = sorted(day for day in nav if first_day <= day < as_of)
    return inside[0] if inside else None


def _holdings_inputs(data: Mapping[str, Any], positions: Mapping[str, Any], nav: Mapping[str, float],
                     classifications: Mapping[str, str]) -> dict[str, Any]:
    as_of = data["as_of"]
    dated = sorted(day for day in positions["values"] if day <= as_of)
    if not dated:
        return {"as_of": as_of, "holdings": [], "no_holdings": f"There are no holdings on or before {as_of}."}
    day = dated[-1]
    level = _on_or_before(nav, day)
    if level is None or level[1] <= 0:
        return {"as_of": as_of, "holdings": [], "holdings_date": day,
                "no_holdings": f"There is no positive NAV on the holdings date {day}."}
    labels = positions["labels"]
    holdings = []
    for conid, value in positions["values"][day].items():
        if not value:
            continue
        row = positions["rows"][(day, conid)]
        holdings.append({"name": labels[conid], "conid": conid, "currency": row["currency"],
                         "sector": classifications.get(conid) or "Unclassified", "weight": value / level[1],
                         "market_value": value})
    holdings.sort(key=lambda h: (-abs(h["weight"]), h["name"]))
    start = _shift(day, -MARK_WINDOW_DAYS)
    prices = {h["name"]: {d: p for d, p in positions["local"].get(h["conid"], {}).items() if start <= d <= day}
              for h in holdings}
    return {"as_of": as_of, "holdings_date": day, "nav": level[1], "holdings": holdings, "prices": prices}


def _trades(data: Mapping[str, Any], fx: FxRates, base: str, labels: Mapping[str, str]) -> tuple[list, int]:
    """The trade window's trades with prices and commission in the reporting currency."""
    found, left_out = [], 0
    for row in data.get("trades") or []:
        day = _day(row.get("trade_date"))
        side = str(row.get("side") or "").strip().upper()
        quantity, price = _number(row.get("quantity")), _number(row.get("price"))
        if not day or side not in ("BUY", "SELL") or not quantity or not price or price <= 0:
            left_out += 1
            continue
        rate = fx.rate(row.get("currency") or base, base, day)
        commission = abs(_number(row.get("commission")) or 0.0)
        commission_rate = fx.rate(row.get("commission_currency") or row.get("currency") or base, base, day)
        if rate is None or (commission and commission_rate is None):
            left_out += 1
            continue
        conid = str(row["conid"])
        found.append({"trade_id": row.get("trade_id") or row.get("execution_id"), "trade_date": day,
                      "conid": conid, "symbol": labels.get(conid) or row.get("symbol") or conid, "side": side,
                      "quantity": abs(quantity), "price": price * rate,
                      "commission": commission * (commission_rate or 0.0)})
    return found, left_out


def _flows(data: Mapping[str, Any], fx: FxRates, base: str) -> list[list[Any]]:
    """External flows (deposits and withdrawals) in the reporting currency, by date."""
    summed: dict[str, float] = defaultdict(float)
    for row in data.get("flows") or []:
        day, amount = _day(row.get("date")), _number(row.get("amount"))
        rate = fx.rate(row.get("currency") or base, base, day) if day else None
        if day and amount is not None and rate:
            summed[day] += amount * rate
    return [[day, value] for day, value in sorted(summed.items()) if value]


def _benchmark_levels(data: Mapping[str, Any]) -> dict[str, float]:
    found = {}
    for row in data.get("comparison") or []:
        day, level = _day(row.get("date")), _number(row.get("benchmark_level"))
        if day and level and level > 0:
            found[day] = level
    return found


def _currency_period(kind: str, data: Mapping[str, Any], positions: Mapping[str, Any], fx: FxRates,
                     nav: Mapping[str, float], benchmark: Mapping[str, float], base: str) -> dict[str, Any]:
    as_of = data["as_of"]
    today = date.fromisoformat(as_of)
    first_day = (today.replace(day=1) if kind == "mtd" else today.replace(month=1, day=1)).isoformat()
    start = _period_start(nav, first_day, as_of)
    if start is None:
        return {"unavailable": f"There is no NAV before {as_of} to start the period from."}
    held = positions["quantities"].get(start)
    if held is None:
        return {"start_date": start, "end_date": as_of, "unavailable": f"There are no holdings on {start}."}
    holdings, moves, missing = [], {}, 0
    for conid, quantity in sorted(held.items()):
        row = positions["rows"][(start, conid)]
        currency = row["currency"]
        start_rate, end_rate = fx.rate(currency, base, start), fx.rate(currency, base, as_of)
        mark = _on_or_before(positions["local"].get(conid, {}), as_of)
        start_value = positions["values"][start].get(conid)
        if start_value is None or start_rate is None or end_rate is None or mark is None:
            missing += 1
            continue
        holdings.append({"conid": conid, "currency": currency, "start_value": start_value,
                         "end_value": quantity * mark[1] * end_rate})
        if currency != base:
            moves[currency] = end_rate / start_rate - 1.0
    bench_start, bench_end = _on_or_before(benchmark, start), _on_or_before(benchmark, as_of)
    return {"start_date": start, "end_date": as_of, "start_nav": nav[start], "holdings": holdings,
            "holdings_left_out": missing, "currency_moves": dict(sorted(moves.items())),
            "benchmark_start": bench_start[1] if bench_start else None,
            "benchmark_end": bench_end[1] if bench_end else None}


def _counterfactual(data: Mapping[str, Any], positions: Mapping[str, Any], nav: Mapping[str, float],
                    flows: Sequence[Sequence[Any]], benchmark: Mapping[str, float]) -> dict[str, Any]:
    as_of = data["as_of"]
    first_day = date.fromisoformat(as_of).replace(month=1, day=1).isoformat()
    start = _period_start(nav, first_day, as_of)
    if start is None:
        return {"unavailable": f"There is no NAV before {as_of} to start the year from."}
    held = positions["quantities"].get(start)
    if not held:
        return {"start_date": start, "unavailable": f"There are no holdings on {start} to hold from."}
    return {"start_date": start,
            "start_holdings": {conid: quantity for conid, quantity in sorted(held.items())},
            "nav": {day: value for day, value in sorted(nav.items()) if start <= day <= as_of},
            "flows": [list(flow) for flow in flows if start < flow[0] <= as_of],
            "benchmark": {day: level for day, level in sorted(benchmark.items()) if day <= as_of} or None}


def prepare_inputs(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """One plain, JSON-ready input dict per section from load_inputs()'s rows. Pure."""
    as_of = str(data["as_of"])
    if data.get("unavailable"):
        return {name: {"as_of": as_of, "unavailable": str(data["unavailable"])} for name in SECTION_NAMES}
    base = str(data.get("reporting_currency") or "USD").upper()
    fx = FxRates(data.get("fx") or [])
    positions = _positions(data, fx, base)
    nav = _nav(data)
    benchmark = _benchmark_levels(data)
    flows = _flows(data, fx, base)
    classifications = dict(data.get("classifications") or {})

    comparison = [row for row in data.get("comparison") or [] if _day(row.get("date"))]
    portfolio = {_day(r["date"]): _number(r.get("portfolio_return")) for r in comparison}
    portfolio = {day: value for day, value in portfolio.items() if value is not None}
    no_comparison = (None if portfolio else
                     f"No primary benchmark comparison with daily returns is stored on or before {as_of}.")
    returns = [[day, portfolio[day]] for day in sorted(portfolio)]
    rate = _number((data.get("risk_free") or {}).get("annual_rate"))
    skill = {"as_of": as_of, "benchmark_id": data.get("benchmark_id"),
             "portfolio": dict(sorted(portfolio.items())),
             "benchmark": {d: v for d, v in sorted((_day(r["date"]), _number(r.get("benchmark_return")))
                                                     for r in comparison) if v is not None},
             "risk_free_annual_rate": rate,
             "risk_free_daily": (1.0 + rate) ** (1.0 / 252.0) - 1.0 if rate is not None and rate > -1 else 0.0}
    series = {"as_of": as_of, "returns": returns}

    holdings = _holdings_inputs(data, positions, nav, classifications)
    window_start = _shift(as_of, -TRADE_WINDOW_DAYS)
    physical = str(data.get("subject_type") or "physical_account") == "physical_account"
    trades, left_out = _trades(data, fx, base, positions["labels"]) if physical else ([], 0)
    trades = [trade for trade in trades if window_start < trade["trade_date"] <= as_of]
    no_trades = (None if trades else
                 "Trades are stored for physical accounts only." if not physical else
                 f"There are no trades in the year to {as_of}.")
    closes = {conid: dict(sorted(series_.items())) for conid, series_ in sorted(positions["reporting"].items())}
    sizes = {day: {conid: value for conid, value in sorted(values.items()) if value > 0}
             for day, values in sorted(positions["values"].items()) if window_start < day <= as_of}
    decisions = {"as_of": as_of, "currency": base, "trades": trades, "trades_left_out": left_out,
                 "no_trades": no_trades, "closes": closes, "sizes": sizes,
                 "counterfactual": _counterfactual(data, positions, nav, flows, benchmark)}
    execution = {"as_of": as_of, "currency": base, "trades": trades, "no_trades": no_trades, "closes": closes}
    currency = {"as_of": as_of, "base_currency": base,
                "periods": {kind: _currency_period(kind, data, positions, fx, nav, benchmark, base)
                            for kind in ("mtd", "ytd")}}
    prepared = {
        "skill": skill, "decisions": decisions, "currency": currency, "tail": dict(series),
        "regime": {"as_of": as_of, "returns": returns[-REGIME_HISTORY:]},
        "holdings_risk": holdings, "correlation": holdings, "concentration": holdings, "execution": execution,
    }
    if no_comparison:
        for name in ("skill", "tail", "regime"):
            prepared[name] = {"as_of": as_of, "unavailable": no_comparison}
    return {name: finite_copy(value)[0] for name, value in prepared.items()}


def _held_quantities(data: Mapping[str, Any], day: str | None) -> dict[str, float]:
    found: dict[str, float] = defaultdict(float)
    for row in data.get("positions") or []:
        if day and _day(row.get("date")) == day and row.get("conid"):
            found[str(row["conid"])] += _number(row.get("quantity")) or 0.0
    return dict(found)


def _not_in_13f(info: Mapping[str, Any]) -> str | None:
    """Why a holding cannot be matched to 13F filings, or None when it can (a share with a US ISIN)."""
    from .sec_13f import cusip_from_isin  # the 13F loader's own rule

    category = str(info.get("asset_category") or "").strip().upper()
    if category and category != "STK":
        return "Only shares are in 13F filings."
    if not str(info.get("isin") or "").strip():
        return "No ISIN in silver instruments."
    if cusip_from_isin(info.get("isin")) is None:
        return "Not a US ISIN, so not in 13F filings."
    return None


def prepare_phase3_inputs(data: Mapping[str, Any], prepared: Mapping[str, Mapping[str, Any]],
                          market: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """One plain, JSON-ready input dict per phase 3 section whose silver rows were read
    (load_phase3_inputs gives None for a table that is not there yet, and that section is left
    out). Uses prepare_inputs()'s holdings and returns, so the holdings and the VaR history are
    exactly the v1 sections'. Pure."""
    as_of = str(data["as_of"])
    names = [name for name in PHASE3_NAMES if (market or {}).get(name) is not None]
    if data.get("unavailable"):
        return {name: {"as_of": as_of, "unavailable": str(data["unavailable"])} for name in names}
    holdings = dict(prepared.get("holdings_risk") or {})
    day = holdings.get("holdings_date")
    tail = dict(prepared.get("tail") or {})
    returns = {}
    for row in data.get("comparison") or []:
        found_day, value = _day(row.get("date")), _number(row.get("portfolio_return"))
        if found_day and found_day <= as_of and value is not None:
            returns[found_day] = value
    returns = dict(sorted(returns.items()))
    no_returns = None if returns else (
        f"No primary benchmark comparison with daily returns is stored on or before {as_of}.")
    found: dict[str, dict[str, Any]] = {}
    if "liquidity" in names:
        stored = dict((market["liquidity"] or {}).get("volumes") or {})
        quantities = _held_quantities(data, day)
        volumes, instruments, rows = {}, {}, []
        for holding in holdings.get("holdings") or []:
            entry = stored.get(str(holding["conid"])) or {}
            series = {d: v for d, v in sorted((entry.get("volumes") or {}).items())
                      if day and _shift(day, -VOLUME_WINDOW_DAYS) <= d <= day}
            if series:
                volumes[str(holding["name"])] = series
                instruments[str(holding["name"])] = {"instrument_id": entry.get("instrument_id")}
            rows.append({"name": holding["name"], "conid": holding["conid"], "weight": holding["weight"],
                         "market_value": holding["market_value"],
                         "quantity": quantities.get(str(holding["conid"]), 0.0)})
        found["liquidity"] = {"as_of": as_of, "holdings_date": day, "holdings": rows,
                              "no_holdings": holdings.get("no_holdings"), "volumes": volumes,
                              "volume_instruments": instruments, "returns": list(tail.get("returns") or []),
                              "no_returns": tail.get("unavailable")}
    if "factor_attribution" in names:
        stored = dict(market["factor_attribution"] or {})
        start = min(date.fromisoformat(as_of).replace(month=1, day=1).isoformat(), _shift(as_of, -365))
        found["factor_attribution"] = (
            {"as_of": as_of, "unavailable": no_returns} if no_returns else
            {"as_of": as_of, "region": FACTOR_REGION, "region_label": FACTOR_REGIONS[FACTOR_REGION][0],
             "returns": {d: r for d, r in returns.items() if d >= start},
             "factors": {d: dict(row) for d, row in sorted((stored.get("factors") or {}).items())
                         if start <= d <= as_of},
             "sources": stored.get("sources"), "vintages": stored.get("vintages")})
    if "macro" in names:
        stored = dict(market["macro"] or {})
        start = _shift(as_of, -365 * MACRO_YEARS - 7)
        found["macro"] = (
            {"as_of": as_of, "unavailable": no_returns} if no_returns else
            {"as_of": as_of, "returns": {d: r for d, r in returns.items() if d >= start},
             "levels": {name: {d: v for d, v in sorted(series.items()) if start <= d <= as_of}
                        for name, series in sorted((stored.get("series") or {}).items())},
             "units": stored.get("units"), "sources": stored.get("sources")})
    if "crowding" in names:
        from .sec_13f import cusip_from_isin

        stored = dict(market["crowding"] or {})
        instruments = dict(stored.get("instruments") or {})
        filings = dict(stored.get("filings") or {})
        covered, not_covered = [], []
        for holding in holdings.get("holdings") or []:
            info = instruments.get(str(holding["conid"])) or {}
            reason = _not_in_13f(info)
            row = {"name": holding["name"], "conid": holding["conid"], "weight": holding["weight"]}
            if reason:
                not_covered.append({**row, "reason": reason})
            else:
                covered.append({**row, "cusip": cusip_from_isin(info.get("isin"))})
        found["crowding"] = {"as_of": as_of, "holdings_date": day, "no_holdings": holdings.get("no_holdings"),
                             "covered": covered, "not_covered": not_covered,
                             "filers": list(filings.get("filers") or []), "stakes": list(filings.get("stakes") or [])}
    return {name: finite_copy(value)[0] for name, value in found.items()}


# ---------------------------------------------------------------- reading silver and gold


def _own_pipeline_reason(connection, *, tenant_id: str, subject_id: str, subject_type: str) -> str | None:
    if subject_id in OWN_PIPELINES:
        return ("Alpha World MS and T report through their own HKD pipeline; quant analytics v1 covers "
                "physical accounts and derived subjects.")
    if subject_type == "composite_portfolio":
        from .profiled_composite import is_profiled

        if is_profiled(connection, subject_id, tenant_id=tenant_id):
            return ("This composite reports through its own HKD design (a report profile); quant analytics v1 "
                    "covers physical accounts and derived subjects.")
    return None


def load_inputs(connection, *, tenant_id: str, subject_id: str, as_of: str) -> dict[str, Any]:
    """Every row the sections need for one subject and date, from silver and gold (and the
    subject's settings). Plain values only."""
    from .benchmark_analytics import CALCULATION_VERSION
    from .subject_read_contract import ACTIVE_NAV_CURRENCY_SQL

    as_of = date.fromisoformat(as_of).isoformat()
    subject = connection.execute(
        "SELECT subject_type, COALESCE(reporting_currency, base_currency) AS currency "
        "FROM platform_subjects WHERE tenant_id = ? AND subject_id = ?", (tenant_id, subject_id)).fetchone()
    subject_type = str(subject["subject_type"]) if subject else "physical_account"
    currency = subject["currency"] if subject else None
    data: dict[str, Any] = {"subject_id": subject_id, "as_of": as_of, "subject_type": subject_type,
                            "reporting_currency": str(currency or "USD").upper()}
    reason = _own_pipeline_reason(connection, tenant_id=tenant_id, subject_id=subject_id, subject_type=subject_type)
    if reason:
        return {**data, "unavailable": reason}

    benchmark = connection.execute(
        """
        SELECT benchmark_id FROM portfolio_benchmark_mapping
        WHERE tenant_id = ? AND subject_id = ? AND mapping_type = 'primary'
          AND effective_from <= ? AND (effective_to IS NULL OR effective_to >= ?)
        ORDER BY effective_from DESC LIMIT 1
        """, (tenant_id, subject_id, as_of, as_of)).fetchone()
    data["benchmark_id"] = str(benchmark["benchmark_id"]) if benchmark else None
    data["comparison"] = [] if benchmark is None else [
        {"date": str(row["observation_date"])[:10], "portfolio_return": row["portfolio_return"],
         "benchmark_return": row["benchmark_return"], "portfolio_value": row["portfolio_value"],
         "benchmark_level": row["benchmark_level"]}
        for row in connection.execute(
            """
            SELECT observation_date, portfolio_return, benchmark_return, portfolio_value, benchmark_level
            FROM performance_comparison_daily
            WHERE tenant_id = ? AND subject_id = ? AND benchmark_id = ? AND calculation_version = ?
              AND quality_status IN ('pass', 'warn') AND observation_date <= ?
            ORDER BY observation_date
            """, (tenant_id, subject_id, benchmark["benchmark_id"], CALCULATION_VERSION, as_of)).fetchall()]
    risk_free = connection.execute(
        """
        SELECT rate_date, annual_rate FROM risk_free_rates
        WHERE tenant_id = ? AND currency = 'USD' AND rate_date <= ? AND quality_status IN ('pass', 'warn')
        ORDER BY rate_date DESC LIMIT 1
        """, (tenant_id, as_of)).fetchone()
    data["risk_free"] = ({"rate_date": str(risk_free["rate_date"])[:10], "annual_rate": risk_free["annual_rate"]}
                         if risk_free else None)

    year_start = date.fromisoformat(as_of).replace(month=1, day=1).isoformat()
    start = min(_shift(as_of, -MARK_WINDOW_DAYS), _shift(year_start, -15))
    data["positions"] = [
        {"date": str(row["snapshot_date"])[:10], "conid": str(row["conid"]), "symbol": row["symbol"],
         "currency": row["currency"], "quantity": row["quantity_text"], "mark": row["mark_price_text"],
         "market_value": row["market_value_text"]}
        for row in connection.execute(
            """
            SELECT p.snapshot_date, p.conid::text AS conid, p.currency, p.quantity_text, p.mark_price_text,
                   p.market_value_text, i.symbol
            FROM subject_position_series AS p
            LEFT JOIN instruments AS i ON i.conid = p.conid
            WHERE p.tenant_id = ? AND p.subject_id = ? AND p.snapshot_date BETWEEN ? AND ?
              AND UPPER(COALESCE(i.symbol, '')) NOT LIKE '%%.REC'
            ORDER BY p.snapshot_date, p.conid
            """, (tenant_id, subject_id, start, as_of)).fetchall()]
    conids = sorted({row["conid"] for row in data["positions"]})
    data["classifications"] = {}
    if conids:
        for row in connection.execute(
                """
                SELECT conid::text AS conid, sector FROM instrument_classifications
                WHERE tenant_id = ? AND conid::text = ANY(?::text[]) AND quality_status IN ('pass', 'warn')
                  AND status = 'classified' AND COALESCE(sector, '') <> ''
                ORDER BY conid, provider
                """, (tenant_id, conids)).fetchall():
            data["classifications"].setdefault(str(row["conid"]), str(row["sector"]))
    data["trades"] = [] if subject_type != "physical_account" else [
        {"trade_id": row["trade_id"], "execution_id": row["execution_id"], "trade_date": row["trade_date"],
         "conid": str(row["conid"]), "symbol": row["symbol"], "side": row["buy_sell"],
         "quantity": row["quantity_text"], "price": row["trade_price_text"], "commission": row["commission_text"],
         "commission_currency": row["commission_currency"], "currency": row["currency"]}
        for row in connection.execute(
            """
            SELECT t.trade_id, t.execution_id, t.trade_date, t.conid, t.buy_sell, t.quantity_text,
                   t.trade_price_text, t.commission_text, t.commission_currency, t.currency, i.symbol
            FROM trades AS t
            JOIN accounts AS a ON a.account_id = t.account_id
            LEFT JOIN instruments AS i ON i.conid = t.conid
            WHERE a.tenant_id = ? AND t.account_id = ? AND t.trade_date > ? AND t.trade_date <= ?
              AND UPPER(COALESCE(t.buy_sell, '')) IN ('BUY', 'SELL') AND COALESCE(t.conid, '') <> ''
              AND UPPER(COALESCE(i.asset_category, '')) <> 'CASH'
            ORDER BY t.trade_date, t.trade_time, t.source_key
            """, (tenant_id, subject_id, _shift(as_of, -TRADE_WINDOW_DAYS), as_of)).fetchall()]
    data["nav"] = [
        {"date": str(row["snapshot_date"])[:10], "nav": row["total_text"]}
        for row in connection.execute(
            "SELECT snapshot_date, total_text FROM subject_nav_series "
            "WHERE tenant_id = ? AND subject_id = ? AND snapshot_date BETWEEN ? AND ? AND currency = "
            + ACTIVE_NAV_CURRENCY_SQL + " ORDER BY snapshot_date", (tenant_id, subject_id, start, as_of)).fetchall()]
    data["flows"] = _external_flows(connection, tenant_id=tenant_id, subject_id=subject_id, start=start, end=as_of)
    currencies = sorted({str(value).upper() for value in (
        [row["currency"] for row in data["positions"]] + [row["currency"] for row in data["trades"]]
        + [row["commission_currency"] for row in data["trades"]] + [row["currency"] for row in data["flows"]]
        + [data["reporting_currency"], "USD"]) if value})
    data["fx"] = [
        {"date": str(row["rate_date"])[:10], "from": row["from_currency"], "to": row["to_currency"],
         "rate": row["rate"]}
        for row in connection.execute(
            """
            SELECT rate_date, from_currency, to_currency, rate FROM reference_fx_rates
            WHERE tenant_id = ? AND quality_status IN ('pass', 'warn') AND rate_date BETWEEN ? AND ?
              AND from_currency = ANY(?::text[]) AND to_currency = ANY(?::text[])
            ORDER BY rate_date, from_currency, to_currency, provider
            """, (tenant_id, _shift(start, -FX_TOLERANCE_DAYS - 1), as_of, currencies, currencies)).fetchall()]
    return data


def _external_flows(connection, *, tenant_id: str, subject_id: str, start: str, end: str) -> list[dict[str, Any]]:
    """Deposits and withdrawals: a derived subject's external ledger rows, or an account's cash
    transactions of that type (as the provisional benchmark reads them)."""
    from .provisional_benchmark import _is_external_cash_transaction

    found = []
    for row in connection.execute(
            """
            SELECT source_kind, effective_date, transaction_type, description, amount_text, currency,
                   included, external_flow
            FROM subject_cash_activity_series
            WHERE tenant_id = ? AND subject_id = ? AND effective_date > ? AND effective_date <= ?
            ORDER BY effective_date, source_key
            """, (tenant_id, subject_id, start, end)).fetchall():
        if row["source_kind"] == "derived_portfolio":
            external = bool(row["included"]) and bool(row["external_flow"])
        else:
            external = _is_external_cash_transaction({"type": row["transaction_type"],
                                                      "description": row["description"]})
        if external:
            found.append({"date": str(row["effective_date"])[:10], "amount": row["amount_text"],
                          "currency": row["currency"]})
    return found


def phase3_section_names(sections: Iterable[str] | None = None,
                         environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The phase 3 sections to build: shadow or on in QUANT_ANALYTICS_PHASE3 (and among `sections`
    when given). Unset, this is empty and the build reads nothing more than v1."""
    from .market_inputs import enabled_sections

    enabled = enabled_sections(environ)
    if sections is None:
        return enabled
    chosen = {str(name) for name in sections}
    return tuple(name for name in enabled if name in chosen)


def load_phase3_inputs(connection, *, tenant_id: str, subject_id: str, as_of: str, data: Mapping[str, Any],
                       sections: Iterable[str]) -> dict[str, Any]:
    """The phase 3 silver rows for the sections asked for, for the holdings load_inputs() found:
    `{section: rows}`, or `{section: None}` for a section whose table is not there yet (asked of
    the catalog, then each read in a savepoint, so the build's transaction stays usable)."""
    from . import market_inputs

    wanted = [name for name in PHASE3_NAMES if name in set(sections)]
    found: dict[str, Any] = {}
    for name in wanted:
        if not market_inputs.table_exists(connection, market_inputs.SECTION_TABLES[name]):
            found[name] = None
    present = [name for name in wanted if name not in found]
    if not present or data.get("unavailable"):
        return {**found, **{name: {} for name in present}}
    dated = sorted({str(row["date"]) for row in data.get("positions") or [] if str(row["date"]) <= as_of})
    day = dated[-1] if dated else None
    rows = {str(row["conid"]): row for row in data.get("positions") or [] if day and str(row["date"]) == day}
    held = sorted(rows)
    instruments = market_inputs.read_instruments(connection, held) if held and "crowding" in present else {}
    for name in present:
        if name == "liquidity":
            # Each holding's volumes are stored under its IBKR conid (daily_volumes.fetch_and_load).
            volumes = (market_inputs.read_daily_volumes(connection, tenant_id=tenant_id, instrument_ids=held,
                                                        start=_shift(day, -VOLUME_WINDOW_DAYS), end=day)
                       if day else {})
            found[name] = None if volumes is None else {"volumes": {
                conid: {"instrument_id": conid, "volumes": series} for conid, series in sorted(volumes.items())
                if series}}
        elif name == "factor_attribution":
            start = min(date.fromisoformat(as_of).replace(month=1, day=1).isoformat(), _shift(as_of, -365))
            found[name] = market_inputs.read_factor_returns(connection, tenant_id=tenant_id, region=FACTOR_REGION,
                                                            start=start, end=as_of)
        elif name == "macro":
            found[name] = market_inputs.read_macro_series(connection, tenant_id=tenant_id,
                                                          start=_shift(as_of, -365 * MACRO_YEARS - 7), end=as_of)
        elif name == "crowding":
            from .sec_13f import cusip_from_isin  # the 13F loader's own rule

            cusips = {cusip_from_isin(info.get("isin")) for info in instruments.values()}
            filings = market_inputs.read_institutional_holdings(connection, tenant_id=tenant_id, as_of=as_of,
                                                                cusips=sorted(c for c in cusips if c))
            found[name] = None if filings is None else {
                "instruments": {conid: {"isin": (instruments.get(conid) or {}).get("isin"),
                                        "asset_category": (instruments.get(conid) or {}).get("asset_category")}
                                for conid in held},
                "filings": filings}
    # Each phase 3 table read is one more input of the job's lineage run (a no-op unless recording;
    # lineage.JOB_INPUTS lists the v1 inputs only, so nothing changes while the setting is off).
    from . import lineage

    for name in present:
        if found.get(name) is not None:
            lineage.current().input(f"silver.{market_inputs.SECTION_TABLES[name]}")
    return found


# ---------------------------------------------------------------- writing and reading gold


def store_section(connection, *, tenant_id: str, subject_id: str, as_of: str, payload: Mapping[str, Any],
                  source_sha256: str, quality: str, duration_ms: float, timings: Mapping[str, Any]) -> None:
    """Upsert one section's row. The caller commits (or the publish gate rolls back)."""
    text = canonical_json(payload)
    connection.execute(
        """
        INSERT INTO quant_analytics_snapshots(
            tenant_id, subject_id, as_of_date, section, analytics_version, model_id, model_version,
            source_data_sha256, payload_sha256, payload_json, quality_status, generated_at, duration_ms,
            timings_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?, ?, ?::jsonb)
        ON CONFLICT (tenant_id, subject_id, as_of_date, section, analytics_version) DO UPDATE SET
            model_id = excluded.model_id, model_version = excluded.model_version,
            source_data_sha256 = excluded.source_data_sha256, payload_sha256 = excluded.payload_sha256,
            payload_json = excluded.payload_json, quality_status = excluded.quality_status,
            generated_at = excluded.generated_at, duration_ms = excluded.duration_ms,
            timings_json = excluded.timings_json
        """,
        (tenant_id, subject_id, as_of, payload["section"], QUANT_ANALYTICS_VERSION, payload["model_id"],
         payload["model_version"], source_sha256, hashlib.sha256(text.encode("utf-8")).hexdigest(), text,
         quality, utc_now_iso(), max(0.0, float(duration_ms)), canonical_json(dict(timings))),
    )


def build_quant_snapshot(connection, *, tenant_id: str, subject_id: str, as_of: str,
                         sections: Iterable[str] | None = None, only_if_changed: bool = True,
                         environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Build and store the sections of one subject and date in ONE transaction, the publish gate
    called before the commit, so a reader never sees a half-written day. An unchanged section
    (same fingerprint, not failed) is skipped. A section whose engine fails with anything other
    than ModellingInputError is not written and is reported under "errors". The publish gate's
    BuildHeldBack (enforce mode) rolls everything back and propagates to the caller. A phase 3
    section is built only when QUANT_ANALYTICS_PHASE3 sets it to shadow or on and its silver table
    is there; one whose table is not is reported as skipped and nothing is written for it."""
    started = time.perf_counter()
    requested = None if sections is None else [str(name) for name in sections]
    names = section_names(None if requested is None else [name for name in requested if name not in PHASE3_NAMES])
    phase3 = phase3_section_names(requested, environ)
    as_of = date.fromisoformat(as_of).isoformat()
    data = load_inputs(connection, tenant_id=tenant_id, subject_id=subject_id, as_of=as_of)
    load_ms = _ms(started)
    prepare_started = time.perf_counter()
    prepared = prepare_inputs(data)
    prepare_ms = _ms(prepare_started)
    report: dict[str, dict[str, Any]] = {}
    market_ms = 0.0
    if phase3:  # QUANT_ANALYTICS_PHASE3: each section only once its silver table is there
        from .market_inputs import SECTION_TABLES

        market_started = time.perf_counter()
        market = load_phase3_inputs(connection, tenant_id=tenant_id, subject_id=subject_id, as_of=as_of,
                                    data=data, sections=phase3)
        prepared = {**prepared, **prepare_phase3_inputs(data, prepared, market)}
        market_ms = _ms(market_started)
        for name in phase3:
            if market.get(name) is None:
                report[name] = {"status": "skipped", "reason": f"{SECTION_TABLES[name]} is not loaded yet"}
        names = names + tuple(name for name in phase3 if name not in report)
    existing = {str(row["section"]): row for row in connection.execute(
        """
        SELECT section, source_data_sha256, quality_status FROM quant_analytics_snapshots
        WHERE tenant_id = ? AND subject_id = ? AND as_of_date = ? AND analytics_version = ?
        """, (tenant_id, subject_id, as_of, QUANT_ANALYTICS_VERSION)).fetchall()}
    errors: dict[str, str] = {}
    written = 0
    for name in names:
        inputs = prepared[name]
        fingerprint = section_fingerprint(name, inputs)
        old = existing.get(name)
        if only_if_changed and old and old["source_data_sha256"] == fingerprint and old["quality_status"] != "fail":
            report[name] = {"status": "unchanged"}
            continue
        section_started = time.perf_counter()
        try:
            payload, replaced = finite_copy(BUILDERS[name](inputs))
        except Exception as error:  # noqa: BLE001 - one broken engine never stops the other sections
            errors[name] = f"{type(error).__name__}: {error}"[:300]
            LOGGER.exception("quant section failed subject=%s as_of=%s section=%s", subject_id, as_of, name)
            report[name] = {"status": "error", "error": errors[name]}
            continue
        build_ms = _ms(section_started)
        quality = quality_status(payload, replaced)
        store_section(connection, tenant_id=tenant_id, subject_id=subject_id, as_of=as_of, payload=payload,
                      source_sha256=fingerprint, quality=quality, duration_ms=build_ms,
                      timings={"build_ms": build_ms, "load_ms": load_ms, "prepare_ms": prepare_ms,
                               "non_finite_replaced": replaced,
                               **({"market_load_ms": market_ms} if name in PHASE3_NAMES else {})})
        written += 1
        report[name] = {"status": "rebuilt", "available": payload["available"], "quality_status": quality,
                        "build_ms": build_ms}
    if written:
        publish_gate.before_commit(connection, {TABLE: {
            "subject_id": subject_id, "as_of_date": as_of, "analytics_version": QUANT_ANALYTICS_VERSION}},
            writer="quant_snapshots", environ=environ)
        connection.commit()
    else:
        connection.rollback()  # nothing to write: end the read transaction
    return {"status": "rebuilt" if written else "unchanged", "subject_id": subject_id, "as_of": as_of,
            "written": written, "sections": report, "errors": errors, "load_ms": load_ms,
            "duration_ms": _ms(started), **({"market_load_ms": market_ms} if phase3 else {})}


@contextmanager
def quant_date_lock(connection, *, tenant_id: str, subject_id: str, as_of: str):
    """One worker per subject and date, as risk_refresh.risk_date_lock: yields whether this
    session holds the advisory lock, and always ends the transaction and lets go after."""
    key = int.from_bytes(hashlib.sha256(canonical_json([QUANT_ANALYTICS_VERSION, tenant_id, subject_id, as_of])
                                        .encode()).digest()[:8], byteorder="big", signed=True)
    acquired = connection.execute("SELECT pg_try_advisory_lock(?) AS acquired", (key,)).fetchone()["acquired"]
    connection.commit()
    try:
        yield acquired
    finally:
        connection.rollback()
        if acquired:
            connection.execute("SELECT pg_advisory_unlock(?)", (key,))
            connection.commit()


def time_limit(minutes: float | None = None, environ: Mapping[str, str] | None = None) -> float:
    """Minutes the job may spend starting subject-dates: the argument, else
    QUANT_ANALYTICS_MINUTES, else DEFAULT_MINUTES (as certified_build.time_limit)."""
    if minutes is None:
        text = str((os.environ if environ is None else environ).get(MINUTES_SETTING, "")).strip()
        try:
            minutes = float(text) if text else DEFAULT_MINUTES
        except ValueError:
            minutes = DEFAULT_MINUTES
    if not math.isfinite(minutes) or minutes <= 0:
        raise ValueError("the time limit must be a positive number of minutes")
    return minutes


def check_contract_rules(connection, *, as_of_dates: Sequence[str],
                         rules: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """The table's own contract rules (publish_gate_rules.json, from its data contract) on the
    rows of this analytics version for the given dates: each rule's count of bad rows against
    the 0 it must be. A rule that cannot run is reported with its error."""
    contract = (publish_gate.load_rules() if rules is None else rules).get(TABLE)
    if contract is None:
        return [{"rule": "contract", "severity": "error", "error": "no contract rules for " + TABLE,
                 "passed": False}]
    raw = getattr(connection, "_connection", connection)  # past db.PostgresConnection's ? placeholders
    condition = "analytics_version = %(version)s AND as_of_date = ANY(%(dates)s::text[])"
    params = {"version": QUANT_ANALYTICS_VERSION, "dates": sorted(set(as_of_dates))}
    found = []
    for rule in contract["rules"]:
        item = {"rule": rule["name"], "severity": rule["severity"], "must_be": rule["mustBe"]}
        try:
            with raw.transaction():
                sql = publish_gate.scoped(rule["query"].replace("%", "%%"), TABLE, condition)
                row = raw.execute(sql, params).fetchone()
            count = int((row[0] if not isinstance(row, Mapping) else next(iter(row.values()))) or 0)
            item.update(count=count, passed=count == rule["mustBe"])
        except Exception as error:  # noqa: BLE001 - reported, never raised
            item.update(error=type(error).__name__, passed=False)
        found.append(item)
    connection.rollback()
    return found


def decoded_payload(value: Any) -> dict[str, Any]:
    payload = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value
    return dict(payload) if isinstance(payload, Mapping) else {}


def read_section(connection, *, tenant_id: str, subject_id: str, section: str,
                 as_of: str | None = None) -> dict[str, Any] | None:
    """The latest stored section on or before `as_of` (latest overall without it) whose quality
    is not fail, for this analytics version; None when there is none."""
    if section not in PHASE3_NAMES:
        section_names([section])
    if as_of is not None:
        as_of = date.fromisoformat(as_of).isoformat()
    row = connection.execute(
        """
        SELECT as_of_date, section, analytics_version, model_id, model_version, source_data_sha256,
               payload_sha256, payload_json, quality_status, generated_at
        FROM quant_analytics_snapshots
        WHERE tenant_id = ? AND subject_id = ? AND section = ? AND analytics_version = ?
          AND quality_status <> 'fail' AND (?::text IS NULL OR as_of_date <= ?::text)
        ORDER BY as_of_date DESC LIMIT 1
        """, (tenant_id, subject_id, section, QUANT_ANALYTICS_VERSION, as_of, as_of)).fetchone()
    if row is None:
        return None
    found = {key: row[key] for key in ("as_of_date", "section", "analytics_version", "model_id", "model_version",
                                       "source_data_sha256", "payload_sha256", "quality_status", "generated_at")}
    found["payload"] = decoded_payload(row["payload_json"])
    return found
