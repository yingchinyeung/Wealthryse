"""Forward-looking risk from today's holdings.

The return history says how the portfolio moved; these functions say how
today's holdings would move together, so a position bought yesterday counts in
full. Pure standard-library Python, like :mod:`ibkr_etl.modelling`:

* :func:`ledoit_wolf_covariance` – covariance of the holdings' daily returns,
  shrunk towards a structured target by the amount the data supports: a scaled
  identity (Ledoit and Wolf 2004) or constant correlation (Ledoit and Wolf 2003).
* :func:`ex_ante_risk` – forward volatility and, against a benchmark, tracking
  error, with each holding's marginal and Euler (component) contribution.
* :func:`factor_risk_model` – a time-series factor risk model: each holding's
  betas to the factors, the factors' covariance and each holding's specific
  variance, giving asset covariance B F Bᵀ + D.
* :func:`factor_risk_decomposition` – how much of the portfolio's (or active)
  variance each factor and the specific part explain.
* :func:`statistical_factors` – principal components of the holdings'
  correlation matrix, for when no named factors fit.

Return series are aligned daily simple returns, one per holding. Covariance
matrices come out annualised (× 252). Every result is a hypothetical model built
from history and says so.
"""
from __future__ import annotations

import math
import random
from typing import Any, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError, _seed, ols

MODEL_VERSION = "holdings-risk-v1"
TRADING_DAYS = 252
MIN_OBSERVATIONS = 20           # about a month of sessions
MIN_RESIDUAL_DEGREES = 10       # days left over after fitting a factor model's coefficients
TARGETS = ("identity", "constant_correlation")
SYMMETRY_TOLERANCE = 1e-9
MAX_STATISTICAL_FACTORS = 10
POWER_ITERATIONS = 1000
POWER_TOLERANCE = 1e-10        # largest change in any entry of the unit eigenvector


# Shared helpers ------------------------------------------------------------------------


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    """Dot product of float sequences; ``map`` keeps the loop in C, about three times faster."""

    return sum(map(float.__mul__, left, right))


