"""Execute consumer SQL against coexisting USD/HKD observations, not SQL mocks."""
import copy
import json
import re
import sqlite3
import unittest
from unittest.mock import patch

from ibkr_etl.agent_tools import AgentToolContext, AgentToolError, execute_tool
from ibkr_etl.subject_onboarding import approved_reference_definitions, configuration_sha256
from ibkr_etl.subject_read_contract import require_ms_hkd_contract, synthetic_exposure
from ibkr_etl.reporting_snapshot import assemble_month_end_payload, ReportingSnapshotError


class Cursor:
    def __init__(self,cursor):self.cursor=cursor
    def fetchone(self):
        row=self.cursor.fetchone()
        return dict(row) if row is not None else None
    def fetchall(self):return [dict(r) for r in self.cursor.fetchall()]
    def __iter__(self):return iter(self.fetchall())


class Fixture:
    def __init__(self):
        self.raw=sqlite3.connect(':memory:')
        self.raw.row_factory=sqlite3.Row
        self.raw.executescript('''
            CREATE TABLE platform_subjects(tenant_id,subject_id,subject_type,base_currency,reporting_currency,
                history_start_date,display_name,updated_at);
            CREATE TABLE derived_portfolios(tenant_id,portfolio_id,base_currency);
            CREATE TABLE derived_portfolio_cash_ledger(portfolio_id,event_date,amount_base,included,external_flow);
            CREATE TABLE subject_calculation_definitions(tenant_id,subject_id,configuration_json,
                configuration_sha256,approval_status,effective_to);
            CREATE TABLE subject_nav_series(tenant_id,subject_id,snapshot_date,currency,total_text);
            CREATE TABLE synthetic_portfolio_components(tenant_id,portfolio_id,snapshot_date,component_id,
                component_name,component_type,base_currency,source_currency,coefficient,source_value,
                fx_rate_to_base,base_value,normalized_value,contribution,direction,observation_date);
            CREATE TABLE subject_position_series(subject_id,snapshot_date);
            CREATE TABLE subject_cash_balance_series(subject_id,snapshot_date);
            CREATE TABLE risk_free_rates(tenant_id,rate_date,series_id,currency,tenor,annual_rate,quality_status);
            CREATE TABLE portfolio_benchmark_mapping(subject_id,benchmark_id,mapping_type,reporting_currency,
                effective_from,effective_to);
            CREATE TABLE benchmark_prices(benchmark_id,observation_date,quality_status);
            CREATE TABLE ingestion_runs(run_id,run_type,completed_at,status,row_count);
            CREATE TABLE benchmark_master(benchmark_id,symbol,name,provider,return_convention,license_status,active);
            CREATE TABLE performance_comparison_daily(subject_id,benchmark_id,calculation_version,
                quality_status,observation_date,reporting_currency,portfolio_value,benchmark_level,
                normalized_portfolio,normalized_benchmark,portfolio_return,benchmark_return,excess_return);
        ''')
        self.raw.execute("INSERT INTO platform_subjects VALUES ('tenant','alpha-world-ms','composite_portfolio','HKD','HKD','2025-01-20','Alpha World MS','2025-01-21')")
        self.raw.execute("INSERT INTO derived_portfolios VALUES ('tenant','alpha-world-ms','HKD')")
        definition=approved_reference_definitions()['alpha-world-ms']
        self.raw.execute('INSERT INTO subject_calculation_definitions VALUES (?,?,?,?,?,NULL)',
            ('tenant','alpha-world-ms',json.dumps(definition),configuration_sha256(definition),'approved'))
        for day,hkd in [('2025-01-20',100),('2025-01-21',103),('2025-01-22',102)]:
            for currency,nav in [('USD',999),('HKD',hkd)]:
                self.raw.execute('INSERT INTO subject_nav_series VALUES (?,?,?,?,?)',('tenant','alpha-world-ms',day,currency,str(nav)))
            self.raw.execute("INSERT INTO synthetic_portfolio_components VALUES ('tenant','alpha-world-ms',?,'leg','Example','account','HKD','USD',.45,400,7.8,3120,100,45,'long',?)",(day,day))
            self.raw.execute("INSERT INTO risk_free_rates VALUES ('tenant',?,'HKAB-HIBOR-1M','HKD','1-month',.03,'pass')",(day,))
        # A newer USD record must not move the HKD as-of date or coverage.
        self.raw.execute("INSERT INTO subject_nav_series VALUES ('tenant','alpha-world-ms','2025-01-23','USD','999')")
        self.raw.execute("INSERT INTO portfolio_benchmark_mapping VALUES ('alpha-world-ms','GSPC','primary','HKD','2025-01-20',NULL)")
        self.raw.execute("INSERT INTO benchmark_master VALUES ('GSPC','GSPC','Comparison only','fixture','price','approved',1)")
        for ccy,nav in [('USD',999),('HKD',103)]:
            self.raw.execute("INSERT INTO performance_comparison_daily VALUES ('alpha-world-ms','GSPC','benchmark-comparison-v1','pass','2025-01-21',?, ?,100,?,100,.03,0,.03)",(ccy,nav,nav))
        self.calls=[]

    def execute(self,sql,params=()):
        self.calls.append((sql,tuple(params)))
        sql=sql.replace('%s','?')
        sql=re.sub(r'([\w.]+|\?)::(?:text|numeric)',r'\1',sql)
        return Cursor(self.raw.execute(sql,params))


