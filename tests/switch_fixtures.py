"""Synthetic v1.1 Flex statements and snapshots for the medallion switch tests.

Accounts and values are made up. Each record's attributes depend only on the
account and its own date, never on the statement period, so overlapping daily
statements report identical records, as IBKR does.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import quoteattr

from ibkr_etl.config import Settings
from ibkr_etl.db import initialize
from ibkr_etl.pilot_snapshot import build_pilot_snapshot
from ibkr_etl.service import ingest_content

ACCOUNT_A = "U9100001"
ACCOUNT_B = "U9100002"
ACCOUNT_C = "U9100003"
FIRST_RUN = date(2026, 3, 9)


def _element(tag: str, **attributes: object) -> str:
    text = " ".join(f"{key}={quoteattr(str(value))}" for key, value in attributes.items())
    return f"<{tag} {text} />"


def _d(day: date) -> str:
    return day.strftime("%Y%m%d")


def _days(start: date, end: date):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def account_statement(account: str, start: date, end: date, *, deposits=None, period_dependent_fx=False,
                      nav_without_currency=False, transfers=None) -> str:
    deposits = deposits or {}
    seed = int(account[-1])
    parts = [f'<FlexStatement accountId="{account}" fromDate="{_d(start)}" toDate="{_d(end)}" period="Custom">']
    parts.append(_element("AccountInformation", accountId=account, accountAlias="Synthetic",
                          currency="USD", baseCurrency="USD", accountType="Individual"))
    parts.append("<FinancialInstrumentInformation>")
    parts.append(_element("FinancialInstrument", conid="9001", symbol="SYNA", description="SYNTHETIC A",
                          assetCategory="STK", currency="USD", listingExchange="NYSE"))
    parts.append(_element("FinancialInstrument", conid="9002", symbol="SYNB", description="SYNTHETIC B",
                          assetCategory="STK", currency="HKD", listingExchange="SEHK"))
    parts.append("</FinancialInstrumentInformation>")
    parts.append("<EquitySummaryInBase>")
    for day in _days(start, end):
        base = 100000 + seed * 1000 + day.toordinal() % 97
        attributes = dict(accountId=account, reportDate=_d(day), cash=f"{base - 60000}.25",
                          stock="60000.00", dividendAccruals="1.50", total=f"{base + 1}.75")
        if not nav_without_currency:
            attributes["currency"] = "USD"
        parts.append(_element("EquitySummaryByReportDateInBase", **attributes))
    parts.append("</EquitySummaryInBase>")
    parts.append("<OpenPositions>")
    parts.append(_element("OpenPosition", accountId=account, currency="USD", assetCategory="STK", symbol="SYNA",
                          conid="9001", reportDate=_d(end), position="100", markPrice="300.00",
                          positionValue="30000.00", costBasisMoney="25000.00", fifoPnlUnrealized="5000.00"))
    parts.append(_element("OpenPosition", accountId=account, currency="HKD", assetCategory="STK", symbol="SYNB",
                          conid="9002", reportDate=_d(end), position="2000", markPrice="117.1875",
                          positionValue="234375.00", costBasisMoney="200000.00", fifoPnlUnrealized="34375.00"))
    parts.append("</OpenPositions>")
    parts.append("<CashReport>")
    for currency, amount in (("BASE_SUMMARY", "40000.25"), ("USD", "30000.25"), ("HKD", "78125.00")):
        parts.append(_element("CashReportCurrency", accountId=account, currency=currency, fromDate=_d(start),
                              toDate=_d(end), endingCash=amount, endingSettledCash=amount))
    parts.append("</CashReport>")
    parts.append("<ConversionRates>")
    for day in _days(start, end):
        parts.append(_element("ConversionRate", reportDate=_d(day), fromCurrency="HKD", toCurrency="USD",
                              rate="0.128"))
    parts.append("</ConversionRates>")
    parts.append("<Trades>")
    for day in _days(start, end):
        if day.toordinal() % 3 == 0:
            parts.append(_element("Trade", accountId=account, currency="USD", assetCategory="STK", symbol="SYNA",
                                  conid="9001", tradeID=f"T{seed}{day.toordinal()}",
                                  ibExecID=f"E{seed}.{day.toordinal()}", tradeDate=_d(day), reportDate=_d(day),
                                  buySell="BUY", quantity="1", tradePrice="300", proceeds="-300",
                                  ibCommission="-1", ibCommissionCurrency="USD", netCash="-301"))
    parts.append("</Trades>")
    parts.append("<CashTransactions>")
    for day in _days(start, end):
        if day.toordinal() % 5 == 0:
            parts.append(_element("CashTransaction", accountId=account, currency="USD", conid="9001",
                                  transactionID=f"C{seed}{day.toordinal()}", reportDate=_d(day),
                                  dateTime=f"{_d(day)};160000", type="Dividends",
                                  description="SYNA CASH DIVIDEND", amount="12.50"))
        if day.toordinal() % 4 == 0:
            # No transactionID: keyed by a content hash.
            parts.append(_element("CashTransaction", accountId=account, currency="HKD", conid="9002",
                                  reportDate=_d(day), dateTime=f"{_d(day)};160000", type="Withholding Tax",
                                  description="SYNB TAX", amount="-3.20"))
        if (account, day) in deposits:
            parts.append(_element("CashTransaction", accountId=account, currency="USD",
                                  transactionID=f"D{seed}{day.toordinal()}", reportDate=_d(day),
                                  dateTime=f"{_d(day)};100000", type="Deposits/Withdrawals",
                                  description="CASH RECEIPTS / ELECTRONIC FUND TRANSFERS",
                                  amount=deposits[(account, day)]))
    parts.append("</CashTransactions>")
    parts.append("<CorporateActions>")
    for day in _days(start, end):
        if day.toordinal() % 11 == 0:
            parts.append(_element("CorporateAction", accountId=account, currency="USD", conid="9001",
                                  actionID=f"A{seed}{day.toordinal()}", reportDate=_d(day), type="FS",
                                  description="SYNA SPLIT", quantity="10", amount="0"))
    parts.append("</CorporateActions>")
    parts.append("<FxTransactions>")
    for day in _days(start, end):
        if day.toordinal() % 6 == 0:
            cost = "-128.00" if not period_dependent_fx else f"-{128 + (day - start).days}.00"
            parts.append(_element("FxTransaction", accountId=account, reportDate=_d(day),
                                  dateTime=f"{_d(day)};120000", functionalCurrency="USD", fxCurrency="HKD",
                                  activityDescription="Forex Trade", quantity="1000", proceeds="128.10",
                                  cost=cost, realizedPL="0.10"))
    parts.append("</FxTransactions>")
    if transfers is None:
        parts.append("<Transfers />")
    else:
        parts.append("<Transfers>")
        for day, amount in transfers:
            if start <= day <= end:
                parts.append(_element("Transfer", accountId=account, currency="USD", assetCategory="CASH",
                                      date=_d(day), type="INTERNAL", direction="IN", cashTransfer=amount))
        parts.append("</Transfers>")
    parts.append("<ChangeInNAV>")
    parts.append(_element("ChangeInNAV", accountId=account, fromDate=_d(start), toDate=_d(end), twr="0.5"))
    parts.append("</ChangeInNAV>")
    parts.append("</FlexStatement>")
    return "".join(parts)


def statement_for_run(run_day: date, accounts=(ACCOUNT_A, ACCOUNT_B, ACCOUNT_C), *, lookback=7, **options) -> bytes:
    """What v1.1 fetches on run_day: the 7 days ending yesterday."""

    end = run_day - timedelta(days=1)
    start = end - timedelta(days=lookback - 1)
    body = "".join(account_statement(account, start, end, **options) for account in accounts)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<FlexQueryResponse queryName="Synthetic" type="AF">'
        f'<FlexStatements count="{len(accounts)}">{body}</FlexStatements></FlexQueryResponse>'
    ).encode("utf-8")


def settings_for(root: Path) -> Settings:
    return Settings(
        project_root=root, flex_token="", flex_query_id="", database_path=root / "v11.sqlite",
        raw_report_dir=root / "raw", log_dir=root / "logs", timezone_name="Asia/Hong_Kong",
        daily_lookback_days=7, user_agent="test", request_timeout_seconds=10, max_wait_seconds=20,
    )


def v11_export(root: Path, statements: list[bytes], generated_at: str) -> Path:
    """Stand-in for v1.1's database and export: the same ETL, loaded daily."""

    root.mkdir(parents=True, exist_ok=True)
    settings = settings_for(root)
    initialize(settings.database_path)
    for content in statements:
        ingest_content(settings, content, run_type="test-v11-daily")
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS snapshot_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT OR REPLACE INTO snapshot_metadata VALUES ('generated_at', ?)", (generated_at,))
        connection.commit()
    finally:
        connection.close()
    return settings.database_path


