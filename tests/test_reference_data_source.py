"""derived-daily's reference data from v1.1's file or from v2's own tables.

Synthetic data only. The file side runs v1.1's real refresh code, and the
database side runs v2's real classification and Treasury parsing code, against
the same fake provider responses. tests/test_reference_data_source_db.py does
the database side against a real PostgreSQL.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ibkr_etl.db import connect, initialize
from ibkr_etl.derived_portfolio import refresh_filtered_portfolio
from ibkr_etl.reference_data import refresh_reference_data
from ibkr_etl.reference_data_source import (
    build_reference_data_from_database,
    compare_risk_free,
    compare_sectors,
    exclusion_impact,
    filtered_portfolio_inputs,
    materialize_database_reference,
    reference_bytes,
    reference_data_source,
    risk_free_entry,
    sector_entry,
)
from ibkr_etl.reference_snapshot import inspect_reference_bytes
from ibkr_etl.risk_free import PROVIDER as TREASURY_PROVIDER
from ibkr_etl.risk_free import SERIES_ID, parse_treasury_3m_xml
from ibkr_etl.sector_classification import YAHOO_PROVIDER, lookup_classification
from ibkr_etl.subject_onboarding import filtered_portfolio_definition

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = str(ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import run_self_service_derived_refresh as worker  # noqa: E402

TENANT = "00000000-0000-0000-0000-000000000001"
ACCOUNT = "UTEST9001"
DATES = ("2026-10-13", "2026-10-14")
GENERATED_AT = "2026-10-15T16:50:00+08:00"
# conid, symbol, listing exchange, currency, market value on each date
HOLDINGS = (
    ("9100001", "700", "SEHK", "HKD", (800.0, 810.0)),
    ("9100002", "AAPL", "NASDAQ", "USD", (200.0, 205.0)),
    ("9100003", "2330", "TWSE", "TWD", (3000.0, 3100.0)),
    ("9100004", "ZZZZ", "NYSE", "USD", (50.0, 55.0)),
)
YAHOO = {
    "0700.HK": {"symbol": "0700.HK", "sector": "Communication Services",
                "industry": "Internet Content & Information", "exchange": "HKG"},
    "AAPL": {"symbol": "AAPL", "sector": "Technology",
             "industry": "Consumer Electronics", "exchange": "NMS"},
    "2330.TW": {"symbol": "2330.TW", "sector": "Technology",
                "industry": "Semiconductors", "exchange": "TAI"},
}
TREASURY_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
  <entry><content type="application/xml"><m:properties>
    <d:NEW_DATE>2026-10-13T00:00:00</d:NEW_DATE><d:BC_3MONTH>4.05</d:BC_3MONTH>
  </m:properties></content></entry>
  <entry><content type="application/xml"><m:properties>
    <d:NEW_DATE>2026-10-14T00:00:00</d:NEW_DATE><d:BC_3MONTH>4.02</d:BC_3MONTH>
  </m:properties></content></entry>
</feed>
"""


def yahoo_search(symbol: str, timeout: int = 20) -> dict:
    quote = YAHOO.get(symbol.upper())
    return {"quotes": [dict(quote)] if quote else []}


def fake_get(url: str, timeout: int = 30) -> bytes:
    """Stands in for both v1.1's and v2's HTTP reads."""

    if "treasury.gov" in url:
        return TREASURY_XML
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return json.dumps(yahoo_search(query["q"][0])).encode()


def holdings() -> list[dict]:
    return [{"conid": conid, "symbol": symbol, "listing_exchange": exchange, "currency": currency}
            for conid, symbol, exchange, currency, _ in HOLDINGS]


def definition(**changes) -> dict:
    values = dict(
        subject_id="synthetic-non-taiwan", display_name="Synthetic Non-Taiwan",
        source_account_id=ACCOUNT, base_currency="USD", history_start_date=DATES[0],
        excluded_countries=("Taiwan",), excluded_exchanges=("TWSE",), excluded_currencies=("TWD",),
        opening_cash={"HKD": 5000.0, "USD": 1000.0}, allowed_cash_currencies=("HKD", "USD"),
        include_actual_interest=True,
    )
    values.update(changes)
    return filtered_portfolio_definition(**values)


