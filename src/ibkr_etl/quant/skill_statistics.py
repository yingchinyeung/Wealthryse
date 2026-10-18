"""Is the skill real? Statistics that tell a manager's skill from luck.

Pure standard-library functions, like :mod:`ibkr_etl.modelling`:

* :func:`stationary_bootstrap_indices` – resampled histories made of runs of consecutive
  days with random lengths (Politis and Romano 1994), so volatility clustering and
  autocorrelation survive the resampling.
* :func:`bootstrap_confidence` – a confidence range for the annualised Sharpe ratio, alpha
  against a benchmark, or information ratio, from that bootstrap, with the portfolio and
  the benchmark resampled on the same days.
* :func:`probabilistic_sharpe_ratio` – the probability that the true Sharpe ratio is above
  a given level, allowing for the length of the record, skewness and fat tails (Bailey and
  López de Prado 2012).
* :func:`deflated_sharpe_ratio` – the same probability after allowing for the number of
  strategies tried before this one was picked (Bailey and López de Prado 2014).
* :func:`minimum_track_record_length` – how long a record at this Sharpe ratio must be before
  it shows, with a given confidence, that the true Sharpe ratio beats a target.
* :func:`skill_summary` – all of the above in one result, with a one-line verdict.

Annualisation: Sharpe ratios given to and reported by these functions are annualised as
the per-period Sharpe ratio × √periods_per_year (√252 for daily returns), the usual
convention, exact for independent returns. The published PSR, DSR and MinTRL formulas
work on the per-period Sharpe ratio with the per-period skewness and kurtosis, so annual
targets are divided by √periods_per_year first, and an annualised variance of trial Sharpe
ratios by periods_per_year. Alpha is annualised arithmetically (× periods_per_year).

Returns are simple daily returns: a plain sequence, or (ISO date, return) pairs or a
date -> return mapping, which are then matched to the benchmark by date. Missing or
non-numeric values are rejected, not skipped. Every resampling is seeded from its inputs,
so results are reproducible.
"""
from __future__ import annotations

import math
import random
import statistics
from itertools import accumulate
from statistics import NormalDist
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..modelling import DISCLAIMER, TRADING_DAYS, ModellingInputError, _quantile, _seed

MODEL_VERSION = "quant-skill-statistics-v1"
MIN_OBSERVATIONS = 20
MEAN_BLOCK = 10.0            # average run of consecutive days in a resample: about two weeks
BOOTSTRAP_SAMPLES = 2000
CONFIDENCE_LEVEL = 0.90
SKILL_CONFIDENCE = 0.95      # the published convention for PSR, DSR and MinTRL
EULER_MASCHERONI = 0.5772156649015329
STATISTICS = {
    "sharpe_ratio": "Sharpe ratio (annualised)",
    "alpha": "Alpha against the benchmark (annualised)",
    "information_ratio": "Information ratio (annualised)",
}
_NORMAL = NormalDist()


# Inputs ---------------------------------------------------------------------------


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


def _daily(values: Iterable[Any], what: str) -> tuple[list[str] | None, list[float]]:
    """A plain sequence of returns, (date, return) pairs or a date -> return mapping: (dates or None, returns)."""

    items = sorted(values.items()) if isinstance(values, Mapping) else list(values)
    dated = [isinstance(item, (tuple, list)) for item in items]
    if any(dated) and not all(dated):
        raise ModellingInputError(f"The {what}s mix plain numbers and (date, return) pairs.")
    if items and all(dated):
        if any(len(item) != 2 for item in items):
            raise ModellingInputError(f"Each dated {what} must be a (date, return) pair.")
        dates = [str(day)[:10] for day, _ in items]
        if len(set(dates)) != len(dates):
            raise ModellingInputError(f"The {what}s have the same date more than once.")
        values = [_number(v, f"The {what} on {d}") for d, (_, v) in zip(dates, items)]
        return dates, values
    return None, [_number(v, f"The {what} at position {i}") for i, v in enumerate(items, start=1)]


