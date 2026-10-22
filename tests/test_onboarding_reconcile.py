"""The reconciliation agent: a new IBKR account's stage 2, checked before approval 1.

No database, network or model: the account's data comes from fake tools and
the model is a fake. All figures are synthetic. The database side (the scoped
views and the platform recording the four checks) is in
test_onboarding_agent_review_db.py.
"""
from __future__ import annotations

import copy
import json
import os
import unittest
from datetime import date, timedelta
from types import SimpleNamespace

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from ibkr_etl.onboarding_agent_reconcile import check_records  # noqa: E402
from wealthryse_agent.config import ModelSettings  # noqa: E402
from wealthryse_agent.month_team.agents import TeamModel, TeamModelError  # noqa: E402
from wealthryse_agent.onboarding_reconcile import (  # noqa: E402
    ReconcileSettings, Reconciler, explanation_is_grounded, reconcile_waiting_cases, reconciliation_days)

CASE_ID = "aaaaaaaa-0000-4000-8000-0000000000e1"
APAC = ModelSettings(endpoint="https://wr-agents-sea.services.ai.azure.com", deployment="wr-reports-apac",
                     router_deployment="", region="southeastasia", processing_scope="apac-data-zone",
                     managed_identity_client_id="x")
TODAY = date(2025, 3, 3)
CASE = {"case_id": CASE_ID, "subject_id": "U0000901", "broker": "ibkr_flex", "broker_account_id": "U0000901",
        "display_name": "Synthetic broker account", "reporting_currency": "HKD", "history_start_date": "2025-01-02",
        "base_currency": "HKD"}
# Synthetic securities: one US stock already in the security master, one HK stock new to it.
SECURITIES = [
    {"conid": "265598", "first_held": "2025-01-02", "last_held": "2025-02-28", "position_currency": "USD",
     "symbol": "SYNA", "description": "SYNTHETIC A INC", "asset_category": "STK", "currency": "USD",
     "listing_exchange": "NASDAQ", "isin": "US0378331005"},
    {"conid": "1001", "first_held": "2025-01-02", "last_held": "2025-02-28", "position_currency": "HKD",
     "symbol": "9999", "description": "SYNTHETIC HK LTD", "asset_category": "STK", "currency": "HKD",
     "listing_exchange": "SEHK", "isin": None},
]
KNOWN = [{"scheme": "ibkr_conid", "identifier": "265598", "security_key": "265598"},
         {"scheme": "isin", "identifier": "US0378331005", "security_key": "265598"}]
# Every weekday from the start date to the end of February.
NAV_DAYS = [d.isoformat() for d in (date(2025, 1, 2) + timedelta(days=n) for n in range(58)) if d.weekday() < 5]


def day(on: str, *, usd_value="1000", hkd_value="5000", usd_cash="100", hkd_cash="2000", accruals="12",
        total=None, mark="10", fx="7.8", actions=(), activity=()):
    positions = (float(usd_value) * float(fx)) + float(hkd_value)
    cash = float(usd_cash) * float(fx) + float(hkd_cash)
    broker_total = total if total is not None else f"{positions + cash + float(accruals):.2f}"
    return {"date": on,
            "nav": [{"currency": "HKD", "asset_class": "TOTAL", "total_text": broker_total, "cash_text": f"{cash:.2f}",
                     "stock_text": f"{positions:.2f}", "options_text": None, "futures_text": None, "bonds_text": None,
                     "accruals_text": accruals}],
            "positions": [{"conid": "265598", "currency": "USD", "quantity_text": "100", "mark_price_text": mark,
                           "market_value_text": usd_value},
                          {"conid": "1001", "currency": "HKD", "quantity_text": "500", "mark_price_text": "10",
                           "market_value_text": hkd_value}],
            "cash": [{"currency": "BASE_SUMMARY", "ending_cash_text": "99999", "fx_rate_to_base_text": "1"},
                     {"currency": "HKD", "ending_cash_text": hkd_cash, "fx_rate_to_base_text": "1"},
                     {"currency": "USD", "ending_cash_text": usd_cash, "fx_rate_to_base_text": fx}],
            "fx_rates": [], "corporate_actions": list(actions), "activity": list(activity)}