def load_source_account(connection) -> None:
    """One synthetic IBKR account: positions, NAV, FX, cash and trades."""

    connection.execute(
        "INSERT INTO ingestion_runs(run_type, requested_from, requested_to, started_at, completed_at, "
        "status, row_count, reconciliation_json) VALUES ('test', ?, ?, ?, ?, 'success', 1, '{}')",
        (DATES[0], DATES[1], DATES[1] + "T08:00:00Z", DATES[1] + "T08:01:00Z"),
    )
    run_id = connection.execute(
        "SELECT run_id FROM ingestion_runs ORDER BY run_id DESC LIMIT 1").fetchone()[0]
    connection.execute(
        "INSERT INTO accounts(account_id, alias, base_currency, account_type, ib_entity, "
        "first_seen_at, last_seen_at, source_json) VALUES (?, 'Synthetic', 'USD', 'Individual', "
        "'IBHK', ?, ?, '{}')", (ACCOUNT, DATES[0], DATES[1]))
    for conid, symbol, exchange, currency, _ in HOLDINGS:
        connection.execute(
            "INSERT INTO instruments(conid, symbol, description, asset_category, currency, "
            "listing_exchange, first_seen_at, last_seen_at, source_json) "
            "VALUES (?, ?, ?, 'STK', ?, ?, ?, ?, '{}')",
            (conid, symbol, f"SYNTHETIC {symbol}", currency, exchange, DATES[0], DATES[1]))
    fx = {"HKD": "0.128", "TWD": "0.031"}
    for index, day in enumerate(DATES):
        total = 0.0
        for conid, _, _, currency, values in HOLDINGS:
            total += values[index] * (float(fx[currency]) if currency in fx else 1.0)
            connection.execute(
                "INSERT INTO position_snapshots(account_id, snapshot_date, conid, model_code, currency, "
                "quantity_text, cost_basis_text, mark_price_text, market_value_text, unrealized_pnl_text, "
                "row_sha256, last_seen_run_id) VALUES (?, ?, ?, '', ?, '1', ?, ?, ?, '0', ?, ?)",
                (ACCOUNT, day, conid, currency, str(values[index]), str(values[index]),
                 str(values[index]), f"position-{day}-{conid}", run_id))
        connection.execute(
            "INSERT INTO nav_snapshots(account_id, snapshot_date, currency, asset_class, total_text, "
            "cash_text, stock_text, options_text, futures_text, bonds_text, accruals_text, row_sha256, "
            "last_seen_run_id) VALUES (?, ?, 'USD', 'TOTAL', ?, '100', ?, '0', '0', '0', '0', ?, ?)",
            (ACCOUNT, day, str(total + 100), str(total), f"nav-{day}", run_id))
        for currency, rate in fx.items():
            connection.execute(
                "INSERT INTO fx_rates(account_id, rate_date, from_currency, to_currency, rate_text, "
                "row_sha256, last_seen_run_id) VALUES (?, ?, ?, 'USD', ?, ?, ?)",
                (ACCOUNT, day, currency, rate, f"fx-{day}-{currency}", run_id))
    cash = (
        ("cash-hk-dividend", DATES[1], "9100001", "40", "HKD", "700 cash dividend"),
        ("cash-tw-dividend", DATES[1], "9100003", "12", "USD", "2330 cash dividend"),
        ("cash-twd-fee", DATES[1], None, "-5", "TWD", "custody fee"),
        ("cash-usd-interest", DATES[1], None, "2", "USD", "USD CREDIT INT"),
    )
    for source_key, day, conid, amount, currency, description in cash:
        connection.execute(
            "INSERT INTO cash_transactions(account_id, source_key, transaction_id, event_datetime, "
            "model_code, effective_date, settle_date, transaction_type, description, amount_text, "
            "currency, conid, row_sha256, last_seen_run_id) VALUES (?, ?, '', ?, '', ?, ?, '', ?, ?, ?, ?, ?, ?)",
            (ACCOUNT, source_key, day.replace("-", "") + ";120000", day, day, description, amount,
             currency, conid, f"row-{source_key}", run_id))
    trades = (
        ("trade-aapl", "9100002", "BUY", "1", "-200", "USD"),
        ("trade-2330", "9100003", "BUY", "1", "-3000", "TWD"),
    )
    for source_key, conid, side, quantity, proceeds, currency in trades:
        connection.execute(
            "INSERT INTO trades(account_id, source_key, conid, trade_date, settle_date, buy_sell, "
            "quantity_text, proceeds_text, commission_text, commission_currency, net_cash_text, currency, "
            "row_sha256, last_seen_run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '0', ?, ?, ?, ?, ?)",
            (ACCOUNT, source_key, conid, DATES[1], DATES[1], side, quantity, proceeds, currency,
             proceeds, currency, f"row-{source_key}", run_id))
    connection.commit()


