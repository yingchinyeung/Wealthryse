import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from ibkr_etl.agent_daily_schedule import due_report_dates, report_window, delivery_key, DELIVERY_CHANNELS


class AgentDailyScheduleTests(unittest.TestCase):
    def test_owner_time_is_18_hong_kong_10_utc_all_year(self):
        for day in (date(2026,1,2), date(2026,7,2), date(2026,10,2), date(2026,12,31)):
            start,end = report_window(day)
            self.assertEqual(end.hour, 10)
            self.assertEqual(end.astimezone(ZoneInfo('Asia/Hong_Kong')).hour,18)
            self.assertEqual(end-start,timedelta(days=1))

    def test_due_at_exact_boundary_not_before(self):
        day=date(2026,10,2)
        _,end=report_window(day)
        self.assertEqual(due_report_dates(now=end-timedelta(microseconds=1),next_report_date=day),[])
        self.assertEqual(due_report_dates(now=end,next_report_date=day),[day])

    def test_london_dst_does_not_shift_hong_kong_delivery(self):
        for day,london_hour in ((date(2026,7,2),11),(date(2026,12,2),10)):
            _,end=report_window(day)
            london=end.astimezone(ZoneInfo('Europe/London'))
            self.assertEqual(london.hour,london_hour)
            self.assertEqual(due_report_dates(now=london,next_report_date=day),[day])

    def test_catch_up_preserves_each_missed_day_and_bounds_work(self):
        dates=due_report_dates(now=datetime(2026,10,5,10,tzinfo=timezone.utc),next_report_date=date(2026,10,1),limit=3)
        self.assertEqual(dates,[date(2026,10,n) for n in (1,2,3)])

    def test_bad_clock_and_limit_rejected(self):
        with self.assertRaises(ValueError):
            due_report_dates(now=datetime(2026,10,2),next_report_date=date(2026,10,2))
        for limit in (0,32,True):
            with self.assertRaises(ValueError):
                due_report_dates(now=datetime.now(timezone.utc),next_report_date=date(2026,10,2),limit=limit)

    def test_channel_keys_stable_and_account_separated(self):
        tenant='00000000-0000-0000-0000-000000000001'
        keys=[delivery_key(tenant,date(2026,10,2),c) for c in DELIVERY_CHANNELS]
        self.assertEqual(len(set(keys)),2)
        self.assertEqual(keys[0],delivery_key(tenant,date(2026,10,2),'website'))
        with self.assertRaises(ValueError):
            delivery_key(tenant,date(2026,10,2),'unregistered')


if __name__=='__main__':
    unittest.main()
