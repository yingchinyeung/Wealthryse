import unittest
from datetime import date, datetime

from ibkr_etl.market_calendar import (CALENDARS, EMERGENCY_CLOSURES, EmergencyClosure,
    CalendarCoverageError, apply_emergency_closures, benchmark_session_on_or_before,
    expected_benchmark_date, check_benchmark_dates)


class MarketCalendarTests(unittest.TestCase):
    def expected(self, benchmark, timestamp):
        return expected_benchmark_date(benchmark, now=datetime.fromisoformat(timestamp))["expected_date"]

    def test_shanghai_long_holiday_does_not_become_stale_on_day_four(self):
        self.assertEqual(self.expected("SHCOMP", "2026-10-07T18:00:00+08:00"), "2026-09-30")

    def test_hong_kong_not_shanghai_calendar(self):
        self.assertEqual(self.expected("HSI", "2026-10-07T18:00:00+08:00"), "2026-10-06")

    def test_taiwan_teacher_holiday_and_clearing_only_days(self):
        for text in ("2026-09-28", "2026-02-12", "2026-02-13"):
            self.assertFalse(CALENDARS["XTAI"].is_session(date.fromisoformat(text)))

    def test_verified_typhoon_closure_is_taiwan_only(self):
        day = date(2026, 7, 10)
        self.assertFalse(CALENDARS["XTAI"].is_session(day))
        self.assertEqual(benchmark_session_on_or_before("TAIEX", day), date(2026, 7, 9))
        self.assertEqual(benchmark_session_on_or_before("TAIEX", date(2026, 7, 13)), date(2026, 7, 13))
        for market in ("XNYS", "XNAS", "XHKG", "XSHG"):
            self.assertTrue(CALENDARS[market].is_session(day))
        closure = EMERGENCY_CLOSURES[0]
        self.assertIn(closure.announcement_source, CALENDARS["XTAI"].sources)
        self.assertIn(closure.policy_source, CALENDARS["XTAI"].sources)

    def test_emergency_closure_requires_reviewed_scope_and_sources(self):
        from dataclasses import replace
        valid = EmergencyClosure("XTAI", date(2026, 7, 14), "verified closure",
                                 "https://example.test/notice", "https://example.test/policy")
        changed = apply_emergency_closures(CALENDARS, (valid,))
        self.assertTrue(CALENDARS["XTAI"].is_session(valid.day))
        self.assertFalse(changed["XTAI"].is_session(valid.day))
        invalid = (replace(valid, market="UNKNOWN"), replace(valid, reason=" "),
                   replace(valid, announcement_source=""), replace(valid, policy_source="http://example.test"),
                   replace(valid, day=date(2027, 7, 14)), replace(valid, day=date(2026, 7, 11)),
                   EMERGENCY_CLOSURES[0])
        for closure in invalid:
            with self.subTest(closure=closure), self.assertRaises(CalendarCoverageError):
                apply_emergency_closures(CALENDARS, (closure,))
        with self.assertRaises(CalendarCoverageError):
            apply_emergency_closures(CALENDARS, (valid, valid))

    def test_typhoon_monitor_accepts_prior_close_not_a_fabricated_observation(self):
        data = self.evidence("2026-07-09", "TAIEX")
        data["capture"]["captured_at"] = "2026-07-11T18:00:00+08:00"
        self.assertEqual(check_benchmark_dates(data)["checks"][0]["state"], "current_for_scheduled_cycle")
        data["benchmarks"][0]["benchmark_usable_date"] = "2026-07-10"
        self.assertEqual(check_benchmark_dates(data)["checks"][0]["state"], "unexpected_non_session")

    def test_us_etf_benchmarks_follow_nyse_and_kospi_stays_unmapped(self):
        for benchmark in ("EWH", "EWT", "SPY", "XLK"):
            self.assertEqual(benchmark_session_on_or_before(benchmark, date(2026, 11, 26)), date(2026, 11, 25))
        with self.assertRaises(CalendarCoverageError):
            benchmark_session_on_or_before("KS200", date(2026, 10, 16))

    def test_half_days_are_sessions(self):
        for market in ("XHKG", "XNYS", "XNAS"):
            self.assertTrue(CALENDARS[market].is_session(date(2026,12,24)))

    def test_new_year_resolves_into_verified_prior_year(self):
        for benchmark in ("GSPC", "IXIC", "HSI", "SHCOMP", "TAIEX"):
            self.assertEqual(benchmark_session_on_or_before(benchmark, date(2026,1,1)), date(2025,12,31))
        self.assertIn(date(2025,12,31), CALENDARS["XHKG"].half_days)
        with self.assertRaises(CalendarCoverageError):
            benchmark_session_on_or_before("TAIEX", date(2025,1,1))

    def test_historical_long_closures_keep_distinct_exchange_sessions(self):
        self.assertEqual(benchmark_session_on_or_before("TAIEX", date(2025,1,31)), date(2025,1,22))
        self.assertEqual(benchmark_session_on_or_before("HSI", date(2025,1,31)), date(2025,1,28))
        self.assertEqual(benchmark_session_on_or_before("SHCOMP", date(2025,2,4)), date(2025,1,27))
        self.assertEqual(benchmark_session_on_or_before("SHCOMP", date(2025,10,8)), date(2025,9,30))
        self.assertEqual(benchmark_session_on_or_before("TAIEX", date(2025,10,6)), date(2025,10,3))

    def test_historical_emergency_mourning_is_not_an_asian_closure(self):
        for market in ("XNYS", "XNAS"):
            self.assertFalse(CALENDARS[market].is_session(date(2025,1,9)))
        for market in ("XHKG", "XSHG", "XTAI"):
            self.assertTrue(CALENDARS[market].is_session(date(2025,1,9)))

    def test_china_makeup_working_weekends_not_trading(self):
        self.assertFalse(CALENDARS["XSHG"].is_session(date(2026,10,10)))

    def test_cycle_grace(self):
        self.assertEqual(self.expected("GSPC", "2026-10-02T17:04:59+08:00"), "2026-09-30")
        self.assertEqual(self.expected("GSPC", "2026-10-02T17:05:00+08:00"), "2026-10-01")

    def test_eastern_daylight_saving_boundary(self):
        self.assertEqual(self.expected("GSPC", "2026-11-02T18:00:00+08:00"), "2026-10-30")

    def test_american_thanksgiving(self):
        self.assertEqual(self.expected("IXIC", "2026-11-27T18:00:00+08:00"), "2026-11-25")

    def test_unsupported_year_and_market_fail_closed(self):
        for market, stamp in [("GSPC","2028-01-05T18:00:00+08:00"), ("TAIEX","2027-01-05T18:00:00+08:00"),
                              ("IXIC","2027-01-05T18:00:00+08:00"), ("NEW","2026-10-01T18:00:00+08:00")]:
            with self.assertRaises(CalendarCoverageError):
                self.expected(market, stamp)

    def test_verified_ms_markets_cover_2027_without_extending_other_markets(self):
        from ibkr_etl.market_calendar import HK_BANK_CALENDAR
        for day in ('2027-01-01','2027-02-08','2027-02-09','2027-03-26','2027-03-29',
                    '2027-04-05','2027-05-13','2027-06-09','2027-07-01','2027-09-16',
                    '2027-10-01','2027-10-08','2027-12-27'):
            self.assertFalse(CALENDARS['XHKG'].is_session(date.fromisoformat(day)))
            self.assertFalse(HK_BANK_CALENDAR.is_session(date.fromisoformat(day)))
        for day in ('2027-02-05','2027-12-24','2027-12-31'):
            self.assertTrue(CALENDARS['XHKG'].is_session(date.fromisoformat(day)))
            self.assertIn(date.fromisoformat(day),CALENDARS['XHKG'].half_days)
        self.assertTrue(CALENDARS['XNYS'].is_session(date(2027,12,31)))
        self.assertFalse(CALENDARS['XNYS'].is_session(date(2027,12,24)))
        self.assertIn(date(2027,11,26),CALENDARS['XNYS'].half_days)
        self.assertEqual(benchmark_session_on_or_before('GSPC',date(2027,7,5)),date(2027,7,2))
        for market in ('XNAS','XSHG','XTAI'):
            with self.assertRaises(CalendarCoverageError):CALENDARS[market].is_session(date(2027,1,4))

    def test_hibor_calendar_not_changed_by_exchange_emergency(self):
        from ibkr_etl.market_calendar import HK_BANK_CALENDAR
        closure=EmergencyClosure('XHKG',date(2026,10,8),'reviewed stock-exchange-only closure',
            'https://example.test/notice','https://example.test/policy')
        changed=apply_emergency_closures(CALENDARS,(closure,))
        self.assertFalse(changed['XHKG'].is_session(closure.day))
        self.assertTrue(HK_BANK_CALENDAR.is_session(closure.day))

    def test_no_naive_time(self):
        with self.assertRaises(ValueError):
            self.expected("GSPC", "2026-10-01T18:00:00")

    def evidence(self, observed="2026-09-30", benchmark="SHCOMP"):
        return {"capture":{"captured_at":"2026-10-07T18:00:00+08:00"},
                "subjects":[{"subject_id":"test", "lifecycle_status":"active"}],
                "registrations":[{"subject_id":"test", "capability":"benchmark_refresh", "operating_state":"active"}],
                "benchmarks":[{"subject_id":"test", "benchmark_id":benchmark,
                               "benchmark_usable_date":observed,"benchmark_pass_date":None}]}

    def test_real_holiday_warning_data_is_current(self):
        result = check_benchmark_dates(self.evidence())
        self.assertEqual(result["checks"][0]["state"], "current_for_scheduled_cycle")
        self.assertEqual(result["checks"][0]["quality"], "warn")

    def test_missing_and_genuinely_late_data(self):
        self.assertEqual(check_benchmark_dates(self.evidence(None))["checks"][0]["state"], "missing")
        self.assertEqual(check_benchmark_dates(self.evidence("2026-09-29"))["checks"][0]["state"], "behind_expected_session")

    def test_unknown_market_never_healthy(self):
        self.assertEqual(check_benchmark_dates(self.evidence(benchmark="NEW"))["checks"][0]["state"], "calendar_unverified")

    def test_observation_on_closed_day_needs_review(self):
        self.assertEqual(check_benchmark_dates(self.evidence("2026-10-01"))["checks"][0]["state"], "unexpected_non_session")

    def test_paused_registration_excluded(self):
        data = self.evidence()
        data["registrations"][0]["operating_state"] = "paused"
        self.assertEqual(check_benchmark_dates(data)["checks"], [])

    def test_onboarding_not_activated(self):
        data = self.evidence()
        data["subjects"][0]["lifecycle_status"] = "onboarding"
        self.assertEqual(check_benchmark_dates(data)["checks"], [])


if __name__ == "__main__":
    unittest.main()
