"""The export agent, website side: plans, files and the checks before download.

The model only proposes a plan: which datasets, dates, columns, filters and
totals. Everything in this module is code. The plan is validated against what
the account actually has, every row comes from the same tenant-scoped reads as
the Exports tab's CSV buttons (``subject_exports``), totals are summed with
Decimal, and the checks read the written files back and tie them to the books
before anyone can download them. Nothing here writes to the database.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Mapping, Sequence

from .subject_exports import CASH, EXPOSURES, HOLDINGS, PERFORMANCE, TRADES

MAX_DATASETS = 4
MAX_FILTER_VALUES = 20
MAX_REQUEST_CHARS = 1000
PREVIEW_ROWS = 5
# Holdings plus cash should land within 1% of the account's value on the same day;
# accrued interest and dividends make up most of what is left.
HOLDINGS_TIE_TOLERANCE = Decimal("0.01")
CASH_ROLL_TOLERANCE = Decimal("0.005")
NAV_GAP_DAYS = 10
NUMERIC = re.compile(r"[-+]?\d+(\.\d+)?([eE][-+]?\d+)?")
ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
SCHEDULES = ("monthly", "quarterly", "yearly")

MEASURES = {
    "amount_text": "amount",
    "market_value_text": "market value",
    "proceeds_text": "proceeds",
    "commission_text": "commission",
    "net_cash_text": "net cash",
    "realized_pnl_text": "realized P&L",
}


class PlanError(ValueError):
    """The plan can't be built as asked; the message is safe to show."""


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    label: str
    columns: tuple[str, ...]
    date_column: str
    # Holdings are one snapshot on or before the end date; the rest are a date range.
    snapshot: bool = False
    # Each amount column and the column that holds its currency. Totals never mix currencies.
    amounts: Mapping[str, str] = field(default_factory=dict)
    groups: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    # Columns that identify one row; a repeat is a duplicate.
    unique: tuple[str, ...] = ()


SPECS = {
    "performance": DatasetSpec("performance", "Account value history", PERFORMANCE, "snapshot_date",
                               unique=("snapshot_date",)),
    "holdings": DatasetSpec("holdings", "Holdings", HOLDINGS, "snapshot_date", snapshot=True,
                            amounts={"market_value_text": "currency"}, groups=("currency",),
                            filters=("currencies", "symbol"), unique=("conid",)),
    "cash-activity": DatasetSpec("cash-activity", "Cash activity", CASH, "effective_date",
                                 amounts={"amount_text": "currency"},
                                 groups=("transaction_type", "currency", "conid", "month", "year"),
                                 filters=("transaction_types", "currencies")),
    "trades": DatasetSpec("trades", "Trades", TRADES, "trade_date",
                          amounts={"proceeds_text": "currency", "commission_text": "commission_currency",
                                   "net_cash_text": "currency", "realized_pnl_text": "currency"},
                          groups=("symbol", "buy_sell", "asset_category", "currency", "month", "year"),
                          filters=("currencies", "symbol", "side"), unique=("execution_id",)),
}
# A composite portfolio's holdings are its sleeves (component exposures), in the base currency.
COMPOSITE_HOLDINGS = DatasetSpec(
    "holdings", "Component exposures", EXPOSURES, "snapshot_date", snapshot=True,
    amounts={"market_value_text": "base_currency"}, groups=("component_type", "base_currency"),
    filters=("currencies",), unique=("component_id",))


def spec_for(dataset: str, subject_type: str) -> DatasetSpec:
    if dataset == "holdings" and subject_type == "composite_portfolio":
        return COMPOSITE_HOLDINGS
    return SPECS[dataset]


def available_datasets(subject_type: str) -> list[str]:
    # A composite has no trades of its own (the CSV export refuses them too).
    return [name for name in SPECS if not (name == "trades" and subject_type == "composite_portfolio")]


# Facts and plans -------------------------------------------------------------------------------

