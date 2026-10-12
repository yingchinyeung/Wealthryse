from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable

from .util import canonical_json, parse_date_text, pick, sha256_text


class FlexParseError(ValueError):
    pass


@dataclass(frozen=True)
class FlexRow:
    account_id: str
    section: str
    record_type: str
    attributes: dict[str, str]
    source_key: str
    row_sha256: str
    report_date: str | None
    conid: str | None
    currency: str | None


@dataclass(frozen=True)
class ParsedReport:
    rows: list[FlexRow]
    statement_accounts: set[str]


ID_FIELDS = (
    "ibExecID", "transactionID", "actionID", "brokerageOrderID",
    "orderID", "activityDescriptionId", "detailLevel",
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter_record_elements(statement: ET.Element) -> Iterable[tuple[str, ET.Element]]:
    for section_element in statement:
        section = _local_name(section_element.tag)
        children = list(section_element)
        if section_element.attrib and not children:
            yield section, section_element
            continue
        for element in section_element.iter():
            if element is section_element:
                continue
            if element.attrib and not list(element):
                yield section, element


def _source_key(
    account_id: str,
    section: str,
    record_type: str,
    attributes: dict[str, str],
    *,
    effective_report_date: str | None = None,
) -> str:
    kind = record_type.lower()
    if kind in {"conversionrate", "fxrate", "currencyconversionrate"}:
        # Currency pairs are part of an FX snapshot's identity. The generic
        # snapshot key previously collapsed different pairs on the same date.
        # Use the same effective date and aliases as normalization, before the
        # generic ID fields (detailLevel is not a unique FX observation ID).
        identity = {
            "account": account_id,
            "section": section,
            "record_type": record_type,
            "date": effective_report_date,
            "from_currency": pick(attributes, "fromCurrency", "currency"),
            "to_currency": pick(attributes, "toCurrency", "baseCurrency", default="BASE"),
            "model": pick(attributes, "model", "modelCode"),
        }
        if not identity["date"] or not identity["from_currency"]:
            # Retain distinct malformed evidence; do not invent missing keys.
            return f"fx-rate-unresolved:v2:{sha256_text(canonical_json({'identity': identity, 'row': attributes}))}"
        # Rate is deliberately excluded so a broker correction keeps its key.
        return f"fx-rate:v2:{sha256_text(canonical_json(identity))}"
    execution_id = pick(attributes, "ibExecID")
    if execution_id:
        return f"ibExecID:{execution_id}"
    trade_id = pick(attributes, "tradeID")
    if trade_id:
        # Flex trade IDs can repeat on different report dates inside one query.
        # The report date distinguishes those broker-supplied detail rows.
        report_date = pick(attributes, "reportDate", "tradeDate", "dateTime")
        return f"tradeID:{trade_id}:reportDate:{report_date}"
    for field in ID_FIELDS:
        value = pick(attributes, field)
        if value:
            return f"{field}:{value}"

    if kind == "cashtransaction":
        material = {
            "account": account_id,
            "date_time": pick(attributes, "dateTime"),
            "report_date": pick(attributes, "reportDate"),
            "settle_date": pick(attributes, "settleDate"),
            "conid": pick(attributes, "conid"),
            "symbol": pick(attributes, "symbol"),
            "currency": pick(attributes, "currency"),
            "model": pick(attributes, "model", "modelCode"),
            "description": pick(attributes, "description"),
            "amount": pick(attributes, "amount"),
        }
        return f"cash:{sha256_text(canonical_json(material))}"
    if kind == "fxtransaction":
        material = {
            "account": account_id,
            "date_time": pick(attributes, "dateTime"),
            "report_date": pick(attributes, "reportDate"),
            "functional_currency": pick(attributes, "functionalCurrency"),
            "fx_currency": pick(attributes, "fxCurrency"),
            "model": pick(attributes, "model", "modelCode"),
            "description": pick(attributes, "activityDescription"),
            "quantity": pick(attributes, "quantity"),
            "proceeds": pick(attributes, "proceeds"),
            "cost": pick(attributes, "cost"),
            "realized_pl": pick(attributes, "realizedPL"),
        }
        return f"fx:{sha256_text(canonical_json(material))}"
    if kind == "corporateaction":
        material = {
            "account": account_id,
            "date_time": pick(attributes, "dateTime"),
            "report_date": pick(attributes, "reportDate"),
            "conid": pick(attributes, "conid"),
            "model": pick(attributes, "model", "modelCode"),
            "type": pick(attributes, "type"),
            "description": pick(attributes, "description", "actionDescription"),
            "quantity": pick(attributes, "quantity"),
            "amount": pick(attributes, "amount", "proceeds", "value"),
            "cost_basis": pick(attributes, "costBasis"),
        }
        return f"corporate-action:{sha256_text(canonical_json(material))}"

    identity = {
        "account": account_id,
        "section": section,
        "record_type": record_type,
        "date": pick(attributes, "reportDate", "tradeDate", "date", "settleDate"),
        "timestamp": pick(attributes, "dateTime", "orderTime"),
        "conid": pick(attributes, "conid"),
        "currency": pick(attributes, "currency"),
        "asset_class": pick(attributes, "assetClass", "assetCategory"),
        "model": pick(attributes, "model", "modelCode"),
        "type": pick(attributes, "type", "transactionType", "actionType"),
        "description": pick(attributes, "description", "activityDescription", "actionDescription"),
    }
    snapshot_types = {
        "openposition", "cashreportcurrency", "cashreport",
        "netassetvaluebyassetclass", "equitysummarybyreportdateinbase",
        "conversionrate", "fxrate", "currencyconversionrate",
    }
    # Snapshots need a stable natural key so corrected values replace the prior
    # value. Other broker rows without IDs use the full content hash so distinct
    # cash/FX/corporate-action details can never be silently collapsed.
    material = identity if kind in snapshot_types else {
        "identity": identity,
        "row": attributes,
    }
    return f"derived:{sha256_text(canonical_json(material))}"


def parse_flex_xml(content: bytes) -> ParsedReport:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise FlexParseError(f"Invalid Flex XML: {exc}") from exc

    if _local_name(root.tag) == "FlexStatementResponse":
        message = root.findtext("ErrorMessage") or "IBKR returned an error response"
        code = root.findtext("ErrorCode")
        raise FlexParseError(f"{message} (code {code})" if code else message)

    statements = [element for element in root.iter() if _local_name(element.tag) == "FlexStatement"]
    if not statements:
        raise FlexParseError("No FlexStatement elements were found")

    rows: list[FlexRow] = []
    accounts: set[str] = set()
    for statement in statements:
        statement_account = pick(statement.attrib, "accountId", "account")
        if statement_account:
            accounts.add(statement_account)
        statement_to_date = parse_date_text(pick(statement.attrib, "toDate"))

        # Preserve statement metadata as a first-class row.
        if statement.attrib:
            attrs = dict(statement.attrib)
            account_id = statement_account or "UNKNOWN"
            row_json = canonical_json(attrs)
            rows.append(
                FlexRow(
                    account_id=account_id,
                    section="Statement",
                    record_type="FlexStatement",
                    attributes=attrs,
                    source_key=_source_key(account_id, "Statement", "FlexStatement", attrs),
                    row_sha256=sha256_text(row_json),
                    report_date=statement_to_date,
                    conid=None,
                    currency=pick(attrs, "currency", "baseCurrency") or None,
                )
            )

        for section, element in _iter_record_elements(statement):
            attrs = dict(element.attrib)
            account_id = pick(attrs, "accountId", "account", default=statement_account or "UNKNOWN")
            if account_id in {"-", "UNKNOWN"} and statement_account:
                account_id = statement_account
            if account_id != "UNKNOWN":
                accounts.add(account_id)
            record_type = _local_name(element.tag)
            report_date = parse_date_text(
                pick(attrs, "reportDate", "tradeDate", "date", "effectiveDate", "toDate")
            ) or statement_to_date
            row_json = canonical_json(attrs)
            rows.append(
                FlexRow(
                    account_id=account_id,
                    section=section,
                    record_type=record_type,
                    attributes=attrs,
                    source_key=_source_key(account_id, section, record_type, attrs,
                                           effective_report_date=report_date),
                    row_sha256=sha256_text(row_json),
                    report_date=report_date,
                    conid=pick(attrs, "conid") or None,
                    currency=pick(attrs, "currency") or None,
                )
            )

    if not rows:
        raise FlexParseError("The Flex report contained no data rows")
    return ParsedReport(rows=rows, statement_accounts=accounts)
