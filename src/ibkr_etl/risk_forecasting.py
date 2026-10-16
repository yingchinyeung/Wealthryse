"""Forward-looking risk and predictive models over a portfolio's daily returns.

Pure standard-library Python, like :mod:`ibkr_etl.modelling`:

* GARCH(1,1) volatility forecast (EWMA when the history is short or the fit is
  unstable), with forward VaR and expected tail loss by filtered historical
  simulation and an expanding-window backtest of past VaR breaches.
* Drawdown odds over the next year from a block bootstrap of the portfolio's
  own returns, at historical and at today's forecast volatility.
* Factor stress tests through the Fama-French betas the factor model fits.
* A Gaussian hidden Markov model of market regimes, a regime-switching outlook
  for the next one to three months, and a walk-forward score of how well those
  ranges and their direction held up in the past.

Every result is hypothetical and says so.
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Any, Mapping, Sequence

from .modelling import DISCLAIMER, MODEL_VERSION, ModellingInputError, _quantile, _seed

TRADING_DAYS = 252
MONTH = 21
MIN_RETURNS = 126             # six months of sessions for any forecast
GARCH_MIN_RETURNS = 250       # a year before GARCH is fitted; EWMA before that
EWMA_LAMBDA = 0.94
VAR_LEVELS = (0.95, 0.99)
SIMULATION_PATHS = 2000


def _stats(values: Sequence[float]) -> tuple[float, float]:
    return statistics.fmean(values), statistics.pstdev(values)


# Volatility -----------------------------------------------------------------------


def _garch_variances(returns: Sequence[float], omega: float, alpha: float, beta: float, start: float) -> list[float]:
    """Variance for each day from information before it, then the next day's forecast."""

    variances = [start]
    for value in returns:
        variances.append(omega + alpha * value * value + beta * variances[-1])
    return variances


def _garch_loglik(returns: Sequence[float], alpha: float, beta: float, sample_variance: float) -> float:
    if alpha < 0 or beta < 0 or alpha + beta >= 0.999:
        return -math.inf
    omega = sample_variance * (1.0 - alpha - beta)
    total, variance = 0.0, sample_variance
    for value in returns:
        if variance <= 0:
            return -math.inf
        total -= 0.5 * (math.log(variance) + value * value / variance)
        variance = omega + alpha * value * value + beta * variance
    return total


def _nelder_mead(function, start: Sequence[float], step: float = 0.02, iterations: int = 200) -> list[float]:
    """Maximise a function of a few parameters with the Nelder-Mead simplex."""

    simplex = [list(start)] + [[v + (step if i == j else 0.0) for j, v in enumerate(start)] for i in range(len(start))]
    scores = [function(p) for p in simplex]
    for _ in range(iterations):
        order = sorted(range(len(simplex)), key=lambda i: -scores[i])
        simplex, scores = [simplex[i] for i in order], [scores[i] for i in order]
        if abs(scores[0] - scores[-1]) < 1e-9:
            break
        centroid = [statistics.fmean(p[k] for p in simplex[:-1]) for k in range(len(start))]
        worst = simplex[-1]
        reflected = [c + (c - w) for c, w in zip(centroid, worst)]
        score = function(reflected)
        if score > scores[0]:
            expanded = [c + 2 * (c - w) for c, w in zip(centroid, worst)]
            expanded_score = function(expanded)
            simplex[-1], scores[-1] = (expanded, expanded_score) if expanded_score > score else (reflected, score)
        elif score > scores[-2]:
            simplex[-1], scores[-1] = reflected, score
        else:
            contracted = [c + 0.5 * (w - c) for c, w in zip(centroid, worst)]
            contracted_score = function(contracted)
            if contracted_score > scores[-1]:
                simplex[-1], scores[-1] = contracted, contracted_score
            else:
                best = simplex[0]
                simplex = [best] + [[b + 0.5 * (p - b) for b, p in zip(best, point)] for point in simplex[1:]]
                scores = [scores[0]] + [function(p) for p in simplex[1:]]
    return simplex[max(range(len(simplex)), key=lambda i: scores[i])]


