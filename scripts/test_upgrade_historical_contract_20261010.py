"""Synthetic-only tests of the installed historical snapshot/queue workflow."""
import contextlib,hashlib,io,json,os,runpy,unittest
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
from ibkr_etl import historical_reporting as h


class HistoricalContracts(unittest.TestCase):
    def setUp(self):
        self.m=runpy.run_path('/app/scripts/generate_historical_month_end_batch.py',run_name='contract_import');self.g=self.m['main'].__globals__
        self.env={'DEPLOYMENT_ENVIRONMENT':'staging','ALLOW_UNLICENSED_BENCHMARK':'true','YAHOO_HISTORICAL_HOLDINGS_APPROVED':'true',
            'DATABASE_URL':'postgresql://fake.invalid/test','PORTFOLIO_TENANT_ID':'tenant','PILOT_ACCOUNT_ID':'synthetic',
            'HISTORICAL_REPORT_START':'2025-01-20','HISTORICAL_REPORT_END':'2025-02-28','REPORTING_PERIOD_START':'2025-01-20',
            'REPORTING_CURRENCY':'HKD','FACTSHEET_TEMPLATE_VERSION':'f1','PRESENTATION_TEMPLATE_VERSION':'p1','AZURE_STORAGE_ACCOUNT_URL':'https://fake.invalid'}
        self.conn=MagicMock();self.payload='{"synthetic":true}'
        self.conn.execute.return_value.fetchone.return_value={'snapshot_payload_json':self.payload,'data_sha256':hashlib.sha256(self.payload.encode()).hexdigest()}
        self.result={'items':[{'snapshot_id':'synthetic-id','period_end':'2025-01-31'}]}
        self.connect=MagicMock(return_value=self.conn);self.classify=MagicMock(return_value={'unclassified':0});self.batch=MagicMock(return_value=self.result);self.upload=MagicMock(return_value='fake-upload')
    def invoke(self):
        with patch.dict(os.environ,self.env,clear=True),patch.dict(self.g,connect=self.connect,refresh_sector_classifications_range=self.classify,run_historical_month_end_batch=self.batch,upload_bytes=self.upload),contextlib.redirect_stdout(io.StringIO()) as out:
            value=self.m['main']()
        return value,json.loads(out.getvalue())
    def test_success(self):
        value,data=self.invoke();self.assertEqual(value,0);self.assertEqual(data['snapshots_uploaded'],1)
        self.assertEqual(self.batch.call_args.kwargs['reporting_currency'],'HKD');self.conn.close.assert_called_once();self.conn.rollback.assert_not_called()
        args,kw=self.upload.call_args;self.assertEqual(args[2],'snapshots/synthetic/2025-01-31/synthetic-id.json');self.assertEqual(args[3],self.payload.encode());self.assertTrue(kw['overwrite'])
    def rejected_env(self,key,value):
        self.env[key]=value
        with self.assertRaises(RuntimeError):self.invoke()
        self.connect.assert_not_called();self.upload.assert_not_called()
    def test_environment(self):self.rejected_env('DEPLOYMENT_ENVIRONMENT','production')
    def test_benchmark_optin(self):self.rejected_env('ALLOW_UNLICENSED_BENCHMARK','false')
    def test_transmission_optin(self):self.rejected_env('YAHOO_HISTORICAL_HOLDINGS_APPROVED','false')
    def test_database_scheme(self):self.rejected_env('DATABASE_URL','sqlite:///test')
    def test_classification_incomplete(self):
        self.classify.return_value={'unclassified':1,'unclassified_symbols':['FAKE']}
        with self.assertRaises(RuntimeError):self.invoke()
        self.batch.assert_not_called();self.upload.assert_not_called();self.conn.rollback.assert_called_once();self.conn.close.assert_called_once()
    def test_batch_failure(self):
        self.batch.side_effect=RuntimeError('synthetic failure')
        with self.assertRaises(RuntimeError):self.invoke()
        self.upload.assert_not_called();self.conn.rollback.assert_called_once()
    def test_missing_snapshot(self):
        self.conn.execute.return_value.fetchone.return_value=None
        with self.assertRaises(RuntimeError):self.invoke()
        self.upload.assert_not_called();self.conn.rollback.assert_called_once()
    def test_snapshot_hash(self):
        self.conn.execute.return_value.fetchone.return_value['data_sha256']='0'*64
        with self.assertRaises(RuntimeError):self.invoke()
        self.upload.assert_not_called()
    def test_upload_failure(self):
        self.upload.side_effect=RuntimeError('fake storage failure')
        with self.assertRaises(RuntimeError):self.invoke()
        self.conn.rollback.assert_called_once();self.conn.close.assert_called_once()
    def test_no_storage_result(self):
        self.upload.return_value=None;_,data=self.invoke();self.assertEqual(data['snapshots_uploaded'],0);self.assertFalse(data['items'][0]['snapshot_blob_uploaded'])
    def test_missing_template(self):
        del self.env['FACTSHEET_TEMPLATE_VERSION']
        with self.assertRaises(KeyError):self.invoke()
        self.batch.assert_not_called();self.conn.rollback.assert_called_once()
    def test_months(self):
        with patch.object(h,'discover_replay_dates',return_value=['2025-01-20','2025-01-31','2025-02-27']):
            result=h.discover_historical_months(self.conn,account_id='synthetic',start_date='2025-01-20',end_date='2025-02-28')
        self.assertEqual([(x.period_end,x.holdings_as_of) for x in result],[('2025-01-31','2025-01-31'),('2025-02-28','2025-02-27')])
    def test_missing_month(self):
        with patch.object(h,'discover_replay_dates',return_value=['2025-01-31']),self.assertRaises(h.HistoricalReportingError):h.discover_historical_months(self.conn,account_id='synthetic',start_date='2025-01-20',end_date='2025-02-28')
    def test_reversed_dates(self):
        with self.assertRaises(h.HistoricalReportingError):h.discover_historical_months(self.conn,account_id='synthetic',start_date='2025-03-20',end_date='2025-02-28')
    def queue(self):return h.queue_report_generation_run(self.conn,tenant_id='tenant',snapshot_id='s',report_type='factsheet',template_id='f',template_version='v')
    def test_existing_queue(self):
        self.conn.execute.return_value.fetchone.return_value={'generation_run_id':23,'status':'awaiting_approval'};self.assertEqual(self.queue(),23);self.assertEqual(self.conn.execute.call_count,1)
    def test_retry_failed_queue(self):
        self.conn.execute.return_value.fetchone.return_value={'generation_run_id':23,'status':'failed'};self.assertEqual(self.queue(),23);self.assertEqual(self.conn.execute.call_count,2);self.assertIn("status = 'queued'",self.conn.execute.call_args.args[0])
    def test_new_queue(self):
        self.conn.execute.return_value.fetchone.side_effect=[None,{'generation_run_id':24}];self.assertEqual(self.queue(),24);self.assertIn('INSERT INTO report_generation_runs',self.conn.execute.call_args.args[0])
    def test_invalid_report_type(self):
        with self.assertRaises(h.HistoricalReportingError):h.queue_report_generation_run(self.conn,tenant_id='tenant',snapshot_id='s',report_type='unapproved',template_id='f',template_version='v')
        self.conn.execute.assert_not_called()
    def test_month_end_replay_scope(self):
        months=[h.HistoricalMonth('2025-01','2025-01-31','2025-01-31'),h.HistoricalMonth('2025-02','2025-02-28','2025-02-27')]
        replay=MagicMock(side_effect=lambda *a,**kw:{'holdings_as_of':kw['holdings_as_of']});freeze=MagicMock(return_value=SimpleNamespace(snapshot_id='s',data_as_of='2025-02-27',publication_allowed=False,publication_blockers=('license',)))
        with patch.object(h,'discover_historical_months',return_value=months),patch.object(h,'queue_report_generation_run',return_value=24):
            result=h.run_historical_month_end_batch(self.conn,tenant_id='tenant',account_id='synthetic',period_start='2025-01-20',start_date='2025-01-20',end_date='2025-02-28',reporting_currency='HKD',factsheet_template_id='f',factsheet_template_version='1',presentation_template_id='p',presentation_template_version='1',replay_refresher=replay,snapshot_freezer=freeze)
        self.assertEqual(replay.call_count,2);self.assertEqual(self.conn.commit.call_count,2);self.assertEqual(result['report_runs_queued'],4);self.assertFalse(result['items'][0]['publication_allowed'])


if __name__=='__main__':unittest.main()
