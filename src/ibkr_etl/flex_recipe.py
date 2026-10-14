"""The one Flex query every IBKR login uses, and a check that a statement matches it.

Instead of a query built differently for each account, every IBKR connection
uses the same recipe. The website shows the recipe when a connection is added,
and the first statement the platform downloads is checked against it before
any account from that login can be onboarded. The check reads the statement
only; it never makes another IBKR request.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class RecipeSection:
    key: str
    label: str
    # Lower-case fragments matched against record names, the same way the
    # loader decides what each record is; container names match exactly.
    record_names: tuple[str, ...]
    container_names: tuple[str, ...]
    fields: tuple[tuple[str, ...], ...]
    always_present: bool


RECIPE_SECTIONS: tuple[RecipeSection, ...] = (
    RecipeSection("account", "Account Information", ("accountinformation",), ("accountinformation",),
                  (("accountId",), ("currency", "baseCurrency")), True),
    RecipeSection("nav", "Net Asset Value (NAV) in Base", ("equitysummarybyreportdate", "netassetvalue"),
                  ("equitysummaryinbase", "netassetvalue"), (("reportDate",), ("total",)), True),
    RecipeSection("positions", "Open Positions", ("openposition",), ("openpositions",),
                  (("conid",), ("position",), ("markPrice",), ("positionValue",), ("currency",)), True),
    RecipeSection("cash", "Cash Report", ("cashreportcurrency",), ("cashreport",),
                  (("currency",), ("endingCash",)), True),
    RecipeSection("securities", "Financial Instrument Information", ("securityinfo", "financialinstrument"),
                  ("securitiesinfo", "financialinstrumentinformation"), (("conid",), ("isin",)), True),
    RecipeSection("fx", "Conversion Rates", ("conversionrate",), ("conversionrates", "currencyconversionrates"),
                  (("fromCurrency", "currency"), ("rate",)), True),
    RecipeSection("trades", "Trades", ("trade",), ("trades",),
                  (("conid",), ("tradeDate",), ("quantity",), ("tradePrice",)), False),
    RecipeSection("cash_transactions", "Cash Transactions", ("cashtransaction",), ("cashtransactions",),
                  (("amount",), ("currency",), ("type",)), False),
    RecipeSection("transfers", "Transfers", ("transfer",), ("transfers",), (("quantity",), ("currency",)), False),
    RecipeSection("corporate_actions", "Corporate Actions", ("corporateaction",), ("corporateactions",),
                  (("conid",), ("actionType", "type")), False),
)

# Shown on the Broker connections page next to the "Add a connection" form.
RECIPE_STEPS: tuple[str, ...] = (
    "In IBKR Client Portal open Performance & Reports, then Flex Queries, and create an Activity Flex Query.",
    "Name it WealthRyse daily. Tick these sections and choose Select All for each one's fields: "
    + ", ".join(section.label for section in RECIPE_SECTIONS) + ".",
    "Delivery configuration: every account, format XML, period Last 365 Calendar Days.",
    "General configuration: date format yyyyMMdd, time format HHmmss, separator semicolon, "
    "include canceled trades No, include currency rates Yes, breakout by day Yes.",
    "Save, and copy the query ID into the form.",
    "Under Flex Web Service Configuration, generate a token valid for one year and restrict it to the "
    "platform address shown on this page. Paste it only into this form.",
)

_DATE = re.compile(r"^[0-9]{8}$")


@dataclass(frozen=True)
class RecipeFinding:
    section: str
    label: str
    status: str  # pass, warn or fail
    message: str


@dataclass(frozen=True)
class RecipeResult:
    statement_findings: tuple[RecipeFinding, ...]
    account_findings: Mapping[str, tuple[RecipeFinding, ...]]

    @property
    def accounts(self) -> tuple[str, ...]:
        return tuple(sorted(self.account_findings))

    @property
    def status(self) -> str:
        states = {item.status for item in self.statement_findings}
        for findings in self.account_findings.values():
            states.update(item.status for item in findings)
        return "fail" if "fail" in states else "warn" if "warn" in states else "pass"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _matches(name: str, fragments: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(fragment in lowered for fragment in fragments)


def _record_matches(name: str, section: RecipeSection) -> bool:
    lowered = name.lower()
    if section.key == "trades":
        # "trade" alone would also match TradeConfirm and similar; keep it exact.
        return lowered == "trade"
    if section.key == "transfers":
        return lowered == "transfer"
    return _matches(lowered, section.record_names)


def _statement_findings(root: ET.Element) -> list[RecipeFinding]:
    findings = []
    query_type = root.attrib.get("type", "")
    if _local(root.tag) != "FlexQueryResponse" or query_type != "AF":
        findings.append(RecipeFinding("query", "Query type", "fail",
                                      "This is not an Activity Flex Query; create one from the recipe."))
    else:
        findings.append(RecipeFinding("query", "Query type", "pass", "Activity Flex Query."))
    return findings


def _account_findings(statement: ET.Element) -> list[RecipeFinding]:
    containers = {_local(child.tag).lower() for child in statement}
    records: dict[str, list[ET.Element]] = defaultdict(list)
    for child in statement:
        for element in child.iter():
            if element.attrib and not list(element):
                for section in RECIPE_SECTIONS:
                    if _record_matches(_local(element.tag), section):
                        records[section.key].append(element)

    findings: list[RecipeFinding] = []
    dates = [statement.attrib.get("fromDate", ""), statement.attrib.get("toDate", "")]
    if all(_DATE.fullmatch(value) for value in dates):
        findings.append(RecipeFinding("dates", "Date format", "pass", "Dates use yyyyMMdd."))
    else:
        findings.append(RecipeFinding("dates", "Date format", "fail",
                                      "Set the query's date format to yyyyMMdd so dates cannot be misread."))

    for section in RECIPE_SECTIONS:
        present = section.key in records or bool(containers & set(section.container_names))
        if not present:
            if section.always_present:
                findings.append(RecipeFinding(section.key, section.label, "fail",
                                              f"Missing. Tick {section.label} in the Flex query."))
            else:
                findings.append(RecipeFinding(
                    section.key, section.label, "warn",
                    f"Not in this statement. Make sure {section.label} is ticked; "
                    "IBKR may leave it out when the period had none.",
                ))
            continue
        rows = records.get(section.key, [])
        missing = [
            "/".join(options)
            for options in section.fields
            if rows and not any(any(name in row.attrib for name in options) for row in rows)
        ]
        if missing:
            findings.append(RecipeFinding(section.key, section.label, "fail",
                                          f"Fields missing: {', '.join(missing)}. Choose Select All for this section."))
        else:
            findings.append(RecipeFinding(section.key, section.label, "pass",
                                          f"{len(rows)} records." if rows else "Ticked; no records in this period."))

    nav_rows = records.get("nav", [])
    nav_dates = {row.attrib.get("reportDate") for row in nav_rows if row.attrib.get("reportDate")}
    if dates[0] != dates[1] and _DATE.fullmatch(dates[0] or "") and len(nav_dates) == 1:
        findings.append(RecipeFinding("nav_daily", "Daily NAV", "warn",
                                      "NAV has one date for a longer period. Set breakout by day to Yes."))
    return findings


def check_flex_recipe(content: bytes) -> RecipeResult:
    """Compare one downloaded statement with the recipe, account by account."""

    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(f"The statement is not valid XML: {error}") from error
    if _local(root.tag) == "FlexStatementResponse":
        raise ValueError("IBKR returned an error instead of a statement")
    statements = [element for element in root.iter() if _local(element.tag) == "FlexStatement"]
    if not statements:
        raise ValueError("The statement has no accounts")

    by_account: dict[str, tuple[RecipeFinding, ...]] = {}
    for statement in statements:
        account = (statement.attrib.get("accountId") or "").strip().upper()
        if not account:
            continue
        by_account[account] = tuple(_account_findings(statement))
    if not by_account:
        raise ValueError("The statement names no account")
    return RecipeResult(tuple(_statement_findings(root)), by_account)
