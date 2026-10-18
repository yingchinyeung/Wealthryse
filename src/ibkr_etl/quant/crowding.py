"""Crowding and concentration: who else owns what we own, and which holdings are one bet.

Pure standard-library Python, like :mod:`ibkr_etl.modelling`:

* :func:`ownership_overlap` – from 13F-style filings (fund → security → stake), how
  much of the portfolio sits in names the given funds hold, what share of each
  holding's shares outstanding they own, a crowding score per holding (the share held
  by its N largest holders among those funds) and the funds whose portfolios look most
  like ours (cosine similarity of weight vectors).
* :func:`correlation_clusters` – groups of holdings whose returns move together closely
  enough to count as one bet: average-linkage hierarchical clustering on the
  correlation distance √(½(1 − ρ)) (Mantegna 1999), cut at a chosen correlation.
* :func:`concentration` – Herfindahl index, effective number of holdings and the
  largest 1, 5 and 10 weights.

Weights are fractions of NAV; a short counts by its size in :func:`concentration`
and by its sign in the overlap and cluster weights. Results describe the data given
and are not hypothetical models, except that 13F filings are a quarter old.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..modelling import ModellingInputError
from .holdings_risk import MIN_OBSERVATIONS, _clean_columns, _demean, _dot

MODEL_VERSION = "crowding-v1"
TOP_HOLDERS = 10           # holders counted in a holding's crowding score
TOP_FUNDS = 10             # most similar funds listed
TOP_COUNTS = (1, 5, 10)
UNITS = ("value", "shares", "share_of_outstanding")
DEFAULT_THRESHOLD = 0.7
TOLERANCE = 1e-12


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


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _weights(weights: Mapping[str, Any], label: str = "portfolio weight") -> dict[str, float]:
    if not isinstance(weights, Mapping) or not weights:
        raise ModellingInputError(f"Give each holding's {label} as a mapping of holding to weight.")
    return {str(key): _number(value, f"The {label} of {key}") for key, value in weights.items()}


# 1. Concentration -----------------------------------------------------------------------


def concentration(weights: Mapping[str, float] | Sequence[float]) -> dict[str, Any]:
    """Herfindahl index, effective number of holdings and the largest weights.

    Each weight's size |wᵢ| is taken as a share of the gross, sᵢ = |wᵢ| / Σ|wⱼ|. The
    Herfindahl-Hirschman index is H = Σ sᵢ² (Herfindahl 1950; Hirschman 1945), from
    1/N for N equal holdings to 1 for a single one, and the effective number of
    holdings is 1/H. Top-k weights are the sums of the k largest |wᵢ| in the units
    given (fractions of NAV) and, as ``top_shares``, as shares of the gross.
    """

    if isinstance(weights, Mapping):
        values = _weights(weights, "weight")
    else:
        values = {str(i + 1): _number(v, f"Weight {i + 1}") for i, v in enumerate(weights)}
    if not values:
        raise ModellingInputError("Give at least one weight.")
    sizes = sorted(((abs(v), k) for k, v in values.items()), key=lambda pair: (-pair[0], pair[1]))
    gross = math.fsum(size for size, _ in sizes)
    if gross <= 0:
        raise ModellingInputError("All the weights are zero, so there is nothing to measure.")
    herfindahl = math.fsum((size / gross) ** 2 for size, _ in sizes)
    top = {str(k): math.fsum(size for size, _ in sizes[:k]) for k in TOP_COUNTS}
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "holdings": sum(1 for size, _ in sizes if size > 0),
        "gross_weight": gross,
        "herfindahl": herfindahl,
        "effective_holdings": 1.0 / herfindahl,
        "largest": sizes[0][1],
        "top_weights": top,
        "top_shares": {k: v / gross for k, v in top.items()},
        "method": (
            "Each holding's size as a share of the gross (shorts count by their size). The Herfindahl index is the "
            "sum of the squared shares, from 1/N for N equal holdings to 1 for one; the effective number of holdings "
            "is one over it. Top weights add up the largest holdings."
        ),
    }


# 2. Ownership overlap -------------------------------------------------------------------


def _stake(raw: Any, unit: str, outstanding: float | None, where: str) -> tuple[float | None, float | None]:
    """A stake's market value and its share of shares outstanding, either of which may be unknown."""

    if isinstance(raw, Mapping):
        value = None if _missing(raw.get("value")) else _number(raw["value"], f"The value of {where}")
        shares = None if _missing(raw.get("shares")) else _number(raw["shares"], f"The shares of {where}")
        share = (None if _missing(raw.get("share_of_outstanding"))
                 else _number(raw["share_of_outstanding"], f"The share of outstanding of {where}"))
    else:
        amount = _number(raw, f"The stake of {where}")
        value, shares, share = (amount if unit == "value" else None, amount if unit == "shares" else None,
                                amount if unit == "share_of_outstanding" else None)
    if any(v is not None and v < 0 for v in (value, shares, share)):
        raise ModellingInputError(f"The stake of {where} is negative; 13F filings report long positions only.")
    if share is not None and share > 1.0 + TOLERANCE:
        raise ModellingInputError(f"The stake of {where} is more than all the shares outstanding.")
    if share is None and shares is not None and outstanding:
        share = shares / outstanding
    return value, share


