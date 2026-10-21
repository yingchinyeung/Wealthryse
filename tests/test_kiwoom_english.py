"""A Kiwoom account's website text is English: Yahoo's names for stocks, English labels for movements."""
from __future__ import annotations

import sqlite3
import unittest

from ibkr_etl.kiwoom_english import (
    apply_english_names,
    movement_description,
    translate_account,
)
from ibkr_etl.sector_classification import YAHOO_PROVIDER


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE instruments (conid TEXT PRIMARY KEY, symbol TEXT, description TEXT);
        CREATE TABLE instrument_classifications (conid TEXT, provider TEXT, long_name TEXT);
        CREATE TABLE position_snapshots (account_id TEXT, conid TEXT);
        CREATE TABLE trades (account_id TEXT, source_key TEXT, conid TEXT, order_reference TEXT);
        CREATE TABLE cash_transactions (account_id TEXT, source_key TEXT, conid TEXT, transaction_type TEXT,
                                        description TEXT);
    """)
    connection.executemany("INSERT INTO instruments VALUES (?, ?, ?)", [
        ("K1", "000660", "SK하이닉스"),       # Yahoo has its name
        ("K2", "123456", "어떤회사"),         # Yahoo has not named it yet
        ("K3", "005930", "005930"),          # a code standing in, now named by Yahoo
        ("U1", "AAPL", "APPLE INC"),         # another account's English name
    ])
    connection.executemany("INSERT INTO instrument_classifications VALUES (?, ?, ?)", [
        ("K1", YAHOO_PROVIDER, "SK hynix Inc."), ("K3", YAHOO_PROVIDER, "Samsung Electronics Co., Ltd."),
        ("U1", YAHOO_PROVIDER, "Apple Inc."),
    ])
    connection.executemany("INSERT INTO position_snapshots VALUES ('KIWOOM-1', ?)", [("K1",), ("K3",)])
    connection.executemany("INSERT INTO trades VALUES ('KIWOOM-1', ?, ?, ?)", [
        ("t1", "K2", "보통매매"), ("t2", "K1", None)])
    connection.executemany("INSERT INTO cash_transactions VALUES ('KIWOOM-1', ?, ?, ?, ?)", [
        ("c1", "K1", "DIVIDEND", "배당금입금"), ("c2", None, "DEPOSIT", "이체입금"),
        ("c3", None, "INTEREST", "Interest")])
    return connection


class KiwoomEnglishTests(unittest.TestCase):
    def test_movement_labels(self):
        self.assertEqual(movement_description("DIVIDEND", "000660"), "Dividend 000660")
        self.assertEqual(movement_description("FX_EXCHANGE"), "Currency exchange")
        self.assertEqual(movement_description("SOMETHING_NEW"), "Something new")

    def test_korean_names_become_yahoo_names_or_the_code(self):
        connection = database()
        renamed = apply_english_names(connection, ["K1", "K2", "K3", "U1", "missing"])
        names = dict(connection.execute("SELECT conid, description FROM instruments").fetchall())
        self.assertEqual(names, {"K1": "SK hynix Inc.", "K2": "123456", "K3": "Samsung Electronics Co., Ltd.",
                                 "U1": "APPLE INC"})
        self.assertEqual(renamed, 3)
        self.assertEqual(apply_english_names(connection, ["K1", "K2", "K3", "U1"]), 0)

    def test_an_account_backfill_leaves_no_korean(self):
        connection = database()
        counts = translate_account(connection, "KIWOOM-1")
        self.assertEqual(counts, {"instruments": 3, "cash_transactions": 2, "trade_references": 1})
        text = [value for table, column in (("instruments", "description"), ("cash_transactions", "description"),
                                            ("trades", "order_reference"))
                for (value,) in connection.execute(f"SELECT {column} FROM {table}").fetchall() if value]
        self.assertTrue(all(value.isascii() for value in text), text)
        labels = dict(connection.execute("SELECT source_key, description FROM cash_transactions").fetchall())
        self.assertEqual(labels, {"c1": "Dividend 000660", "c2": "Deposit", "c3": "Interest"})
        self.assertEqual(translate_account(connection, "KIWOOM-1"),
                         {"instruments": 0, "cash_transactions": 0, "trade_references": 0})


if __name__ == "__main__":
    unittest.main()