def account_snapshot(export: Path, output: Path, account: str, start_date: str, generated_at: str) -> Path:
    build_pilot_snapshot(export, output, account_id=account, start_date=start_date, generated_at=generated_at)
    return output


def edit_snapshot(path: Path, statements: list[str]) -> None:
    """Change a snapshot and keep its metadata row counts valid."""

    connection = sqlite3.connect(path)
    try:
        for sql in statements:
            connection.execute(sql)
        metadata = dict(connection.execute("SELECT key, value FROM snapshot_metadata"))
        counts = json.loads(metadata["row_counts_json"])
        for table in counts:
            counts[table] = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        connection.execute("UPDATE snapshot_metadata SET value = ? WHERE key = 'row_counts_json'",
                           (json.dumps(counts, sort_keys=True),))
        connection.commit()
    finally:
        connection.close()


def scenario(root: Path, runs: int = 10, *, statement_options=None, accounts=(ACCOUNT_A, ACCOUNT_B, ACCOUNT_C),
             snapshot_accounts=(ACCOUNT_A, ACCOUNT_B), start_date: str = "2026-03-01"):
    """v1.1 runs daily for `runs` days; returns its statements and the last day's snapshots."""

    statement_options = statement_options or {}
    days = [FIRST_RUN + timedelta(days=offset) for offset in range(runs)]
    statements = [statement_for_run(day, accounts, **statement_options) for day in days]
    generated_at = f"{days[-1].isoformat()}T16:02:35+08:00"
    export = v11_export(root / "v11", statements, generated_at)
    snapshots = {
        account: account_snapshot(export, root / "snapshots" / f"{account}.sqlite", account, start_date, generated_at)
        for account in snapshot_accounts
    }
    return days, statements, snapshots
