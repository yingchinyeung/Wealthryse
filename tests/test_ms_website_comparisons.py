import copy
from dataclasses import replace
from datetime import date
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from ibkr_etl.benchmark_analytics import BenchmarkContract, build_comparison
from ibkr_etl.provisional_benchmark import BenchmarkSpec, convert_levels_to_reporting_currency
from ibkr_etl.website_benchmarks import (
    MS_HKD_COMPARISON_VERSION, require_ms_website_benchmarks,
    version_website_comparison, website_comparison_version,
)
from ms_website_runtime_environment import verify_env, normalized_env
from ibkr_etl import web, provisional_benchmark as provisional


def specs():
    return [BenchmarkSpec('GSPC','^GSPC','S&P 500','primary','USD','HKD'),
            BenchmarkSpec('HSI','^HSI','Hang Seng','secondary','HKD','HKD')]


class MSWebsiteComparisons(unittest.TestCase):
    def test_ms_failure_rolls_back_only_its_savepoint_and_never_commits(self):
        connection=Mock()
        with patch.object(provisional,'_refresh_and_materialize',side_effect=RuntimeError('HSI rejected')) as build:
            with self.assertRaisesRegex(RuntimeError,'HSI rejected'):
                provisional.refresh_and_materialize(connection,account_id='alpha-world-ms')
        self.assertEqual([c.args[0] for c in connection.execute.call_args_list],[
            'SAVEPOINT ms_website_benchmark_refresh',
            'ROLLBACK TO SAVEPOINT ms_website_benchmark_refresh',
            'RELEASE SAVEPOINT ms_website_benchmark_refresh'])
        connection.commit.assert_not_called()
        self.assertFalse(build.call_args.kwargs['commit'])

    def test_ms_pair_commits_once_only_after_success(self):
        connection=Mock()
        with patch.object(provisional,'_refresh_and_materialize',return_value={'status':'success'}):
            self.assertEqual(provisional.refresh_and_materialize(connection,account_id='alpha-world-ms'),{'status':'success'})
        connection.commit.assert_called_once_with()
        self.assertEqual(connection.execute.call_args.args[0],'RELEASE SAVEPOINT ms_website_benchmark_refresh')

    def test_other_subjects_keep_legacy_transaction_behavior(self):
        connection=Mock()
        with patch.object(provisional,'_refresh_and_materialize',return_value={}) as build:
            provisional.refresh_and_materialize(connection,account_id='U16088571')
        connection.execute.assert_not_called()
        self.assertNotIn('commit',build.call_args.kwargs)

    def test_hsi_is_required_even_though_it_is_secondary(self):
        connection=Mock()
        with patch.object(provisional,'_account_contract',return_value=('HKD','HKD',date(2025,1,20))), \
                patch.object(provisional,'mapped_benchmarks',return_value=specs()), \
                patch('ibkr_etl.subject_read_contract.require_ms_hkd_contract'), \
                patch.object(provisional,'_portfolio_values',return_value={'2026-10-06':100}), \
                patch.object(provisional,'_external_flows',return_value={}), \
                patch.object(provisional,'fetch_benchmark_yfinance',side_effect=[[('2026-10-06',100)],RuntimeError('empty')]):
            with self.assertRaisesRegex(RuntimeError,'Required benchmark HSI'):
                provisional.refresh_and_materialize(connection,account_id='alpha-world-ms')
        connection.commit.assert_not_called()

    def test_exact_two_index_contract(self):
        require_ms_website_benchmarks(specs())
        for changed in (specs()+[BenchmarkSpec('IXIC','^IXIC','Nasdaq','secondary','USD','HKD')],
                        [specs()[0]], [specs()[0],specs()[0]],
                        [replace(specs()[0],reporting_currency='USD'),specs()[1]],
                        [replace(specs()[0],return_convention='gross_total_return'),specs()[1]]):
            with self.subTest(specs=changed), self.assertRaises(ValueError):
                require_ms_website_benchmarks(changed)

    def test_ms_version_is_separate_and_other_subjects_unchanged(self):
        self.assertEqual(website_comparison_version('alpha-world-ms','HKD'),MS_HKD_COMPARISON_VERSION)
        self.assertEqual(website_comparison_version('U16088571','USD'),'benchmark-comparison-v1')
        with self.assertRaises(ValueError): website_comparison_version('alpha-world-t','USD')
        with self.assertRaises(ValueError): website_comparison_version('alpha-world-ms','USD')

    def test_conversion_changes_returns_not_just_label(self):
        levels,audit=convert_levels_to_reporting_currency(
            [('2026-09-21',100.0),('2026-09-22',110.0)],
            [('2026-09-21',7.8),('2026-09-22',7.9)])
        result=version_website_comparison(build_comparison(subject_id='alpha-world-ms',
            reporting_currency='HKD',portfolio_currency='HKD',
            portfolio_values={'2026-09-21':100,'2026-09-22':103},cash_flows={},
            benchmark=BenchmarkContract('GSPC','HKD','price','provisional_close','restricted'),
            benchmark_levels=levels,start_date='2026-09-21',expected_as_of='2026-09-22',
            minimum_return_observations=1,development_mode=True,required_return_convention='price'))
        self.assertEqual(result.calculation_version,MS_HKD_COMPARISON_VERSION)
        self.assertAlmostEqual(result.rows[1].benchmark_return,(110*7.9)/(100*7.8)-1)
        self.assertNotAlmostEqual(result.rows[1].benchmark_return,0.1)
        self.assertAlmostEqual(result.rows[1].normalized_benchmark,100*(110*7.9)/(100*7.8))
        self.assertEqual(audit[1]['fx_date'],'2026-09-22')

    def test_secret_projection_only_accepts_exact_existing_job_alias(self):
        old=[{'name':'DATABASE_URL','secretRef':'database-url'}]
        new=[{'name':'DATABASE_URL','secretRef':'cappjob-ibkrv2stg-onboard-render','value':''}]
        job={'identity':{'userAssignedIdentities':{'id':{}}},'properties':{
            'template':{'containers':[{'env':old}]},'configuration':{'secrets':[
                {'name':'database-url','keyVaultUrl':'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/database-app-url','identity':'id'}]}}}
        self.assertTrue(verify_env(old,new,job))
        self.assertEqual(new[0]['value'],'', 'Input must not be mutated')
        for change in ('alias','vault','identity','value'):
            wrong=copy.deepcopy(new); altered=copy.deepcopy(job)
            if change=='alias': wrong[0]['secretRef']='cappjob-another-job'
            elif change=='vault': altered['properties']['configuration']['secrets'][0]['keyVaultUrl']='wrong'
            elif change=='identity': altered['properties']['configuration']['secrets'][0]['identity']='wrong'
            else: wrong[0]['value']='unexpected'
            with self.subTest(change=change),self.assertRaises(ValueError): verify_env(old,wrong,altered)
        with self.assertRaises(ValueError): normalized_env(old+old)

    def test_actual_website_route_binds_tenant_currency_version_and_date(self):
        connection=Mock()
        connection.execute.return_value.fetchone.return_value={
            'benchmark_id':'GSPC','reporting_currency':'HKD','name':'S&P 500',
            'mapping_type':'primary','return_convention':'price'}
        connection.execute.return_value.fetchall.return_value=[
            {'observation_date':day,'portfolio_value':portfolio,'benchmark_level':benchmark,
             'normalized_portfolio':portfolio,'normalized_benchmark':benchmark,
             'portfolio_return':pr,'benchmark_return':br,'excess_return':er}
            for day,portfolio,benchmark,pr,br,er in (
                ('2026-09-21',100,100,None,None,None),('2026-09-22',103,104,.03,.04,-.01))]
        @contextmanager
        def database(): yield connection
        with patch.object(web,'_database',database),patch.object(web,'_portfolio_access_context'):
            response=web.benchmark_comparison('alpha-world-ms',benchmark_id='GSPC',
                as_of='2026-09-22',user={'email':'owner-fixture'})
        sql,params=connection.execute.call_args.args
        self.assertIn('tenant_id = %s',sql)
        self.assertIn('reporting_currency = %s',sql)
        self.assertIn('calculation_version = %s',sql)
        self.assertEqual(params,(web.TENANT_ID,'alpha-world-ms','GSPC',
            MS_HKD_COMPARISON_VERSION,'HKD','2026-09-22','2026-09-22'))
        self.assertEqual(response['reporting_currency'],'HKD')
        self.assertEqual(response['calculation_version'],MS_HKD_COMPARISON_VERSION)
        self.assertEqual(len(response['series']),2)


if __name__=='__main__': unittest.main()
