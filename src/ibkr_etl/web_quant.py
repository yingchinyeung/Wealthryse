"""Quant analytics pages: overnight sections, request-time Modelling models, cards and script.

Nothing here shows until ``QUANT_ANALYTICS_PAGES`` switches a page on, for example
``performance=shadow,risk=on,modelling=off``. A page that is missing, misspelt or ``off`` has
no data, no cards, no subpage links and no Metrics guide sections, and its routes answer 404
like a route that does not exist. ``shadow`` shows the page to owners only, for review;
``on`` shows it to everyone who can open the account. The setting is read on every request.

Two kinds of figures:

* Overnight sections, built by the quant analytics job into ``quant_analytics_snapshots``
  (gold). ``GET /api/portfolio/{id}/quant/{section}`` serves the newest row on or before
  ``as_of`` whose quality is not ``fail``. Until the table exists (its migration is held)
  the route answers 404 and the cards stay hidden.
* Request-time Modelling models (advanced optimisers, rebalancing, goals and scenarios)
  over exactly the holdings, prices, returns and factor fit the Modelling tab uses, through
  the reads ``web_modelling.install`` returns. Nothing is written anywhere.

QUANT_SCRIPT runs inside the account page script after MODELLING_SCRIPT and uses the page's
helpers (account, activeSection, reportingCurrency, esc, pct, pct2, signedPct, weightPct,
money, ratio, toned, analyticsDate, analyticsMonth). Its names all start with ``quant``.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from datetime import date, datetime
from html import escape
from typing import Any, Callable, Mapping, Sequence

from fastapi import Depends, FastAPI, HTTPException

from . import fama_french, market_inputs, model_cards, risk_forecasting
from . import metrics_guide_quant as quant_guide
from .metrics_guide import GuideAddition
from .modelling import (
    DISCLAIMER,
    FACTOR_MIN_WEEKS,
    OPTIMISE_MAX_HOLDINGS,
    PROJECTION_MIN_RETURNS,
    RETURN_SHRINKAGE,
    TRADING_DAYS,
    WEEKS_PER_YEAR,
    ModellingInputError,
    _portfolio_point,
    covariance_matrix,
    holding_return_matrix,
    monthly_block_returns,
    optimise,
)
from .performance_periods import PerformanceInputError
from .quant import goals, optimisers, rebalancing, stress_engine
from .quant.calibration import chi_square_survival
from .web_modelling import ModellingReads, _cached, _number, _shocks

PAGES_SETTING = "QUANT_ANALYTICS_PAGES"
PAGES = ("performance", "risk", "modelling")
MODES = ("off", "shadow", "on")
ANALYTICS_VERSION = "quant-analytics-v1"
NOT_FOUND = "Not Found"

# Overnight sections: the page that shows each and its model card.
SECTIONS = {
    "skill": ("performance", "skill-statistics"),
    "decisions": ("performance", "decision-attribution"),
    "currency": ("performance", "attribution"),
    "tail": ("risk", "tail-risk"),
    "regime": ("risk", "stress-engine"),
    "holdings_risk": ("risk", "holdings-risk"),
    "correlation": ("risk", "dynamic-correlation"),
    "concentration": ("risk", "crowding"),
    "execution": ("modelling", "execution-analysis"),
}
SECTION_LABELS = {
    "skill": "skill and luck", "decisions": "decision", "currency": "currency", "tail": "tail risk",
    "regime": "regime risk", "holdings_risk": "holdings risk", "correlation": "moving correlation",
    "concentration": "concentration", "execution": "trading cost",
}

# Subpages appended to each page: (slug, label, id of the card that starts it).
QUANT_SUBPAGES = {
    "performance": (
        ("skill-and-luck", "Skill and luck", "quant-skill"),
        ("decisions", "Decisions", "quant-decisions"),
        ("currency", "Currency", "quant-currency"),
    ),
    "risk": (
        ("holdings-risk", "Holdings risk", "quant-holdings-risk"),
        ("tail-risk", "Tail risk", "quant-tail"),
        ("regimes", "Regimes", "quant-regime"),
        ("moving-correlations", "Moving correlations", "quant-correlation"),
        ("concentration", "Concentration", "quant-concentration"),
    ),
    "modelling": (
        ("advanced-optimisers", "Advanced optimisers", "quant-optimisers"),
        ("rebalancing", "Rebalancing", "quant-rebalancing"),
        ("goals", "Goals", "quant-goals"),
        ("scenarios", "Scenarios", "quant-scenarios"),
        ("trading-costs", "Trading costs", "quant-execution"),
    ),
}

# Phase 3 overnight sections. Each shows only when its page is allowed by QUANT_ANALYTICS_PAGES
# AND the section by QUANT_ANALYTICS_PHASE3 (market_inputs.phase3_modes; shadow = owners only).
PHASE3_SECTIONS = {
    "factor_attribution": ("performance", "attribution"),
    "liquidity": ("risk", "liquidity"),
    "macro": ("risk", "macro-factors"),
    "crowding": ("risk", "crowding"),
}
PHASE3_LABELS = {"factor_attribution": "factor attribution", "liquidity": "liquidity", "macro": "macro exposure",
                 "crowding": "crowding"}
PHASE3_SUBPAGES = {
    "factor_attribution": ("factor-attribution", "Factor attribution", "quant-factor-attribution"),
    "liquidity": ("liquidity", "Liquidity", "quant-liquidity"),
    "macro": ("macro-exposures", "Macro exposures", "quant-macro"),
    "crowding": ("crowding", "Crowding", "quant-crowding"),
}

OPTIMISER_LABELS = {
    "black_litterman": "Black-Litterman",
    "hierarchical_risk_parity": "Hierarchical risk parity",
    "minimum_tail_loss": "Minimum tail loss",
    "mean_variance_costs": "Mean-variance after costs",
    "robust_mean_variance": "Robust mean-variance",
}
# Today's Optimisation page portfolios, which rebalancing can also aim for.
CLASSIC_OPTIMISERS = {"minimum_variance": "Minimum variance", "risk_parity": "Risk parity",
                      "maximum_sharpe": "Maximum Sharpe"}
MAX_VIEWS = 5
MAX_TARGETS = 60
TAIL_LEVEL = 0.95
DEFAULT_GOAL_MULTIPLE = 1.5


# --- Settings ---------------------------------------------------------------------------

def page_modes(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """``{page: mode}`` from QUANT_ANALYTICS_PAGES; anything unknown or missing is off."""

    text = (os.environ if environ is None else environ).get(PAGES_SETTING, "") or ""
    modes = {page: "off" for page in PAGES}
    for part in str(text).split(","):
        name, separator, mode = part.partition("=")
        name, mode = name.strip().lower(), mode.strip().lower()
        if separator and name in modes and mode in MODES:
            modes[name] = mode
    return modes


def page_allowed(page: str, user: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None) -> bool:
    """On for everyone with access, or shadow and this viewer is an owner."""

    mode = page_modes(environ).get(page, "off")
    return mode == "on" or (mode == "shadow" and str((user or {}).get("role") or "") == "owner")


def allowed_pages(user: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    modes = page_modes(environ)
    role = str((user or {}).get("role") or "")
    return tuple(page for page in PAGES if modes[page] == "on" or (modes[page] == "shadow" and role == "owner"))


def phase3_allowed(section: str, user: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None) -> bool:
    """A phase 3 section shows when its page is allowed and QUANT_ANALYTICS_PHASE3 allows it."""

    page = PHASE3_SECTIONS[section][0]
    return page_allowed(page, user, environ) and market_inputs.section_allowed(section, user, environ)


def allowed_phase3(user: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    return tuple(name for name in PHASE3_SECTIONS if phase3_allowed(name, user, environ))


def _phase3_on(page: str, user: Mapping[str, Any] | None) -> tuple[str, ...]:
    return tuple(name for name in allowed_phase3(user) if PHASE3_SECTIONS[name][0] == page)


# --- Account page pieces (all empty when the page is off) --------------------------------

def subpages(section: str, user: Mapping[str, Any] | None) -> tuple[tuple[str, str, str], ...]:
    return (QUANT_SUBPAGES.get(section, ()) + tuple(PHASE3_SUBPAGES[name] for name in _phase3_on(section, user))
            if section in PAGES and page_allowed(section, user) else ())


def page_cards(section: str, user: Mapping[str, Any] | None) -> str:
    return (QUANT_CARDS[section] + "".join(PHASE3_CARDS[name] for name in _phase3_on(section, user))
            if section in PAGES and page_allowed(section, user) else "")


def page_css(user: Mapping[str, Any] | None) -> str:
    return QUANT_CSS if allowed_pages(user) else ""


def page_script(user: Mapping[str, Any] | None) -> str:
    return QUANT_SCRIPT + (PHASE3_SCRIPT if allowed_phase3(user) else "") if allowed_pages(user) else ""


def render_hook(user: Mapping[str, Any] | None) -> str:
    """The call renderAsOf makes; it loads the quant cards of the open page."""

    return "void renderQuant(asOf);" if allowed_pages(user) else ""


def guide_additions(user: Mapping[str, Any] | None) -> tuple[GuideAddition, ...]:
    addition = quant_guide.addition(allowed_pages(user), allowed_phase3(user))
    return (addition,) if addition else ()


# --- Gold reads -------------------------------------------------------------------------

SNAPSHOT_SQL = """
SELECT as_of_date, section, analytics_version, model_id, model_version, payload_json,
       quality_status, generated_at
FROM quant_analytics_snapshots
WHERE tenant_id = %s AND subject_id = %s AND section = %s AND analytics_version = %s
  AND quality_status IN ('pass', 'warn')
  AND (%s::text IS NULL OR as_of_date <= %s)
