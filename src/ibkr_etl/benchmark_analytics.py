from __future__ import annotations

import math
import hashlib
import random
import statistics
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from .market_calendar import (
    BENCHMARK_MARKETS, CALENDARS, CALENDAR_VERSION, CalendarCoverageError,
    benchmark_session_on_or_before,
)


CALCULATION_VERSION = "benchmark-comparison-v1"


@dataclass(frozen=True)
class BenchmarkContract:
    benchmark_id: str
    currency: str
    return_convention: str
    observation_status: str
    license_status: str


@dataclass(frozen=True)
class QualityIssue:
    check_id: str
    status: str
    message: str


@dataclass(frozen=True)
class ComparisonRow:
    observation_date: str
    portfolio_value: float
    benchmark_level: float
    normalized_portfolio: float
    normalized_benchmark: float
    portfolio_return: float | None
    benchmark_return: float | None
    excess_return: float | None


@dataclass(frozen=True)
class ComparisonResult:
    subject_id: str
    benchmark_id: str
    reporting_currency: str
    calculation_version: str
    quality_status: str
    issues: tuple[QualityIssue, ...]
    rows: tuple[ComparisonRow, ...]
    metrics: Mapping[str, float | int | str | None]

    def as_dict(self) -> dict:
        return asdict(self)


class ComparisonValidationError(ValueError):
    """Raised for malformed inputs that cannot safely be interpreted."""


def _validated_series(
    values: Mapping[str, float] | Iterable[tuple[str, float]], name: str
) -> dict[str, float]:
    items = values.items() if isinstance(values, Mapping) else values
    result: dict[str, float] = {}
    for raw_day, raw_value in items:
        try:
            parsed_day = date.fromisoformat(str(raw_day)).isoformat()
            value = float(raw_value)
        except (TypeError, ValueError) as error:
            raise ComparisonValidationError(
                f"{name} contains an invalid observation: {raw_day!r}, {raw_value!r}"
            ) from error
        if parsed_day in result:
            raise ComparisonValidationError(
                f"{name} contains a duplicate date: {parsed_day}"
            )
        if not math.isfinite(value) or value <= 0:
            raise ComparisonValidationError(
                f"{name} must contain only finite positive values: {parsed_day}"
            )
        result[parsed_day] = value
    if not result:
        raise ComparisonValidationError(f"{name} is empty")
    return result


def _validated_flows(
    values: Mapping[str, float] | Iterable[tuple[str, float]] | None,
) -> dict[str, float]:
    if values is None:
        return {}
    items = values.items() if isinstance(values, Mapping) else values
    result: dict[str, float] = {}
    for raw_day, raw_value in items:
        try:
            parsed_day = date.fromisoformat(str(raw_day)).isoformat()
            value = float(raw_value)
        except (TypeError, ValueError) as error:
            raise ComparisonValidationError(
                f"cash flows contain an invalid observation: {raw_day!r}, {raw_value!r}"
            ) from error
        if not math.isfinite(value):
            raise ComparisonValidationError(
                f"cash flow must be finite: {parsed_day}"
            )
        result[parsed_day] = result.get(parsed_day, 0.0) + value
    return result


def _annualized_return(total_return: float, start: str, end: str) -> float | None:
    growth = 1.0 + total_return
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    if growth <= 0 or days <= 0:
        return None
    return growth ** (365.25 / days) - 1.0


def _drawdown(returns: list[float]) -> float | None:
    if not returns:
        return None
    wealth = peak = 1.0
    maximum = 0.0
    for daily_return in returns:
        wealth *= 1.0 + daily_return
        peak = max(peak, wealth)
        maximum = min(maximum, wealth / peak - 1.0)
    return maximum


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _simulated_drawdown_distribution(
    returns: list[float],
    *,
    horizon: int = 252,
    paths: int = 5_000,
    block_length: int = 5,
) -> list[float]:
    """Deterministic moving-block bootstrap distribution of maximum drawdown."""
    if len(returns) < max(20, block_length) or horizon < 2 or paths < 1:
        return []
    seed_material = ",".join(f"{value:.12g}" for value in returns).encode("ascii")
    seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
    generator = random.Random(seed)
    maximum_start = len(returns) - block_length
    drawdowns: list[float] = []
    for _ in range(paths):
        simulated: list[float] = []
        while len(simulated) < horizon:
            start = generator.randint(0, maximum_start)
            simulated.extend(returns[start : start + block_length])
        drawdowns.append(float(_drawdown(simulated[:horizon]) or 0.0))
    return drawdowns


