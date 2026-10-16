"""The export agent: plan validation, building, checks, packaging, the website routes and the agent side."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import unittest
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ibkr_etl import export_agent as xa  # noqa: E402

FACTS = xa.planner_facts(account_id="U1", display_name="Test account", subject_type="physical_account",
                         reporting_currency="USD", history_start_date="2025-01-20", latest_date="2026-10-14",
                         transaction_types=["Dividends", "Withholding Tax", "Deposits/Withdrawals", "Other Fees"],
                         currencies=["USD", "HKD"], today="2026-10-16")

CASH_ROWS = [
    {"effective_date": "2026-03-02", "event_datetime": "2026-03-02", "transaction_type": "Dividends",
     "description": "AAPL cash dividend", "amount_text": "120.50", "currency": "USD", "conid": "265598"},
    {"effective_date": "2026-03-02", "event_datetime": "2026-03-02", "transaction_type": "Withholding Tax",
     "description": "=HYPERLINK(\"x\")", "amount_text": "-36.15", "currency": "USD", "conid": "265598"},
    {"effective_date": "2026-05-10", "event_datetime": "2026-05-10", "transaction_type": "Dividends",
     "description": "0700 cash dividend", "amount_text": "400", "currency": "HKD", "conid": "41020"},
    {"effective_date": "2026-06-01", "event_datetime": "2026-06-01", "transaction_type": "Deposits/Withdrawals",
     "description": "Deposit", "amount_text": "5000", "currency": "USD", "conid": None},
    {"effective_date": "2025-12-31", "event_datetime": "2025-12-31", "transaction_type": "Dividends",
     "description": "Last year", "amount_text": "10", "currency": "USD", "conid": "265598"},
]
HOLDING_ROWS = [
    {"snapshot_date": "2026-09-30", "conid": "265598", "symbol": "AAPL", "description": "APPLE INC", "currency": "USD",
     "quantity_text": "10", "mark_price_text": "250", "market_value_text": "2500"},
    {"snapshot_date": "2026-09-30", "conid": "41020", "symbol": "700", "description": "TENCENT", "currency": "HKD",
     "quantity_text": "100", "mark_price_text": "390", "market_value_text": "39000"},
]
NAV_ROWS = [{"snapshot_date": d, "currency": "USD", "total_text": v}
            for d, v in (("2026-09-28", "10000"), ("2026-09-29", "10100"), ("2026-09-30", "10050"))]
TRADE_ROWS = [
    {"trade_date": "2026-08-03", "trade_time": "10:00:00", "settle_date": "2026-08-05", "execution_id": "E1",
     "trade_id": "1", "conid": "265598", "symbol": "AAPL", "description": "APPLE INC", "asset_category": "STK",
     "buy_sell": "BUY", "quantity_text": "10", "trade_price_text": "200", "currency": "USD", "proceeds_text": "-2000",
     "commission_text": "-1", "commission_currency": "USD", "net_cash_text": "-2001", "realized_pnl_text": "0"},
]


def fetch_from(rows_by_dataset):
    def fetch(item):
        return [], [dict(r) for r in rows_by_dataset[item["dataset"]]]
    return fetch


def books(nav=Decimal("10050"), holdings=None, roll=None):
    return xa.Books(
        nav_on=lambda day: nav,
        holdings_value=lambda day: holdings if holdings is not None else {
            "nav": Decimal("10050"), "positions": Decimal("7500"), "cash": Decimal("2540"), "currency": "USD", "date": day},
        cash_roll=lambda start, end: roll if roll is not None else {"reason": "No cash balances in this test."})


def plan_of(*datasets, title="Test export"):
    plan, adjustments = xa.validate_plan({"title": title, "summary": "", "unsupported": "", "refusal": "",
                                          "datasets": list(datasets)}, FACTS)
    return plan, adjustments


def item(dataset, **kwargs):
    return {"dataset": dataset, "from_date": "", "to_date": "", "columns": [], "transaction_types": [],
            "currencies": [], "symbol": "", "side": "", "group_by": [], **kwargs}


class PlanValidationTests(unittest.TestCase):
    def test_plan_is_canonical_and_dates_move_to_the_history(self):
        plan, adjustments = plan_of(item("cash-activity", from_date="2024-01-01", to_date="2026-12-31",
                                         transaction_types=["dividends", "WITHHOLDING TAX"],
                                         group_by=["currency", "transaction_type"]))
        d = plan["datasets"][0]
        self.assertEqual((d["from_date"], d["to_date"]), ("2025-01-20", "2026-10-14"))
        self.assertEqual(d["transaction_types"], ["Dividends", "Withholding Tax"])
        self.assertEqual(d["group_by"], ["transaction_type", "currency"])  # the dataset's own order
        self.assertEqual(d["columns"], list(xa.SPECS["cash-activity"].columns))
        self.assertEqual(len(adjustments), 2)

    def test_holdings_are_a_snapshot(self):
        plan, _ = plan_of(item("holdings", from_date="2026-01-01", to_date="2026-09-30"))
        self.assertIsNone(plan["datasets"][0]["from_date"])

    def test_anything_unknown_is_refused_not_guessed(self):
        for bad, message in (
                (item("cash-activity", transaction_types=["Interest"]), "no cash activity of type Interest"),
                (item("trades", columns=["secret"]), "no column called secret"),
                (item("performance", group_by=["currency"]), "can't be totalled"),
                (item("holdings", side="BUY"), "Only trades"),
                (item("performance", currencies=["USD"]), "can't be filtered by currency"),
                (item("cash-activity", from_date="2026-05-01", to_date="2026-04-01"), "after the end date"),
                (item("cash-activity", from_date="1 May"), "YYYY-MM-DD"),
                (item("ledger"), "no dataset")):
            with self.subTest(message=message), self.assertRaisesRegex(xa.PlanError, message):
                plan_of(bad)

    def test_refusal_and_limits(self):
        with self.assertRaisesRegex(xa.PlanError, "I can prepare"):
            xa.validate_plan({"refusal": "I can prepare exports only.", "datasets": []}, FACTS)
        with self.assertRaisesRegex(xa.PlanError, "at most 4"):
            plan_of(*[item("cash-activity", from_date=f"2026-0{m}-01") for m in range(1, 6)])

    def test_composites_have_no_trades(self):
        facts = {**FACTS, "subject_type": "composite_portfolio"}
        with self.assertRaisesRegex(xa.PlanError, "no trades of its own"):
            xa.validate_plan({"datasets": [item("trades")]}, facts)

    def test_description_is_written_by_code(self):
        plan, _ = plan_of(item("trades", from_date="2026-07-01", to_date="2026-09-30", side="SELL", symbol="AAPL",
                               group_by=["symbol"]))
        self.assertEqual(xa.describe(plan["datasets"][0], "physical_account"),
                         "Trades from 2026-07-01 to 2026-09-30; symbols containing AAPL; sells only; "
                         "with totals by symbol.")

    def test_pack_plans_take_the_period(self):
        plan, _ = plan_of(item("cash-activity", from_date="2026-01-01"), item("holdings"))
        dated = xa.dated_plan(plan, "2026-09-01", "2026-09-30")
        self.assertEqual([(d["from_date"], d["to_date"]) for d in dated["datasets"]],
                         [("2026-09-01", "2026-09-30"), (None, "2026-09-30")])


class BuildAndCheckTests(unittest.TestCase):
    def _build(self, *datasets, rows=None, book=None):
        plan, adjustments = plan_of(*datasets)
        built = xa.build(plan, fetch_from(rows or {"cash-activity": CASH_ROWS, "holdings": HOLDING_ROWS,
                                                   "performance": NAV_ROWS, "trades": TRADE_ROWS}), "physical_account")
        checks = xa.run_checks(built, book or books(), "physical_account", adjustments)
        return plan, built, checks

    def test_dividend_export_with_totals_passes_and_reads_back(self):
        plan, built, checks = self._build(item("cash-activity", from_date="2026-01-01", to_date="2026-09-30",
                                               transaction_types=["Dividends", "Withholding Tax"],
                                               group_by=["currency", "transaction_type"]))
        self.assertEqual(xa.overall(checks), "pass", checks)
        dataset = built[0]
        self.assertEqual(len(dataset.rows), 3)  # last year's dividend and the deposit are left out
        totals = {(r["currency"], r["transaction_type"]): r["total"] for r in dataset.summary_rows}
        self.assertEqual(totals, {("HKD", "Dividends"): "400", ("USD", "Dividends"): "120.50",
                                  ("USD", "Withholding Tax"): "-36.15"})
        table = list(csv.reader(io.StringIO(dataset.data.decode("utf-8-sig"))))
        amounts = [r[table[0].index("amount_text")] for r in table[1:]]
        descriptions = [r[table[0].index("description")] for r in table[1:]]
        self.assertIn("-36.15", amounts)  # negative numbers stay numbers
        self.assertIn("'=HYPERLINK(\"x\")", descriptions)  # formulas don't run
        self.assertTrue(dataset.data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(checks[-1]["id"], "cash_roll_1")
        self.assertEqual(checks[-1]["status"], "not_checked")  # only some kinds of cash activity

    def test_totals_never_mix_currencies(self):
        _, built, _ = self._build(item("cash-activity", from_date="2026-01-01", group_by=["transaction_type"]))
        dividends = [r for r in built[0].summary_rows if r["transaction_type"] == "Dividends"]
        self.assertEqual(sorted((r["currency"], r["total"]) for r in dividends), [("HKD", "400"), ("USD", "120.50")])

    def test_trade_commission_is_totalled_in_its_own_currency(self):
        _, built, checks = self._build(item("trades", from_date="2026-01-01", group_by=["symbol"]))
        measures = {(r["measure"], r["currency"]): r["total"] for r in built[0].summary_rows}
        self.assertEqual(measures[("commission", "USD")], "-1")
        self.assertEqual(measures[("net cash", "USD")], "-2001")
        self.assertEqual(xa.overall(checks), "pass")

    def test_a_tampered_totals_file_fails(self):
        plan, _ = plan_of(item("cash-activity", group_by=["currency"]))
        built = xa.build(plan, fetch_from({"cash-activity": CASH_ROWS}), "physical_account")
        self.assertIn(b"HKD,amount,HKD,1,400\r\n", built[0].summary)
        built[0].summary = built[0].summary.replace(b"HKD,amount,HKD,1,400", b"HKD,amount,HKD,1,401")
        checks = xa.file_checks(built[0])
        self.assertEqual(next(c for c in checks if c["id"] == "totals_tie_1")["status"], "fail")

    def test_a_file_that_does_not_read_back_fails(self):
        plan, _ = plan_of(item("cash-activity"))
        built = xa.build(plan, fetch_from({"cash-activity": CASH_ROWS}), "physical_account")
        built[0].data = built[0].data.replace(b"Deposit", b"Withdrawal")
        self.assertEqual(xa.file_checks(built[0])[0]["status"], "fail")

    def test_duplicate_executions_fail(self):
        _, _, checks = self._build(item("trades"), rows={"trades": TRADE_ROWS * 2})
        self.assertEqual(next(c for c in checks if c["id"] == "no_duplicates_1")["figures"]["duplicates"], 1)
        self.assertEqual(xa.overall(checks), "fail")

    def test_unreadable_amounts_fail(self):
        bad = [{**CASH_ROWS[0], "amount_text": "n/a"}]
        _, _, checks = self._build(item("cash-activity", group_by=["currency"]), rows={"cash-activity": bad})
        self.assertEqual(xa.overall(checks), "fail")

    def test_empty_file_needs_review(self):
        _, _, checks = self._build(item("cash-activity", currencies=["EUR"]))
        self.assertEqual(xa.overall(checks), "review")

    def test_value_history_ties_to_the_books_and_flags_gaps(self):
        _, _, checks = self._build(item("performance", from_date="2026-09-01"))
        self.assertEqual(xa.overall(checks), "pass")
        _, _, checks = self._build(item("performance", from_date="2026-09-01"), book=books(nav=Decimal("10051")))
        self.assertEqual(next(c for c in checks if c["id"] == "nav_matches_books_1")["status"], "fail")
        gappy = [NAV_ROWS[0], {**NAV_ROWS[2], "snapshot_date": "2026-10-14"}]
        _, _, checks = self._build(item("performance", from_date="2026-09-01"), rows={"performance": gappy})
        gap = next(c for c in checks if c["id"] == "nav_gaps_1")
        self.assertEqual((gap["status"], gap["figures"]["longest_gap_days"]), ("review", 16))

    def test_holdings_tie_to_the_account_value(self):
        _, built, checks = self._build(item("holdings", to_date="2026-09-30"))
        tie = next(c for c in checks if c["id"] == "holdings_tie_1")
        self.assertEqual((tie["status"], tie["figures"]["difference"]), ("pass", "-10"))
        self.assertEqual(built[0].snapshot_date, "2026-09-30")
        self.assertEqual([r["symbol"] for r in built[0].rows], ["700", "AAPL"])  # largest value first
        far = {"nav": Decimal("10050"), "positions": Decimal("5000"), "cash": Decimal("2540"), "currency": "USD",
               "date": "2026-09-30"}
        _, _, checks = self._build(item("holdings"), book=books(holdings=far))
        self.assertEqual(xa.overall(checks), "review")
        _, _, checks = self._build(item("holdings"), book=books(holdings={"reason": "No FX."}))
        self.assertEqual(next(c for c in checks if c["id"] == "holdings_tie_1")["status"], "not_checked")
        _, _, checks = self._build(item("holdings", currencies=["USD"]))
        self.assertEqual(next(c for c in checks if c["id"] == "holdings_tie_1")["status"], "not_checked")

    def test_cash_roll_forward_is_shown_not_held(self):
        roll = {"USD": {"opening": Decimal("100"), "activity": Decimal("5084.35"), "trades": Decimal("-2001"),
                        "closing": Decimal("3183.35")},
                "HKD": {"opening": Decimal("0"), "activity": Decimal("400"), "trades": Decimal("0"),
                        "closing": Decimal("1400")}}
        _, _, checks = self._build(item("cash-activity", from_date="2026-01-01"), book=books(roll=roll))
        cash = next(c for c in checks if c["id"] == "cash_roll_1")
        self.assertEqual(cash["status"], "info")
        self.assertIn("HKD", cash["detail"])
        self.assertNotIn("USD", cash["detail"])
        self.assertEqual(xa.overall(checks), "pass")

    def test_fingerprint_ignores_source_order_and_follows_the_data(self):
        plan, _ = plan_of(item("cash-activity", group_by=["currency"]), item("holdings"))
        first = xa.build(plan, fetch_from({"cash-activity": CASH_ROWS, "holdings": HOLDING_ROWS}), "physical_account")
        again = xa.build(plan, fetch_from({"cash-activity": CASH_ROWS[::-1], "holdings": HOLDING_ROWS[::-1]}),
                         "physical_account")
        self.assertEqual(xa.fingerprint(first), xa.fingerprint(again))
        changed = [dict(CASH_ROWS[0], amount_text="121")] + CASH_ROWS[1:]
        later = xa.build(plan, fetch_from({"cash-activity": changed, "holdings": HOLDING_ROWS}), "physical_account")
        self.assertNotEqual(xa.fingerprint(first), xa.fingerprint(later))

    def test_package_holds_readme_manifest_and_files(self):
        plan, built, checks = self._build(item("cash-activity", group_by=["currency"]))
        content = xa.package(built=built, plan=plan, checks=checks, status="pass", note="All good [T1].",
                             note_checked=True, check_id="c1", account_id="U1", account_name="Test account",
                             created_at="2026-10-16T01:00:00+00:00", released_by_person=False,
                             subject_type="physical_account")
        archive = zipfile.ZipFile(io.BytesIO(content))
        names = archive.namelist()
        self.assertEqual(names[:2], ["README.txt", "manifest.json"])
        manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["fingerprint"], xa.fingerprint(built))
        for entry in manifest["files"]:
            self.assertEqual(hashlib.sha256(archive.read(entry["name"])).hexdigest(), entry["sha256"])
        readme = archive.read("README.txt").decode()
        self.assertIn("Result: PASS", readme)
        self.assertIn("not advice", readme)

    def test_fallback_note_names_what_needs_attention(self):
        plan, built, checks = self._build(item("cash-activity", currencies=["EUR"]))
        facts = xa.check_facts(account_id="U1", plan=plan, built=built, checks=checks,
                               subject_type="physical_account", reporting_currency="USD")
        self.assertIn("has rows", xa.fallback_note(facts))


class PeriodTests(unittest.TestCase):
    def test_last_closed_period(self):
        self.assertEqual(xa.period_for("monthly", "2026-10-14"), ("2026-09-01", "2026-09-30", "September 2026"))
        self.assertEqual(xa.period_for("monthly", "2026-09-30"), ("2026-09-01", "2026-09-30", "September 2026"))
        self.assertEqual(xa.period_for("quarterly", "2026-10-14"), ("2026-07-01", "2026-09-30", "Q3 2026"))
        self.assertEqual(xa.period_for("quarterly", "2026-12-31"), ("2026-10-01", "2026-12-31", "Q4 2026"))
        self.assertEqual(xa.period_for("quarterly", "2026-02-01"), ("2025-10-01", "2025-12-31", "Q4 2025"))
        self.assertEqual(xa.period_for("yearly", "2026-10-14"), ("2025-01-01", "2025-12-31", "2025"))


# Website routes ----------------------------------------------------------------------------------

USER = {"object_id": "22222222-2222-2222-2222-222222222222", "email": "v@example.com", "role": "owner"}


class FakeSource:
    subject_type, currency, display_name = "physical_account", "USD", "Test account"

    def __init__(self, rows=None, book=None):
        self.rows = rows or {"cash-activity": CASH_ROWS, "holdings": HOLDING_ROWS, "performance": NAV_ROWS,
                             "trades": TRADE_ROWS}
        self.book = book or books()

    def facts(self, today):
        return FACTS

    def fetch(self, item):
        return fetch_from(self.rows)(item)

    def books(self):
        return self.book


class FakeApi:
    def __init__(self):
        self.calls, self.plan, self.records, self.inbox = [], None, {}, []

    def request(self, method, path, timeout=30.0, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/v1/exports/plan":
            return {"plan": self.plan}
        if path == "/v1/exports/checks":
            body = kwargs["json"]
            self.records["c1"] = {"subject_id": body["account_id"], "plan": body["plan"], "status": body["status"],
                                  "fingerprint": body["fingerprint"], "note": "", "note_checked": False,
                                  "checks": body["facts"]["checks"], "pack_item_id": body["pack_item_id"],
                                  "created_at": "2026-10-16T01:00:00+00:00", "released_at": None}
            return {"check_id": "c1", "status": body["status"], "note": "", "note_checked": False, "concerns": []}
        if path.startswith("/v1/exports/checks/") and method == "GET":
            return self.records[path.rsplit("/", 1)[1]]
        if path == "/v1/inbox":
            return {"items": self.inbox}
        if path.startswith("/v1/exports/packs/"):
            return {"plan": self.plan}
        return {"ok": True}


class WebRouteTests(unittest.TestCase):
    def setUp(self):
        from ibkr_etl import export_agent_web

        self.api, self.source = FakeApi(), FakeSource()
        app = FastAPI()
        export_agent_web.install(app, lambda: USER, lambda user: ["U1"], self.api, lambda user, account: self.source)
        self.client = TestClient(app)

    def test_plan_is_validated_and_described(self):
        self.api.plan = {"title": "Dividends", "summary": "s", "unsupported": "", "refusal": "",
                         "datasets": [item("cash-activity", transaction_types=["Dividends"], group_by=["currency"])]}
        body = self.client.post("/api/agents/exports/plan", json={"account_id": "U1", "request": "dividends"}).json()
        self.assertTrue(body["ok"])
        self.assertIn("types Dividends", body["lines"][0])
        sent = self.api.calls[0][2]["json"]
        self.assertEqual(sent["facts"]["cash_transaction_types"][0], "Deposits/Withdrawals")
        self.api.plan = {"refusal": "", "datasets": [item("cash-activity", transaction_types=["Bitcoin"])]}
        body = self.client.post("/api/agents/exports/plan", json={"account_id": "U1", "request": "x"}).json()
        self.assertFalse(body["ok"])

    def test_other_accounts_are_refused(self):
        response = self.client.post("/api/agents/exports/plan", json={"account_id": "U2", "request": "x"})
        self.assertEqual(response.status_code, 403)

    def _build(self, plan_item):
        plan, _ = plan_of(plan_item)
        return self.client.post("/api/agents/exports/build", json={"account_id": "U1", "plan": plan}).json()

    def test_build_then_download_the_checked_files(self):
        body = self._build(item("cash-activity", group_by=["currency"]))
        self.assertEqual(body["status"], "pass")
        self.assertIn("checks passed", body["note"])  # the code-written note while the checker gives none
        self.assertEqual(body["files"][0]["rows"], 5)
        response = self.client.get("/api/agents/exports/c1/download")
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertIn("summary/01_cash-activity_totals.csv", archive.namelist())
        self.assertIn(("POST", "/v1/exports/checks/c1/release"), [(m, p) for m, p, _ in self.api.calls])

    def test_failed_exports_cannot_be_downloaded(self):
        self.source.rows = {"trades": TRADE_ROWS * 2}
        self.assertEqual(self._build(item("trades"))["status"], "fail")
        self.assertEqual(self.client.get("/api/agents/exports/c1/download?release=true").status_code, 409)

    def test_review_needs_a_release(self):
        self.source.rows = {"cash-activity": []}
        self.assertEqual(self._build(item("cash-activity"))["status"], "review")
        self.assertEqual(self.client.get("/api/agents/exports/c1/download").status_code, 409)
        released = self.client.get("/api/agents/exports/c1/download?release=true")
        self.assertEqual(released.status_code, 200)
        self.assertIn(b"released by the person", zipfile.ZipFile(io.BytesIO(released.content)).read("README.txt"))

    def test_data_that_changed_after_the_check_is_not_served(self):
        self._build(item("cash-activity"))
        self.source.rows = {"cash-activity": CASH_ROWS[1:]}
        response = self.client.get("/api/agents/exports/c1/download")
        self.assertEqual(response.status_code, 409)
        self.assertIn("changed", response.json()["detail"])

    def test_browser_plans_are_validated_again(self):
        response = self.client.post("/api/agents/exports/build", json={
            "account_id": "U1", "plan": {"datasets": [item("cash-activity", columns=["password"])]}})
        self.assertEqual(response.status_code, 422)

    def test_pack_card_builds_its_period(self):
        plan, _ = plan_of(item("cash-activity", group_by=["currency"]))
        self.api.plan = plan
        self.api.inbox = [{"item_id": 7, "kind": "export_pack", "subject_id": "U1",
                           "citations": [{"export_pack": "p1", "from_date": "2026-03-01", "to_date": "2026-03-31"}]}]
        body = self.client.post("/api/agents/exports/build", json={"account_id": "U1", "pack_item_id": 7}).json()
        self.assertEqual((body["plan"]["datasets"][0]["from_date"], body["plan"]["datasets"][0]["to_date"]),
                         ("2026-03-01", "2026-03-31"))
        self.assertEqual(body["files"][0]["rows"], 2)
        self.assertEqual(self.client.get("/api/agents/exports/c1/download").status_code, 200)
        self.assertIn(("POST", "/v1/inbox/7/decision"), [(m, p) for m, p, _ in self.api.calls])
        missing = self.client.post("/api/agents/exports/build", json={"account_id": "U1", "pack_item_id": 8})
        self.assertEqual(missing.status_code, 404)


# Agent side ------------------------------------------------------------------------------------

def _final(payload):
    message = SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")],
                              model_dump=lambda **_: {"type": "message", "role": "assistant", "content": []})
    return SimpleNamespace(status="completed", output=[message], output_text=json.dumps(payload),
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def _call(name):
    call = SimpleNamespace(type="function_call", name=name, arguments="{}", call_id="c1",
                           model_dump=lambda **_: {"type": "function_call", "name": name, "arguments": "{}",
                                                   "call_id": "c1"})
    return SimpleNamespace(status="completed", output=[call], output_text="",
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


class FakeModel:
    def __init__(self, responses):
        self.responses = SimpleNamespace(create=self._create)
        self.queue, self.requests = list(responses), []

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.queue.pop(0)


CHECK_FACTS = {"title": "Dividends", "status": "pass", "files": [{"file": "data/01.csv", "rows": 3}],
               "checks": [{"id": "reads_back_1", "status": "pass", "detail": "ok", "figures": {"rows": 3}}]}


class AgentSideTests(unittest.TestCase):
    def _agent(self, responses):
        from wealthryse_agent.config import ModelSettings
        from wealthryse_agent.runtime import Agent

        self.model = FakeModel(responses)
        return Agent(ModelSettings(endpoint="https://example.invalid", deployment="analyst", router_deployment="",
                                   region="southeastasia", processing_scope="apac-data-zone",
                                   managed_identity_client_id="x", max_tool_calls=3), model_client=self.model)

    def test_planner_uses_a_strict_schema_and_sees_no_figures(self):
        from wealthryse_agent.exports import plan_export

        agent = self._agent([_final({"title": "t", "summary": "", "unsupported": "", "refusal": "",
                                     "datasets": [item("trades")]})])
        plan, turn = plan_export(agent, account_id="U1", request="all my trades", facts=FACTS)
        self.assertEqual(plan["datasets"][0]["dataset"], "trades")
        request = self.model.requests[0]
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertIn("Deposits/Withdrawals", request["instructions"])
        self.assertNotIn("tools", request)
        self.assertEqual(turn.usage["input_tokens"], 10)

    def test_checker_concerns_go_through_the_figure_check(self):
        from wealthryse_agent.exports import check_note

        good = {"answer": "The file holds 3 rows [T1].", "citations": ["T1"], "visuals": [], "remember": [],
                "concerns": []}
        note, concerns, ok, _ = check_note(self._agent([_call("get_export_check"), _final(good)]),
                                           account_id="U1", facts=CHECK_FACTS)
        self.assertEqual((note, concerns, ok), ("The file holds 3 rows [T1].", [], True))
        invented = {**good, "concerns": ["About 950 rows look missing [T1]."]}
        note, concerns, ok, _ = check_note(self._agent([_call("get_export_check"), _final(invented), _final(invented)]),
                                           account_id="U1", facts=CHECK_FACTS)
        self.assertFalse(ok)
        self.assertEqual((note, concerns), ("", []))

    def _server(self, store, responses=()):
        from wealthryse_agent.agent_server import Services, create_app
        from wealthryse_agent.config import RuntimeSettings, ServiceAuthSettings

        class Tools:
            def call(self, name, arguments):
                return {"data": {"accounts": [{"account_id": "U1"}]}}

            def close(self):
                pass

        agent = self._agent(list(responses))
        services = Services(auth=ServiceAuthSettings("", "", frozenset(), disabled_for_local_tests=True),
                            runtime=RuntimeSettings("", "", "", 100000, 90), tenant_id="t",
                            store_factory=lambda: store, agent_factory=lambda: agent, tools_factory=lambda u, c: Tools())
        services._store = store
        return TestClient(create_app(services))

    def test_a_concern_turns_a_pass_into_a_review_and_fail_stays_fail(self):
        store = FakeStore()
        concern = {"answer": "The file holds 3 rows [T1].", "citations": ["T1"], "visuals": [], "remember": [],
                   "concerns": ["Only 3 rows for a whole year [T1]."]}
        client = self._server(store, [_call("get_export_check"), _final(concern)])
        body = {"user_object_id": USER["object_id"], "account_id": "U1", "plan": {"datasets": []},
                "fingerprint": "a" * 64, "status": "pass", "facts": CHECK_FACTS}
        result = client.post("/v1/exports/checks", json=body).json()
        self.assertEqual(result["status"], "review")
        self.assertEqual(store.checks[0]["status"], "review")
        self.assertEqual([r[0] for r in store.runs], ["export_checker"])
        client = self._server(store, [_call("get_export_check"), _final(concern)])
        self.assertEqual(client.post("/v1/exports/checks", json={**body, "status": "fail"}).json()["status"], "fail")
        denied = client.post("/v1/exports/checks", json={**body, "account_id": "U9"})
        self.assertEqual(denied.status_code, 403)

    def test_checker_failure_still_records_the_checks(self):
        store = FakeStore()
        client = self._server(store, [])  # the model has nothing to say: the call fails
        body = {"user_object_id": USER["object_id"], "account_id": "U1", "plan": {"datasets": []},
                "fingerprint": "a" * 64, "status": "pass", "facts": CHECK_FACTS}
        with self.assertLogs("wealthryse_agent.runtime", "ERROR"):
            result = client.post("/v1/exports/checks", json=body).json()
        self.assertEqual((result["status"], result["note"], result["note_checked"]), ("pass", "", False))

    def test_pack_cards_show_only_to_their_owner(self):
        store = FakeStore()
        owner = hashlib.sha256(USER["object_id"].encode()).hexdigest()
        store.items = [{"item_id": 1, "kind": "export_pack", "subject_id": "U1", "citations": [{"owner_sha256": owner}]},
                       {"item_id": 2, "kind": "export_pack", "subject_id": "U1", "citations": [{"owner_sha256": "x"}]},
                       {"item_id": 3, "kind": "weekly_brief", "subject_id": "U1", "citations": []}]
        client = self._server(store)
        items = client.get("/v1/inbox", params={"user_object_id": USER["object_id"]}).json()["items"]
        self.assertEqual([i["item_id"] for i in items], [1, 3])
        decision = {"user_object_id": USER["object_id"], "decision": "read"}
        self.assertEqual(client.post("/v1/inbox/2/decision", json=decision).status_code, 404)
        self.assertEqual(client.post("/v1/inbox/1/decision", json=decision).status_code, 200)
        check = {"user_object_id": USER["object_id"], "account_id": "U1", "plan": {}, "fingerprint": "a" * 64,
                 "status": "pass", "facts": CHECK_FACTS, "pack_item_id": 2}
        self.assertEqual(client.post("/v1/exports/checks", json=check).status_code, 404)


class FakeStore:
    def __init__(self):
        self.checks, self.runs, self.items = [], [], []

    def tokens_used_today(self):
        return 0

    def start_run(self, job, correlation_id):
        self.runs.append([job, None])
        return len(self.runs)

    def finish_run(self, run_id, **kwargs):
        self.runs[run_id - 1][1] = kwargs

    def add_export_check(self, **kwargs):
        self.checks.append(kwargs)
        return "c1"

    def inbox(self, accounts, status=None):
        return [i for i in self.items if i["subject_id"] in accounts]

    def decide(self, item_id, accounts, decision, digest):
        return any(i["item_id"] == item_id for i in self.items)


class ExportPacksJobTests(unittest.TestCase):
    def test_one_card_per_closed_period_for_the_owner(self):
        from wealthryse_agent.jobs import export_packs

        class Store:
            def __init__(self):
                self.cards = {}

            def active_export_packs(self):
                created = datetime(2026, 8, 20, tzinfo=timezone.utc)
                return [{"pack_id": "p1", "user_object_id": USER["object_id"], "subject_id": "U1", "name": "Tax pack",
                         "schedule": "monthly", "created_at": created},
                        {"pack_id": "p2", "user_object_id": USER["object_id"], "subject_id": "U9", "name": "Other",
                         "schedule": "monthly", "created_at": created},
                        {"pack_id": "p3", "user_object_id": USER["object_id"], "subject_id": "U1", "name": "New",
                         "schedule": "yearly", "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc)}]

            def add_inbox(self, **kwargs):
                key = (kwargs["kind"], kwargs["account_id"], kwargs["period_key"])
                if key in self.cards:
                    return False
                self.cards[key] = kwargs
                return True

        class Tools:
            def call(self, name, arguments):
                return {"data": {"latest_valuation_date": "2026-10-14"}}

        store = Store()
        self.assertEqual(export_packs(store, Tools(), ["U1"]), 2)
        self.assertEqual(export_packs(store, Tools(), ["U1"]), 0)
        cards = sorted(store.cards.values(), key=lambda c: c["period_key"])
        self.assertEqual([c["period_key"] for c in cards], ["p1:2026-09-30", "p3:2025-12-31"])
        self.assertEqual(cards[0]["title"], "Tax pack: September 2026 is ready to check")
        self.assertEqual(cards[0]["citations"][0]["owner_sha256"], hashlib.sha256(USER["object_id"].encode()).hexdigest())

    def test_packs_never_backfill_before_they_were_saved(self):
        from wealthryse_agent.jobs import export_packs

        class Store:
            cards = []

            def active_export_packs(self):
                return [{"pack_id": "p1", "user_object_id": USER["object_id"], "subject_id": "U1", "name": "Tax pack",
                         "schedule": "monthly", "created_at": "2026-10-15T00:00:00+00:00"}]

            def add_inbox(self, **kwargs):
                self.cards.append(kwargs)
                return True

        class Tools:
            def call(self, name, arguments):
                return {"data": {"latest_valuation_date": "2026-08-31"}}  # data is behind: August only

        self.assertEqual(export_packs(Store(), Tools(), ["U1"]), 0)


if __name__ == "__main__":
    unittest.main()