def _align(
    portfolio: Iterable[Any], benchmark: Iterable[Any] | None
) -> tuple[list[str] | None, list[float], list[float] | None]:
    dates, values = _daily(portfolio, "portfolio return")
    if benchmark is None:
        return dates, values, None
    benchmark_dates, benchmark_values = _daily(benchmark, "benchmark return")
    if (dates is None) != (benchmark_dates is None):
        raise ModellingInputError("Give the portfolio and the benchmark both with dates or both without.")
    if dates is None:
        if len(values) != len(benchmark_values):
            raise ModellingInputError(
                f"The portfolio has {len(values)} returns and the benchmark {len(benchmark_values)}; they must match."
            )
        return None, values, benchmark_values
    lookup = dict(zip(benchmark_dates, benchmark_values))
    rows = sorted((d, v, lookup[d]) for d, v in zip(dates, values) if d in lookup)
    return [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]


def _enough(values: Sequence[float]) -> None:
    if len(values) < MIN_OBSERVATIONS:
        raise ModellingInputError(f"This needs at least {MIN_OBSERVATIONS} returns; there are {len(values)}.")


def _per_year(periods_per_year: Any) -> int:
    if isinstance(periods_per_year, bool) or not isinstance(periods_per_year, int) or periods_per_year < 1:
        raise ModellingInputError("Periods per year must be a positive whole number.")
    return periods_per_year


# The published formulas -------------------------------------------------------------


def _moments(values: Sequence[float]) -> dict[str, float]:
    """Mean, sample standard deviation (n − 1), skewness and kurtosis (not excess; 3 if normal).

    Skewness and kurtosis are the moment ratios of the sample itself (1/n central moments),
    as in Bailey and López de Prado's own code; the Sharpe ratio uses the n − 1 standard deviation.
    """

    n = len(values)
    mean = math.fsum(values) / n
    central = [v - mean for v in values]
    m2 = math.fsum(d * d for d in central) / n
    if m2 <= 0:
        raise ModellingInputError("The returns do not vary, so a Sharpe ratio cannot be measured.")
    m3 = math.fsum(d ** 3 for d in central) / n
    m4 = math.fsum(d ** 4 for d in central) / n
    return {"observations": n, "mean": mean, "sd": math.sqrt(m2 * n / (n - 1)),
            "skewness": m3 / m2 ** 1.5, "kurtosis": m4 / (m2 * m2)}


def _spread(sharpe: float, skewness: float, kurtosis: float) -> float:
    """1 − γ3·SR + (γ4 − 1)/4·SR²: the non-normal variance factor of the Sharpe estimate."""

    return max(1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe, 1e-12)


def _psr(sharpe: float, target: float, observations: int, skewness: float, kurtosis: float) -> tuple[float, float]:
    """PSR(SR*) = Φ[(SR − SR*)√(n − 1) / √(1 − γ3·SR + (γ4 − 1)/4·SR²)], per-period Sharpe ratios."""

    z = (sharpe - target) * math.sqrt(observations - 1) / math.sqrt(_spread(sharpe, skewness, kurtosis))
    return _NORMAL.cdf(z), z


def _expected_maximum_sharpe(trials: int, variance: float) -> float:
    """E[max SR] of N skill-less trials: √V[(1 − γ)Φ⁻¹(1 − 1/N) + γΦ⁻¹(1 − 1/(Ne))], γ Euler-Mascheroni.

    One trial has nothing to pick from, so its expected maximum is the mean, 0.
    """

    if trials <= 1 or variance <= 0:
        return 0.0
    return math.sqrt(variance) * ((1.0 - EULER_MASCHERONI) * _NORMAL.inv_cdf(1.0 - 1.0 / trials)
                                  + EULER_MASCHERONI * _NORMAL.inv_cdf(1.0 - 1.0 / (trials * math.e)))


