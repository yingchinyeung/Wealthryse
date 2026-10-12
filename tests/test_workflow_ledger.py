import copy
from datetime import date
from uuid import UUID
import unittest
from ibkr_etl.workflow_ledger import normalized_record,digest,evaluate_heads,POLICY_VERSION,append_evidence
from ibkr_etl.workflow_readiness import PREREQUISITES
from unittest.mock import patch

TENANT='00000000-0000-0000-0000-000000000001'
DAY=date(2026,9,30)
SCOPE=dict(tenant_id=TENANT,cycle_key='2026-10-01',subject_id='test-subject')


def chain():
    rows={}
    for i,(stage,parents) in enumerate(PREREQUISITES.items(),1):
        record=normalized_record(**SCOPE,stage=stage,status='succeeded',validated=True,through_date=DAY,
            input_sha256=f'{i:064x}',output_sha256=f'{i:064x}',producer='test-producer',
            dependency_ids={p:rows[p]['evidence_id'] for p in parents})
        rows[stage]={**record,'through_date':DAY,'evidence_id':str(UUID(int=i)),
            'dependency_ids_json':record['dependency_ids'],'record_sha256':digest(record),'sequence_no':1}
    return rows


class WorkflowLedgerTests(unittest.TestCase):
    def test_full_chain_is_ready(self):
        self.assertEqual(evaluate_heads(chain(),**SCOPE,stage='assurance',expected_date=DAY),(True,()))

    def test_correction_supersedes_dependants_even_if_output_hash_same(self):
        rows=chain()
        rows['ingestion']['evidence_id']=str(UUID(int=900))
        self.assertFalse(evaluate_heads(rows,**SCOPE,stage='assurance',expected_date=DAY)[0])

    def test_cross_tenant_cycle_account_and_corrupt_record_block(self):
        for key,value in [('tenant_id',str(UUID(int=2))),('cycle_key','old-cycle'),
                          ('subject_id','other'),('output_sha256','f'*64),('policy_version','unknown')]:
            rows=chain()
            rows['ingestion'][key]=value
            self.assertFalse(evaluate_heads(rows,**SCOPE,stage='assurance',expected_date=DAY)[0])

    def test_missing_stale_and_failed_inputs_block(self):
        rows=chain()
        del rows['benchmarks_fx']
        self.assertFalse(evaluate_heads(rows,**SCOPE,stage='assurance',expected_date=DAY)[0])
        self.assertFalse(evaluate_heads(chain(),**SCOPE,stage='assurance',expected_date=date(2026,10,1))[0])
        rows=chain()
        rows['ingestion'].update(status='failed',validated=False)
        self.assertFalse(evaluate_heads(rows,**SCOPE,stage='assurance',expected_date=DAY)[0])

    def test_no_synthetic_parent_or_untyped_dependency(self):
        arguments=dict(**SCOPE,stage='risk',status='succeeded',validated=True,through_date=DAY,
            input_sha256='a'*64,output_sha256='b'*64,producer='test',dependency_ids={})
        with self.assertRaises(ValueError):
            normalized_record(**arguments)
        arguments.update(stage='ingestion',dependency_ids={})
        for values in [dict(validated='true'),dict(status='failed'),dict(input_sha256='counts-only'),dict(through_date='2026-09-30')]:
            with self.assertRaises(ValueError):
                normalized_record(**dict(arguments,**values))

    def test_stale_worker_cannot_overwrite_new_completion(self):
        rows=chain()
        r=rows['ingestion']
        arguments={k:r[k] for k in ('stage','status','validated','through_date','input_sha256','output_sha256','producer')}
        arguments['output_sha256']='f'*64
        with patch('ibkr_etl.workflow_ledger._scope'),patch('ibkr_etl.workflow_ledger.heads',return_value=rows):
            with self.assertRaisesRegex(ValueError,'expected head changed'):
                append_evidence(None,**SCOPE,**arguments,dependency_ids={})

    def test_current_duplicate_is_idempotent_but_stale_dependency_is_not(self):
        rows=chain()
        r=rows['reconciliation']
        arguments={k:r[k] for k in ('stage','status','validated','through_date','input_sha256','output_sha256','producer')}
        with patch('ibkr_etl.workflow_ledger._scope'),patch('ibkr_etl.workflow_ledger.heads',return_value=rows):
            result=append_evidence(None,**SCOPE,**arguments,dependency_ids=r['dependency_ids_json'])
            self.assertEqual(result['status'],'unchanged')
            rows['ingestion']['evidence_id']=str(UUID(int=999))
            with self.assertRaisesRegex(ValueError,'superseded dependency'):
                append_evidence(None,**SCOPE,**arguments,dependency_ids=r['dependency_ids_json'])

    def test_duplicate_with_stale_transitive_dependency_is_rejected(self):
        rows=chain()
        r=rows['assurance']
        arguments={k:r[k] for k in ('stage','status','validated','through_date','input_sha256','output_sha256','producer')}
        rows['ingestion']['evidence_id']=str(UUID(int=999))
        with patch('ibkr_etl.workflow_ledger._scope'),patch('ibkr_etl.workflow_ledger.heads',return_value=rows):
            with self.assertRaisesRegex(ValueError,'not ready'):
                append_evidence(None,**SCOPE,**arguments,dependency_ids=r['dependency_ids_json'])


if __name__=='__main__':
    unittest.main()