def _outstanding(shares_outstanding: Mapping[str, Any] | None) -> dict[str, float]:
    result = {}
    for key, raw in (shares_outstanding or {}).items():
        if _missing(raw):
            continue
        amount = _number(raw, f"The shares outstanding of {key}")
        if amount <= 0:
            raise ModellingInputError(f"The shares outstanding of {key} must be positive.")
        result[str(key)] = amount
    return result


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float | None:
    norm = math.sqrt(math.fsum(v * v for v in left.values()) * math.fsum(v * v for v in right.values()))
    if norm <= 0:
        return None
    return math.fsum(v * right[k] for k, v in left.items() if k in right) / norm


def ownership_overlap(
    portfolio_weights: Mapping[str, float],
    institutional_holdings: Mapping[str, Mapping[str, Any]],
    *,
    shares_outstanding: Mapping[str, float] | None = None,
    unit: str = "value",
    top_n: int = TOP_HOLDERS,
    top_funds: int = TOP_FUNDS,
) -> dict[str, Any]:
    """How much the portfolio shares its names with the funds given, and which funds look like it.

    ``institutional_holdings`` maps fund → security → stake, as in 13F filings. A stake
    is a number in ``unit`` (``"value"``, the reported market value; ``"shares"``; or
    ``"share_of_outstanding"``, a fraction) or a mapping with any of ``value``,
    ``shares`` and ``share_of_outstanding``. Shares become a share of the company with
    ``shares_outstanding`` (security → shares outstanding, or the free float for a
    share of float). Pass the funds you count as large, such as the biggest filers.

    * A holding's ownership is the share of it the funds own together; its crowding
      score is the share owned by its ``top_n`` largest holders among them. Both need
      shares outstanding (or stakes given as shares of it).
    * The portfolio's weighted ownership is Σ|wᵢ|·ownershipᵢ / Σ|wᵢ| over the holdings
      where it is known; ``coverage`` is their share of the portfolio.
    * A fund's similarity is the cosine of the angle between its weight vector (its
      stakes' values over its total) and the portfolio's, over all securities: 1 for
      the same mix, 0 for nothing in common, negative when we are short its names.
    """

    if unit not in UNITS:
        raise ModellingInputError(f"Unknown stake unit {unit!r}; use 'value', 'shares' or 'share_of_outstanding'.")
    for count, label in ((top_n, "top_n"), (top_funds, "top_funds")):
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ModellingInputError(f"{label} must be a whole number of at least 1.")
    weights = {k: v for k, v in _weights(portfolio_weights).items() if v != 0}
    if not weights:
        raise ModellingInputError("All the portfolio weights are zero.")
    if not institutional_holdings:
        raise ModellingInputError("There are no fund holdings to compare with.")
    outstanding = _outstanding(shares_outstanding)
    values: dict[str, dict[str, float]] = {}
    ownership: dict[str, list[tuple[float, str]]] = {}
    holders: dict[str, set[str]] = {}
    skipped = 0
    for fund, stakes in institutional_holdings.items():
        fund = str(fund)
        values[fund] = {}
        for security, raw in (stakes or {}).items():
            security = str(security)
            if _missing(raw):
                skipped += 1
                continue
            value, share = _stake(raw, unit, outstanding.get(security), f"{fund} in {security}")
            if value is None and share is None:
                skipped += 1
                continue
            holders.setdefault(security, set()).add(fund)
            if value is not None and value > 0:
                values[fund][security] = value
            if share is not None:
                ownership.setdefault(security, []).append((share, fund))
    gross = math.fsum(abs(w) for w in weights.values())
    rows, above_outstanding = [], []
    for security, weight in sorted(weights.items(), key=lambda item: (-abs(item[1]), item[0])):
        shares = sorted(ownership.get(security, []), key=lambda pair: (-pair[0], pair[1]))
        owned = math.fsum(s for s, _ in shares) if shares else None
        if owned is not None and owned > 1.0 + TOLERANCE:
            above_outstanding.append(security)
        rows.append({
            "security": security,
            "weight": weight,
            "funds_holding": len(holders.get(security, ())),
            "ownership": owned,
            "crowding_score": math.fsum(s for s, _ in shares[:top_n]) if shares else None,
            "largest_holders": [{"fund": f, "share_of_outstanding": s} for s, f in shares[:top_n]],
        })
    known = [r for r in rows if r["ownership"] is not None]
    known_weight = math.fsum(abs(r["weight"]) for r in known)
    held_weight = math.fsum(abs(r["weight"]) for r in rows if r["funds_holding"])
    funds = []
    for fund, stakes in values.items():
        total = math.fsum(stakes.values())
        if total <= 0:
            continue
        mix = {k: v / total for k, v in stakes.items()}
        shared = [k for k in weights if k in mix]
        funds.append({
            "fund": fund,
            "similarity": _cosine(weights, mix),
            "shared_holdings": len(shared),
            "portfolio_weight_in_fund_names": math.fsum(abs(weights[k]) for k in shared) / gross,
            "fund_weight_in_portfolio_names": math.fsum(mix[k] for k in shared),
            "reported_value": total,
        })
    funds.sort(key=lambda r: (-(r["similarity"] or 0.0), r["fund"]))
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "funds": len(institutional_holdings),
        "top_n": top_n,
        "share_in_names_funds_hold": held_weight / gross,
        "weighted_ownership": math.fsum(abs(r["weight"]) * r["ownership"] for r in known) / known_weight
        if known_weight else None,
        "weighted_crowding_score": math.fsum(abs(r["weight"]) * r["crowding_score"] for r in known) / known_weight
        if known_weight else None,
        "coverage": known_weight / gross,
        "holdings": rows,
        "most_similar_funds": funds[:top_funds],
        "funds_without_values": sorted(f for f, stakes in values.items() if not stakes),
        "ownership_above_outstanding": above_outstanding,
        "stakes_skipped": skipped,
        "method": (
            "From the funds' filings: each holding's ownership is the share of its shares outstanding the funds own "
            f"together, and its crowding score the share owned by its {top_n} largest holders among them; the "
            "portfolio figures weight these by the size of each holding. A fund's similarity is the cosine of its "
            "weight vector with the portfolio's (1 is the same mix, 0 nothing in common). 13F filings are made 45 "
            "days after each quarter, cover US-listed long positions only and leave out shorts, so they lag and miss "
            "part of each fund."
        ),
    }