def _minimum_track_record(sharpe: float, target: float, skewness: float, kurtosis: float,
                          confidence: float) -> float | None:
    """MinTRL = 1 + (1 − γ3·SR + (γ4 − 1)/4·SR²)(Φ⁻¹(confidence) / (SR − SR*))²; None if SR ≤ SR*."""

    if sharpe <= target:
        return None
    return 1.0 + _spread(sharpe, skewness, kurtosis) * (_NORMAL.inv_cdf(confidence) / (sharpe - target)) ** 2


def _sharpe_inputs(returns: Iterable[Any], periods_per_year: Any) -> tuple[list[float], dict[str, float], float, int]:
    _, values = _daily(returns, "return")
    _enough(values)
    per_year = _per_year(periods_per_year)
    moments = _moments(values)
    return values, moments, moments["mean"] / moments["sd"], per_year


def _sharpe_description(moments: dict[str, float], sharpe: float, per_year: int) -> dict[str, Any]:
    return {"observations": moments["observations"], "sharpe_ratio": sharpe,
            "sharpe_ratio_annual": sharpe * math.sqrt(per_year), "skewness": moments["skewness"],
            "kurtosis": moments["kurtosis"], "periods_per_year": per_year}


def probabilistic_sharpe_ratio(
    returns: Iterable[Any], benchmark_sharpe: float = 0.0, *, periods_per_year: int = TRADING_DAYS
) -> dict[str, Any]:
    """Probability that the true Sharpe ratio exceeds ``benchmark_sharpe`` (Bailey and López de Prado 2012).

    PSR = Φ[(SR − SR*)√(n − 1) / √(1 − γ3·SR + (γ4 − 1)/4·SR²)] on per-period values: SR is the
    mean over the n − 1 standard deviation of ``returns`` (pass excess returns for a Sharpe
    ratio over cash), γ3 the skewness and γ4 the kurtosis. ``benchmark_sharpe`` is annualised and
    divided by √periods_per_year; ``sharpe_ratio`` in the result is per period and
    ``sharpe_ratio_annual`` annualised.
    """

    _, moments, sharpe, per_year = _sharpe_inputs(returns, periods_per_year)
    target = _number(benchmark_sharpe, "The benchmark Sharpe ratio") / math.sqrt(per_year)
    probability, z = _psr(sharpe, target, moments["observations"], moments["skewness"], moments["kurtosis"])
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "probabilistic_sharpe_ratio": probability,
        "z_score": z,
        "benchmark_sharpe_annual": target * math.sqrt(per_year),
        **_sharpe_description(moments, sharpe, per_year),
        "method": (
            f"Probabilistic Sharpe ratio (Bailey and López de Prado 2012) from {moments['observations']} returns: "
            "the chance the true Sharpe ratio is above the benchmark level, allowing for the length of the "
            "record, skewness and fat tails."
        ),
        "disclaimer": DISCLAIMER,
    }


def deflated_sharpe_ratio(
    returns: Iterable[Any], *, trials: int, trial_sharpe_variance: float, periods_per_year: int = TRADING_DAYS
) -> dict[str, Any]:
    """PSR against the Sharpe ratio the best of ``trials`` skill-less strategies would show.

    Bailey and López de Prado (2014): SR0 = √V[(1 − γ)Φ⁻¹(1 − 1/N) + γΦ⁻¹(1 − 1/(Ne))], with N
    trials, V the variance of their Sharpe ratios and γ the Euler-Mascheroni constant; then
    DSR = PSR(SR0). ``trial_sharpe_variance`` is the variance of the trials' annualised Sharpe
    ratios (divided by periods_per_year to put it per period). With one trial SR0 = 0 and
    DSR equals PSR against zero.
    """

    _, moments, sharpe, per_year = _sharpe_inputs(returns, periods_per_year)
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise ModellingInputError("The number of trials must be a whole number of at least 1.")
    variance = _number(trial_sharpe_variance, "The variance of the trials' Sharpe ratios")
    if variance < 0:
        raise ModellingInputError("The variance of the trials' Sharpe ratios cannot be negative.")
    threshold = _expected_maximum_sharpe(trials, variance / per_year)
    probability, z = _psr(sharpe, threshold, moments["observations"], moments["skewness"], moments["kurtosis"])
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "deflated_sharpe_ratio": probability,
        "z_score": z,
        "trials": trials,
        "trial_sharpe_variance_annual": variance,
        "expected_maximum_sharpe_annual": threshold * math.sqrt(per_year),
        **_sharpe_description(moments, sharpe, per_year),
        "method": (
            f"Deflated Sharpe ratio (Bailey and López de Prado 2014): the probabilistic Sharpe ratio measured "
            f"against the Sharpe ratio the best of {trials} skill-less strategies would be expected to show by luck."
        ),
        "disclaimer": DISCLAIMER,
    }


