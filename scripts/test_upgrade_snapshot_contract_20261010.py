"""Synthetic entrypoint preservation tests; no real storage or database calls."""
import contextlib,hashlib,io,json,os,runpy,unittest
from pathlib import Path
from unittest.mock import Mock,patch


class SnapshotContract(unittest.TestCase):
    def setUp(self):
        self.main=runpy.run_path(str(Path(os.environ.get('QUALIFICATION_APP','/app'))/'scripts/freeze_month_end_snapshot.py'),run_name='qualification_import')['main']
        self.g=self.main.__globals__
        self.env={'DEPLOYMENT_ENVIRONMENT':'staging','DATABASE_URL':'postgresql://synthetic.invalid/test',
            'PORTFOLIO_TENANT_ID':'synthetic-tenant','PILOT_ACCOUNT_ID':'synthetic-subject','REPORTING_PERIOD_START':'2025-01-20',
            'REPORTING_PERIOD_END':'2026-09-30','REPORTING_CURRENCY':'HKD','AZURE_STORAGE_ACCOUNT_URL':'https://synthetic.invalid'}

    def case(self,change=None,failure=None,uploaded=True):
        env={**self.env,**(change or {})};conn=Mock();connect=Mock(return_value=conn)
        result=Mock(snapshot_id='synthetic-snapshot',period_end='2026-09-30')
        result.as_dict.return_value={'snapshot_id':'synthetic-snapshot','publication_allowed':False}
        freeze=Mock(return_value=result);payload='{"synthetic":true}'
        row={'snapshot_payload_json':payload,'data_sha256':hashlib.sha256(payload.encode()).hexdigest()}
        if failure=='missing':row=None
        if failure=='digest':row['data_sha256']='0'*64
        conn.execute.return_value.fetchone.return_value=row
        upload=Mock(return_value='https://synthetic.invalid/file' if uploaded else None)
        if failure=='freeze':freeze.side_effect=RuntimeError('synthetic freeze failure')
        if failure=='upload':upload.side_effect=RuntimeError('synthetic upload failure')
        output=io.StringIO()
        with patch.dict(os.environ,env,clear=True),patch.dict(self.g,connect=connect,freeze_month_end_snapshot=freeze,upload_bytes=upload),contextlib.redirect_stdout(output):
            if failure or change:
                with self.assertRaises((RuntimeError,KeyError)):self.main()
            else:self.assertEqual(self.main(),0)
        conn.commit.assert_not_called() # Freeze owns its transaction; wrapper must not add another.
        if change:connect.assert_not_called();upload.assert_not_called();return
        self.assertEqual(connect.call_args.args[2],'synthetic-tenant')
        self.assertEqual(freeze.call_args.kwargs,{'tenant_id':'synthetic-tenant','subject_id':'synthetic-subject',
            'period_start':'2025-01-20','period_end':'2026-09-30','reporting_currency':'HKD'})
        conn.close.assert_called_once()
        if failure:
            conn.rollback.assert_called_once()
            if failure!='upload':upload.assert_not_called()
        else:
            conn.rollback.assert_not_called();upload.assert_called_once()
            self.assertEqual(upload.call_args.args,('https://synthetic.invalid','report-drafts','snapshots/synthetic-subject/2026-09-30/synthetic-snapshot.json',payload.encode()))
            self.assertEqual(upload.call_args.kwargs,{'overwrite':True,'content_type':'application/json; charset=utf-8'})
            value=json.loads(output.getvalue());self.assertEqual(value['snapshot_blob_uploaded'],uploaded)
            self.assertEqual(value['publication_allowed'],False)
            self.assertEqual(conn.execute.call_args.args[1],('synthetic-tenant','synthetic-snapshot','synthetic-subject'))

    def test_hkd_success(self):self.case()
    def test_upload_not_configured_is_reported(self):self.case(uploaded=False)
    def test_other_environment_rejected(self):self.case({'DEPLOYMENT_ENVIRONMENT':'production'})
    def test_missing_environment_rejected(self):self.case({'DEPLOYMENT_ENVIRONMENT':''})
    def test_missing_database_rejected(self):self.case({'DATABASE_URL':''})
    def test_sqlite_rejected(self):self.case({'DATABASE_URL':'sqlite:///test'})
    def test_freeze_failure_rolls_back(self):self.case(failure='freeze')
    def test_missing_frozen_row_rejected(self):self.case(failure='missing')
    def test_corrupt_payload_rejected(self):self.case(failure='digest')
    def test_storage_failure_rolls_back(self):self.case(failure='upload')
    def test_month_end_validation(self):
        from ibkr_etl.reporting_snapshot import _require_calendar_month_end
        self.assertEqual(str(_require_calendar_month_end('2024-02-29')),'2024-02-29')
        for value in ('2026-09-29','2026-02-29','bad'):
            with self.subTest(value=value),self.assertRaises(ValueError):_require_calendar_month_end(value)
    def test_monthly_return_aggregation(self):
        from ibkr_etl.reporting_snapshot import _month_end_points
        value=_month_end_points([{'date':'2026-08-30','portfolio':99,'benchmark':49},
            {'date':'2026-08-31','portfolio':100,'benchmark':50},{'date':'2026-09-30','portfolio':105,'benchmark':49}])
        self.assertIsNone(value[0]['portfolio_monthly_return'])
        self.assertAlmostEqual(value[1]['portfolio_monthly_return'],.05)
        self.assertAlmostEqual(value[1]['benchmark_monthly_return'],-.02)


if __name__=='__main__':unittest.main()