class Account:
    """The tool server, for one synthetic account."""

    def __init__(self, nav_days=NAV_DAYS, securities=SECURITIES, identifiers=KNOWN, activity=None, **day_changes):
        self.overview = {"case": dict(CASE), "nav": [{"snapshot_date": d, "currency": "HKD", "total_text": "1"}
                                                      for d in nav_days],
                         "activity": activity if activity is not None else [
                             {"kind": "cash", "count": 3, "first": "2025-01-05", "last": "2025-02-20"},
                             {"kind": "trade", "count": 12, "first": "2025-01-02", "last": "2025-02-25"}],
                         "securities": copy.deepcopy(securities), "security_identifiers": list(identifiers)}
        self.changes = day_changes
        self.days: dict[str, dict] = {}
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "list_onboarding_broker_cases":
            return {"data": {"cases": [dict(CASE)]}}
        if name == "get_onboarding_broker_overview":
            return {"data": copy.deepcopy(self.overview)}
        if name == "get_onboarding_broker_day":
            on = arguments["date"]
            self.days.setdefault(on, day(on, **self.changes.get(on, {})))
            return {"data": copy.deepcopy(self.days[on])}
        raise AssertionError(name)


class FakeResponses:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def create(self, **request):
        self.calls.append(request)
        answer = self.answers.pop(0) if self.answers else {"cause": "not_found", "explanation": "Nothing explains it."}
        return SimpleNamespace(output_text=json.dumps(answer), status="completed",
                               usage=SimpleNamespace(input_tokens=50, output_tokens=5))


def model(*answers):
    client = SimpleNamespace(responses=FakeResponses(answers))
    return TeamModel(APAC, tokens_left=1_000_000, client=client), client.responses


def reconcile(account: Account, *answers, today=TODAY):
    team_model, responses = model(*answers)
    reconciler = Reconciler(lambda: team_model)
    overview, days = reconciler.gather(account.call, CASE_ID)
    return reconciler.reconcile(overview, days, today=today), responses


