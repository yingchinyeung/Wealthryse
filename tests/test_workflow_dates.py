import copy
from datetime import date
from unittest.mock import Mock,patch
from uuid import UUID
import unittest
from ibkr_etl.market_calendar import CALENDAR_VERSION
from ibkr_etl.workflow_readiness import PREREQUISITES
from ibkr_etl.workflow_dates import normalized_policy,policy_record,append_policy,require_policy,evaluation_dates
from ibkr_etl.workflow_ledger import normalized_record,digest,evaluate_heads,append_evidence,record_shadow_decision

TENANT='00000000-0000-0000-0000-000000000001'
SCOPE=dict(tenant_id=TENANT,cycle_key='2026-10-06',subject_id='account')
CUTOFF=date(2026,10,5)


def arguments():
    dates={name:CUTOFF for name in PREREQUISITES};dates['benchmarks_fx']=date(2026,9,30)
    return dict(**SCOPE,cycle_cutoff=CUTOFF,calendar_version=CALENDAR_VERSION,expected_dates=dates)


def policy():
    record=normalized_policy(**arguments())
    return dict(record,cycle_cutoff=CUTOFF,policy_id=str(UUID(int=100)),sequence_no=1,
        expected_dates_json=record['expected_dates'],record_sha256=digest(record))


def chain(p):
    rows={}
    for index,(stage,parents) in enumerate(PREREQUISITES.items(),1):
        record=normalized_record(**SCOPE,stage=stage,status='succeeded',validated=True,
            through_date=date.fromisoformat(p['expected_dates_json'][stage]),input_sha256='a'*64,output_sha256='b'*64,
            dependency_ids={name:rows[name]['evidence_id'] for name in parents},producer='fixture',
            date_policy_id=p['policy_id'],date_policy_sha256=p['record_sha256'])
        rows[stage]=dict(record,through_date=date.fromisoformat(record['through_date']),sequence_no=1,
            evidence_id=str(UUID(int=index)),dependency_ids_json=record['dependency_ids'],record_sha256=digest(record))
    return rows


