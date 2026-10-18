"""Do the forecasts deserve their ranges? Calibration checks for forward-looking figures.

Pure standard-library functions, like :mod:`ibkr_etl.modelling`. Each takes past
forecasts and what then happened:

* :func:`interval_coverage` – how often outcomes fell inside a stated range, against
  the stated level, with an exact binomial test.
* :func:`quantile_calibration` – for each forecast quantile, the share of outcomes at
  or below it, with its binomial test and the quantile (pinball) loss.
* :func:`pit_histogram` – the probability integral transform histogram and a
  chi-square test of uniformity (Dawid 1984; Diebold, Gunther and Tay 1998), with
  bias and spread read from the normal transforms (Berkowitz 2001).
* :func:`conformal_factor` and :func:`widen_interval` – split conformal widening
  (Vovk, Gammerman and Shafer 2005; Lei et al. 2018): the multiple of the forecast's
  scale that past errors say a range needs to cover the level it claims.
* :func:`probability_reliability` – the Brier score of up/down forecasts and its
  reliability, resolution and uncertainty parts (Murphy 1973).

Missing (None or NaN) items are skipped and counted, never guessed. Checks need at
least ``MIN_OBSERVATIONS`` usable items.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError

MODEL_VERSION = "quant-calibration-v1"
MIN_OBSERVATIONS = 20
SIGNIFICANCE = 0.05
NORMAL = statistics.NormalDist()


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _level(value: Any, label: str = "level") -> float:
    number = _finite(value)
    if number is None or not 0.0 < number < 1.0:
        raise ModellingInputError(f"The {label} must be above 0% and below 100%.")
    return number


def _enough(count: int, what: str) -> None:
    if count < MIN_OBSERVATIONS:
        raise ModellingInputError(f"A calibration check needs at least {MIN_OBSERVATIONS} {what}; there are {count}.")


def _same_length(*series: Sequence[Any]) -> None:
    if len({len(s) for s in series}) > 1:
        raise ModellingInputError("The forecasts and the outcomes must line up one for one.")


# Statistics ---------------------------------------------------------------------------


def binomial_p_value(successes: int, trials: int, probability: float) -> float:
    """Exact two-sided binomial test: the chance of a count at most as likely as the one seen."""

    p = min(max(probability, 1e-15), 1.0 - 1e-15)
    base = math.lgamma(trials + 1)

    def log_pmf(k: int) -> float:
        return base - math.lgamma(k + 1) - math.lgamma(trials - k + 1) + k * math.log(p) + (trials - k) * math.log1p(-p)

    observed = log_pmf(successes)
    total = math.fsum(math.exp(lp) for lp in map(log_pmf, range(trials + 1)) if lp <= observed + 1e-7)
    return min(1.0, total)


def _upper_gamma(a: float, x: float) -> float:
    """Regularised upper incomplete gamma Q(a, x): series below a + 1, continued fraction above."""

    if x <= 0:
        return 1.0
    prefix = math.exp(-x + a * math.log(x) - math.lgamma(a))
    if x < a + 1.0:
        term = total = 1.0 / a
        denominator = a
        for _ in range(10_000):
            denominator += 1.0
            term *= x / denominator
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        return min(1.0, max(0.0, 1.0 - total * prefix))
    tiny = 1e-300
    b = x + 1.0 - a
    c, d = 1.0 / tiny, 1.0 / b
    h = d
    for i in range(1, 10_000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return min(1.0, max(0.0, prefix * h))


def chi_square_survival(statistic: float, degrees_of_freedom: int) -> float:
    """P(χ² ≥ statistic) with the given degrees of freedom."""

    return _upper_gamma(degrees_of_freedom / 2.0, statistic / 2.0)


# Ranges and quantiles -------------------------------------------------------------------


def interval_coverage(
    lower: Sequence[float],
    upper: Sequence[float],
    outcomes: Sequence[float],
    *,
    level: float,
) -> dict[str, Any]:
    """Share of outcomes inside [lower, upper] against the stated level, with an exact two-sided binomial test."""

    _same_length(lower, upper, outcomes)
    stated = _level(level)
    triples, skipped = [], 0
    for low, high, outcome in zip(lower, upper, outcomes):
        values = (_finite(low), _finite(high), _finite(outcome))
        if None in values:
            skipped += 1
            continue
        if values[0] > values[1]:
            raise ModellingInputError("A forecast range has its lower end above its upper end.")
        triples.append(values)
    _enough(len(triples), "forecast ranges with an outcome")
    n = len(triples)
    below = sum(1 for low, _, y in triples if y < low)
    above = sum(1 for _, high, y in triples if y > high)
    hits = n - below - above
    p_value = binomial_p_value(hits, n, stated)
    rate = hits / n
    verdict = ("in line with the stated level" if p_value >= SIGNIFICANCE else
               "too narrow: outcomes fell outside more often than the level allows" if rate < stated else
               "wider than needed: outcomes fell outside less often than the level allows")
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "level": stated,
        "observations": n,
        "skipped": skipped,
        "hits": hits,
        "hit_rate": rate,
        "expected_hits": stated * n,
        "misses_below": below,
        "misses_above": above,
        "p_value": p_value,
        "verdict": (f"The {stated:.0%} range held the outcome {hits} times out of {n} ({rate:.0%}), "
                    f"so it has been {verdict}."),
        "method": ("Count of outcomes inside their forecast range (ends included), against the stated level, with "
                   "an exact two-sided binomial test; a p-value under 5% means the gap is unlikely to be luck. "
                   "Items with a missing value are skipped."),
    }


def quantile_calibration(quantile_forecasts: Mapping[float, Sequence[float]], outcomes: Sequence[float]) -> dict[str, Any]:
    """Per forecast level, the share of outcomes at or below the forecast quantile.

    ``quantile_forecasts`` maps each level (for example 0.05, 0.5, 0.95) to that
    quantile's forecasts, one per outcome. For a calibrated forecast the share at or
    below the 5% quantile is about 5%. Also gives each level's binomial p-value and
    mean quantile (pinball) loss, and counts outcomes whose forecast quantiles cross.
    """

    if not quantile_forecasts:
        raise ModellingInputError("No quantile forecasts were given.")
    levels = sorted((_level(level, "quantile level"), level) for level in quantile_forecasts)
    for _, key in levels:
        _same_length(quantile_forecasts[key], outcomes)
    rows = []
    for level, key in levels:
        pairs = [(q, y) for q, y in ((_finite(a), _finite(b)) for a, b in zip(quantile_forecasts[key], outcomes))
                 if q is not None and y is not None]
        _enough(len(pairs), f"forecasts with an outcome at the {level:.0%} level")
        below = sum(1 for q, y in pairs if y <= q)
        rows.append({
            "level": level,
            "observations": len(pairs),
            "skipped": len(outcomes) - len(pairs),
            "share_at_or_below": below / len(pairs),
            "p_value": binomial_p_value(below, len(pairs), level),
            "pinball_loss": statistics.fmean((level - (1.0 if y < q else 0.0)) * (y - q) for q, y in pairs),
        })
    crossings = 0
    for index in range(len(outcomes)):
        values = [_finite(quantile_forecasts[key][index]) for _, key in levels]
        if None not in values and any(b < a for a, b in zip(values, values[1:])):
            crossings += 1
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "levels": rows,
        "mean_absolute_error": statistics.fmean(abs(r["share_at_or_below"] - r["level"]) for r in rows),
        "crossings": crossings,
        "method": ("For each forecast quantile, the share of outcomes at or below it, which should match the level; "
                   "an exact two-sided binomial test says whether the gap could be luck. Pinball loss is the average "
                   "quantile loss (lower is better). Crossings counts outcomes whose forecast quantiles are out of order."),
    }


def pit_histogram(cdf_values: Sequence[float], bins: int = 10) -> dict[str, Any]:
    """Histogram of probability integral transform values F_t(y_t) and a chi-square test of uniformity.

    Calibrated forecasts give a flat histogram. A U shape means outcomes land in the
    tails too often, so the ranges are too narrow; a hump means they are too wide; a
    tilt means outcomes run above or below the forecast. Bias and spread are judged on
    the normal transforms z = Φ⁻¹(u) (Berkowitz 2001), which should have mean 0 and
    variance 1, so a shifted forecast is not mistaken for one that is too wide.
    """

    if isinstance(bins, bool) or not isinstance(bins, int) or not 2 <= bins <= 100:
        raise ModellingInputError("Choose between 2 and 100 bins.")
    values, skipped = [], 0
    for raw in cdf_values:
        value = _finite(raw)
        if value is None:
            skipped += 1
            continue
        if not -1e-12 <= value <= 1.0 + 1e-12:
            raise ModellingInputError("Probability integral transform values must lie between 0 and 1.")
        values.append(min(1.0, max(0.0, value)))
    _enough(len(values), "transformed outcomes")
    n = len(values)
    if n < 5 * bins:
        raise ModellingInputError(f"The chi-square test needs about 5 values per bin: at least {5 * bins} for {bins} bins.")
    counts = [0] * bins
    for value in values:
        counts[min(int(value * bins), bins - 1)] += 1
    expected = n / bins
    statistic = sum((c - expected) ** 2 / expected for c in counts)
    p_value = chi_square_survival(statistic, bins - 1)
    normal = [NORMAL.inv_cdf(min(max(v, 1e-12), 1.0 - 1e-12)) for v in values]
    normal_mean, normal_variance = statistics.fmean(normal), statistics.variance(normal)
    bias_z = normal_mean * math.sqrt(n)
    spread_z = (normal_variance - 1.0) / math.sqrt(2.0 / (n - 1))
    findings = []
    if spread_z > 2:
        findings.append("outcomes land in the tails too often, so the ranges are too narrow")
    elif spread_z < -2:
        findings.append("outcomes cluster in the middle, so the ranges are wider than needed")
    if abs(bias_z) > 2:
        findings.append("outcomes tend to come in " + ("above" if bias_z > 0 else "below") + " the forecast centre")
    verdict = ("The histogram is consistent with calibrated forecasts." if p_value >= SIGNIFICANCE and not findings else
               "The forecasts look miscalibrated: " + ("; ".join(findings) if findings else
                                                      "the histogram is uneven in a way that is unlikely to be luck") + ".")
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "observations": n,
        "skipped": skipped,
        "bins": [{"from": i / bins, "to": (i + 1) / bins, "count": c, "share": c / n} for i, c in enumerate(counts)],
        "chi_square": statistic,
        "degrees_of_freedom": bins - 1,
        "p_value": p_value,
        "mean": statistics.fmean(values),
        "variance": statistics.pvariance(values),
        "normal_mean": normal_mean,
        "normal_variance": normal_variance,
        "verdict": verdict,
        "method": ("Each outcome's place in its forecast distribution (0 = lowest, 1 = highest), counted in equal bins. "
                   "Calibrated forecasts give a flat histogram; the chi-square test (bins − 1 degrees of freedom) says "
                   "whether the unevenness could be luck. Bias and spread are read from the values' normal transforms "
                   "(Berkowitz 2001): mean away from 0 means outcomes run above or below the forecast, variance above 1 "
                   "means ranges too narrow, below 1 too wide, each flagged beyond two standard errors."),
    }


# Conformal widening ------------------------------------------------------------------------


def nonconformity_scores(centres: Sequence[float], scales: Sequence[float], outcomes: Sequence[float]) -> list[float]:
    """|outcome − centre| / scale for each past forecast; items with a missing value or a scale of zero are skipped."""

    _same_length(centres, scales, outcomes)
    scores = []
    for centre, scale, outcome in zip(centres, scales, outcomes):
        c, s, y = _finite(centre), _finite(scale), _finite(outcome)
        if c is None or s is None or y is None or s <= 0:
            continue
        scores.append(abs(y - c) / s)
    return scores


def conformal_factor(scores: Sequence[float], *, level: float, nominal_factor: float | None = None) -> dict[str, Any]:
    """Split conformal multiple of the forecast scale (Vovk et al. 2005; Lei et al. 2018).

    ``scores`` are past nonconformity scores |outcome − centre| / scale (see
    :func:`nonconformity_scores`). The factor is the ⌈(n + 1)·level⌉-th smallest, so
    centre ± factor × scale covers a new outcome with probability at least ``level``
    when past and future errors are exchangeable. ``nominal_factor`` (for example 1.645
    for a normal 90% range) reports how much the stated range needs widening.
    """

    stated = _level(level)
    clean, skipped = [], 0
    for raw in scores:
        value = _finite(raw)
        if value is None:
            skipped += 1
            continue
        if value < 0:
            raise ModellingInputError("Nonconformity scores are distances and cannot be negative.")
        clean.append(value)
    n = len(clean)
    rank = math.ceil((n + 1) * stated - 1e-9)
    if rank > n:
        needed = math.ceil(stated / (1.0 - stated) - 1e-9)
        raise ModellingInputError(f"A {stated:.0%} conformal range needs at least {needed} past forecasts with outcomes; "
                                  f"there are {n}.")
    factor = sorted(clean)[rank - 1]
    nominal = _finite(nominal_factor) if nominal_factor is not None else None
    if nominal_factor is not None and (nominal is None or nominal <= 0):
        raise ModellingInputError("The nominal factor must be a positive number.")
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "level": stated,
        "observations": n,
        "skipped": skipped,
        "rank": rank,
        "factor": factor,
        "nominal_factor": nominal,
        "widening": factor / nominal - 1.0 if nominal else None,
        "method": (f"Split conformal prediction: past errors, each divided by the forecast's own scale, are sorted and "
                   f"the {rank}th smallest of {n} becomes the multiple of the scale a {stated:.0%} range needs. If past "
                   "and future errors behave alike, the range holds the outcome at least that often."),
        "disclaimer": DISCLAIMER,
    }


def widen_interval(centre: float, scale: float, factor: float) -> tuple[float, float]:
    """The range centre ± factor × scale."""

    c, s, f = _finite(centre), _finite(scale), _finite(factor)
    if c is None or s is None or f is None or s < 0 or f < 0:
        raise ModellingInputError("A range needs a centre, and a scale and factor of zero or more.")
    return c - f * s, c + f * s


# Up/down probabilities -------------------------------------------------------------------


def _event(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    number = _finite(value)
    if number is None:
        return None
    if number not in (0.0, 1.0):
        raise ModellingInputError("Outcomes of up/down forecasts must be 0 or 1 (or True/False).")
    return number


def probability_reliability(probabilities: Sequence[float], outcomes: Sequence[Any], bins: int = 10) -> dict[str, Any]:
    """Brier score of probability forecasts and its Murphy (1973) decomposition.

    BS = reliability − resolution + uncertainty, with forecasts grouped in equal bins
    and each bin's mean forecast. Because forecasts within a bin differ slightly, the
    identity leaves a small within-bin remainder, reported so the parts add up exactly
    (Stephenson, Coelho and Jolliffe 2008).
    """

    _same_length(probabilities, outcomes)
    if isinstance(bins, bool) or not isinstance(bins, int) or not 1 <= bins <= 100:
        raise ModellingInputError("Choose between 1 and 100 bins.")
    pairs, skipped = [], 0
    for raw_p, raw_o in zip(probabilities, outcomes):
        p, o = _finite(raw_p), _event(raw_o)
        if p is None or o is None:
            skipped += 1
            continue
        if not 0.0 <= p <= 1.0:
            raise ModellingInputError("Forecast probabilities must lie between 0 and 1.")
        pairs.append((p, o))
    _enough(len(pairs), "probability forecasts with an outcome")
    n = len(pairs)
    base_rate = statistics.fmean(o for _, o in pairs)
    brier = statistics.fmean((p - o) ** 2 for p, o in pairs)
    groups: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for p, o in pairs:
        groups[min(int(p * bins), bins - 1)].append((p, o))
    rows, reliability, resolution = [], 0.0, 0.0
    for index, group in enumerate(groups):
        if not group:
            continue
        mean_p = statistics.fmean(p for p, _ in group)
        frequency = statistics.fmean(o for _, o in group)
        reliability += len(group) * (mean_p - frequency) ** 2 / n
        resolution += len(group) * (frequency - base_rate) ** 2 / n
        rows.append({"from": index / bins, "to": (index + 1) / bins, "count": len(group),
                     "mean_forecast": mean_p, "observed_frequency": frequency})
    uncertainty = base_rate * (1.0 - base_rate)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "observations": n,
        "skipped": skipped,
        "brier_score": brier,
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": uncertainty,
        "within_bin": brier - (reliability - resolution + uncertainty),
        "brier_skill_score": 1.0 - brier / uncertainty if uncertainty > 0 else None,
        "base_rate": base_rate,
        "bins": rows,
        "method": ("Brier score: the average squared gap between the forecast probability and what happened (1 or 0). "
                   "Reliability (lower is better) is how far each bin's average forecast sits from how often the event "
                   "happened; resolution (higher is better) is how far those frequencies move from the overall rate; "
                   "uncertainty is the overall rate times its complement. Skill above 0 beats always forecasting "
                   "the overall rate."),
    }