def fit_volatility(returns: Sequence[float]) -> dict[str, Any]:
    """GARCH(1,1) by maximum likelihood with variance targeting, or EWMA as the fallback.

    ``returns`` are demeaned daily returns. The result carries each day's
    conditional variance (``variances[i]`` for ``returns[i]``) and the next
    day's forecast (``next_variance``).
    """

    n = len(returns)
    sample_variance = statistics.fmean(v * v for v in returns)
    if sample_variance <= 0:
        raise ModellingInputError("The portfolio's returns do not vary, so volatility cannot be modelled.")
    fit: dict[str, Any] | None = None
    if n >= GARCH_MIN_RETURNS:
        grid = [(a, b) for a in (0.03, 0.06, 0.1, 0.15) for b in (0.8, 0.85, 0.9, 0.94) if a + b < 0.995]
        start = max(grid, key=lambda p: _garch_loglik(returns, p[0], p[1], sample_variance))
        alpha, beta = _nelder_mead(lambda p: _garch_loglik(returns, p[0], p[1], sample_variance), start)
        constant = -0.5 * n * (math.log(sample_variance) + 1.0)
        likelihood = _garch_loglik(returns, alpha, beta, sample_variance)
        if math.isfinite(likelihood) and likelihood > constant + 1.0 and 0.0 < alpha + beta < 0.999:
            omega = sample_variance * (1.0 - alpha - beta)
            variances = _garch_variances(returns, omega, alpha, beta, sample_variance)
            fit = {"method": "garch", "omega": omega, "alpha": alpha, "beta": beta, "persistence": alpha + beta,
                   "long_run_variance": sample_variance, "log_likelihood": likelihood}
    if fit is None:
        variances = [sample_variance]
        for value in returns:
            variances.append(EWMA_LAMBDA * variances[-1] + (1.0 - EWMA_LAMBDA) * value * value)
        fit = {"method": "ewma", "omega": 0.0, "alpha": 1.0 - EWMA_LAMBDA, "beta": EWMA_LAMBDA, "persistence": 1.0,
               "long_run_variance": sample_variance, "log_likelihood": None}
    fit["variances"] = variances[:-1]
    fit["next_variance"] = variances[-1]
    return fit


def variance_path(fit: Mapping[str, Any], horizon: int) -> list[float]:
    """Expected daily variance for each of the next ``horizon`` days."""

    first, persistence = fit["next_variance"], fit["persistence"]
    if fit["method"] == "ewma":
        return [first] * horizon
    long_run = fit["long_run_variance"]
    return [long_run + persistence ** k * (first - long_run) for k in range(horizon)]


def _residuals(returns: Sequence[float], variances: Sequence[float]) -> list[float]:
    return [r / math.sqrt(v) for r, v in zip(returns, variances) if v > 0]


def _tail(ordered: Sequence[float], level: float) -> tuple[float, float]:
    """VaR and expected tail loss (positive numbers) of a sorted sample of returns."""

    cut = _quantile(ordered, 1.0 - level)
    tail = [v for v in ordered if v <= cut] or [cut]
    return -cut, -statistics.fmean(tail)


def _kupiec(breaches: int, observations: int, probability: float) -> float | None:
    """p-value of Kupiec's proportion-of-failures test (chi-squared, one degree of freedom)."""

    if observations == 0:
        return None
    rate = breaches / observations

    def log_likelihood(p):
        return (observations - breaches) * math.log(1 - p) + (breaches * math.log(p) if breaches else 0.0) \
            if 0 < p < 1 else 0.0

    statistic = -2.0 * (log_likelihood(probability) - (log_likelihood(rate) if 0 < rate < 1 else 0.0))
    return math.erfc(math.sqrt(max(statistic, 0.0) / 2.0))