def minimum_track_record_length(
    returns: Iterable[Any], target_sharpe: float = 0.0, confidence: float = SKILL_CONFIDENCE,
    *, periods_per_year: int = TRADING_DAYS,
) -> dict[str, Any]:
    """Periods of record needed to show, at ``confidence``, that the true Sharpe ratio beats the target.

    Bailey and López de Prado (2012): MinTRL = 1 + (1 − γ3·SR + (γ4 − 1)/4·SR²)(Φ⁻¹(confidence) /
    (SR − SR*))², per-period values; ``target_sharpe`` is annualised. When SR ≤ SR* no length
    of record would show it, and the length is None. ``enough_history`` is true exactly when
    the PSR against the target reaches ``confidence``.
    """

    _, moments, sharpe, per_year = _sharpe_inputs(returns, periods_per_year)
    target = _number(target_sharpe, "The target Sharpe ratio") / math.sqrt(per_year)
    level = _number(confidence, "The confidence")
    if not 0.5 <= level < 1.0:
        raise ModellingInputError("The confidence must be at least 50% and below 100%.")
    periods = _minimum_track_record(sharpe, target, moments["skewness"], moments["kurtosis"], level)
    n = moments["observations"]
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "minimum_periods": periods,
        "minimum_years": periods / per_year if periods is not None else None,
        "enough_history": periods is not None and n >= periods,
        "target_sharpe_annual": target * math.sqrt(per_year),
        "confidence": level,
        **_sharpe_description(moments, sharpe, per_year),
        "reason": None if periods is not None else (
            "The Sharpe ratio is not above the target, so no length of record would show that it beats it."
        ),
        "method": (
            "Minimum track record length (Bailey and López de Prado 2012): the record needed for the "
            f"probabilistic Sharpe ratio against the target to reach {level:.0%}, given this Sharpe ratio, "
            "skewness and kurtosis."
        ),
        "disclaimer": DISCLAIMER,
    }


# Stationary bootstrap --------------------------------------------------------------


def _blocks(length: int, mean_block: float, generator: random.Random) -> list[tuple[int, int]]:
    """One resample as (start, run) blocks: uniform starts, geometric runs with mean ``mean_block``.

    This is the Politis-Romano scheme (after each draw, start a new block with probability
    1/mean_block, else take the next day, wrapping round) drawn a block at a time.
    """

    stay = math.log1p(-1.0 / mean_block) if mean_block > 1.0 else None
    blocks, filled = [], 0
    while filled < length:
        start = int(generator.random() * length)
        run = 1 if stay is None else 1 + int(math.log(1.0 - generator.random()) / stay)
        run = min(run, length - filled)
        blocks.append((start, run))
        filled += run
    return blocks


def _check_resampling(length: int, samples: Any, mean_block: Any) -> tuple[int, float]:
    if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 100_000:
        raise ModellingInputError("The number of bootstrap samples must be a whole number from 1 to 100,000.")
    block = _number(mean_block, "The mean block length")
    if not 1.0 <= block <= max(1.0, float(length)):
        raise ModellingInputError("The mean block length must be at least 1 and no longer than the record.")
    return samples, block


