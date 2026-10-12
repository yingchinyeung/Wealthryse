"""Installed legacy review registration contract, not the newer local renderer."""
import contextlib,copy,hashlib,io,json,os,runpy,unittest
from pathlib import Path
from unittest.mock import Mock,patch


class RegistrationContract(unittest.TestCase):
    def setUp(self):
        self.main=runpy.run_path(str(Path(os.environ.get('QUALIFICATION_APP','/app'))/'scripts/register_historical_review_batch.py'),run_name='qualification_import')['main'];self.g=self.main.__globals__
        self.env={'DEPLOYMENT_ENVIRONMENT':'staging','DATABASE_URL':'postgresql://synthetic.invalid/test','PORTFOLIO_TENANT_ID':'synthetic-tenant',
            'AZURE_STORAGE_ACCOUNT_URL':'https://synthetic.invalid','PILOT_ACCOUNT_ID':'U16088571','REVIEW_MANIFEST_BLOB':'review/U16088571/manifest.json'}
        self.body=b'synthetic-content-not-a-real-report';digest=hashlib.sha256(self.body).hexdigest()
        self.manifest={'account':'U16088571','period_count':1,'artifacts':[{'period_end':'2026-09-30','snapshot_id':'synthetic-snapshot',
            'files':[{'report_type':typ,'output_reference':'internal-reports/review/U16088571/2026-09-30/synthetic.'+ext,
                'sha256':digest,'bytes':len(self.body)} for typ,ext in [('factsheet','pdf'),('presentation','pptx')]]}]}

    def case(self,env_change=None,fail=None):
        manifest=copy.deepcopy(self.manifest);env={**self.env,**(env_change or {})}
        if fail=='account':manifest['account']='other'
        if fail=='count':manifest['period_count']=2
        if fail=='period':manifest['artifacts'][0]['period_end']='2026-09-29'
        files=manifest['artifacts'][0]['files']
        if fail=='package':files.pop()
        if fail=='scope':files[0]['output_reference']=files[0]['output_reference'].replace('U16088571','other')
        if fail=='digest':files[0]['sha256']='0'*64
        if fail=='size':files[0]['bytes']+=1
        conn=Mock();connect=Mock(return_value=conn);writes=[]
        def execute(sql,args=()):
            normalized=' '.join(sql.split());value=None
            if normalized.startswith('SELECT snapshot_id'):value=None if fail=='snapshot' else {'snapshot_id':'synthetic-snapshot'}
            elif normalized.startswith('SELECT generation_run_id'):
                value=None if fail=='run' else {'generation_run_id':1,'status':fail if fail in ('published','released_internal') else 'awaiting_approval'}
            elif normalized.startswith('SELECT 1 FROM audit_events'):value=(1,) if fail=='existing_event' else None
            elif normalized.startswith(('UPDATE ','INSERT ')):
                writes.append((normalized,args))
                if fail=='write':raise RuntimeError('synthetic write failure')
            else:raise AssertionError('Unexpected SQL in preserved workflow')
            return Mock(fetchone=Mock(return_value=value))
        conn.execute.side_effect=execute
        def download(url,container,name):
            self.assertEqual(url,'https://synthetic.invalid');self.assertEqual(container,'internal-reports')
            if fail=='download':raise RuntimeError('synthetic storage failure')
            return json.dumps(manifest).encode() if name.endswith('manifest.json') else self.body
        store=Mock(side_effect=download);out=io.StringIO();error=bool(env_change or (fail and fail!='existing_event'))
        with patch.dict(os.environ,env,clear=True),patch.dict(self.g,connect=connect,download_blob_bytes=store),contextlib.redirect_stdout(out):
            if error:
                with self.assertRaises((RuntimeError,ValueError)):self.main()
            else:self.assertEqual(self.main(),0)
        if env_change or fail in ('account','count','download'):
            connect.assert_not_called();return
        self.assertEqual(connect.call_args.args[2],'synthetic-tenant');conn.close.assert_called_once()
        if error:
            conn.rollback.assert_called_once();conn.commit.assert_not_called()
            if fail!='write':self.assertFalse(writes)
        else:
            conn.commit.assert_called_once();conn.rollback.assert_not_called()
            self.assertEqual(json.loads(out.getvalue())['artifact_count'],2)
            updates=[x for x in writes if x[0].startswith('UPDATE')];self.assertEqual(len(updates),2)
            self.assertTrue(all("status = 'awaiting_approval'" in sql and args[-2]=='synthetic-tenant' for sql,args in updates))
            self.assertFalse(any('report_artifact_releases' in sql or 'report_release_decisions' in sql for sql,_ in writes))
            self.assertEqual(sum(sql.startswith('INSERT') for sql,_ in writes),0 if fail=='existing_event' else 1)

    def test_registration_awaits_owner_approval(self):self.case()
    def test_audit_event_not_duplicated(self):self.case(fail='existing_event')
    def test_environment_rejected(self):self.case({'DEPLOYMENT_ENVIRONMENT':'production'})
    def test_database_rejected(self):self.case({'DATABASE_URL':'sqlite:///test'})
    def test_storage_required(self):self.case({'AZURE_STORAGE_ACCOUNT_URL':''})
    def test_manifest_required(self):self.case({'REVIEW_MANIFEST_BLOB':''})
    def test_manifest_scope(self):self.case({'REVIEW_MANIFEST_BLOB':'review/other/manifest.json'})
    def test_account_mismatch(self):self.case(fail='account')
    def test_period_count(self):self.case(fail='count')
    def test_storage_failure(self):self.case(fail='download')
    def test_month_end(self):self.case(fail='period')
    def test_snapshot_missing(self):self.case(fail='snapshot')
    def test_package_incomplete(self):self.case(fail='package')
    def test_cross_account_artifact(self):self.case(fail='scope')
    def test_digest_mismatch(self):self.case(fail='digest')
    def test_length_mismatch(self):self.case(fail='size')
    def test_missing_generation_run(self):self.case(fail='run')
    def test_published_preserved(self):self.case(fail='published')
    def test_internal_release_preserved(self):self.case(fail='released_internal')
    def test_write_failure_rolls_back(self):self.case(fail='write')


if __name__=='__main__':unittest.main()
