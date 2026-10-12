import copy
import sqlite3
from datetime import date
from decimal import Decimal
import unittest
from unittest.mock import Mock

from ibkr_etl.comparison_cashflow_evidence import external_flows,verify_returns,load_flow_evidence
from ibkr_etl.util import canonical_json,sha256_text
import test_comparison_source_evidence as fixtures


class ComparisonCashflowEvidenceTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.ComparisonSourceEvidenceTests();fixture.setUp()
        self.data=fixture.data
        self.data['contract']['subject_type']='physical_account'
        self.evidence=dict(tenant_id='tenant',subject_id='account',base_currency='USD',
            as_of='2026-09-30',source_kind='received_transfers',transfers=[],fx=[])

    def transfer(self,**values):
        attributes={'date':'20260930','assetCategory':'CASH','currency':'USD','cashTransfer':'10',**values}
        return dict(tenant_id='tenant',account_id='account',section='Transfers',record_type='Transfer',
            source_key='one',row_sha256=sha256_text(canonical_json(attributes)),data_json=canonical_json(attributes))

    def verify(self):return verify_returns(self.data,self.evidence)

    def flows(self):return external_flows(self.evidence,tenant_id='tenant',subject_id='account',base_currency='USD',as_of='2026-09-30')

    def test_no_flows_checks_returns_without_claiming_ingestion_or_dispatch(self):
        result=self.verify()
        self.assertTrue(result['cashflow_projection_verified'])
        self.assertEqual(result['return_observations_checked'],1)
        self.assertFalse(result['source_completeness_verified'])
        self.assertFalse(result['execution_authorized'])

    def test_received_flow_correction_same_count_invalidates_returns_and_hash(self):
        before=self.verify()
        self.evidence['transfers']=[self.transfer()]
        result=self.verify()
        self.assertNotEqual(before['flow_source_sha256'],result['flow_source_sha256'])
        self.assertIn('portfolio_return_cashflow_mismatch',result['issue_counts'])
        self.assertIn('normalized_portfolio_cashflow_mismatch',result['issue_counts'])

    def test_modified_dietz_existing_half_period_method_with_deposit(self):
        self.evidence['transfers']=[self.transfer()]
        row=self.data['comparisons'][1]
        row['portfolio_return']=float(Decimal('-8')/Decimal('105'))
        row['normalized_portfolio']=float(Decimal(100)*(1+Decimal('-8')/Decimal('105')))
        self.assertTrue(self.verify()['cashflow_projection_verified'])

    def test_between_observation_dates_flows_are_included_in_right_interval(self):
        self.data['nav'][1]['snapshot_date']='2026-10-02'
        self.data['prices'][1]['observation_date']='2026-10-02'
        self.data['comparisons'][1]['observation_date']='2026-10-02'
        self.data['as_of']=self.evidence['as_of']='2026-10-02'
        self.evidence['transfers']=[self.transfer(date='20261001')]
        self.assertFalse(self.verify()['cashflow_projection_verified'])
        self.evidence['transfers']=[self.transfer(date='20260929')]
        self.assertTrue(self.verify()['cashflow_projection_verified'])

    def test_foreign_cash_uses_latest_prior_ibkr_fx_not_future_quote(self):
        self.evidence['transfers']=[self.transfer(currency='HKD',cashTransfer='80')]
        self.evidence['fx']=[dict(tenant_id='tenant',account_id='account',rate_date=day,
            from_currency='HKD',to_currency='USD',rate_text=rate)
            for day,rate in [('2026-09-29','.125'),('2026-10-01','9')]]
        self.assertEqual(self.flows()[date(2026,9,30)],Decimal('10'))
        self.evidence['fx'].pop(0)
        with self.assertRaisesRegex(ValueError,'Missing'):self.flows()

    def test_invalid_latest_fx_never_falls_back(self):
        self.evidence['transfers']=[self.transfer(currency='HKD')]
        self.evidence['fx']=[dict(tenant_id='tenant',account_id='account',rate_date=day,
            from_currency='HKD',to_currency='USD',rate_text=value)
            for day,value in [('2026-09-28','.125'),('2026-09-29','NaN')]]
        with self.assertRaises(ValueError):self.flows()

    def test_security_transfer_base_value_or_dated_conversion(self):
        self.evidence['transfers']=[self.transfer(assetCategory='STK',positionAmountInBase='12')]
        self.assertEqual(self.flows()[date(2026,9,30)],12)
        self.evidence['transfers']=[self.transfer(assetCategory='STK',positionAmount='8')]
        self.assertEqual(self.flows()[date(2026,9,30)],8)

    def test_corrupt_hash_cross_account_duplicates_rejected(self):
        row=self.transfer()
        for change in ({'row_sha256':'f'*64},{'account_id':'other'},{'tenant_id':'other'},{'section':'Trades'}):
            self.evidence['transfers']=[dict(row,**change)]
            with self.assertRaises(ValueError):self.verify()
        self.evidence['transfers']=[row,copy.deepcopy(row)]
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.verify()

    def test_explicit_invalid_amount_not_silently_skipped(self):
        for value in ('NaN','Infinity','-Infinity'):
            self.evidence['transfers']=[self.transfer(cashTransfer=value)]
            with self.assertRaises(ValueError):self.verify()

    def test_derived_flow_is_scoped_and_does_not_certify_received_source_lineage(self):
        self.data['contract']['subject_type']='filtered_portfolio'
        self.evidence.update(source_kind='derived_external_ledger',ledger=[])
        self.assertTrue(self.verify()['cashflow_projection_verified'])
        self.evidence['ledger']=[dict(tenant_id='tenant',portfolio_id='account',event_key='x',event_date='2026-09-30',
            amount_base='10',included=1,external_flow=1)]
        self.assertFalse(self.verify()['cashflow_projection_verified'])
        self.evidence['ledger'][0]['included']=0
        with self.assertRaisesRegex(ValueError,'Non-external'):self.verify()

    def test_source_kind_or_date_cannot_cross_contract_scope(self):
        for key,value in [('tenant_id','other'),('subject_id','other'),('as_of','2026-10-01'),('base_currency','HKD'),
            ('source_kind','derived_external_ledger')]:
            changed=copy.deepcopy(self.evidence);changed[key]=value
            with self.assertRaises(ValueError):verify_returns(self.data,changed)

    def test_nonpositive_denominator_and_wrong_comparison_identity_block(self):
        self.evidence['transfers']=[self.transfer(cashTransfer='-200')]
        self.assertIn('nonpositive_cashflow_denominator',self.verify()['issue_counts'])
        for key in ('tenant_id','subject_id','benchmark_id','calculation_version'):
            changed=copy.deepcopy(self.data);changed['comparisons'][0][key]='wrong'
            with self.assertRaisesRegex(ValueError,'identity'):verify_returns(changed,self.evidence)

    def test_missing_comparison_and_failed_quality_never_pass(self):
        self.data['comparisons'].pop()
        self.assertFalse(self.verify()['cashflow_projection_verified'])
        self.data['comparisons'][0]['quality_status']='fail'
        with self.assertRaisesRegex(ValueError,'Failed'):self.verify()

    def test_loader_rejects_mutable_wrong_tenant_or_inconsistent_reads_before_source_sql(self):
        for values in [('on','other','repeatable read'),('off','tenant','repeatable read'),('on','tenant','read committed')]:
            connection=Mock()
            connection.execute.return_value.fetchone.return_value=dict(zip(('read_only','tenant_id','isolation'),values))
            with self.assertRaisesRegex(ValueError,'READ ONLY'):load_flow_evidence(connection,self.data)
            self.assertEqual(connection.execute.call_count,1)

    def test_loader_uses_account_scoped_fx_schema_without_inventing_tenant_column(self):
        raw=sqlite3.connect(':memory:');raw.row_factory=sqlite3.Row
        self.addCleanup(raw.close)
        raw.executescript('''
            CREATE TABLE platform_subjects(tenant_id TEXT,subject_id TEXT,subject_type TEXT);
            CREATE TABLE accounts(account_id TEXT,tenant_id TEXT);
            CREATE TABLE flex_rows(tenant_id TEXT,account_id TEXT,section TEXT,record_type TEXT,
                source_key TEXT,row_sha256 TEXT,data_json TEXT);
            CREATE TABLE fx_rates(account_id TEXT,rate_date TEXT,from_currency TEXT,to_currency TEXT,rate_text TEXT);
            INSERT INTO platform_subjects VALUES('tenant','account','physical_account');
            INSERT INTO accounts VALUES('account','tenant'),('other','other-tenant');
            INSERT INTO fx_rates VALUES('account','2026-09-29','HKD','USD','.125'),
                ('account','2026-10-01','HKD','USD','9'),('other','2026-09-29','HKD','USD','8');
        ''')
        class Connection:
            def execute(inner,sql,args=()):
                if 'app_tenant_id()' in sql:
                    result=Mock();result.fetchone.return_value=dict(tenant_id='tenant',read_only='on',isolation='repeatable read')
                    return result
                return raw.execute(sql,args)
        result=load_flow_evidence(Connection(),self.data)
        self.assertEqual(result['fx'],[dict(tenant_id='tenant',account_id='account',rate_date='2026-09-29',
            from_currency='HKD',to_currency='USD',rate_text='.125')])
        raw.execute("UPDATE accounts SET tenant_id='other-tenant' WHERE account_id='account'")
        self.assertEqual(load_flow_evidence(Connection(),self.data)['fx'],[])