def var_backtest(returns: Sequence[float], dates: Sequence[str], *, level: float = 0.95,
                 training: int = GARCH_MIN_RETURNS, refit_every: int = 63) -> dict[str, Any]:
    """Expanding-window backtest: each day's VaR uses only earlier returns, refitted quarterly."""

    if len(returns) < training + 63:
        return {"available": False, "reason": "A backtest needs at least a year of returns plus a quarter to test."}
    breaches, rows, fit, z_quantile = [], [], None, None
    for t in range(training, len(returns)):
        if fit is None or (t - training) % refit_every == 0:
            fit = fit_volatility(returns[:t])
            z_quantile = _quantile(sorted(_residuals(returns[:t], fit["variances"])), 1.0 - level)
            variance = fit["next_variance"]
        var = -z_quantile * math.sqrt(variance)
        breached = returns[t] < -var
        if breached:
            breaches.append(dates[t])
        rows.append({"date": dates[t], "return": returns[t], "var": var})
        variance = fit["omega"] + fit["alpha"] * returns[t] ** 2 + fit["beta"] * variance \
            if fit["method"] == "garch" else EWMA_LAMBDA * variance + (1 - EWMA_LAMBDA) * returns[t] ** 2
    observations = len(rows)
    expected = observations * (1.0 - level)
    p_value = _kupiec(len(breaches), observations, 1.0 - level)
    verdict = ("in line with the model" if p_value is not None and p_value >= 0.05 else
               "more often than the model expects, so treat its VaR as too low" if len(breaches) > expected else
               "less often than the model expects, so its VaR has been cautious")
    return {"available": True, "level": level, "observations": observations, "breaches": len(breaches),
            "expected_breaches": expected, "breach_rate": len(breaches) / observations, "kupiec_p_value": p_value,
            "verdict": f"Losses broke the {level:.0%} VaR line on {len(breaches)} of {observations} days "
                       f"(about {expected:.0f} expected): {verdict}.",
            "recent_breaches": breaches[-10:], "series": rows[-260:]}


def volatility_forecast(daily: Sequence[tuple[str, float]], *, paths: int = SIMULATION_PATHS) -> dict[str, Any]:
    """Forecast volatility, forward VaR and expected tail loss, and backtest past VaR."""

    if len(daily) < MIN_RETURNS:
        raise ModellingInputError(f"A volatility forecast needs at least {MIN_RETURNS} daily returns; "
                                  f"this portfolio has {len(daily)}.")
    dates = [d for d, _ in daily]
    raw = [r for _, r in daily]
    mean = statistics.fmean(raw)
    returns = [r - mean for r in raw]
    fit = fit_volatility(returns)
    residuals = sorted(_residuals(returns, fit["variances"]))
    next_sd = math.sqrt(fit["next_variance"])
    one_day = {f"{round(level * 100)}": dict(zip(("var", "expected_tail_loss"),
                                                (-_quantile(residuals, 1 - level) * next_sd,
                                                 -statistics.fmean([z for z in residuals if z <= _quantile(residuals, 1 - level)]) * next_sd)))
               for level in VAR_LEVELS}
    generator = random.Random(_seed(raw, paths, "volatility"))
    month_returns = []
    for _ in range(paths):
        variance, growth = fit["next_variance"], 1.0
        for _day in range(MONTH):
            shock = residuals[generator.randrange(len(residuals))] * math.sqrt(variance)
            growth *= max(1.0 + shock, 0.0)
            variance = (fit["omega"] + fit["alpha"] * shock * shock + fit["beta"] * variance
                        if fit["method"] == "garch" else variance)
        month_returns.append(growth - 1.0)
    month_returns.sort()
    one_month = {f"{round(level * 100)}": dict(zip(("var", "expected_tail_loss"), _tail(month_returns, level)))
                 for level in VAR_LEVELS}
    path = variance_path(fit, MONTH)
    recent = returns[-TRADING_DAYS:]
    persistence = fit["persistence"]
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method_name": "GARCH(1,1)" if fit["method"] == "garch" else "EWMA (λ = 0.94)",
        "observations": len(returns),
        "window_start": dates[0],
        "window_end": dates[-1],
        "forecast_volatility_annual": next_sd * math.sqrt(TRADING_DAYS),
        "forecast_volatility_next_month_annual": math.sqrt(statistics.fmean(path) * TRADING_DAYS),
        "long_run_volatility_annual": math.sqrt(fit["long_run_variance"] * TRADING_DAYS),
        "realised_volatility_last_year": statistics.pstdev(recent) * math.sqrt(TRADING_DAYS) if len(recent) > 1 else None,
        "shock_half_life_days": (math.log(0.5) / math.log(persistence)) if 0 < persistence < 1 else None,
        "parameters": {k: fit[k] for k in ("omega", "alpha", "beta", "persistence")},
        "one_day": one_day,
        "one_month": one_month,
        "backtest": var_backtest(returns, dates),
        "_fit": fit,
        "method": (
            "Daily returns, net of contributions and withdrawals, minus their average. Volatility is a "
            + ("GARCH(1,1) model fitted by maximum likelihood with its long-run level set to the sample variance: "
               "a large move raises tomorrow's forecast, which then decays back. "
               if fit["method"] == "garch" else
               "exponentially weighted average of squared returns (used when there is under a year of history or "
               "GARCH does not fit better than constant volatility). ")
            + "VaR and expected tail loss come from filtered historical simulation: the portfolio's own "
            "standardised returns rescaled to today's forecast; the one-month figures compound "
            f"{paths:,} simulated 21-session paths. The backtest refits quarterly using only earlier data."
        ),
        "disclaimer": DISCLAIMER,
    }


