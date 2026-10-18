"""Portfolio optimisers beyond plain mean-variance, for the Modelling page.

Pure standard-library functions over plain sequences, like :mod:`ibkr_etl.modelling`.
Holdings are positional: row ``i`` of a covariance matrix, item ``i`` of the expected
returns and weight ``i`` all describe the same holding. Covariances and expected
returns are annual, in excess of cash.

* :func:`black_litterman` – expected returns implied by market weights, blended with
  your own views at a stated confidence (He and Litterman 1999; Idzorek 2005).
* :func:`hierarchical_risk_parity` – weights from a correlation tree, steadier than
  risk parity on small samples (López de Prado 2016).
* :func:`min_cvar_weights` – the weights with the smallest expected tail loss over
  historical scenarios (Rockafellar and Uryasev 2000).
* :func:`mean_variance_with_costs` – mean-variance weights that pay for their own
  trades, with an optional limit on turnover.
* :func:`robust_mean_variance` – mean-variance weights that still hold up when the
  expected returns are wrong within an ellipsoid (Goldfarb and Iyengar 2003; Ceria
  and Stubbs 2006).

Weights are long only and fully invested, with a per-holding cap, as in
:func:`ibkr_etl.modelling.optimise` (hierarchical risk parity is uncapped unless a cap
is given, like modelling's risk parity). Everything here is a hypothetical model.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Callable, Sequence

from ..modelling import (
    DISCLAIMER,
    ModellingInputError,
    _largest_eigenvalue,
    _matvec,
    _solve_inverse,
    mean_variance_weights,
    risk_contributions,
)

try:
    from math import sumprod as _sumprod
except ImportError:  # Python 3.11
    def _sumprod(left: Sequence[float], right: Sequence[float]) -> float:
        return sum(a * b for a, b in zip(left, right))

MODEL_VERSION = "quant-optimisers-v1"
DEFAULT_CAP = 0.25
MIN_TAIL_SCENARIOS = 10           # a tail average over fewer scenarios is noise
MAX_COST_BPS = 1000.0
# Smoothing widths (as a share of the loss spread) and stopping tolerances for min CVaR.
CVAR_STAGES = ((0.5, 1e-7), (0.1, 1e-7), (0.02, 1e-8), (0.004, 1e-9))
CVAR_STAGE_ITERATIONS = 200


# Validation -----------------------------------------------------------------------------


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _square_matrix(matrix: Sequence[Sequence[float]], label: str = "covariance matrix") -> list[list[float]]:
    """Validated copy of a symmetric matrix; missing entries are rejected, tiny asymmetry averaged out."""

    n = len(matrix)
    if n == 0:
        raise ModellingInputError(f"The {label} is empty.")
    rows = []
    for row in matrix:
        if len(row) != n:
            raise ModellingInputError(f"The {label} must be square: one row and one column per holding.")
        values = [_finite(v) for v in row]
        if any(v is None for v in values):
            raise ModellingInputError(f"The {label} has missing or non-numeric entries.")
        rows.append(values)
    for i in range(n):
        if rows[i][i] < 0:
            raise ModellingInputError(f"The {label} has a negative variance on its diagonal.")
        for j in range(i + 1, n):
            a, b = rows[i][j], rows[j][i]
            if abs(a - b) > 1e-9 * max(1.0, abs(a), abs(b)):
                raise ModellingInputError(f"The {label} must be symmetric.")
            rows[i][j] = rows[j][i] = 0.5 * (a + b)
    return rows


def _vector(values: Sequence[float], n: int, label: str) -> list[float]:
    if len(values) != n:
        raise ModellingInputError(f"{label} need one value per holding ({n}); got {len(values)}.")
    out = [_finite(v) for v in values]
    if any(v is None for v in out):
        raise ModellingInputError(f"{label} have missing or non-numeric values.")
    return out


def _weights(values: Sequence[float], n: int, label: str) -> list[float]:
    """Non-negative weights scaled to sum to 1."""

    weights = _vector(values, n, label)
    if any(w < 0 for w in weights):
        raise ModellingInputError(f"{label} must be zero or positive; short positions are not modelled.")
    total = sum(weights)
    if total <= 0:
        raise ModellingInputError(f"{label} add up to nothing.")
    return [w / total for w in weights]


def _positive(value: Any, label: str) -> float:
    number = _finite(value)
    if number is None or number <= 0:
        raise ModellingInputError(f"The {label} must be a positive number.")
    return number


def _cap(cap: Any, n: int) -> float:
    value = _finite(cap)
    if value is None or not 0.0 < value <= 1.0:
        raise ModellingInputError("The maximum weight per holding must be above 0% and at most 100%.")
    return max(value, 1.0 / n + 1e-9)  # as modelling.optimise: a cap below 1/n cannot be met


def _cost_rate(cost_bps: Any) -> float:
    value = _finite(cost_bps)
    if value is None or not 0.0 <= value <= MAX_COST_BPS:
        raise ModellingInputError(f"The trading cost must be between 0 and {MAX_COST_BPS:.0f} basis points.")
    return value / 10_000.0


def _point(weights: Sequence[float], cov: Sequence[Sequence[float]], mu: Sequence[float]) -> dict[str, float]:
    return {
        "expected_return": _sumprod(weights, mu),
        "volatility": math.sqrt(max(0.0, _sumprod(weights, _matvec(cov, weights)))),
    }


def _l1(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(abs(a - b) for a, b in zip(left, right))


# Projections and the accelerated proximal gradient ---------------------------------------


def _shrink_project(values: Sequence[float], anchor: Sequence[float], threshold: float, cap: float) -> list[float]:
    """Exact argmin of ½‖w − v‖² + threshold·‖w − anchor‖₁ over 0 ≤ w ≤ cap, Σw = 1.

    For a multiplier ν on the budget, each weight is the soft-threshold of ``v − ν``
    around its anchor, clipped to the box; the sum is piecewise linear and falling in
    ν, so ν is found exactly from its breakpoints. With ``threshold = 0`` this is the
    projection onto the capped simplex.
    """

    k = threshold
    pairs = list(zip(values, anchor))

    def weights_at(nu: float) -> list[float]:
        out = []
        for v, a in pairs:
            u = v - nu
            h = u - k if u - a > k else u + k if u - a < -k else a
            out.append(cap if h > cap else 0.0 if h < 0.0 else h)
        return out

    points = sorted({p for v, a in pairs for p in (v - a - k, v - a + k, v - k, v - k - cap, v + k, v + k - cap)})
    low, high = 0, len(points) - 1          # sum ≥ 1 at points[0] (all at cap), 0 at points[-1]
    total_low, total_high = sum(weights_at(points[low])), sum(weights_at(points[high]))
    while high - low > 1:
        middle = (low + high) // 2
        total = sum(weights_at(points[middle]))
        if total >= 1.0:
            low, total_low = middle, total
        else:
            high, total_high = middle, total
    if total_low == total_high:
        return weights_at(points[low])
    nu = points[low] + (total_low - 1.0) * (points[high] - points[low]) / (total_low - total_high)
    return weights_at(nu)


def _project(values: Sequence[float], cap: float) -> list[float]:
    return _shrink_project(values, values, 0.0, cap)


def _accelerated(
    smooth: Callable[[list[float]], tuple[float, list[float]]],
    prox: Callable[[list[float], float], list[float]],
    start: Sequence[float],
    *,
    lipschitz: float,
    penalty: Callable[[list[float]], float] | None = None,
    iterations: int = 5000,
    tolerance: float = 1e-11,
) -> list[float]:
    """Minimise smooth + penalty by accelerated proximal gradient.

    FISTA with backtracking (Beck and Teboulle 2009), restarting the momentum whenever
    the objective rises (O'Donoghue and Candès 2015). ``smooth`` returns the value and
    gradient; ``prox(v, step)`` applies the penalty and the constraints.
    """

    x = list(start)
    fx, _ = smooth(x)
    objective = fx + (penalty(x) if penalty else 0.0)
    y, t, lip = list(x), 1.0, max(lipschitz, 1e-12)
    for _ in range(iterations):
        fy, gy = smooth(y)
        while True:
            z = prox([a - g / lip for a, g in zip(y, gy)], 1.0 / lip)
            d = [a - b for a, b in zip(z, y)]
            fz, _ = smooth(z)
            if fz <= fy + _sumprod(gy, d) + 0.5 * lip * _sumprod(d, d) + 1e-13 * (1.0 + abs(fy)):
                break
            lip *= 2.0
        value = fz + (penalty(z) if penalty else 0.0)
        if value > objective + 1e-13 * (1.0 + abs(objective)):
            y, t = list(x), 1.0
            continue
        t_next = 0.5 * (1.0 + math.sqrt(1.0 + 4.0 * t * t))
        moved = max(abs(a - b) for a, b in zip(z, x))
        y = [a + (t - 1.0) / t_next * (a - b) for a, b in zip(z, x)]
        x, objective, t = z, value, t_next
        if moved < tolerance:
            break
    return x


def _quadratic(cov: Sequence[Sequence[float]], mu: Sequence[float], risk_aversion: float):
    """λ·w'Σw − μ'w and its gradient."""

    def smooth(w: list[float]) -> tuple[float, list[float]]:
        sw = [_sumprod(row, w) for row in cov]
        return (risk_aversion * _sumprod(w, sw) - _sumprod(mu, w),
                [2.0 * risk_aversion * s - m for s, m in zip(sw, mu)])

    return smooth


# 1. Black-Litterman -------------------------------------------------------------------


def _view_rows(views: Sequence[dict[str, Any]], n: int) -> tuple[list[list[float]], list[float], list[float | None]]:
    rows, targets, confidences = [], [], []
    for number, view in enumerate(views, start=1):
        if not isinstance(view, dict):
            raise ModellingInputError(f"View {number} must be a mapping with weights and an expected return.")
        weights = _vector(view.get("weights") or [], n, f"The weights of view {number}")
        if not any(weights):
            raise ModellingInputError(f"View {number} does not name any holding.")
        target = _finite(view.get("return"))
        if target is None:
            raise ModellingInputError(f"View {number} needs an expected return.")
        confidence = view.get("confidence")
        if confidence is not None:
            confidence = _finite(confidence)
            if confidence is None or not 0.0 < confidence <= 1.0:
                raise ModellingInputError(f"The confidence of view {number} must be above 0% and at most 100%.")
        rows.append(weights)
        targets.append(target)
        confidences.append(confidence)
    return rows, targets, confidences


def black_litterman(
    covariance: Sequence[Sequence[float]],
    market_weights: Sequence[float],
    *,
    risk_aversion: float = 2.5,
    tau: float = 0.05,
    views: Sequence[dict[str, Any]] = (),
    cap: float = DEFAULT_CAP,
) -> dict[str, Any]:
    """Market-implied expected returns blended with views (He and Litterman 1999).

    Implied equilibrium returns are π = δΣw. Each view is ``{"weights": [...],
    "return": q, "confidence": c}``: the weights row is over the holdings (one 1 for an
    absolute view, +1 and -1 for "A beats B by q"), q is the view's expected excess
    return, and the optional confidence c in (0, 1] sets the view's uncertainty by
    Idzorek's (2005) method in closed form, ω = (1 − c)/c · τ·pΣp' (100% holds the view
    exactly). With no confidence the He-Litterman default ω = τ·pΣp' is used, which is
    the same as c = 50%. The posterior is

        μ = π + τΣP'(τPΣP' + Ω)⁻¹(q − Pπ),   Σ_post = Σ + τΣ − τΣP'(τPΣP' + Ω)⁻¹PτΣ.

    ``unconstrained_weights`` = (δΣ)⁻¹μ as in Idzorek (equal to the market weights with
    no views); ``weights`` maximise μ'w − (δ/2)w'Σw long only, fully invested and capped.
    """

    cov = _square_matrix(covariance)
    n = len(cov)
    market = _weights(market_weights, n, "Market weights")
    delta = _positive(risk_aversion, "risk aversion")
    tau_value = _finite(tau)
    if tau_value is None or not 0.0 < tau_value <= 1.0:
        raise ModellingInputError("Tau, the uncertainty of the implied returns, must be above 0 and at most 1.")
    cap = _cap(cap, n)
    rows, targets, confidences = _view_rows(list(views), n)
    implied = [delta * v for v in _matvec(cov, market)]

    posterior, posterior_cov, unconstrained = list(implied), [[(1.0 + tau_value) * v for v in row] for row in cov], list(market)
    view_rows = []
    if rows:
        spread = [_matvec(cov, p) for p in rows]                      # Σp' for each view
        k = len(rows)
        view_cov = [[_sumprod(rows[a], spread[b]) for b in range(k)] for a in range(k)]   # PΣP'
        omegas = []
        for a in range(k):
            if view_cov[a][a] <= 0:
                raise ModellingInputError(f"View {a + 1} has no risk under this covariance, so it cannot be weighed.")
            c = confidences[a]
            omegas.append(tau_value * view_cov[a][a] * (1.0 if c is None else (1.0 - c) / c))
        system = [[tau_value * view_cov[a][b] + (omegas[a] if a == b else 0.0) for b in range(k)] for a in range(k)]
        try:
            inverse = _solve_inverse(system)
        except ModellingInputError:
            raise ModellingInputError("The views overlap or contradict each other at full confidence; "
                                      "lower a confidence or drop a duplicate view.") from None
        surprise = [q - _sumprod(p, implied) for p, q in zip(rows, targets)]
        x = _matvec(inverse, surprise)
        posterior = [pi + tau_value * sum(spread[a][i] * x[a] for a in range(k)) for i, pi in enumerate(implied)]
        unconstrained = [w + tau_value / delta * sum(rows[a][i] * x[a] for a in range(k)) for i, w in enumerate(market)]
        posterior_cov = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i, n):
                shrink = sum(spread[a][i] * inverse[a][b] * spread[b][j] for a in range(k) for b in range(k))
                value = (1.0 + tau_value) * cov[i][j] - tau_value * tau_value * shrink
                posterior_cov[i][j] = posterior_cov[j][i] = value
        for a in range(k):
            view_rows.append({
                "kind": "relative" if abs(sum(rows[a])) < 1e-9 else "absolute",
                "weights": rows[a],
                "view_return": targets[a],
                "implied_return": _sumprod(rows[a], implied),
                "posterior_return": _sumprod(rows[a], posterior),
                "confidence": confidences[a],
                "omega": omegas[a],
                "omega_method": "He-Litterman default" if confidences[a] is None else "Idzorek",
            })
    weights = mean_variance_weights(cov, posterior, risk_aversion_inverse=2.0 / delta, cap=cap,
                                    start=_project(market, cap), iterations=3000)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "risk_aversion": delta,
        "tau": tau_value,
        "maximum_weight": cap,
        "market_weights": market,
        "implied_returns": implied,
        "posterior_returns": posterior,
        "posterior_covariance": posterior_cov,
        "unconstrained_weights": unconstrained,
        "weights": weights,
        "portfolio": _point(weights, cov, posterior),
        "views": view_rows,
        "method": (
            f"Black-Litterman (He and Litterman 1999). Implied excess returns π = δΣw from the market weights "
            f"with risk aversion δ = {delta:g}; the prior on expected returns has covariance τΣ with τ = {tau_value:g}. "
            "Each view's uncertainty ω comes from its confidence by Idzorek's (2005) method, "
            "ω = (1 − confidence)/confidence × τ·pΣp', or the He-Litterman default ω = τ·pΣp' (the same as 50%) "
            "when no confidence is given. Posterior covariance is Σ plus the uncertainty left in the mean. "
            f"Weights maximise μ'w − (δ/2)w'Σw, long only, fully invested, at most {cap:.0%} in one holding."
        ),
        "disclaimer": DISCLAIMER,
    }


# 2. Hierarchical risk parity -------------------------------------------------------------


def _single_linkage(distance: Sequence[Sequence[float]]) -> tuple[list[int], list[list[float]]]:
    """Leaf order of the single-linkage tree and its merges (left id, right id, distance, size)."""

    n = len(distance)
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    between = {(i, j): distance[i][j] for i in range(n) for j in range(i + 1, n)}
    merges = []
    next_id = n
    while len(members) > 1:
        (a, b), gap = min(between.items(), key=lambda item: (item[1], item[0]))
        merged = members.pop(a) + members.pop(b)      # smaller id first, as scipy's linkage orders it
        merges.append([a, b, gap, len(merged)])
        for other in members:
            key_a, key_b = (min(a, other), max(a, other)), (min(b, other), max(b, other))
            between[(other, next_id)] = min(between.pop(key_a), between.pop(key_b))
        del between[(a, b)]
        members[next_id] = merged
        next_id += 1
    return next(iter(members.values())), merges


def _cluster_variance(cov: Sequence[Sequence[float]], cluster: Sequence[int]) -> float:
    inverse = [1.0 / cov[i][i] for i in cluster]
    total = sum(inverse)
    weights = [v / total for v in inverse]
    return sum(weights[a] * weights[b] * cov[i][j] for a, i in enumerate(cluster) for b, j in enumerate(cluster))


def _bisect_weights(cov: Sequence[Sequence[float]], order: list[int]) -> list[float]:
    weights = [1.0] * len(cov)
    clusters = [order]
    while clusters:
        following = []
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            left, right = cluster[:len(cluster) // 2], cluster[len(cluster) // 2:]
            left_var, right_var = _cluster_variance(cov, left), _cluster_variance(cov, right)
            share = 1.0 - left_var / (left_var + right_var)
            for i in left:
                weights[i] *= share
            for i in right:
                weights[i] *= 1.0 - share
            following += [left, right]
        clusters = following
    return weights


def _cap_by_water_filling(weights: Sequence[float], cap: float) -> list[float]:
    """Holdings above the cap are cut to it and the excess shared pro rata among the rest."""

    out, fixed = list(weights), set()
    for _ in range(len(out)):
        over = [i for i, w in enumerate(out) if i not in fixed and w > cap]
        if not over:
            break
        excess = sum(out[i] - cap for i in over)
        for i in over:
            out[i] = cap
            fixed.add(i)
        free = [i for i in range(len(out)) if i not in fixed]
        total = sum(out[i] for i in free)
        for i in free:
            out[i] += excess * (out[i] / total if total > 0 else 1.0 / len(free))
    return out


def hierarchical_risk_parity(covariance: Sequence[Sequence[float]], *, cap: float | None = None) -> dict[str, Any]:
    """Hierarchical risk parity weights (López de Prado 2016).

    Correlation distance d = √(½(1 − ρ)); as in the paper, holdings are clustered by
    the Euclidean distance between their columns of d, with single linkage. The tree's
    leaf order quasi-diagonalises the covariance, and recursive bisection of that order
    splits each cluster's weight in inverse proportion to the variance of its
    inverse-variance-weighted sub-portfolio. Uncapped unless ``cap`` is given, when
    weights above it are cut and the excess shared pro rata.
    """

    cov = _square_matrix(covariance)
    n = len(cov)
    if any(cov[i][i] <= 0 for i in range(n)):
        raise ModellingInputError("Every holding needs some price movement for hierarchical risk parity; "
                                  "one has zero variance.")
    capped = _cap(cap, n) if cap is not None else None
    if n == 1:
        order, merges, weights = [0], [], [1.0]
    else:
        scale = [math.sqrt(cov[i][i]) for i in range(n)]
        corr = [[max(-1.0, min(1.0, cov[i][j] / (scale[i] * scale[j]))) for j in range(n)] for i in range(n)]
        dist = [[math.sqrt(max(0.0, 0.5 * (1.0 - corr[i][j]))) for j in range(n)] for i in range(n)]
        column_distance = [[math.dist(dist[i], dist[j]) for j in range(n)] for i in range(n)]
        order, merges = _single_linkage(column_distance)
        weights = _bisect_weights(cov, order)
    if capped is not None:
        weights = _cap_by_water_filling(weights, capped)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "weights": weights,
        "order": order,
        "linkage": merges,
        "risk_contributions": risk_contributions(weights, cov),
        "volatility": math.sqrt(max(0.0, _sumprod(weights, _matvec(cov, weights)))),
        "maximum_weight": capped,
        "method": (
            "Hierarchical risk parity (López de Prado 2016): holdings are clustered by correlation distance "
            "√(½(1 − ρ)) (Euclidean distance between distance columns, single linkage), ordered so similar "
            "holdings sit together, then weight is split top-down between the two halves of each cluster in "
            "inverse proportion to their variance. Long only and fully invested; "
            + (f"at most {capped:.0%} in one holding, excess shared pro rata." if capped is not None else "no cap.")
        ),
        "disclaimer": DISCLAIMER,
    }


# 3. Minimum expected tail loss -----------------------------------------------------------


def _scenario_rows(scenario_returns: Sequence[Sequence[float]]) -> tuple[list[list[float]], int]:
    """Scenario rows with every value present; rows with a missing value are skipped and counted."""

    present = [row for row in scenario_returns if row is not None]
    if not present:
        raise ModellingInputError("No return scenarios were given.")
    n = len(present[0])
    if n == 0:
        raise ModellingInputError("The return scenarios name no holdings.")
    rows, skipped = [], len(scenario_returns) - len(present)
    for row in present:
        if len(row) != n:
            raise ModellingInputError("Every scenario needs one return per holding.")
        values = [_finite(v) for v in row]
        if any(v is None for v in values):
            skipped += 1
            continue
        rows.append(values)
    return rows, skipped


def _tail_count(level: float, scenarios: int) -> float:
    m = (1.0 - level) * scenarios
    return float(round(m)) if abs(m - round(m)) < 1e-9 else m


def _tail_loss(descending: Sequence[float], m: float) -> float:
    """Rockafellar-Uryasev CVaR of equally likely losses: the mean of the worst m, fractional part included."""

    whole = int(m)
    total = math.fsum(descending[:whole])
    if m > whole and whole < len(descending):
        total += (m - whole) * descending[whole]
    return total / m


def _smoothed_tail(losses: Sequence[float], m: float, mu: float) -> tuple[float, float, float]:
    """min over a of a + (1/m)·Σ s(L − a), with s the hinge max(0, z) smoothed quadratically over [0, mu].

    Returns the smoothed value, the minimising a, and the exact tail loss.
    """

    descending = sorted(losses, reverse=True)
    k = math.ceil(m - 1e-9)
    top = descending[k - 1]
    low = top - mu
    count = k
    while count < len(descending) and descending[count] > low:
        count += 1
    relevant = descending[:count]                   # a lies in [top − mu, top]; lower losses never count

    def slope(a: float) -> float:
        return sum(1.0 if x - a >= mu else (x - a) / mu for x in relevant if x > a)

    points = sorted({p for x in relevant for p in (x, x - mu) if low <= p <= top} | {low, top}, reverse=True)
    i, j = 0, len(points) - 1                       # slope(top) < m ≤ slope(low)
    slope_i, slope_j = slope(points[i]), slope(points[j])
    while j - i > 1:
        middle = (i + j) // 2
        value = slope(points[middle])
        if value <= m:
            i, slope_i = middle, value
        else:
            j, slope_j = middle, value
    alpha = points[i] if slope_j == slope_i else points[i] + (m - slope_i) * (points[j] - points[i]) / (slope_j - slope_i)
    hinge = sum((x - alpha) ** 2 / (2.0 * mu) if x - alpha < mu else x - alpha - 0.5 * mu
                for x in relevant if x > alpha)
    return alpha + hinge / m, alpha, _tail_loss(descending, m)


def _cvar_descent(rows: list[list[float]], cols: list[list[float]], m: float, cap: float) -> tuple[list[float], int]:
    """Projected accelerated gradient on the smoothed CVaR, narrowing the smoothing in stages.

    Losses are linear in the weights, so the losses at the extrapolated point are
    combined from the last two iterates instead of recomputed. The best iterate by
    the exact tail loss is kept.
    """

    n = len(cols)

    def losses(w: Sequence[float]) -> list[float]:
        return [-_sumprod(row, w) for row in rows]

    x = _project([1.0 / n] * n, cap)
    lx = losses(x)
    scale = statistics.pstdev(lx) or statistics.fmean(statistics.pstdev(c) for c in cols) or 1e-8
    best_value, best = _tail_loss(sorted(lx, reverse=True), m), list(x)
    lip = statistics.fmean(_sumprod(row, row) for row in rows) / scale + 1e-12
    iterations = 0
    for factor, tolerance in CVAR_STAGES:
        mu = factor * scale
        fx = _smoothed_tail(lx, m, mu)[0]
        y, ly, t = list(x), lx, 1.0
        for _ in range(CVAR_STAGE_ITERATIONS):
            iterations += 1
            fy, alpha, _ = _smoothed_tail(ly, m, mu)
            share = [(1.0 if v - alpha >= mu else (v - alpha) / mu) if v > alpha else 0.0 for v in ly]
            gradient = [-_sumprod(col, share) / m for col in cols]
            while True:
                z = _project([a - g / lip for a, g in zip(y, gradient)], cap)
                lz = losses(z)
                fz, _, exact = _smoothed_tail(lz, m, mu)
                d = [a - b for a, b in zip(z, y)]
                if fz <= fy + _sumprod(gradient, d) + 0.5 * lip * _sumprod(d, d) + 1e-15 * (1.0 + abs(fy)):
                    break
                lip *= 2.0
            if exact < best_value:
                best_value, best = exact, z
            if fz > fx + 1e-15 * (1.0 + abs(fx)):
                y, ly, t = list(x), lx, 1.0
                continue
            t_next = 0.5 * (1.0 + math.sqrt(1.0 + 4.0 * t * t))
            beta = (t - 1.0) / t_next
            moved = max(abs(a - b) for a, b in zip(z, x))
            y = [a + beta * (a - b) for a, b in zip(z, x)]
            ly = [(1.0 + beta) * a - beta * b for a, b in zip(lz, lx)]
            x, lx, fx, t = z, lz, fz, t_next
            lip *= 0.9                               # let the step grow back where the curvature is low
            if moved < tolerance:
                break
    return best, iterations


def min_cvar_weights(
    scenario_returns: Sequence[Sequence[float]],
    *,
    level: float = 0.95,
    cap: float = DEFAULT_CAP,
) -> dict[str, Any]:
    """Weights with the smallest expected tail loss over historical scenarios (Rockafellar and Uryasev 2000).

    ``scenario_returns`` has one row per scenario (for example a trading day) and one
    column per holding; rows with a missing value are skipped and counted. The tail
    loss is CVaR_β(w) = min_a a + Σ max(0, −r_t·w − a) / ((1 − β)T), the average of the
    worst (1 − β) share of scenario losses. Without an LP solver it is minimised by
    projected accelerated gradient on a smoothed version (the hinge made quadratic over
    a narrow band, narrowed in stages), keeping the iterate with the lowest exact value.
    """

    rows, skipped = _scenario_rows(scenario_returns)
    beta = _finite(level)
    if beta is None or not 0.5 <= beta < 1.0:
        raise ModellingInputError("The tail level must be at least 50% and below 100% (for example 95%).")
    if not rows:
        raise ModellingInputError("Every scenario has a missing return.")
    n = len(rows[0])
    m = _tail_count(beta, len(rows))
    if m < MIN_TAIL_SCENARIOS:
        needed = math.ceil(MIN_TAIL_SCENARIOS / (1.0 - beta) - 1e-9)
        raise ModellingInputError(
            f"Minimum tail loss at the {beta:.0%} level needs at least {needed} complete scenarios so the tail "
            f"holds {MIN_TAIL_SCENARIOS}; there are {len(rows)}."
        )
    cap = _cap(cap, n)
    cols = [list(column) for column in zip(*rows)]
    if n == 1:
        weights, iterations = [1.0], 0
    else:
        weights, iterations = _cvar_descent(rows, cols, m, cap)
    losses = sorted((-_sumprod(row, weights) for row in rows), reverse=True)
    equal = _project([1.0 / n] * n, cap)
    equal_losses = sorted((-_sumprod(row, equal) for row in rows), reverse=True)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "level": beta,
        "scenarios": len(rows),
        "skipped_scenarios": skipped,
        "tail_scenarios": m,
        "maximum_weight": cap,
        "weights": weights,
        "expected_tail_loss": _tail_loss(losses, m),
        "value_at_risk": losses[int(m)] if int(m) < len(losses) else losses[-1],
        "expected_return": statistics.fmean(-v for v in losses),
        "equal_weight_expected_tail_loss": _tail_loss(equal_losses, m),
        "iterations": iterations,
        "method": (
            f"Minimum expected tail loss (CVaR, Rockafellar and Uryasev 2000) over {len(rows)} historical scenarios: "
            f"the average loss in the worst {1 - beta:.0%} of scenarios, made as small as possible. Long only, fully "
            f"invested, at most {cap:.0%} in one holding. Solved by projected accelerated gradient on a smoothed "
            "tail loss whose smoothing is narrowed in stages; the weights reported have the lowest exact tail loss "
            "found. Value at risk is the loss at the tail's edge. Losses are per scenario period, not annualised."
        ),
        "disclaimer": DISCLAIMER,
    }


# 4. Mean-variance with trading costs -----------------------------------------------------


def mean_variance_with_costs(
    mu: Sequence[float],
    covariance: Sequence[Sequence[float]],
    current_weights: Sequence[float],
    *,
    risk_aversion: float,
    cost_bps: float,
    turnover_limit: float | None = None,
    cap: float = DEFAULT_CAP,
) -> dict[str, Any]:
    """Mean-variance weights that pay proportional costs to trade from the current weights.

    Maximises w'μ − λ·w'Σw − c·‖w − w₀‖₁ (c = cost_bps / 10,000 per unit traded, charged
    once against a year of expected return; λ has no ½, so
    ``modelling.mean_variance_weights`` with ``risk_aversion_inverse = 1/λ`` gives the
    zero-cost answer). Solved by accelerated proximal gradient whose step soft-thresholds
    each trade and projects onto the capped simplex in one exact operation. Turnover is
    ‖w − w₀‖₁, buys plus sells; a hard ``turnover_limit`` is met by raising the cost
    rate to the limit's shadow price, found by bracketing (Lagrangian duality).
    Current weights are scaled to sum to 1.
    """

    cov = _square_matrix(covariance)
    n = len(cov)
    expected = _vector(mu, n, "Expected returns")
    current = _weights(current_weights, n, "Current weights")
    lam = _positive(risk_aversion, "risk aversion")
    cost = _cost_rate(cost_bps)
    cap = _cap(cap, n)
    floor = 2.0 * sum(max(0.0, w - cap) for w in current)   # least turnover that brings holdings under the cap
    limit = None
    if turnover_limit is not None:
        limit = _finite(turnover_limit)
        if limit is None or not 0.0 <= limit <= 2.0:
            raise ModellingInputError("The turnover limit must be between 0% and 200% (buys plus sells).")
        if limit < floor - 1e-12:
            raise ModellingInputError(f"The turnover limit is too tight: bringing every holding under the "
                                      f"{cap:.0%} cap alone needs {floor:.1%} of turnover.")
    smooth = _quadratic(cov, expected, lam)
    lip = 2.0 * lam * _largest_eigenvalue(cov)

    def solve(rate: float, start: Sequence[float]) -> list[float]:
        return _accelerated(smooth, lambda v, step: _shrink_project(v, current, step * rate, cap), start,
                            lipschitz=lip, penalty=lambda w: rate * _l1(w, current))

    weights = solve(cost, _project(current, cap))
    shadow, binding = 0.0, False
    if limit is not None and _l1(weights, current) > limit + 1e-9:
        weights, shadow = _turnover_shadow_price(solve, smooth, current, cost, limit, weights)
        binding = True
    turnover = _l1(weights, current)
    point, now = _point(weights, cov, expected), _point(current, cov, expected)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "risk_aversion": lam,
        "cost_bps": cost * 10_000.0,
        "maximum_weight": cap,
        "current_weights": current,
        "weights": weights,
        "trades": [w - c for w, c in zip(weights, current)],
        "turnover": turnover,
        "trading_cost": cost * turnover,
        "turnover_limit": limit,
        "turnover_limit_binding": binding,
        "turnover_shadow_cost_bps": shadow * 10_000.0,
        "expected_return": point["expected_return"],
        "net_expected_return": point["expected_return"] - cost * turnover,
        "volatility": point["volatility"],
        "current": now,
        "method": (
            f"Mean-variance with trading costs: maximise expected return − {lam:g} × variance − "
            f"{cost * 10_000:g} bp × turnover, starting from the current weights. Turnover is buys plus sells as a "
            "share of the sleeve and its cost is charged once against a year of expected return. Long only, fully "
            f"invested, at most {cap:.0%} in one holding. Solved by accelerated proximal gradient: each step "
            "shrinks small trades to zero (soft-thresholding) and projects onto the allowed weights exactly."
            + (f" A turnover limit of {limit:.0%} is met by raising the cost to its shadow price." if limit is not None else "")
        ),
        "disclaimer": DISCLAIMER,
    }


def _turnover_shadow_price(solve, smooth, current, cost, limit, unconstrained):
    """Extra cost rate θ at which the optimal turnover falls to the limit (regula falsi, Illinois variant).

    Turnover falls as the penalty rises, so θ is bracketed between 0 and a rate at
    which staying put is optimal, then narrowed; the feasible end of the bracket is returned.
    """

    def gap(w):
        return _l1(w, current) - limit

    gradient = smooth(list(current))[1]
    low, w_low, f_low = 0.0, unconstrained, gap(unconstrained)
    high = max(0.5 * (max(gradient) - min(gradient)) * 1.01 - cost, 1e-9)
    w_high = solve(cost + high, unconstrained)
    f_high = gap(w_high)
    for _ in range(60):
        if f_high <= 1e-9:
            break
        low, w_low, f_low = high, w_high, f_high
        high *= 2.0
        w_high = solve(cost + high, w_low)
        f_high = gap(w_high)
    actual_high, side = f_high, 0
    for _ in range(60):
        if actual_high > -1e-7 or high - low <= 1e-15 * max(1.0, high):
            break
        theta = (low * f_high - high * f_low) / (f_high - f_low)
        if not low < theta < high:
            theta = 0.5 * (low + high)
        w_theta = solve(cost + theta, w_high)
        f_theta = gap(w_theta)
        if f_theta > 1e-9:
            low, w_low, f_low = theta, w_theta, f_theta
            if side == -1:
                f_high *= 0.5
            side = -1
        else:
            high, w_high, f_high, actual_high = theta, w_theta, f_theta, f_theta
            if side == 1:
                f_low *= 0.5
            side = 1
    return w_high, high


# 5. Robust mean-variance -----------------------------------------------------------------


def robust_mean_variance(
    mu: Sequence[float],
    covariance: Sequence[Sequence[float]],
    *,
    uncertainty_covariance: Sequence[Sequence[float]],
    kappa: float,
    risk_aversion: float,
    cap: float = DEFAULT_CAP,
) -> dict[str, Any]:
    """Mean-variance with ellipsoidal uncertainty in expected returns (Goldfarb and Iyengar 2003; Ceria and Stubbs 2006).

    The true expected returns are taken to lie in {μ + Ω^½u : ‖u‖ ≤ κ}; the worst case
    within it lowers a portfolio's return by κ√(w'Ωw), so the weights maximise
    w'μ − κ√(w'Ωw) − λw'Σw. A usual choice is Ω = Σ/T (the error of a T-period sample
    mean) and κ² the chi-square quantile for the confidence wanted. κ = 0 is plain
    mean-variance. Solved by accelerated projected gradient with backtracking.
    """

    cov = _square_matrix(covariance)
    n = len(cov)
    expected = _vector(mu, n, "Expected returns")
    omega = _square_matrix(uncertainty_covariance, "uncertainty covariance")
    if len(omega) != n:
        raise ModellingInputError("The uncertainty covariance needs one row and column per holding.")
    radius = _finite(kappa)
    if radius is None or radius < 0:
        raise ModellingInputError("Kappa, the size of the uncertainty region, must be zero or positive.")
    lam = _positive(risk_aversion, "risk aversion")
    cap = _cap(cap, n)
    plain_smooth = _quadratic(cov, expected, lam)
    floor = 1e-14 * max(sum(omega[i][i] for i in range(n)) / n, 1e-300)  # keeps √ smooth if Ω is singular

    def robust_smooth(w: list[float]) -> tuple[float, list[float]]:
        value, gradient = plain_smooth(w)
        if radius == 0:
            return value, gradient
        ow = [_sumprod(row, w) for row in omega]
        root = math.sqrt(_sumprod(w, ow) + floor)
        return value + radius * root, [g + radius * o / root for g, o in zip(gradient, ow)]

    lip = 2.0 * lam * _largest_eigenvalue(cov)

    def prox(values: list[float], _step: float) -> list[float]:
        return _project(values, cap)

    plain = _accelerated(plain_smooth, prox, _project([1.0 / n] * n, cap), lipschitz=lip)
    weights = plain if radius == 0 else _accelerated(robust_smooth, prox, plain, lipschitz=lip)

    def worst_case(w: Sequence[float]) -> float:
        return _sumprod(w, expected) - radius * math.sqrt(max(0.0, _sumprod(w, _matvec(omega, w))))

    point = _point(weights, cov, expected)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "risk_aversion": lam,
        "kappa": radius,
        "maximum_weight": cap,
        "weights": weights,
        "expected_return": point["expected_return"],
        "worst_case_return": worst_case(weights),
        "volatility": point["volatility"],
        "plain_weights": plain,
        "plain_worst_case_return": worst_case(plain),
        "method": (
            "Robust mean-variance (Goldfarb and Iyengar 2003; Ceria and Stubbs 2006): expected returns may be wrong "
            f"anywhere in an ellipsoid of radius κ = {radius:g} shaped by the uncertainty covariance, and the weights "
            f"maximise the worst-case return in it minus {lam:g} × variance. Long only, fully invested, at most "
            f"{cap:.0%} in one holding. The plain weights are the same model with κ = 0."
        ),
        "disclaimer": DISCLAIMER,
    }
