"""Installed historical presentation workflow boundaries; synthetic data only."""
import contextlib,hashlib,io,json,os,runpy,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch


class PresentationContracts(unittest.TestCase):
    def setUp(self):
        self.m=runpy.run_path('/app/scripts/upgrade_historical_presentations_v23.py',run_name='contract_import');self.g=self.m['main'].__globals__
        self.env={'DEPLOYMENT_ENVIRONMENT':'staging','UPGRADE_MODE':'qa','DATABASE_URL':'postgresql://fake.invalid/test','PORTFOLIO_TENANT_ID':'tenant','AZURE_STORAGE_ACCOUNT_URL':'https://fake.invalid','PRESENTATION_TEMPLATE_VERSION':'test','REPORT_REPLACEMENT_APPROVED_BY':'owner@example.invalid','QA_MANIFEST_BLOB':'qa-v23/U16088571/test.json'}
        self.conn=MagicMock();self.connect=MagicMock(return_value=self.conn);self.service=MagicMock();self.blob=MagicMock(return_value=self.service);self.qa=MagicMock(return_value={'status':'QA_READY'});self.publish=MagicMock(return_value={'status':'PASS'})
    def invoke(self):
        with patch.dict(os.environ,self.env,clear=True),patch.dict(self.g,connect=self.connect,_blob_service=self.blob,_run_qa=self.qa,_run_publish=self.publish),contextlib.redirect_stdout(io.StringIO()) as out:value=self.m['main']()
        return value,json.loads(out.getvalue())
    def test_qa_mode(self):
        value,result=self.invoke();self.assertEqual(value,0);self.assertEqual(result['status'],'QA_READY');self.qa.assert_called_once();self.publish.assert_not_called();self.conn.close.assert_called_once()
    def test_publish_mode(self):
        self.env['UPGRADE_MODE']='publish';self.invoke();self.publish.assert_called_once();self.qa.assert_not_called()
    def reject(self,key,value):
        self.env[key]=value
        with self.assertRaises(RuntimeError):self.invoke()
        self.connect.assert_not_called()
    def test_environment(self):self.reject('DEPLOYMENT_ENVIRONMENT','production')
    def test_unknown_mode(self):self.reject('UPGRADE_MODE','unreviewed')
    def test_database_scheme(self):self.reject('DATABASE_URL','sqlite:///test')
    def test_tenant_required(self):self.reject('PORTFOLIO_TENANT_ID','')
    def test_storage_required(self):self.reject('AZURE_STORAGE_ACCOUNT_URL','')
    def test_qa_failure(self):
        self.qa.side_effect=RuntimeError('synthetic failure')
        with self.assertRaises(RuntimeError):self.invoke()
        self.conn.rollback.assert_called_once();self.conn.close.assert_called_once()
    def test_render_dispatch(self):
        with patch.object(self.g['subprocess'],'run',return_value=SimpleNamespace(returncode=0)) as run:self.m['_render'](Path('/tmp/t'),Path('/tmp/s'),Path('/tmp/o'))
        self.assertEqual(run.call_args.args[0][-3:],['/tmp/t','/tmp/s','/tmp/o']);self.assertEqual(run.call_args.kwargs['timeout'],420)
    def test_render_failure(self):
        with patch.object(self.g['subprocess'],'run',return_value=SimpleNamespace(returncode=1,stderr='synthetic error',stdout='')),self.assertRaises(RuntimeError):self.m['_render'](Path('/tmp/t'),Path('/tmp/s'),Path('/tmp/o'))
    def rows(self):return [{'period_end':d,'snapshot_id':'synthetic-'+d} for d in self.m['EXPECTED_PERIODS']]
    def test_all_periods(self):
        self.conn.execute.return_value.fetchall.return_value=self.rows();self.assertEqual(len(self.m['_active_snapshots'](self.conn)),20)
    def test_missing_period(self):
        self.conn.execute.return_value.fetchall.return_value=self.rows()[:-1]
        with self.assertRaises(RuntimeError):self.m['_active_snapshots'](self.conn)
    def test_duplicate_period(self):
        self.conn.execute.return_value.fetchall.return_value=self.rows()+self.rows()[:1]
        with self.assertRaises(RuntimeError):self.m['_active_snapshots'](self.conn)
    def augment(self,good_hash=True,available=True):
        payload='{"synthetic":true}';row={'period_end':'2026-08-31','snapshot_id':'synthetic','snapshot_payload_json':payload,'data_sha256':hashlib.sha256(payload.encode()).hexdigest() if good_hash else '0'*64}
        with patch.dict(self.g,build_monthly_reporting_analytics=MagicMock(return_value={'quality_status':'pass' if available else 'fail','data':{'available':available}})):return self.m['_augment_snapshot'](self.conn,row)
    def test_augmentation(self):
        payload,digest=self.augment();self.assertTrue(payload['monthly_reporting']['available']);self.assertEqual(len(digest),64)
    def test_augmentation_hash(self):
        with self.assertRaises(RuntimeError):self.augment(good_hash=False)
    def test_unavailable_analytics(self):
        with self.assertRaises(RuntimeError):self.augment(available=False)
    def manifest(self):return {'account':self.m['ACCOUNT_ID'],'layout_version':self.m['LAYOUT_VERSION'],'template_sha256':self.m['TEMPLATE_SHA256'],'period_count':20,'factsheet_changed':False,'artifacts':[{'period_end':d,'blob_name':'qa-v23/U16088571/test.pptx','sha256':'0'*64,'bytes':1,'snapshot_id':'s','augmentation_sha256':'a'} for d in self.m['EXPECTED_PERIODS']]}
    def test_manifest_scope(self):
        for key,value in [('account','other'),('layout_version','other'),('template_sha256','0'),('period_count',19),('factsheet_changed',True)]:
            data=self.manifest();data[key]=value;self.service.get_blob_client.return_value.download_blob.return_value.readall.return_value=json.dumps(data).encode()
            with self.subTest(key=key),patch.dict(os.environ,self.env,clear=True),self.assertRaises(RuntimeError):self.m['_run_publish'](self.conn,self.service,container='fake',tenant_id='tenant')
        self.conn.execute.assert_not_called()
    def test_manifest_blob_scope(self):
        data=self.manifest();data['artifacts'][0]['blob_name']='released/other/test.pptx';self.service.get_blob_client.return_value.download_blob.return_value.readall.return_value=json.dumps(data).encode()
        with patch.dict(os.environ,self.env,clear=True),self.assertRaises(RuntimeError):self.m['_run_publish'](self.conn,self.service,container='fake',tenant_id='tenant')
        self.conn.execute.assert_not_called()
    def test_artifact_hash(self):
        data=self.manifest();self.service.get_blob_client.return_value.download_blob.return_value.readall.side_effect=[json.dumps(data).encode(),b'x']
        with patch.dict(os.environ,self.env,clear=True),self.assertRaises(RuntimeError):self.m['_run_publish'](self.conn,self.service,container='fake',tenant_id='tenant')
        self.conn.execute.assert_not_called()
    def test_template_identity(self):
        path=Path('/app/templates/U16088571/approved-august-2026-20-slide-v23.pptx');self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),self.m['TEMPLATE_SHA256'])


if __name__=='__main__':unittest.main()
