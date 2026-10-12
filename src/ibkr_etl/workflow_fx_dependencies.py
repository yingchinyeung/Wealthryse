"""Conservative dated FX dependencies across the normalized input inventory.

Not a financial-impact certification: demands include records a particular
approved filter may exclude. Never repairs rates or changes broker identifiers.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, DecimalException
from .fx_selection import latest_fx_quote, InvalidFxObservation
from .workflow_financial_inputs import observe_bundle
from .workflow_ledger import digest

VERSION = "normalized-fx-dependency-observation-v1"
MAX_DEMANDS = 100000


def trace_fx_dependencies(bundle, *, base_currency):
    observed = observe_bundle(bundle)  # Enforce exact table inventory and scope.
    if type(base_currency) is not str or not base_currency or base_currency != base_currency.strip().upper():
        raise ValueError("Explicit account base currency required")
    instruments = {row["conid"]: row for row in bundle["instruments"]}
    demands = defaultdict(set)
    undated = Counter()
    base_summary_rows = 0

    def add(row, table, field, day, value=None):
        code = row.get(field) if value is None else value
        if not code:
            return
        if type(code) is not str:
            raise ValueError("Invalid stored currency identity")
        code = code.strip().upper()
        if code in (base_currency, "BASE"):
            return
        if not day:
            undated[table + ":" + field] += 1
            return
        demands[(str(day), code)].add(table + ":" + field)
        if len(demands) > MAX_DEMANDS:
            raise ValueError("FX dependency limit exceeded")

    for table in ("position_snapshots", "nav_snapshots", "cash_snapshots",
                  "cash_transactions", "trades", "corporate_actions", "fx_transactions"):
        day_field = {"position_snapshots": "snapshot_date", "nav_snapshots": "snapshot_date",
                     "cash_snapshots": "snapshot_date", "cash_transactions": "effective_date",
                     "trades": "trade_date", "corporate_actions": "effective_date",
                     "fx_transactions": "report_date"}[table]
        for row in bundle["tables"][table]:
            day = row[day_field]
            # CashReport's BASE_SUMMARY is a base-denominated aggregate, not
            # an ISO currency or an instruction to invent an exchange rate.
            # Restrict this interpretation to that table and report its count.
            if table == "cash_snapshots" and row.get("currency") == "BASE_SUMMARY":
                base_summary_rows += 1
                continue
            if table == "fx_transactions":
                for field in ("functional_currency", "fx_currency"):
                    add(row, table, field, day)
            else:
                add(row, table, "currency", day)
            if table == "trades":
                add(row, table, "commission_currency", day)
                instrument = instruments.get(row["conid"], {})
                if instrument.get("asset_category") == "CASH":
                    pair = str(instrument.get("symbol") or "").split(".")
                    if len(pair) == 2 and all(pair):
                        for currency in pair:
                            add(row, table, "fx_pair_leg", day, currency)

    quotes = defaultdict(list)
    invalid_pairs = Counter()
    for row in bundle["tables"]["fx_rates"]:
        pair = (row["from_currency"], row["to_currency"])
        if not row["rate_date"]:
            undated["fx_rates:rate_date"] += 1
            continue
        quotes[pair].append((str(row["rate_date"]), row["rate_text"]))
        try:
            value = Decimal(str(row["rate_text"]))
            invalid = not value.is_finite() or value <= 0
        except DecimalException:
            invalid = True
        if invalid:
            invalid_pairs[pair] += 1
    for history in quotes.values():
        history.sort(key=lambda item: item[0])
    outcomes = []; failures = Counter(); usage = Counter()
    for (day, code), sources in sorted(demands.items()):
        selection = "direct"
        try:
            quote = latest_fx_quote(quotes.get((code, base_currency), []), day)
            if quote is None:
                selection = "reciprocal"
                quote = latest_fx_quote(quotes.get((base_currency, code), []), day)
        except InvalidFxObservation:
            status = "invalid_latest_observation"; quote = None
        else:
            status = "available" if quote is not None else "missing_observation"
        outcome = {"date": day, "currency": code, "sources": sorted(sources), "status": status}
        if quote:
            outcome.update(selection=selection, observation_date=quote[1], rate=str(quote[0]))
            usage[selection] += 1
        else:
            failures[code + ":" + status] += 1
        outcomes.append(outcome)
    return {"version": VERSION, "account_id": bundle["account_id"], "base_currency": base_currency,
            "as_of": str(bundle["as_of"]), "input_manifest_sha256": observed["input_manifest_sha256"],
            "dependency_sha256": digest(outcomes), "dated_demands": len(outcomes),
            "selection_counts": dict(usage), "failure_counts": dict(failures),
            "undated_counts": dict(undated),
            "base_cash_summary_rows": base_summary_rows,
            "invalid_stored_pairs": [{"from_currency": source, "to_currency": target, "rows": count,
                "appears_in_dated_demands": any(code in (source, target) for _, code in demands)}
                for (source, target), count in sorted(invalid_pairs.items())],
            "conservative_dependency_coverage_verified": not failures and not undated,
            "actual_financial_consumption_verified": False, "financial_accuracy_verified": False,
            "source_changes": False, "execution_authorized": False}