class CheckTests(unittest.TestCase):
    def test_a_clean_account_passes_its_checks_with_no_model_call(self):
        result, responses = reconcile(Account())
        self.assertEqual({k: v["status"] for k, v in result.checks.items()},
                         {"holdings_and_cash_loaded": "pass", "transactions_loaded": "pass",
                          "securities_mapped": "warn", "nav_reconciled": "pass"})
        self.assertEqual(responses.calls, [])
        nav = result.checks["nav_reconciled"]
        self.assertIn("matches IBKR's on all 2 day(s) checked, from 2025-01-31 to 2025-02-28", nav["message"])
        self.assertEqual([d["date"] for d in nav["evidence"]["days"]], ["2025-02-28", "2025-01-31"])
        self.assertIn("2 holding(s) and cash in 2 currency(ies) are loaded for 2025-02-28",
                      result.checks["holdings_and_cash_loaded"]["message"])
        self.assertIn("loaded from 2025-01-02 to 2025-02-28 with no gap, with 12 trade(s) and 3 cash transaction(s)",
                      result.checks["transactions_loaded"]["message"])
        # One security is new to the master, proposed with its identifiers; nothing conflicts.
        proposal, = result.proposals
        self.assertEqual((proposal["security_key"], proposal["identifiers"]),
                         ("1001", [{"scheme": "ibkr_conid", "identifier": "1001"}]))
        self.assertIn("1 of 2 securities are already in the security master. 1 are new",
                      result.checks["securities_mapped"]["message"])

    def test_old_holdings_warn(self):
        result, _ = reconcile(Account(), today=date(2025, 3, 10))
        self.assertEqual(result.checks["holdings_and_cash_loaded"]["status"], "warn")
        self.assertIn("newest day is 2025-02-28, 10 days ago", result.checks["holdings_and_cash_loaded"]["message"])

    def test_the_month_ends_and_the_latest_day_are_rebuilt(self):
        overview = Account().overview
        self.assertEqual(reconciliation_days(overview, ReconcileSettings()), ["2025-01-31", "2025-02-28"])
        self.assertEqual(reconciliation_days(overview, ReconcileSettings(month_ends=1)), ["2025-02-28"])

    def test_no_data_fails_with_what_to_do(self):
        result, _ = reconcile(Account(nav_days=[], securities=[], activity=[]))
        self.assertEqual({v["status"] for k, v in result.checks.items() if k != "securities_mapped"}, {"fail"})
        self.assertIn("not be in a scheduled Flex query", result.checks["holdings_and_cash_loaded"]["message"])

    def test_history_that_starts_late_fails_and_a_gap_warns(self):
        late, _ = reconcile(Account(nav_days=[d for d in NAV_DAYS if d >= "2025-01-31"]))
        self.assertEqual(late.checks["transactions_loaded"]["status"], "fail")
        self.assertIn("starts on 2025-01-02, but the loaded data starts on 2025-01-31",
                      late.checks["transactions_loaded"]["message"])
        # No NAV from 2025-01-13 to 2025-01-24: a gap of 13 days after 2025-01-10.
        gap, _ = reconcile(Account(nav_days=[d for d in NAV_DAYS if not "2025-01-13" <= d <= "2025-01-24"]))
        check = gap.checks["transactions_loaded"]
        self.assertEqual(check["status"], "warn")
        self.assertIn("1 gap(s) with no NAV: 16 days after 2025-01-10", check["message"])

    def test_no_transactions_is_a_warning(self):
        result, _ = reconcile(Account(activity=[]))
        self.assertEqual(result.checks["transactions_loaded"]["status"], "warn")
        self.assertIn("missing its Trades and Cash Transactions sections", result.checks["transactions_loaded"]["message"])

    def test_conflicting_identifiers_fail_and_name_the_security(self):
        conflicting = KNOWN + [{"scheme": "us_ticker", "identifier": "SYNA", "security_key": "US:SYNA"}]
        result, _ = reconcile(Account(identifiers=conflicting))
        check = result.checks["securities_mapped"]
        self.assertEqual(check["status"], "fail")
        self.assertIn("SYNA (Identifiers resolve to different securities: 265598, US:SYNA)", check["message"])

    def test_a_security_with_no_instrument_details_fails(self):
        securities = copy.deepcopy(SECURITIES)
        securities[1].update(symbol=None, description=None)
        result, _ = reconcile(Account(securities=securities))
        self.assertEqual(result.checks["securities_mapped"]["status"], "fail")
        self.assertIn("conid 1001", result.checks["securities_mapped"]["message"])

    def test_every_security_known_passes(self):
        known = KNOWN + [{"scheme": "ibkr_conid", "identifier": "1001", "security_key": "1001"}]
        result, _ = reconcile(Account(identifiers=known))
        self.assertEqual(result.checks["securities_mapped"]["status"], "pass")
        self.assertEqual(result.proposals, [])


