"""English text for Kiwoom accounts on the website.

Kiwoom names stocks in Korean (SK하이닉스) and labels each cash movement in
Korean (배당금입금). The website shows instruments.description and the cash
movement description as stored, so a Kiwoom account's pages would read in
Korean. Here a stock takes Yahoo's English company name, which the sector job
stores as long_name, and a movement takes an English label for its kind. Until
Yahoo has named a stock, its code stands in, so Korean never reaches a page.
The raw Korean stays in the captured Kiwoom responses and in security_master.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .sector_classification import YAHOO_PROVIDER, has_long_name

KIND_LABELS: dict[str, str] = {
    "BUY": "Buy",
    "SELL": "Sell",
    "TRANSFER_IN": "Shares transferred in",
    "TRANSFER_OUT": "Shares transferred out",
    "DIVIDEND": "Dividend",
    "INTEREST": "Interest",
    "TAX": "Tax",
    "FEE": "Fee",
    "WITHDRAWAL": "Withdrawal",
    "DEPOSIT": "Deposit",
    "FX_EXCHANGE": "Currency exchange",
}


def is_english(text: Any) -> bool:
    return str(text or "").isascii()


def movement_description(kind: str, code: str | None = None) -> str:
    """An English label for a Kiwoom cash movement, with the stock code when it has one."""

    label = KIND_LABELS.get(kind, kind.replace("_", " ").capitalize())
    return f"{label} {code}" if code else label


def _chunks(values: list[str], size: int = 500) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def english_names(connection, conids: Iterable[str]) -> dict[str, str]:
    """Yahoo's English company name for each instrument that has one."""

    wanted = sorted({str(conid) for conid in conids})
    if not wanted or not has_long_name(connection):
        return {}
    found: dict[str, str] = {}
    for chunk in _chunks(wanted):
        rows = connection.execute(
            f"SELECT conid, long_name FROM instrument_classifications WHERE provider = ? "
            f"AND conid IN ({', '.join('?' for _ in chunk)})",
            (YAHOO_PROVIDER, *chunk),
        ).fetchall()
        for row in rows:
            name = " ".join(str(row["long_name"] or "").split())
            if name and name.isascii():
                found[str(row["conid"])] = name[:200]
    return found


def apply_english_names(connection, conids: Iterable[str]) -> int:
    """Give each instrument with a Korean name Yahoo's English name, or its code until Yahoo has one.

    An instrument already named in English is left alone, except one still carrying
    its code as a stand-in, which takes Yahoo's name once there is one.
    Returns the number of instruments renamed. The caller commits.
    """

    wanted = sorted({str(conid) for conid in conids})
    if not wanted:
        return 0
    rows: list[Mapping[str, Any]] = []
    for chunk in _chunks(wanted):
        rows.extend(connection.execute(
            f"SELECT conid, symbol, description FROM instruments WHERE conid IN ({', '.join('?' for _ in chunk)})",
            tuple(chunk),
        ).fetchall())
    names = english_names(connection, [str(row["conid"]) for row in rows])
    renamed = 0
    for row in rows:
        conid, symbol, description = str(row["conid"]), str(row["symbol"] or ""), str(row["description"] or "")
        stand_in = description == symbol
        if is_english(description) and not (stand_in and conid in names):
            continue
        english = names.get(conid) or (symbol if is_english(symbol) and symbol else conid)
        if english == description:
            continue
        connection.execute("UPDATE instruments SET description = ? WHERE conid = ?", (english, conid))
        renamed += 1
    return renamed


def translate_account(connection, account_id: str) -> dict[str, int]:
    """Put one Kiwoom account's stored names and movement labels into English. The caller commits."""

    conids = {str(row["conid"]) for table in ("position_snapshots", "trades", "cash_transactions")
              for row in connection.execute(
                  f"SELECT DISTINCT conid FROM {table} WHERE account_id = ? AND conid IS NOT NULL", (account_id,),
              ).fetchall()}
    instruments = apply_english_names(connection, conids)
    symbols = {str(row["conid"]): str(row["symbol"] or "") for row in connection.execute(
        "SELECT i.conid, i.symbol FROM instruments i JOIN cash_transactions c ON c.conid = i.conid "
        "WHERE c.account_id = ?", (account_id,)).fetchall()}
    movements = 0
    for row in connection.execute(
        "SELECT source_key, transaction_type, description, conid FROM cash_transactions WHERE account_id = ?",
        (account_id,),
    ).fetchall():
        if is_english(row["description"]):
            continue
        code = symbols.get(str(row["conid"])) if row["conid"] else None
        connection.execute(
            "UPDATE cash_transactions SET description = ? WHERE account_id = ? AND source_key = ?",
            (movement_description(str(row["transaction_type"]), code or None), account_id, row["source_key"]),
        )
        movements += 1
    references = 0
    for row in connection.execute(
        "SELECT source_key, order_reference FROM trades WHERE account_id = ? AND order_reference IS NOT NULL",
        (account_id,),
    ).fetchall():
        if is_english(row["order_reference"]):
            continue
        connection.execute("UPDATE trades SET order_reference = NULL WHERE account_id = ? AND source_key = ?",
                           (account_id, row["source_key"]))
        references += 1
    return {"instruments": instruments, "cash_transactions": movements, "trade_references": references}