def planner_facts(*, account_id: str, display_name: str, subject_type: str, reporting_currency: str,
                  history_start_date: str, latest_date: str | None, transaction_types: Sequence[str],
                  currencies: Sequence[str], today: str) -> dict[str, Any]:
    """What the planner may know about the account: shapes and names, never balances or amounts."""
    datasets = {}
    for name in available_datasets(subject_type):
        spec = spec_for(name, subject_type)
        datasets[name] = {"label": spec.label, "columns": list(spec.columns), "group_by": list(spec.groups),
                          "filters": list(spec.filters), "dated": "snapshot on or before to_date" if spec.snapshot
                          else "rows from from_date to to_date"}
    return {"account_id": account_id, "display_name": display_name, "subject_type": subject_type,
            "reporting_currency": reporting_currency, "history_start_date": history_start_date,
            "latest_date": latest_date, "today": today, "datasets": datasets,
            "cash_transaction_types": sorted(dict.fromkeys(str(t) for t in transaction_types if t))[:60],
            "currencies": sorted(dict.fromkeys(str(c).upper() for c in currencies if c))[:40]}


def _day(value: Any, name: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if not ISO_DAY.fullmatch(text):
            raise ValueError(text)
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise PlanError(f"{name} must be a date in YYYY-MM-DD form.") from error


def _strings(value: Any, name: str, limit: int = MAX_FILTER_VALUES) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise PlanError(f"{name} must be a list of names.")
    cleaned = list(dict.fromkeys(v.strip() for v in value if v.strip()))
    if len(cleaned) > limit:
        raise PlanError(f"{name} can hold at most {limit} values.")
    return cleaned


def validate_plan(raw: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Check a proposed plan against the account and return it in canonical form, with any adjustments.

    Dates outside the account's history are moved to its edges and said so; anything else that
    does not fit (an unknown dataset, column, type or grouping) is refused, never guessed.
    """
    if not isinstance(raw, Mapping):
        raise PlanError("The export plan is not readable.")
    refusal = str(raw.get("refusal") or "").strip()
    if refusal:
        raise PlanError(refusal[:300])
    items = raw.get("datasets")
    if not isinstance(items, list) or not items:
        raise PlanError("The plan names no data to export.")
    if len(items) > MAX_DATASETS:
        raise PlanError(f"An export can hold at most {MAX_DATASETS} datasets.")
    subject_type = str(facts["subject_type"])
    start, latest = str(facts["history_start_date"]), facts.get("latest_date")
    if not latest:
        raise PlanError("This account has no data to export yet.")
    latest = str(latest)
    known_types = {t.lower(): t for t in facts.get("cash_transaction_types") or []}
    adjustments: list[str] = []
    datasets: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise PlanError("Each dataset in the plan must be an object.")
        name = str(item.get("dataset") or "")
        if name not in SPECS:
            raise PlanError(f"There is no dataset called {name or 'that'}.")
        if name not in available_datasets(subject_type):
            raise PlanError("This composite portfolio has no trades of its own. Export its cash activity or "
                            "component exposures instead.")
        spec = spec_for(name, subject_type)
        to_day = _day(item.get("to_date"), "to_date") or latest
        from_day = None if spec.snapshot else (_day(item.get("from_date"), "from_date") or start)
        if to_day > latest:
            adjustments.append(f"{spec.label} ends on {latest}, the latest date with data, not {to_day}.")
            to_day = latest
        if to_day < start:
            raise PlanError(f"{spec.label}: the account's history starts on {start}, after {to_day}.")
        if from_day is not None and from_day < start:
            adjustments.append(f"{spec.label} starts on {start}, when the account's history begins, not {from_day}.")
            from_day = start
        if from_day is not None and from_day > to_day:
            raise PlanError(f"{spec.label}: the start date {from_day} is after the end date {to_day}.")
        columns = _strings(item.get("columns"), "columns", 40)
        unknown = [c for c in columns if c not in spec.columns]
        if unknown:
            raise PlanError(f"{spec.label} has no column called {', '.join(unknown)}.")
        columns = [c for c in spec.columns if c in columns] or list(spec.columns)
        types = _strings(item.get("transaction_types"), "transaction_types")
        if types and "transaction_types" not in spec.filters:
            raise PlanError(f"{spec.label} can't be filtered by cash activity type.")
        missing = [t for t in types if t.lower() not in known_types]
        if missing:
            raise PlanError(f"This account has no cash activity of type {', '.join(missing)}.")
        types = sorted({known_types[t.lower()] for t in types})
        currencies = [c.upper() for c in _strings(item.get("currencies"), "currencies")]
        if currencies and "currencies" not in spec.filters:
            raise PlanError(f"{spec.label} can't be filtered by currency.")
        if any(not re.fullmatch(r"[A-Z]{3}", c) for c in currencies):
            raise PlanError("Currencies must be three-letter codes such as USD.")
        symbol = str(item.get("symbol") or "").strip()
        if symbol and "symbol" not in spec.filters:
            raise PlanError(f"{spec.label} can't be filtered by symbol.")
        if len(symbol) > 64 or any(ord(ch) < 32 for ch in symbol):
            raise PlanError("The symbol filter is not valid.")
        side = str(item.get("side") or "").strip().upper()
        if side and ("side" not in spec.filters or side not in ("BUY", "SELL")):
            raise PlanError("Only trades can be filtered by side, and the side must be BUY or SELL.")
        group_by = _strings(item.get("group_by"), "group_by")
        bad = [g for g in group_by if g not in spec.groups]
        if bad:
            raise PlanError(f"{spec.label} can't be totalled by {', '.join(bad)}.")
        dataset = {"dataset": name, "from_date": from_day, "to_date": to_day, "columns": columns,
                   "transaction_types": types, "currencies": sorted(set(currencies)), "symbol": symbol or None,
                   "side": side or None, "group_by": [g for g in spec.groups if g in group_by]}
        if dataset not in datasets:
            datasets.append(dataset)
    title = re.sub(r"\s+", " ", str(raw.get("title") or "").strip())[:80] or "Export"
    summary = re.sub(r"\s+", " ", str(raw.get("summary") or "").strip())[:300]
    unsupported = re.sub(r"\s+", " ", str(raw.get("unsupported") or "").strip())[:300]
    return {"title": title, "summary": summary, "unsupported": unsupported, "datasets": datasets}, adjustments


def dated_plan(plan: Mapping[str, Any], from_day: str, to_day: str) -> dict[str, Any]:
    """A saved pack's plan for one period: every dataset covers exactly that period."""
    datasets = []
    for item in plan["datasets"]:
        snapshot = SPECS[item["dataset"]].snapshot
        datasets.append({**item, "from_date": None if snapshot else from_day, "to_date": to_day})
    return {**plan, "datasets": datasets}


def describe(item: Mapping[str, Any], subject_type: str) -> str:
    """One plain sentence for a dataset in the plan, written by code so it can't drift from what is built."""
    spec = spec_for(item["dataset"], subject_type)
    when = (f"as of {item['to_date']}" if spec.snapshot else f"from {item['from_date']} to {item['to_date']}")
    parts = [f"{spec.label} {when}"]
    if item.get("transaction_types"):
        parts.append("types " + ", ".join(item["transaction_types"]))
    if item.get("currencies"):
        parts.append("in " + ", ".join(item["currencies"]))
    if item.get("symbol"):
        parts.append(f"symbols containing {item['symbol']}")
    if item.get("side"):
        parts.append("buys only" if item["side"] == "BUY" else "sells only")
    if len(item.get("columns") or []) < len(spec.columns):
        parts.append(f"{len(item['columns'])} of {len(spec.columns)} columns")
    if item.get("group_by"):
        parts.append("with totals by " + ", ".join(g.replace("_", " ") for g in item["group_by"]))
    return "; ".join(parts) + "."


# Building --------------------------------------------------------------------------------------

def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def cell(value: str) -> str:
    """Stop spreadsheet formulas without spoiling numbers: "-12.5" stays a number, "=SUM()" does not run."""
    if value and value[0] in "=+-@\t\r" and not NUMERIC.fullmatch(value):
        return "'" + value
    return value


def amount(value: str) -> Decimal | None:
    if value == "":
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(value) from error
    if not number.is_finite():
        raise ValueError(value)
    return number


def _matches(row: Mapping[str, str], item: Mapping[str, Any], spec: DatasetSpec) -> bool:
    day = row.get(spec.date_column, "")[:10]
    if not spec.snapshot and not (item["from_date"] <= day <= item["to_date"]):
        return False
    if spec.snapshot and day > item["to_date"]:
        return False
    if item.get("transaction_types") and row.get("transaction_type", "").lower() not in {
            t.lower() for t in item["transaction_types"]}:
        return False
    if item.get("currencies"):
        currency_column = next(iter(spec.amounts.values()), "currency")
        if row.get(currency_column, "").upper() not in item["currencies"]:
            return False
    if item.get("symbol") and item["symbol"].upper() not in row.get("symbol", "").upper():
        return False
    if item.get("side") and row.get("buy_sell", "").upper() != item["side"]:
        return False
    return True


def _order(spec: DatasetSpec, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """A fixed order, so the same data always gives the same bytes (the download is checked against them)."""
    def key(row: dict[str, str]) -> tuple:
        texts = tuple(row.get(c, "") for c in spec.columns)
        if spec.snapshot:
            try:
                value = -abs(amount(row.get("market_value_text", "")) or Decimal(0))
            except ValueError:
                value = Decimal(0)
            return (value, texts)
        return (row.get(spec.date_column, ""), texts)
    return sorted(rows, key=key)


@dataclass
class BuiltDataset:
    index: int
    item: dict[str, Any]
    spec: DatasetSpec
    rows: list[dict[str, str]]
    data_name: str
    data: bytes
    summary_name: str | None = None
    summary: bytes | None = None
    summary_rows: list[dict[str, str]] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    snapshot_date: str | None = None


def _csv(header: Sequence[str], rows: Iterable[Sequence[str]]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(list(header))
    for row in rows:
        writer.writerow([cell(v) for v in row])
    # A byte-order mark lets Excel read non-ASCII names correctly.
    return out.getvalue().encode("utf-8-sig")


def summarise(spec: DatasetSpec, item: Mapping[str, Any], rows: Sequence[Mapping[str, str]]
              ) -> tuple[list[dict[str, str]], list[str]]:
    """Totals of every amount column by the plan's groups, one currency at a time."""
    totals: dict[tuple, list] = {}
    unreadable: list[str] = []
    for row in rows:
        keys = []
        for group in item["group_by"]:
            day = row.get(spec.date_column, "")
            keys.append(day[:7] if group == "month" else day[:4] if group == "year" else row.get(group, ""))
        for column, currency_column in spec.amounts.items():
            try:
                value = amount(row.get(column, ""))
            except ValueError:
                unreadable.append(f"{column}={row.get(column, '')[:40]}")
                continue
            if value is None:
                continue
            key = (*keys, MEASURES[column], row.get(currency_column, ""))
            entry = totals.setdefault(key, [0, Decimal(0)])
            entry[0] += 1
            entry[1] += value
    header = [*item["group_by"], "measure", "currency"]
    summary = [{**dict(zip(header, key)), "rows": str(count), "total": format(total, "f")}
               for key, (count, total) in sorted(totals.items())]
    return summary, unreadable


Fetch = Callable[[Mapping[str, Any]], tuple[Sequence[str], Sequence[Mapping[str, Any]]]]


def build(plan: Mapping[str, Any], fetch: Fetch, subject_type: str) -> list[BuiltDataset]:
    """Rows for every dataset in the plan, filtered, ordered, written and totalled."""
    built = []
    for index, item in enumerate(plan["datasets"], start=1):
        spec = spec_for(item["dataset"], subject_type)
        _, source = fetch(item)
        rows = [{c: text(r.get(c)) for c in spec.columns} for r in source]
        rows = _order(spec, [r for r in rows if _matches(r, item, spec)])
        period = item["to_date"] if spec.snapshot else f"{item['from_date']}_{item['to_date']}"
        data_name = f"data/{index:02d}_{item['dataset']}_{period}.csv"
        data = _csv(item["columns"], ([r[c] for c in item["columns"]] for r in rows))
        dataset = BuiltDataset(index, dict(item), spec, rows, data_name, data,
                               snapshot_date=rows[0][spec.date_column] if spec.snapshot and rows else None)
        if item["group_by"]:
            dataset.summary_rows, dataset.unreadable = summarise(spec, item, rows)
            header = [*item["group_by"], "measure", "currency", "rows", "total"]
            dataset.summary_name = f"summary/{index:02d}_{item['dataset']}_totals.csv"
            dataset.summary = _csv(header, ([r[h] for h in header] for r in dataset.summary_rows))
        else:
            for row in rows:
                for column in spec.amounts:
                    try:
                        amount(row.get(column, ""))
                    except ValueError:
                        dataset.unreadable.append(f"{column}={row.get(column, '')[:40]}")
        built.append(dataset)
    return built


def files(built: Sequence[BuiltDataset]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for dataset in built:
        out[dataset.data_name] = dataset.data
        if dataset.summary_name and dataset.summary is not None:
            out[dataset.summary_name] = dataset.summary
    return out


def fingerprint(built: Sequence[BuiltDataset]) -> str:
    """One hash over every data and totals file; the download must match it byte for byte."""
    digest = hashlib.sha256()
    for name, content in sorted(files(built).items()):
        digest.update(name.encode() + b"\0" + hashlib.sha256(content).hexdigest().encode() + b"\n")
    return digest.hexdigest()


# Checks ----------------------------------------------------------------------------------------

@dataclass
class Books:
    """Independent reads of the book of record, for the checks. Any of them may answer None."""
    nav_on: Callable[[str], Decimal | None]
    # {"nav", "positions", "cash", "currency", "date"} as Decimals and text, or {"reason": ...}.
    holdings_value: Callable[[str], Mapping[str, Any]]
    # {currency: {"opening", "activity", "trades", "closing"}} or {"reason": ...}.
    cash_roll: Callable[[str, str], Mapping[str, Any]]


def _check(check_id: str, label: str, status: str, detail: str, **figures: Any) -> dict[str, Any]:
    return {"id": check_id, "label": label, "status": status, "detail": detail,
            "figures": {k: (format(v, "f") if isinstance(v, Decimal) else v) for k, v in figures.items()}}


def _reread(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))


def _uncell(value: str) -> str:
    return value[1:] if value[:2] in {"'" + c for c in "=+-@\t\r"} and not NUMERIC.fullmatch(value[1:]) else value


def file_checks(dataset: BuiltDataset) -> list[dict[str, Any]]:
    """Read the written files back and prove they hold exactly the planned rows and totals."""
    spec, item, name = dataset.spec, dataset.item, dataset.data_name
    checks = []
    table = _reread(dataset.data)
    expected = [[cell(r[c]) for c in item["columns"]] for r in dataset.rows]
    if table[:1] != [item["columns"]] or table[1:] != expected:
        checks.append(_check(f"reads_back_{dataset.index}", f"{name} reads back", "fail",
                             "The written file does not read back as the rows that were selected.",
                             file=name, rows_written=len(table) - 1, rows_selected=len(dataset.rows)))
    else:
        checks.append(_check(f"reads_back_{dataset.index}", f"{name} reads back", "pass",
                             "The file reads back row for row as the selected data.", file=name, rows=len(dataset.rows)))
    outside = [r for r in dataset.rows if not _matches(r, item, spec)]
    checks.append(_check(
        f"matches_plan_{dataset.index}", f"{name} matches the plan", "fail" if outside else "pass",
        (f"{len(outside)} row(s) fall outside the planned dates or filters." if outside
         else "Every row is inside the planned dates and filters."), file=name, rows_outside=len(outside)))
    if spec.unique:
        seen: dict[tuple, int] = {}
        for row in dataset.rows:
            key = tuple(row.get(c, "") for c in spec.unique)
            if all(key):
                seen[key] = seen.get(key, 0) + 1
        repeats = sum(n - 1 for n in seen.values() if n > 1)
        checks.append(_check(f"no_duplicates_{dataset.index}", f"{name} has no duplicates",
                             "fail" if repeats else "pass",
                             (f"{repeats} row(s) repeat a {', '.join(spec.unique)} already in the file." if repeats
                              else f"No {', '.join(spec.unique)} appears twice."), file=name, duplicates=repeats))
    if dataset.unreadable:
        checks.append(_check(f"amounts_readable_{dataset.index}", f"{name} amounts are numbers", "fail",
                             "Some amounts are not numbers, so they could not be totalled.", file=name,
                             unreadable=len(dataset.unreadable)))
    if not dataset.rows:
        checks.append(_check(f"has_rows_{dataset.index}", f"{name} has rows", "review",
                             "No rows match this part of the plan. Check the dates and filters.", file=name))
    if dataset.summary is not None and dataset.summary_name:
        checks.append(_totals_check(dataset, table))
    return checks


def _totals_check(dataset: BuiltDataset, table: list[list[str]]) -> dict[str, Any]:
    """Recompute every total from the detail rows (from the file when it carries the columns) and compare."""
    spec, item = dataset.spec, dataset.item
    header = table[0] if table else []
    needed = {spec.date_column, *spec.amounts, *spec.amounts.values(), *(g for g in item["group_by"]
                                                                             if g not in ("month", "year"))}
    if needed <= set(header):
        rows = [dict(zip(header, (_uncell(v) for v in r))) for r in table[1:]]
        source = "the detail file"
    else:
        rows, source = dataset.rows, "the selected rows (the file leaves out columns the totals need)"
    recomputed, _ = summarise(spec, item, rows)
    written = _reread(dataset.summary or b"")
    totals_header = written[0] if written else []
    written_rows = [dict(zip(totals_header, r)) for r in written[1:]]
    ok = written_rows == recomputed
    return _check(f"totals_tie_{dataset.index}", f"{dataset.summary_name} ties to {dataset.data_name}",
                  "pass" if ok else "fail",
                  (f"Every total equals the sum of {source}." if ok
                   else f"The totals file does not equal the sum of {source}."),
                  file=dataset.summary_name, totals=len(written_rows))


def book_checks(dataset: BuiltDataset, books: Books, subject_type: str) -> list[dict[str, Any]]:
    """Tie each file to the book of record the website shows."""
    item, name, index = dataset.item, dataset.data_name, dataset.index
    checks: list[dict[str, Any]] = []
    if item["dataset"] == "performance" and dataset.rows:
        last = dataset.rows[-1]
        books_nav = books.nav_on(last["snapshot_date"])
        if books_nav is None:
            checks.append(_check(f"nav_matches_books_{index}", "Last value matches the books", "not_checked",
                                 "The books have no value for that day to compare with.", file=name))
        else:
            shown = amount(last["total_text"])
            checks.append(_check(
                f"nav_matches_books_{index}", "Last value matches the books",
                "pass" if shown == books_nav else "fail",
                ("The last value in the file is the account's value on that day." if shown == books_nav
                 else "The last value in the file differs from the account's value on that day."),
                file=name, date=last["snapshot_date"], file_value=shown, books_value=books_nav))
        days = [date.fromisoformat(r["snapshot_date"][:10]) for r in dataset.rows]
        gaps = [((b - a).days, a.isoformat(), b.isoformat()) for a, b in zip(days, days[1:])]
        widest = max(gaps, default=(0, None, None))
        checks.append(_check(
            f"nav_gaps_{index}", "No long gaps in the value history", "review" if widest[0] > NAV_GAP_DAYS else "pass",
            (f"The value history skips {widest[0]} days between {widest[1]} and {widest[2]}." if widest[0] > NAV_GAP_DAYS
             else f"No gap between values is longer than {NAV_GAP_DAYS} days."),
            file=name, longest_gap_days=widest[0]))
    if item["dataset"] == "holdings" and dataset.rows:
        checks.append(_holdings_tie(dataset, books, subject_type))
    if item["dataset"] == "cash-activity":
        checks.append(_cash_roll(dataset, books, subject_type))
    return checks


def _holdings_tie(dataset: BuiltDataset, books: Books, subject_type: str) -> dict[str, Any]:
    check_id, label = f"holdings_tie_{dataset.index}", "Holdings and cash add up to the account's value"
    if subject_type == "composite_portfolio":
        return _check(check_id, label, "not_checked", "A composite's sleeves are checked by the composite's own "
                      "reconciliation, not against a broker statement.", file=dataset.data_name)
    if dataset.item.get("currencies") or dataset.item.get("symbol"):
        return _check(check_id, label, "not_checked", "The file holds only some holdings, so it can't add up "
                      "to the whole account.", file=dataset.data_name)
    value = books.holdings_value(str(dataset.snapshot_date))
    if "reason" in value:
        return _check(check_id, label, "not_checked", str(value["reason"]), file=dataset.data_name)
    nav, positions, cash = value["nav"], value["positions"], value["cash"]
    difference = positions + cash - nav
    tolerance = max(Decimal(1), abs(nav) * HOLDINGS_TIE_TOLERANCE)
    within = abs(difference) <= tolerance
    return _check(check_id, label, "pass" if within else "review",
                  ("Holdings plus cash are within 1% of the account's value that day." if within
                   else "Holdings plus cash differ from the account's value that day by more than 1%."),
                  file=dataset.data_name, date=value["date"], currency=value["currency"], positions=positions,
                  cash=cash, account_value=nav, difference=difference)


def _cash_roll(dataset: BuiltDataset, books: Books, subject_type: str) -> dict[str, Any]:
    check_id, label = f"cash_roll_{dataset.index}", "Cash balances roll forward"
    item = dataset.item
    if item.get("transaction_types"):
        return _check(check_id, label, "not_checked", "The file holds only some kinds of cash activity, so "
                      "balances can't roll forward from it.", file=dataset.data_name)
    if subject_type != "physical_account":
        return _check(check_id, label, "not_checked", "Only a broker account's own cash rolls forward from its "
                      "statement.", file=dataset.data_name)
    roll = books.cash_roll(item["from_date"], item["to_date"])
    if "reason" in roll:
        return _check(check_id, label, "not_checked", str(roll["reason"]), file=dataset.data_name)
    currencies = [c for c in sorted(roll) if not item.get("currencies") or c in item["currencies"]]
    rows, apart = [], []
    for currency in currencies:
        r = roll[currency]
        unexplained = r["closing"] - r["opening"] - r["activity"] - r["trades"]
        tolerance = max(Decimal(1), abs(r["closing"]) * CASH_ROLL_TOLERANCE)
        rows.append({"currency": currency, "opening": format(r["opening"], "f"), "activity": format(r["activity"], "f"),
                     "trades": format(r["trades"], "f"), "closing": format(r["closing"], "f"),
                     "unexplained": format(unexplained, "f")})
        if abs(unexplained) > tolerance:
            apart.append(currency)
    # Currency conversions and corrections the statement books elsewhere can leave a residue; it is shown, not held.
    return _check(check_id, label, "info" if apart else "pass",
                  (f"Opening cash plus activity and trades does not reach closing cash in {', '.join(apart)}; "
                   "currency conversions are the usual reason." if apart
                   else "Opening cash plus activity and trades reaches closing cash in every currency."),
                  file=dataset.data_name, currencies=rows)


def run_checks(built: Sequence[BuiltDataset], books: Books, subject_type: str,
               adjustments: Sequence[str] = ()) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for dataset in built:
        checks.extend(file_checks(dataset))
        try:
            checks.extend(book_checks(dataset, books, subject_type))
        except (ValueError, KeyError, TypeError, InvalidOperation):
            checks.append(_check(f"books_{dataset.index}", f"{dataset.data_name} ties to the books", "not_checked",
                                 "The books could not be read for this check.", file=dataset.data_name))
    if adjustments:
        checks.append(_check("plan_adjusted", "Dates moved to the account's history", "info", " ".join(adjustments)))
    return checks


def overall(checks: Sequence[Mapping[str, Any]]) -> str:
    statuses = {c["status"] for c in checks}
    return "fail" if "fail" in statuses else "review" if "review" in statuses else "pass"


# What the checker agent reads, and the package ------------------------------------------------

def check_facts(*, account_id: str, plan: Mapping[str, Any], built: Sequence[BuiltDataset],
                checks: Sequence[Mapping[str, Any]], subject_type: str, reporting_currency: str) -> dict[str, Any]:
    return {
        "account_id": account_id, "title": plan["title"], "reporting_currency": reporting_currency,
        "status": overall(checks),
        "files": [{"file": d.data_name, "contents": describe(d.item, subject_type), "rows": len(d.rows),
                   "from_date": d.item["from_date"], "to_date": d.item["to_date"], "snapshot_date": d.snapshot_date,
                   "first_date": d.rows[0][d.spec.date_column][:10] if d.rows and not d.spec.snapshot else None,
                   "last_date": d.rows[-1][d.spec.date_column][:10] if d.rows and not d.spec.snapshot else None,
                   "totals_file": d.summary_name, "totals": d.summary_rows[:60]}
                  for d in built],
        "checks": list(checks),
    }


def preview(built: Sequence[BuiltDataset]) -> list[dict[str, Any]]:
    return [{"file": d.data_name, "contents": d.spec.label, "rows": len(d.rows), "columns": d.item["columns"],
             "sample": [[d_row[c] for c in d.item["columns"]] for d_row in d.rows[:PREVIEW_ROWS]],
             "totals_file": d.summary_name, "totals": d.summary_rows[:20]} for d in built]


def readme(*, account_name: str, plan: Mapping[str, Any], built: Sequence[BuiltDataset], checks: Sequence[Mapping[str, Any]],
           status: str, note: str, note_checked: bool, check_id: str, created_at: str, released_by_person: bool,
           subject_type: str) -> str:
    marks = {"pass": "PASS", "fail": "FAIL", "review": "REVIEW", "info": "NOTE", "not_checked": "NOT CHECKED"}
    lines = [f"{plan['title']}", f"{account_name}", "", f"Prepared {created_at} by the WealthRyse export agent.",
             f"Check record {check_id}. Result: {status.upper()}"
             + (" (released by the person after review)" if released_by_person else ""), "", "WHAT IS IN THIS EXPORT"]
    lines += [f"- {d.data_name}: {describe(d.item, subject_type)} {len(d.rows)} rows." for d in built]
    lines += ["", "COVER NOTE", note.strip() or "(none)", "",
              "Every figure in the cover note was matched to the check results before it was written here."
              if note_checked else "The cover note was written from the check results by code.", "", "CHECKS"]
    lines += [f"[{marks.get(c['status'], c['status'])}] {c['label']}: {c['detail']}" for c in checks]
    lines += ["", "Amounts are as recorded by the broker or the portfolio's ledger, in the currency of each row.",
              "manifest.json lists each file with its SHA-256 hash. This export is information, not advice."]
    return "\n".join(lines) + "\n"


def package(*, built: Sequence[BuiltDataset], plan: Mapping[str, Any], checks: Sequence[Mapping[str, Any]], status: str,
            note: str, note_checked: bool, check_id: str, account_id: str, account_name: str, created_at: str,
            released_by_person: bool, subject_type: str) -> bytes:
    contents = files(built)
    manifest = {
        "check_id": check_id, "account_id": account_id, "created_at": created_at, "status": status,
        "released_by_person": released_by_person, "fingerprint": fingerprint(built), "plan": plan,
        "files": [{"name": n, "sha256": hashlib.sha256(c).hexdigest(), "bytes": len(c)} for n, c in sorted(contents.items())],
        "checks": list(checks),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", readme(account_name=account_name, plan=plan, built=built, checks=checks,
                                              status=status, note=note, note_checked=note_checked, check_id=check_id,
                                              created_at=created_at, released_by_person=released_by_person,
                                              subject_type=subject_type))
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        for name, content in sorted(contents.items()):
            archive.writestr(name, content)
    return out.getvalue()


def fallback_note(facts: Mapping[str, Any]) -> str:
    """Used when the checker agent is unavailable or its note fails the figure check."""
    counts: dict[str, int] = {}
    for check in facts["checks"]:
        counts[check["status"]] = counts.get(check["status"], 0) + 1
    files_line = "; ".join(f"{f['file']} has {f['rows']} rows" for f in facts["files"])
    held = [c["label"] for c in facts["checks"] if c["status"] in ("fail", "review")]
    return (f"{facts['title']}: {files_line}. "
            f"{counts.get('pass', 0)} checks passed"
            + (f"; these need attention: {', '.join(held)}." if held else ".")
            + " The detailed results are listed below.")


def period_for(schedule: str, latest: str) -> tuple[str, str, str] | None:
    """The last whole period that ends on or before the latest data date: (from, to, label)."""
    if schedule not in SCHEDULES:
        raise ValueError(schedule)
    day = date.fromisoformat(latest)
    if schedule == "monthly":
        end = (day + timedelta(days=1)).replace(day=1) - timedelta(days=1)
        if end > day:
            end = day.replace(day=1) - timedelta(days=1)
        return end.replace(day=1).isoformat(), end.isoformat(), end.strftime("%B %Y")
    if schedule == "quarterly":
        quarter_start = date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)
        nxt = date(day.year + (quarter_start.month == 10), (quarter_start.month + 2) % 12 + 1, 1)
        end = nxt - timedelta(days=1)
        if end > day:
            end = quarter_start - timedelta(days=1)
        start = date(end.year, 3 * ((end.month - 1) // 3) + 1, 1)
        return start.isoformat(), end.isoformat(), f"Q{(end.month - 1) // 3 + 1} {end.year}"
    end = date(day.year, 12, 31)
    if end > day:
        end = date(day.year - 1, 12, 31)
    return date(end.year, 1, 1).isoformat(), end.isoformat(), str(end.year)