def _one_year_drawdown_metrics(
    measured: list[ComparisonRow],
) -> dict[str, float | int | str | None]:
    returns = [float(row.portfolio_return) for row in measured]
    simulated = _simulated_drawdown_distribution(returns)
    tail_cutoff = _quantile(simulated, 0.05)
    tail = [value for value in simulated if tail_cutoff is not None and value <= tail_cutoff]
    crisis = [
        float(row.portfolio_return)
        for row in measured
        if row.portfolio_return is not None
        and "2008-09-15" <= row.observation_date <= "2009-03-09"
    ]
    return {
        "expected_maximum_drawdown_1y": (
            statistics.fmean(simulated) if simulated else None
        ),
        "actual_maximum_drawdown_1y": _drawdown(returns[-252:]),
        "tail_maximum_drawdown_1y": statistics.fmean(tail) if tail else None,
        "crisis_maximum_drawdown": _drawdown(crisis) if len(crisis) >= 2 else None,
        "crisis_period_start": "2008-09-15",
        "crisis_period_end": "2009-03-09",
        "drawdown_simulation_paths": len(simulated),
        "drawdown_horizon_sessions": 252,
        "drawdown_block_length": 5,
        "drawdown_tail_confidence": 0.95,
    }


def expected_tail_loss_contributors(
    rows: list[ComparisonRow],
    position_rows: Iterable[Mapping[str, Any]],
    *,
    reporting_currency: str = "USD",
    confidence: float = 0.95,
    limit: int = 5,
) -> dict[str, Any]:
    """Attribute historical ETL using unchanged carried positions on tail days.

    A holding is attributed only when its aggregate quantity and currency are
    unchanged between adjacent validated portfolio dates. Everything else—cash,
    fees, FX, trading and unmatched position activity—remains in the residual.
    """
    measured = [row for row in rows if row.portfolio_return is not None]
    returns = [float(row.portfolio_return) for row in measured]
    cutoff = _quantile(returns, 1.0 - confidence)
    tail_dates = expected_tail_loss_dates(rows, confidence=confidence)
    expected_tail_loss = (
        max(
            0.0,
            -statistics.fmean(
                float(row.portfolio_return)
                for row in measured
                if row.observation_date in tail_dates
            ),
        )
        if tail_dates
        else None
    )

    positions: dict[str, dict[str, dict[str, Any]]] = {}
    for raw in position_rows:
        day = str(raw["snapshot_date"])
        conid = str(raw["conid"])
        currency = str(raw.get("currency") or "").upper()
        try:
            quantity = float(raw.get("quantity_text") or 0.0)
            market_value = float(raw.get("market_value_text") or 0.0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(quantity) or not math.isfinite(market_value):
            continue
        aggregate = positions.setdefault(day, {}).setdefault(
            conid,
            {
                "quantity": 0.0,
                "market_value": 0.0,
                "currency": currency,
                "symbol": str(raw.get("symbol") or conid),
                "description": str(raw.get("description") or ""),
            },
        )
        if aggregate["currency"] != currency:
            aggregate["currency"] = "MIXED"
        aggregate["quantity"] += quantity
        aggregate["market_value"] += market_value

    previous_by_date: dict[str, ComparisonRow] = {}
    for previous, current in zip(rows, rows[1:]):
        previous_by_date[current.observation_date] = previous
    contribution_sums: dict[str, float] = {}
    metadata: dict[str, dict[str, str]] = {}
    attributed_pairs = 0
    excluded_pairs = 0
    for current in measured:
        if current.observation_date not in tail_dates:
            continue
        previous = previous_by_date.get(current.observation_date)
        if previous is None or previous.portfolio_value <= 0:
            excluded_pairs += 1
            continue
        before = positions.get(previous.observation_date, {})
        after = positions.get(current.observation_date, {})
        for conid in before.keys() & after.keys():
            prior_position = before[conid]
            current_position = after[conid]
            if (
                prior_position["currency"] != reporting_currency
                or current_position["currency"] != reporting_currency
            ):
                excluded_pairs += 1
                continue
            prior_quantity = float(prior_position["quantity"])
            current_quantity = float(current_position["quantity"])
            tolerance = 1e-8 * max(1.0, abs(prior_quantity), abs(current_quantity))
            if abs(current_quantity - prior_quantity) > tolerance:
                excluded_pairs += 1
                continue
            contribution_sums[conid] = contribution_sums.get(conid, 0.0) + (
                float(current_position["market_value"])
                - float(prior_position["market_value"])
            ) / float(previous.portfolio_value)
            metadata[conid] = {
                "symbol": str(current_position["symbol"]),
                "description": str(current_position["description"]),
            }
            attributed_pairs += 1

    tail_count = len(tail_dates)
    contributors = [
        {
            "conid": conid,
            **metadata[conid],
            "expected_tail_loss_contribution": -value / tail_count,
        }
        for conid, value in contribution_sums.items()
        if tail_count and value != 0.0
    ]
    contributors.sort(
        key=lambda item: float(item["expected_tail_loss_contribution"]), reverse=True
    )
    attributed_total = sum(
        float(item["expected_tail_loss_contribution"]) for item in contributors
    )
    residual = (
        float(expected_tail_loss) - attributed_total
        if expected_tail_loss is not None
        else None
    )
    bottom = sorted(
        contributors,
        key=lambda item: float(item["expected_tail_loss_contribution"]),
    )[:limit]
    return {
        "confidence": confidence,
        "daily_expected_tail_loss": expected_tail_loss,
        "tail_return_threshold": cutoff,
        "tail_observations": tail_count,
        "top": contributors[:limit],
        "bottom": bottom,
        "attributed_total": attributed_total,
        "residual_contribution": residual,
        "attributed_position_pairs": attributed_pairs,
        "excluded_position_pairs": excluded_pairs,
        "method": "unchanged-position market-value contribution on portfolio tail days",
    }


def expected_tail_loss_dates(
    rows: list[ComparisonRow], *, confidence: float = 0.95
) -> set[str]:
    """Return the validated portfolio dates that form the historical ETL tail.

    Keeping this selection in one place lets the database layer load positions
    only for the tail dates and their immediately preceding observations rather
    than loading the complete daily position history for every page request.
    """
    measured = [row for row in rows if row.portfolio_return is not None]
    cutoff = _quantile(
        [float(row.portfolio_return) for row in measured], 1.0 - confidence
    )
    return {
        row.observation_date
        for row in measured
        if cutoff is not None and float(row.portfolio_return) <= cutoff
    }


def portfolio_risk_metrics(
    rows: list[ComparisonRow], risk_free_annual_rate: float | None = None
) -> dict[str, float | int | str | None]:
    """Calculate deterministic portfolio risk from cash-flow-adjusted daily returns.

    The comparison rows are used because their portfolio returns have already
    passed common-date, currency and cash-flow validation. A missing risk-free
    rate deliberately leaves Sharpe unavailable rather than estimating one.
    """
    measured = [row for row in rows if row.portfolio_return is not None]
    returns = [float(row.portfolio_return) for row in measured]
    if not rows:
        return {
            "period_start": None,
            "period_end": None,
            "observations": 0,
            "return_observations": 0,
        }
    total_return = math.prod(1.0 + value for value in returns) - 1.0 if returns else None
    annualized_return = (
        _annualized_return(total_return, rows[0].observation_date, rows[-1].observation_date)
        if total_return is not None
        else None
    )
    daily_mean = statistics.fmean(returns) if returns else None
    daily_volatility = statistics.stdev(returns) if len(returns) >= 2 else None
    risk_free_daily = (
        (1.0 + risk_free_annual_rate) ** (1.0 / 252.0) - 1.0
        if risk_free_annual_rate is not None and risk_free_annual_rate > -1.0
        else None
    )
    fifth_percentile = _quantile(returns, 0.05)
    tail = (
        [value for value in returns if fifth_percentile is not None and value <= fifth_percentile]
        if returns
        else []
    )
    result = {
        "period_start": rows[0].observation_date,
        "period_end": rows[-1].observation_date,
        "observations": len(rows),
        "return_observations": len(returns),
        "total_return": total_return,
        "annualized_return": annualized_return,
        "annualized_volatility": (
            daily_volatility * math.sqrt(252) if daily_volatility is not None else None
        ),
        "risk_free_annual_rate": risk_free_annual_rate,
        "sharpe_ratio": (
            (daily_mean - risk_free_daily) / daily_volatility * math.sqrt(252)
            if daily_mean is not None
            and risk_free_daily is not None
            and daily_volatility not in (None, 0)
            else None
        ),
        "maximum_drawdown": _drawdown(returns),
        "var_95_daily": max(0.0, -fifth_percentile) if fifth_percentile is not None else None,
        "cvar_95_daily": max(0.0, -statistics.fmean(tail)) if tail else None,
        "win_rate": (
            sum(value > 0 for value in returns) / len(returns) if returns else None
        ),
        "blue_mean_daily_return": daily_mean,
        "blue_standard_error": (
            daily_volatility / math.sqrt(len(returns))
            if daily_volatility is not None and returns
            else None
        ),
    }
    result.update(_one_year_drawdown_metrics(measured))
    result["daily_expected_tail_loss"] = result["cvar_95_daily"]
    return result


def _capture(portfolio: list[float], benchmark: list[float], positive: bool) -> float | None:
    selected = [
        (portfolio_return, benchmark_return)
        for portfolio_return, benchmark_return in zip(portfolio, benchmark)
        if (benchmark_return > 0 if positive else benchmark_return < 0)
    ]
    if not selected:
        return None
    portfolio_growth = math.prod(1.0 + pair[0] for pair in selected)
    benchmark_growth = math.prod(1.0 + pair[1] for pair in selected)
    if portfolio_growth <= 0 or benchmark_growth <= 0:
        return None
    periods = len(selected)
    portfolio_annualized = portfolio_growth ** (252.0 / periods) - 1.0
    benchmark_annualized = benchmark_growth ** (252.0 / periods) - 1.0
    if benchmark_annualized == 0:
        return None
    return 100.0 * portfolio_annualized / benchmark_annualized


def comparison_metrics(
    rows: list[ComparisonRow],
) -> dict[str, float | int | str | None]:
    measured = [row for row in rows if row.portfolio_return is not None]
    portfolio = [float(row.portfolio_return) for row in measured]
    benchmark = [float(row.benchmark_return) for row in measured]
    excess = [float(row.excess_return) for row in measured]
    first, last = rows[0], rows[-1]
    portfolio_total = first.normalized_portfolio and (
        last.normalized_portfolio / first.normalized_portfolio - 1.0
    )
    benchmark_total = first.normalized_benchmark and (
        last.normalized_benchmark / first.normalized_benchmark - 1.0
    )
    portfolio_volatility = (
        statistics.stdev(portfolio) * math.sqrt(252) if len(portfolio) >= 2 else None
    )
    benchmark_volatility = (
        statistics.stdev(benchmark) * math.sqrt(252) if len(benchmark) >= 2 else None
    )
    tracking_error = (
        statistics.stdev(excess) * math.sqrt(252) if len(excess) >= 2 else None
    )
    information_ratio = (
        statistics.fmean(excess) / statistics.stdev(excess) * math.sqrt(252)
        if len(excess) >= 2 and statistics.stdev(excess) != 0
        else None
    )
    benchmark_variance = statistics.variance(benchmark) if len(benchmark) >= 2 else None
    covariance = (
        statistics.covariance(portfolio, benchmark) if len(portfolio) >= 2 else None
    )
    beta = (
        covariance / benchmark_variance
        if covariance is not None and benchmark_variance not in (None, 0)
        else None
    )
    correlation = (
        statistics.correlation(portfolio, benchmark)
        if len(portfolio) >= 2
        and statistics.stdev(portfolio) != 0
        and statistics.stdev(benchmark) != 0
        else None
    )
    return {
        "period_start": first.observation_date,
        "period_end": last.observation_date,
        "observations": len(rows),
        "return_observations": len(portfolio),
        "portfolio_total_return": portfolio_total,
        "benchmark_total_return": benchmark_total,
        "excess_total_return": (
            portfolio_total - benchmark_total
            if portfolio_total is not None and benchmark_total is not None
            else None
        ),
        "portfolio_annualized_return": _annualized_return(
            float(portfolio_total), first.observation_date, last.observation_date
        ),
        "benchmark_annualized_return": _annualized_return(
            float(benchmark_total), first.observation_date, last.observation_date
        ),
        "portfolio_annualized_volatility": portfolio_volatility,
        "benchmark_annualized_volatility": benchmark_volatility,
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "beta": beta,
        "correlation": correlation,
        "portfolio_max_drawdown": _drawdown(portfolio),
        "benchmark_max_drawdown": _drawdown(benchmark),
        "relative_win_rate": (
            sum(value > 0 for value in excess) / len(excess) if excess else None
        ),
        "upside_capture_pct": _capture(portfolio, benchmark, True),
        "downside_capture_pct": _capture(portfolio, benchmark, False),
    }


def build_comparison(
    *,
    subject_id: str,
    reporting_currency: str,
    portfolio_currency: str,
    portfolio_values: Mapping[str, float] | Iterable[tuple[str, float]],
    cash_flows: Mapping[str, float] | Iterable[tuple[str, float]] | None,
    benchmark: BenchmarkContract,
    benchmark_levels: Mapping[str, float] | Iterable[tuple[str, float]],
    start_date: str,
    end_date: str | None = None,
    expected_as_of: str | None = None,
    maximum_staleness_days: int = 4,
    minimum_return_observations: int = 30,
    maximum_absolute_portfolio_return: float = 1.0,
    maximum_absolute_benchmark_return: float = 0.5,
    development_mode: bool = False,
    required_return_convention: str = "gross_total_return",
) -> ComparisonResult:
    """Build a common-date, cash-flow-adjusted portfolio/benchmark comparison.

    Currency conversion must occur before calling this function. This deliberate
    boundary prevents an implicit or forward-looking FX assumption.
    """

    start = date.fromisoformat(start_date).isoformat()
    end = date.fromisoformat(end_date).isoformat() if end_date else None
    expected = date.fromisoformat(expected_as_of) if expected_as_of else None
    portfolio = _validated_series(portfolio_values, "portfolio values")
    levels = _validated_series(benchmark_levels, "benchmark levels")
    flows = _validated_flows(cash_flows)
    issues: list[QualityIssue] = []

    reporting_currency = reporting_currency.upper()
    if portfolio_currency.upper() != reporting_currency:
        issues.append(
            QualityIssue(
                "portfolio_currency",
                "fail",
                "Portfolio values were not converted to the reporting currency.",
            )
        )
    if benchmark.currency.upper() != reporting_currency:
        issues.append(
            QualityIssue(
                "benchmark_currency",
                "fail",
                "Benchmark levels were not converted to the reporting currency.",
            )
        )
    if benchmark.return_convention != required_return_convention:
        issues.append(
            QualityIssue(
                "benchmark_convention",
                "fail",
                "The configured benchmark does not match the approved "
                f"{required_return_convention} convention.",
            )
        )
    if benchmark.observation_status != "official_close":
        issues.append(
            QualityIssue(
                "benchmark_observation_status",
                "warn" if development_mode else "fail",
                "Provisional yfinance observations are approved for internal and shareholder analysis only; client distribution is prohibited until Bloomberg is installed."
                if development_mode
                else "Only official closing benchmark observations may be published.",
            )
        )
    if benchmark.license_status != "approved":
        issues.append(
            QualityIssue(
                "benchmark_license",
                "warn" if development_mode else "fail",
                "The yfinance benchmark feed is restricted to internal use and is not licensed for client distribution."
                if development_mode
                else "The benchmark provider and redistribution rights are not approved.",
            )
        )

    common_dates = sorted(
        day
        for day in set(portfolio).intersection(levels)
        if day >= start and (end is None or day <= end)
    )
    if len(common_dates) < 2:
        issues.append(
            QualityIssue(
                "common_dates", "fail", "Fewer than two common valid observations exist."
            )
        )
        return ComparisonResult(
            subject_id,
            benchmark.benchmark_id,
            reporting_currency,
            CALCULATION_VERSION,
            "fail",
            tuple(issues),
            (),
            {},
        )
    if common_dates[0] != start:
        issues.append(
            QualityIssue(
                "normalization_date",
                "warn",
                f"The first common valid observation is {common_dates[0]}, not {start}.",
            )
        )
    if expected is not None:
        observed = date.fromisoformat(common_dates[-1])
        market = BENCHMARK_MARKETS.get(benchmark.benchmark_id)
        # Preserve the explicitly configured legacy policy for historical
        # periods predating the reviewed calendar. Never guess future holidays.
        if market and expected >= CALENDARS[market].coverage_start:
            try:
                required = benchmark_session_on_or_before(benchmark.benchmark_id, expected)
                valid_session = CALENDARS[market].is_session(observed)
                if observed != required or not valid_session:
                    issues.append(QualityIssue(
                        "freshness", "fail",
                        f"Latest common observation {observed} does not match the required "
                        f"{market} session {required} ({CALENDAR_VERSION}).",
                    ))
            except CalendarCoverageError as error:
                issues.append(QualityIssue("freshness", "fail", str(error)))
        elif (expected - observed).days > maximum_staleness_days:
            issues.append(
                QualityIssue(
                    "freshness",
                    "fail",
                    f"The latest common observation is {(expected - observed).days} calendar days stale.",
                )
            )

    rows = [
        ComparisonRow(
            common_dates[0],
            portfolio[common_dates[0]],
            levels[common_dates[0]],
            100.0,
            100.0,
            None,
            None,
            None,
        )
    ]
    normalized_portfolio = normalized_benchmark = 100.0
    for previous_day, current_day in zip(common_dates, common_dates[1:]):
        external_flow = sum(
            amount
            for flow_day, amount in flows.items()
            if previous_day < flow_day <= current_day
        )
        denominator = portfolio[previous_day] + 0.5 * external_flow
        if denominator <= 0:
            issues.append(
                QualityIssue(
                    "portfolio_return_denominator",
                    "fail",
                    f"The cash-flow-adjusted denominator is not positive on {current_day}.",
                )
            )
            continue
        portfolio_return = (
            portfolio[current_day] - portfolio[previous_day] - external_flow
        ) / denominator
        benchmark_return = levels[current_day] / levels[previous_day] - 1.0
        if not math.isfinite(portfolio_return) or portfolio_return <= -1:
            issues.append(
                QualityIssue(
                    "portfolio_return_validity",
                    "fail",
                    f"The portfolio return is invalid on {current_day}.",
                )
            )
            continue
        if abs(portfolio_return) > maximum_absolute_portfolio_return:
            issues.append(
                QualityIssue(
                    "portfolio_return_reasonableness",
                    "fail",
                    f"The portfolio return exceeds the review threshold on {current_day}.",
                )
            )
        if abs(benchmark_return) > maximum_absolute_benchmark_return:
            issues.append(
                QualityIssue(
                    "benchmark_return_reasonableness",
                    "fail",
                    f"The benchmark return exceeds the review threshold on {current_day}.",
                )
            )
        normalized_portfolio *= 1.0 + portfolio_return
        normalized_benchmark *= 1.0 + benchmark_return
        rows.append(
            ComparisonRow(
                current_day,
                portfolio[current_day],
                levels[current_day],
                normalized_portfolio,
                normalized_benchmark,
                portfolio_return,
                benchmark_return,
                portfolio_return - benchmark_return,
            )
        )

    if len(rows) - 1 < minimum_return_observations:
        issues.append(
            QualityIssue(
                "sample_size",
                "fail",
                f"Only {len(rows) - 1} return observations are available; "
                f"at least {minimum_return_observations} are required.",
            )
        )
    for previous_day, current_day in zip(common_dates, common_dates[1:]):
        if (date.fromisoformat(current_day) - date.fromisoformat(previous_day)).days > 7:
            issues.append(
                QualityIssue(
                    "observation_gap",
                    "warn",
                    f"A gap longer than seven days ends on {current_day}.",
                )
            )
            break

    quality_status = (
        "fail"
        if any(issue.status == "fail" for issue in issues)
        else "warn"
        if issues
        else "pass"
    )
    return ComparisonResult(
        subject_id,
        benchmark.benchmark_id,
        reporting_currency,
        CALCULATION_VERSION,
        quality_status,
        tuple(issues),
        tuple(rows),
        comparison_metrics(rows) if len(rows) >= 2 else {},
    )