def stationary_bootstrap_indices(
    length: int, *, samples: int = BOOTSTRAP_SAMPLES, mean_block: float = MEAN_BLOCK, seed: int | None = None
) -> Iterator[list[int]]:
    """Index lists for ``samples`` stationary-bootstrap resamples of a record of ``length`` (Politis and Romano 1994).

    Each resample starts at a random day and walks forward one day at a time, wrapping from
    the end of the record to the start; before each step it jumps to a new random day with
    probability 1/``mean_block``, so runs of consecutive days have random (geometric) lengths
    averaging ``mean_block``. ``mean_block`` = 1 is the ordinary independent bootstrap. With no
    ``seed`` the draws are seeded from the other arguments, so they are reproducible.
    """

    if isinstance(length, bool) or not isinstance(length, int) or length < 1:
        raise ModellingInputError("The record length must be a positive whole number.")
    samples, block = _check_resampling(length, samples, mean_block)
    generator = random.Random(_seed(length, samples, block, "stationary-bootstrap") if seed is None else seed)

    def draws() -> Iterator[list[int]]:
        for _ in range(samples):
            yield [(start + offset) % length for start, run in _blocks(length, block, generator)
                   for offset in range(run)]

    return draws()


def _resampled_sums(x: Sequence[float], y: Sequence[float] | None, *, samples: int, mean_block: float,
                    seed: int) -> Iterator[list[float]]:
    """For each resample, the sums of centred x, x² and (with a benchmark) y, y², xy.

    Running sums over the record laid end to end twice give each block's sums in O(1), so a
    resample costs one step per block rather than per day.
    """

    n = len(x)
    centred_x = [v - statistics.fmean(x) for v in x]
    columns = [centred_x, [v * v for v in centred_x]]
    if y is not None:
        centred_y = [v - statistics.fmean(y) for v in y]
        columns += [centred_y, [v * v for v in centred_y], [a * b for a, b in zip(centred_x, centred_y)]]
    running = [list(accumulate(column + column, initial=0.0)) for column in columns]
    generator = random.Random(seed)
    for _ in range(samples):
        blocks = _blocks(n, mean_block, generator)
        yield [sum(r[start + run] - r[start] for start, run in blocks) for r in running]


def _from_sums(n: int, sums: Sequence[float], centres: tuple[float, float], per_year: int,
               floors: tuple[float, float, float]) -> dict[str, float | None]:
    sx, sxx = sums[0], sums[1]
    mean_x = centres[0] + sx / n
    var_x = (sxx - sx * sx / n) / (n - 1)
    root = math.sqrt(per_year)
    result: dict[str, float | None] = {"sharpe_ratio": mean_x / math.sqrt(var_x) * root if var_x > floors[0] else None}
    if len(sums) == 2:
        return result
    sy, syy, sxy = sums[2:]
    mean_y = centres[1] + sy / n
    var_y = (syy - sy * sy / n) / (n - 1)
    cov = (sxy - sx * sy / n) / (n - 1)
    result["alpha"] = (mean_x - cov / var_y * mean_y) * per_year if var_y > floors[1] else None
    var_d = var_x + var_y - 2.0 * cov
    result["information_ratio"] = (mean_x - mean_y) / math.sqrt(var_d) * root if var_d > floors[2] else None
    return result