# 3. Correlation clusters ----------------------------------------------------------------


def _correlations(columns: Sequence[Sequence[float]]) -> tuple[list[list[float]], list[int]]:
    """Pearson correlations; a holding that never moves is uncorrelated with everything."""

    centred = [_demean(column) for column in columns]
    norms = [math.sqrt(_dot(c, c)) for c in centred]
    n = len(columns)
    matrix = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if norms[i] > 0 and norms[j] > 0:
                rho = max(-1.0, min(1.0, _dot(centred[i], centred[j]) / (norms[i] * norms[j])))
                matrix[i][j] = matrix[j][i] = rho
    return matrix, [i for i in range(n) if norms[i] == 0]


def _average_linkage(distance: list[list[float]], cut: float) -> tuple[list[list[int]], list[dict[str, Any]]]:
    """Agglomerate while the closest pair of clusters is within ``cut`` (Lance-Williams update for UPGMA)."""

    clusters = {i: [i] for i in range(len(distance))}
    d = {i: {j: distance[i][j] for j in clusters if j != i} for i in clusters}
    merges = []
    while len(clusters) > 1:
        best = min(((d[a][b], a, b) for a in clusters for b in clusters if a < b))
        gap, a, b = best
        if gap > cut + TOLERANCE:
            break
        na, nb = len(clusters[a]), len(clusters[b])
        for k in clusters:
            if k not in (a, b):
                d[a][k] = d[k][a] = (na * d[a][k] + nb * d[b][k]) / (na + nb)
        clusters[a] = sorted(clusters[a] + clusters.pop(b))
        del d[b]
        for k in d:
            d[k].pop(b, None)
        merges.append({"distance": gap, "equivalent_correlation": 1.0 - 2.0 * gap * gap, "members": clusters[a]})
    return list(clusters.values()), merges


