import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, Mock, patch
from ibkr_etl import agent_daily_worker as worker


class DailyWorkerTests(unittest.TestCase):
    def test_hong_kong_boundary_not_host_zone(self):
        self.assertEqual(worker.latest_closed_day(datetime(2026, 10, 3, 9, 59, tzinfo=timezone.utc)), date(2026, 10, 2))
        self.assertEqual(worker.latest_closed_day(datetime(2026, 10, 3, 10, tzinfo=timezone.utc)), date(2026, 10, 3))
        with self.assertRaises(ValueError): worker.latest_closed_day(datetime(2026, 10, 3, 10))

    def test_cursor_uses_holes_not_max_date(self):
        c = Mock(); c.execute.return_value.fetchall.return_value = [dict(report_date=date(2026, 10, 4))]
        with patch.object(worker, 'require_tenant'):
            result = worker.pending_dates(c, now=datetime(2026, 10, 5, 10, tzinfo=timezone.utc))
        self.assertEqual(result, [date(2026, 10, 4)])
        sql, values = c.execute.call_args.args
        self.assertIn("state IS DISTINCT FROM 'delivered'", sql)
        self.assertIn('ORDER BY days.day', sql)
        self.assertEqual(values[-1], 7)

    def test_before_activation_no_backfill(self):
        c = Mock()
        with patch.object(worker, 'require_tenant'):
            self.assertEqual(worker.pending_dates(c, now=datetime(2026, 10, 3, 9, tzinfo=timezone.utc)), [])
        c.execute.assert_not_called()

    def test_invalid_cursor_or_controls_rejected(self):
        c = Mock(); now = datetime(2026, 10, 5, 10, tzinfo=timezone.utc)
        for bad in (0, 8, True):
            with self.assertRaises(ValueError): worker.pending_dates(c, now=now, limit=bad)
        for days in ([date(2026, 10, 6)], [date(2026, 10, 2)], [date(2026, 10, 4)] * 2,
                     [date(2026, 10, 5), date(2026, 10, 4)], [datetime(2026, 10, 4)]):
            c.execute.return_value.fetchall.return_value = [dict(report_date=d) for d in days]
            with patch.object(worker, 'require_tenant'), self.assertRaises(ValueError):
                worker.pending_dates(c, now=now)

    def test_publication_binds_exact_hash_and_date(self):
        day = date(2026, 10, 3); c = Mock(); archive = Mock()
        built = dict(report_sha256='a'*64, event_count=2)
        with patch.object(worker, 'prepare_daily_report', return_value=built), \
             patch.object(worker, 'get_daily_report', return_value=None), \
             patch.object(worker, 'publish_website_report', return_value=dict(state='available_on_website', report_date=day.isoformat())) as pub:
            item = worker.publish_exact(c, archive, day)
        pub.assert_called_once_with(c, tenant_id=worker.TENANT, report_date=day, report_sha256='a'*64)
        self.assertFalse(item['already_published'])

    def test_existing_report_replays_without_delivery_attempt(self):
        built = dict(report_sha256='a'*64, event_count=0)
        with patch.object(worker, 'prepare_daily_report', return_value=built), \
             patch.object(worker, 'get_daily_report', return_value=built), \
             patch.object(worker, 'publish_website_report') as pub:
            item = worker.publish_exact(Mock(), Mock(), date(2026, 10, 2))
        self.assertTrue(item['already_published']); pub.assert_not_called()

    def test_conflict_and_wrong_publication_fail(self):
        c = Mock(); archive = Mock(); day = date(2026, 10, 3)
        with patch.object(worker, 'prepare_daily_report', side_effect=[dict(report_sha256='a'*64), dict(report_sha256='b'*64)]), \
             patch.object(worker, 'publish_website_report') as pub, self.assertRaises(ValueError):
            worker.publish_exact(c, archive, day)
        pub.assert_not_called()
        with patch.object(worker, 'prepare_daily_report', return_value=dict(report_sha256='a'*64, event_count=0)), \
             patch.object(worker, 'get_daily_report', return_value=None), \
             patch.object(worker, 'publish_website_report', return_value=dict(state='idle')), self.assertRaises(ValueError):
            worker.publish_exact(c, archive, day)

    def cycle(self, *, locked=True, day=date(2026, 10, 3), replay=None, archive_error=False, fresh_hash='a'*64):
        c = MagicMock()
        c.execute.return_value.fetchone.side_effect = [dict(locked=locked), dict(now=datetime(2026, 10, 3, 10, 2, tzinfo=timezone.utc)),
                                                     dict(now=datetime(2026, 10, 3, 10, 3, tzinfo=timezone.utc))]
        connect = MagicMock(); connect.return_value.__enter__.return_value = c
        archive = Mock(); emit = Mock()
        if archive_error: archive.check_ready.side_effect = ValueError('missing archive')
        patches = [patch.object(worker, 'bind_report_session'), patch.object(worker, 'verify_report_access', return_value={}),
                   patch.object(worker, 'pending_dates', side_effect=[[day], []]),
                   patch.object(worker, 'publish_exact', return_value=dict(report_date=day.isoformat(), report_sha256='a'*64)),
                   patch.object(worker, 'get_daily_report', return_value=dict(report_sha256=fresh_hash)),
                   patch.object(worker, 'ready_event', return_value={}), patch.object(worker, 'serialize_ready_event', return_value='ready')]
        import contextlib
        with contextlib.ExitStack() as stack:
            for p in patches: stack.enter_context(p)
            try:
                result = worker.run_daily_cycle(connect, archive, replay_date=replay, emit=emit)
            except Exception:
                self.assertFalse(emit.called)
                self.assertIn('pg_advisory_unlock', c.execute.call_args.args[0])
                raise
        return result, c, emit

    def test_busy_worker_no_publication_or_notification(self):
        result, c, emit = self.cycle(locked=False)
        self.assertEqual(result['status'], 'busy'); emit.assert_not_called()
        self.assertNotIn('pg_advisory_unlock', c.execute.call_args.args[0])

    def test_success_fresh_login_before_telemetry_and_unlock(self):
        result, c, emit = self.cycle()
        self.assertTrue(result['current_report_available']); emit.assert_called_once_with('ready')
        self.assertFalse(result['email_submitted']); self.assertEqual(result['source_rows_changed'], 0)
        self.assertIn('pg_advisory_unlock', c.execute.call_args.args[0])

    def test_rehearsal_never_emits_ready_notice(self):
        result, _, emit = self.cycle(day=date(2026, 10, 2), replay=date(2026, 10, 2))
        self.assertEqual(result['mode'], 'rehearsal'); emit.assert_not_called()

    def test_archive_or_fresh_content_failure_not_success(self):
        with self.assertRaises(ValueError): self.cycle(archive_error=True)
        with self.assertRaises(ValueError): self.cycle(fresh_hash='b'*64)

    def test_unclosed_window_or_bad_replay_rejected(self):
        with self.assertRaises(ValueError): self.cycle(replay=date(2026, 10, 4))
        with self.assertRaises(ValueError): worker.run_daily_cycle(Mock(), Mock(), replay_date='2026-10-03')