def _estimates(x: Sequence[float], y: Sequence[float] | None, per_year: int) -> dict[str, float | None]:
    """Point estimates on the record itself; x and y are returns over cash."""

    root = math.sqrt(per_year)
    sd = statistics.stdev(x)
    result: dict[str, float | None] = {"sharpe_ratio": statistics.fmean(x) / sd * root if sd > 0 else None}
    if y is None:
        return result
    var_y = statistics.variance(y)
    beta = statistics.covariance(x, y) / var_y if var_y > 0 else None
    difference = [a - b for a, b in zip(x, y)]
    tracking = statistics.stdev(difference)
    result.update({
        "alpha": (statistics.fmean(x) - beta * statistics.fmean(y)) * per_year if beta is not None else None,
        "beta": beta,
        "information_ratio": statistics.fmean(difference) / tracking * root if tracking > 0 else None,
        "tracking_error": tracking * root,
        "excess_return_annual": statistics.fmean(difference) * per_year,
    })
    return result


def _bootstrap(x: Sequence[float], y: Sequence[float] | None, *, samples: int, mean_block: float,
               per_year: int) -> dict[str, list[float]]:
    """Resampled values of every statistic; the seed depends only on the data and the settings."""

    n = len(x)
    names = ["sharpe_ratio"] + (["alpha", "information_ratio"] if y is not None else [])
    centres = (statistics.fmean(x), statistics.fmean(y) if y is not None else 0.0)
    var_x = statistics.variance(x)
    var_y = statistics.variance(y) if y is not None else 0.0
    var_d = statistics.variance([a - b for a, b in zip(x, y)]) if y is not None else 0.0
    floors = (1e-12 * var_x, 1e-12 * var_y, 1e-12 * var_d)
    seed = _seed(list(x), list(y) if y is not None else [], samples, mean_block, "skill-bootstrap")
    draws: dict[str, list[float]] = {name: [] for name in names}
    for sums in _resampled_sums(x, y, samples=samples, mean_block=mean_block, seed=seed):
        values = _from_sums(n, sums, centres, per_year, floors)
        for name in names:
            if values[name] is not None:
                draws[name].append(values[name])
    return draws


def _interval(estimate: float | None, draws: Sequence[float], level: float) -> dict[str, Any]:
    if estimate is None or not draws:
        return {"estimate": estimate, "low": None, "high": None, "standard_error": None,
                "probability_positive": None, "samples_used": len(draws)}
    ordered = sorted(draws)
    tail = (1.0 - level) / 2.0
    return {
        "estimate": estimate,
        "low": _quantile(ordered, tail),
        "high": _quantile(ordered, 1.0 - tail),
        "standard_error": statistics.stdev(ordered) if len(ordered) > 1 else None,
        "probability_positive": sum(1 for v in ordered if v > 0) / len(ordered),
        "samples_used": len(ordered),
    }


def _check_level(level: Any) -> float:
    value = _number(level, "The confidence level")
    if not 0.5 <= value < 1.0:
        raise ModellingInputError("The confidence level must be at least 50% and below 100%.")
    return value


def _bootstrap_method(samples: int, mean_block: float, level: float, with_benchmark: bool) -> str:
    return (
        f"Stationary bootstrap (Politis and Romano 1994): {samples:,} resampled histories built from runs of "
        f"consecutive days of random length averaging {mean_block:g} days, wrapping round the end of the record"
        + (", with portfolio and benchmark taken from the same days" if with_benchmark else "")
        + f". The range is the middle {level:.0%} of the resampled values (percentile method)."
    )


