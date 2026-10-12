from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
from uuid import UUID
import unittest
from psycopg.pq import TransactionStatus
from ibkr_etl import workflow_leases as leases
from test_workflow_dates import SCOPE, CUTOFF, policy, chain

OWNER = str(UUID(int=500))
NOW = datetime(2026,10,6,12,tzinfo=timezone.utc)


def arguments():
    p = policy(); rows = chain(p); row = rows['reconciliation']
    return dict(**SCOPE, **{key:row[key] for key in
        ('stage','status','validated','through_date','input_sha256','output_sha256','producer',
         'date_policy_id','date_policy_sha256')}, dependency_ids=row['dependency_ids_json'],
        method_sha256='c'*64)


def lease():
    work = leases.work_record(**arguments())
    return dict(**SCOPE, stage='reconciliation', version=leases.VERSION, sequence_no=1,
        generation=1, token=str(UUID(int=600)), owner_id=OWNER, work=work,
        work_sha256=leases.ledger.digest(work), state='claimed', attempt=1,
        observed_at=NOW.isoformat(), expires_at=(NOW+timedelta(seconds=120)).isoformat(),
        execution_authorized=False)


def connection():
    c = Mock(); c.info.transaction_status=TransactionStatus.INTRANS
    c.transaction.return_value.__enter__=Mock(); c.transaction.return_value.__exit__=Mock(return_value=False)
    return c