def v11_reference_file(folder: Path) -> bytes:
    """reference_data.json as v1.1's daily job builds it, from the fake providers."""

    database = folder / "v11.db"
    initialize(database)
    connection = connect(database)
    try:
        load_source_account(connection)
    finally:
        connection.close()
    cache = folder / "v11-reference_data.json"
    with patch("ibkr_etl.reference_data._get", side_effect=fake_get):
        refresh_reference_data(database, cache, workers=1)
    return cache.read_bytes()


def v2_stored_rows() -> tuple[list[dict], dict]:
    """The rows v2's sector and risk-free jobs store for the same provider responses."""

    classifications = []
    for holding in holdings():
        result = lookup_classification(holding, fetcher=yahoo_search, profile_fetcher=lambda _: {})
        classifications.append({**result, "provider": YAHOO_PROVIDER})
    latest = parse_treasury_3m_xml(TREASURY_XML)[-1]
    risk_free = {"rate_date": latest.rate_date, "series_id": SERIES_ID, "currency": "USD",
                 "tenor": "3-month", "annual_rate": latest.annual_rate, "provider": TREASURY_PROVIDER,
                 "fetched_at": "2026-10-14T23:00:05+00:00", "quality_status": "pass"}
    return classifications, risk_free


def v2_reference_data() -> dict:
    classifications, risk_free = v2_stored_rows()
    return {"version": 2, "sectors": {row["conid"]: sector_entry(row) for row in classifications},
            "risk_free": {"USD": risk_free_entry(risk_free)}}


class FakeTables:
    """Answers the two reference-table queries the way PostgreSQL would."""

    def __init__(self, classifications, risk_free):
        self.classifications, self.risk_free, self.statements = classifications, risk_free, []

    def execute(self, sql, parameters=()):
        self.statements.append(sql)
        if "instrument_classifications" in sql:
            rows = sorted(self.classifications, key=lambda row: row["conid"])
        else:
            rows = [self.risk_free] if self.risk_free else []
        return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)


class SourceSettingTests(unittest.TestCase):
    def test_blob_unless_database_is_named(self):
        self.assertEqual(reference_data_source({}), "blob")
        self.assertEqual(reference_data_source({"REFERENCE_DATA_SOURCE": " "}), "blob")
        self.assertEqual(reference_data_source({"REFERENCE_DATA_SOURCE": "blob"}), "blob")
        self.assertEqual(reference_data_source({"REFERENCE_DATA_SOURCE": "Database"}), "database")
        for value in ("db", "file", "tables"):
            with self.assertRaises(ValueError):
                reference_data_source({"REFERENCE_DATA_SOURCE": value})


class EquivalenceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.file_bytes = v11_reference_file(self.folder)
        self.file_data, _ = inspect_reference_bytes(self.file_bytes)
        self.database_data = v2_reference_data()

    def test_same_provider_answers_give_the_same_reference_data(self):
        sectors = compare_sectors(self.file_data["sectors"], self.database_data["sectors"])
        self.assertEqual((sectors["file_rows"], sectors["database_rows"], sectors["common_rows"]), (4, 4, 4))
        self.assertEqual((sectors["file_classified"], sectors["database_classified"]), (3, 3))
        self.assertEqual(sectors["only_in_file"]["count"] + sectors["only_in_database"]["count"], 0)
        self.assertEqual(sectors["field_differences"], {})
        self.assertEqual(sectors["file_countries"]["count"], 0)
        usd = self.database_data["risk_free"]["USD"]
        risk_free = compare_risk_free(self.file_data["risk_free"], self.database_data["risk_free"],
                                      database_rate_on_file_date=usd["annual_rate"])
        self.assertEqual(risk_free["differences"], [])
        self.assertEqual(self.file_data["risk_free"]["USD"]["observation_date"], "2026-10-14")
        self.assertEqual(usd["observation_date"], "2026-10-14")
        self.assertEqual(usd["annual_percent"], self.file_data["risk_free"]["USD"]["annual_percent"])

    def test_the_filtered_portfolio_is_identical_from_either_source(self):
        database = self.folder / "derived.db"
        initialize(database)
        connection = connect(database)
        self.addCleanup(connection.close)
        load_source_account(connection)
        inputs = filtered_portfolio_inputs(connection, definition())
        impact = exclusion_impact(self.file_data, self.database_data, definition=definition(), **inputs)
        self.assertEqual(impact["excluded_status_changes"]["count"], 0)
        self.assertEqual(impact["reason_text_changes"]["count"], 0)
        self.assertEqual((impact["position_conids_checked"], impact["activity_conids_checked"]), (4, 3))

        database_file = self.folder / "database-reference.json"
        database_file.write_bytes(reference_bytes(self.database_data))
        file_file = self.folder / "file-reference.json"
        file_file.write_bytes(self.file_bytes)
        tables = ("derived_portfolio_nav", "derived_portfolio_positions", "derived_portfolio_exclusions",
                  "derived_portfolio_cash_ledger", "derived_portfolio_cash_balances")
        outputs = []
        for path in (file_file, database_file):
            result = refresh_filtered_portfolio(connection, path, contract=definition(), generated_at=GENERATED_AT,
                                                expected_reference_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            rows = {table: sorted(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))
                    for table in tables}
            result.pop("reference_input")
            outputs.append((result, rows))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0][0]["latest_excluded_positions"], 1)
        self.assertTrue(outputs[0][1]["derived_portfolio_nav"])


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.database_data = v2_reference_data()

    def _impact(self, file_data):
        rows = [{"conid": conid, "listing_exchange": exchange, "currency": currency, "instrument_currency": currency}
                for conid, _, exchange, currency, _ in HOLDINGS]
        instruments = {row["conid"]: {**row, "symbol": ""} for row in rows}
        return exclusion_impact(file_data, self.database_data, definition=definition(), positions=rows,
                                activity_conids=["9100002", "9100003"], instruments=instruments)

    def test_a_file_country_the_database_lacks_is_reported_and_blocks_the_switch(self):
        file_data = json.loads(json.dumps(self.database_data))
        file_data["sectors"]["9100002"]["country"] = "Taiwan"
        sectors = compare_sectors(file_data["sectors"], self.database_data["sectors"])
        self.assertEqual(sectors["file_countries"]["examples"],
                         [{"conid": "9100002", "symbol": "AAPL", "country": "Taiwan"}])
        self.assertEqual(sectors["field_differences"]["country"]["count"], 1)
        impact = self._impact(file_data)
        self.assertEqual(impact["excluded_status_changes"]["count"], 2)
        self.assertEqual({row["rows"] for row in impact["excluded_status_changes"]["examples"]},
                         {"positions", "cash_and_trades"})

    def test_a_country_on_an_already_excluded_holding_changes_only_the_reason(self):
        file_data = json.loads(json.dumps(self.database_data))
        file_data["sectors"]["9100003"]["country"] = "Taiwan"
        impact = self._impact(file_data)
        self.assertEqual(impact["excluded_status_changes"]["count"], 0)
        self.assertEqual(impact["reason_text_changes"]["count"], 2)
        example = impact["reason_text_changes"]["examples"][0]
        self.assertEqual((example["file_reason"], example["database_reason"]),
                         ("reference country=Taiwan", "listing exchange=TWSE"))

    def test_missing_keys_and_value_differences_are_counted(self):
        file_sectors = json.loads(json.dumps(self.database_data["sectors"]))
        file_sectors.pop("9100004")
        file_sectors["9100009"] = {"symbol": "OLD", "sector": "Energy"}
        file_sectors["9100001"]["industry"] = "Something else"
        result = compare_sectors(file_sectors, self.database_data["sectors"], examples=1)
        self.assertEqual(result["only_in_file"], {"count": 1, "examples": ["9100009"]})
        self.assertEqual(result["only_in_database"], {"count": 1, "examples": ["9100004"]})
        self.assertEqual(result["field_differences"]["industry"]["examples"],
                         [{"conid": "9100001", "file": "Something else",
                           "database": "Internet Content & Information"}])
        self.assertEqual(set(result["field_differences"]), {"industry"})

    def test_risk_free_rates_are_compared_on_the_file_date_within_a_tolerance(self):
        file_rf = {"USD": {"observation_date": "2026-10-14", "annual_rate": 0.0402}}
        database_rf = {"USD": {"observation_date": "2026-10-14", "annual_rate": 0.0402}}
        close = compare_risk_free(file_rf, database_rf, database_rate_on_file_date=0.0402005)
        self.assertEqual(close["differences"], [])
        far = compare_risk_free(file_rf, database_rf, database_rate_on_file_date=0.0403)
        self.assertEqual(len(far["differences"]), 1)
        self.assertIn("differs by", far["differences"][0])
        newer = {"USD": {"observation_date": "2026-10-15", "annual_rate": 0.04}}
        later = compare_risk_free(file_rf, newer, database_rate_on_file_date=0.0402)
        self.assertEqual(later["differences"], ["latest observation dates differ: file 2026-10-14, database 2026-10-15"])
        missing = compare_risk_free(file_rf, {}, database_rate_on_file_date=None)
        self.assertEqual(len(missing["differences"]), 2)
        extra = compare_risk_free({**file_rf, "HKD": {}}, database_rf, database_rate_on_file_date=0.0402)
        self.assertEqual(len(extra["differences"]), 1)


