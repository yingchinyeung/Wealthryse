"""External cash flows from silver (Medallion step S6), behind the FLOWS_SOURCE setting.

Every return figure gets its deposits and withdrawals from one reader,
provisional_benchmark._external_flows: the website pages (through
web._subject_external_flows, which the certified build and Modelling also call), the
benchmark comparison, the monthly reports, the month-end snapshots and the website
analyst's tools (agent_tools), the agent tool server (wealthryse_agent.tools) and
composite sleeves (derived_portfolio). For a physical account it reads bronze: the
Transfers and CashTransactions rows of flex_rows, the known bronze exception in
docs/MEDALLION_ARCHITECTURE.md. Filtered and composite portfolios read their silver cash
ledger in every mode, so this setting does not touch them.

FLOWS_SOURCE (docs/MEDALLION_ARCHITECTURE.md, "Flows from silver"):

- off, the default (also unset or not understood): bronze, exactly as before.
- fallback: bronze and silver are both read. Silver is used only when it equals bronze
  on every date: the same dates, and on each date the same amount per currency (summed
  exactly), with the base-currency total within a cent. Otherwise bronze is used and one
  line is logged, ``FLOWS_SOURCE {json}`` with the masked subject and counts only.
- on: silver only. Not before two clean month-ends in fallback, and not before the
  Transfers section has a silver table (below).

How silver holds the same flows. The loader writes each CashTransactions record to bronze
flex_rows and to silver cash_transactions in the same loop, under the parser's key:

- the daily 14-day re-send updates the same row in both, so nothing is counted twice;
- a correction under the same key (a record with an IBKR transactionID) overwrites both
  (bronze's one IBKR exception, docs/MEDALLION_AS_KNOWN_ON.md);
- a correction under a new key (no transactionID, so the key hashes the amount) adds a
  second row to both, so both readers count it twice, as today;
- with IBKR_FLEX_LEGACY_KEY_COMPAT, silver keeps v1.1's key where bronze has the parser's,
  so rows are compared by content (date, currency, amount), never by key.

Two gaps make fallback keep bronze. Silver has no table for the Transfers section (the
loader writes it to flex_rows only), so a date with a transfer always differs
(transfers_not_in_silver). And silver cannot tell a consolidated copy (accountId "-",
which the parser files under the statement's account) from the account's own row; bronze
skips it.

``python -m ibkr_etl.flows_source compare`` compares bronze and silver for every subject
over all history in a READ ONLY session and prints counts only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

FLOWS_SOURCE_ENV = "FLOWS_SOURCE"
OFF, FALLBACK, ON = "off", "fallback", "on"
OFF_VALUES = frozenset({"", "off", "false", "0", "no", "none", "disabled", "bronze"})
LOG_PREFIX = "FLOWS_SOURCE "
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
# Portfolios read their silver cash ledger (derived_portfolio_cash_ledger) in every mode.
DERIVED_TYPES = frozenset({"filtered_portfolio", "composite_portfolio"})
# Flows are money: the certified comparisons' money tolerance (certified_pages.MONEY_TOLERANCE).
MONEY_TOLERANCE = 0.005
# Why a date differs, checked in this order.
KINDS = ("transfers_not_in_silver", "only_in_bronze", "only_in_silver", "currency_differs", "amount_differs")

# (source, date, currency, amount, base amount): source is "cash" or "transfer"; a cash
# transaction's amount is in its currency, a transfer's is its base amount.
FlowRecord = tuple[str, str, str, float, float]

_noted: set[str] = set()


def emit(line: str) -> None:
    """To stderr: many callers print a report on stdout. The console logs keep both."""
    print(line, file=sys.stderr, flush=True)


def _log(out: Callable[[str], None] | None, **details: Any) -> None:
    line = LOG_PREFIX + json.dumps(details, sort_keys=True, separators=(",", ":"), default=str)
    try:
        (out or emit)(line)
    except Exception:  # noqa: BLE001 - logging must never change a figure
        pass


def _mask(subject_id: str) -> str:
    """As certified_build.mask: u… and the last two characters for an account, s… and a short
    hash for another subject. Kept here so that logging needs no other module."""
    text = str(subject_id)
    if re.fullmatch(r"[UF]\d+", text):
        return "u…" + text[-2:]
    return "s…" + hashlib.sha256(text.encode()).hexdigest()[:6]


def mode(environ: Mapping[str, str] | None = None) -> tuple[str, str | None]:
    """The mode, and a note when the value was not understood (then off)."""
    environ = os.environ if environ is None else environ
    value = str(environ.get(FLOWS_SOURCE_ENV, "")).strip().lower()
    if value in OFF_VALUES:
        return OFF, None
    if value in (FALLBACK, ON):
        return value, None
    return OFF, "unknown_setting"


def selected_mode(environ: Mapping[str, str] | None = None, out: Callable[[str], None] | None = None) -> str:
    """The mode the reader runs in. A value not understood reads bronze and is logged once."""
    selected, note = mode(environ)
    if note and note not in _noted:
        _noted.add(note)
        _log(out, decision="bronze", reason=note)
    return selected


# ---------------------------------------------------------------- the readers

def subject_type_of(connection: Any, subject_id: str) -> str:
    """As _bronze_external_flows looks it up."""
    subject = connection.execute(
        "SELECT subject_type FROM platform_subjects WHERE subject_id = ?", (subject_id,)
    ).fetchone()
    if subject is None:
        raise RuntimeError(f"Unknown subject: {subject_id}")
    return str(subject["subject_type"])


def silver_external_flows(connection: Any, account_id: str, *,
                          records: list[FlowRecord] | None = None) -> dict[str, float]:
    """A physical account's flows from silver cash_transactions, by today's rules.

    The same rule as bronze: IBKR's type "Deposits/Withdrawals", or for older records
    without a type, a bank-transfer description; the report date (silver's effective_date),
    else the settle date, else the day of dateTime; converted to the base currency with the
    account's IBKR FX rates. Transfers have no silver table, so they are not here. A Kiwoom
    U.S. account's DEPOSIT and WITHDRAWAL rows are added as the bronze reader adds them.
    """
    from .provisional_benchmark import KIWOOM_FLOW_CURRENCIES, _is_external_cash_transaction, _kiwoom_flows
    from .report import FxConverter, _convert, _flex_date, _number

    account = connection.execute(
        "SELECT base_currency, account_type FROM accounts WHERE account_id = ?", (account_id,)
    ).fetchone()
    if account is None:
        raise RuntimeError(f"Unknown account: {account_id}")
    converter = FxConverter(
        connection.execute(
            "SELECT account_id, rate_date, from_currency, to_currency, rate_text FROM fx_rates WHERE account_id = ?",
            (account_id,),
        ),
        {account_id: account["base_currency"]},
    )
    flows: dict[str, float] = defaultdict(float)
    for row in connection.execute(
        "SELECT effective_date, settle_date, event_datetime, transaction_type, description, amount_text, currency "
        "FROM cash_transactions WHERE account_id = ? ORDER BY effective_date, source_key",
        (account_id,),
    ):
        if not _is_external_cash_transaction({"type": row["transaction_type"], "description": row["description"]}):
            continue
        flow_date = _flex_date(row["effective_date"] or row["settle_date"] or str(row["event_datetime"] or "")[:8])
        if not flow_date:
            continue
        rate = converter.rate(account_id, row["currency"], flow_date)
        amount = _convert(row["amount_text"], rate)
        if amount is not None:
            flows[flow_date] += amount
            if records is not None:
                records.append(("cash", flow_date, str(row["currency"] or ""), _number(row["amount_text"]), amount))
    # A Kiwoom account's deposits and withdrawals, read as the bronze reader reads them (silver rows in both).
    if str(account["account_type"] or "") == "kiwoom" and \
            str(account["base_currency"] or "").upper() in KIWOOM_FLOW_CURRENCIES:
        for flow_date, amount in _kiwoom_flows(connection, account_id, converter, records=records).items():
            flows[flow_date] += amount
    return dict(flows)


def _guarded(connection: Any, read: Callable[[], Any]) -> Any:
    """Run a read so that its failure leaves the caller's transaction usable: inside a
    savepoint on PostgreSQL (PostgresConnection and psycopg have transaction()); SQLite
    needs none."""
    begin = getattr(connection, "transaction", None)
    if not callable(begin):
        return read()
    with begin():
        return read()


def external_flows(connection: Any, account_id: str, *, subject_type: str | None = None,
                   selected: str | None = None, out: Callable[[str], None] | None = None) -> dict[str, float]:
    """provisional_benchmark._external_flows in the mode FLOWS_SOURCE selects."""
    from .provisional_benchmark import _bronze_external_flows

    selected = selected_mode() if selected is None else selected
    if selected == OFF:
        return _bronze_external_flows(connection, account_id, subject_type=subject_type)
    if subject_type is None:
        subject_type = subject_type_of(connection, account_id)
    if subject_type in DERIVED_TYPES:
        return _bronze_external_flows(connection, account_id, subject_type=subject_type)
    if selected == ON:
        return silver_external_flows(connection, account_id)
    bronze_records: list[FlowRecord] = []
    bronze = _bronze_external_flows(connection, account_id, subject_type=subject_type, records=bronze_records)
    silver_records: list[FlowRecord] = []
    try:
        silver = _guarded(connection, lambda: silver_external_flows(connection, account_id, records=silver_records))
    except Exception as error:  # noqa: BLE001 - silver unreadable: keep today's figure
        _log(out, decision="bronze", reason="silver_unreadable", subject=_mask(account_id), mode=selected,
             error=type(error).__name__)
        return bronze
    result = compare(bronze, bronze_records, silver, silver_records)
    if result.equal:
        return silver
    _log(out, decision="bronze", reason="silver_differs", subject=_mask(account_id), mode=selected,
         dates_differing=result.dates_differing, kinds=dict(result.kinds))
    return bronze


# ---------------------------------------------------------------- the comparison

@dataclass(frozen=True)
class Comparison:
    """How a subject's silver flows differ from bronze's: dates, by kind (KINDS)."""

    dates_differing: int = 0
    kinds: Mapping[str, int] = field(default_factory=dict)
    bronze_dates: int = 0
    silver_dates: int = 0

    @property
    def equal(self) -> bool:
        return self.dates_differing == 0


def _parts(records: Iterable[FlowRecord]) -> dict[str, dict[tuple[str, str], float]]:
    """date -> (source, currency) -> the exact sum (fsum) of the amounts."""
    grouped: dict[str, dict[tuple[str, str], list[float]]] = defaultdict(lambda: defaultdict(list))
    for source, day, currency, amount, _base in records:
        grouped[day][(source, currency)].append(amount)
    return {day: {key: math.fsum(values) for key, values in by_key.items()} for day, by_key in grouped.items()}


def _close(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= MONEY_TOLERANCE


def _kind(bronze: Mapping[tuple[str, str], float], silver: Mapping[tuple[str, str], float]) -> str:
    if any(source == "transfer" for source, _ in bronze):
        return "transfers_not_in_silver"
    if not silver:
        return "only_in_bronze"
    if not bronze:
        return "only_in_silver"
    if {currency for _, currency in bronze} != {currency for _, currency in silver}:
        return "currency_differs"
    return "amount_differs"


def compare(bronze: Mapping[str, float], bronze_records: Iterable[FlowRecord],
            silver: Mapping[str, float], silver_records: Iterable[FlowRecord]) -> Comparison:
    """Equal when every date has the same amount per source and currency on both sides
    (summed exactly) and the same base-currency total within a cent."""
    bronze_parts, silver_parts = _parts(bronze_records), _parts(silver_records)
    kinds: Counter = Counter()
    for day in sorted(set(bronze) | set(silver) | set(bronze_parts) | set(silver_parts)):
        left, right = bronze_parts.get(day, {}), silver_parts.get(day, {})
        if left == right and _close(bronze.get(day), silver.get(day)):
            continue
        kinds[_kind(left, right)] += 1
    return Comparison(sum(kinds.values()), {kind: kinds[kind] for kind in KINDS if kinds[kind]},
                      len(bronze), len(silver))


def _diagnostics(connection: Any, account_id: str) -> Counter:
    """Rows that explain the usual differences, counted."""
    from .provisional_benchmark import _is_external_cash_transaction

    notes: Counter = Counter()
    for row in connection.execute(
        "SELECT section, data_json FROM flex_rows WHERE account_id = ? AND section IN ('Transfers', 'CashTransactions')",
        (account_id,),
    ):
        if row["section"] == "Transfers":
            notes["bronze_transfer_rows"] += 1
            continue
        values = json.loads(row["data_json"]) if isinstance(row["data_json"], str) else dict(row["data_json"] or {})
        if _is_external_cash_transaction(values):
            notes["bronze_external_cash_rows"] += 1
            if str(values.get("accountId") or account_id) != account_id:
                notes["bronze_consolidated_copies"] += 1
    ids: Counter = Counter()
    for row in connection.execute(
        "SELECT transaction_id, transaction_type, description FROM cash_transactions WHERE account_id = ?",
        (account_id,),
    ):
        if _is_external_cash_transaction({"type": row["transaction_type"], "description": row["description"]}):
            notes["silver_external_cash_rows"] += 1
            if row["transaction_id"]:
                ids[str(row["transaction_id"])] += 1
    notes["silver_rows_sharing_a_transaction_id"] = sum(count for count in ids.values() if count > 1)
    return Counter({key: value for key, value in notes.items() if value})


class Tally:
    """Counts across subjects (and tenants) for the comparison report."""

    def __init__(self) -> None:
        self.counts: Counter = Counter()
        self.kind_subjects: Counter = Counter()
        self.kind_dates: Counter = Counter()
        self.note_subjects: Counter = Counter()
        self.note_rows: Counter = Counter()
        self.errors: Counter = Counter()
        self.differing: list[dict[str, Any]] = []

    def add(self, connection: Any, subject_id: str, subject_type: str) -> None:
        from .provisional_benchmark import _bronze_external_flows

        self.counts["subjects"] += 1
        if subject_type in DERIVED_TYPES:
            self.counts["portfolios_not_affected"] += 1
            return
        self.counts["accounts"] += 1
        bronze_records: list[FlowRecord] = []
        silver_records: list[FlowRecord] = []
        try:
            bronze = _guarded(connection, lambda: _bronze_external_flows(
                connection, subject_id, subject_type=subject_type, records=bronze_records))
        except Exception as error:  # noqa: BLE001 - counted, the run carries on
            self.counts["errors"] += 1
            self.errors[f"bronze {type(error).__name__}"] += 1
            return
        try:
            silver = _guarded(connection, lambda: silver_external_flows(connection, subject_id, records=silver_records))
        except Exception as error:  # noqa: BLE001
            self.counts["errors"] += 1
            self.errors[f"silver {type(error).__name__}"] += 1
            return
        result = compare(bronze, bronze_records, silver, silver_records)
        self.counts["flow_dates_bronze"] += result.bronze_dates
        self.counts["flow_dates_silver"] += result.silver_dates
        try:
            notes = _guarded(connection, lambda: _diagnostics(connection, subject_id))
        except Exception as error:  # noqa: BLE001
            notes = Counter()
            self.errors[f"diagnostics {type(error).__name__}"] += 1
        for name, rows in notes.items():
            self.note_subjects[name] += 1
            self.note_rows[name] += rows
        if result.equal:
            self.counts["matching"] += 1
            return
        self.counts["differing"] += 1
        for kind, dates in result.kinds.items():
            self.kind_subjects[kind] += 1
            self.kind_dates[kind] += dates
        self.differing.append({"subject": _mask(subject_id), "dates_differing": result.dates_differing,
                               "kinds": dict(result.kinds), "notes": dict(sorted(notes.items()))})

    def report(self, **extra: Any) -> dict[str, Any]:
        counts = self.counts
        return {
            "compared": "bronze and silver external flows of every subject, all history",
            **extra,
            "subjects": counts["subjects"],
            "portfolios_not_affected": counts["portfolios_not_affected"],
            "accounts": counts["accounts"],
            "matching": counts["matching"],
            "differing": counts["differing"],
            "errors": counts["errors"],
            "error_kinds": dict(sorted(self.errors.items())),
            "differences": {kind: {"subjects": self.kind_subjects[kind], "dates": self.kind_dates[kind]}
                            for kind in KINDS if self.kind_subjects[kind]},
            "flow_dates": {"bronze": counts["flow_dates_bronze"], "silver": counts["flow_dates_silver"]},
            "notes": {name: {"subjects": self.note_subjects[name], "rows": self.note_rows[name]}
                      for name in sorted(self.note_subjects)},
            "differing_subjects": sorted(self.differing, key=lambda item: item["subject"]),
        }


def compare_subjects(connection: Any, subjects: Iterable[tuple[str, str]], tally: Tally | None = None) -> Tally:
    """Bronze against silver for each (subject id, subject type). Reads only."""
    tally = Tally() if tally is None else tally
    for subject_id, subject_type in subjects:
        tally.add(connection, str(subject_id), str(subject_type))
    return tally


def compare_database(database_url: str, tenants: Iterable[str]) -> dict[str, Any]:
    """Every subject of each tenant, in READ ONLY transactions that are rolled back.
    Counts only; subjects masked as in the logs."""
    import psycopg

    from .db import PostgresConnection, _hybrid_row_factory

    raw = psycopg.connect(database_url, connect_timeout=10, row_factory=_hybrid_row_factory,
                          options="-c statement_timeout=120000 -c lock_timeout=5000")
    tenants = list(tenants)
    try:
        raw.read_only = True  # every transaction begins READ ONLY
        connection = PostgresConnection(raw)
        tally = Tally()
        for tenant in tenants:
            connection.execute("SELECT set_config('app.tenant_id', ?, true)", (tenant,))
            subjects = [(row["subject_id"], row["subject_type"]) for row in connection.execute(
                "SELECT subject_id, subject_type FROM platform_subjects WHERE tenant_id = ? ORDER BY subject_id",
                (tenant,),
            ).fetchall()]
            compare_subjects(connection, subjects, tally)
            raw.rollback()
        return tally.report(tenants=len(tenants))
    finally:
        raw.rollback()
        raw.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ibkr_etl.flows_source",
        description="Compare bronze and silver external flows for every subject (read-only, counts only).")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("compare", help="bronze against silver, every subject, all history")
    command.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""),
                         help="default: DATABASE_URL")
    command.add_argument("--tenant", action="append", default=[],
                         help="tenant id (repeatable; default: PORTFOLIO_TENANT_ID or the default tenant)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.database_url:
        parser.error("give --database-url or set DATABASE_URL")
    tenants = args.tenant or [os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)]
    print(json.dumps(compare_database(args.database_url, tenants), indent=1, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