# Drawdown odds ----------------------------------------------------------------------


DRAWDOWN_THRESHOLDS = (0.1, 0.2, 0.3)


def drawdown_odds(daily_returns: Sequence[float], *, horizon: int = TRADING_DAYS, volatility_scale: float | None = None,
                  paths: int = SIMULATION_PATHS) -> dict[str, Any]:
    """Chance of a peak-to-trough fall of 10%, 20% or 30% within the horizon, starting from today.

    Paths are built from random overlapping 21-session stretches of the
    portfolio's own daily returns. ``volatility_scale`` rescales each return's
    distance from the average, to replay history at today's forecast volatility.
    """

    if len(daily_returns) < MIN_RETURNS:
        raise ModellingInputError(f"Drawdown odds need at least {MIN_RETURNS} daily returns.")
    mean = statistics.fmean(daily_returns)
    scale = volatility_scale or 1.0
    returns = [mean + (r - mean) * scale for r in daily_returns]
    starts = len(returns) - MONTH + 1
    generator = random.Random(_seed(list(daily_returns), horizon, scale, paths, "drawdown"))
    worst = []
    for _ in range(paths):
        level = peak = 1.0
        fall = 0.0
        days = 0
        while days < horizon:
            start = generator.randrange(starts)
            for value in returns[start:start + min(MONTH, horizon - days)]:
                level *= max(1.0 + value, 0.0)
                peak = max(peak, level)
                fall = min(fall, level / peak - 1.0)
            days += MONTH
        worst.append(fall)
    worst.sort()
    return {
        "volatility_scale": scale,
        "probabilities": [{"threshold": t, "probability": sum(1 for w in worst if w <= -t) / paths}
                          for t in DRAWDOWN_THRESHOLDS],
        "median_worst_fall": _quantile(worst, 0.5),
        "bad_case_worst_fall": _quantile(worst, 0.05),
    }


# Factor stress ----------------------------------------------------------------------

STRESS_PRESETS = {
    "market_down_20": ("Market falls 20%", {"Mkt-RF": -0.20}),
    "growth_selloff": ("Growth and momentum sell-off", {"Mkt-RF": -0.10, "HML": 0.08, "MOM": -0.10}),
    "flight_to_quality": ("Flight to quality", {"Mkt-RF": -0.12, "RMW": 0.06, "CMA": 0.05}),
    "small_cap_rally": ("Small-cap and value rally", {"Mkt-RF": 0.05, "SMB": 0.06, "HML": 0.05}),
    "value_rotation": ("Rotation from growth to value", {"HML": 0.10, "MOM": -0.06}),
}
MAX_FACTOR_SHOCK = 0.6


