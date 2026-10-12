from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import unittest
from ibkr_etl import resource_cost_workbook as policy


class CostWorkbookTests(unittest.TestCase):
    def record(self):
        return {'scope':policy.SCOPE,'complete_pagination':True,'captured_at':'2026-10-06T12:00:00+00:00',
            'rows':2,'currencies':['KRW','USD'],'query':{'type':'ActualCost','timeframe':'MonthToDate','dataset':{
                'granularity':'Daily','aggregation':{'totalCost':{'name':'PreTaxCost','function':'Sum'}},
                'grouping':[{'name':'ResourceId','type':'Dimension'},{'name':'ServiceName','type':'Dimension'}]}},
            'pages':[{'properties':{'columns':[{'name':k,'type':v} for k,v in policy.SCHEMA.items()],
                'nextLink':None,'rows':[[0.1,20261001,policy.SCOPE+'/providers/type/one','Database','USD'],
                                      [-2,20261001,'','Credits','KRW']]}}]}

    def qualify(self,record=None):
        return policy.qualify_cost(record or self.record(),now=datetime(2026,10,6,12,30,tzinfo=timezone.utc))

    def test_currencies_are_separate_credits_retained(self):
        summary=self.qualify();self.assertEqual(summary['totals'],{'KRW':'-2','USD':'0.1'})
        self.assertFalse(summary['live']);self.assertFalse(summary['zero_spend_certified'])

    def test_empty_and_incomplete_are_not_zero(self):
        for mutate in (lambda r:r.update(pages=[]),lambda r:r.update(complete_pagination=False),
                       lambda r:r['pages'][0]['properties'].update(nextLink='pending'),lambda r:r.update(rows=10)):
            record=self.record();mutate(record)
            with self.assertRaises(ValueError):self.qualify(record)

    def test_unknown_scope_and_period_rejected(self):
        for mutate in (lambda r:r.update(scope='/subscriptions/other'),
                       lambda r:r['query'].update(timeframe='BillingMonth'),
                       lambda r:r['pages'][0]['properties']['rows'][0].__setitem__(2,policy.SCOPE+'-other/providers/a/b')):
            record=self.record();mutate(record)
            with self.assertRaises(ValueError):self.qualify(record)

    def test_dates_staleness_and_nonfinite_rejected(self):
        cases=[]
        for value in (True,float('nan'),float('inf')):
            record=self.record();record['pages'][0]['properties']['rows'][0][0]=value;cases.append(record)
        for value in (True,20260930,20261007,20260230):
            record=self.record();record['pages'][0]['properties']['rows'][0][1]=value;cases.append(record)
        for value in ('2026-10-06T08:00:00+00:00','2026-10-06T12:31:00+00:00','2026-10-06T12:00:00'):
            record=self.record();record['captured_at']=value;cases.append(record)
        for record in cases:
            with self.assertRaises(ValueError):self.qualify(record)

    def test_schema_conflicts_and_duplicate_rows_rejected(self):
        record=self.record();record['pages'][0]['properties']['rows'][1]=deepcopy(record['pages'][0]['properties']['rows'][0])
        with self.assertRaises(ValueError):self.qualify(record)
        record=self.record();record['pages'][0]['properties']['columns'][0]['type']='String'
        with self.assertRaises(ValueError):self.qualify(record)

    def test_column_order_uses_headers_not_assumptions(self):
        record=self.record();props=record['pages'][0]['properties']
        props['columns'].reverse();props['rows']=[list(reversed(row)) for row in props['rows']]
        self.assertEqual(self.qualify(record)['totals'],self.qualify()['totals'])

    def test_readonly_inventory_and_no_tokens_or_embedded_billing_calls(self):
        expected=policy.workbook(self.qualify(),location='eastasia')
        notebook=json.loads(expected['properties']['serializedData'])
        query=json.loads(notebook['items'][2]['content']['query'])
        self.assertEqual(query['method'],'GETARRAY');self.assertEqual(query['path'],policy.SCOPE+'/resources')
        self.assertEqual(query['headers'],[]);self.assertIsNone(query['data'])
        self.assertIn('not live',notebook['items'][0]['content']['json'])
        self.assertNotIn('CostManagement/query',expected['properties']['serializedData'])
        self.assertNotIn('identity',expected)

    def test_exact_native_readback_required(self):
        expected=policy.workbook(self.qualify(),location='eastasia');rid=policy.SCOPE+'/providers/Microsoft.Insights/workbooks/new'
        actual=deepcopy(expected);actual['id']=rid
        policy.verify_workbook(actual,expected,rid)
        for mutate in (lambda v:v.update(identity={'type':'SystemAssigned'}),
                       lambda v:v['properties'].update(sourceId='/other'),
                       lambda v:v['properties'].update(serializedData='{}'),
                       lambda v:v.update(tags={})):
            value=deepcopy(actual);mutate(value)
            with self.assertRaises(ValueError):policy.verify_workbook(value,expected,rid)

    def test_only_exact_azure_added_title_tag_is_accepted(self):
        expected=policy.workbook(self.qualify(),location='eastasia');rid=policy.SCOPE+'/providers/Microsoft.Insights/workbooks/new'
        actual=deepcopy(expected);actual['id']=rid
        actual['tags']['hidden-title']=expected['properties']['displayName']
        policy.verify_workbook(actual,expected,rid)
        for extra in ({'hidden-title':'other'},{'hidden-title':expected['properties']['displayName'],'extra':'unexpected'}):
            value=deepcopy(actual);value['tags'].update(extra)
            with self.assertRaises(ValueError):policy.verify_workbook(value,expected,rid)


if __name__=='__main__':unittest.main()
