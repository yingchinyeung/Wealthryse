"""Independent Decimal checks of comparison NAV/benchmark/FX projections.

No writes, network calls, approvals or workflow dispatch. Portfolio cash-flow
classification and underlying IBKR import completeness are not certified here.
"""
from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from datetime import date, timedelta
from decimal import Decimal, localcontext

VERSION = "comparison-source-observation-v1"
COMPARISON_VERSION = "benchmark-comparison-v1"
PROVIDER = "Yahoo Finance via yfinance"
STATUS = "provisional_close"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        default=str, allow_nan=False).encode()).hexdigest()


def number(value, positive=False):
    result = Decimal(str(value))
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("Invalid numerical evidence")
    return result


def series(records, day_key, value_key):
    result = {}
    for row in records:
        day = date.fromisoformat(str(row[day_key]))
        if day in result:
            raise ValueError("Ambiguous duplicate source observation")
        result[day] = number(row[value_key], positive=True)
    return result


def verify_sources(data, *, expected_date):
    """Verify current source projection, with declared finite rounding bounds.

    Benchmark level = native index level * same/prior native-to-reporting FX
    (maximum four calendar days). Benchmark return and growth-of-100 are checked
    independently of the production float calculation. Portfolio return itself
    requires separate reconciled external flows; only its internal accumulation
    and excess-return arithmetic are checked here.
    """
    contract = data["contract"]
    cutoff = date.fromisoformat(data["as_of"])
    start = date.fromisoformat(str(contract["history_start_date"]))
    if type(expected_date) is not date or expected_date > cutoff:
        raise ValueError("Explicit expected comparison date required")
    if contract["base_currency"] != contract["reporting_currency"]:
        raise ValueError("Unreviewed portfolio currency conversion")
    if contract["mapping_currency"] != contract["reporting_currency"]:
        raise ValueError("Mapping currency mismatch")
    for row in data["prices"]:
        if (row["currency"] != contract["benchmark_currency"] or row["provider"] != PROVIDER
                or row["observation_status"] != STATUS or row["quality_status"] not in {"pass", "warn"}):
            raise ValueError("Unreviewed benchmark source")
    for row in data["fx"]:
        if (row["from_currency"] != contract["benchmark_currency"]
                or row["to_currency"] != contract["reporting_currency"]
                or row["provider"] != PROVIDER or row["observation_status"] != STATUS
                or row["quality_status"] not in {"pass", "warn"}):
            raise ValueError("Unreviewed FX source")
    nav = series(data["nav"], "snapshot_date", "total_text")
    prices = series(data["prices"], "observation_date", "level")
    fx = series(data["fx"], "rate_date", "rate")
    common = sorted(day for day in nav.keys() & prices.keys() if start <= day <= cutoff)
    issues = []
    def issue(code, day=None):
        issues.append({"code": code, "date": day.isoformat() if day else None})
    if not common or common[-1] != expected_date:
        issue("source_coverage_mismatch")
    saved = {}
    for row in data["comparisons"]:
        if (row["subject_id"] != contract["subject_id"] or row["benchmark_id"] != contract["benchmark_id"]
                or str(row["tenant_id"]) != str(contract["tenant_id"])
                or row["reporting_currency"] != contract["reporting_currency"]
                or row["calculation_version"] != COMPARISON_VERSION):
            raise ValueError("Comparison identity mismatch")
        day = date.fromisoformat(str(row["observation_date"]))
        if day in saved:
            raise ValueError("Ambiguous duplicate comparison")
        saved[day] = row
        if row["quality_status"] not in {"pass", "warn"}:
            issue("comparison_quality_failed", day)
    if set(saved) != set(common):
        issue("comparison_date_inventory_mismatch")
    fx_days = sorted(fx)
    converted = {}
    selected_fx = []
    with localcontext() as ctx:
        ctx.prec = 40
        for day in common:
            rate = Decimal(1)
            if contract["benchmark_currency"] != contract["reporting_currency"]:
                index = bisect_right(fx_days, day) - 1
                if index < 0 or (day - fx_days[index]).days > 4:
                    issue("missing_or_stale_prior_fx", day)
                    continue
                rate = fx[fx_days[index]]
                selected_fx.append({"observation_date": day.isoformat(), "rate_date": fx_days[index].isoformat(),
                                    "rate": str(rate)})
            converted[day] = prices[day] * rate
        def check(row, key, expected, day):
            actual = row.get(key)
            if expected is None:
                if actual is not None:
                    issue(key + "_mismatch", day)
                return
            if actual is None:
                issue(key + "_missing", day)
                return
            # Float->database rounding only: no broad financial-error allowance.
            tolerance = max(Decimal("0.00000001"), abs(expected) * Decimal("0.0000000001"))
            if abs(number(actual) - expected) > tolerance:
                issue(key + "_mismatch", day)
        portfolio_growth = Decimal(100)
        for index, day in enumerate(common):
            row = saved.get(day)
            if row is None:
                continue
            check(row, "portfolio_value", nav[day], day)
            level = converted.get(day)
            if level is not None:
                check(row, "benchmark_level", level, day)
                if common[0] in converted:
                    check(row, "normalized_benchmark", level / converted[common[0]] * 100, day)
            if index == 0:
                for key in ("portfolio_return", "benchmark_return", "excess_return"):
                    check(row, key, None, day)
                check(row, "normalized_portfolio", Decimal(100), day)
            else:
                previous = converted.get(common[index - 1])
                benchmark_return = level / previous - 1 if level is not None and previous is not None else None
                if benchmark_return is not None:
                    check(row, "benchmark_return", benchmark_return, day)
                if row.get("portfolio_return") is None:
                    issue("portfolio_return_missing", day)
                    continue
                portfolio_return = number(row["portfolio_return"])
                if portfolio_return <= -1:
                    issue("portfolio_return_invalid", day)
                portfolio_growth *= 1 + portfolio_return
                check(row, "normalized_portfolio", portfolio_growth, day)
                if benchmark_return is not None:
                    check(row, "excess_return", portfolio_return - benchmark_return, day)
    counts = {}
    for item in issues:
        counts[item["code"]] = counts.get(item["code"], 0) + 1
    return {"observation_version": VERSION, "subject_id": contract["subject_id"],
        "benchmark_id": contract["benchmark_id"], "as_of": data["as_of"],
        "expected_date": expected_date.isoformat(), "source_sha256": fingerprint({
            key: data[key] for key in ("contract", "as_of", "nav", "prices", "fx")}),
        "comparison_sha256": fingerprint(data["comparisons"]), "selected_fx_sha256": fingerprint(selected_fx),
        "common_observations": len(common), "saved_observations": len(saved),
        "source_projection_verified": not issues, "issue_counts": counts, "issue_samples": issues[:10],
        "cash_flows_verified": False, "ingestion_verified": False, "execution_authorized": False,
        "warnings": ["provisional_source", "portfolio_cash_flow_reconciliation_pending"]}


