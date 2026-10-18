"""Tail risk: extreme-value VaR and expected shortfall, and backtests that check them.

Pure standard-library Python, like :mod:`ibkr_etl.risk_forecasting`, whose GARCH fit,
Nelder-Mead search, historical tail and Kupiec test this module reuses:

* :func:`fit_gpd` fits a generalised Pareto distribution to losses beyond a threshold
  by maximum likelihood, started from probability-weighted moments (Hosking and
  Wallis 1987).
* :func:`evt_var_es` gives peaks-over-threshold VaR and expected shortfall (McNeil and
  Frey 2000): on the raw returns, and on GARCH-standardised returns rescaled to
  tomorrow's forecast volatility.
* :func:`christoffersen_test` asks whether VaR breaches cluster, and whether their
  number and timing together fit the model (Christoffersen 1998).
* :func:`es_backtest` tests the expected shortfall itself with the Z2 statistic of
  Acerbi and Szekely (2014).
* :func:`traffic_light` gives the Basel green, amber or red zone for a breach count.
* :func:`tail_backtest_report` walks a historical-simulation VaR and expected shortfall
  forward at 97.5% and 99%, refitted monthly, and runs every test above on it.

Losses are positive numbers: a VaR of 0.02 means a 2% fall. Every model figure is
hypothetical and says so.
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Any, Sequence

from ..modelling import DISCLAIMER, ModellingInputError, _quantile, _seed
from ..risk_forecasting import _kupiec, _nelder_mead, _residuals, _tail, fit_volatility

MODEL_VERSION = "tail-risk-v1"
MIN_EXCEEDANCES = 30
SHAPE_BOUNDS = (-0.5, 1.5)     # maximum likelihood is regular above -0.5 (Smith 1985)
EXPONENTIAL_LIMIT = 1e-9       # |shape| below this uses the exponential (shape 0) formulas
BACKTEST_TRAINING = 250
BACKTEST_MIN_TEST = 250
REFIT_EVERY = 21
BACKTEST_LEVELS = (0.975, 0.99)
ES_SIMULATIONS = 20000          # enough to resolve the 0.01% red cut-off
# Acerbi and Szekely (2014), "Backtesting Expected Shortfall", Risk, December 2014: for
# 250 days at 97.5% the Z2 thresholds are -0.70 at 5% significance and -1.8 at 0.01%,
# and they hardly move across Student-t tails. They mirror the Basel zone edges below.
Z2_AMBER, Z2_RED = -0.70, -1.8
Z2_SOURCE = ("Acerbi and Szekely (2014), Backtesting Expected Shortfall, Risk (December): Z2 below -0.70 is "
             "significant at 5% and below -1.8 at 0.01%, for 250 days at 97.5%.")
# Basel Committee (1996), Supervisory framework for the use of backtesting in conjunction
# with the internal models approach: zones by the binomial cumulative probability.
BASEL_GREEN_BELOW, BASEL_RED_FROM = 0.95, 0.9999
BASEL_PLUS_FACTORS = {5: 0.40, 6: 0.50, 7: 0.65, 8: 0.75, 9: 0.85}   # 250 days at 99%


def _level_key(level: float) -> str:
    return f"{round(level * 100, 4):g}"


def _clean_returns(values: Sequence[float | None]) -> tuple[list[float], int]:
    """Drop missing values (None or NaN) and count them; refuse infinite ones."""

    clean, skipped = [], 0
    for value in values:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            skipped += 1
            continue
        value = float(value)
        if not math.isfinite(value):
            raise ModellingInputError("One of the values is infinite, so the tail cannot be modelled.")
        clean.append(value)
    return clean, skipped


def _check_level(level: float) -> None:
    if not (isinstance(level, (int, float)) and 0.0 < level < 1.0):
        raise ModellingInputError("Each confidence level must be between 0 and 1, such as 0.99.")


# Chi-square and binomial tails -------------------------------------------------------


def _chi_square_tail(statistic: float, degrees_of_freedom: int) -> float:
    """P(X >= statistic) for X chi-square with whole-number degrees of freedom (closed form)."""

    k = int(degrees_of_freedom)
    if k < 1:
        raise ModellingInputError("A chi-square test needs at least one degree of freedom.")
    x = max(float(statistic), 0.0)
    half = x / 2.0
    if k % 2 == 0:
        term = total = 1.0
        for i in range(1, k // 2):
            term *= half / i
            total += term
        return min(1.0, math.exp(-half) * total)
    total = math.erfc(math.sqrt(half))
    term = math.sqrt(2.0 * x / math.pi) * math.exp(-half)
    for r in range(1, (k - 1) // 2 + 1):
        total += term
        term *= x / (2 * r + 1)
    return min(1.0, total)


def _binomial_cdf(successes: int, trials: int, probability: float) -> float:
    """P(X <= successes) for X binomial, summed in log space so long windows do not underflow."""

    log_p, log_q = math.log(probability), math.log1p(-probability)
    base = math.lgamma(trials + 1)
    total = math.fsum(math.exp(base - math.lgamma(i + 1) - math.lgamma(trials - i + 1) + i * log_p
                               + (trials - i) * log_q) for i in range(successes + 1))
    return min(1.0, total)


def _xlogy(count: float, probability: float) -> float:
    """count * log(probability), with 0 * log(0) = 0."""

    if count == 0:
        return 0.0
    return count * math.log(probability) if probability > 0 else -math.inf


def _bernoulli_loglik(zeros: int, ones: int, probability: float) -> float:
    return _xlogy(zeros, 1.0 - probability) + _xlogy(ones, probability)


# Generalised Pareto fit ---------------------------------------------------------------


def _gpd_loglik(excesses: Sequence[float], shape: float, scale: float) -> float:
    """Log-likelihood of the generalised Pareto distribution; shape 0 is the exponential limit."""

    if scale <= 0:
        return -math.inf
    n = len(excesses)
    if abs(shape) < EXPONENTIAL_LIMIT:
        return -n * math.log(scale) - math.fsum(excesses) / scale
    total = 0.0
    for y in excesses:
        z = shape * y / scale
        if z <= -1.0:
            return -math.inf          # beyond the end of a short (shape < 0) tail
        total += math.log1p(z)
    return -n * math.log(scale) - (1.0 + 1.0 / shape) * total


def _pwm(ordered: Sequence[float]) -> tuple[float, float] | None:
    """Probability-weighted moment estimates (Hosking and Wallis 1987), plotting position (i - 0.35) / n.

    Hosking and Wallis write the shape as k = -shape; valid while the shape is below 1.
    """

    n = len(ordered)
    a0 = statistics.fmean(ordered)
    a1 = math.fsum((1.0 - (i + 0.65) / n) * y for i, y in enumerate(ordered)) / n
    denominator = a0 - 2.0 * a1
    if denominator <= 0:
        return None
    return 2.0 - a0 / denominator, 2.0 * a0 * a1 / denominator


def fit_gpd(exceedances: Sequence[float]) -> dict[str, Any]:
    """Generalised Pareto fit to the amounts by which losses went past a threshold.

    Maximum likelihood over (shape ξ, log scale β) by Nelder-Mead, started from the
    probability-weighted moments of Hosking and Wallis (1987). Shape 0 is the
    exponential tail, shape above 0 a heavy (Pareto-like) tail, below 0 a tail with an
    end. Standard errors are the asymptotic ones of Smith (1987), valid for ξ > -0.5:
    var(ξ) = (1 + ξ)² / n, var(β) = 2β²(1 + ξ) / n.
    """

    excesses, skipped = _clean_returns(exceedances)
    if len(excesses) < MIN_EXCEEDANCES:
        raise ModellingInputError(f"A tail fit needs at least {MIN_EXCEEDANCES} losses beyond the threshold; "
                                  f"there are {len(excesses)}.")
    if min(excesses) < 0:
        raise ModellingInputError("Exceedances are the amounts by which losses went past the threshold, "
                                  "so none can be negative.")
    ordered = sorted(excesses)
    if ordered[-1] <= 0 or ordered[-1] - ordered[0] <= 1e-12 * ordered[-1]:
        raise ModellingInputError("The losses beyond the threshold are all the same size, so their tail "
                                  "cannot be fitted.")
    n, mean = len(ordered), statistics.fmean(ordered)

    def objective(point: Sequence[float]) -> float:
        shape, log_scale = point
        if not SHAPE_BOUNDS[0] < shape < SHAPE_BOUNDS[1] or abs(log_scale) > 700:
            return -math.inf
        return _gpd_loglik(ordered, shape, math.exp(log_scale))

    pwm = _pwm(ordered)
    exponential = [0.0, math.log(mean)]          # always feasible
    candidates = [exponential]
    if pwm is not None:
        start = [min(max(pwm[0], -0.45), 0.95), math.log(pwm[1])]
        if math.isfinite(objective(start)):
            candidates.append(start)
    best = max(candidates, key=objective)
    for step in (0.1, 0.02):                      # a restart from the first answer guards against stalling
        best = max([best, _nelder_mead(objective, best, step=step, iterations=400)], key=objective)
    shape, scale = best[0], math.exp(best[1])
    if abs(shape) < EXPONENTIAL_LIMIT:
        shape = 0.0
    errors = None
    if shape > -0.5:
        errors = {"shape": (1.0 + shape) / math.sqrt(n), "scale": scale * math.sqrt(2.0 * (1.0 + shape) / n)}
    return {
        "model_version": MODEL_VERSION,
        "shape": shape,
        "scale": scale,
        "log_likelihood": _gpd_loglik(ordered, shape, scale),
        "exceedances": n,
        "standard_errors": errors,
        "shape_interval_95": ([shape - 1.96 * errors["shape"], shape + 1.96 * errors["shape"]]
                              if errors else None),
        "mean_is_finite": shape < 1.0,
        "start": {"shape": pwm[0], "scale": pwm[1]} if pwm else None,
        "missing_skipped": skipped,
        "method": ("Generalised Pareto distribution fitted by maximum likelihood (Nelder-Mead from the "
                   "probability-weighted moment estimates of Hosking and Wallis 1987); standard errors are "
                   "the asymptotic ones (Smith 1987)."),
    }


# Peaks over threshold -----------------------------------------------------------------


def _pot_measures(threshold: float, shape: float, scale: float, rate: float, level: float) -> tuple[float, float | None]:
    """VaR and expected shortfall from a GPD tail (McNeil and Frey 2000).

    ``rate`` is the share of observations beyond the threshold. VaR = u + (β/ξ)(((1 - q)/rate)^-ξ - 1)
    and ES = (VaR + β - ξu) / (1 - ξ); at ξ = 0 these become u + β log(rate / (1 - q)) and VaR + β.
    """

    log_ratio = math.log((1.0 - level) / rate)
    if abs(shape) < EXPONENTIAL_LIMIT:
        var = threshold - scale * log_ratio
    else:
        var = threshold + scale / shape * math.expm1(-shape * log_ratio)
    es = (var + scale - shape * threshold) / (1.0 - shape) if shape < 1.0 else None
    return var, es


def _peaks_over_threshold(losses: Sequence[float], threshold_quantile: float,
                          levels: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(losses)
    threshold = _quantile(ordered, threshold_quantile)
    excesses = [x - threshold for x in ordered if x > threshold]
    if len(excesses) < MIN_EXCEEDANCES:
        needed = math.ceil(MIN_EXCEEDANCES / (1.0 - threshold_quantile))
        raise ModellingInputError(
            f"Extreme-value VaR needs at least {MIN_EXCEEDANCES} losses beyond the {threshold_quantile:.0%} "
            f"threshold; this history has {len(excesses)}. That takes about {needed} daily returns.")
    fit = fit_gpd(excesses)
    rate = len(excesses) / len(ordered)
    measures = {}
    for level in levels:
        if 1.0 - level >= rate:
            raise ModellingInputError(f"The {level:.1%} level is not beyond the {threshold_quantile:.0%} threshold; "
                                      "choose a higher level or a lower threshold.")
        var, es = _pot_measures(threshold, fit["shape"], fit["scale"], rate, level)
        measures[_level_key(level)] = {"level": level, "var": var, "expected_shortfall": es}
    return {"threshold_loss": threshold, "exceedances": len(excesses), "exceedance_rate": rate,
            "gpd": {k: fit[k] for k in ("shape", "scale", "log_likelihood", "standard_errors",
                                        "shape_interval_95", "mean_is_finite")},
            "levels": measures}


def evt_var_es(daily_returns: Sequence[float | None], *, threshold_quantile: float = 0.90,
               levels: Sequence[float] = (0.99, 0.995), conditional: bool = True) -> dict[str, Any]:
    """Peaks-over-threshold VaR and expected shortfall of daily returns (McNeil and Frey 2000).

    Losses (minus the returns) beyond their ``threshold_quantile`` are fitted with a
    generalised Pareto distribution. ``unconditional`` applies it to the raw returns.
    ``conditional`` (McNeil and Frey's two-step method) fits it to the GARCH-standardised
    returns of :func:`ibkr_etl.risk_forecasting.fit_volatility` instead and rescales by
    tomorrow's forecast volatility, so it reflects today's conditions. Plain historical
    VaR and expected shortfall are given alongside for comparison. Missing returns are
    skipped and counted.
    """

    returns, skipped = _clean_returns(daily_returns)
    if not 0.5 <= threshold_quantile < 1.0:
        raise ModellingInputError("The threshold must be a quantile between 50% and 100%, such as 0.90.")
    if not levels:
        raise ModellingInputError("Choose at least one confidence level.")
    for level in levels:
        _check_level(level)
    if len(returns) < 2 or statistics.pstdev(returns) == 0:
        raise ModellingInputError("The returns do not vary, so there is no tail to model.")
    unconditional = _peaks_over_threshold([-r for r in returns], threshold_quantile, levels)
    ordered = sorted(returns)
    for level in levels:
        var, es = _tail(ordered, level)
        unconditional["levels"][_level_key(level)].update(historical_var=var, historical_expected_shortfall=es)
    result: dict[str, Any] = {
        "available": True,
        "model_version": MODEL_VERSION,
        "observations": len(returns),
        "missing_skipped": skipped,
        "threshold_quantile": threshold_quantile,
        "unconditional": unconditional,
        "conditional": None,
    }
    if conditional:
        mean = statistics.fmean(returns)
        demeaned = [r - mean for r in returns]
        fit = fit_volatility(demeaned)
        residuals = _residuals(demeaned, fit["variances"])
        standardised = _peaks_over_threshold([-z for z in residuals], threshold_quantile, levels)
        next_sd = math.sqrt(fit["next_variance"])
        scaled = {}
        for key, row in standardised["levels"].items():
            es = row["expected_shortfall"]
            scaled[key] = {"level": row["level"], "var": next_sd * row["var"] - mean,
                           "expected_shortfall": next_sd * es - mean if es is not None else None,
                           "standardised_var": row["var"], "standardised_expected_shortfall": es}
        result["conditional"] = {
            "volatility_model": "GARCH(1,1)" if fit["method"] == "garch" else "EWMA (λ = 0.94)",
            "next_day_volatility": next_sd, "mean_daily_return": mean,
            **{k: standardised[k] for k in ("threshold_loss", "exceedances", "exceedance_rate", "gpd")},
            "levels": scaled,
        }
    result["method"] = (
        f"Peaks over threshold: daily losses beyond their {threshold_quantile:.0%} quantile are fitted with a "
        "generalised Pareto distribution by maximum likelihood, and VaR and expected shortfall come from its "
        "tail formulas (McNeil and Frey 2000). This reaches further into the tail than a plain historical "
        "estimate, which only has a handful of very bad days to go on."
        + (" The conditional figures fit the same tail to returns divided by their GARCH volatility and scale "
           "it to tomorrow's forecast, so a calm or stormy market shows up." if conditional else "")
    )
    result["disclaimer"] = DISCLAIMER
    return result


# Breach tests -------------------------------------------------------------------------


def _breach_sequence(breaches: Sequence[int | bool]) -> list[int]:
    sequence = []
    for value in breaches:
        if value in (0, 1):                     # True and False compare equal to 1 and 0
            sequence.append(int(value))
        else:
            raise ModellingInputError("Breaches must be a sequence of 0 (no breach) and 1 (breach).")
    return sequence


def christoffersen_test(breaches: Sequence[int | bool], *, level: float = 0.99) -> dict[str, Any]:
    """Christoffersen (1998) tests on a 0/1 sequence of VaR breaches.

    * Unconditional coverage (Kupiec 1995): is the breach rate 1 - ``level``?
    * Independence: is a breach as likely after a breach as after a calm day? A
      first-order Markov chain against independence, chi-square with 1 degree of freedom.
    * Conditional coverage: both at once, LR_cc = LR_uc + LR_ind, chi-square with 2.

    As is usual (Christoffersen 2012, Elements of Financial Risk Management), LR_uc
    uses every day and LR_ind the T - 1 day-to-day transitions.
    """

    _check_level(level)
    sequence = _breach_sequence(breaches)
    observations = len(sequence)
    if observations < 2:
        raise ModellingInputError("The independence test needs at least two days of breach history.")
    expected_rate = 1.0 - level
    hits = sum(sequence)
    counts = {"n00": 0, "n01": 0, "n10": 0, "n11": 0}
    for previous, current in zip(sequence, sequence[1:]):
        counts[f"n{previous}{current}"] += 1
    n00, n01, n10, n11 = counts["n00"], counts["n01"], counts["n10"], counts["n11"]
    rate = hits / observations
    lr_uc = -2.0 * (_bernoulli_loglik(observations - hits, hits, expected_rate)
                    - _bernoulli_loglik(observations - hits, hits, rate))
    after_calm = n01 / (n00 + n01) if n00 + n01 else 0.0
    after_breach = n11 / (n10 + n11) if n10 + n11 else 0.0
    pooled = (n01 + n11) / (observations - 1)
    lr_ind = -2.0 * (_bernoulli_loglik(n00 + n10, n01 + n11, pooled)
                     - _bernoulli_loglik(n00, n01, after_calm) - _bernoulli_loglik(n10, n11, after_breach))
    lr_uc, lr_ind = max(lr_uc, 0.0), max(lr_ind, 0.0)
    lr_cc = lr_uc + lr_ind
    p_ind = _chi_square_tail(lr_ind, 1)
    clustered = p_ind < 0.05 and after_breach > after_calm
    return {
        "observations": observations,
        "breaches": hits,
        "expected_rate": expected_rate,
        "breach_rate": rate,
        "transitions": counts,
        "breach_after_calm_rate": after_calm,
        "breach_after_breach_rate": after_breach if n10 + n11 else None,
        "unconditional_coverage": {"statistic": lr_uc, "p_value": _chi_square_tail(lr_uc, 1)},
        "independence": {"statistic": lr_ind, "p_value": p_ind},
        "conditional_coverage": {"statistic": lr_cc, "p_value": _chi_square_tail(lr_cc, 2)},
        "clustered": clustered,
        "verdict": (f"Breaches cluster: after a breach the next day broke the line {after_breach:.0%} of the time, "
                    f"against {after_calm:.0%} after a calm day, so the model reacts too slowly to stress."
                    if clustered else
                    "No sign that breaches bunch together." if p_ind >= 0.05 else
                    "Breaches are spread out more evenly than chance would give, which is unusual but harmless."),
        "method": ("Christoffersen (1998) likelihood-ratio tests: unconditional coverage (chi-square, 1 degree "
                   "of freedom), independence of each day's breach from the day before (1), and conditional "
                   "coverage, the two together (2)."),
    }


def traffic_light(breaches: int, observations: int, level: float = 0.99) -> dict[str, Any]:
    """Basel zone for a VaR breach count, by the binomial cumulative probability.

    Green while P(X <= breaches) is below 95%, red from 99.99%, amber between (Basel
    Committee 1996). At 99% over 250 days that is 0-4 green, 5-9 amber and 10 or more
    red; the plus factor added to the capital multiplier is given for that case.
    """

    _check_level(level)
    if not (isinstance(observations, int) and observations >= 1):
        raise ModellingInputError("The traffic light needs at least one day of history.")
    if not (isinstance(breaches, int) and 0 <= breaches <= observations):
        raise ModellingInputError("The breach count must be a whole number between 0 and the number of days.")
    probability = 1.0 - level
    cumulative = _binomial_cdf(breaches, observations, probability)
    zone = "green" if cumulative < BASEL_GREEN_BELOW else "red" if cumulative >= BASEL_RED_FROM else "amber"
    green_up_to, red_from, running = -1, None, 0.0
    log_p, log_q, base = math.log(probability), math.log1p(-probability), math.lgamma(observations + 1)
    for k in range(observations + 1):
        running += math.exp(base - math.lgamma(k + 1) - math.lgamma(observations - k + 1)
                            + k * log_p + (observations - k) * log_q)
        if running < BASEL_GREEN_BELOW:
            green_up_to = k
        if running >= BASEL_RED_FROM:
            red_from = k
            break
    basel_case = observations == 250 and abs(level - 0.99) < 1e-12
    return {
        "zone": zone,
        "breaches": breaches,
        "observations": observations,
        "level": level,
        "expected_breaches": observations * probability,
        "cumulative_probability": cumulative,
        "green_up_to": green_up_to if green_up_to >= 0 else None,
        "red_from": red_from,
        "plus_factor": (0.0 if zone == "green" else 1.0 if zone == "red" else BASEL_PLUS_FACTORS[breaches])
        if basel_case else None,
        "method": ("Basel traffic light (Basel Committee 1996): the chance of seeing this many breaches or fewer if "
                   "the model were right. Green below 95%, red at 99.99% or more, amber between."),
    }


# Expected shortfall backtest ----------------------------------------------------------


def _es_null_draw(var: Sequence[float], es: Sequence[float], alpha: float, shape: float,
                  generator: random.Random) -> float:
    """One Z2 under the null: breaches Bernoulli(alpha), loss beyond VaR GPD with mean ES - VaR."""

    days, log_calm = len(var), math.log1p(-alpha)
    total, t = 0.0, -1
    while True:
        # Days to the next breach are geometric, so only the breaches are drawn.
        t += 1 + int(math.log(1.0 - generator.random()) / log_calm)
        if t >= days:
            break
        scale = (1.0 - shape) * (es[t] - var[t])
        u = 1.0 - generator.random()
        excess = -scale * math.log(u) if abs(shape) < EXPONENTIAL_LIMIT else scale / shape * math.expm1(-shape * math.log(u))
        total -= (var[t] + excess) / es[t]
    return total / (days * alpha) + 1.0


def es_backtest(returns: Sequence[float], var_forecasts: Sequence[float], es_forecasts: Sequence[float],
                level: float, *, simulations: int = ES_SIMULATIONS, tail_shape: float = 0.0) -> dict[str, Any]:
    """Acerbi and Szekely (2014) Z2 test of expected shortfall forecasts.

    Z2 = Σ_t r_t·I_t / (T·α·ES_t) + 1, with α = 1 - ``level`` and I_t = 1 when the
    return broke the VaR line. Its expected value is 0 when the forecasts are right and
    it turns negative when the expected shortfall is too low. Its spread shrinks as the
    window grows, so the published thresholds (-0.70 amber, -1.8 red) are used only where
    they were calibrated, 250 days at 97.5%; anywhere else the zone comes from the
    simulated p-value against the same 5% and 0.01% significance levels.

    The p-value is the share of ``simulations`` Z2 draws under the null that are as low
    as the observed one, (1 + count) / (1 + simulations). The null needs a shape for the
    loss beyond VaR; it is a generalised Pareto with ``tail_shape`` (0, the exponential,
    by default) and the mean that makes each day's expected shortfall exactly right.
    Draws are seeded from the inputs, so the answer is repeatable. The p-value can only
    reach the red cut-off with at least 10,000 simulations.
    """

    _check_level(level)
    days = len(returns)
    if not days or len(var_forecasts) != days or len(es_forecasts) != days:
        raise ModellingInputError("Returns, VaR and expected shortfall must be given for the same days.")
    if not -0.5 < tail_shape < 1.0:
        raise ModellingInputError("The tail shape must be between -0.5 and 1.")
    if simulations < 100:
        raise ModellingInputError("Use at least 100 simulations for the p-value.")
    returns, var, es = ([float(v) for v in series] for series in (returns, var_forecasts, es_forecasts))
    if not all(math.isfinite(v) for v in (*returns, *var, *es)):
        raise ModellingInputError("Returns, VaR and expected shortfall must all be numbers.")
    if any(e <= 0 or e < v - 1e-12 for v, e in zip(var, es)):
        raise ModellingInputError("Each expected shortfall must be positive and at least as large as its VaR.")
    alpha = 1.0 - level
    hit_days = [t for t in range(days) if returns[t] < -var[t]]
    z2 = math.fsum(returns[t] / es[t] for t in hit_days) / (days * alpha) + 1.0
    generator = random.Random(_seed(returns, var, es, level, simulations, tail_shape, "es-backtest"))
    lower = sum(1 for _ in range(simulations) if _es_null_draw(var, es, alpha, tail_shape, generator) <= z2)
    p_value = (1 + lower) / (1 + simulations)
    published = abs(level - 0.975) < 1e-9 and days == 250
    if published:
        zone = "green" if z2 > Z2_AMBER else "red" if z2 <= Z2_RED else "amber"
    else:
        zone = "green" if p_value >= 1.0 - BASEL_GREEN_BELOW else "red" if p_value <= 1.0 - BASEL_RED_FROM else "amber"
    ratios = [-returns[t] / es[t] for t in hit_days]
    return {
        "level": level,
        "observations": days,
        "breaches": len(hit_days),
        "expected_breaches": days * alpha,
        "z2": z2,
        "p_value": p_value,
        "simulations": simulations,
        "null_tail_shape": tail_shape,
        "zone": zone,
        "zone_basis": "published thresholds" if published else "simulated p-value",
        "thresholds": {"amber": Z2_AMBER, "red": Z2_RED, "source": Z2_SOURCE},
        "average_loss_to_es_on_breach_days": statistics.fmean(ratios) if ratios else None,
        "verdict": ("Losses on breach days were about as large as the expected shortfall said."
                    if zone == "green" else
                    "Losses on breach days, or their number, ran beyond what the expected shortfall allowed for, "
                    + ("well past chance: treat it as too low." if zone == "red" else "more than chance usually gives.")),
        "method": ("Acerbi and Szekely (2014) Z2: realised losses on VaR breach days against the expected shortfall, "
                   "scaled so 0 means the forecasts were right and negative means expected shortfall was too low. "
                   f"The p-value comes from {simulations:,} simulated histories in which each forecast is exactly right."),
    }


# Walk-forward report ------------------------------------------------------------------


def _clean_daily(daily: Sequence[tuple[str, float | None]]) -> tuple[list[str], list[float], int]:
    dates, returns, skipped = [], [], 0
    for day, value in daily:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            skipped += 1
            continue
        if not math.isfinite(float(value)):
            raise ModellingInputError(f"The return on {day} is infinite.")
        dates.append(str(day)[:10])
        returns.append(float(value))
    return dates, returns, skipped


def tail_backtest_report(daily: Sequence[tuple[str, float | None]], *, training: int = BACKTEST_TRAINING,
                         refit_every: int = REFIT_EVERY, levels: Sequence[float] = BACKTEST_LEVELS,
                         simulations: int = ES_SIMULATIONS) -> dict[str, Any]:
    """Walk-forward test of historical-simulation VaR and expected shortfall.

    ``daily`` is (ISO date, daily return) pairs in date order; missing returns are
    skipped and counted. Each day's VaR and expected shortfall use only the returns
    before it (an expanding window from the first ``training`` days), refitted every
    ``refit_every`` sessions. Each level then gets the Kupiec, Christoffersen and Z2
    tests and a traffic light over the whole test period. ``basel`` is the traffic light
    regulators use, VaR at 99% over the last 250 days, and ``basel_expected_shortfall`` the
    Z2 test at 97.5% over the same days, zoned by the published thresholds.
    """

    dates, returns, skipped = _clean_daily(daily)
    if training < 50 or refit_every < 1:
        raise ModellingInputError("Use at least 50 days of training and refit at least every day.")
    if not levels:
        raise ModellingInputError("Choose at least one confidence level.")
    for level in levels:
        _check_level(level)
    if len(returns) < training + BACKTEST_MIN_TEST:
        raise ModellingInputError(
            f"A tail backtest needs {training} days to learn from and {BACKTEST_MIN_TEST} to test, "
            f"{training + BACKTEST_MIN_TEST} in all; this history has {len(returns)}.")
    forecasts: dict[float, tuple[list[float], list[float]]] = {level: ([], []) for level in levels}
    current: dict[float, tuple[float, float]] = {}
    for t in range(training, len(returns)):
        if (t - training) % refit_every == 0:
            ordered = sorted(returns[:t])
            current = {level: _tail(ordered, level) for level in levels}
        for level in levels:
            forecasts[level][0].append(current[level][0])
            forecasts[level][1].append(current[level][1])
    tested, tested_dates = returns[training:], dates[training:]
    observations = len(tested)
    by_level, breach_flags = {}, {}
    for level in levels:
        var, es = forecasts[level]
        flags = [1 if r < -v else 0 for r, v in zip(tested, var)]
        breach_flags[level] = flags
        count = sum(flags)
        expected = observations * (1.0 - level)
        by_level[_level_key(level)] = {
            "level": level,
            "breaches": count,
            "expected_breaches": expected,
            "breach_rate": count / observations,
            "kupiec_p_value": _kupiec(count, observations, 1.0 - level),
            "christoffersen": christoffersen_test(flags, level=level),
            "traffic_light": traffic_light(count, observations, level),
            "expected_shortfall": es_backtest(tested, var, es, level, simulations=simulations),
            "recent_breaches": [d for d, f in zip(tested_dates, flags) if f][-10:],
            "verdict": f"Losses broke the {level:.1%} VaR line on {count} of {observations} days "
                       f"(about {expected:.1f} expected).",
        }
    # The regulators' windows: VaR at 99% and expected shortfall at 97.5%, over the last 250 days.
    basel = basel_es = None
    for level in levels:
        if abs(level - 0.99) < 1e-12:
            basel = traffic_light(sum(breach_flags[level][-250:]), 250, 0.99)
        if abs(level - 0.975) < 1e-12:
            basel_es = es_backtest(tested[-250:], forecasts[level][0][-250:], forecasts[level][1][-250:], level,
                                   simulations=simulations)
    series = [{"date": tested_dates[i], "return": tested[i],
               **{f"var_{_level_key(level)}": forecasts[level][0][i] for level in levels},
               **{f"es_{_level_key(level)}": forecasts[level][1][i] for level in levels}}
              for i in range(max(0, observations - 260), observations)]
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "observations": observations,
        "training_days": training,
        "refit_every": refit_every,
        "missing_skipped": skipped,
        "window_start": tested_dates[0],
        "window_end": tested_dates[-1],
        "levels": by_level,
        "basel": basel,
        "basel_expected_shortfall": basel_es,
        "series": series,
        "method": (
            "Historical simulation: each day's VaR is the matching quantile of all earlier daily returns and its "
            f"expected shortfall the average return beyond it, refitted every {refit_every} sessions after the first "
            f"{training}. The tests: Kupiec (is the number of breaches right?), Christoffersen (do breaches "
            "bunch together?), Acerbi and Szekely's Z2 (are losses beyond VaR as large as expected shortfall said?) "
            "and the Basel traffic light."
        ),
        "disclaimer": DISCLAIMER,
    }