ORDER BY as_of_date DESC
LIMIT 1
"""


def read_snapshot(connection: Any, *, tenant_id: str, subject_id: str, section: str,
                  as_of: str | None) -> dict[str, Any] | None:
    """The newest servable row on or before ``as_of``; None if there is none or no table yet.

    The table's migration is held, so a missing relation (or schema, or grant) is the same
    as no row. The query runs in a savepoint so the failure leaves the connection usable.
    """

    import psycopg

    try:
        with connection.transaction():
            row = connection.execute(
                SNAPSHOT_SQL, (tenant_id, subject_id, section, ANALYTICS_VERSION, as_of, as_of)
            ).fetchone()
    except (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName,
            psycopg.errors.InsufficientPrivilege):
        return None
    return dict(row) if row else None


def _decoded(value: Any) -> dict[str, Any]:
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value or {})


def _stamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _clean(value: Any) -> Any:
    """JSON-safe copy: non-finite numbers become None."""

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _card_facts(model_id: str) -> dict[str, Any]:
    try:
        card = model_cards.as_dict(model_id)
    except KeyError:
        return {"model_name": None, "model_status": None, "model_limits": []}
    return {"model_name": card.get("name"), "model_status": card.get("status"),
            "model_limits": list(card.get("limits") or [])}


def section_response(account_id: str, section: str, row: Mapping[str, Any], mode: str) -> dict[str, Any]:
    payload = _decoded(row.get("payload_json"))
    model_id = str(row.get("model_id") or payload.get("model_id") or SECTIONS[section][1])
    return _clean({
        **payload,
        "account_id": account_id,
        "section": section,
        "as_of_date": str(row.get("as_of_date") or "")[:10] or None,
        "generated_at": _stamp(row.get("generated_at")),
        "quality_status": row.get("quality_status"),
        "analytics_version": row.get("analytics_version") or ANALYTICS_VERSION,
        "model_id": model_id,
        "model_version": row.get("model_version") or payload.get("model_version"),
        **_card_facts(model_id),
        "page_mode": mode,
    })


# --- Request-time models ----------------------------------------------------------------

def sleeve_inputs(held: Sequence[Mapping[str, Any]], prices: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    """The Optimisation page's own inputs: the same holdings, covariance and expected returns.

    As ``modelling.optimise``: the largest 20 long holdings with a year of daily prices,
    covariance shrunk toward its diagonal, each holding's average return shrunk halfway to
    the average holding, and today's weights as shares of that sleeve.
    """

    current = {str(row["conid"]): float(row["weight"]) for row in held if row.get("weight") is not None}
    labels = {str(row["conid"]): {"symbol": row.get("symbol"), "description": row.get("description")} for row in held}
    matrix = holding_return_matrix(prices)
    returns = matrix["returns"]
    keys = [k for k in sorted(current, key=lambda k: -current[k]) if k in returns and current[k] > 0]
    keys = keys[:OPTIMISE_MAX_HOLDINGS]
    if len(keys) < 2:
        raise ModellingInputError("These models need at least two holdings with a year of daily prices.")
    if len(matrix["dates"]) < PROJECTION_MIN_RETURNS:
        raise ModellingInputError("Not enough shared daily price history to estimate risk.")
    columns = [returns[k] for k in keys]
    cov = covariance_matrix(columns)
    raw_mu = [statistics.fmean(c) * TRADING_DAYS for c in columns]
    average = statistics.fmean(raw_mu)
    mu = [RETURN_SHRINKAGE * average + (1.0 - RETURN_SHRINKAGE) * m for m in raw_mu]
    sleeve = sum(current[k] for k in keys)
    return {
        "keys": keys, "labels": labels, "current": current, "matrix": matrix, "columns": columns, "cov": cov,
        "mu": mu, "sleeve": sleeve, "weights_now": [current[k] / sleeve for k in keys],
        "dates": matrix["dates"],
        "excluded": [k for k in current if k not in keys and current[k] > 0],
    }


def _symbol(inputs: Mapping[str, Any], key: str) -> str:
    return str((inputs["labels"].get(key) or {}).get("symbol") or key)


def _holding_index(inputs: Mapping[str, Any]) -> dict[str, int]:
    index: dict[str, int] = {}
    for i, key in enumerate(inputs["keys"]):
        index[key.upper()] = i
        index.setdefault(_symbol(inputs, key).upper(), i)
    return index


def parse_views(text: str | None, inputs: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``SYM:return%:confidence%`` (absolute) or ``A>B:return%:confidence%`` (relative), confidence optional."""

    index = _holding_index(inputs)
    views, described = [], []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if len(views) >= MAX_VIEWS:
            raise HTTPException(status_code=400, detail=f"Give at most {MAX_VIEWS} views")
        pieces = part.split(":")
        if len(pieces) not in (2, 3) or not pieces[0].strip():
            raise HTTPException(status_code=400, detail="Write each view as SYMBOL:return% or A>B:return%, "
                                                        "with an optional :confidence%")
        better, _, worse = pieces[0].strip().upper().partition(">")
        better, worse = better.strip(), worse.strip()
        if not better:
            raise HTTPException(status_code=400, detail="Each view needs a holding, as SYMBOL:return%")
        if ">" in pieces[0] and (not worse or worse == better):
            raise HTTPException(status_code=400, detail="A relative view needs two different holdings, as A>B")
        row = [0.0] * len(inputs["keys"])
        for name, sign in ((better, 1.0), (worse, -1.0)):
            if not name:
                continue
            if name not in index:
                raise HTTPException(status_code=400, detail=f"{name[:20]} is not one of the optimised holdings")
            row[index[name]] += sign
        expected = _number(pieces[1], "Each view's return", low=-50.0, high=50.0)
        if expected is None:
            raise HTTPException(status_code=400, detail="Each view needs a return in % a year")
        confidence = _number(pieces[2], "Each view's confidence", low=1.0, high=100.0) if len(pieces) == 3 else None
        view: dict[str, Any] = {"weights": row, "return": expected / 100.0}
        if confidence is not None:
            view["confidence"] = confidence / 100.0
        views.append(view)
        described.append({"holding": better, "against": worse or None, "return": expected / 100.0,
                          "confidence": None if confidence is None else confidence / 100.0})
    return views, described


def chi_square_quantile(probability: float, degrees_of_freedom: int) -> float:
    """x with P(χ²_k ≤ x) = probability, by bisection on the survival function."""

    tail = 1.0 - probability
    low, high = 0.0, max(10.0, degrees_of_freedom + 12.0 * math.sqrt(2.0 * degrees_of_freedom))
    while chi_square_survival(high, degrees_of_freedom) > tail:
        high *= 2.0
    for _ in range(200):
        middle = 0.5 * (low + high)
        if chi_square_survival(middle, degrees_of_freedom) > tail:
            low = middle
        else:
            high = middle
    return 0.5 * (low + high)


def tail_loss(weights: Sequence[float], columns: Sequence[Sequence[float]], level: float = TAIL_LEVEL) -> float:
    """Average daily loss on the worst (1 − level) of days at these weights, as a positive number."""

    series = sorted(sum(w * column[t] for w, column in zip(weights, columns)) for t in range(len(columns[0])))
    count = max(1, math.ceil((1.0 - level) * len(series) - 1e-9))
    return -statistics.fmean(series[:count])


def _trimmed(result: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if k not in ("posterior_covariance", "linkage", "covariance")}


def _advanced_optimisers(inputs: Mapping[str, Any], *, risk_free: float, cap: float, risk_aversion: float,
                         cost_bps: float, turnover_limit: float | None, confidence: float,
                         views: Sequence[Mapping[str, Any]], only: str | None = None) -> dict[str, Any]:
    """The five advanced optimisers on the Optimisation page's inputs, compared on one basis.

    Every portfolio's expected return, volatility and Sharpe ratio use the same shrunk
    expected returns and covariance as today's optimiser; tail loss is the average of the
    worst 5% of the year's daily returns at those weights. Black-Litterman's market is
    today's sleeve. The two mean-variance models take λ = δ/2, so all four utility models
    share U = μ'w − (δ/2)w'Σw. The robust model's uncertainty is the covariance over the
    years of data, with κ² the chi-square quantile at ``confidence``. A model that cannot
    run is marked unavailable with its reason; the others still run.
    """

    keys, cov, mu, columns, now = inputs["keys"], inputs["cov"], inputs["mu"], inputs["columns"], inputs["weights_now"]
    excess = [m - risk_free for m in mu]
    years = len(inputs["dates"]) / TRADING_DAYS
    kappa = math.sqrt(chi_square_quantile(confidence, len(keys)))
    omega = [[value / years for value in row] for row in cov]
    rows = [list(day) for day in zip(*columns)]
    lam = risk_aversion / 2.0
    runs: dict[str, Callable[[], dict[str, Any]]] = {
        "black_litterman": lambda: optimisers.black_litterman(cov, now, risk_aversion=risk_aversion, views=views,
                                                              cap=cap),
        "hierarchical_risk_parity": lambda: optimisers.hierarchical_risk_parity(cov),
        "minimum_tail_loss": lambda: optimisers.min_cvar_weights(rows, level=TAIL_LEVEL, cap=cap),
        "mean_variance_costs": lambda: optimisers.mean_variance_with_costs(
            excess, cov, now, risk_aversion=lam, cost_bps=cost_bps, turnover_limit=turnover_limit, cap=cap),
        "robust_mean_variance": lambda: optimisers.robust_mean_variance(
            excess, cov, uncertainty_covariance=omega, kappa=kappa, risk_aversion=lam, cap=cap),
    }

    def point(weights: Sequence[float]) -> dict[str, Any]:
        return {**_portfolio_point(weights, cov, mu, risk_free), "tail_loss_95": tail_loss(weights, columns),
                "turnover": math.fsum(abs(a - b) for a, b in zip(weights, now))}

    portfolios: dict[str, dict[str, Any]] = {"current": {"available": True, **point(now), "turnover": 0.0}}
    results, methods, weights_by = {}, {}, {}
    for name, run in runs.items():
        if only is not None and name != only:
            continue
        try:
            result = run()
        except ModellingInputError as error:
            portfolios[name] = {"available": False, "reason": str(error)}
            continue
        weights = [float(w) for w in result["weights"]]
        weights_by[name] = weights
        extra: dict[str, Any] = {}
        if name == "black_litterman":
            extra["posterior_expected_return"] = math.fsum(
                w * (r + risk_free) for w, r in zip(weights, result.get("posterior_returns") or [0.0] * len(weights)))
        elif name == "minimum_tail_loss":
            extra["optimised_tail_loss"] = result.get("expected_tail_loss")
        elif name == "mean_variance_costs":
            extra.update(trading_cost=result.get("trading_cost"), net_expected_return=(
                None if result.get("net_expected_return") is None else result["net_expected_return"] + risk_free))
        elif name == "robust_mean_variance":
            extra["worst_case_return"] = (None if result.get("worst_case_return") is None
                                          else result["worst_case_return"] + risk_free)
        portfolios[name] = {"available": True, **point(weights), **extra}
        results[name] = _trimmed(result)
        methods[name] = result.get("method", "")
    holdings = [{"conid": key, "symbol": _symbol(inputs, key),
                 "description": (inputs["labels"].get(key) or {}).get("description") or "",
                 "current_weight": now[i], **{f"{name}_weight": w[i] for name, w in weights_by.items()}}
                for i, key in enumerate(keys)]
    return {
        "available": True,
        "model_version": optimisers.MODEL_VERSION,
        "window_start": inputs["dates"][0],
        "window_end": inputs["dates"][-1],
        "observations": len(inputs["dates"]),
        "risk_free_rate": risk_free,
        "maximum_weight": max(cap, 1.0 / len(keys) + 1e-9),
        "risk_aversion": risk_aversion,
        "cost_bps": cost_bps,
        "turnover_limit": turnover_limit,
        "confidence": confidence,
        "kappa": kappa,
        "sleeve_weight": inputs["sleeve"],
        "holdings": holdings,
        "portfolios": portfolios,
        "results": results,
        "methods": methods,
        "weights": weights_by,
        "method": (
            f"Same holdings and inputs as the Optimisation page: covariance of {len(inputs['dates'])} sessions of daily "
            f"price returns, annualised and shrunk toward its diagonal; expected returns are each holding's average shrunk "
            f"{RETURN_SHRINKAGE:.0%} toward the average holding. Every portfolio is scored on those, with tail loss the "
            f"average of the worst 5% of days at its weights. Risk aversion δ = {risk_aversion:g} (the two mean-variance "
            f"models use δ/2 without the ½). Robust κ = {kappa:.2f} for {confidence:.0%} confidence."
        ),
        "disclaimer": DISCLAIMER,
    }


def _valuation_base(held: Sequence[Mapping[str, Any]]) -> float | None:
    """The amount the weights are shares of (NAV, or gross holdings when NAV is missing)."""

    best = None
    for row in held:
        try:
            value, weight = float(row.get("reporting_market_value_text")), float(row.get("weight"))
        except (TypeError, ValueError):
            continue
        if weight and math.isfinite(value) and math.isfinite(weight) and (best is None or abs(weight) > best[1]):
            best = (abs(value / weight), abs(weight))
    return best[0] if best else None