def factor_stress(model: Mapping[str, Any], shocks: Mapping[str, float]) -> dict[str, Any]:
    """Portfolio move implied by the factor betas for one-off factor shocks."""

    if not shocks:
        raise ModellingInputError("Choose at least one factor shock.")
    betas = {x["factor"]: x for x in model["exposures"]}
    rows, ignored, total = [], [], 0.0
    for factor, shock in shocks.items():
        if not math.isfinite(shock) or abs(shock) > MAX_FACTOR_SHOCK:
            raise ModellingInputError(f"Each factor shock must be between -{MAX_FACTOR_SHOCK:.0%} and +{MAX_FACTOR_SHOCK:.0%}.")
        exposure = betas.get(factor)
        if exposure is None:
            ignored.append(factor)
            continue
        impact = exposure["beta"] * shock
        total += impact
        rows.append({"factor": factor, "label": exposure["label"], "shock": shock, "beta": exposure["beta"],
                     "significant": exposure["significant"], "impact": impact})
    if not rows:
        raise ModellingInputError("None of the shocked factors is in the fitted model.")
    residual = model.get("residual_volatility_annual")
    return {
        "impact": total,
        "factors": sorted(rows, key=lambda r: -abs(r["impact"])),
        "factors_not_in_model": ignored,
        "r_squared": model.get("r_squared"),
        "unexplained_monthly_range": 1.645 * residual / math.sqrt(12) if residual is not None else None,
        "method": ("Each factor's shock times the portfolio's fitted beta to it, summed. This is an instant, "
                   "one-off move: alpha and the risk-free rate are left out. The factor model explains "
                   "only part of the portfolio's moves (R²), so the real outcome can differ by about the "
                   "unexplained monthly range either way."),
        "disclaimer": DISCLAIMER,
    }


# Regimes ----------------------------------------------------------------------------

REGIME_LABELS = {2: ("Calm", "Stressed"), 3: ("Calm", "Normal", "Stressed")}
REGIME_HISTORY = 1260          # five years of sessions
REGIME_MIN_SHARE = 0.03        # a state used under 3% of the time is not a regime


def _normal_density(x: float, mean: float, sd: float) -> float:
    return math.exp(-0.5 * ((x - mean) / sd) ** 2) / (sd * math.sqrt(2 * math.pi))


def _forward(x: Sequence[float], model: Mapping[str, Any]) -> tuple[list[list[float]], list[float]]:
    k, means, sds, transition = len(model["means"]), model["means"], model["sds"], model["transition"]
    alphas, scales = [], []
    previous = model["initial"]
    for t, value in enumerate(x):
        if t:
            previous = [sum(alphas[-1][i] * transition[i][j] for i in range(k)) for j in range(k)]
        row = [previous[j] * max(_normal_density(value, means[j], sds[j]), 1e-300) for j in range(k)]
        total = sum(row)
        scales.append(total)
        alphas.append([v / total for v in row])
    return alphas, scales