class SubjectReadTests(unittest.TestCase):
    def setUp(self):
        self.connection=Fixture()
        self.context=AgentToolContext('tenant','owner',frozenset({'alpha-world-ms'}),'test')
        self.addCleanup(self.connection.raw.close)

    def tool(self,name):
        return execute_tool(self.connection,self.context,name,{'subject_id':'alpha-world-ms'})

    def test_performance_and_summary_use_only_active_hkd_history(self):
        performance=self.tool('get_performance_series')
        self.assertEqual(performance['as_of'],'2025-01-22')
        self.assertEqual([r['nav'] for r in performance['data']['series']],[100,103,102])
        self.assertEqual({r['currency'] for r in performance['data']['series']},{'HKD'})
        summary=self.tool('get_portfolio_summary')
        self.assertEqual(summary['data']['nav_coverage']['observations'],3)
        self.assertEqual(summary['data']['latest_nav']['currency'],'HKD')
        self.assertEqual(summary['data']['latest_nav']['total_text'],'102')

    def test_benchmark_comparison_filters_currency_without_relabelling_index(self):
        result=self.tool('compare_with_benchmarks')
        self.assertEqual(len(result['data']['series']),1)
        self.assertEqual(result['data']['series'][0]['portfolio'],103)
        self.assertEqual(result['data']['benchmark']['benchmark_id'],'GSPC')
        self.connection.raw.execute("UPDATE portfolio_benchmark_mapping SET reporting_currency='USD'")
        with self.assertRaises(AgentToolError):self.tool('compare_with_benchmarks')

    def test_holdings_are_reconciled_exposures_not_share_quantities(self):
        row=self.tool('get_holdings')['data']['holdings'][0]
        self.assertEqual(row['holding_kind'],'synthetic_exposure')
        self.assertIsNone(row['quantity_text']);self.assertIsNone(row['mark_price_text'])
        self.assertEqual(row['coefficient'],.45)
        self.assertAlmostEqual(row['coefficient']*row['normalized_value'],float(row['market_value_text']))
        self.connection.raw.execute('UPDATE synthetic_portfolio_components SET contribution=999')
        with self.assertRaises(AgentToolError):self.tool('get_holdings')

    def test_freshness_uses_complete_hibor_period_not_treasury_date(self):
        self.assertEqual(self.tool('get_data_freshness')['quality_status'],'pass')
        self.connection.raw.execute("DELETE FROM risk_free_rates WHERE rate_date='2025-01-21'")
        result=self.tool('get_data_freshness')
        self.assertEqual(result['quality_status'],'fail')
        self.assertIsNone(result['data']['risk_free_date'])
        self.assertEqual(result['citations'][2]['currency'],'HKD')

    def test_drifted_metadata_contract_hash_and_wrong_tenant_fail_closed(self):
        for statement in ("UPDATE platform_subjects SET base_currency='USD'",
                "UPDATE platform_subjects SET reporting_currency='USD'",
                "UPDATE derived_portfolios SET base_currency='USD'",
                "UPDATE subject_calculation_definitions SET approval_status='draft'",
                "UPDATE subject_calculation_definitions SET configuration_sha256='invalid'"):
            fixture=Fixture()
            try:
                fixture.raw.execute(statement)
                with self.assertRaises(ValueError):require_ms_hkd_contract(fixture,tenant_id='tenant')
            finally:fixture.raw.close()
        with self.assertRaises(ValueError):require_ms_hkd_contract(self.connection,tenant_id='other')

    def test_website_performance_dates_summary_and_exposure_share_currency_contract(self):
        from ibkr_etl import web
        metadata={'reporting_currency':'HKD','base_currency':'HKD','history_start_date':'2025-01-20','subject_type':'composite_portfolio'}
        with patch.object(web,'TENANT_ID','tenant'),patch.object(web,'_portfolio_access_context',return_value=metadata),patch.object(web,'_database') as database:
            database.return_value.__enter__.return_value=self.connection
            user={'email':'owner'}
            self.assertEqual(len(web.performance('alpha-world-ms',user=user)['series']),3)
            self.assertEqual(web.available_dates('alpha-world-ms',user=user)['latest'],'2025-01-22')
            self.assertEqual(web.portfolio_summary('alpha-world-ms',user=user)['latest_nav']['currency'],'HKD')
            self.assertIsNone(web.holdings('alpha-world-ms',user=user)['holdings'][0]['quantity_text'])

    def test_report_payload_rejects_currency_or_numeric_relabelling(self):
        from ibkr_etl.alpha_world_ms_analytics import analyze
        from ibkr_etl.hibor import period_average
        from datetime import date
        def result(data):return {'as_of':'2025-01-28','quality_status':'pass','data':data}
        days=['2025-01-20','2025-01-21','2025-01-22','2025-01-23','2025-01-24','2025-01-27','2025-01-28']
        series=[{'date':d,'currency':'HKD','nav':100+i} for i,d in enumerate(days)]
        rates=period_average([{'rate_date':d,'annual_rate':.03} for d in days],date(2025,1,20),date(2025,1,28))
        snapshot={'subject_id':'alpha-world-ms','period_start':'2025-01-20','period_end':'2025-01-31',
            'data_as_of':'2025-01-28','reporting_currency':'HKD','performance':{'series':series},'risk':{'risk_free':rates}}
        metrics,_,_=analyze(snapshot)
        inputs={name:result({}) for name in ('summary','performance','holdings','primary_benchmark','secondary_benchmark','risk','freshness','sector_allocation')}
        for item in inputs.values():item['as_of']='2025-01-28'
        inputs['summary']['data']={'account':{'base_currency':'HKD'},'latest_nav':{'currency':'HKD','total_text':'106'}}
        inputs['performance']['data']={'series':series}
        inputs['risk']['data']={'risk_free':rates,'metrics':metrics}
        payload,_,allowed,_=assemble_month_end_payload(subject_id='alpha-world-ms',period_start='2025-01-20',period_end='2025-01-31',reporting_currency='HKD',tool_results=inputs)
        self.assertFalse(allowed)  # Missing acceptance evidence cannot authorize release.
        self.assertEqual(payload['month_end_performance'][0]['portfolio'],106)
        for mutate in (lambda p:p['performance']['data']['series'][0].update(currency='USD'),
                lambda p:p['summary']['data']['latest_nav'].update(currency='USD'),
                lambda p:p['risk']['data']['metrics'].update(sharpe_ratio=999)):
            changed=copy.deepcopy(inputs);mutate(changed)
            with self.assertRaises((ValueError,ReportingSnapshotError)):
                assemble_month_end_payload(subject_id='alpha-world-ms',period_start='2025-01-20',period_end='2025-01-31',reporting_currency='HKD',tool_results=changed)


if __name__=='__main__':unittest.main()
