from __future__ import annotations

import unittest
import uuid
from datetime import date, timedelta

from ibkr_etl.broker_connections import (
    connection_health,
    display_account_id,
    ip_lock_state,
    new_connection_record,
    normalize_account_id,
    onboarding_v2_enabled,
    secret_name_for,
    validate_ip_lock,
)
from ibkr_etl.flex_sources import SECRET_NAME_PATTERN
from ibkr_etl.onboarding_cases import (
    OnboardingCase,
    advance,
    approve,
    assert_can_start,
    cancel,
    case_status,
    go_live_phrase,
    next_owner_action,
    progress,
    record_check,
    terms_digest,
)
from ibkr_etl.security_master import (
    SecurityConflict,
    SecurityIdentifier,
    SecurityMaster,
    ibkr_identifiers,
    isin_is_valid,
    new_security_key,
    unmapped_holdings,
)

TODAY = date(2026, 10, 13)


class SwitchTests(unittest.TestCase):
    def test_new_onboarding_is_off_unless_set_to_true(self) -> None:
        self.assertFalse(onboarding_v2_enabled({}))
        self.assertFalse(onboarding_v2_enabled({"ONBOARDING_V2_ENABLED": "yes"}))
        self.assertTrue(onboarding_v2_enabled({"ONBOARDING_V2_ENABLED": " TRUE "}))