class BreakTests(unittest.TestCase):
    def test_a_missing_price_is_found_and_explained_in_the_agents_grounded_words(self):
        # IBKR gave no mark for SYNA on 28 February; its NAV still counts the holding.
        account = Account(**{"2025-02-28": {"mark": ""}})
        answer = {"cause": "missing_price", "explanation": "On 2025-02-28 IBKR gave no price for conid 265598, so the "
                                                           "rebuilt NAV is -7,800.00 HKD short on the positions side."}
        result, responses = reconcile(account, answer)
        check = result.checks["nav_reconciled"]
        self.assertEqual(check["status"], "fail")
        broken = check["evidence"]["days"][0]
        self.assertEqual((broken["date"], broken["gap"], broken["side"], broken["cause"], broken["worded_by"]),
                         ("2025-02-28", "-7800.00", "positions", "missing_price", "agent"))
        self.assertIn("2025-02-28: -7,800.00 HKD", check["message"])
        self.assertIn("IBKR gave no price for conid 265598", check["message"])
        self.assertEqual(responses.calls[0]["model"], "wr-reports-apac")
        self.assertEqual(result.usage, {"input_tokens": 50, "output_tokens": 5})

    def test_an_invented_number_falls_back_to_the_platforms_own_wording(self):
        account = Account(**{"2025-02-28": {"mark": ""}})
        answer = {"cause": "missing_price", "explanation": "About 8,000 HKD is missing."}
        result, _ = reconcile(account, answer)
        broken = result.checks["nav_reconciled"]["evidence"]["days"][0]
        self.assertEqual(broken["worded_by"], "code")
        self.assertIn("IBKR gave no price on 2025-02-28 for conid 265598", broken["explanation"])

    def test_a_model_error_keeps_the_platforms_wording(self):
        account = Account(**{"2025-02-28": {"mark": ""}})
        team_model, _ = model()
        team_model.ask = lambda **_: (_ for _ in ()).throw(TeamModelError("model_rejected", "no"))
        reconciler = Reconciler(lambda: team_model)
        overview, days = reconciler.gather(account.call, CASE_ID)
        broken = reconciler.reconcile(overview, days, today=TODAY).checks["nav_reconciled"]["evidence"]["days"][0]
        self.assertEqual((broken["cause"], broken["worded_by"]), ("missing_price", "code"))

    def test_a_corporate_action_near_the_day_is_named(self):
        split = {"effective_date": "2025-02-27", "action_type": "FS", "description": "SYNA 2 FOR 1 SPLIT",
                 "conid": "265598", "quantity_text": "100", "amount_text": "0", "currency": "USD"}
        account = Account(**{"2025-02-28": {"total": "20000.00", "actions": [split]}})
        result, _ = reconcile(account, {"cause": "not_found", "explanation": ""})
        broken = result.checks["nav_reconciled"]["evidence"]["days"][0]
        self.assertEqual(broken["cause"], "corporate_action")
        self.assertIn("SYNA 2 FOR 1 SPLIT", broken["explanation"])

    def test_a_small_gap_that_matches_a_settlement_warns(self):
        # The broker's NAV is 50 HKD above the rebuild, and a 50 HKD fee settles that day.
        clean = day("2025-02-28")
        total = f"{float(clean['nav'][0]['total_text']) + 50:.2f}"
        fee = {"kind": "cash", "activity_date": "2025-02-27", "settle_date": "2025-02-28", "activity_type": "Fees",
               "conid": None, "amount_text": "-50", "currency": "HKD"}
        account = Account(**{"2025-02-28": {"total": total, "activity": [fee]}})
        result, _ = reconcile(account, {"cause": "settlement_timing", "explanation": "x"})
        check = result.checks["nav_reconciled"]
        self.assertEqual(check["status"], "warn")
        self.assertEqual(check["evidence"]["days"][0]["cause"], "settlement_timing")

    def test_numbers_in_an_explanation_must_be_facts(self):
        facts = {"gap": "-7,800.00 HKD", "date": "2025-02-28"}
        self.assertTrue(explanation_is_grounded("The gap of -7,800.00 HKD on 2025-02-28.", facts))
        self.assertFalse(explanation_is_grounded("The gap of 7,801 HKD.", facts))


class FakeStore:
    def __init__(self):
        self.saved = {}

    def done(self, case_id, digest):
        return (case_id, digest) in self.saved

    def save(self, case, digest, result, correlation_id):
        self.saved[(case["case_id"], digest)] = result


class JobTests(unittest.TestCase):
    def test_unchanged_data_is_reconciled_once_a_day(self):
        account, store, usage = Account(), FakeStore(), {}
        reconciler = Reconciler(lambda: model()[0])
        self.assertEqual(reconcile_waiting_cases(store, account.call, reconciler, usage, today=TODAY), 1)
        self.assertEqual(reconcile_waiting_cases(store, account.call, reconciler, usage, today=TODAY), 0)
        self.assertEqual(reconcile_waiting_cases(store, account.call, reconciler, usage, today=date(2025, 3, 4)), 1)
        account.days["2025-02-28"]["cash"][1]["ending_cash_text"] = "2001"
        self.assertEqual(reconcile_waiting_cases(store, account.call, reconciler, usage, today=date(2025, 3, 4)), 1)
        self.assertEqual(len(store.saved), 3)


class RecordTests(unittest.TestCase):
    def test_each_check_is_recorded_as_by_the_agent_with_its_evidence(self):
        result, _ = reconcile(Account())
        records = check_records({"reconciliation_id": 4, "created_at": "2025-03-03 08:00:00+00",
                                 "checks": json.dumps(result.checks, default=str),
                                 "proposals": json.dumps(result.proposals)})
        self.assertEqual(set(records), {"holdings_and_cash_loaded", "transactions_loaded", "securities_mapped",
                                        "nav_reconciled"})
        status, message, evidence = records["securities_mapped"]
        self.assertEqual((status, evidence["recorded_by"], evidence["reconciliation_id"]), ("warn", "agent", 4))
        self.assertEqual(evidence["proposals"][0]["security_key"], "1001")


if __name__ == "__main__":
    unittest.main()