def bootstrap_confidence(
    returns: Iterable[Any],
    statistic: str,
    *,
    benchmark: Iterable[Any] | None = None,
    level: float = CONFIDENCE_LEVEL,
    samples: int = BOOTSTRAP_SAMPLES,
    risk_free_daily: float = 0.0,
    mean_block: float = MEAN_BLOCK,
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, Any]:
    """Point estimate and stationary-bootstrap confidence range for one statistic.

    ``statistic`` is ``"sharpe_ratio"`` (annualised mean over standard deviation of returns
    less ``risk_free_daily``), ``"alpha"`` (annualised intercept of an OLS regression of the
    portfolio's returns over cash on the benchmark's) or ``"information_ratio"`` (annualised
    mean over standard deviation of portfolio less benchmark returns, as on the Performance
    page). Alpha and the information ratio need ``benchmark``; portfolio and benchmark are
    resampled on the same days. The range is the percentile interval at ``level``;
    ``probability_positive`` is the share of resamples above zero.
    """

    if statistic not in STATISTICS:
        raise ModellingInputError(f"Choose one of: {', '.join(STATISTICS)}.")
    if statistic != "sharpe_ratio" and benchmark is None:
        raise ModellingInputError(f"The {STATISTICS[statistic].lower()} needs a benchmark.")
    dates, portfolio, bench = _align(returns, benchmark)
    _enough(portfolio)
    per_year = _per_year(periods_per_year)
    confidence = _check_level(level)
    samples, block = _check_resampling(len(portfolio), samples, mean_block)
    cash = _number(risk_free_daily, "The daily risk-free rate")
    x = [v - cash for v in portfolio]
    y = [v - cash for v in bench] if bench is not None else None
    estimate = _estimates(x, y, per_year)[statistic]
    if estimate is None:
        raise ModellingInputError(f"The {STATISTICS[statistic].lower()} is undefined: the returns do not vary.")
    draws = _bootstrap(x, y, samples=samples, mean_block=block, per_year=per_year)[statistic]
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "statistic": statistic,
        "label": STATISTICS[statistic],
        "level": confidence,
        "observations": len(x),
        "window_start": dates[0] if dates else None,
        "window_end": dates[-1] if dates else None,
        "samples": samples,
        "mean_block": block,
        **_interval(estimate, draws, confidence),
        "method": _bootstrap_method(samples, block, confidence, y is not None),
        "disclaimer": DISCLAIMER,
    }


# Summary ----------------------------------------------------------------------------


def _verdict(*, sharpe: float, psr: float, dsr: float, trials: int, observations: int,
             minimum_days: float | None, alpha: dict[str, Any] | None) -> tuple[str, str]:
    alpha_low = alpha["low"] if alpha else None
    alpha_high = alpha["high"] if alpha else None
    if sharpe <= 0:
        if alpha_low is not None and alpha_low > 0:
            return "no_skill", ("Returns have not beaten cash for their risk, though the portfolio did better than "
                                "its benchmark by more than luck usually explains.")
        return "no_skill", "No sign of skill: the portfolio has not earned more than cash for the risk it took."
    if psr < SKILL_CONFIDENCE:
        return "too_short", (f"Not enough history to tell skill from luck: at this Sharpe ratio it takes about "
                             f"{minimum_days / TRADING_DAYS:,.1f} years of record, and there are "
                             f"{observations / TRADING_DAYS:.1f}.")
    if dsr < SKILL_CONFIDENCE:
        return "not_after_trials", (f"The Sharpe ratio would count as real for a single strategy, but not once "
                                    f"the {trials} strategies tried are allowed for.")
    if alpha_low is not None and alpha_low <= 0:
        if alpha_high is not None and alpha_high < 0:
            return "behind_benchmark", ("Returns beat cash reliably for their risk, but trailed the benchmark "
                                        "once its market exposure is allowed for.")
        return "benchmark_edge_unproven", ("Returns beat cash reliably for their risk, but the edge over the "
                                           "benchmark could still be luck.")
    return "skill_likely", "The record is long and steady enough that the result is unlikely to be luck alone."