def _number(value: Any, description: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = math.nan
    if not math.isfinite(number):
        raise ModellingInputError(f"{description} is missing or not a number.")
    return number


def _clean_columns(
    data: Mapping[str, Sequence[float]] | Sequence[Sequence[float]],
    *,
    what: str = "holding",
    minimum: int = MIN_OBSERVATIONS,
) -> tuple[list[str], list[list[float]]]:
    """Names and float columns; rejects missing values, ragged lengths and short histories.

    A mapping is named by its keys, a list by position (``"1"``, ``"2"``, …).
    """

    if isinstance(data, Mapping):
        names, raw = [str(key) for key in data], list(data.values())
    else:
        raw = list(data)
        names = [str(index + 1) for index in range(len(raw))]
    if not raw:
        raise ModellingInputError(f"Give at least one {what}'s returns.")
    columns = []
    for name, column in zip(names, raw):
        values = []
        for position, value in enumerate(column):
            values.append(_number(value, f"The return of {what} {name} at position {position + 1}"))
        columns.append(values)
    lengths = {len(column) for column in columns}
    if len(lengths) > 1:
        raise ModellingInputError(
            f"The {what} return series must be aligned on the same dates; their lengths run from "
            f"{min(lengths)} to {max(lengths)}."
        )
    length = lengths.pop()
    if length < minimum:
        raise ModellingInputError(f"The model needs at least {minimum} daily returns per {what}; there are {length}.")
    return names, columns


def _demean(column: Sequence[float]) -> list[float]:
    mean = math.fsum(column) / len(column)
    return [value - mean for value in column]


def _cross_products(columns: Sequence[Sequence[float]], divisor: float) -> list[list[float]]:
    """Symmetric matrix of column dot products over ``divisor``."""

    n = len(columns)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            matrix[i][j] = matrix[j][i] = _dot(columns[i], columns[j]) / divisor
    return matrix


def _cholesky(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    """Lower-triangular L with L Lᵀ = matrix; a direction with no variance gets a zero column."""

    n = len(matrix)
    lower = [[0.0] * n for _ in range(n)]
    for j in range(n):
        pivot = matrix[j][j] - sum(v * v for v in lower[j][:j])
        if pivot <= 1e-12 * matrix[j][j]:
            continue
        root = math.sqrt(pivot)
        lower[j][j] = root
        for i in range(j + 1, n):
            lower[i][j] = (matrix[i][j] - _dot(lower[i][:j], lower[j][:j])) / root
    return lower


def _covariance_input(
    covariance: Mapping[str, Any] | Sequence[Sequence[float]], names: Sequence[str] | None
) -> tuple[list[list[float]], list[str]]:
    """A square, symmetric covariance matrix and its labels, from a matrix or a model result."""

    labels = None
    if isinstance(covariance, Mapping):
        labels, covariance = covariance.get("names"), covariance.get("covariance")
        if covariance is None:
            raise ModellingInputError(
                "Pass a covariance matrix, or a result of ledoit_wolf_covariance or factor_risk_model."
            )
    matrix = [[_number(v, "A covariance entry") for v in row] for row in covariance]
    n = len(matrix)
    if n == 0 or any(len(row) != n for row in matrix):
        raise ModellingInputError("The covariance matrix must be square, with one row per holding.")
    scale = max(abs(v) for row in matrix for v in row) or 1.0
    if any(abs(matrix[i][j] - matrix[j][i]) > SYMMETRY_TOLERANCE * scale for i in range(n) for j in range(i)):
        raise ModellingInputError("The covariance matrix is not symmetric.")
    if any(matrix[i][i] < 0 for i in range(n)):
        raise ModellingInputError("The covariance matrix has a negative variance on its diagonal.")
    labels = list(names or labels or [str(i + 1) for i in range(n)])
    if len(labels) != n:
        raise ModellingInputError(f"There are {len(labels)} holding names for a {n} × {n} covariance matrix.")
    return matrix, [str(label) for label in labels]


def _weight_vector(weights: Mapping[str, float] | Sequence[float], names: Sequence[str], label: str) -> list[float]:
    """Weights in the order of ``names``; a mapping may leave holdings out (weight 0)."""

    if isinstance(weights, Mapping):
        index = {name: i for i, name in enumerate(names)}
        unknown = sorted(str(key) for key in weights if str(key) not in index)
        if unknown:
            raise ModellingInputError(f"The {label} name holdings that are not in the risk model: {', '.join(unknown[:5])}.")
        vector = [0.0] * len(names)
        for key, value in weights.items():
            vector[index[str(key)]] = _number(value, f"The {label[:-1]} of {key}")
        return vector
    values = list(weights)
    if len(values) != len(names):
        raise ModellingInputError(f"There are {len(values)} {label} for {len(names)} holdings.")
    return [_number(value, f"The {label[:-1]} of holding {name}") for value, name in zip(values, names)]


# 1. Ledoit-Wolf shrinkage ------------------------------------------------------------


def _identity_shrinkage(sample: list[list[float]], fourth: float, days: int) -> tuple[float, list[list[float]]]:
    """The estimators m, d², b̄² and b² of Ledoit and Wolf (2004), in the normalised Frobenius norm ||A||² = tr(AAᵀ)/N."""

    n = len(sample)
    mu = sum(sample[i][i] for i in range(n)) / n
    squared_norm = sum(v * v for row in sample for v in row)
    d2 = (squared_norm - n * mu * mu) / n                   # ||S - μI||²
    b2_bar = max(0.0, (fourth - squared_norm) / (n * days))  # (1/T²) Σ_t ||x_t x_tᵀ - S||²
    intensity = min(b2_bar, d2) / d2 if d2 > 0 else 0.0
    prior = [[mu if i == j else 0.0 for j in range(n)] for i in range(n)]
    return intensity, prior


def _constant_correlation_shrinkage(
    x: list[list[float]], sample: list[list[float]], fourth: float
) -> tuple[float, list[list[float]], float]:
    """Ledoit and Wolf (2003): intensity κ/T with κ = (π - ρ)/γ, clamped to [0, 1].

    π sums the asymptotic variances of the sample covariances, ρ their covariance
    with the target and γ the squared distance from the target. The sums over
    pairs are rearranged so that only O(N·T) work is done beyond the sample matrix.
    """

    n, days = len(x), len(x[0])
    sd = [math.sqrt(sample[i][i]) for i in range(n)]
    varying = [i for i in range(n) if sd[i] > 0]
    correlations = [sample[i][j] / (sd[i] * sd[j]) for a, i in enumerate(varying) for j in varying[a + 1:]]
    r_bar = math.fsum(correlations) / len(correlations) if correlations else 0.0
    prior = [[sample[i][i] if i == j else r_bar * sd[i] * sd[j] for j in range(n)] for i in range(n)]
    pi_hat = fourth - sum(v * v for row in sample for v in row)
    scaled = [sum(values) for values in zip(*([sd[j] * v for v in x[j]] for j in range(n)))]   # Σ_j sd_j x_tj
    pi_diagonal, theta = 0.0, 0.0
    for i in range(n):
        squares = [v * v for v in x[i]]
        fourth_i = _dot(squares, squares) / days
        pi_diagonal += fourth_i - sample[i][i] ** 2
        if sd[i] == 0:
            continue
        cubes = [s * v for s, v in zip(squares, x[i])]
        # Σ_{j≠i} sd_j θ_ii,ij with θ_ii,ij = (1/T) Σ_t x_ti³ x_tj - s_ii s_ij
        moment = _dot(cubes, scaled) / days - sd[i] * fourth_i
        moment -= sample[i][i] * sum(sd[j] * sample[i][j] for j in range(n) if j != i)
        theta += moment / sd[i]
    rho_hat = pi_diagonal + r_bar * theta
    gamma = sum((s - p) ** 2 for srow, prow in zip(sample, prior) for s, p in zip(srow, prow))
    intensity = max(0.0, min(1.0, (pi_hat - rho_hat) / gamma / days)) if gamma > 0 else 0.0
    return intensity, prior, r_bar


def ledoit_wolf_covariance(
    columns: Mapping[str, Sequence[float]] | Sequence[Sequence[float]], *, target: str = "identity"
) -> dict[str, Any]:
    """Annualised covariance of the holdings, shrunk towards ``target`` by the estimated intensity.

    ``target="identity"`` is Ledoit and Wolf (2004, "A well-conditioned estimator
    for large-dimensional covariance matrices"): the average variance on the
    diagonal and zero elsewhere. ``target="constant_correlation"`` is Ledoit and
    Wolf (2003, "Honey, I shrunk the sample covariance matrix"): each holding keeps
    its own variance and every pair gets the average sample correlation. Both use
    the paper's sample covariance of demeaned returns divided by T, so the result
    is δ·target + (1 − δ)·sample, then × 252.
    """

    if target not in TARGETS:
        raise ModellingInputError(f"Unknown shrinkage target {target!r}; use 'identity' or 'constant_correlation'.")
    names, raw = _clean_columns(columns)
    x = [_demean(column) for column in raw]
    days = len(x[0])
    sample = _cross_products(x, days)
    row_norms = [sum(values) for values in zip(*([v * v for v in column] for column in x))]
    fourth = _dot(row_norms, row_norms) / days                # (1/T) Σ_t ||x_t||⁴
    average_correlation = None
    if target == "identity":
        intensity, prior = _identity_shrinkage(sample, fourth, days)
        described = "a scaled identity matrix (the average variance on the diagonal, zero elsewhere), Ledoit and Wolf (2004)"
    else:
        intensity, prior, average_correlation = _constant_correlation_shrinkage(x, sample, fourth)
        described = ("a constant-correlation matrix (each holding keeps its own variance, every pair gets the average "
                     "sample correlation), Ledoit and Wolf (2003)")
    covariance = [[(intensity * p + (1.0 - intensity) * s) * TRADING_DAYS for p, s in zip(prow, srow)]
                  for prow, srow in zip(prior, sample)]
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "names": names,
        "covariance": covariance,
        "shrinkage_intensity": intensity,
        "target": target,
        "average_correlation": average_correlation,
        "observations": days,
        "method": (
            f"Sample covariance of the holdings' demeaned daily returns (divided by the number of days), shrunk towards "
            f"{described}. The shrinkage intensity is the weight on the target that minimises the expected squared "
            "error of the matrix, estimated from the same data; 0 keeps the sample matrix, 1 uses the target. "
            "Annualised × 252."
        ),
        "disclaimer": DISCLAIMER,
    }


# 2. Ex-ante risk and its contributions ------------------------------------------------


def _euler(weights: Sequence[float], covariance: Sequence[Sequence[float]]) -> tuple[float, list[float], list[float]]:
    """Risk √(wᵀΣw), marginal contributions (Σw)/σ and components w·(Σw)/σ, which sum to σ."""

    product = [_dot(row, weights) for row in covariance]
    variance = _dot(weights, product)
    if variance < -1e-12 * max(1.0, max(abs(v) for row in covariance for v in row)):
        raise ModellingInputError("The covariance matrix is not positive semi-definite for these weights.")
    risk = math.sqrt(max(0.0, variance))
    if risk == 0:
        return 0.0, [0.0] * len(weights), [0.0] * len(weights)
    marginal = [p / risk for p in product]
    return risk, marginal, [w * m for w, m in zip(weights, marginal)]


def ex_ante_risk(
    weights: Mapping[str, float] | Sequence[float],
    covariance: Mapping[str, Any] | Sequence[Sequence[float]],
    *,
    benchmark_weights: Mapping[str, float] | Sequence[float] | None = None,
    names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Forward volatility (and tracking error) of today's weights, split by holding.

    ``covariance`` is an annualised matrix or a result of
    :func:`ledoit_wolf_covariance` or :func:`factor_risk_model`. Weights are
    fractions of NAV; whatever they leave out (cash) is treated as riskless. Each
    holding's component contribution w_i (Σw)_i / σ is its Euler allocation of
    risk (Litterman 1996, "Hot spots and hedges"); components sum to σ and the
    shares to 1. With a benchmark the same split is made of the active weights
    w − b, whose risk is the tracking error.
    """

    matrix, labels = _covariance_input(covariance, names)
    w = _weight_vector(weights, labels, "weights")
    volatility, marginal, component = _euler(w, matrix)
    rows = [
        {"name": name, "weight": wi, "marginal_contribution": m, "contribution": c,
         "share": c / volatility if volatility else None}
        for name, wi, m, c in zip(labels, w, marginal, component)
    ]
    benchmark_volatility = tracking_error = None
    if benchmark_weights is not None:
        b = _weight_vector(benchmark_weights, labels, "benchmark weights")
        benchmark_volatility = _euler(b, matrix)[0]
        active = [wi - bi for wi, bi in zip(w, b)]
        tracking_error, active_marginal, active_component = _euler(active, matrix)
        for row, bi, ai, m, c in zip(rows, b, active, active_marginal, active_component):
            row.update({"benchmark_weight": bi, "active_weight": ai, "active_marginal_contribution": m,
                        "active_contribution": c, "active_share": c / tracking_error if tracking_error else None})
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "volatility": volatility,
        "variance": volatility * volatility,
        "benchmark_volatility": benchmark_volatility,
        "tracking_error": tracking_error,
        "holdings": rows,
        "method": (
            "Annualised volatility √(wᵀΣw) of today's weights under the covariance matrix given. A holding's marginal "
            "contribution is how much volatility rises per unit of extra weight, (Σw)ᵢ/σ; its contribution is weight × "
            "marginal contribution (the Euler allocation), and the contributions add up to the volatility. With a "
            "benchmark the same split is made of the active weights (portfolio minus benchmark), whose volatility is "
            "the tracking error. Cash left out of the weights is treated as riskless."
        ),
        "disclaimer": DISCLAIMER,
    }


# 3. Factor risk model -----------------------------------------------------------------


def factor_risk_model(
    asset_returns: Mapping[str, Sequence[float]] | Sequence[Sequence[float]],
    factor_returns: Mapping[str, Sequence[float]] | Sequence[Sequence[float]],
) -> dict[str, Any]:
    """Time-series factor risk model with asset covariance B F Bᵀ + D (annualised).

    Each holding's daily returns are regressed on the factors' daily returns
    with an intercept (:func:`ibkr_etl.modelling.ols`), giving its betas (a row of
    B) and its specific variance (residual variance, a diagonal entry of D). F is
    the factors' sample covariance. Specific returns are assumed uncorrelated
    across holdings, the standard assumption of a factor risk model (Grinold and
    Kahn, "Active Portfolio Management", 2000). B F Bᵀ is built as C Cᵀ with
    C = B L and F = L Lᵀ, so the matrix is positive semi-definite by construction.
    """

    names, assets = _clean_columns(asset_returns)
    factors, columns = _clean_columns(factor_returns, what="factor")
    days, k = len(assets[0]), len(columns)
    if len(columns[0]) != days:
        raise ModellingInputError(
            f"The holdings and the factors must be aligned on the same dates; there are {days} holding returns and "
            f"{len(columns[0])} factor returns."
        )
    if days - k - 1 < MIN_RESIDUAL_DEGREES:
        raise ModellingInputError(f"{k} factors need at least {k + 1 + MIN_RESIDUAL_DEGREES} days of returns; there are {days}.")
    centred = [_demean(column) for column in columns]
    for name, column in zip(factors, centred):
        if not any(column):
            raise ModellingInputError(f"Factor {name} does not move over the window, so no beta to it can be fitted.")
    factor_covariance = [[v * TRADING_DAYS for v in row] for row in _cross_products(centred, days - 1)]
    betas, alphas, specific, r_squared = [], [], [], []
    for column in assets:
        fit = ols(column, columns)
        alphas.append(fit["coefficients"][0] * TRADING_DAYS)
        betas.append(fit["coefficients"][1:])
        specific.append(max(0.0, fit["residual_volatility"]) ** 2 * TRADING_DAYS)
        r_squared.append(fit["r_squared"])
    lower_columns = [list(column) for column in zip(*_cholesky(factor_covariance))]
    loaded = [[_dot(row, column) for column in lower_columns] for row in betas]   # C = B L
    n = len(names)
    covariance = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            covariance[i][j] = covariance[j][i] = _dot(loaded[i], loaded[j]) + (specific[i] if i == j else 0.0)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "names": names,
        "factors": factors,
        "betas": betas,
        "alphas_annual": alphas,
        "factor_covariance": factor_covariance,
        "specific_variances": specific,
        "r_squared": r_squared,
        "covariance": covariance,
        "observations": days,
        "method": (
            "Each holding's daily returns regressed on the factors' daily returns with an intercept (ordinary least "
            "squares), giving its betas B and its specific variance D (the variance of what the factors leave "
            "unexplained). Asset covariance = B F Bᵀ + D, with F the factors' sample covariance; specific returns are "
            "assumed uncorrelated across holdings. Annualised × 252."
        ),
        "disclaimer": DISCLAIMER,
    }


def factor_risk_decomposition(
    weights: Mapping[str, float] | Sequence[float],
    model: Mapping[str, Any],
    *,
    benchmark_weights: Mapping[str, float] | Sequence[float] | None = None,
) -> dict[str, Any]:
    """Split portfolio (or active) variance into each factor's part and the specific part.

    With exposures x = Bᵀw, factor k contributes x_k (F x)_k and the specific part
    is Σ w_i² D_i; together they are wᵀ(B F Bᵀ + D)w exactly. A factor's part can
    be negative when it hedges the others. With a benchmark, w is replaced by the
    active weights w − b and the total is the squared tracking error.
    """

    names, factors = [str(n) for n in model["names"]], [str(f) for f in model["factors"]]
    betas = [[_number(v, "A beta in the factor model") for v in row] for row in model["betas"]]
    factor_covariance = [[_number(v, "A factor covariance") for v in row] for row in model["factor_covariance"]]
    specific = [_number(v, "A specific variance") for v in model["specific_variances"]]
    k = len(factors)
    if (len(betas) != len(names) or len(specific) != len(names) or any(len(row) != k for row in betas)
            or len(factor_covariance) != k or any(len(row) != k for row in factor_covariance)):
        raise ModellingInputError("The factor model's betas, factor covariance and specific variances do not fit together.")
    w = _weight_vector(weights, names, "weights")
    b = _weight_vector(benchmark_weights, names, "benchmark weights") if benchmark_weights is not None else None
    x = [wi - bi for wi, bi in zip(w, b)] if b is not None else w

    def exposures(vector: Sequence[float]) -> list[float]:
        return [_dot(vector, column) for column in zip(*betas)]

    exposure = exposures(x)
    pulled = [_dot(row, exposure) for row in factor_covariance]
    parts = [e * p for e, p in zip(exposure, pulled)]
    specific_parts = [xi * xi * d for xi, d in zip(x, specific)]
    factor_variance, specific_variance = math.fsum(parts), math.fsum(specific_parts)
    variance = max(0.0, factor_variance + specific_variance)
    risk = math.sqrt(variance)
    portfolio_exposure = exposures(w)
    benchmark_exposure = exposures(b) if b is not None else None
    rows = []
    for i, name in enumerate(factors):
        rows.append({
            "factor": name,
            "exposure": exposure[i],
            "portfolio_exposure": portfolio_exposure[i],
            "benchmark_exposure": benchmark_exposure[i] if benchmark_exposure else None,
            "variance_contribution": parts[i],
            "share": parts[i] / variance if variance else None,
            "risk_contribution": parts[i] / risk if risk else 0.0,
        })
    measure = "tracking_error" if b is not None else "volatility"
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "measure": measure,
        "risk": risk,
        "variance": variance,
        "factor_variance": factor_variance,
        "specific_variance": specific_variance,
        "factor_share": factor_variance / variance if variance else None,
        "specific_share": specific_variance / variance if variance else None,
        "factors": rows,
        "specific_by_holding": [
            {"name": name, "weight": xi, "variance_contribution": part} for name, xi, part in zip(names, x, specific_parts)
        ],
        "method": (
            f"Exposures x = Bᵀw are the {'active ' if b is not None else ''}weights' betas to each factor. Factor k's part "
            "of the variance is x_k × (F x)_k and the specific part is Σ wᵢ² Dᵢ; together they are the whole variance "
            f"of the factor model, whose square root is the {measure.replace('_', ' ')}. A negative part means the "
            "factor offsets the others. Risk contribution is the variance part divided by the total risk, so those "
            "add up to the risk itself."
        ),
        "disclaimer": DISCLAIMER,
    }


# 4. Statistical factors ----------------------------------------------------------------


def _power_iteration(
    matrix: Sequence[Sequence[float]], found: Sequence[Sequence[float]], generator: random.Random
) -> tuple[list[float], float, int, bool]:
    """Leading eigenvector and eigenvalue of a symmetric PSD matrix, kept orthogonal to vectors already found."""

    def orthogonalise(vector: list[float]) -> list[float]:
        for other in found:
            projection = _dot(vector, other)
            vector = [v - projection * o for v, o in zip(vector, other)]
        return vector

    vector = orthogonalise([generator.gauss(0.0, 1.0) for _ in matrix])
    norm = math.sqrt(_dot(vector, vector)) or 1.0
    vector = [v / norm for v in vector]
    converged, iteration = False, 0
    while not converged and iteration < POWER_ITERATIONS:
        iteration += 1
        product = orthogonalise([_dot(row, vector) for row in matrix])
        norm = math.sqrt(_dot(product, product))
        if norm < 1e-15:                       # nothing left: the remaining eigenvalues are zero
            return vector, 0.0, iteration, True
        if _dot(product, vector) < 0:
            norm = -norm
        product = [v / norm for v in product]
        converged = max(abs(p - v) for p, v in zip(product, vector)) < POWER_TOLERANCE
        vector = product
    value = _dot(vector, [_dot(row, vector) for row in matrix])   # Rayleigh quotient
    return vector, max(0.0, value), iteration, converged


def statistical_factors(columns: Mapping[str, Sequence[float]] | Sequence[Sequence[float]], k: int) -> dict[str, Any]:
    """The ``k`` leading principal components of the holdings' correlation matrix.

    Principal components (Hotelling 1933) by power iteration with deflation:
    each eigenvector is found by repeated multiplication from a seeded random
    start, then its part λ v vᵀ is removed before the next. Signs are fixed so
    each component's largest loading is positive. Loadings are v √λ, the
    correlation between a holding and the component; explained share is λ / N.
    """

    names, raw = _clean_columns(columns)
    n, days = len(raw), len(raw[0])
    most = min(n, MAX_STATISTICAL_FACTORS)
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= most:
        raise ModellingInputError(f"Ask for between 1 and {most} statistical factors.")
    x = [_demean(column) for column in raw]
    standardised = []
    for name, column in zip(names, x):
        sd = math.sqrt(_dot(column, column) / (days - 1))
        if sd == 0:
            raise ModellingInputError(f"Holding {name} does not move over the window, so its correlations are undefined; leave it out.")
        standardised.append([v / sd for v in column])
    correlation = _cross_products(standardised, days - 1)
    for i in range(n):
        correlation[i][i] = 1.0
    generator = random.Random(_seed("statistical-factors", n, days, k))
    deflated, found, components = [row[:] for row in correlation], [], []
    cumulative = 0.0
    for index in range(k):
        vector, value, iterations, converged = _power_iteration(deflated, found, generator)
        largest = max(range(n), key=lambda i: abs(vector[i]))
        if vector[largest] < 0:
            vector = [-v for v in vector]
        found.append(vector)
        deflated = [[a - value * vi * vj for a, vj in zip(row, vector)] for row, vi in zip(deflated, vector)]
        cumulative += value / n
        components.append({
            "factor": f"PC{index + 1}",
            "eigenvalue": value,
            "explained_share": value / n,
            "cumulative_share": cumulative,
            "eigenvector": vector,
            "loadings": [v * math.sqrt(value) for v in vector],
            "iterations": iterations,
            "converged": converged,
        })
    scores = {c["factor"]: [_dot(row, c["eigenvector"]) for row in zip(*standardised)] for c in components}
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "names": names,
        "factors": components,
        "component_scores": scores,
        "observations": days,
        "method": (
            "Principal components of the correlation matrix of the holdings' daily returns, by power iteration with "
            "deflation from a seeded start. Each component is a mix of the standardised holdings; its eigenvalue over "
            "the number of holdings is the share of total variance it explains. Loadings are each holding's "
            "correlation with the component; signs are set so the largest loading is positive. Component scores are "
            "the daily values of each mix of standardised returns."
        ),
        "disclaimer": DISCLAIMER,
    }