def correlation_clusters(
    columns: Mapping[str, Sequence[float]] | Sequence[Sequence[float]],
    names: Sequence[str] | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Groups of holdings that behave as one bet, from their daily returns.

    The distance between two holdings is d = √(½(1 − ρ)), 0 for perfect correlation
    and 1 for perfectly opposite moves (Mantegna 1999, "Hierarchical structure in
    financial markets"). Average-linkage (UPGMA) clustering joins the two closest
    clusters, the distance between clusters being the average distance between their
    members, until the closest pair is farther apart than √(½(1 − threshold)). The
    cut is on average distance, so a cluster's average internal correlation can sit a
    little below the threshold. With ``weights`` (fractions of NAV) each cluster gets
    its combined (net) and gross weight; holdings without a weight count as zero.
    """

    threshold = _number(threshold, "The correlation threshold")
    if not -1.0 <= threshold <= 1.0:
        raise ModellingInputError("The correlation threshold must be between -1 and 1, such as 0.7.")
    if not isinstance(columns, Mapping):
        columns = list(columns)
        if names is None or len(names) != len(columns):
            raise ModellingInputError("Give one name for each return series.")
        if len({str(n) for n in names}) != len(names):
            raise ModellingInputError("The holding names must be different from each other.")
        columns = {str(n): c for n, c in zip(names, columns)}
    labels, clean = _clean_columns(columns, minimum=MIN_OBSERVATIONS)
    given = _weights(weights, "weight") if weights is not None else None
    correlation, still = _correlations(clean)
    # The square root turns a rounding error of 1e-16 in ρ = 1 into a distance of 1e-8, so snap it to zero.
    distance = [[math.sqrt(0.5 * (1.0 - rho)) if 1.0 - rho > TOLERANCE else 0.0 for rho in row]
                for row in correlation]
    cut = math.sqrt(max(0.0, 0.5 * (1.0 - threshold)))
    groups, merges = _average_linkage(distance, cut)
    rows = []
    for members in groups:
        pairs = [correlation[i][j] for x, i in enumerate(members) for j in members[x + 1:]]
        member_weights = [given.get(labels[i], 0.0) for i in members] if given is not None else None
        order = sorted(members, key=lambda i: (-abs(given.get(labels[i], 0.0)) if given else 0.0, i))
        rows.append({
            "members": [labels[i] for i in order],
            "size": len(members),
            "average_correlation": math.fsum(pairs) / len(pairs) if pairs else None,
            "combined_weight": math.fsum(member_weights) if member_weights is not None else None,
            "gross_weight": math.fsum(abs(w) for w in member_weights) if member_weights is not None else None,
        })
    rows.sort(key=lambda r: (-abs(r["combined_weight"] or 0.0), -r["size"], r["members"][0]))
    for merge in merges:
        merge["members"] = [labels[i] for i in merge["members"]]
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "threshold": threshold,
        "distance_cut": cut,
        "observations": len(clean[0]),
        "clusters": rows,
        "cluster_count": len(rows),
        "multi_holding_clusters": sum(1 for r in rows if r["size"] > 1),
        "merges": merges,
        "not_moving": [labels[i] for i in still],
        "weights_not_clustered": sorted(set(given) - set(labels)) if given is not None else [],
        "method": (
            "Correlations of the holdings' daily returns turned into distances √(½(1 − ρ)) (Mantegna 1999), then "
            "average-linkage hierarchical clustering: the two closest groups join, the distance between groups being "
            f"the average distance between their members, until no two groups are within the distance of a "
            f"{threshold:g} correlation. Each group moves as one bet; its weight is the net of its members'. A "
            "holding whose price never moved is treated as uncorrelated with the rest."
        ),
    }