def load_source_projection(connection, *, tenant_id, subject_id, benchmark_id, as_of):
    context = connection.execute("SELECT app_tenant_id() AS tenant_id, "
        "current_setting('transaction_read_only') AS read_only, "
        "current_setting('transaction_isolation') AS isolation").fetchone()
    if (str(context["tenant_id"]) != str(tenant_id) or context["read_only"] != "on"
            or context["isolation"] not in {"repeatable read", "serializable"}):
        raise ValueError("Tenant-scoped consistent READ ONLY transaction required")
    contracts = connection.execute("""SELECT s.tenant_id, s.subject_id, s.subject_type, s.base_currency, s.reporting_currency,
        s.history_start_date, m.benchmark_id, m.reporting_currency AS mapping_currency,
        m.mapping_type, m.effective_from, m.effective_to, b.currency AS benchmark_currency,
        b.return_convention, b.provider
        FROM platform_subjects s JOIN portfolio_benchmark_mapping m
          ON m.tenant_id=s.tenant_id AND m.subject_id=s.subject_id
        JOIN benchmark_master b ON b.tenant_id=m.tenant_id AND b.benchmark_id=m.benchmark_id
        WHERE s.tenant_id=? AND s.subject_id=? AND m.benchmark_id=? AND b.active
          AND m.effective_from<=? AND (m.effective_to IS NULL OR m.effective_to>=?)""",
        (tenant_id, subject_id, benchmark_id, as_of, as_of)).fetchall()
    if len(contracts) != 1:
        raise ValueError("Missing or ambiguous effective mapping")
    contract = dict(contracts[0])
    start = str(contract["history_start_date"])
    def rows(sql, args):
        return [dict(row) for row in connection.execute(sql, args).fetchall()]
    nav = rows("""SELECT snapshot_date, total_text FROM subject_nav_series
        WHERE tenant_id=? AND subject_id=? AND snapshot_date BETWEEN ? AND ? ORDER BY snapshot_date""",
        (tenant_id, subject_id, start, as_of))
    prices = rows("""SELECT observation_date, level, currency, provider, observation_status, quality_status
        FROM benchmark_prices WHERE tenant_id=? AND benchmark_id=? AND observation_date BETWEEN ? AND ?
        AND provider=? AND observation_status=? ORDER BY observation_date""",
        (tenant_id, benchmark_id, start, as_of, PROVIDER, STATUS))
    fx = []
    if contract["benchmark_currency"] != contract["reporting_currency"]:
        fx = rows("""SELECT rate_date, rate, from_currency, to_currency, provider, observation_status, quality_status
            FROM reference_fx_rates WHERE tenant_id=? AND from_currency=? AND to_currency=?
            AND rate_date BETWEEN ? AND ? AND provider=? ORDER BY rate_date""",
            (tenant_id, contract["benchmark_currency"], contract["reporting_currency"],
             (date.fromisoformat(start) - timedelta(days=4)).isoformat(), as_of, PROVIDER))
    comparisons = rows("""SELECT tenant_id, subject_id, benchmark_id, observation_date, reporting_currency,
        portfolio_value, benchmark_level, normalized_portfolio, normalized_benchmark, portfolio_return,
        benchmark_return, excess_return, calculation_version, quality_status FROM performance_comparison_daily
        WHERE tenant_id=? AND subject_id=? AND benchmark_id=? AND observation_date BETWEEN ? AND ?
        AND calculation_version=? ORDER BY observation_date""",
        (tenant_id, subject_id, benchmark_id, start, as_of, COMPARISON_VERSION))
    return {"contract": contract, "as_of": as_of, "nav": nav, "prices": prices,
            "fx": fx, "comparisons": comparisons}