def _share_prices(held: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    prices = {}
    for row in held:
        try:
            value, quantity = float(row.get("reporting_market_value_text")), float(row.get("quantity_text"))
        except (TypeError, ValueError):
            continue
        if quantity and math.isfinite(value) and math.isfinite(quantity) and value / quantity > 0:
            prices[str(row["conid"])] = value / quantity
    return prices


def parse_targets(text: str | None, inputs: Mapping[str, Any]) -> dict[str, float]:
    """``SYM=fraction,...`` as shares of NAV, for holdings in the optimised sleeve."""

    index = _holding_index(inputs)
    targets: dict[str, float] = {}
    parts = [part for part in (text or "").split(",") if part.strip()]
    if len(parts) > MAX_TARGETS:
        raise HTTPException(status_code=400, detail=f"Give at most {MAX_TARGETS} target weights")
    for part in parts:
        name, separator, raw = part.partition("=")
        name = name.strip().upper()
        if not separator or not name or len(name) > 40:
            raise HTTPException(status_code=400, detail="Target weights must be written as SYMBOL=fraction")
        if name not in index:
            raise HTTPException(status_code=400, detail=f"{name[:20]} is not a holding with a year of daily prices")
        weight = _number(raw, "Each target weight", low=0.0, high=1.0)
        targets[inputs["keys"][index[name]]] = weight or 0.0
    return targets


def _factor_covariance(daily: Mapping[str, Mapping[str, float]], model: Mapping[str, Any]) -> tuple[dict, int]:
    """Monthly covariance of the model's factors: weekly returns over its window, × 52/12."""

    names = [row["factor"] for row in model["exposures"]]
    start, end = str(model["window_start"]), str(model["window_end"])
    weeks: dict[str, dict[str, float]] = {}
    for day in sorted(daily):
        row = daily[day]
        if any(name not in row for name in names):
            continue
        parsed = date.fromisoformat(str(day)[:10])
        monday = date.fromordinal(parsed.toordinal() - parsed.weekday()).isoformat()
        if not start <= monday <= end:
            continue
        week = weeks.setdefault(monday, {name: 0.0 for name in names})
        for name in names:
            week[name] = (1.0 + week[name]) * (1.0 + float(row[name])) - 1.0
    if len(weeks) < FACTOR_MIN_WEEKS:
        raise ModellingInputError(f"The scenarios need at least {FACTOR_MIN_WEEKS} weeks of factor returns.")
    ordered = [weeks[w] for w in sorted(weeks)]
    series = {name: [week[name] for week in ordered] for name in names}
    scale = WEEKS_PER_YEAR / 12.0
    return ({a: {b: statistics.covariance(series[a], series[b]) * scale for b in names} for a in names},
            len(ordered))


def _returns_key(kind: str, tenant_id: str, account_id: str, returns: Sequence[tuple[str, float]]) -> tuple:
    return (kind, tenant_id, account_id, returns[0][0] if returns else None, returns[-1][0] if returns else None,
            len(returns), round(math.fsum(r for _, r in returns), 12))


def goal_models(values: Sequence[float], *, start: float, target: float, months: int, contribution: float,
                withdrawal: float, probability: float) -> dict[str, Any]:
    """Goal projection and required saving under the history (bootstrap) and regime models."""

    contributions = [{"amount": contribution, "month": 1, "repeat": "monthly"}] if contribution else []
    withdrawals = [{"amount": withdrawal, "month": 1, "repeat": "monthly"}] if withdrawal else []
    plan = dict(contributions=contributions, withdrawals=withdrawals, target=target, horizon_months=months)
    models: dict[str, Any] = {}
    sources: dict[str, Callable[[], dict[str, Any]]] = {
        "history": lambda: {"monthly_return_pool": monthly_block_returns(values)},
    }

    def regimes() -> dict[str, Any]:
        if len(values) < TRADING_DAYS:
            raise ModellingInputError("Regimes need at least a year of daily returns.")
        window = list(values[-risk_forecasting.REGIME_HISTORY:])
        fitted = risk_forecasting.choose_regimes(window)
        model = goals.monthly_regime_model(window, fitted)
        model.setdefault("labels", list(risk_forecasting.REGIME_LABELS.get(len(fitted["means"]), ())))
        return {"regime_model": model}

    sources["regimes"] = regimes
    for name, source in sources.items():
        try:
            market = source()
            projection = goals.goal_projection(start, **market, **plan)
            required = goals.required_contribution(start, **market, **plan, probability=probability)
        except ModellingInputError as error:
            models[name] = {"available": False, "reason": str(error)}
            continue
        models[name] = {"available": True, "projection": projection, "required": required}
    return models


# --- Routes -----------------------------------------------------------------------------

def install(
    app: FastAPI,
    *,
    authorized_user: Callable[..., dict[str, Any]],
    access_context: Callable[[str, dict], dict],
    parse_as_of: Callable[[str | None], str | None],
    read_database: Callable[[], Any],
    modelling: ModellingReads,
    tenant_id: str,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Add /api/portfolio/{id}/quant/{section} and /api/portfolio/{id}/modelling/{optimisers,
    rebalancing,goals,scenarios}. Each answers 404 while its page is off for the viewer."""

    def require(page: str, user: Mapping[str, Any]) -> str:
        if not page_allowed(page, user, environ):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return page_modes(environ)[page]

    def model_error(error: Exception) -> HTTPException:
        return HTTPException(status_code=422, detail=str(error))

    def phase3_section(account_id: str, section: str, as_of: str | None, user: Mapping[str, Any]) -> dict:
        """A phase 3 section: 404 unless its page and QUANT_ANALYTICS_PHASE3 both allow it."""
        page = PHASE3_SECTIONS[section][0]
        if not phase3_allowed(section, user, environ):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        modes = (page_modes(environ)[page], market_inputs.phase3_modes(environ)[section])
        access_context(account_id, user)
        requested = parse_as_of(as_of)
        with read_database() as connection:
            row = read_snapshot(connection, tenant_id=tenant_id, subject_id=account_id, section=section,
                                as_of=requested)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No {PHASE3_LABELS[section]} figures have been built "
                                                        "for this account on or before this date yet.")
        return section_response(account_id, section, row, "shadow" if "shadow" in modes else "on")

    @app.get("/api/portfolio/{account_id}/quant/{section}")
    def quant_section(account_id: str, section: str, as_of: str | None = None,
                      user: dict[str, Any] = Depends(authorized_user)) -> dict:
        """One overnight section: the newest row on or before as_of whose quality is not fail."""
        if section in PHASE3_SECTIONS:
            return phase3_section(account_id, section, as_of, user)
        if section not in SECTIONS:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        mode = require(SECTIONS[section][0], user)
        access_context(account_id, user)
        requested = parse_as_of(as_of)
        with read_database() as connection:
            row = read_snapshot(connection, tenant_id=tenant_id, subject_id=account_id, section=section,
                                as_of=requested)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No {SECTION_LABELS[section]} figures have been built "
                                                        "for this account on or before this date yet.")
        return section_response(account_id, section, row, mode)

    def sleeve(account_id: str, user: Mapping[str, Any], as_of: str | None):
        access = modelling.holding_access(account_id, user)
        requested = parse_as_of(as_of)
        with read_database() as connection:
            snapshot, held, prices = modelling.holdings_and_prices(connection, account_id, access, requested)
            risk_free = modelling.risk_free_rate(connection, account_id, requested)
        try:
            inputs = sleeve_inputs(held, prices)
        except ModellingInputError as error:
            raise model_error(error) from error
        return access, snapshot, held, inputs, risk_free

    def optimiser_settings(max_weight, risk_aversion, cost_bps, turnover_limit, confidence) -> dict[str, Any]:
        cap = _number(max_weight, "The maximum weight", low=1.0, high=100.0)
        delta = _number(risk_aversion, "The risk aversion", low=0.5, high=20.0)
        cost = _number(cost_bps, "The trading cost", low=0.0, high=200.0)
        limit = _number(turnover_limit, "The turnover limit", low=0.0, high=200.0)
        level = _number(confidence, "The robust confidence", low=50.0, high=99.9)
        return {"cap": (cap or 25.0) / 100.0, "risk_aversion": delta or 2.5, "cost_bps": 10.0 if cost is None else cost,
                "turnover_limit": None if limit is None else limit / 100.0,
                "confidence": (level or 95.0) / 100.0}

    @app.get("/api/portfolio/{account_id}/modelling/optimisers")
    def quant_optimisers(account_id: str, as_of: str | None = None, max_weight: str | None = None,
                         risk_aversion: str | None = None, cost_bps: str | None = None,
                         turnover_limit: str | None = None, confidence: str | None = None,
                         views: str | None = None, user: dict[str, Any] = Depends(authorized_user)) -> dict:
        """Black-Litterman, HRP, minimum tail loss, mean-variance after costs and robust mean-variance."""
        require("modelling", user)
        settings = optimiser_settings(max_weight, risk_aversion, cost_bps, turnover_limit, confidence)
        access, snapshot, _held, inputs, risk_free = sleeve(account_id, user, as_of)
        parsed, described = parse_views(views, inputs)
        try:
            result = _advanced_optimisers(inputs, risk_free=risk_free or 0.0, views=parsed, **settings)
        except ModellingInputError as error:
            raise model_error(error) from error
        return _clean({"account_id": account_id, "currency": access["reporting_currency"], "snapshot_date": snapshot,
                       "risk_free_available": risk_free is not None, "views": described,
                       "excluded": [_symbol(inputs, k) for k in inputs["excluded"]],
                       **_card_facts("optimisers"), **result})

    @app.get("/api/portfolio/{account_id}/modelling/rebalancing")
    def quant_rebalancing(account_id: str, as_of: str | None = None, target: str | None = None,
                          optimiser: str | None = None, cost_bps: str | None = None,
                          risk_aversion: str | None = None, max_weight: str | None = None,
                          user: dict[str, Any] = Depends(authorized_user)) -> dict:
        """No-trade bands and a plan toward target weights (SYM=fraction) or an optimiser's weights."""
        require("modelling", user)
        if bool((target or "").strip()) == bool((optimiser or "").strip()):
            raise HTTPException(status_code=400, detail="Give target weights or choose an optimiser, not both")
        if optimiser and optimiser not in OPTIMISER_LABELS and optimiser not in CLASSIC_OPTIMISERS:
            raise HTTPException(status_code=400, detail="Unknown optimiser")
        settings = optimiser_settings(max_weight, risk_aversion, cost_bps, None, None)
        access, snapshot, held, inputs, risk_free = sleeve(account_id, user, as_of)
        keys, sleeve_weight = inputs["keys"], inputs["sleeve"]
        current = {k: w for k, w in inputs["current"].items() if w > 0}
        try:
            if optimiser:
                if optimiser in CLASSIC_OPTIMISERS:
                    classic = optimise(inputs["current"], inputs["labels"], inputs["matrix"],
                                       risk_free=risk_free or 0.0, cap=settings["cap"])
                    weights = [row[f"{optimiser}_weight"] for row in classic["holdings"]]
                else:
                    advanced = _advanced_optimisers(inputs, risk_free=risk_free or 0.0, views=(), confidence=0.95,
                                                    only=optimiser, turnover_limit=None,
                                                    **{k: settings[k] for k in ("cap", "risk_aversion", "cost_bps")})
                    if optimiser not in advanced["weights"]:
                        raise ModellingInputError(advanced["portfolios"][optimiser]["reason"])
                    weights = advanced["weights"][optimiser]
                targets = {key: w * sleeve_weight for key, w in zip(keys, weights)}
                source = (CLASSIC_OPTIMISERS | OPTIMISER_LABELS)[optimiser]
            else:
                chosen = parse_targets(target, inputs)
                targets = {key: chosen.get(key, current.get(key, 0.0)) for key in keys}
                source = "Your target weights"
            others = {k: w for k, w in current.items() if k not in keys}
            if math.fsum(targets.values()) + math.fsum(others.values()) > 1.0 + 1e-9:
                raise HTTPException(status_code=400, detail="Target weights add up to more than 100% of NAV")
            bands = rebalancing.no_trade_bands(inputs["cov"], targets, cost_bps=settings["cost_bps"],
                                               risk_aversion=settings["risk_aversion"])
            base = _valuation_base(held)
            if base is None:
                raise ModellingInputError("The holdings have no values to size the trades with.")
            plan = rebalancing.rebalance_plan({**{k: current.get(k, 0.0) for k in keys}, **others},
                                              {**targets, **others}, bands["bands"], nav=base,
                                              prices=_share_prices(held), cost_bps=settings["cost_bps"])
        except ModellingInputError as error:
            raise model_error(error) from error
        for row in (*plan["holdings"], *plan["trades"]):
            row["symbol"] = _symbol(inputs, row["holding"])
        for row in bands["holdings"]:
            row["symbol"] = _symbol(inputs, row["holding"])
        return _clean({"account_id": account_id, "currency": access["reporting_currency"], "snapshot_date": snapshot,
                       "target_source": source, "optimiser": optimiser or None, "bands": bands, **_card_facts("rebalancing"),
                       "left_out": [_symbol(inputs, k) for k in inputs["excluded"]], **plan,
                       "model_version": rebalancing.MODEL_VERSION})

    @app.get("/api/portfolio/{account_id}/modelling/goals")
    def quant_goals(account_id: str, as_of: str | None = None, target: str | None = None, years: str | None = None,
                    monthly_contribution: str | None = None, monthly_withdrawal: str | None = None,
                    probability: str | None = None, user: dict[str, Any] = Depends(authorized_user)) -> dict:
        """Chance of reaching a target and the saving needed, from history and from market regimes."""
        require("modelling", user)
        horizon = _number(years, "The number of years", low=1.0, high=50.0) or 10.0
        if not float(horizon).is_integer():
            raise HTTPException(status_code=400, detail="The number of years must be a whole number")
        goal = _number(target, "The target value", low=0.01, high=1e13)
        saving = _number(monthly_contribution, "The monthly saving", low=0.0, high=1e10) or 0.0
        spending = _number(monthly_withdrawal, "The monthly withdrawal", low=0.0, high=1e10) or 0.0
        chance = (_number(probability, "The chance wanted", low=50.0, high=99.0) or 80.0) / 100.0
        access = access_context(account_id, user)
        requested = parse_as_of(as_of)
        with read_database() as connection:
            try:
                nav, returns = modelling.portfolio_returns(connection, account_id, access, requested)
            except PerformanceInputError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        if len(returns) < PROJECTION_MIN_RETURNS or not nav:
            raise HTTPException(status_code=422, detail="Goals need at least three months of daily returns.")
        last_day = next(reversed(nav))
        start = float(nav[last_day])
        if start <= 0:
            raise HTTPException(status_code=422, detail="Goals need a positive value to start from.")
        aim = goal or start * DEFAULT_GOAL_MULTIPLE
        values = [r for _, r in returns]
        months = int(horizon) * 12
        key = (*_returns_key("quant-goals", tenant_id, account_id, returns), aim, months, saving, spending, chance)
        models = _cached(key, lambda: goal_models(values, start=start, target=aim, months=months, contribution=saving,
                                                  withdrawal=spending, probability=chance))
        return _clean({"account_id": account_id, "currency": access["reporting_currency"], "as_of": last_day,
                       "start_value": start, "target": aim, "target_source": "yours" if goal else "default",
                       "years": int(horizon), "monthly_contribution": saving, "monthly_withdrawal": spending,
                       "probability": chance, "history_start": returns[0][0], "models": models,
                       "model_version": goals.MODEL_VERSION, **_card_facts("goals"), "disclaimer": DISCLAIMER})

    @app.get("/api/portfolio/{account_id}/modelling/scenarios")
    def quant_scenarios(account_id: str, as_of: str | None = None, region: str = fama_french.DEFAULT_REGION,
                        scenario: str | None = None, shocks: str | None = None, loss: str | None = None,
                        user: dict[str, Any] = Depends(authorized_user)) -> dict:
        """A linked (conditional) factor scenario and a reverse stress test on today's factor model."""
        require("modelling", user)
        if scenario and scenario != "custom":
            if scenario not in risk_forecasting.STRESS_PRESETS:
                raise HTTPException(status_code=400, detail="Unknown stress scenario")
            label, chosen = risk_forecasting.STRESS_PRESETS[scenario]
        else:
            label, chosen = "Custom shocks", _shocks(shocks)
            if not chosen or not any(chosen.values()):
                raise HTTPException(status_code=400, detail="Choose at least one factor shock")
        size = (_number(loss, "The loss to test", low=1.0, high=60.0) or 20.0) / 100.0
        access = access_context(account_id, user)
        factors, model, value = modelling.fitted_factors(account_id, access, parse_as_of(as_of), region)
        try:
            covariance, weeks = _factor_covariance(factors["factors"], model)
            betas = {row["factor"]: row["beta"] for row in model["exposures"]}
            shocked = {name: move for name, move in chosen.items() if name in covariance}
            if not shocked:
                raise ModellingInputError("None of the shocked factors is in the fitted model.")
            linked = stress_engine.conditional_scenario(covariance, shocked)
            impact = stress_engine.scenario_impact(betas, linked)
            named = risk_forecasting.factor_stress(model, shocked)
            reverse = stress_engine.reverse_stress(betas, covariance, size)
        except ModellingInputError as error:
            raise model_error(error) from error
        return _clean({
            "account_id": account_id, "currency": access["reporting_currency"], "scenario": scenario or "custom",
            "scenario_label": label, "region": model["region"], "window_start": model["window_start"],
            "window_end": model["window_end"], "weeks": weeks, "r_squared": model.get("r_squared"), "nav": value,
            "factors_not_in_model": [name for name in chosen if name not in covariance],
            "linked": linked, "impact": impact, "named_only": named, "reverse": reverse,
            "linked_impact_value": value * impact["portfolio_return"] if value else None,
            "named_impact_value": value * named["impact"] if value else None,
            "loss_value": value * size if value else None,
            "presets": {k: v[0] for k, v in risk_forecasting.STRESS_PRESETS.items()},
            "model_id": "stress-engine", "model_version": stress_engine.MODEL_VERSION, **_card_facts("stress-engine"),
            "method": (f"{linked.get('method', '')} The factor covariance is from {weeks} weeks of factor returns over "
                       "the factor model's window, scaled to a month. Portfolio moves use the fitted betas only: "
                       "alpha and the part of returns the factors do not explain are left out."),
            "disclaimer": DISCLAIMER,
        })


# --- Cards ------------------------------------------------------------------------------

def _guide_href(name: str) -> str:
    return escape(f"/metrics-guide#{quant_guide.METRIC_ANCHORS[name]}", quote=True)


def _card(card_id: str, title: str, guide_name: str, intro: str, form: str = "") -> str:
    return (
        f'<div class="card wide quant-card" id="{card_id}" hidden><div class="label">'
        f'<a class="metric-guide-heading-link" href="{_guide_href(guide_name)}">{escape(title)} '
        '<span aria-hidden="true">↗</span></a></div>'
        f'<div class="muted">{escape(intro)}</div>{form}<div class="quant-body"></div>'
        '<div class="quant-note muted"></div><div class="quant-model muted"></div></div>'
    )


def _number_input(input_id: str, label: str, value: str, low: str, high: str, step: str = "any",
                  aria: str | None = None) -> str:
    return (f'<label><span class="label">{label}</span><input id="{input_id}" type="number" step="{step}" '
            f'min="{low}" max="{high}" value="{value}" aria-label="{escape(aria or label, quote=True)}"></label>')


_REGION_OPTIONS = "".join(
    f'<option value="{escape(key, quote=True)}"{" selected" if key == fama_french.DEFAULT_REGION else ""}>'
    f'{escape(value[0])}</option>' for key, value in fama_french.REGIONS.items()
)
_SCENARIO_OPTIONS = "".join(
    f'<option value="{escape(key, quote=True)}">{escape(value[0])}</option>'
    for key, value in risk_forecasting.STRESS_PRESETS.items()
) + '<option value="custom">Custom shocks</option>'
_FACTOR_INPUTS = "".join(
    _number_input(f"quant-scen-{name.lower().replace('-', '')}", f"{label} (%)", "", "-60", "60", "1")
    .replace('value=""', f'data-quant-factor="{name}"')
    for name, label in (("Mkt-RF", "Market"), ("SMB", "Size"), ("HML", "Value"), ("RMW", "Profitability"),
                        ("CMA", "Investment"), ("MOM", "Momentum"))
)
_REBALANCE_OPTIONS = "".join(
    f'<option value="{key}"{" selected" if key == "minimum_variance" else ""}>{escape(label)}</option>'
    for key, label in (*CLASSIC_OPTIMISERS.items(), *OPTIMISER_LABELS.items())
) + '<option value="custom">My target weights</option>'

QUANT_CARDS = {
    "performance": "".join((
        _card("quant-skill", "Skill and luck", "Skill and luck",
              "How sure we can be that the portfolio's risk-adjusted return is skill rather than luck: ranges from "
              "thousands of reshuffled histories, the chance the true Sharpe ratio is above zero, and how long a "
              "record that takes."),
        _card("quant-decisions", "Decisions", "Decision value added",
              "What the trades added against not making them, 5, 21 and 63 trading days later, how often they were "
              "right, the cost of timing, and the portfolio against simple alternatives since 1 January."),
        _card("quant-currency", "Currency", "Currency effect",
              "How much of the return against the benchmark came from currency moves, month to date and year to "
              "date, beside the choice of markets and holdings in local currency."),
    )),
    "risk": "".join((
        _card("quant-holdings-risk", "Holdings risk", "Ex-ante volatility",
              "How risky today's holdings are from how they have moved together, which holdings carry that risk, "
              "and the few hidden factors behind it."),
        _card("quant-tail", "Tail risk", "EVT VaR and expected shortfall",
              "Losses on very bad days from a model of the extreme tail, beside plain history, and whether past "
              "daily VaR figures held up."),
        _card("quant-regime", "Regimes", "Risk by market regime",
              "How large a bad day has been in each market regime of the portfolio's own history."),
        _card("quant-correlation", "Moving correlations", "Moving correlation (DCC)",
              "How closely the largest holdings move together now against the long run, and on the worst days."),
        _card("quant-concentration", "Concentration", "Herfindahl index and effective number",
              "How concentrated the portfolio is by holding and by sector, and groups of holdings that move as one."),
    )),
    "modelling": "".join((
        _card("quant-optimisers", "Advanced optimisers", "Advanced optimisers",
              "Five more ways to weight the same holdings, beside the Optimisation page's three. A view is "
              "SYMBOL:expected excess return in % a year, or A>B:% for A beating B, with an optional :confidence %.",
              '<div class="filter-grid">'
              + _number_input("quant-opt-cap", "Maximum weight per holding (%)", "25", "1", "100", "1")
              + _number_input("quant-opt-aversion", "Risk aversion", "2.5", "0.5", "20", "0.1")
              + _number_input("quant-opt-cost", "Trading cost (basis points)", "10", "0", "200", "1")
              + _number_input("quant-opt-confidence", "Robust confidence (%)", "95", "50", "99.9", "0.1")
              + '<label><span class="label">Views (optional)</span><input id="quant-opt-views" type="text" '
              'maxlength="200" autocomplete="off" placeholder="e.g. AAPL:8:60, MSFT&gt;KO:3" '
              'aria-label="Black-Litterman views"></label></div>'
              '<div class="actions"><button id="quant-opt-run" class="button" type="button">Run optimisers</button></div>'),
        _card("quant-rebalancing", "Rebalancing", "Rebalancing plan",
              "When trading back toward a target is worth its cost: a no-trade band for each holding, and the trades "
              "that bring holdings outside their band back to its edge. It places no orders.",
              '<div class="filter-grid"><label><span class="label">Rebalance toward</span>'
              f'<select id="quant-rebal-target" aria-label="Rebalance toward">{_REBALANCE_OPTIONS}</select></label>'
              + _number_input("quant-rebal-cost", "Trading cost (basis points)", "10", "0", "200", "1")
              + _number_input("quant-rebal-aversion", "Risk aversion", "2.5", "0.5", "20", "0.1")
              + '<label id="quant-rebal-custom" hidden><span class="label">Target weights (% of NAV)</span>'
              '<input id="quant-rebal-weights" type="text" maxlength="600" autocomplete="off" '
              'placeholder="e.g. AAPL=20, MSFT=15" aria-label="Target weights"></label></div>'
              '<div class="actions"><button id="quant-rebal-run" class="button" type="button">Plan rebalancing</button></div>'),
        _card("quant-goals", "Goals", "Chance of reaching the goal",
              "The chance of reaching a target value by a date with regular saving or withdrawals, and the saving "
              "that would reach it, from the portfolio's own past months and from its market regimes.",
              '<div class="filter-grid">'
              + _number_input("quant-goal-target", 'Target value (<span class="model-ccy"></span>, blank for 1.5 × today)',
                              "", "0", "1e13", aria="Target value").replace(' value=""', "")
              + '<label><span class="label">Years</span><select id="quant-goal-years" aria-label="Years">'
              + "".join(f'<option value="{y}"{" selected" if y == 10 else ""}>{y}</option>' for y in (1, 3, 5, 10, 15, 20, 30))
              + '</select></label>'
              + _number_input("quant-goal-saving", 'Monthly saving (<span class="model-ccy"></span>)', "0", "0", "1e10",
                              aria="Monthly saving")
              + _number_input("quant-goal-withdrawal", 'Monthly withdrawal (<span class="model-ccy"></span>)', "0", "0",
                              "1e10", aria="Monthly withdrawal")
              + _number_input("quant-goal-chance", "Chance wanted (%)", "80", "50", "99", "1")
              + '</div><div class="actions"><button id="quant-goal-run" class="button" type="button">Run goal</button></div>'),
        _card("quant-scenarios", "Scenarios", "Linked factor scenario",
              "A factor stress test in which the factors you do not set move as they usually have, and the most "
              "likely factor moves behind a loss of the size you choose.",
              '<div class="filter-grid"><label><span class="label">Factor region</span>'
              f'<select id="quant-scen-region" aria-label="Factor region">{_REGION_OPTIONS}</select></label>'
              '<label><span class="label">Scenario</span>'
              f'<select id="quant-scen-scenario" aria-label="Scenario">{_SCENARIO_OPTIONS}</select></label>'
              + _number_input("quant-scen-loss", "Loss to test (%)", "20", "1", "60", "1")
              + f'</div><div id="quant-scen-custom" class="filter-grid" hidden>{_FACTOR_INPUTS}</div>'
              '<div class="actions"><button id="quant-scen-run" class="button" type="button">Run scenarios</button></div>'),
        _card("quant-execution", "Trading costs", "Implementation shortfall",
              "What trading cost against a paper trade at the previous close: the move before the trade, the price "
              "paid, commission and anything left unfilled."),
    )),
}

# Phase 3 cards, added after their page's cards when QUANT_ANALYTICS_PHASE3 allows them.
PHASE3_CARDS = {
    "factor_attribution": _card(
        "quant-factor-attribution", "Factor attribution", "Factor attribution",
        "How much of the return over cash came from the market, size, value, profitability, investment and "
        "momentum factors, how much from alpha and how much was left over, for the year to date and the past year."),
    "liquidity": _card(
        "quant-liquidity", "Liquidity", "Days to liquidate",
        "How long the holdings would take to sell without trading more than a fifth of each one's daily volume, "
        "and one-day VaR with the cost of selling added."),
    "macro": _card(
        "quant-macro", "Macro exposures", "Macro exposures",
        "How the portfolio has moved with US rates, the yield curve, the dollar, oil, the VIX and the yuan, and "
        "what standard shocks to them would mean for it."),
    "crowding": _card(
        "quant-crowding", "Crowding", "Fund ownership overlap",
        "How much of the portfolio's US-listed holdings the largest US managers also hold, from their quarterly "
        "13F filings, and which of their funds look most like it."),
}

QUANT_CSS = """
.quant-card .quant-body{margin-top:12px}
.quant-range{color:var(--muted,#667085);font-size:.78em;font-weight:400}
.quant-verdict{margin:12px 0 0;font-weight:600}
.quant-model{margin-top:10px;font-size:12px}
.quant-zone{display:inline-block;padding:1px 8px;border-radius:999px;font-size:.85em;text-transform:capitalize;background:var(--surface-soft,#f2f4f7)}
.quant-zone-green{color:var(--gain,#0f7a4a)}.quant-zone-amber{color:var(--warn-ink,#b45309)}.quant-zone-red{color:var(--loss,#b42318)}
.quant-card input[type=text]{min-width:0;width:100%;max-width:100%;margin-top:7px;padding:10px 13px;color:var(--ink);background:var(--surface);border:1px solid var(--line-strong);font:inherit}
.quant-card .filter-grid label{display:flex;flex-direction:column;justify-content:flex-end}
"""

QUANT_SCRIPT = r"""
const quantState={asOf:null,currency:reportingCurrency};
const quantNum=n=>n!=null&&n!==''&&Number.isFinite(Number(n));
const quantTile=(label,value,hint)=>`<div class="metric"${hint?` title="${esc(hint)}"`:''}><div class="label">${esc(label)}</div><div class="value">${value}</div></div>`;
const quantMoney=n=>quantNum(n)?`${money(n)} ${esc(quantState.currency)}`:'—';
const quantSignedMoney=n=>quantNum(n)?toned(quantMoney(n),n):'—';
const quantLoss=n=>quantNum(n)?pct2(Math.abs(Number(n))):'—';
const quantFixed=(n,d)=>quantNum(n)?minus(Number(n).toFixed(d)):'—';
const quantBp=n=>quantNum(n)?`${quantFixed(n,1)} bp`:'—';
const quantTiles=items=>`<div class="metrics">${items.filter(Boolean).join('')}</div>`;
const quantTable=(head,rows)=>rows.length?`<div class="table-scroll"><table><thead><tr>${head.map((h,i)=>`<th${i?'':' class="text-col"'}>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`:'';
const quantRow=(first,cells)=>`<tr><td class="text-col">${esc(first)}</td>${cells.map(c=>`<td>${c}</td>`).join('')}</tr>`;
const quantSub=text=>`<div class="label model-subhead">${esc(text)}</div>`;
const quantText=parts=>parts.filter(x=>x&&String(x).trim()).join(' ');
const quantFail=async response=>{let detail='';try{const body=await response.json();detail=typeof body.detail==='string'?body.detail:''}catch(error){}return detail||`This could not be loaded (error ${response.status}).`};
const quantGet=async(path,params)=>{const query=new URLSearchParams(Object.entries(params).filter(([,v])=>v!=null&&v!==''));const response=await fetch(`/api/portfolio/${account}/${path}?${query}`);if(!response.ok){const error=new Error(await quantFail(response));error.status=response.status;throw error}return response.json()};
const quantModelLine=p=>p?quantText([`Model: ${p.model_name||p.model_id||'—'}`,p.model_version?`· ${p.model_version}`:'',p.model_status?`· ${p.model_status}`:'',p.as_of_date?`· as of ${analyticsDate(p.as_of_date)}`:'',p.generated_at?`· built ${String(p.generated_at).slice(0,16).replace('T',' ')} UTC`:'',p.quality_status==='warn'?'· built with warnings':'']):'';
const quantShow=(id,html,note,model)=>{const card=document.getElementById(id);if(!card)return;card.querySelector('.quant-body').innerHTML=html||'';card.querySelector('.quant-note').textContent=note||'';card.querySelector('.quant-model').textContent=model||'';card.hidden=false};
const quantPick=(value,horizon)=>{if(!value)return {};if(Array.isArray(value))return value.find(x=>Number(x&&x.horizon)===horizon)||value[0]||{};if(value.hit_rate!==undefined||value.trades!==undefined)return value;return value[String(horizon)]||Object.values(value).find(x=>x&&typeof x==='object')||{}};
const quantRange=(x,fmt)=>x&&quantNum(x.estimate)?`${fmt(x.estimate)} <span class="quant-range">(${fmt(x.low)} to ${fmt(x.high)})</span>`:'—';
const quantRenderSkill=r=>{const s=r.skill_summary||{},c=s.confidence||{},level=quantNum(s.confidence_level)?pct(s.confidence_level):'90%';
  const html=quantTiles([
    quantTile('Sharpe ratio range',quantRange(c.sharpe_ratio,ratio),`Estimate, with the middle ${level} of resampled histories`),
    c.alpha?quantTile('Alpha range',quantRange(c.alpha,pct2),'Alpha a year against the benchmark'):'',
    c.information_ratio?quantTile('Information ratio range',quantRange(c.information_ratio,ratio)):'',
    quantTile('Probabilistic Sharpe ratio',pct2(s.probabilistic_sharpe_ratio),'Chance the true Sharpe ratio is above zero'),
    Number(s.trials)>1?quantTile('Deflated Sharpe ratio',pct2(s.deflated_sharpe_ratio),`Against the best of ${s.trials} strategies tried`):'',
    quantTile('Track record needed',quantNum(s.minimum_track_record_years)?`${quantFixed(s.minimum_track_record_years,1)} years`:quantNum((s.estimates||{}).sharpe_ratio_daily)&&Number(s.estimates.sharpe_ratio_daily)<=0?'Not reachable':'—',quantNum(s.minimum_track_record_years)?'For 95% confidence that the Sharpe ratio is above zero':'No length of record shows a Sharpe ratio of zero or below to be above zero'),
    quantTile('Years of returns',quantNum(s.years)?`${quantFixed(s.years,1)} years`:'—'),
  ])+(s.verdict?`<p class="quant-verdict">${esc(s.verdict)}</p>`:'');
  return [html,quantText([s.window_start?`From ${analyticsDate(s.window_start)} to ${analyticsDate(s.window_end)}, ${s.observations} daily returns.`:'',s.method,s.disclaimer])]};
const quantRenderDecisions=r=>{const tva=r.trade_value_added||{},totals=tva.totals||[],prof=quantPick(r.skill_profile,21),tc=(r.timing_cost||{}).totals||{},cf=r.counterfactual_portfolios||{},sm=cf.summary||{};
  const at=h=>totals.find(t=>Number(t.horizon)===h);const added=h=>{const t=at(h);return t?quantSignedMoney(t.value_added):'—'};const hint=h=>{const t=at(h);return t?`${t.trades} trades, ${t.unavailable||0} too recent`:''};
  const all=tc.all||{},sizing=prof.sizing_skill||{},sells=prof.sell_discipline||{};
  const tiles=quantTiles([
    quantTile('Value added (5 sessions)',added(5),hint(5)),quantTile('Value added (21 sessions)',added(21),hint(21)),quantTile('Value added (63 sessions)',added(63),hint(63)),
    quantTile('Hit rate',pct2(prof.hit_rate),prof.horizon?`${prof.trades} trades over ${prof.horizon} sessions`:''),
    quantTile('Payoff ratio',ratio(prof.payoff_ratio),'Average win over average loss'),
    quantTile('Sizing skill',ratio(sizing.spearman),'Rank correlation of position size with the next return'),
    quantTile('Sell discipline',quantNum(sells.average_advantage)?toned(signedPct(sells.average_advantage),sells.average_advantage):'—','Kept holdings minus sold holdings, after each sale'),
    quantTile('Timing cost',all.trades?`${quantMoney(all.timing_cost)} · ${quantBp(all.timing_cost_bps)}`:'—','Positive is a cost'),
    sm.buy_and_hold?quantTile('Actual minus buy and hold',quantSignedMoney(sm.buy_and_hold.actual_minus_counterfactual)):'',
    sm.benchmark?quantTile('Actual minus benchmark path',quantSignedMoney(sm.benchmark.actual_minus_counterfactual)):'',
  ]);
  const paths=[['Actual',{end_value:cf.actual_end_value}],['Buy and hold',sm.buy_and_hold],['Quarterly rebalance',sm.quarterly_rebalance],['Benchmark',sm.benchmark]].filter(([,v])=>v&&quantNum(v.end_value));
  const cfTable=paths.length?quantSub(`Since ${analyticsDate(cf.start_date)}: the portfolio against simple alternatives`)+quantTable(['Path','End value','Actual minus this path'],paths.map(([label,v])=>quantRow(label,[quantMoney(v.end_value),label==='Actual'?'—':quantSignedMoney(v.actual_minus_counterfactual)]))):'';
  const holdingAdded=x=>{const h=(x.horizons||[]).find(y=>Number(y.horizon)===21);return h?Number(h.value_added)||0:0};
  const byHolding=(tva.by_holding||[]).slice().sort((a,b)=>Math.abs(holdingAdded(b))-Math.abs(holdingAdded(a))).slice(0,5);
  const holdTable=byHolding.length?quantSub('Largest value added by holding (21 sessions)')+quantTable(['Holding','Trades','Value added'],byHolding.map(x=>quantRow(x.symbol||x.conid,[esc(x.trades),quantSignedMoney(holdingAdded(x))]))):'';
  return [tiles+cfTable+holdTable,quantText([tva.method,prof.method,(r.timing_cost||{}).method,cf.method,tva.disclaimer])]};
const quantRenderCurrency=r=>{const ca=r.currency_attribution||r,mtd=ca.mtd||{},ytd=ca.ytd||{};
  const effect=x=>x&&x.totals&&quantNum(x.totals.currency_allocation)?Number(x.totals.currency_allocation)+(Number(x.totals.currency_compounding)||0):null;
  const signed=n=>quantNum(n)?toned(signedPct(n),n):'—';const t=ytd.totals||{};
  const tiles=quantTiles([
    quantTile('Currency effect (month to date)',signed(effect(mtd)),'Currency allocation plus currency compounding'),
    quantTile('Currency effect (year to date)',signed(effect(ytd)),'Currency allocation plus currency compounding'),
    quantTile('Excess return (year to date)',signed(ytd.excess_return),'Portfolio minus benchmark, in the reporting currency'),
    quantTile('Local market effect (year to date)',signed(['allocation','selection','interaction'].every(k=>quantNum(t[k]))?Number(t.allocation)+Number(t.selection)+Number(t.interaction):null),'How the holdings did in their own currencies against the benchmark'),
  ]);
  const rows=(ytd.currencies||[]).map(x=>quantRow(x.currency,[weightPct(x.portfolio_weight),weightPct(x.benchmark_weight),signedPct(x.currency_return),signed(x.currency_allocation)]));
  return [tiles+(rows.length?quantSub('By currency, year to date'):'')+quantTable(['Currency','Portfolio weight','Benchmark weight','Currency move','Currency allocation'],rows),quantText([ytd.method||mtd.method,...(ytd.notes||[])])]};
const quantRenderHoldingsRisk=r=>{const lw=r.ledoit_wolf_covariance||{},ex=r.ex_ante_risk||{},pc=r.statistical_factors||{};
  const holdings=(ex.holdings||[]).slice().sort((a,b)=>(Number(b.contribution)||0)-(Number(a.contribution)||0));const top=holdings[0];const first=(pc.factors||[])[0];
  const tiles=quantTiles([
    quantTile('Ex-ante volatility',pct2(ex.volatility),'A year, at today\'s weights'),
    quantNum(ex.tracking_error)?quantTile('Ex-ante tracking error',pct2(ex.tracking_error)):'',
    quantTile('Covariance shrinkage',pct(lw.shrinkage_intensity),'Weight on the simple target'),
    top?quantTile('Largest risk contributor',`${esc(top.name)} · ${pct(top.share)}`):'',
    first?quantTile('First statistical factor',`${pct(first.explained_share)} of variance`):'',
  ]);
  const names=pc.names||[];const loadings=f=>{const pairs=Array.isArray(f.loadings)?f.loadings.map((v,i)=>[names[i],v]):Object.entries(f.loadings||{});return pairs.filter(([,v])=>quantNum(v)).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,3).map(([n,v])=>`${esc(n)} ${ratio(v)}`).join(', ')};
  const contributors=quantTable(['Holding','Weight','Contribution to volatility','Share of risk'],holdings.slice(0,5).map(x=>quantRow(x.name,[weightPct(x.weight),pct2(x.contribution),pct(x.share)])));
  const factors=quantTable(['Factor','Share of variance','Cumulative','Largest loadings'],(pc.factors||[]).map(f=>quantRow(f.factor,[pct(f.explained_share),pct(f.cumulative_share),loadings(f)])));
  return [tiles+(contributors?quantSub('Largest contributors to risk')+contributors:'')+(factors?quantSub('Statistical factors')+factors:''),quantText([ex.method,lw.method,pc.method,ex.disclaimer])]};
const quantZone=z=>z?`<span class="quant-zone quant-zone-${esc(z)}">${esc(z)}</span>`:'—';
const quantRenderTail=r=>{const evt=r.evt_var_es||{},bt=r.tail_backtest_report||{},u=(evt.unconditional||{}).levels||{},c=(evt.conditional||{}).levels||{},a=u['99']||{},b=u['99.5']||{},basel=bt.basel||null;
  const tiles=quantTiles([
    quantTile('EVT VaR (99%)',quantLoss(a.var),'One-day loss exceeded on about 1 day in 100'),
    quantTile('EVT expected shortfall (99%)',quantLoss(a.expected_shortfall),'Average loss on those days'),
    quantTile('Historical VaR (99%)',quantLoss(a.historical_var)),
    quantTile('Historical expected shortfall (99%)',quantLoss(a.historical_expected_shortfall)),
    quantTile('EVT VaR (99.5%)',quantLoss(b.var)),quantTile('EVT expected shortfall (99.5%)',quantLoss(b.expected_shortfall)),
    c['99']?quantTile('VaR for tomorrow (99%, volatility-adjusted)',quantLoss(c['99'].var),'Scaled to tomorrow\'s GARCH volatility'):'',
    basel?quantTile('Traffic light (99%, last 250 days)',`${quantZone(basel.zone)} · ${esc(basel.breaches)} breaches`,`Green up to ${basel.green_up_to}, red from ${basel.red_from}`):'',
  ]);
  const levels=Object.entries(bt.levels||{});
  const rows=levels.map(([key,x])=>{const ch=x.christoffersen||{},ind=ch.independence||{},es=x.expected_shortfall||{};return quantRow(`${key}%`,[`${esc(x.breaches)} (expected ${quantFixed(x.expected_breaches,1)})`,quantFixed(x.kupiec_p_value,3),ch.clustered==null?'—':`${ch.clustered?'Yes':'No'}${quantNum(ind.p_value)?` (p ${quantFixed(ind.p_value,3)})`:''}`,quantZone((x.traffic_light||{}).zone),es.zone?`${quantZone(es.zone)} · Z2 ${quantFixed(es.z2,2)}`:'—'])});
  const table=rows.length?quantSub(`Backtest of daily VaR, ${analyticsDate(bt.window_start)} to ${analyticsDate(bt.window_end)}`)+quantTable(['VaR level','Breaches','Kupiec p-value','Breaches bunched','Traffic light','Expected shortfall test'],rows):'';
  return [tiles+table,quantText([evt.method,bt.method,evt.disclaimer])]};
const quantRenderRegime=r=>{const rr=r.regime_risk||{},regimes=rr.regimes||[];const level=(x,k)=>((x.levels||{})[k])||{};
  const stressed=regimes.find(x=>x.regime==='Stressed')||regimes.slice().sort((a,b)=>(Number(b.annual_volatility)||0)-(Number(a.annual_volatility)||0))[0];
  const today=Object.entries((r.regime_model||{}).today||{}).filter(([,v])=>quantNum(v)).sort((a,b)=>b[1]-a[1])[0];
  const tiles=quantTiles([today?quantTile('Most likely regime today',esc(today[0]),`${pct2(today[1])} likely`):'',quantTile('Regimes found',esc(regimes.length)),stressed?quantTile('Stressed regime expected shortfall (99%)',quantLoss(level(stressed,'99').expected_shortfall),stressed.regime):'']);
  const rows=regimes.map(x=>quantRow(x.regime,[weightPct(x.share_of_days),pct2(x.annual_volatility),quantLoss(level(x,'95').var),quantLoss(level(x,'95').expected_shortfall),quantLoss(level(x,'99').var),quantLoss(level(x,'99').expected_shortfall),quantFixed(x.effective_days,0)]));
  const all=rr.all_days;if(all)rows.push(quantRow('All days',['100%',quantNum(all.daily_volatility)?pct2(Number(all.daily_volatility)*Math.sqrt(252)):'—',quantLoss(level(all,'95').var),quantLoss(level(all,'95').expected_shortfall),quantLoss(level(all,'99').var),quantLoss(level(all,'99').expected_shortfall),quantFixed(rr.observations,0)]));
  return [tiles+quantTable(['Regime','Share of days','Volatility (a year)','VaR 95%','Expected shortfall 95%','VaR 99%','Expected shortfall 99%','Effective days'],rows),quantText([rr.method,rr.disclaimer])]};
const quantRenderCorrelation=r=>{const d=r.dcc_correlation||{},avg=d.average_correlation||{},names=d.names||[],now=d.latest_correlation||[],long=d.long_run_correlation||d.unconditional_correlation||null;
  const tiles=quantTiles([
    quantTile('Average correlation now',ratio(avg.latest),'Weighted by each pair\'s product of weights'),
    quantTile('Average correlation, long run',ratio(avg.unconditional)),
    quantTile('Average correlation on stress days',ratio(avg.stress_days),'The 5% of days today\'s weights would have lost most'),
    quantTile('Average correlation on calm days',ratio(avg.calm_days)),
    quantTile('Correlation half-life',quantNum(d.half_life_days)?`${quantFixed(d.half_life_days,1)} days`:'—','Days for a shift in correlation to fade by half'),
  ]);
  const pairs=[];for(let i=0;i<names.length;i++)for(let j=i+1;j<names.length;j++){const v=(now[i]||[])[j];if(quantNum(v))pairs.push([i,j,Number(v)])}
  pairs.sort((a,b)=>b[2]-a[2]);
  const head=long?['Pair','Now','Long run']:['Pair','Now'];
  const rows=pairs.slice(0,5).map(([i,j,v])=>quantRow(`${names[i]} and ${names[j]}`,long?[ratio(v),ratio((long[i]||[])[j])]:[ratio(v)]));
  return [tiles+(rows.length?quantSub('Most correlated pairs now'):'')+quantTable(head,rows),quantText([d.not_modelled&&d.not_modelled.length?`Not modelled: ${d.not_modelled.join(', ')}.`:'',d.method,d.disclaimer])]};
const quantRenderConcentration=r=>{const conc=r.concentration||{};const byHolding=conc.by_holding||r.concentration_by_holding||(conc.herfindahl!=null?conc:{});const bySector=conc.by_sector||r.concentration_by_sector||r.sector_concentration||null;const cl=r.correlation_clusters||{};
  const tiles=quantTiles([
    quantTile('Effective number (by holding)',quantFixed(byHolding.effective_holdings,1),quantNum(byHolding.holdings)?`${byHolding.holdings} holdings`:''),
    quantTile('Herfindahl index (by holding)',quantFixed(byHolding.herfindahl,3)),
    quantTile('Top 5 share of gross',pct(((byHolding.top_shares||{})['5']))),
    bySector?quantTile('Effective number (by sector)',quantFixed(bySector.effective_holdings,1),bySector.largest?`Largest: ${bySector.largest}`:''):'',
    quantNum(cl.multi_holding_clusters)?quantTile('Clusters of 2 or more',esc(cl.multi_holding_clusters),`Holdings correlated above ${cl.threshold}`):'',
  ]);
  const rows=(cl.clusters||[]).filter(x=>Number(x.size)>1).map(x=>quantRow((x.members||[]).join(', '),[esc(x.size),ratio(x.average_correlation),weightPct(x.combined_weight)]));
  return [tiles+(rows.length?quantSub('Holdings that move as one'):'')+quantTable(['Holdings','Count','Average correlation','Combined weight'],rows),quantText([byHolding.method,cl.method])]};
const quantRenderExecution=(r,p)=>{const is=r.implementation_shortfall;const entries=Array.isArray(is)?is:is&&Array.isArray(is.trades)?is.trades:[];
  const trades=entries.map(x=>x&&x.implementation_shortfall&&typeof x.implementation_shortfall==='object'?{...x.implementation_shortfall,trade_date:x.trade_date,symbol:x.symbol,side:x.side}:x);
  const sum=f=>trades.reduce((s,x)=>s+(Number(f(x))||0),0);const base=sum(x=>x.paper_value);const bp=n=>base?quantBp(n/base*1e4):'—';
  const comp=k=>sum(x=>((x.components||{})[k]||{}).amount),total=sum(x=>(x.total||{}).amount);
  const tiles=quantTiles([
    quantTile('Orders analysed',esc(trades.length),'Fills of one holding on one side on one day count as one order'),
    quantTile('Implementation shortfall',trades.length?`${quantMoney(total)} · ${bp(total)}`:'—','Positive is a cost'),
    quantTile('Delay cost',trades.length?`${quantMoney(comp('delay'))} · ${bp(comp('delay'))}`:'—'),
    quantTile('Trading cost (fills against arrival)',trades.length?`${quantMoney(comp('trading'))} · ${bp(comp('trading'))}`:'—'),
    quantTile('Opportunity cost (unfilled)',trades.length?`${quantMoney(comp('opportunity'))} · ${bp(comp('opportunity'))}`:'—'),
  ]);
  let months=r.by_month||r.monthly||(is&&!Array.isArray(is)&&is.by_month)||null;
  if(!Array.isArray(months)){const grouped={};trades.forEach(x=>{const m=String(x.month||x.trade_date||x.date||'').slice(0,7);if(!m)return;const g=grouped[m]=grouped[m]||{month:m,trades:0,amount:0,paper:0};g.trades+=1;g.amount+=Number((x.total||{}).amount)||0;g.paper+=Number(x.paper_value)||0});months=Object.values(grouped).sort((a,b)=>a.month<b.month?1:-1)}
  const rows=months.slice().sort((a,b)=>a.month<b.month?1:-1).map(m=>{const amount=quantNum(m.amount)?m.amount:quantNum(m.total)?m.total:(m.total||{}).amount,paper=m.paper||m.paper_value,bps=[m.bps,m.total_bps,(m.total||{}).bps].find(quantNum);return quantRow(analyticsMonth(m.month),[esc(m.trades??m.orders??'—'),quantMoney(amount),quantNum(bps)?quantBp(bps):paper?quantBp(amount/paper*1e4):'—'])});
  const first=trades[0]||(is&&!Array.isArray(is)?is:{})||{},inputs=(p||{}).inputs||{},skipped=Array.isArray(r.skipped)?r.skipped.length:0,older=Number(inputs.orders_left_out)||0;
  return [tiles+(rows.length?quantSub('By month'):'')+quantTable(['Month','Orders','Shortfall','Basis points'],rows),quantText([skipped?`${skipped} ${skipped===1?'order':'orders'} could not be measured.`:'',older>0?`The ${older} oldest ${older===1?'order is':'orders are'} left out.`:'',inputs.prices?`Prices: ${inputs.prices}.`:'',first.method])]};
const quantSections=[['skill','quant-skill',quantRenderSkill],['decisions','quant-decisions',quantRenderDecisions],['currency','quant-currency',quantRenderCurrency],['holdings_risk','quant-holdings-risk',quantRenderHoldingsRisk],['tail','quant-tail',quantRenderTail],['regime','quant-regime',quantRenderRegime],['correlation','quant-correlation',quantRenderCorrelation],['concentration','quant-concentration',quantRenderConcentration],['execution','quant-execution',quantRenderExecution]];
const quantLoadSection=async(section,id,render,asOf)=>{let p;
  try{p=await quantGet(`quant/${section}`,{as_of:asOf})}catch(error){quantShow(id,'',error.status===404?'Not built yet: the overnight analytics have not produced these figures for this account on or before the As of date.':error.message,'');return}
  quantState.currency=reportingCurrency;
  if(p.available===false){quantShow(id,'',`Not available: ${p.reason||'the overnight build could not work these figures out.'}`,quantModelLine(p));return}
  let html='',note='';try{[html,note]=render(p.results||{},p)}catch(error){html='';note='These figures could not be displayed.'}
  const missing=[...new Set(Object.values(p.results||{}).filter(x=>x&&typeof x==='object'&&!Array.isArray(x)&&x.available===false&&x.reason).map(x=>String(x.reason)))];
  if(missing.length)note=quantText([note,...missing.map(x=>`Not worked out: ${x}`)]);
  quantShow(id,html,note,quantModelLine(p))};
const quantValue=selector=>{const el=document.querySelector(selector);return el?String(el.value||'').trim():''};
const quantPortfolioRows=[['current','Today'],['black_litterman','Black-Litterman'],['hierarchical_risk_parity','Hierarchical risk parity'],['minimum_tail_loss','Minimum tail loss'],['mean_variance_costs','Mean-variance after costs'],['robust_mean_variance','Robust mean-variance']];
const runQuantOptimisers=async()=>{const id='quant-optimisers';let r;
  try{r=await quantGet('modelling/optimisers',{as_of:quantState.asOf,max_weight:quantValue('#quant-opt-cap'),risk_aversion:quantValue('#quant-opt-aversion'),cost_bps:quantValue('#quant-opt-cost'),confidence:quantValue('#quant-opt-confidence'),views:quantValue('#quant-opt-views')})}catch(error){quantShow(id,'',error.message,'');return}
  quantState.currency=r.currency||reportingCurrency;const ps=r.portfolios||{};
  const rows=quantPortfolioRows.filter(([k])=>ps[k]).map(([k,label])=>{const p=ps[k];return p.available===false?`<tr><td class="text-col">${esc(label)}</td><td colspan="5">${esc(p.reason||'Not available')}</td></tr>`:quantRow(label,[pct2(p.expected_return),pct2(p.volatility),ratio(p.sharpe_ratio),quantLoss(p.tail_loss_95),k==='current'?'—':weightPct(p.turnover)])});
  const shown=quantPortfolioRows.filter(([k])=>k==='current'||(ps[k]&&ps[k].available!==false));
  const holdings=(r.holdings||[]).map(h=>quantRow(h.symbol,shown.map(([k])=>weightPct(h[`${k}_weight`]))));
  const html=quantTable(['Portfolio','Expected return','Volatility','Sharpe ratio','Tail loss (worst 5% of days)','Turnover from today'],rows)+quantSub('Weights (share of the invested sleeve)')+quantTable(['Holding',...shown.map(([,label])=>label)],holdings);
  quantShow(id,html,quantText([`From ${analyticsDate(r.window_start)} to ${analyticsDate(r.window_end)}.`,r.risk_free_available?'':'No risk-free rate was available, so 0% is used.',r.excluded&&r.excluded.length?`Left out (not enough price history or beyond the largest 20): ${r.excluded.join(', ')}.`:'',r.method,...Object.values(r.methods||{}),r.disclaimer]),quantModelLine(r))};
const runQuantRebalancing=async()=>{const id='quant-rebalancing',choice=quantValue('#quant-rebal-target');const params={as_of:quantState.asOf,cost_bps:quantValue('#quant-rebal-cost'),risk_aversion:quantValue('#quant-rebal-aversion')};
  if(choice==='custom'){const text=quantValue('#quant-rebal-weights');if(!text){quantShow(id,'','Enter target weights such as AAPL=20, MSFT=15 (percent of NAV).','');return}
    const parts=text.split(',').map(x=>x.trim()).filter(Boolean),converted=[];for(const part of parts){const [name,raw]=part.split('=');const n=Number(raw);if(!name||!Number.isFinite(n)){quantShow(id,'',`${part} is not written as SYMBOL=percent.`,'');return}converted.push(`${name.trim()}=${n/100}`)}params.target=converted.join(',')}else params.optimiser=choice;
  let r;try{r=await quantGet('modelling/rebalancing',params)}catch(error){quantShow(id,'',error.message,'');return}
  quantState.currency=r.currency||reportingCurrency;const drift=r.remaining_drift||{};
  const tiles=quantTiles([quantTile('Value to trade',quantMoney(r.traded_value)),quantTile('Expected trading cost',quantMoney(r.expected_cost)),quantTile('Saved against a full rebalance',quantMoney(r.cost_saved)),quantTile('Largest drift left',weightPct(drift.largest),'Weight after the trades minus the target')]);
  const bands=Object.fromEntries(((r.bands||{}).holdings||[]).map(b=>[b.holding,b]));
  const holdings=quantTable(['Holding','Now','Target','No-trade band','After','Action'],(r.holdings||[]).map(h=>quantRow(h.symbol||h.holding,[weightPct(h.current_weight),weightPct(h.target_weight),bands[h.holding]?`± ${weightPct(bands[h.holding].half_width)}`:'—',weightPct(h.weight_after),esc(h.action)])));
  const trades=quantTable(['Holding','Side','Value','Shares','Price','Cost'],(r.trades||[]).map(t=>quantRow(t.symbol||t.holding,[esc(t.side),quantMoney(Math.abs(t.value)),quantFixed(Math.abs(t.shares),2),quantMoney(t.price),quantMoney(t.cost)])));
  quantShow(id,tiles+quantSub(`Toward: ${r.target_source}`)+holdings+(trades?quantSub('Trades'):'')+trades,quantText([...(r.warnings||[]),r.left_out&&r.left_out.length?`Not rebalanced (no year of daily prices or beyond the largest 20): ${r.left_out.join(', ')}.`:'',(r.bands||{}).method,r.method,r.disclaimer]),quantModelLine(r))};
const runQuantGoals=async()=>{const id='quant-goals';let r;
  try{r=await quantGet('modelling/goals',{as_of:quantState.asOf,target:quantValue('#quant-goal-target'),years:quantValue('#quant-goal-years'),monthly_contribution:quantValue('#quant-goal-saving'),monthly_withdrawal:quantValue('#quant-goal-withdrawal'),probability:quantValue('#quant-goal-chance')})}catch(error){quantShow(id,'',error.message,'');return}
  quantState.currency=r.currency||reportingCurrency;const h=(r.models||{}).history||{},g=(r.models||{}).regimes||{};const hp=h.projection||{},gp=g.projection||{},hr=h.required||{},gr=g.required||{};
  const saving=x=>!x||!quantNum(x.monthly_contribution)?'—':x.already_on_track?'None: on track':quantMoney(x.monthly_contribution);
  const tiles=quantTiles([
    h.available?quantTile('Chance of reaching the goal',pct(hp.probability_target),`${hp.paths} paths from past months`):'',
    g.available?quantTile('Chance of reaching the goal (regimes)',pct(gp.probability_target),'Following calm and stressed spells'):'',
    h.available?quantTile('Monthly saving needed',saving(hr),`For a ${pct(r.probability)} chance`):'',
    g.available?quantTile('Monthly saving needed (regimes)',saving(gr)):'',
    h.available?quantTile('Median end value',quantMoney((hp.terminal||{}).p50)):'',
    h.available&&Number(r.monthly_withdrawal)>0?quantTile('Chance of running out first',pct(hp.probability_depleted)):'',
  ]);
  const row=(label,m)=>m.available?quantRow(label,[pct(m.projection.probability_target),pct(m.projection.probability_depleted),quantMoney((m.projection.terminal||{}).p5),quantMoney((m.projection.terminal||{}).p50),quantMoney((m.projection.terminal||{}).p95),saving(m.required)]):`<tr><td class="text-col">${esc(label)}</td><td colspan="6">${esc(m.reason||'Not available')}</td></tr>`;
  const table=quantTable(['Model','Chance of reaching','Chance of running out','Weak case (5th)','Middle','Strong case (95th)','Monthly saving needed'],[row('Past months',h),row('Market regimes',g)]);
  quantShow(id,tiles+quantSub(`Target ${quantMoney(r.target)}${r.target_source==='default'?' (1.5 × today\'s value)':''} in ${r.years} ${r.years===1?'year':'years'}, from ${quantMoney(r.start_value)}`)+table,quantText([...(hp.warnings||[]),hp.method,g.available?gp.method:'',r.disclaimer]),quantModelLine(r))};
const runQuantScenarios=async()=>{const id='quant-scenarios',choice=quantValue('#quant-scen-scenario');const params={as_of:quantState.asOf,region:quantValue('#quant-scen-region'),loss:quantValue('#quant-scen-loss')};
  if(choice==='custom'){params.scenario='custom';params.shocks=[...document.querySelectorAll('#quant-scen-custom input[data-quant-factor]')].filter(i=>i.value!=='').map(i=>`${i.dataset.quantFactor}:${Number(i.value)/100}`).join(',')}else params.scenario=choice;
  let r;try{r=await quantGet('modelling/scenarios',params)}catch(error){quantShow(id,'',error.message,'');return}
  quantState.currency=r.currency||reportingCurrency;const im=r.impact||{},nm=r.named_only||{},rv=r.reverse||{};
  const value=n=>quantNum(n)?` · ${quantSignedMoney(n)}`:'';
  const tiles=quantTiles([
    quantTile('Linked scenario impact',`${toned(signedPct(im.portfolio_return),im.portfolio_return)}${value(r.linked_impact_value)}`,r.scenario_label),
    quantTile('Named shocks only',`${toned(signedPct(nm.impact),nm.impact)}${value(r.named_impact_value)}`,'The Stress test page\'s figure, other factors held still'),
    quantTile('Loss tested',quantLoss(rv.loss)),
    quantTile('Chance of a loss this large',pct2(rv.probability_of_loss_at_least),'In a month, if factor moves were normal'),
    quantTile('How unusual (Mahalanobis distance)',ratio(rv.mahalanobis_distance)),
  ]);
  const contrib=Object.fromEntries((im.factors||[]).map(f=>[f.factor,f]));
  const linked=quantTable(['Factor','Move','Set or inferred','Move in standard deviations','Effect on the portfolio'],((r.linked||{}).factors||[]).map(f=>quantRow(f.factor,[signedPct(f.move),esc(f.source),quantFixed(f.move_in_sd,2),contrib[f.factor]?signedPct(contrib[f.factor].contribution):'—'])));
  const reverse=quantTable(['Factor','Move','Move in standard deviations','Beta','Share of the loss'],(rv.factors||[]).map(f=>quantRow(f.factor,[signedPct(f.move),quantFixed(f.move_in_sd,2),ratio(f.beta),pct(f.share_of_loss)])));
  quantShow(id,tiles+quantSub(`Linked scenario: ${r.scenario_label}`)+linked+quantSub(`Most likely moves behind a ${quantLoss(rv.loss)} loss`)+reverse,quantText([`${r.region}, weeks ${analyticsDate(r.window_start)} to ${analyticsDate(r.window_end)}.`,r.factors_not_in_model&&r.factors_not_in_model.length?`Not in the fitted model: ${r.factors_not_in_model.join(', ')}.`:'',r.method,(r.reverse||{}).method,r.disclaimer]),quantModelLine(r))};
const quantOn=(selector,event,handler)=>{const el=document.querySelector(selector);if(el)el.addEventListener(event,handler)};
quantOn('#quant-opt-run','click',()=>void runQuantOptimisers());
quantOn('#quant-rebal-run','click',()=>void runQuantRebalancing());
quantOn('#quant-rebal-target','change',()=>{const box=document.querySelector('#quant-rebal-custom');if(box)box.hidden=quantValue('#quant-rebal-target')!=='custom'});
quantOn('#quant-goal-run','click',()=>void runQuantGoals());
quantOn('#quant-scen-run','click',()=>void runQuantScenarios());
quantOn('#quant-scen-scenario','change',()=>{const box=document.querySelector('#quant-scen-custom');if(box)box.hidden=quantValue('#quant-scen-scenario')!=='custom'});
const renderQuant=async asOf=>{quantState.asOf=asOf;const view=document.querySelector('.page-view.active');if(!view)return;
  const jobs=quantSections.filter(([,id])=>view.querySelector('#'+id)).map(([section,id,render])=>quantLoadSection(section,id,render,asOf));
  [['#quant-optimisers',runQuantOptimisers],['#quant-rebalancing',runQuantRebalancing],['#quant-goals',runQuantGoals],['#quant-scenarios',runQuantScenarios]].forEach(([selector,run])=>{if(view.querySelector(selector))jobs.push(run())});
  await Promise.allSettled(jobs)};
"""

# Phase 3 renderers, appended to QUANT_SCRIPT only when a phase 3 section is allowed for the
# viewer; each adds its section to quantSections, so renderQuant loads it with the others.
PHASE3_SCRIPT = r"""
const quantCount=n=>quantNum(n)?minus(new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(Number(n))):'—';
const quantDays=n=>quantNum(n)?`${quantFixed(n,Number(n)<10?1:0)} ${Number(n)===1?'day':'days'}`:'—';
const quantSigned=n=>quantNum(n)?toned(signedPct(n),n):'—';
const quantFactorNames={'Mkt-RF':'Market','SMB':'Size','HML':'Value','RMW':'Profitability','CMA':'Investment','MOM':'Momentum'};
const quantMacroNames={'DGS10':'US 10-year yield','DGS10-DGS2':'Yield curve (10-year minus 2-year)','DTWEXBGS':'Broad dollar index','DCOILBRENTEU':'Brent oil','VIXCLS':'VIX','USDCNH':'USD/CNH'};
const quantRenderFactorAttribution=(r,p)=>{const y=r.past_year||{},t=r.ytd||{},inputs=(p||{}).inputs||{};
  const ok=x=>x&&x.available!==false&&quantNum(x.cumulative_excess_return);const alpha=x=>(x.alpha||{}),rest=x=>(x.residual||{});
  const tiles=quantTiles([
    ok(y)?quantTile('Return over cash (past year)',quantSigned(y.cumulative_excess_return),`${y.observations} daily returns from ${analyticsDate(y.window_start)}`):'',
    ok(y)?quantTile('Explained by factors (past year)',quantSigned(y.factors_cumulative),'The factor parts added up'):'',
    ok(y)?quantTile('Alpha (past year)',quantSigned(alpha(y).cumulative_contribution),quantNum(alpha(y).t_statistic)?`t-statistic ${quantFixed(alpha(y).t_statistic,2)}`:''):'',
    ok(y)?quantTile('Residual (past year)',quantSigned(rest(y).cumulative_contribution),'Left over after the factors and alpha'):'',
    ok(y)?quantTile('Factor fit R² (past year)',pct(y.r_squared)):'',
    ok(t)?quantTile('Return over cash (year to date)',quantSigned(t.cumulative_excess_return),`${t.observations} daily returns from ${analyticsDate(t.window_start)}`):'',
    ok(t)?quantTile('Explained by factors (year to date)',quantSigned(t.factors_cumulative)):'',
    ok(t)?quantTile('Alpha (year to date)',quantSigned(alpha(t).cumulative_contribution)):'',
  ]);
  const main=ok(y)?y:t,label=ok(y)?'past year':'year to date',other=Object.fromEntries((ok(y)&&ok(t)?t.exposures||[]:[]).map(x=>[x.factor,x]));
  const head=['Factor',`Beta (${label})`,'t-statistic',`Contribution (${label})`].concat(ok(y)&&ok(t)?['Contribution (year to date)']:[]);
  const rows=(main.exposures||[]).map(x=>quantRow(quantFactorNames[x.factor]||x.factor,[ratio(x.beta),quantFixed(x.t_statistic,2),quantSigned(x.cumulative_contribution)].concat(ok(y)&&ok(t)?[other[x.factor]?quantSigned(other[x.factor].cumulative_contribution):'—']:[])));
  const sources=Object.keys(inputs.factor_sources||{}).map(s=>s==='etf-proxy'?'fund stand-ins':'the Kenneth French data library').join(' and ');
  return [tiles+(rows.length?quantSub('By factor'):'')+quantTable(head,rows),quantText([inputs.region_label?`${inputs.region_label} factors${sources?` from ${sources}`:''}, through ${analyticsDate(inputs.factor_data_through)}; daily portfolio returns less the daily risk-free rate.`:'',main.method,main.disclaimer])]};
const quantRenderLiquidity=(r,p)=>{const d=r.days_to_liquidate||{},lv=r.liquidity_adjusted_var||{},inputs=(p||{}).inputs||{},s=d.sellable_within||{};const lvar=lv.available!==false&&quantNum(lv.liquidity_adjusted_var),dl=d.available!==false&&quantNum(s['1']);
  const total=Number(inputs.holdings)||0,without=(inputs.not_covered||[]).length;
  const tiles=quantTiles([
    dl?quantTile('Sellable in 1 day',pct(s['1']),quantNum(d.participation)?`Share of the holdings' value, trading at most ${pct(d.participation)} of daily volume`:''):'',
    dl?quantTile('Sellable in 5 days',pct(s['5'])):'',
    dl?quantTile('Sellable in 20 days',pct(s['20'])):'',
    dl&&quantNum(d.value_weighted_days)?quantTile('Value-weighted days to sell',quantDays(d.value_weighted_days),'Each holding weighted by its value'):'',
    dl?(quantNum(d.days_to_sell_everything)?quantTile('Days to sell everything',quantDays(d.days_to_sell_everything),'The slowest holding'):quantTile('Value without volume data',pct(d.share_without_volume_data),'Counted as not sellable')):'',
    total?quantTile('Holdings with volume data',`${quantCount(total-without)} of ${quantCount(total)}`,'Holdings with daily volumes in silver over the last 45 days'):'',
    lvar?quantTile('Liquidity-adjusted VaR (99%)',quantLoss(lv.liquidity_adjusted_var),'One day: VaR plus the cost of selling through the bid-ask spread'):'',
    lvar?quantTile('VaR before liquidity (99%)',quantLoss(lv.var),inputs.var_basis?`The tail risk card's ${inputs.var_basis} one-day VaR`:''):'',
    lvar?quantTile('Spread cost added',pct2(lv.liquidity_cost),'Zero until bid-ask spreads are stored'):'',
  ]);
  const rows=(d.holdings||[]).slice(0,10).map(x=>quantRow(x.key,[quantCount(x.quantity),quantCount(x.average_daily_volume),quantDays(x.days),weightPct(x.share_of_portfolio)]));
  const missing=d.no_volume_data||[];
  return [tiles+(rows.length?quantSub('Slowest holdings to sell'):'')+quantTable(['Holding','Quantity','Average daily volume','Days to sell','Share of holdings'],rows),quantText([missing.length?`No volume data: ${missing.join(', ')}.`:'',inputs.holdings_date?`Holdings on ${analyticsDate(inputs.holdings_date)}; average daily volume over the last ${inputs.volume_window_sessions||20} sessions with volume.`:'',d.method,lvar?lv.method:'',lvar?inputs.spreads:'',d.disclaimer])]};
const quantShockNames={'rates_up_100bp':'US 10-year yield +100bp','curve_steeper_50bp':'Yield curve 50bp steeper','dollar_up_5pct':'Dollar index +5%','oil_down_20pct':'Brent oil −20%','volatility_up_50pct':'VIX +50%','yuan_down_5pct':'Yuan 5% weaker','risk_off':'Risk-off week'};
const quantRiskOffParts={'DGS10':'yield −50bp','DTWEXBGS':'dollar +5%','DCOILBRENTEU':'Brent −20%','VIXCLS':'VIX +50%','USDCNH':'USD/CNH +3%'};
const quantRenderMacro=(r,p)=>{const m=r.macro_exposures||{},sens=r.macro_sensitivity||{},inputs=(p||{}).inputs||{};
  const has=k=>sens[k]&&quantNum(sens[k].impact);const shock=k=>quantSigned(sens[k].impact);
  const range=k=>Array.isArray(sens[k].range_95)?`95% range ${signedPct(sens[k].range_95[0])} to ${signedPct(sens[k].range_95[1])}`:'';
  const offOut=new Set((sens.risk_off||{}).factors_not_in_model||[]),offIn=Object.keys(quantRiskOffParts).filter(k=>!offOut.has(k)).map(k=>quantRiskOffParts[k]).join(', ');
  const offHint=offIn?`${offIn.charAt(0).toUpperCase()}${offIn.slice(1)} together${offOut.size?`; ${[...offOut].map(k=>quantMacroNames[k]||k).join(', ')} left out`:''}`:'';
  const tiles=quantTiles([
    has('rates_up_100bp')?quantTile('US 10-year yield +100bp',shock('rates_up_100bp'),range('rates_up_100bp')):'',
    has('curve_steeper_50bp')?quantTile('Yield curve 50bp steeper',shock('curve_steeper_50bp'),range('curve_steeper_50bp')):'',
    has('dollar_up_5pct')?quantTile('Dollar index +5%',shock('dollar_up_5pct'),range('dollar_up_5pct')):'',
    has('oil_down_20pct')?quantTile('Brent oil −20%',shock('oil_down_20pct'),range('oil_down_20pct')):'',
    has('volatility_up_50pct')?quantTile('VIX +50%',shock('volatility_up_50pct'),range('volatility_up_50pct')):'',
    has('yuan_down_5pct')?quantTile('Yuan 5% weaker',shock('yuan_down_5pct'),range('yuan_down_5pct')):'',
    has('risk_off')?quantTile('Risk-off week',shock('risk_off'),offHint):'',
    quantTile('Macro R²',pct(m.r_squared),'Share of the weekly moves the series explain together'),
    quantTile('Weeks in the macro model',quantCount((m.window||{}).observations),(m.window||{}).start?`${analyticsDate(m.window.start)} to ${analyticsDate(m.window.end)}`:''),
  ]);
  const per=x=>x.transform==='diff_bp'?`${signedPct(Number(x.beta)*100)} per 100bp rise`:`${signedPct(Number(x.beta)*Math.log(1.1))} per 10% rise`;
  const rows=(m.exposures||[]).map(x=>quantRow(quantMacroNames[x.factor]||x.factor,[quantNum(x.beta)?per(x):'—',quantFixed(x.t_statistic,2),x.significant?'Yes':'No']));
  const why=inputs.factors_missing_reasons||{},plain=t=>String(t||'').replace(/\.$/,'');
  const missing=(inputs.factors_missing||[]).map(x=>`${quantMacroNames[x]||x}${why[x]?` (${plain(why[x]).charAt(0).toLowerCase()}${plain(why[x]).slice(1)})`:''}`);
  const skipped=Object.keys(inputs.shocks_left_out||{}).map(k=>quantShockNames[k]||k);
  return [tiles+(rows.length?quantSub('Sensitivity to each series, the others held still'):'')+quantTable(['Series','Portfolio move','t-statistic (Newey-West)','Significant'],rows),quantText([missing.length?`Left out of the model: ${missing.join('; ')}.`:'',skipped.length?`Shocks left out, as their series are not in the model: ${skipped.join(', ')}.`:'',m.method,(Object.values(sens).find(x=>x&&x.method)||{}).method,m.disclaimer])]};
const quantRenderCrowding=(r,p)=>{const o=r.ownership_overlap||{},inputs=(p||{}).inputs||{},names=inputs.securities||{},funds=o.most_similar_funds||[],top=funds[0];
  const held=(o.holdings||[]).slice().sort((a,b)=>(Number(b.funds_holding)||0)-(Number(a.funds_holding)||0)||Math.abs(Number(b.weight)||0)-Math.abs(Number(a.weight)||0));const wide=held[0];
  const periods=(inputs.periods||[]).map(analyticsDate).join(', ');
  const tiles=quantTiles([
    quantTile('Funds compared',quantCount(o.funds),periods?`13F filings for the quarter to ${periods}`:''),
    quantTile('Covered by 13F filings',weightPct(inputs.covered_weight),'Share of NAV in shares with a US ISIN'),
    quantTile('In names the funds hold',pct(o.share_in_names_funds_hold),'Share of the covered weight'),
    top&&quantNum(top.similarity)?quantTile('Most similar fund',`${esc(top.fund)} · ${ratio(top.similarity)}`,'Cosine similarity: 1 is the same mix, 0 nothing in common'):'',
    wide?quantTile('Most widely held',`${esc(names[wide.security]||wide.security)} · ${quantCount(wide.funds_holding)} ${Number(wide.funds_holding)===1?'fund':'funds'}`):'',
  ]);
  const fundRows=funds.map(f=>quantRow(f.fund,[ratio(f.similarity),quantCount(f.shared_holdings),weightPct(f.portfolio_weight_in_fund_names),weightPct(f.fund_weight_in_portfolio_names),`${money(f.reported_value)} USD`]));
  const holdingRows=held.slice(0,10).map(x=>quantRow(names[x.security]||x.security,[weightPct(x.weight),quantCount(x.funds_holding)]));
  const out=inputs.not_covered||[];
  return [tiles+(fundRows.length?quantSub('Most similar funds'):'')+quantTable(['Fund','Similarity','Shared holdings','Our weight in its names','Its weight in our names','Reported value'],fundRows)+(holdingRows.length?quantSub('Covered holdings by number of funds'):'')+quantTable(['Holding','Weight','Funds holding'],holdingRows),quantText([out.length?`Not covered (${weightPct(inputs.not_covered_weight)} of NAV): ${out.map(x=>`${x.name} (${String(x.reason||'').replace(/\.$/,'').toLowerCase()})`).join(', ')}.`:'',inputs.shares_outstanding,o.method])]};
quantSections.push(['factor_attribution','quant-factor-attribution',quantRenderFactorAttribution],['liquidity','quant-liquidity',quantRenderLiquidity],['macro','quant-macro',quantRenderMacro],['crowding','quant-crowding',quantRenderCrowding]);
"""