class LeaseTests(unittest.TestCase):
    def test_only_dated_deterministic_work_is_eligible(self):
        for stage in ('ingestion','benchmarks_fx','reports'):
            args=arguments(); args.update(stage=stage, dependency_ids={p:str(UUID(int=1))
                for p in leases.ledger.PREREQUISITES[stage]})
            with self.assertRaises(ValueError): leases.work_record(**args)
        with self.assertRaises(ValueError): leases.work_record(**dict(arguments(),date_policy_id=None))

    def test_method_and_ttl_are_strict(self):
        for value in (None,'bad',True):
            with self.assertRaises(ValueError): leases.work_record(**dict(arguments(),method_sha256=value))
        for value in (True,0,901,1.5,'120'):
            with self.assertRaises(ValueError): leases._ttl(value)

    def test_autocommit_is_rejected(self):
        c=connection(); c.info.transaction_status=TransactionStatus.IDLE
        with self.assertRaisesRegex(ValueError,'transaction'): leases._transaction(c)

    def test_scope_policy_and_current_heads_are_checked(self):
        c=connection(); p=policy(); rows=chain(p); rows.pop('reconciliation')
        with patch.object(leases.ledger,'_scope'),patch.object(leases,'require_policy',return_value=p),patch.object(leases.ledger,'heads',return_value=rows):
            self.assertEqual(leases._context(c,lease()['work']),SCOPE)
            rows['ingestion']['evidence_id']=str(UUID(int=9))
            with self.assertRaisesRegex(ValueError,'Dependency'): leases._context(c,lease()['work'])

    def test_new_claim_has_no_execution_authority(self):
        c=connection(); c.execute.return_value.fetchone.return_value={'n':0}
        with patch.object(leases,'_context',return_value=SCOPE),patch.object(leases,'_head',return_value=None),patch.object(leases,'_now',return_value=NOW),patch.object(leases,'_append',side_effect=lambda c,v:v):
            result=leases.claim(c,owner_id=OWNER,**arguments())
            self.assertEqual(result['generation'],1); self.assertFalse(result['execution_authorized'])

    def test_restart_claim_never_renews_lease(self):
        old=lease()
        with patch.object(leases,'_context',return_value=SCOPE),patch.object(leases,'_head',return_value=old),patch.object(leases,'_now',return_value=NOW),patch.object(leases,'_append') as append:
            self.assertEqual(leases.claim(connection(),owner_id=OWNER,**arguments()),old)
            append.assert_not_called()
            with self.assertRaisesRegex(ValueError,'busy'): leases.claim(connection(),owner_id=str(UUID(int=501)),**arguments())

    def test_expiration_advances_generation_and_bounds_recovery(self):
        old=dict(lease(),expires_at=NOW.isoformat()); c=connection()
        c.execute.return_value.fetchone.return_value={'n':1}
        with patch.object(leases,'_context',return_value=SCOPE),patch.object(leases,'_head',return_value=old),patch.object(leases,'_now',return_value=NOW),patch.object(leases,'_append',side_effect=lambda c,v:v):
            result=leases.claim(c,owner_id=str(UUID(int=501)),**arguments())
            self.assertEqual((result['generation'],result['attempt']),(2,2));self.assertNotEqual(result['token'],old['token'])
            c.execute.return_value.fetchone.return_value={'n':3}
            with self.assertRaisesRegex(ValueError,'limit'): leases.claim(c,owner_id=OWNER,**arguments())

    def test_late_or_superseded_workers_cannot_heartbeat(self):
        old=lease()
        for head in (dict(old,token=str(UUID(int=999))),dict(old,state='cancelled'),dict(old,expires_at=NOW.isoformat())):
            with patch.object(leases,'_context',return_value=SCOPE),patch.object(leases,'_head',return_value=head),patch.object(leases,'_now',return_value=NOW):
                with self.assertRaises(ValueError): leases.heartbeat(connection(),old)

    def test_heartbeat_and_cancel_are_append_only_transitions(self):
        old=lease()
        with patch.object(leases,'_fence',return_value=(old,NOW)),patch.object(leases,'_append',side_effect=lambda c,v:v):
            updated=leases.heartbeat(connection(),old,ttl_seconds=30)
            self.assertEqual(updated['generation'],old['generation']);self.assertEqual(updated['sequence_no'],2)
            self.assertEqual(updated['expires_at'],(NOW+timedelta(seconds=30)).isoformat())
            self.assertEqual(leases.cancel(connection(),old)['state'],'cancelled')

    def test_changed_completion_payload_is_rejected_before_writing(self):
        with patch.object(leases.ledger,'append_evidence') as append:
            with self.assertRaises(ValueError): leases.complete(connection(),lease(),**dict(arguments(),input_sha256='d'*64))
            append.assert_not_called()

    def test_completion_journal_and_evidence_share_savepoint(self):
        c=connection(); old=lease()
        with patch.object(leases.ledger,'_scope'),patch.object(leases,'_head',return_value=old),patch.object(leases,'_fence',return_value=(old,NOW)),patch.object(leases.ledger,'append_evidence',return_value={'evidence_id':str(UUID(int=8)),'status':'recorded'}),patch.object(leases,'_append',side_effect=RuntimeError('journal unavailable')):
            with self.assertRaises(RuntimeError): leases.complete(c,old,**arguments())
            self.assertIs(c.transaction.return_value.__exit__.call_args.args[0],RuntimeError)

    def test_missing_or_modified_event_payload_is_not_accepted(self):
        c=connection(); payload=lease()
        row=dict(**SCOPE,stage='reconciliation',sequence_no=1,payload_json=payload,record_sha256='f'*64)
        c.execute.return_value.fetchone.return_value=row
        with self.assertRaisesRegex(ValueError,'integrity'): leases._head(c,SCOPE,'reconciliation')

    def test_completed_replay_rejects_new_parent_even_if_parent_is_ready(self):
        old=lease(); args=arguments(); rows=chain(policy())
        record=leases.ledger.normalized_record(**{k:v for k,v in args.items() if k!='method_sha256'})
        fingerprint=leases.ledger.digest(record)
        completed=dict(old,state='completed',evidence_id=rows['reconciliation']['evidence_id'],completion_sha256=fingerprint)
        rows['ingestion']['evidence_id']=str(UUID(int=900))
        with patch.object(leases.ledger,'_scope'),patch.object(leases,'_head',return_value=completed),patch.object(leases,'require_policy',return_value=policy()),patch.object(leases.ledger,'heads',return_value=rows),patch.object(leases.ledger,'evaluate_heads',return_value=(True,())):
            with self.assertRaisesRegex(ValueError,'changed'): leases.complete(connection(),old,**args)


if __name__=='__main__': unittest.main()
