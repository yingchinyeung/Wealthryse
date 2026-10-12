import hashlib
import unittest
from contextlib import contextmanager, ExitStack
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ibkr_etl.agent_daily_delivery import dispatch_daily_email
from ibkr_etl.util import canonical_json


class Connection:
    autocommit=True
    info=SimpleNamespace(transaction_status=0)
    def __init__(self, row): self.row=row; self.commits=0
    @contextmanager
    def transaction(self):
        yield
        self.commits+=1
    def execute(self,*args): return SimpleNamespace(fetchone=lambda:self.row)


class DailyDeliveryTests(unittest.TestCase):
    def setup_dispatch(self, stack):
        content={'report_date':'2026-10-01'}
        c=Connection({'report_json':content,'report_sha256':hashlib.sha256(canonical_json(content).encode()).hexdigest()})
        mocks={name:stack.enter_context(patch('ibkr_etl.agent_daily_delivery.'+name)) for name in
               ('expire_ambiguous_sends','claim_delivery','start_send','finish_send','render_daily_message')}
        mocks['claim_delivery'].return_value=dict(report_date=date(2026,10,1),lease_token='lease',delivery_key='stable-key')
        mocks['render_daily_message'].return_value={'recipient':'anthony.ying@wealthryse.ai'}
        return c,mocks

    def test_two_commits_before_send_acceptance_not_delivery(self):
        with ExitStack() as stack:
            c,m=self.setup_dispatch(stack)
            class Sender:
                def submit(_,message,**fields):
                    self.assertEqual(c.commits,2)
                    self.assertEqual(fields['delivery_key'],'stable-key')
                    return {'state':'accepted','provider_reference':'receipt-123'}
            result=dispatch_daily_email(c,Sender(),tenant_id='tenant')
            self.assertEqual(c.commits,3)
            self.assertFalse(result['recipient_delivery_confirmed'])
            self.assertEqual(m['finish_send'].call_args.kwargs['outcome'],'accepted')

    def test_timeout_marks_uncertain_without_retry_or_secret_logging(self):
        with ExitStack() as stack:
            c,m=self.setup_dispatch(stack); sender=Mock()
            sender.submit.side_effect=TimeoutError('secret-text')
            result=dispatch_daily_email(c,sender,tenant_id='tenant')
            self.assertEqual(result['state'],'uncertain'); sender.submit.assert_called_once()
            self.assertNotIn('secret-text',str(m['finish_send'].call_args))

    def test_invalid_provider_receipt_cannot_claim_success(self):
        for receipt in [None,{'state':'delivered','provider_reference':'one'},
                        {'state':'accepted','provider_reference':'https://unexpected/secret'}]:
            with ExitStack() as stack:
                c,m=self.setup_dispatch(stack); sender=Mock(); sender.submit.return_value=receipt
                self.assertEqual(dispatch_daily_email(c,sender,tenant_id='tenant')['state'],'uncertain')

    def test_corrupted_report_is_not_sent(self):
        with ExitStack() as stack:
            c,m=self.setup_dispatch(stack); c.row['report_sha256']='f'*64; sender=Mock()
            with self.assertRaises(ValueError): dispatch_daily_email(c,sender,tenant_id='tenant')
            sender.submit.assert_not_called(); m['start_send'].assert_not_called()

    def test_stale_lease_cannot_reach_provider(self):
        with ExitStack() as stack:
            c,m=self.setup_dispatch(stack); m['start_send'].side_effect=ValueError('expired'); sender=Mock()
            with self.assertRaises(ValueError): dispatch_daily_email(c,sender,tenant_id='tenant')
            sender.submit.assert_not_called()

    def test_idle_and_open_transaction_do_not_send(self):
        with ExitStack() as stack:
            c,m=self.setup_dispatch(stack); m['claim_delivery'].return_value=None; sender=Mock()
            self.assertEqual(dispatch_daily_email(c,sender,tenant_id='tenant')['send_attempts'],0)
            c.autocommit=False
            with self.assertRaises(ValueError): dispatch_daily_email(c,sender,tenant_id='tenant')
            sender.submit.assert_not_called()

    def test_disabled_graph_adapter_preserves_pending_queue_without_attempt(self):
        from ibkr_etl.agent_daily_graph_mail import create_graph_sender
        with ExitStack() as stack:
            c, m = self.setup_dispatch(stack)
            with self.assertRaisesRegex(ValueError, 'Daily mail transport is not activated'):
                dispatch_daily_email(c, create_graph_sender(), tenant_id='tenant')
            self.assertEqual(c.commits, 0)
            for name in ('expire_ambiguous_sends', 'claim_delivery', 'start_send', 'finish_send'):
                m[name].assert_not_called()