class WorkflowDateTests(unittest.TestCase):
    def test_exact_complete_typed_policy_and_known_calendar_required(self):
        for key,value in [('calendar_version','unreviewed'),('cycle_cutoff','2026-10-05'),('subject_id',' account')]:
            with self.assertRaises(ValueError):normalized_policy(**dict(arguments(),**{key:value}))
        for change in ('missing','extra','future','string'):
            values=arguments()
            if change=='missing':del values['expected_dates']['risk']
            elif change=='extra':values['expected_dates']['unknown']=CUTOFF
            else:values['expected_dates']['risk']=date(2026,10,6) if change=='future' else '2026-10-05'
            with self.assertRaises(ValueError):normalized_policy(**values)

    def test_policy_integrity_scope_and_cutoff_cannot_be_changed(self):
        p=policy()
        for key,value in [('calendar_version','unknown'),('record_sha256','f'*64),('policy_version','unknown')]:
            with self.assertRaises(ValueError):policy_record(dict(p,**{key:value}))
        for key,value in [('tenant_id',str(UUID(int=2))),('subject_id','other'),('cycle_key','old')]:
            with self.assertRaises(ValueError):evaluation_dates(p,**dict(SCOPE,**{key:value}),stage='risk',expected_date=CUTOFF)
        with self.assertRaises(ValueError):evaluation_dates(p,**SCOPE,stage='risk',expected_date=date(2026,10,6))

    def test_holiday_dates_are_bound_to_all_completions(self):
        p=policy();rows=chain(p)
        self.assertEqual(evaluate_heads(rows,**SCOPE,stage='assurance',expected_date=CUTOFF,date_policy=p),(True,()))
        self.assertFalse(evaluate_heads(rows,**SCOPE,stage='assurance',expected_date=CUTOFF)[0])
        with self.assertRaises(ValueError):evaluate_heads(rows,**SCOPE,stage='risk',expected_date=CUTOFF,
            date_policy=p,expected_dates={'derived':CUTOFF})

    def test_new_policy_identity_fences_old_completions_even_with_equal_dates(self):
        p=policy();rows=chain(p)
        newer=dict(p,policy_id=str(UUID(int=101)),sequence_no=2)
        self.assertFalse(evaluate_heads(rows,**SCOPE,stage='assurance',expected_date=CUTOFF,date_policy=newer)[0])
        for key,value in [('date_policy_id',str(UUID(int=111))),('date_policy_sha256','c'*64)]:
            altered=copy.deepcopy(rows);altered['ingestion'][key]=value
            self.assertFalse(evaluate_heads(altered,**SCOPE,stage='assurance',expected_date=CUTOFF,date_policy=p)[0])

    def test_policy_hash_covers_same_count_date_correction(self):
        before=normalized_policy(**arguments());changed=arguments();changed['expected_dates']['benchmarks_fx']=CUTOFF
        self.assertNotEqual(digest(before),digest(normalized_policy(**changed)))

    def test_policy_append_rejects_late_writer_and_keeps_current_idempotency(self):
        p=policy();connection=Mock()
        with patch('ibkr_etl.workflow_ledger._scope'),patch('ibkr_etl.workflow_dates.current_policy',return_value=p):
            result=append_policy(connection,**arguments())
            self.assertEqual(result['status'],'unchanged');connection.execute.assert_not_called()
            changed=arguments();changed['expected_dates']['benchmarks_fx']=CUTOFF
            with self.assertRaisesRegex(ValueError,'Stale'):append_policy(connection,**changed)
            result=append_policy(connection,expected_previous_id=p['policy_id'],**changed)
            self.assertEqual(result['status'],'recorded');self.assertNotEqual(result['policy_id'],p['policy_id'])

    def test_require_policy_checks_current_head_scope_and_hash(self):
        p=policy()
        with patch('ibkr_etl.workflow_dates.current_policy',return_value=p):
            self.assertEqual(require_policy(None,**SCOPE,policy_id=p['policy_id'])['policy_id'],p['policy_id'])
            with self.assertRaises(ValueError):require_policy(None,**SCOPE,policy_id=str(UUID(int=99)))
            with self.assertRaises(ValueError):require_policy(None,**SCOPE,policy_id=p['policy_id'],policy_sha256='c'*64)
            with self.assertRaises(ValueError):require_policy(None,**dict(SCOPE,subject_id='other'),policy_id=p['policy_id'])

    def test_dated_record_requires_complete_identity_hash_pair(self):
        row=chain(policy())['ingestion']
        args={key:row[key] for key in ('stage','status','validated','through_date','input_sha256','output_sha256','producer')}
        for pair in [(None,'b'*64),(str(UUID(int=9)),None),(str(UUID(int=9)),'bad')]:
            with self.assertRaises(ValueError):normalized_record(**SCOPE,**args,dependency_ids={},
                date_policy_id=pair[0],date_policy_sha256=pair[1])

    def test_append_binds_policy_to_sql_and_retains_restart_idempotency(self):
        p=policy();rows=chain(p);row=rows['ingestion'];connection=Mock()
        args={key:row[key] for key in ('stage','status','validated','through_date','input_sha256','output_sha256','producer','date_policy_id','date_policy_sha256')}
        with patch('ibkr_etl.workflow_ledger._scope'),patch('ibkr_etl.workflow_ledger.heads',return_value=rows),patch('ibkr_etl.workflow_dates.require_policy',return_value=p):
            self.assertEqual(append_evidence(connection,**SCOPE,**args,dependency_ids={})['status'],'unchanged')
            connection.execute.assert_not_called()
            args['output_sha256']='d'*64
            result=append_evidence(connection,**SCOPE,**args,dependency_ids={},expected_previous_id=row['evidence_id'])
            self.assertEqual(result['status'],'recorded')
            sql,params=connection.execute.call_args.args
            self.assertIn('date_policy_id,date_policy_sha256',sql)
            self.assertEqual(params[-2:],(p['policy_id'],p['record_sha256']))
            with self.assertRaises(ValueError):append_evidence(connection,**SCOPE,**dict(args,through_date=date(2026,10,6)),dependency_ids={})
            with self.assertRaises(ValueError):append_evidence(connection,**SCOPE,**dict(args,through_date=date(2026,9,30)),dependency_ids={})

    def test_legacy_completion_cannot_downgrade_a_dated_cycle(self):
        rows=chain(policy());row=rows['ingestion']
        args={key:row[key] for key in ('stage','status','validated','through_date','input_sha256','output_sha256','producer')}
        with patch('ibkr_etl.workflow_ledger._scope'),patch('ibkr_etl.workflow_ledger.heads',return_value=rows):
            with self.assertRaisesRegex(ValueError,'Persisted'):append_evidence(None,**SCOPE,**args,dependency_ids={})

    def test_shadow_decision_is_bound_but_never_authorizes_execution(self):
        p=policy();rows=chain(p);connection=Mock()
        with patch('ibkr_etl.workflow_ledger._scope'),patch('ibkr_etl.workflow_ledger.heads',return_value=rows),patch('ibkr_etl.workflow_dates.require_policy',return_value=p):
            result=record_shadow_decision(connection,**SCOPE,stage='assurance',expected_date=CUTOFF,date_policy_id=p['policy_id'])
            self.assertTrue(result['ready']);self.assertFalse(result['execution_authorized'])
            sql,params=connection.execute.call_args.args
            self.assertIn('date_policy_id,date_policy_sha256',sql);self.assertEqual(params[-2:],(p['policy_id'],p['record_sha256']))

    def test_truthy_validation_is_not_a_pass(self):
        from dataclasses import replace
        from ibkr_etl.workflow_readiness import StageEvidence,readiness
        values={'ingestion':StageEvidence('account','ingestion','succeeded','true',CUTOFF,'a'*64,{})}
        self.assertFalse(readiness('reconciliation',subject_id='account',expected_date=CUTOFF,evidence=values)[0])


if __name__=='__main__':unittest.main()