class BrokerConnectionTests(unittest.TestCase):
    def test_account_numbers_are_checked_per_broker(self) -> None:
        self.assertEqual(normalize_account_id("ibkr_flex", " u16088571 "), "U16088571")
        self.assertEqual(normalize_account_id("kiwoom_rest", "5012-3456-10"), "5012345610")
        self.assertEqual(display_account_id("kiwoom_rest", "5012345610"), "5012-3456-10")
        self.assertEqual(display_account_id("kiwoom_rest", "50123456"), "5012-3456")
        for broker, value in (("ibkr_flex", "5012345610"), ("kiwoom_rest", "U16088571"), ("kiwoom_rest", "123")):
            with self.subTest(broker=broker, value=value), self.assertRaises(ValueError):
                normalize_account_id(broker, value)
        with self.assertRaisesRegex(ValueError, "Unsupported broker"):
            normalize_account_id("schwab", "123")

    def test_ibkr_connection_keeps_the_existing_flex_secret_name(self) -> None:
        connection_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        name = secret_name_for("ibkr_flex", connection_id)
        self.assertEqual(name, "ibkr-flex-source-12345678123456781234567812345678")
        self.assertRegex(name, SECRET_NAME_PATTERN)
        self.assertEqual(
            secret_name_for("kiwoom_rest", connection_id),
            "broker-kiwoom-rest-12345678123456781234567812345678",
        )

    def test_record_never_carries_the_credential(self) -> None:
        record = new_connection_record(
            broker="kiwoom_rest",
            display_name="  Kiwoom   Korea ",
            environment="mock",
            credentials={"app_key": "synthetic-key", "app_secret": "synthetic-secret"},
            expires_on=None,
            ip_locked_to="20.6.1.10",
            today=TODAY,
        )
        self.assertEqual(record.display_name, "Kiwoom Korea")
        self.assertEqual(record.status, "pending_check")
        self.assertRegex(record.credential_fingerprint, r"^[0-9a-f]{16}$")
        self.assertNotIn("synthetic", repr(record))

    def test_ibkr_needs_its_token_expiry_within_a_year(self) -> None:
        base = dict(broker="ibkr_flex", display_name="Main login", environment="live",
                    credentials={"flex_token": "0123456789"}, ip_locked_to=None, today=TODAY)
        with self.assertRaisesRegex(ValueError, "expiry date"):
            new_connection_record(expires_on=None, **base)
        with self.assertRaisesRegex(ValueError, "already expired"):
            new_connection_record(expires_on=TODAY, **base)
        with self.assertRaisesRegex(ValueError, "one year"):
            new_connection_record(expires_on=TODAY + timedelta(days=400), **base)
        with self.assertRaisesRegex(ValueError, "no mock"):
            new_connection_record(expires_on=TODAY + timedelta(days=365), **{**base, "environment": "mock"})
        with self.assertRaisesRegex(ValueError, "exactly"):
            new_connection_record(expires_on=TODAY + timedelta(days=365),
                                  **{**base, "credentials": {"flex_token": "1", "extra": "2"}})
        record = new_connection_record(expires_on=TODAY + timedelta(days=365), **base)
        self.assertTrue(record.key_vault_secret_name.startswith("ibkr-flex-source-"))

    def test_ip_lock_must_be_a_public_address(self) -> None:
        self.assertEqual(validate_ip_lock(" 20.6.1.10 "), "20.6.1.10")
        self.assertIsNone(validate_ip_lock(""))
        for value in ("10.50.9.4", "127.0.0.1", "not-an-ip"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_ip_lock(value)
        self.assertEqual(ip_lock_state("20.6.1.10", "20.6.1.10"), "locked")
        self.assertEqual(ip_lock_state("20.6.1.11", "20.6.1.10"), "mismatch")
        self.assertEqual(ip_lock_state(None, "20.6.1.10"), "not_locked")

    def test_health_turns_amber_then_red_as_expiry_nears(self) -> None:
        def health(days: int | None, stored: str = "healthy", lock: str = "locked") -> str:
            expires = None if days is None else TODAY + timedelta(days=days)
            return connection_health(stored_status=stored, expires_on=expires, today=TODAY, ip_lock=lock)

        self.assertEqual(health(200), "healthy")
        self.assertEqual(health(None), "healthy")
        self.assertEqual(health(21), "renew_soon")
        self.assertEqual(health(0), "expired")
        self.assertEqual(health(200, stored="failing"), "failing")
        self.assertEqual(health(200, lock="mismatch"), "failing")
        self.assertEqual(health(200, stored="pending_check"), "not_checked")
        self.assertEqual(health(-5, stored="suspended"), "suspended")


def _case(**changes) -> OnboardingCase:
    values = dict(case_id="c1", broker="kiwoom_rest", broker_account_id="5012345610")
    values.update(changes)
    return OnboardingCase(**values)


def _pass_all(case: OnboardingCase) -> OnboardingCase:
    from ibkr_etl.onboarding_cases import checks_for

    for check_id in checks_for(case.source_kind, case.stage):
        case = record_check(case, check_id, "pass")
    return case


class OnboardingCaseTests(unittest.TestCase):
    def test_full_journey_needs_three_owner_decisions(self) -> None:
        case = _case()
        self.assertEqual(case_status(case), "platform_working")
        case, event = advance(_pass_all(case))
        self.assertEqual((case.stage, event.to_stage), ("load_and_reconcile", "load_and_reconcile"))

        case = _pass_all(case)
        self.assertEqual(case_status(case), "waiting_on_owner")
        self.assertEqual(next_owner_action(case), "Approve the account terms.")
        self.assertIsNone(advance(case)[1])
        case, _ = approve(case, "account_terms")
        case, _ = advance(case)
        self.assertEqual(case.stage, "build_history")

        case = _pass_all(case)
        with self.assertRaisesRegex(ValueError, "historical reports before"):
            approve(case, "go_live", confirmation=go_live_phrase("5012-3456-10"),
                    broker_account_display="5012-3456-10")
        case, _ = approve(case, "historical_reports")
        self.assertIn("confirmation phrase", next_owner_action(case))
        with self.assertRaisesRegex(ValueError, "exact confirmation"):
            approve(case, "go_live", confirmation="go live", broker_account_display="5012-3456-10")
        case, _ = approve(case, "go_live", confirmation="GO LIVE 5012-3456-10",
                          broker_account_display="5012-3456-10")
        case, _ = advance(case)
        self.assertEqual((case.stage, case_status(case)), ("first_month_end", "live"))
        self.assertIsNone(next_owner_action(case))

        case, event = advance(_pass_all(case))
        self.assertEqual((case.stage, case_status(case), event.event_type), ("closed", "closed", "advanced"))
        self.assertEqual([tile["state"] for tile in progress(case)], ["done"] * 4)

    def test_approval_waits_for_checks_and_warnings_are_acknowledged(self) -> None:
        case = _case(stage="load_and_reconcile")
        with self.assertRaisesRegex(ValueError, "Every check"):
            approve(case, "account_terms")
        case = _pass_all(case)
        case = record_check(case, "transactions_loaded", "warn")
        with self.assertRaisesRegex(ValueError, "Acknowledge"):
            approve(case, "account_terms")
        case, _ = approve(case, "account_terms", acknowledge_warnings=["transactions_loaded"])
        self.assertEqual(advance(case)[0].stage, "build_history")
        with self.assertRaisesRegex(ValueError, "cannot be approved"):
            approve(_case(), "account_terms")

    def test_a_failed_check_blocks_and_names_itself(self) -> None:
        case = record_check(_case(stage="load_and_reconcile"), "nav_reconciled", "fail")
        self.assertEqual(case_status(case), "blocked")
        self.assertEqual(next_owner_action(case), "Look at the failed check: NAV reconciliation.")
        with self.assertRaisesRegex(ValueError, "not a check"):
            record_check(case, "history_built", "pass")

    def test_up_to_three_accounts_at_a_time(self) -> None:
        live = _case(case_id="a", broker_account_id="50123456", stage="first_month_end")
        working = [_case(case_id=f"b{n}", broker_account_id=f"5099999{n}", stage="build_history") for n in range(3)]
        assert_can_start(broker="ibkr_flex", broker_account_id="U16088571", open_cases=[live])
        assert_can_start(broker="ibkr_flex", broker_account_id="U16088571", open_cases=[live, *working[:2]])
        with self.assertRaisesRegex(ValueError, "3 accounts are already being onboarded"):
            assert_can_start(broker="ibkr_flex", broker_account_id="U16088571", open_cases=[live, *working])
        with self.assertRaisesRegex(ValueError, "already has"):
            assert_can_start(broker="kiwoom_rest", broker_account_id="50123456", open_cases=[live])
        cancelled = _case(case_id="c", broker_account_id="50999999", cancelled=True)
        assert_can_start(broker="kiwoom_rest", broker_account_id="50999999", open_cases=[cancelled])

    def test_a_derived_portfolio_dry_runs_instead_of_loading(self) -> None:
        composite = _case(broker=None, broker_account_id=None, source_kind="composite_portfolio",
                          target_subject_id="synthetic-long-short")
        case, _ = advance(_pass_all(composite))
        self.assertEqual(case.stage, "load_and_reconcile")
        with self.assertRaisesRegex(ValueError, "not a check"):
            record_check(case, "nav_reconciled", "pass")
        case = _pass_all(case)
        self.assertEqual(next_owner_action(case), "Approve the portfolio definition and terms.")
        case, _ = approve(case, "account_terms")
        self.assertEqual(advance(case)[0].stage, "build_history")

        with self.assertRaisesRegex(ValueError, "portfolio ID already has"):
            assert_can_start(target_subject_id="synthetic-long-short", open_cases=[composite])
        others = [_case(case_id=f"o{n}", broker_account_id=f"5077777{n}") for n in range(2)]
        assert_can_start(broker="kiwoom_rest", broker_account_id="50123456",
                         target_subject_id="KIWOOM-50123456", open_cases=[composite, *others[:1]])
        with self.assertRaisesRegex(ValueError, "already being onboarded"):
            assert_can_start(broker="kiwoom_rest", broker_account_id="50123456",
                             target_subject_id="KIWOOM-50123456", open_cases=[composite, *others])
        with self.assertRaisesRegex(ValueError, "broker account case"):
            _case(source_kind="composite_portfolio", target_subject_id="synthetic-long-short")
        with self.assertRaisesRegex(ValueError, "broker account case"):
            _case(broker=None)

    def test_cancel_only_before_go_live(self) -> None:
        case, event = cancel(_case(stage="build_history"), reason="Wrong account")
        self.assertEqual(case_status(case), "cancelled")
        self.assertEqual(event.detail, {"reason": "Wrong account"})
        self.assertEqual(progress(case)[2]["state"], "cancelled")
        with self.assertRaisesRegex(ValueError, "paused"):
            cancel(_case(stage="first_month_end"), reason="Changed mind")

    def test_terms_digest_is_stable(self) -> None:
        self.assertEqual(terms_digest({"b": 1, "a": "x"}), terms_digest({"a": "x", "b": 1}))
        self.assertNotEqual(terms_digest({"a": "x"}), terms_digest({"a": "y"}))


class SecurityMasterTests(unittest.TestCase):
    def test_isin_check_digit(self) -> None:
        for isin in ("US0378331005", "KR7005930003", "KR7000660001", "US5949181045"):
            self.assertTrue(isin_is_valid(isin), isin)
        for isin in ("US0378331006", "KR700593000", ""):
            self.assertFalse(isin_is_valid(isin), isin)
        with self.assertRaisesRegex(ValueError, "check digit"):
            SecurityIdentifier("isin", "KR7005930004")

    def test_same_isin_resolves_to_one_security_across_brokers(self) -> None:
        master = SecurityMaster()
        ibkr_key, added = master.register(
            [SecurityIdentifier("ibkr_conid", "34784593"), SecurityIdentifier("isin", "KR7005930003")]
        )
        self.assertEqual((ibkr_key, len(added)), ("34784593", 2))
        kiwoom_key, added = master.register(
            [SecurityIdentifier("krx_code", "005930"), SecurityIdentifier("isin", "kr7005930003")]
        )
        self.assertEqual(kiwoom_key, "34784593")
        self.assertEqual(added, [SecurityIdentifier("krx_code", "005930")])

    def test_new_kiwoom_security_gets_a_namespaced_key(self) -> None:
        self.assertEqual(new_security_key([SecurityIdentifier("krx_code", "000660")]), "KRX:000660")
        master = SecurityMaster()
        self.assertEqual(master.register([SecurityIdentifier("krx_code", "000660")])[0], "KRX:000660")

    def test_us_tickers_are_normalized_and_namespaced(self) -> None:
        self.assertEqual(SecurityIdentifier("us_ticker", " aapl ").value, "AAPL")
        for spelling in ("BRK B", "BRK/B", "BRK-B", "brk.b"):
            self.assertEqual(SecurityIdentifier("us_ticker", spelling).value, "BRK.B", spelling)
        for bad in ("", "1ABC", "AAPL$", "TOOLONGX", "BRK.ABCDE"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                SecurityIdentifier("us_ticker", bad)
        self.assertEqual(new_security_key([SecurityIdentifier("us_ticker", "BRK B")]), "US:BRK.B")
        self.assertEqual(SecurityMaster().register([SecurityIdentifier("us_ticker", "AMGN")])[0], "US:AMGN")

    def test_a_us_stock_at_ibkr_and_kiwoom_is_one_security(self) -> None:
        master = SecurityMaster()
        ibkr = ibkr_identifiers(conid=265598, isin="US0378331005", symbol="AAPL", listing_exchange="NASDAQ")
        self.assertEqual([item.scheme for item in ibkr], ["ibkr_conid", "isin", "us_ticker"])
        self.assertEqual(master.register(ibkr)[0], "265598")
        self.assertEqual(master.resolve([SecurityIdentifier("us_ticker", "AAPL")]), "265598")
        # The same symbol on another exchange is a different company, so it is not registered.
        hk = ibkr_identifiers(conid=12345, isin="bad", symbol="AAPL", listing_exchange="SEHK")
        self.assertEqual(hk, [SecurityIdentifier("ibkr_conid", "12345")])

    def test_conflicts_block_instead_of_guessing(self) -> None:
        master = SecurityMaster([
            (SecurityIdentifier("krx_code", "005930"), "KRX:005930"),
            (SecurityIdentifier("isin", "KR7000660001"), "KRX:000660"),
        ])
        with self.assertRaises(SecurityConflict):
            master.resolve([SecurityIdentifier("krx_code", "005930"), SecurityIdentifier("isin", "KR7000660001")])
        missing = unmapped_holdings(
            [
                {"label": "Samsung", "identifiers": [SecurityIdentifier("krx_code", "005930")]},
                {"label": "Mixed", "identifiers": [SecurityIdentifier("krx_code", "005930"),
                                                   SecurityIdentifier("isin", "KR7000660001")]},
                {"label": "Unknown", "identifiers": [SecurityIdentifier("krx_code", "035420")]},
                {"label": "Nothing", "identifiers": []},
            ],
            master,
        )
        self.assertEqual(missing, ["Mixed (conflicting identifiers)", "Unknown", "Nothing"])


if __name__ == "__main__":
    unittest.main()
