"""Synthetic acceptance of the installed historical verification contract."""
import contextlib,io,json,os,re,runpy,unittest
from unittest.mock import MagicMock,Mock,patch

class VerifyContract(unittest.TestCase):
    def setUp(self):
        self.main=runpy.run_path('/app/scripts/verify_pilot_staging.py',run_name='qualification_import')['main'];self.g=self.main.__globals__
    def case(self,fail=None):
        conn=MagicMock();conn.__enter__.return_value=conn;queries=[]
        def execute(sql,args=()):
            q=' '.join(sql.split());queries.append((q,args));values=[];row=None
            if q.startswith('SELECT set_config'):pass
            elif q.startswith('SELECT version'):
                names=['001_init.sql','002_platform_reporting.sql','003_pilot_benchmark_contract.sql','004_provisional_benchmark_feed.sql','005_sp500_total_return_primary.sql','006_price_index_benchmarks.sql','007_crisis_holdings_replay.sql']
                values=[{'version':n} for n in names[1:] if fail=='migrations'] if fail=='migrations' else [{'version':n} for n in names]
            elif 'JOIN portfolio_benchmark_mapping' in q:
                values=[{'benchmark_id':b,'symbol':s,'name':b,'subject_id':'test-account','provider':'Yahoo Finance via yfinance','return_convention':'price',
                    'currency':'USD','license_status':'restricted','mapping_type':m,'effective_from':'2025-01-20','reporting_currency':'USD'} for b,s,m in [('GSPC','^GSPC','primary'),('IXIC','^IXIC','secondary')]]
                if fail=='benchmark':values[0]['currency']='HKD'
                if fail=='license':values[0]['license_status']='unrestricted'
            elif 'FROM benchmark_master' in q:values=[{'benchmark_id':b,'active':fail=='retired','mappings':0} for b in ('XNDX','SP500TR')]
            elif 'FROM benchmark_prices' in q or 'FROM performance_comparison_daily' in q:
                values=[{'benchmark_id':b,'observations':300,'first':'2025-01-21','latest':'2026-09-18','minimum_quality':'warn','maximum_quality':'warn'} for b in ('GSPC','IXIC')]
                if fail=='benchmark_history' and 'FROM benchmark_prices' in q:values[0]['observations']=2
                if fail=='comparison' and 'FROM performance_comparison_daily' in q:values[0]['latest']='2026-09-17'
            elif q.startswith('SELECT account_id'):values=[{'account_id':'other' if fail=='account' else 'test-account'}]
            elif q.startswith('SELECT COUNT'):
                table=re.search(r'FROM "([^"]+)"',q).group(1);row={'n':self.g['EXPECTED_COUNTS'][table]+(1 if fail=='counts' else 0)}
            elif q.startswith('SELECT MIN'):
                table=re.search(r'FROM "([^"]+)"',q).group(1);_,first,last=self.g['EXPECTED_COVERAGE'][table];row={'first':first,'last':'2026-01-01' if fail=='coverage' else last}
            elif q.startswith('SELECT to_regclass'):row={'table_name':None if fail=='table' else args[0]}
            elif 'FROM pg_class' in q:values=[{'relname':n,'relrowsecurity':fail!='rls','relforcerowsecurity':True} for n in args[0]]
            elif 'FROM ingestion_runs' in q:row=None if fail=='ingestion' else {'run_id':1,'status':'success','row_count':1,'reconciliation_json':'{}'}
            else:raise AssertionError('Unexpected or mutating SQL: '+q)
            result=MagicMock();result.__iter__.return_value=iter(values);result.fetchall.return_value=values;result.fetchone.return_value=row;return result
        conn.execute.side_effect=execute;out=io.StringIO()
        env={'DATABASE_URL':'sqlite:///test' if fail=='database' else 'postgresql://synthetic.invalid/test','PORTFOLIO_TENANT_ID':'test-tenant','PILOT_ACCOUNT_ID':'test-account'}
        with patch.dict(os.environ,env,clear=True),patch('psycopg.connect',return_value=conn) as db,contextlib.redirect_stdout(out):
            if fail=='database':
                with self.assertRaises(RuntimeError):self.main()
                db.assert_not_called();return
            self.assertEqual(self.main(),1 if fail else 0)
        data=json.loads(out.getvalue());self.assertEqual(data['status'],'FAIL' if fail else 'PASS')
        self.assertEqual(bool(data['failures']),bool(fail));self.assertEqual(queries[0][1],('test-tenant',))
        self.assertTrue(all(q.startswith('SELECT ') for q,_ in queries));conn.commit.assert_not_called()
    def test_accepted_original_contract(self):self.case()
    def test_database_type(self):self.case('database')
    def test_migration_drift(self):self.case('migrations')
    def test_benchmark_currency(self):self.case('benchmark')
    def test_license_status(self):self.case('license')
    def test_retired_benchmark(self):self.case('retired')
    def test_benchmark_history(self):self.case('benchmark_history')
    def test_comparison_tail(self):self.case('comparison')
    def test_account_scope(self):self.case('account')
    def test_row_counts(self):self.case('counts')
    def test_date_coverage(self):self.case('coverage')
    def test_missing_table(self):self.case('table')
    def test_row_level_security(self):self.case('rls')
    def test_ingestion_status(self):self.case('ingestion')

if __name__=='__main__':unittest.main()
