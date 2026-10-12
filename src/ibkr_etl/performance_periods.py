"""Period performance from a dated NAV series and dated external flows.

Pure functions only: callers load NAV and net external flows (positive for a
contribution, negative for a withdrawal) and pass plain mappings in.

Time-weighted returns chain-link the website's daily Modified Dietz return
``(V1 - V0 - F) / (V0 + F / 2)`` between consecutive NAV observations, where
``F`` is the net external flow dated after the earlier observation and on or
before the later one. A flow dated on an observation day is therefore already
inside that day's NAV. A non-positive denominator or an invalid sub-period
return fails the whole period closed instead of being skipped.

Every period ``[first_day, last_day]`` is valued from the last NAV observation
strictly before ``first_day`` (for example, the prior month-end for MTD) to the
last observation on or before ``last_day``. When no observation exists before
``first_day`` the period starts at inception and is flagged partial.

Money-weighted returns solve XIRR on: the opening value invested, each
external flow invested, and the closing value returned.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Iterable, Mapping, Sequence


CALCULATION_VERSION = "performance-periods-v1"
DAYS_PER_YEAR = 365.0
SHORT_PERIOD_IRR_NOTE = (
    "Period is shorter than one year; the annualised IRR extrapolates a short-period result."
)


class PerformanceInputError(ValueError):
    """Raised for malformed NAV or flow inputs that cannot be interpreted."""


@dataclass(frozen=True)
class PeriodResult:
    key: str
    label: str
    kind: str
    requested_start: str
    requested_end: str
    start_date: str | None
    end_date: str | None
    status: str
    partial: bool
    start_value: float | None
    end_value: float | None
    observations: int
    twr: float | None
    twr_annualized: float | None
    irr_annualized: float | None
    irr_period: float | None
    irr_note: str | None
    flows_in: float
    flows_out: float
    net_flows: float
    issues: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        result = asdict(self)
        result["issues"] = list(self.issues)
        return result


def _day(value: object, name: str) -> str:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError as error:
        raise PerformanceInputError(f"{name} contains an invalid date: {value!r}") from error


def _items(values: Mapping[object, object] | Iterable[tuple[object, object]] | None):
    if values is None:
        return ()
    return values.items() if isinstance(values, Mapping) else values


def validated_nav(
    values: Mapping[object, object] | Iterable[tuple[object, object]],
) -> dict[str, float]:
    """Return ``{iso_date: nav}`` sorted by date; values must be finite."""

    result: dict[str, float] = {}
    for raw_day, raw_value in _items(values):
        day = _day(raw_day, "NAV series")
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as error:
            raise PerformanceInputError(f"NAV is not numeric on {day}") from error
        if not math.isfinite(value):
            raise PerformanceInputError(f"NAV is not finite on {day}")
        if day in result:
            raise PerformanceInputError(f"NAV series contains a duplicate date: {day}")
        result[day] = value
    return dict(sorted(result.items()))


def validated_flows(
    values: Mapping[object, object] | Iterable[tuple[object, object]] | None,
) -> dict[str, float]:
    """Return net flows per ISO date; same-day flows are summed."""

    result: dict[str, float] = {}
    for raw_day, raw_value in _items(values):
        day = _day(raw_day, "external flows")
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as error:
            raise PerformanceInputError(f"External flow is not numeric on {day}") from error
        if not math.isfinite(value):
            raise PerformanceInputError(f"External flow is not finite on {day}")
        result[day] = result.get(day, 0.0) + value
    return dict(sorted(result.items()))


def _annualized(total: float | None, start: str, end: str) -> float | None:
    if total is None:
        return None
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    growth = 1.0 + total
    if days < DAYS_PER_YEAR or growth <= 0:
        return None
    return growth ** (DAYS_PER_YEAR / days) - 1.0


def _npv(rate: float, flows: Sequence[tuple[float, float]]) -> float:
    base = 1.0 + rate
    return sum(amount / base ** years for years, amount in flows)


def xirr(cash_flows: Iterable[tuple[object, float]]) -> float | None:
    """Annualised internal rate of return for dated investor cash flows.

    Investments are negative and receipts positive. Returns ``None`` when the
    flows do not change sign or no root is bracketed in (-100%, +1e6%).
    When several roots exist, the one closest to zero is returned.
    """

    dated = [(_day(day, "cash flows"), float(amount)) for day, amount in cash_flows]
    dated = [(day, amount) for day, amount in dated if amount != 0.0]
    if not dated or not all(math.isfinite(amount) for _, amount in dated):
        return None
    if not (any(a > 0 for _, a in dated) and any(a < 0 for _, a in dated)):
        return None
    origin = min(date.fromisoformat(day) for day, _ in dated)
    flows = [
        ((date.fromisoformat(day) - origin).days / DAYS_PER_YEAR, amount)
        for day, amount in dated
    ]
    if max(years for years, _ in flows) <= 0:
        return None
    # Scan growth factors (1 + r) on a log grid, then bisect each bracket.
    grid = [10 ** (exponent / 20.0) for exponent in range(-120, 81)]
    rates = [growth - 1.0 for growth in grid]
    roots: list[float] = []
    previous_rate = previous_value = None
    for rate in rates:
        try:
            value = _npv(rate, flows)
        except (OverflowError, ZeroDivisionError):
            previous_rate = previous_value = None
            continue
        if not math.isfinite(value):
            previous_rate = previous_value = None
            continue
        if value == 0.0:
            roots.append(rate)
        elif previous_value is not None and (previous_value < 0) != (value < 0):
            low, high, low_value = previous_rate, rate, previous_value
            for _ in range(200):
                middle = (low + high) / 2.0
                middle_value = _npv(middle, flows)
                if (middle_value < 0) == (low_value < 0):
                    low, low_value = middle, middle_value
                else:
                    high = middle
                if high - low <= 1e-12 * max(1.0, abs(low)):
                    break
            roots.append((low + high) / 2.0)
        previous_rate, previous_value = rate, value
    if not roots:
        return None
    return min(roots, key=abs)


def _flow_totals(flows: Mapping[str, float], after: str, through: str) -> tuple[float, float, float]:
    """Contributions, withdrawals and net flow dated in ``(after, through]``."""

    flows_in = flows_out = 0.0
    for day, amount in flows.items():
        if not after < day <= through:
            continue
        if amount > 0:
            flows_in += amount
        else:
            flows_out += amount
    return flows_in, flows_out, flows_in + flows_out


def period_return(
    nav: Mapping[str, float],
    flows: Mapping[str, float],
    first_day: str,
    last_day: str,
    *,
    key: str = "custom",
    label: str = "Custom period",
    kind: str = "custom",
) -> PeriodResult:
    """Chain-linked TWR and XIRR for ``[first_day, last_day]``.

    ``nav`` and ``flows`` must be outputs of :func:`validated_nav` and
    :func:`validated_flows` (sorted ISO-date keys).
    """

    first_day = _day(first_day, "period start")
    last_day = _day(last_day, "period end")
    if first_day > last_day:
        raise PerformanceInputError("The period start is after the period end")
    dates = list(nav)

    def empty(status: str, issue: str, start: str | None = None,
              end: str | None = None, partial: bool = False) -> PeriodResult:
        return PeriodResult(
            key, label, kind, first_day, last_day, start, end, status, partial,
            nav.get(start) if start else None, nav.get(end) if end else None,
            0, None, None, None, None, None, 0.0, 0.0, 0.0, (issue,),
        )

    end_index = bisect_right(dates, last_day) - 1
    if end_index < 0:
        return empty("empty", "No NAV observation exists on or before the period end.")
    base_index = bisect_left(dates, first_day) - 1
    partial = False
    if base_index < 0:
        base_index = 0
        partial = kind != "since_inception"
    start, end = dates[base_index], dates[end_index]
    if end_index <= base_index:
        return empty(
            "empty", "No NAV observation falls inside the period.", start, end, partial
        )

    failures: list[str] = []
    growth = 1.0
    window = dates[base_index:end_index + 1]
    flow_days = list(flows)
    for previous_day, current_day in zip(window, window[1:]):
        lo = bisect_right(flow_days, previous_day)
        hi = bisect_right(flow_days, current_day)
        external = sum(flows[day] for day in flow_days[lo:hi])
        denominator = nav[previous_day] + 0.5 * external
        if not denominator > 0:
            failures.append(
                f"The cash-flow-adjusted denominator is not positive on {current_day}."
            )
            continue
        daily = (nav[current_day] - nav[previous_day] - external) / denominator
        if not math.isfinite(daily) or daily <= -1:
            failures.append(f"The portfolio return is invalid on {current_day}.")
            continue
        growth *= 1.0 + daily
    flows_in, flows_out, net = _flow_totals(flows, start, end)
    issues = ([f"History begins on {start}, after the period start."] if partial else [])
    issues += failures
    if failures:
        return PeriodResult(
            key, label, kind, first_day, last_day, start, end, "fail", partial,
            nav[start], nav[end], len(window) - 1, None, None, None, None, None,
            flows_in, flows_out, net, tuple(issues),
        )
    twr = growth - 1.0
    investor_flows: list[tuple[str, float]] = [(start, -nav[start])]
    investor_flows += [
        (day, -amount) for day, amount in flows.items() if start < day <= end
    ]
    investor_flows.append((end, nav[end]))
    irr = xirr(investor_flows) if nav[start] > 0 else None
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    irr_period = (1.0 + irr) ** (days / DAYS_PER_YEAR) - 1.0 if irr is not None else None
    irr_note = None
    if irr is None:
        irr_note = "No money-weighted return solves these cash flows."
    elif days < DAYS_PER_YEAR:
        irr_note = SHORT_PERIOD_IRR_NOTE
    return PeriodResult(
        key, label, kind, first_day, last_day, start, end,
        "partial" if partial else "pass", partial, nav[start], nav[end],
        len(window) - 1, twr, _annualized(twr, start, end), irr, irr_period,
        irr_note, flows_in, flows_out, net, tuple(issues),
    )


def _quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def standard_periods(
    nav: Mapping[str, float], flows: Mapping[str, float], as_of: str
) -> list[PeriodResult]:
    """MTD, QTD, YTD, every calendar year (newest first) and since inception."""

    as_of = _day(as_of, "as_of")
    end = date.fromisoformat(as_of)
    results = [
        period_return(nav, flows, end.replace(day=1).isoformat(), as_of,
                      key="mtd", label="Month to date", kind="mtd"),
        period_return(nav, flows, _quarter_start(end).isoformat(), as_of,
                      key="qtd", label="Quarter to date", kind="qtd"),
        period_return(nav, flows, date(end.year, 1, 1).isoformat(), as_of,
                      key="ytd", label="Year to date", kind="ytd"),
    ]
    first = next((day for day in nav if day <= as_of), None)
    if first is not None:
        for year in range(end.year, int(first[:4]) - 1, -1):
            year_end = min(date(year, 12, 31), end).isoformat()
            results.append(period_return(
                nav, flows, f"{year}-01-01", year_end, key=f"year_{year}",
                label=f"{year} (to date)" if year == end.year else str(year),
                kind="calendar_year",
            ))
        results.append(
            period_return(nav, flows, first, as_of, key="since_inception",
                          label="Since inception", kind="since_inception")
        )
    return results


def flow_statement(
    flows: Mapping[str, float], from_date: str | None = None, to_date: str | None = None
) -> dict:
    """Dated contributions and withdrawals within ``[from_date, to_date]``."""

    from_date = _day(from_date, "from_date") if from_date else None
    to_date = _day(to_date, "to_date") if to_date else None
    if from_date and to_date and from_date > to_date:
        raise PerformanceInputError("from_date is after to_date")
    rows = [
        {
            "date": day,
            "amount": amount,
            "direction": "contribution" if amount > 0 else "withdrawal",
        }
        for day, amount in sorted(flows.items())
        if amount != 0.0
        and (from_date is None or day >= from_date)
        and (to_date is None or day <= to_date)
    ]

    def grouped(width: int) -> list[dict]:
        buckets: dict[str, dict] = {}
        for row in rows:
            bucket = buckets.setdefault(
                row["date"][:width],
                {"period": row["date"][:width], "flows_in": 0.0, "flows_out": 0.0,
                 "net": 0.0, "count": 0},
            )
            bucket["flows_in" if row["amount"] > 0 else "flows_out"] += row["amount"]
            bucket["net"] += row["amount"]
            bucket["count"] += 1
        return list(buckets.values())

    flows_in = sum(row["amount"] for row in rows if row["amount"] > 0)
    flows_out = sum(row["amount"] for row in rows if row["amount"] < 0)
    return {
        "from_date": from_date,
        "to_date": to_date,
        "flows": rows,
        "totals": {
            "flows_in": flows_in,
            "flows_out": flows_out,
            "net": flows_in + flows_out,
            "count": len(rows),
        },
        "by_month": grouped(7),
        "by_year": grouped(4),
    }


def performance_periods(
    nav_values: Mapping[object, object] | Iterable[tuple[object, object]],
    flow_values: Mapping[object, object] | Iterable[tuple[object, object]] | None,
    *,
    as_of: str | None = None,
    custom_start: str | None = None,
    custom_end: str | None = None,
) -> dict:
    """Standard and optional custom period results as JSON-ready dictionaries."""

    nav = validated_nav(nav_values)
    flows = validated_flows(flow_values)
    if as_of is None:
        as_of = next(reversed(nav), None) if nav else None
    if as_of is None:
        return {
            "calculation_version": CALCULATION_VERSION,
            "as_of": None,
            "effective_as_of": None,
            "inception_date": None,
            "periods": [],
            "custom": None,
        }
    as_of = _day(as_of, "as_of")
    nav = {day: value for day, value in nav.items() if day <= as_of}
    flows = {day: value for day, value in flows.items() if day <= as_of}
    custom = None
    if custom_start is not None or custom_end is not None:
        first = next(iter(nav), as_of)
        custom = period_return(
            nav, flows, custom_start or first, custom_end or as_of,
            key="custom", label="Custom period", kind="custom",
        ).as_dict()
    effective = next(reversed(nav), None) if nav else None
    return {
        "calculation_version": CALCULATION_VERSION,
        "as_of": as_of,
        "effective_as_of": effective,
        "inception_date": next(iter(nav), None),
        "periods": [result.as_dict() for result in standard_periods(nav, flows, as_of)],
        "custom": custom,
    }