def fit_hmm(x: Sequence[float], states: int, *, iterations: int = 100, start: Mapping[str, Any] | None = None,
            tolerance: float = 1e-6) -> dict[str, Any]:
    """Gaussian hidden Markov model by Baum-Welch, states ordered from lowest to highest volatility."""

    n, k = len(x), states
    mean, sd = _stats(x)
    if start is None:
        ordered = sorted(abs(v - mean) for v in x)
        sds = [max(math.sqrt(statistics.fmean([d * d for d in ordered[i * n // k:(i + 1) * n // k]] or [sd * sd])),
                   sd * 0.05) for i in range(k)]
        model = {"means": [mean] * k, "sds": sds, "initial": [1.0 / k] * k,
                 "transition": [[0.96 if i == j else 0.04 / (k - 1) for j in range(k)] for i in range(k)]}
    else:
        model = {key: [list(r) if isinstance(r, list) else r for r in start[key]] for key in
                 ("means", "sds", "initial", "transition")}
    floor = sd * 0.02
    likelihood = -math.inf
    for _ in range(iterations):
        alphas, scales = _forward(x, model)
        new_likelihood = sum(math.log(s) for s in scales)
        betas = [[1.0] * k for _ in range(n)]
        for t in range(n - 2, -1, -1):
            emissions = [max(_normal_density(x[t + 1], model["means"][j], model["sds"][j]), 1e-300) for j in range(k)]
            betas[t] = [sum(model["transition"][i][j] * emissions[j] * betas[t + 1][j] for j in range(k)) / scales[t + 1]
                        for i in range(k)]
        gammas = []
        for t in range(n):
            row = [alphas[t][i] * betas[t][i] for i in range(k)]
            total = sum(row) or 1.0
            gammas.append([v / total for v in row])
        counts = [[0.0] * k for _ in range(k)]
        for t in range(n - 1):
            emissions = [max(_normal_density(x[t + 1], model["means"][j], model["sds"][j]), 1e-300) for j in range(k)]
            for i in range(k):
                for j in range(k):
                    counts[i][j] += alphas[t][i] * model["transition"][i][j] * emissions[j] * betas[t + 1][j] / scales[t + 1]
        weights = [sum(g[i] for g in gammas) for i in range(k)]
        model["initial"] = gammas[0]
        model["transition"] = [[counts[i][j] / (sum(counts[i]) or 1.0) for j in range(k)] for i in range(k)]
        model["means"] = [sum(g[i] * v for g, v in zip(gammas, x)) / (weights[i] or 1.0) for i in range(k)]
        model["sds"] = [max(math.sqrt(sum(g[i] * (v - model["means"][i]) ** 2 for g, v in zip(gammas, x))
                                      / (weights[i] or 1.0)), floor) for i in range(k)]
        if abs(new_likelihood - likelihood) < tolerance * max(1.0, abs(new_likelihood)):
            likelihood = new_likelihood
            break
        likelihood = new_likelihood
    order = sorted(range(k), key=lambda i: model["sds"][i])
    model = {
        "means": [model["means"][i] for i in order],
        "sds": [model["sds"][i] for i in order],
        "initial": [model["initial"][i] for i in order],
        "transition": [[model["transition"][i][j] for j in order] for i in order],
    }
    alphas, scales = _forward(x, model)
    model["log_likelihood"] = sum(math.log(s) for s in scales)
    model["filtered"] = alphas
    model["shares"] = [statistics.fmean(a[i] for a in alphas) for i in range(k)]
    model["parameters"] = k * k - 1 + 2 * k
    model["bic"] = -2.0 * model["log_likelihood"] + model["parameters"] * math.log(n)
    return model


def _matrix_power_row(row: Sequence[float], transition: Sequence[Sequence[float]], steps: int) -> list[float]:
    k = len(row)
    current = list(row)
    for _ in range(steps):
        current = [sum(current[i] * transition[i][j] for i in range(k)) for j in range(k)]
    return current


def choose_regimes(x: Sequence[float]) -> dict[str, Any]:
    """Two- or three-state model, whichever has the lower BIC with every state actually used."""

    candidates = []
    for states in (2, 3):
        model = fit_hmm(x, states)
        if min(model["shares"]) >= REGIME_MIN_SHARE:
            candidates.append(model)
    if not candidates:
        raise ModellingInputError("The portfolio's returns do not separate into distinct regimes.")
    return min(candidates, key=lambda m: m["bic"])


def _simulate(model: Mapping[str, Any], pools: Sequence[Sequence[float]], start: Sequence[float], horizons: Sequence[int],
              paths: int, generator: random.Random) -> dict[int, list[float]]:
    k, longest = len(model["means"]), max(horizons)
    outcomes: dict[int, list[float]] = {h: [] for h in horizons}
    cumulative = [list(itertools_accumulate(row)) for row in model["transition"]]
    initial = list(itertools_accumulate(start))
    for _ in range(paths):
        state = _draw(initial, generator)
        growth = 1.0
        for day in range(1, longest + 1):
            state = _draw(cumulative[state], generator)
            pool = pools[state]
            value = pool[generator.randrange(len(pool))] if len(pool) >= 20 else \
                generator.gauss(model["means"][state], model["sds"][state])
            growth *= max(1.0 + value, 0.0)
            if day in outcomes:
                outcomes[day].append(growth - 1.0)
    return outcomes


def itertools_accumulate(values: Sequence[float]) -> list[float]:
    total, out = 0.0, []
    for v in values:
        total += v
        out.append(total)
    return out


def _draw(cumulative: Sequence[float], generator: random.Random) -> int:
    pick = generator.random() * cumulative[-1]
    for i, edge in enumerate(cumulative):
        if pick <= edge:
            return i
    return len(cumulative) - 1


def _pools(x: Sequence[float], model: Mapping[str, Any]) -> list[list[float]]:
    pools: list[list[float]] = [[] for _ in model["means"]]
    for value, probabilities in zip(x, model["filtered"]):
        pools[max(range(len(probabilities)), key=lambda i: probabilities[i])].append(value)
    return pools


OUTLOOK_HORIZONS = (21, 42, 63)
OUTLOOK_BANDS = (0.05, 0.25, 0.5, 0.75, 0.95)


def _score(x: Sequence[float], *, horizon: int = MONTH, training: int = 504, refit_every: int = 63,
           paths: int = 500, seed: int = 0) -> dict[str, Any]:
    """Walk-forward check of the one-month outlook: band coverage and direction against a naive rule."""

    points = list(range(training, len(x) - horizon + 1, horizon))
    if len(points) < 12:
        return {"available": False, "reason": "There is not enough history yet to score past outlooks "
                                              "(it needs two years to learn from plus a year to test)."}
    generator = random.Random(seed)
    inside_90 = inside_50 = hits = naive_hits = 0
    model = None
    for count, t in enumerate(points):
        history = x[max(0, t - REGIME_HISTORY):t]
        if model is None or count % max(1, refit_every // horizon) == 0:
            model = choose_regimes(history) if model is None else \
                fit_hmm(history, len(model["means"]), iterations=40, start=model)
        filtered, _ = _forward(history, model)
        sample = sorted(_simulate({**model, "filtered": filtered}, _pools(history, {**model, "filtered": filtered}),
                                  filtered[-1], (horizon,), paths, generator)[horizon])
        actual = math.prod(1.0 + v for v in x[t:t + horizon]) - 1.0
        low, high = _quantile(sample, 0.05), _quantile(sample, 0.95)
        q25, median, q75 = _quantile(sample, 0.25), _quantile(sample, 0.5), _quantile(sample, 0.75)
        inside_90 += low <= actual <= high
        inside_50 += q25 <= actual <= q75
        hits += (median >= 0) == (actual >= 0)
        past_months = [math.prod(1.0 + v for v in history[i:i + horizon]) - 1.0
                       for i in range(0, len(history) - horizon + 1, horizon)]
        naive_up = sum(1 for m in past_months if m >= 0) >= len(past_months) / 2
        naive_hits += naive_up == (actual >= 0)
    tested = len(points)
    # One-sided binomial test of the model's hits against the naive rule's hit rate.
    baseline = max(naive_hits / tested, 0.5)
    p_value = sum(math.comb(tested, i) * baseline ** i * (1 - baseline) ** (tested - i) for i in range(hits, tested + 1))
    coverage = inside_90 / tested
    calibration = ("well calibrated" if 0.8 <= coverage <= 0.97 else
                   "too narrow: outcomes fell outside it more often than they should" if coverage < 0.8 else
                   "wider than needed")
    skill = hits > naive_hits and p_value < 0.1
    return {
        "available": True, "tested_months": tested, "horizon_sessions": horizon,
        "coverage_90": coverage, "coverage_50": inside_50 / tested,
        "direction_hit_rate": hits / tested, "naive_hit_rate": naive_hits / tested, "direction_p_value": p_value,
        "directional_skill": skill,
        "verdict": (f"Over {tested} past months the 90% range held the outcome {inside_90} times ({coverage:.0%}), "
                    f"so it has been {calibration}. "
                    + (f"The median called the direction right {hits} times against {naive_hits} for simply assuming "
                       "the more common past direction, which is unlikely to be luck."
                       if skill else
                       f"The median called the direction right {hits} times against {naive_hits} for simply assuming "
                       "the more common past direction, so it shows no reliable directional skill: use the range, "
                       "not the middle line.")),
    }


def regime_outlook(daily: Sequence[tuple[str, float]], *, paths: int = SIMULATION_PATHS,
                   score: bool = True) -> dict[str, Any]:
    """Market regimes from the portfolio's own returns, today's regime, and a scored 1-3 month outlook."""

    if len(daily) < TRADING_DAYS:
        raise ModellingInputError("Regimes need at least a year of daily returns.")
    window = list(daily[-REGIME_HISTORY:])
    dates = [d for d, _ in window]
    x = [r for _, r in window]
    model = choose_regimes(x)
    k = len(model["means"])
    labels = REGIME_LABELS[k]
    now = model["filtered"][-1]
    in_a_month = _matrix_power_row(now, model["transition"], MONTH)
    pools = _pools(x, model)
    regimes = []
    for i in range(k):
        stay = model["transition"][i][i]
        regimes.append({
            "regime": labels[i], "annual_return": model["means"][i] * TRADING_DAYS,
            "annual_volatility": model["sds"][i] * math.sqrt(TRADING_DAYS), "share_of_time": model["shares"][i],
            "expected_duration_days": 1.0 / (1.0 - stay) if stay < 1 else None,
            "probability_now": now[i], "probability_in_a_month": in_a_month[i], "days_observed": len(pools[i]),
        })
    current = max(range(k), key=lambda i: now[i])
    week_ago = model["filtered"][-6] if len(model["filtered"]) >= 6 else now
    generator = random.Random(_seed(x, paths, "regimes"))
    outcomes = _simulate(model, pools, now, OUTLOOK_HORIZONS, paths, generator)
    outlook = []
    for horizon in OUTLOOK_HORIZONS:
        sample = sorted(outcomes[horizon])
        outlook.append({"sessions": horizon, "months": horizon // MONTH,
                        **{f"p{round(b * 100)}": _quantile(sample, b) for b in OUTLOOK_BANDS},
                        "probability_loss": sum(1 for v in sample if v < 0) / len(sample),
                        "probability_fall_10": sum(1 for v in sample if v <= -0.1) / len(sample)})
    timeline = []
    for index in range(0, len(dates), 5):
        timeline.append({"date": dates[index], "stressed_probability": model["filtered"][index][-1]})
    if timeline[-1]["date"] != dates[-1]:
        timeline.append({"date": dates[-1], "stressed_probability": model["filtered"][-1][-1]})
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "window_start": dates[0], "window_end": dates[-1], "observations": len(x),
        "states": k, "current_regime": labels[current], "current_probability": now[current],
        "regime_a_week_ago": labels[max(range(k), key=lambda i: week_ago[i])],
        "regimes": regimes,
        "transition": [{"from": labels[i], **{labels[j]: model["transition"][i][j] for j in range(k)}} for i in range(k)],
        "outlook": outlook,
        "timeline": timeline[-400:],
        "score": _score(x, seed=_seed(x, "score")) if score else {"available": False, "reason": "Not requested."},
        "method": (
            f"A {k}-state Gaussian hidden Markov model of daily returns (net of flows) over up to five years, fitted "
            "by Baum-Welch; the number of states is the one with the lower BIC. States are named by their "
            "volatility. Today's regime probabilities use only past data. The outlook simulates "
            f"{paths:,} paths that switch regimes with the fitted odds and draw each day's return from past days "
            "in that regime. The score refits on data available at each past month-end and checks the next month."
        ),
        "disclaimer": DISCLAIMER,
    }
