"""Synthetic original release-job contracts. No real files are approved/uploaded."""
import contextlib,hashlib,io,json,os,runpy,unittest
from pathlib import Path
from unittest.mock import Mock,patch

class ReleaseContract(unittest.TestCase):
    def setUp(self):
        self.main=runpy.run_path(str(Path(os.environ.get('QUALIFICATION_APP','/app'))/'scripts/release_internal_reports.py'),run_name='qualification_import')['main'];self.g=self.main.__globals__
        self.env={'DEPLOYMENT_ENVIRONMENT':'staging','DATABASE_URL':'postgresql://synthetic.invalid/test','PORTFOLIO_TENANT_ID':'test-tenant',
            'REPORTING_SNAPSHOT_ID':'test-snapshot','INTERNAL_RELEASE_APPROVED_BY':' OWNER@EXAMPLE.INVALID ', 'AZURE_STORAGE_ACCOUNT_URL':'https://synthetic.invalid/'}
    def case(self,fail=None,existing=False):
        env=dict(self.env);body=b'synthetic-not-a-report';sha=hashlib.sha256(body).hexdigest()
        snap={'snapshot_id':'test-snapshot','subject_id':'test-account','period_end':'2026-09-30','status':'frozen','quality_status':'warn',
            'publication_allowed':False,'publication_blockers_json':'["primary_benchmark:not_licensed_for_publication"]'}
        runs=[{'generation_run_id':i,'snapshot_id':'test-snapshot','report_type':t,'status':'awaiting_approval','output_reference':'report-drafts/test.'+e,'output_sha256':sha}
            for i,t,e in [(39,'factsheet','pdf'),(40,'presentation','pptx')]]
        if fail=='environment':env['DEPLOYMENT_ENVIRONMENT']='production'
        if fail=='database':env['DATABASE_URL']='sqlite:///test'
        if fail=='quality':snap['quality_status']='fail'
        if fail=='state':snap['status']='draft'
        if fail=='risk':snap['publication_blockers_json']='["risk:crisis_replay_unavailable"]'
        if fail=='no_blockers':snap['publication_blockers_json']='[]'
        if fail=='pair':runs.pop()
        if fail=='snapshot_mismatch':runs[0]['snapshot_id']='other'
        if fail=='not_ready':runs[0]['status']='failed'
        if fail=='digest':runs[0]['output_sha256']='0'*64
        if fail=='missing_digest':runs[0]['output_sha256']=''
        if fail=='container':runs[0]['output_reference']='https://synthetic.invalid/other/test.pdf'
        conn=Mock();writes=[]
        def execute(sql,args=()):
            q=' '.join(sql.split());result=Mock()
            if q.startswith('SELECT snapshot_id'):result.fetchone.return_value=None if fail=='missing_snapshot' else snap
            elif q.startswith('SELECT generation_run_id'):result.fetchall.return_value=runs
            elif "event_type = 'month_end_package_user_accepted'" in q:result.fetchone.return_value=None if fail=='acceptance' else (1,)
            elif q.startswith('SELECT 1 FROM audit_events'):result.fetchone.return_value=(1,) if fail=='existing_event' else None
            elif q.startswith(('INSERT ','UPDATE ')):
                writes.append((q,args));result.fetchone.return_value=(7,)
                if fail=='db_write':raise RuntimeError('synthetic database failure')
            else:raise AssertionError(q)
            return result
        conn.execute.side_effect=execute;source=Mock();source.download_blob.return_value.readall.return_value=body
        dest=Mock();dest.exists.return_value=existing;dest.download_blob.return_value.readall.return_value=b'changed' if fail=='existing_digest' else body
        if fail=='upload':dest.upload_blob.side_effect=RuntimeError('synthetic upload failure')
        service=Mock();service.get_blob_client.side_effect=lambda container,name:source if container=='report-drafts' else dest
        connect=Mock(return_value=conn);output=io.StringIO();error=fail not in (None,'existing_event')
        with patch.dict(os.environ,env,clear=True),patch.dict(self.g,connect=connect),patch('azure.identity.DefaultAzureCredential'),patch('azure.storage.blob.BlobServiceClient',return_value=service),contextlib.redirect_stdout(output):
            if error:
                with self.assertRaises((RuntimeError,ValueError)):self.main()
            else:self.assertEqual(self.main(),0)
        if fail in ('environment','database'):connect.assert_not_called();return
        conn.close.assert_called_once();self.assertEqual(connect.call_args.args[2],'test-tenant')
        if error:
            conn.rollback.assert_called_once();conn.commit.assert_not_called()
            if fail not in ('db_write',):self.assertEqual(writes,[])
            if fail not in ('db_write','upload'):dest.upload_blob.assert_not_called()
        else:
            conn.commit.assert_called_once();conn.rollback.assert_not_called()
            result=json.loads(output.getvalue());self.assertFalse(result['client_publication_allowed']);self.assertEqual(len(result['artifacts']),2)
            self.assertEqual(writes[0][1][2],'owner@example.invalid')
            conditions=json.loads(writes[0][1][-1]);self.assertEqual(conditions['client_distribution'],'prohibited')
            if existing:
                dest.upload_blob.assert_not_called();self.assertEqual([x[0] for x in dest.method_calls if x[0] in ('set_http_headers','set_blob_metadata')],['set_http_headers','set_blob_metadata']*2)
            else:
                self.assertEqual(dest.upload_blob.call_count,2)
                for call in dest.upload_blob.call_args_list:
                    self.assertIs(call.kwargs['overwrite'],False);self.assertEqual(call.kwargs['metadata']['client_distribution'],'prohibited')
            event_count=sum("INSERT INTO audit_events" in q for q,_ in writes)
            self.assertEqual(event_count,0 if fail=='existing_event' else 1)
    def test_new_internal_release(self):self.case()
    def test_existing_immutable_release(self):self.case(existing=True)
    def test_audit_deduplication(self):self.case(fail='existing_event')
    def test_environment(self):self.case(fail='environment')
    def test_database(self):self.case(fail='database')
    def test_missing_snapshot(self):self.case(fail='missing_snapshot')
    def test_owner_acceptance(self):self.case(fail='acceptance')
    def test_quality_gate(self):self.case(fail='quality')
    def test_snapshot_state(self):self.case(fail='state')
    def test_risk_gate(self):self.case(fail='risk')
    def test_reserved_workflow(self):self.case(fail='no_blockers')
    def test_complete_pair(self):self.case(fail='pair')
    def test_snapshot_identity(self):self.case(fail='snapshot_mismatch')
    def test_generation_ready(self):self.case(fail='not_ready')
    def test_source_digest(self):self.case(fail='digest')
    def test_missing_digest(self):self.case(fail='missing_digest')
    def test_source_container(self):self.case(fail='container')
    def test_existing_digest(self):self.case(fail='existing_digest',existing=True)
    def test_upload_failure(self):self.case(fail='upload')
    def test_database_failure(self):self.case(fail='db_write')

if __name__=='__main__':unittest.main()
