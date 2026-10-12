import hashlib
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ibkr_etl.agent_daily_report import prepare_daily_report, render_daily_message
from ibkr_etl.agent_daily_schedule import report_window
from ibkr_etl.agent_governance import REGISTRY
from ibkr_etl.util import canonical_json

TENANT='00000000-0000-0000-0000-000000000001'
DAY=date(2026,10,1)


def event(sequence=1,previous='0'*64):
    e=dict(sequence=sequence,previous_sha256=previous,event_id=f'event-{sequence}',
           occurred_at='2026-10-01T09:00:00+00:00',role='data_engineer',action='inspect_and_analyze',
           operation_id='operation-one',phase='succeeded',evidence_sha256='a'*64,cost_microusd=0)
    e['sha256']=hashlib.sha256(canonical_json(e).encode()).hexdigest()
    return e


def row(e): return dict(sequence_no=e['sequence'],event_sha256=e['sha256'],event_json=e)


def ready_archive():
    archive=Mock()
    archive.check_ready.return_value={'protected_archive_verified':True}
    archive.verify_detail.side_effect=lambda tenant,e:{'detail_verified':True,'evidence_sha256':e['evidence_sha256']}
    return archive


class Connection:
    autocommit=True
    info=SimpleNamespace(transaction_status=0)
    def __init__(self, prior=None, head=None, events=(), now=None):
        self.responses=iter([{}, {'tenant_id':TENANT},{},
            {'observed_at':now or datetime(2026,10,2,tzinfo=timezone.utc)},prior,head,list(events)])
        self.committed=False
        self.statements=[]
    def execute(self,*args):
        self.statements.append(args[0])
        result=next(self.responses)
        return SimpleNamespace(fetchone=lambda:result,fetchall=lambda:result)
    @contextmanager
    def transaction(self):
        yield
        self.committed=True


