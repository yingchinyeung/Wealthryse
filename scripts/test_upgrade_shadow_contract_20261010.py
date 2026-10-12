"""Reconciliation wrapper behavior with synthetic, non-network dependencies."""
import contextlib, io, json, os, runpy, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch


class ShadowContract(unittest.TestCase):
    def setUp(self):
        self.main=runpy.run_path(str(Path(os.environ.get('QUALIFICATION_APP','/app'))/'scripts/reconcile_institutional_shadow.py'),run_name='qualification_import')['main']
        self.g=self.main.__globals__
        self.env={'DEPLOYMENT_ENVIRONMENT':'internal-production','AUTHORITATIVE_JOB_MANIFEST_JSON':'{}','SHADOW_RUN_DATE':'2026-09-30'}

    def test_wrong_environment(self):self.guard({'DEPLOYMENT_ENVIRONMENT':'staging'})
    def test_missing_manifest(self):self.guard({'AUTHORITATIVE_JOB_MANIFEST_JSON':''})
    def test_nonobject_manifest(self):self.guard({'AUTHORITATIVE_JOB_MANIFEST_JSON':'[]'})
    def guard(self,change):
        env=dict(self.env);env.update(change);connect=Mock(side_effect=AssertionError('No connection permitted'))
        with patch.dict(os.environ,env,clear=True),patch.dict(self.g,connect=connect),self.assertRaises(RuntimeError):self.main()
        connect.assert_not_called()

    def run_case(self,mutation=False,fail=False,mismatch=False):
        conn=Mock();conn.execute.side_effect=[Mock(fetchone=lambda:{'count':3}),Mock(fetchone=lambda:{'count':4 if mutation else 3})]
        evidence={'status':'fail' if mismatch else 'pass','mismatches':[{'code':'synthetic'}] if mismatch else [],
            'manifest_sha256':'a'*64,'registry_sha256':'b'*64,'active_subjects':['synthetic'],'preview_subjects':[], 'planned_capability_count':1}
        load=Mock(return_value={'synthetic':[]});reconcile=Mock(return_value=evidence)
        record=Mock(return_value='synthetic-id',side_effect=RuntimeError('synthetic database error') if fail else None)
        settings=SimpleNamespace(database_path='/tmp/unused',database_url='postgresql://synthetic.invalid/db',tenant_id='synthetic-tenant')
        output=io.StringIO()
        with patch.dict(os.environ,self.env,clear=True),patch.dict(self.g,connect=Mock(return_value=conn),load_settings=Mock(return_value=settings),
            load_shadow_targets=load,reconcile_shadow_plan=reconcile,record_shadow_reconciliation=record),contextlib.redirect_stdout(output):
            if mutation or fail or mismatch:
                with self.assertRaises(RuntimeError):self.main()
            else:self.assertEqual(self.main(),0)
        conn.close.assert_called_once()
        self.assertEqual(load.call_args.kwargs['tenant_id'],'synthetic-tenant')
        self.assertEqual(record.call_args.kwargs['tenant_id'],'synthetic-tenant')
        if fail:conn.rollback.assert_called_once();conn.commit.assert_not_called()
        else:
            conn.commit.assert_called_once();conn.rollback.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())['work_queue_unchanged'],not mutation)
        if mutation:self.assertIn('shadow_mutated_work_queue',{x['code'] for x in evidence['mismatches']})

    def test_success(self):self.run_case()
    def test_queue_mutation_detected(self):self.run_case(mutation=True)
    def test_mismatch_fails_closed(self):self.run_case(mismatch=True)
    def test_record_failure_rolls_back(self):self.run_case(fail=True)


if __name__=='__main__':unittest.main()
