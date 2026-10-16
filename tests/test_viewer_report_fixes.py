import copy
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from ibkr_etl.viewer_labels import account_card_name, account_card_title
from ibkr_etl.subject_exports import subject_export, PERFORMANCE, EXPOSURES, TRADES
from ibkr_etl.ai_analyst import prepare_model_context,AIAnalystError,AIAnalystConfig,generate_grounded_narrative,_deterministic_outputs,ANALYSIS_TOOLS
from ibkr_etl.agent_tools import AgentToolContext
from test_ai_analyst import _overview_results,_Client

class Cursor:
    def __init__(self,data):self.data=data
    def fetchone(self):return self.data[0]
    def fetchall(self):return self.data
class Connection:
    def __init__(self,responses):self.responses=iter(responses);self.queries=[]
    def execute(self,sql,params):self.queries.append((sql,params));return Cursor(next(self.responses))

class ViewerFixTests(unittest.TestCase):
    def args(self,**kwargs):
        return dict(tenant_id='tenant',subject_id='hong-kong-china',access={'reporting_currency':'USD','history_start_date':'2025-01-20','subject_type':'filtered_portfolio'},dataset='performance',requested_as_of=None,from_date=None,symbol=None,side=None,cash_filter=lambda s,e:(s,e),trade_filter=lambda s,e,a,b:(s,e,a,b),**kwargs)
    def test_card_names(self):
        for sid,name,expected in [('U16088571','U16088571-U.S. Technology','U16088571 - U.S. Technology'),('U16088606','U16088606 - Greater China','U16088606 - Greater China'),('hong-kong-china','China & Hong Kong','hong-kong-china - China & Hong Kong'),('alpha-world-ms','Alpha World MS','alpha-world-ms - Alpha World MS')]:
            self.assertEqual(account_card_title({'subject_id':sid,'display_name':name}),expected)
    def test_home_card_shows_only_the_name(self):
        for sid,name,expected in [('U16088571','U16088571-U.S. Technology','U.S. Technology'),('alpha-world-t','alpha-world-t - Alpha World T','Alpha World T'),('hong-kong-china','China & Hong Kong','China & Hong Kong'),('U16088606','U16088606','U16088606')]:
            self.assertEqual(account_card_name({'subject_id':sid,'display_name':name}),expected)
    def test_derived_performance_reads_scoped_view(self):
        connection=Connection([[{'latest':'2026-10-07'}],[{'snapshot_date':'2026-10-07','currency':'USD','total_text':'100'}]])
        columns,rows,end,start=subject_export(connection,**self.args())
        self.assertEqual(columns,PERFORMANCE);self.assertEqual(len(rows),1)
        for sql,params in connection.queries:
            self.assertIn('subject_nav_series',sql);self.assertIn('tenant_id=%s',sql);self.assertIn('currency=%s',sql);self.assertEqual(params[:3],('tenant','hong-kong-china','USD'))
    def test_composite_trades_are_explicit_not_blank_csv(self):
        args=self.args();args['dataset']='trades';args['access']={**args['access'],'subject_type':'composite_portfolio'};connection=Connection([])
        with self.assertRaises(HTTPException) as error:subject_export(connection,**args)
        self.assertEqual(error.exception.status_code,409);self.assertFalse(connection.queries)
    def test_filtered_trades_export_source_trades_kept_by_the_portfolio(self):
        args=self.args();args['dataset']='trades';args['symbol']='700';args['side']='BUY'
        connection=Connection([[{'latest':'2026-10-07'}],[{'trade_date':'2026-10-06'}]])
        columns,rows,end,start=subject_export(connection,**args)
        self.assertEqual(columns,TRADES);self.assertEqual(rows,[{'trade_date':'2026-10-06'}])
        sql,params=connection.queries[-1]
        self.assertIn('FROM derived_portfolio_cash_ledger l JOIN trades t',sql)
        self.assertIn("l.event_key LIKE '%%:net'",sql);self.assertNotIn('LIMIT',sql)
        self.assertEqual(params,('tenant','hong-kong-china','duplicate logical trade','all legs of an excluded-currency trade are excluded',len('trade belongs to excluded Taiwan instrument: '),'trade belongs to excluded Taiwan instrument: ','2025-01-20','2026-10-07','700','700','BUY','BUY'))
    def test_missing_data_is_not_successful_empty_export(self):
        with self.assertRaises(HTTPException) as error:subject_export(Connection([[{'latest':None}]]),**self.args())
        self.assertEqual(error.exception.status_code,404)
    def test_model_context_is_subject_specific(self):
        for subject in ('hong-kong-china','U16088606','alpha-world-ms'):
            results=copy.deepcopy(_overview_results())
            for row in results:row['subject_id']=subject
            self.assertEqual(prepare_model_context('portfolio_overview',results)['approved_subject'],subject)
    def test_mixed_subject_grounding_rejected(self):
        results=_overview_results();results[0]['subject_id']='other'
        with self.assertRaises(AIAnalystError) as error:prepare_model_context('portfolio_overview',results)
        self.assertEqual(error.exception.code,'grounding_subject_mismatch')
    def test_cash_matches_page_inclusion_filter(self):
        args=self.args();args['dataset']='cash-activity';connection=Connection([[{'latest':'2026-10-07'}],[]])
        subject_export(connection,**args)
        sql,params=connection.queries[-1]
        self.assertIn('subject_cash_activity_series',sql);self.assertIn('AND included',sql);self.assertEqual(params[:2],('tenant','hong-kong-china'))
    def test_full_narrative_path_accepts_authorized_nonpilot_subjects(self):
        config=AIAnalystConfig(enabled=True,endpoint='https://approved.openai.azure.com/',deployment='regional-model',region='southeastasia',processing_scope='apac-data-zone',managed_identity_client_id='test-client')
        payload={'title':'Portfolio overview','as_of':'2026-09-18','summary':'Validated internal summary.','key_points':[{'text':'NAV is available.','citation_ids':['C1']}],'risks_and_caveats':[{'text':'Benchmark is provisional.','citation_ids':['C2']}]}
        for subject in ('hong-kong-china','U16088606','alpha-world-ms'):
            with self.subTest(subject=subject):
                results=copy.deepcopy(_overview_results())
                for row in results:row['subject_id']=subject
                context=AgentToolContext('tenant','viewer',frozenset((subject,)),'test-correlation')
                calls=[]
                with patch('ibkr_etl.ai_analyst.execute_tool',side_effect=results):
                    response=generate_grounded_narrative(None,context,config,subject_id=subject,analysis_type='portfolio_overview',as_of=None,benchmark_id=None,question='Summarize this portfolio.',client_factory=lambda _: _Client(payload,calls))
                self.assertEqual(response['subject_id'],subject);self.assertEqual(len(calls),1)
    def test_full_narrative_denies_unauthorized_subject_before_tools(self):
        config=AIAnalystConfig(enabled=True,endpoint='https://approved.openai.azure.com/',deployment='regional-model',region='southeastasia',processing_scope='apac-data-zone',managed_identity_client_id='test-client')
        context=AgentToolContext('tenant','viewer',frozenset(('U16088571',)),'test-correlation')
        with patch('ibkr_etl.ai_analyst.execute_tool') as execute:
            with self.assertRaises(AIAnalystError) as error:generate_grounded_narrative(None,context,config,subject_id='hong-kong-china',analysis_type='portfolio_overview',as_of=None,benchmark_id=None,question=None)
            self.assertEqual(error.exception.code,'subject_not_authorized');execute.assert_not_called()
    def test_nav_chart_labels_use_observed_currency(self):
        for currency in ('HKD','USD'):
            rows=[{'tool_name':'get_performance_series','data':{'series':[{'date':'2026-10-07','currency':currency,'nav':100}]}}]
            _,charts=_deterministic_outputs(rows,{'results':[]})
            self.assertEqual(charts[0]['unit'],currency);self.assertEqual(charts[0]['y_label'],f'NAV ({currency})')
    def test_nav_chart_rejects_mixed_or_missing_currency(self):
        for currencies in (('HKD','USD'),('','')):
            rows=[{'tool_name':'get_performance_series','data':{'series':[{'date':'2026-10-07','currency':currency,'nav':100} for currency in currencies]}}]
            with self.assertRaises(AIAnalystError) as error:_deterministic_outputs(rows,{'results':[]})
            self.assertEqual(error.exception.code,'grounding_currency_mismatch')