def skill_summary(
    daily_portfolio: Iterable[Any],
    daily_benchmark: Iterable[Any] | None = None,
    *,
    risk_free_daily: float = 0.0,
    trials: int = 1,
    trial_sharpe_variance: float | None = None,
    level: float = CONFIDENCE_LEVEL,
    samples: int = BOOTSTRAP_SAMPLES,
) -> dict[str, Any]:
    """Point estimates, bootstrap ranges, PSR, DSR and minimum track record in one result, with a verdict.

    ``daily_portfolio`` and ``daily_benchmark`` are daily returns, plain, as (date, return)
    pairs or as a date -> return mapping (then matched by date). ``risk_free_daily`` is a
    constant daily cash return taken off both (pass excess returns yourself for a varying
    rate). PSR is against a Sharpe ratio of zero, DSR against the best of ``trials``
    strategies, and the minimum track record is for beating zero at 95%.
    ``trial_sharpe_variance`` is the variance of the trials' annualised Sharpe ratios; without
    it the trials are taken to be skill-less strategies like this one, whose Sharpe ratios
    scatter only by estimation error (variance 1/(n − 1) per period).
    The verdict uses the published 95% threshold: PSR (equivalently, the minimum track
    record), then DSR, then the alpha range when there is a benchmark.
    """

    dates, portfolio, bench = _align(daily_portfolio, daily_benchmark)
    _enough(portfolio)
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise ModellingInputError("The number of trials must be a whole number of at least 1.")
    confidence = _check_level(level)
    samples, block = _check_resampling(len(portfolio), samples, MEAN_BLOCK)
    cash = _number(risk_free_daily, "The daily risk-free rate")
    x = [v - cash for v in portfolio]
    y = [v - cash for v in bench] if bench is not None else None
    n = len(x)
    moments = _moments(x)
    sharpe = moments["mean"] / moments["sd"]
    skew, kurt = moments["skewness"], moments["kurtosis"]
    estimates = _estimates(x, y, TRADING_DAYS)
    draws = _bootstrap(x, y, samples=samples, mean_block=block, per_year=TRADING_DAYS)
    ranges = {name: _interval(estimates[name], values, confidence) for name, values in draws.items()}

    psr, _ = _psr(sharpe, 0.0, n, skew, kurt)
    if trial_sharpe_variance is None:
        variance = 1.0 / (n - 1)
    else:
        variance = _number(trial_sharpe_variance, "The variance of the trials' Sharpe ratios") / TRADING_DAYS
        if variance < 0:
            raise ModellingInputError("The variance of the trials' Sharpe ratios cannot be negative.")
    threshold = _expected_maximum_sharpe(trials, variance)
    dsr, _ = _psr(sharpe, threshold, n, skew, kurt)
    minimum = _minimum_track_record(sharpe, 0.0, skew, kurt, SKILL_CONFIDENCE)
    code, verdict = _verdict(sharpe=sharpe, psr=psr, dsr=dsr, trials=trials, observations=n,
                             minimum_days=minimum, alpha=ranges.get("alpha"))
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "observations": n,
        "years": n / TRADING_DAYS,
        "window_start": dates[0] if dates else None,
        "window_end": dates[-1] if dates else None,
        "has_benchmark": y is not None,
        "risk_free_daily": cash,
        "estimates": {**estimates, "sharpe_ratio_daily": sharpe, "skewness": skew, "kurtosis": kurt},
        "confidence_level": confidence,
        "confidence": ranges,
        "probabilistic_sharpe_ratio": psr,
        "deflated_sharpe_ratio": dsr,
        "trials": trials,
        "expected_maximum_sharpe_annual": threshold * math.sqrt(TRADING_DAYS),
        "minimum_track_record_days": minimum,
        "minimum_track_record_years": minimum / TRADING_DAYS if minimum is not None else None,
        "enough_history": minimum is not None and n >= minimum,
        "verdict_code": code,
        "verdict": verdict,
        "method": (
            "Sharpe ratio, alpha (OLS on the benchmark, both over cash) and information ratio from daily returns, "
            "annualised with 252 days. " + _bootstrap_method(samples, block, confidence, y is not None)
            + " The probabilistic Sharpe ratio (Bailey and López de Prado 2012) is the chance the true Sharpe "
            "ratio is above zero given the record's length, skewness and kurtosis; the deflated Sharpe ratio "
            f"(2014) is the same chance against the best of {trials} strategies tried; the minimum track record is "
            "the length needed for the first to reach 95%."
        ),
        "disclaimer": DISCLAIMER,
    }
