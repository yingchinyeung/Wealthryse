"""Read-only independent check of the implemented external-flow return policy.

Physical accounts use Flex Transfers; derived subjects use the classified cash
ledger. Matching these sources does not prove complete IBKR ingestion or that
every economic deposit/withdrawal was classified correctly upstream.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext
import json

from .comparison_source_evidence import fingerprint, number


def flex_day(value):
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = text[:4] + "-" + text[4:6] + "-" + text[6:]
    return date.fromisoformat(text[:10])


def reconstruct_flows(data):
    """Do not silently skip malformed amounts/dates, missing FX or lineage."""
    contract = data["contract"]
    start, end = date.fromisoformat(contract["start"]), date.fromisoformat(contract["as_of"])
    flows = defaultdict(Decimal)
    issues, warnings = [], []
    seen = set()
    def fail(code):
        issues.append(code)
    with localcontext() as context:
        context.prec = 40
        if contract["subject_type"] == "physical_account":
            rates = {}
            invalid_rates, consumed_rates = set(), set()
            for row in data["fx"]:
                if row["account_id"] != contract["subject_id"] or row["to_currency"] != contract["currency"]:
                    raise ValueError("FX scope mismatch")
                key = (row["from_currency"], date.fromisoformat(row["rate_date"]))
                if key in rates:
                    raise ValueError("Ambiguous account FX")
                try:
                    rates[key] = number(row["rate_text"], positive=True)
                except (ValueError, ArithmeticError, TypeError):
                    # Preserve an unusable observation as an explicit marker.
                    # Reject if consumed; report separately if irrelevant to
                    # this period's transfers. Never silently carry past it.
                    rates[key] = None
                    invalid_rates.add(key)
            for row in data["transfers"]:
                if row["account_id"] != contract["subject_id"]:
                    raise ValueError("Transfer scope mismatch")
                identity = (row["record_type"], row["source_key"])
                if identity in seen:
                    raise ValueError("Duplicate transfer identity")
                seen.add(identity)
                values = row["values"]
                try:
                    day = flex_day(values.get("date") or values.get("reportDate"))
                except (ValueError, TypeError):
                    fail("invalid_transfer_date")
                    continue
                if not start <= day <= end:
                    continue
                if row.get("ingestion_status") != "success" or not row.get("ingestion_completed_at"):
                    fail("transfer_ingestion_lineage_not_successful")
                # Match the production policy: prefer explicit base amount for
                # non-cash security transfers, otherwise convert native amount.
                cash = str(values.get("assetCategory") or "").upper() == "CASH"
                amount = values.get("positionAmountInBase") if not cash else None
                try:
                    if amount not in (None, ""):
                        converted = number(amount)
                    else:
                        native = number(values.get("cashTransfer") if cash else values.get("positionAmount"))
                        currency = values.get("currency")
                        if currency == contract["currency"]:
                            rate = Decimal(1)
                        else:
                            available = [d for c, d in rates if c == currency and d <= day]
                            if not available:
                                fail("transfer_fx_missing")
                                continue
                            rate_day = max(available)
                            key = (currency, rate_day)
                            consumed_rates.add(key)
                            rate = rates[key]
                            if rate is None:
                                fail("transfer_fx_invalid")
                                continue
                            if (day - rate_day).days > 4:
                                # Current production policy has no age limit;
                                # expose it, do not silently change methodology.
                                warnings.append("transfer_fx_older_than_four_days")
                        converted = native * rate
                    flows[day] += converted
                except (ValueError, ArithmeticError, TypeError):
                    fail("invalid_transfer_amount")
            if invalid_rates - consumed_rates:
                warnings.append("unused_invalid_fx_observations:" + str(len(invalid_rates - consumed_rates)))
        elif contract["subject_type"] in {"filtered_portfolio", "composite_portfolio"}:
            for row in data["ledger"]:
                if row["portfolio_id"] != contract["subject_id"]:
                    raise ValueError("Derived ledger scope mismatch")
                if row["event_key"] in seen:
                    raise ValueError("Duplicate ledger event")
                seen.add(row["event_key"])
                day = date.fromisoformat(row["event_date"])
                if not start <= day <= end:
                    continue
                if row["included"] not in (0, 1) or row["external_flow"] not in (0, 1):
                    raise ValueError("Invalid ledger flags")
                if not row["included"] or not row["external_flow"]:
                    continue
                try:
                    amount = number(row["amount_base"])
                    native = number(row["amount"])
                    rate = number(row["fx_rate_to_base"], positive=True)
                    if abs(amount - native * rate) > max(Decimal("1e-8"), abs(amount) * Decimal("1e-10")):
                        fail("derived_flow_conversion_mismatch")
                    flows[day] += amount
                except (ValueError, ArithmeticError, TypeError):
                    fail("invalid_derived_flow_amount")
        else:
            raise ValueError("Unsupported subject flow policy")
    return dict(flows), sorted(set(issues)), sorted(set(warnings))


def verify_cashflow_returns(data, comparisons):
    contract = data["contract"]
    if contract["currency"] != contract["base_currency"]:
        raise ValueError("Unreviewed cash-flow currency conversion")
    flows, issues, warnings = reconstruct_flows(data)
    rows = sorted(comparisons, key=lambda r: str(r["observation_date"]))
    days = [date.fromisoformat(str(row["observation_date"])) for row in rows]
    if len(days) < 2 or len(days) != len(set(days)):
        raise ValueError("Unique comparison history required")
    for row in rows:
        if (row["subject_id"] != contract["subject_id"] or str(row["tenant_id"]) != str(contract["tenant_id"])
                or row["reporting_currency"] != contract["currency"] or row["benchmark_id"] != rows[0]["benchmark_id"]
                or row["calculation_version"] != "benchmark-comparison-v1"):
            raise ValueError("Comparison scope mismatch")
        if row["quality_status"] not in {"pass", "warn"}:
            issues.append("comparison_quality_failed")
    if rows[0]["portfolio_return"] is not None:
        issues.append("opening_return_not_null")
    if days[0] < date.fromisoformat(contract["start"]) or days[-1] > date.fromisoformat(contract["as_of"]):
        raise ValueError("Comparison date scope mismatch")
    mismatches = []
    with localcontext() as context:
        context.prec = 40
        for previous, current, prior_day, day in zip(rows, rows[1:], days, days[1:]):
            external = sum((amount for flow_day, amount in flows.items() if prior_day < flow_day <= day), Decimal(0))
            opening = number(previous["portfolio_value"], positive=True)
            closing = number(current["portfolio_value"], positive=True)
            denominator = opening + external / 2
            if denominator <= 0:
                issues.append("nonpositive_flow_adjusted_denominator")
                continue
            expected = (closing - opening - external) / denominator
            try:
                saved = number(current["portfolio_return"])
                if abs(saved - expected) > max(Decimal("1e-8"), abs(expected) * Decimal("1e-10")):
                    mismatches.append(day.isoformat())
            except (ValueError, ArithmeticError, TypeError):
                issues.append("invalid_saved_portfolio_return")
    if mismatches:
        issues.append("cashflow_adjusted_return_mismatch")
    return {"observation_version": "cashflow-source-observation-v1", "subject_id": contract["subject_id"],
        "benchmark_id": rows[0]["benchmark_id"], "as_of": contract["as_of"],
        "source_sha256": fingerprint(data), "flow_sha256": fingerprint({d.isoformat(): str(v) for d, v in sorted(flows.items())}),
        "return_intervals": len(rows) - 1, "flow_days": len(flows),
        "implemented_flow_policy_verified": not issues, "issues": sorted(set(issues)),
        "mismatch_count": len(mismatches), "mismatch_dates": mismatches[:10],
        "warnings": warnings + ["economic_flow_classification_not_independently_verified"],
        "ingestion_completeness_verified": False, "execution_authorized": False}


def load_cashflow_sources(connection, *, tenant_id, subject_id, as_of):
    check = connection.execute("SELECT app_tenant_id() AS tenant_id, current_setting('transaction_read_only') AS ro, "
                               "current_setting('transaction_isolation') AS isolation").fetchone()
    if (str(check["tenant_id"]) != str(tenant_id) or check["ro"] != "on"
            or check["isolation"] not in {"repeatable read", "serializable"}):
        raise ValueError("Tenant-scoped consistent READ ONLY transaction required")
    row = connection.execute("""SELECT subject_id, subject_type, base_currency, reporting_currency,
        history_start_date FROM platform_subjects WHERE tenant_id=? AND subject_id=?""", (tenant_id, subject_id)).fetchone()
    if row is None:
        raise ValueError("Subject contract missing")
    data = {"contract": {"tenant_id": str(tenant_id), "subject_id": subject_id,
        "subject_type": row["subject_type"], "base_currency": row["base_currency"],
        "currency": row["reporting_currency"], "start": str(row["history_start_date"]), "as_of": as_of},
        "transfers": [], "fx": [], "ledger": []}
    if row["subject_type"] == "physical_account":
        # Deliberately parse every transfer date before period filtering: a bad
        # date must not disappear behind a SQL date predicate.
        records = connection.execute("""SELECT f.account_id, f.record_type, f.source_key, f.data_json,
            f.last_seen_run_id, r.status AS ingestion_status, r.completed_at AS ingestion_completed_at
            FROM flex_rows f LEFT JOIN ingestion_runs r ON r.tenant_id=f.tenant_id AND r.run_id=f.last_seen_run_id
            WHERE f.tenant_id=? AND f.account_id=? AND f.section='Transfers'
            ORDER BY f.record_type, f.source_key""", (tenant_id, subject_id)).fetchall()
        for raw in records:
            values = json.loads(raw["data_json"])
            sanitized = {key: values.get(key) for key in ("date", "reportDate", "assetCategory", "currency",
                "cashTransfer", "positionAmountInBase", "positionAmount")}
            item = {k: raw[k] for k in ("account_id", "record_type", "source_key", "last_seen_run_id", "ingestion_status", "ingestion_completed_at")}
            item["values"] = sanitized
            data["transfers"].append(item)
        data["fx"] = [dict(r) for r in connection.execute("""SELECT f.account_id, f.rate_date, f.from_currency,
            f.to_currency, f.rate_text FROM fx_rates f JOIN accounts a ON a.account_id=f.account_id
            WHERE a.tenant_id=? AND f.account_id=? AND f.to_currency=? AND f.rate_date<=?
            ORDER BY f.from_currency, f.rate_date""", (tenant_id, subject_id, row["base_currency"], as_of)).fetchall()]
    else:
        data["ledger"] = [dict(r) for r in connection.execute("""SELECT portfolio_id, event_key, event_date,
            amount, fx_rate_to_base, amount_base, included, external_flow, currency, classification,
            source_account_id, source_type, source_key FROM derived_portfolio_cash_ledger
            WHERE tenant_id=? AND portfolio_id=? AND event_date BETWEEN ? AND ? ORDER BY event_date, event_key""",
            (tenant_id, subject_id, str(row["history_start_date"]), as_of)).fetchall()]
    return data