class DatabaseBuildTests(unittest.TestCase):
    def test_build_is_deterministic_valid_and_fails_closed_when_empty(self):
        classifications, risk_free = v2_stored_rows()
        tables = FakeTables(classifications, risk_free)
        data = build_reference_data_from_database(tables, TENANT)
        self.assertEqual(data["sectors"], v2_reference_data()["sectors"])
        self.assertEqual(data["sectors"]["9100004"]["status"], "unclassified")
        self.assertFalse(data["sectors"]["9100001"]["country_checked"])
        self.assertEqual(reference_bytes(data), reference_bytes(build_reference_data_from_database(tables, TENANT)))
        _, manifest = inspect_reference_bytes(reference_bytes(data))
        self.assertEqual((manifest["classification_rows"], manifest["risk_free_rows"]), (4, 1))
        self.assertTrue(all("tenant_id = ?" in sql for sql in tables.statements))
        with self.assertRaises(RuntimeError):
            build_reference_data_from_database(FakeTables([], None), TENANT)

    def test_materialized_file_feeds_the_calculation_hash_check(self):
        classifications, risk_free = v2_stored_rows()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data" / "reference_data.json"
            receipt = materialize_database_reference(FakeTables(classifications, risk_free), TENANT, path)
            self.assertEqual(receipt["source"], "database")
            self.assertEqual(receipt["content_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(receipt["storage"]["content_sha256"], receipt["content_sha256"])
            self.assertEqual(receipt["storage"]["risk_free_observation_date"], "2026-10-14")
            self.assertEqual(receipt["classification_rows"], 4)


class WorkerSwitchTests(unittest.TestCase):
    """The derived worker reads the published file unless the switch says database."""

    CONTENT = b'{"version":2,"risk_free":{},"sectors":{}}'

    def _run_main(self, environment: dict):
        settings = SimpleNamespace(database_url="postgresql://unused", azure_storage_account_url="https://website",
                                   azure_report_container="reports", tenant_id=TENANT, database_path=Path("unused"))
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = [True]
        target = {"subject_id": "synthetic-non-taiwan", "subject_type": "filtered_portfolio",
                  "configuration_json": "{}", "configuration_sha256": "a" * 64, "definition_id": "d",
                  "effective_from": DATES[0]}
        receipt = {"source": "legacy-production", "content_sha256": hashlib.sha256(self.CONTENT).hexdigest()}
        database_receipt = {"source": "database", "content_sha256": "b" * 64}
        patches = [
            patch.dict(os.environ, environment),
            patch.object(worker, "load_settings", return_value=settings),
            patch.object(worker, "connect", return_value=connection),
            patch.object(worker, "refresh_alpha_world_market_data", return_value={}),
            patch.object(worker, "_dry_run_portfolio_cases", return_value=[]),
            patch.object(worker, "_record_agent_reconciliations", return_value={}),
            patch.object(worker, "_has_onboarding_cases", return_value=False),
            patch.object(worker, "_approved_targets", return_value=[target]),
            patch.object(worker, "validate_typed_runtime_contract",
                         return_value={"definition_version": 1, "calculation_kind": "k"}),
            patch.object(worker, "_start_run", return_value="run"),
            patch.object(worker, "_validate_and_complete", return_value={}),
            patch.object(worker, "_refresh_analytics_if_approved", return_value=None),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        calls = {}
        with patch.object(worker, "_download_reference_data", return_value=receipt) as download, \
                patch.object(worker, "materialize_database_reference", return_value=database_receipt) as build, \
                patch.object(worker, "refresh_filtered_portfolio", return_value={}) as calculate, \
                patch("builtins.print"):
            calls["exit"] = worker.main()
        calls.update(download=download, build=build, calculate=calculate, connection=connection)
        return calls

    def test_default_reads_the_published_file_exactly_as_before(self):
        environment = {key: value for key, value in os.environ.items() if key != "REFERENCE_DATA_SOURCE"}
        with patch.dict(os.environ, environment, clear=True):
            calls = self._run_main({})
        self.assertEqual(calls["exit"], 0)
        calls["build"].assert_not_called()
        calls["download"].assert_called_once()
        self.assertEqual(calls["download"].call_args.args[1], worker.PROJECT_ROOT / "data" / "reference_data.json")
        self.assertEqual(calls["calculate"].call_args.kwargs["expected_reference_sha256"],
                         hashlib.sha256(self.CONTENT).hexdigest())

    def test_blob_setting_is_the_default(self):
        calls = self._run_main({"REFERENCE_DATA_SOURCE": "blob"})
        calls["build"].assert_not_called()
        calls["download"].assert_called_once()

    def test_database_setting_builds_from_tables_and_never_reads_the_blob(self):
        calls = self._run_main({"REFERENCE_DATA_SOURCE": "database"})
        calls["download"].assert_not_called()
        calls["build"].assert_called_once_with(
            calls["connection"], TENANT, worker.PROJECT_ROOT / "data" / "reference_data.json")
        self.assertEqual(calls["calculate"].call_args.kwargs["expected_reference_sha256"], "b" * 64)

    def test_an_unknown_setting_stops_before_the_database_is_touched(self):
        with patch.dict(os.environ, {"REFERENCE_DATA_SOURCE": "tables"}), \
                patch.object(worker, "load_settings", return_value=SimpleNamespace(
                    database_url="postgresql://unused", azure_storage_account_url="https://website")), \
                patch.object(worker, "connect") as connect_database, self.assertRaises(ValueError):
            worker.main()
        connect_database.assert_not_called()

    def test_load_reference_input_defaults_to_the_blob_download(self):
        settings = SimpleNamespace(azure_storage_account_url="https://website", azure_report_container="reports",
                                   tenant_id="tenant")
        metadata = {"content_sha256": hashlib.sha256(self.CONTENT).hexdigest(), "version_id": "v", "etag": "e"}
        connection = Mock()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(worker, "download_blob_snapshot", return_value=(self.CONTENT, metadata)):
            receipt = worker._load_reference_input(settings, connection, Path(folder) / "ref.json")
        self.assertEqual((receipt["source"], receipt["storage"]), ("website", metadata))
        connection.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