class DailyReportTests(unittest.TestCase):
    def test_verified_slice_commits_queue_but_does_not_send(self):
        e=event(); c=Connection(head=row(e),events=[row(e)]); archive=ready_archive()
        with patch('ibkr_etl.agent_daily_report.enqueue_report',return_value='b'*64) as enqueue:
            result=prepare_daily_report(c,archive,tenant_id=TENANT,report_date=DAY)
        self.assertTrue(c.committed)
        self.assertEqual(result['messages_sent'],0)
        archive.verify.assert_called_once_with(TENANT,e)
        archive.verify_detail.assert_called_once_with(TENANT,e)
        report=enqueue.call_args.kwargs['report']
        self.assertFalse(report['all_system_actions_captured'])
        self.assertTrue(report['protected_archive_verified'])

    def test_empty_day_is_valid_and_explicitly_limited(self):
        c=Connection(); archive=ready_archive()
        with patch('ibkr_etl.agent_daily_report.enqueue_report',return_value='b'*64) as enqueue:
            prepare_daily_report(c,archive,tenant_id=TENANT,report_date=DAY)
        start,end=report_window(DAY)
        msg=render_daily_message(dict(report_date=DAY.isoformat(),timezone='Asia/Hong_Kong',
            period_start=start.isoformat(),period_end=end.isoformat(),report=enqueue.call_args.kwargs['report']))
        self.assertIn('No specialist actions were recorded',msg['text'])
        self.assertIn('complete system-wide capture is not yet established',msg['text'])
        self.assertIn('do not prove autonomous specialist execution',msg['text'])
        self.assertEqual(msg['recipient'],'anthony.ying@wealthryse.ai')
        self.assertEqual(archive.check_ready.call_count,2)

    def test_empty_ledger_cannot_claim_archive_readiness(self):
        for archive in (None,Mock(),Mock(check_ready=Mock(return_value={})),
                        Mock(check_ready=Mock(return_value={'protected_archive_verified':False}))):
            c=Connection()
            with patch('ibkr_etl.agent_daily_report.enqueue_report') as enqueue:
                with self.assertRaises(ValueError):
                    prepare_daily_report(c,archive,tenant_id=TENANT,report_date=DAY)
                enqueue.assert_not_called()
            self.assertFalse(c.committed)

    def test_readiness_lost_before_commit_never_queues(self):
        archive=ready_archive()
        archive.check_ready.side_effect=[{'protected_archive_verified':True},ValueError('hold removed')]
        c=Connection()
        with patch('ibkr_etl.agent_daily_report.enqueue_report') as enqueue:
            with self.assertRaises(ValueError):
                prepare_daily_report(c,archive,tenant_id=TENANT,report_date=DAY)
            enqueue.assert_not_called()
        self.assertFalse(c.committed)

    def test_preview_is_database_read_only_and_does_not_enqueue(self):
        c=Connection()
        with patch('ibkr_etl.agent_daily_report.enqueue_report') as enqueue:
            result=prepare_daily_report(c,ready_archive(),tenant_id=TENANT,report_date=DAY,persist=False)
            enqueue.assert_not_called()
        self.assertIn('READ ONLY',c.statements[0])
        self.assertEqual(result['queued_channels'],[])
        self.assertEqual(result['reports_created'],0)
        self.assertFalse(result['database_committed'])
        self.assertEqual(hashlib.sha256(canonical_json(result['content']).encode()).hexdigest(),result['report_sha256'])
        self.assertIn('No specialist actions',render_daily_message(result['content'])['text'])

    def test_boundary_verified_even_on_empty_day(self):
        e=event(); e['occurred_at']='2026-09-30T09:00:00+00:00'
        c=Connection(prior=row(e),head=row(e)); archive=ready_archive()
        with patch('ibkr_etl.agent_daily_report.enqueue_report',return_value='b'*64):
            prepare_daily_report(c,archive,tenant_id=TENANT,report_date=DAY)
        archive.verify.assert_called_once_with(TENANT,e)

    def test_archive_failure_never_queues_or_commits(self):
        e=event(); c=Connection(head=row(e),events=[row(e)]); archive=ready_archive()
        archive.verify.side_effect=ValueError('missing evidence')
        with patch('ibkr_etl.agent_daily_report.enqueue_report') as enqueue:
            with self.assertRaises(ValueError): prepare_daily_report(c,archive,tenant_id=TENANT,report_date=DAY)
        self.assertFalse(c.committed); enqueue.assert_not_called()

    def test_missing_unregistered_or_wrong_detail_never_queues(self):
        for receipt in (None,{}, {'detail_verified':False,'evidence_sha256':'a'*64},
                        {'detail_verified':True,'evidence_sha256':'b'*64}):
            e=event();c=Connection(head=row(e),events=[row(e)]);archive=ready_archive()
            archive.verify_detail.side_effect=None;archive.verify_detail.return_value=receipt
            with patch('ibkr_etl.agent_daily_report.enqueue_report') as enqueue:
                with self.assertRaises(ValueError): prepare_daily_report(c,archive,tenant_id=TENANT,report_date=DAY)
                enqueue.assert_not_called()
            self.assertFalse(c.committed)

    def test_boundary_detail_required_even_when_window_empty(self):
        e=event();c=Connection(prior=row(e),head=row(e));archive=ready_archive()
        archive.verify_detail.side_effect=ValueError('unregistered producer')
        with patch('ibkr_etl.agent_daily_report.enqueue_report') as enqueue:
            with self.assertRaises(ValueError): prepare_daily_report(c,archive,tenant_id=TENANT,report_date=DAY)
            enqueue.assert_not_called()

    def test_incomplete_or_tampered_slice_rejected(self):
        e=event()
        for rows in [[],[row(dict(e,phase='failed'))], [dict(row(e),sequence_no=2)]]:
            with patch('ibkr_etl.agent_daily_report.enqueue_report') as enqueue:
                with self.assertRaises(ValueError):
                    prepare_daily_report(Connection(head=row(e),events=rows),ready_archive(),tenant_id=TENANT,report_date=DAY)
                enqueue.assert_not_called()

    def test_period_must_be_closed_and_connection_idle(self):
        with self.assertRaises(ValueError):
            prepare_daily_report(Connection(now=datetime(2026,10,1,9,tzinfo=timezone.utc)),ready_archive(),tenant_id=TENANT,report_date=DAY)
        c=Connection(); c.autocommit=False
        with self.assertRaises(ValueError): prepare_daily_report(c,Mock(),tenant_id=TENANT,report_date=DAY)

    def test_oversized_range_cannot_truncate_into_success(self):
        e=event(sequence=10001)
        with self.assertRaises(ValueError):
            prepare_daily_report(Connection(head=row(e)),ready_archive(),tenant_id=TENANT,report_date=DAY)

    def test_formatter_rejects_false_verification_or_role_count(self):
        start,end=report_window(DAY)
        report=dict(ledger_slice_verified=True,protected_archive_verified=True,event_count=0,
                    role_counts={r:0 for r in REGISTRY})
        content=dict(report_date=DAY.isoformat(),timezone='Asia/Hong_Kong',period_start=start.isoformat(),
                     period_end=end.isoformat(),report=report)
        for changes in [{'protected_archive_verified':False},{'event_count':True},{'role_counts':{'other':0}}]:
            with self.assertRaises(ValueError): render_daily_message(dict(content,report={**report,**changes}))

