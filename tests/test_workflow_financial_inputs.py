import copy,json,unittest
from datetime import date
from decimal import Decimal
from uuid import UUID
from unittest.mock import Mock,patch
from ibkr_etl.workflow_financial_inputs import TABLES,observe_bundle,load_bundle,inspect_financial_inputs
from ibkr_etl.util import canonical_json,sha256_text

TENANT=str(UUID(int=1));ACCOUNT='U12345678';ASOF='2026-10-05'


def fixture():
    tables={name:[] for name in TABLES}
    for name,(day,keys,columns,numeric) in TABLES.items():
        row={field:None for field in columns.split()}
        row.update(tenant_id=TENANT,account_id=ACCOUNT,row_sha256='a'*64,last_seen_run_id=1)
        row[day]=ASOF
        for key in keys:row[key]=row[key] or ('USD' if key in ('currency','from_currency','to_currency') else 'one')
        if 'conid' in row:row['conid']='123'
        if 'currency' in row:row['currency']='USD'
        for field in numeric.split():row[field]='10'
        tables[name]=[row]
    attrs={'accountId':ACCOUNT,'date':'20261005','cashTransfer':'10'}
    parsed=dict(tenant_id=TENANT,account_id=ACCOUNT,section='Transfers',record_type='Transfer',source_key='transfer',
        report_date=ASOF,conid=None,currency='USD',row_sha256=sha256_text(canonical_json(attrs)),
        data_json=canonical_json(attrs),first_seen_run_id=1,last_seen_run_id=1)
    return dict(tenant_id=TENANT,account_id=ACCOUNT,start='2026-01-02',as_of=ASOF,tables=tables,
        instruments=[dict(conid='123',symbol='TEST',description='Test',asset_category='STK',currency='USD',
            listing_exchange='NYSE',source_json='{}')],parsed_rows=[parsed],
        runs=[dict(tenant_id=TENANT,run_id=1,run_type='staging-snapshot-import',requested_from='2026-01-02',
            requested_to=ASOF,status='success',started_at='2026-10-05T00:00:00Z',
            completed_at='2026-10-05T00:01:00Z',raw_sha256='b'*64)])


class FinancialInputTests(unittest.TestCase):
    def test_all_tables_fingerprinted_without_values_or_input_mutation(self):
        bundle=fixture();before=copy.deepcopy(bundle);result=observe_bundle(bundle)
        self.assertEqual(bundle,before);self.assertEqual(set(result['tables']),set(TABLES))
        self.assertTrue(result['stored_input_integrity_verified'])
        for flag in ('execution_authorized','raw_xml_verified','source_completeness_verified',
                'all_consumed_inputs_verified','reference_cache_verified'):self.assertFalse(result[flag])
        self.assertNotIn('Test',json.dumps(result));self.assertNotIn('cashTransfer',json.dumps(result))

    def test_same_count_corrections_change_manifest_for_every_table(self):
        original=observe_bundle(fixture())
        for table,(_,_,_,numeric) in TABLES.items():
            b=fixture();b['tables'][table][0][numeric.split()[0]]='11'
            result=observe_bundle(b)
            with self.subTest(table=table):
                self.assertEqual(result['tables'][table]['rows'],original['tables'][table]['rows'])
                self.assertNotEqual(result['input_manifest_sha256'],original['input_manifest_sha256'])

    def test_instrument_parsed_data_and_run_identity_changes_are_detected(self):
        original=observe_bundle(fixture())
        for kind in ('instrument','parsed','run'):
            b=fixture()
            if kind=='instrument':b['instruments'][0]['listing_exchange']='SEHK'
            elif kind=='parsed':b['parsed_rows'][0]['data_json']=b['parsed_rows'][0]['data_json'].replace('10','11')
            else:b['runs'][0]['raw_sha256']='c'*64
            self.assertNotEqual(observe_bundle(b)['input_manifest_sha256'],original['input_manifest_sha256'])

    def test_cross_scope_rows_and_duplicate_identity_fail_closed(self):
        for key,value in [('tenant_id',str(UUID(int=2))),('account_id','other')]:
            b=fixture();b['tables']['trades'][0][key]=value
            with self.assertRaises(ValueError):observe_bundle(b)
        b=fixture();b['tables']['trades']*=2
        with self.assertRaises(ValueError):observe_bundle(b)
        b=fixture();b['runs'][0]['tenant_id']=str(UUID(int=2))
        with self.assertRaises(ValueError):observe_bundle(b)

    def test_bad_numbers_preserved_in_hash_and_reported_without_repair(self):
        for value in ('NaN','Infinity','bad',0):
            b=fixture();b['tables']['fx_rates'][0]['rate_text']=value;before=copy.deepcopy(b)
            result=observe_bundle(b)
            self.assertFalse(result['stored_input_integrity_verified'])
            self.assertEqual(result['issue_counts']['fx_rates:rate_text:unusable'],1)
            self.assertEqual(b,before)

    def test_missing_instrument_or_ingestion_lineage_is_visible(self):
        for field in ('instruments','runs'):
            b=fixture();b[field]=[];r=observe_bundle(b)
            self.assertFalse(r['stored_input_integrity_verified'])
        b=fixture();b['runs'][0]['status']='running'
        self.assertIn('ingestion_lineage_not_successful',observe_bundle(b)['issue_counts'])

    def test_missing_required_values_and_invalid_source_hashes_are_visible(self):
        b=fixture();b['tables']['positions'] = b['tables'].pop('position_snapshots')
        with self.assertRaises(ValueError):observe_bundle(b)
        b=fixture();b['tables']['position_snapshots'][0]['market_value_text']=None
        self.assertIn('position_snapshots:market_value_text:missing',observe_bundle(b)['issue_counts'])
        b=fixture();b['runs'][0]['raw_sha256']='not-a-digest'
        self.assertIn('ingestion_lineage_not_successful',observe_bundle(b)['issue_counts'])

    def test_parsed_content_hash_and_embedded_account_checked(self):
        b=fixture();b['parsed_rows'][0]['row_sha256']='f'*64
        self.assertIn('parsed_input_content_hash_mismatch',observe_bundle(b)['issue_counts'])
        b=fixture();b['parsed_rows'][0]['data_json']=b['parsed_rows'][0]['data_json'].replace(ACCOUNT,'other')
        with self.assertRaises(ValueError):observe_bundle(b)

    def test_dates_bounds_and_prior_fx_are_explicit(self):
        b=fixture();b['tables']['fx_rates'][0]['rate_date']='2025-12-01';observe_bundle(b)
        for day in ('2025-12-01','2026-10-06'):
            b=fixture();b['tables']['trades'][0]['trade_date']=day
            with self.assertRaises(ValueError):observe_bundle(b)
        b=fixture();b['tables']['trades'][0]['trade_date']=None
        self.assertIn('trades:undated',observe_bundle(b)['issue_counts'])

    def test_inventory_and_row_limit_fail_closed(self):
        b=fixture();b['tables'].pop('trades')
        with self.assertRaises(ValueError):observe_bundle(b)
        with patch('ibkr_etl.workflow_financial_inputs.MAX_ROWS',0),self.assertRaises(ValueError):observe_bundle(fixture())

    def test_native_scalar_types_are_deterministic(self):
        b=fixture();b['tables']['fx_rates'][0].update(tenant_id=UUID(TENANT),rate_date=date(2026,10,5),rate_text=Decimal('10'))
        r=observe_bundle(b);self.assertTrue(r['stored_input_integrity_verified'])
        self.assertEqual(r['input_manifest_sha256'],observe_bundle(copy.deepcopy(b))['input_manifest_sha256'])

    def test_loader_requires_tenant_scoped_readonly_consistency(self):
        c=Mock();c.execute.return_value.fetchone.return_value=dict(tenant_id=TENANT,read_only='off',isolation='repeatable read')
        with self.assertRaises(ValueError):load_bundle(c,tenant_id=TENANT,account_id=ACCOUNT,start='2026-01-02',as_of=ASOF)

    def test_parent_input_corrections_change_derived_lineage_without_dispatch(self):
        c=Mock();c.execute.return_value.fetchall.return_value=[dict(subject_id=ACCOUNT,history_start_date='2026-01-02')]
        lineage=dict(graph_sha256='a'*64,order=[ACCOUNT,'filtered'],root_lineage_sha256={ACCOUNT:'b'*64,'filtered':'c'*64},
            edges=[dict(subject_id='filtered',parent_id=ACCOUNT,kind='source_account')],observed_coverage_verified=True)
        b=fixture()
        with patch('ibkr_etl.workflow_financial_inputs.inspect_lineage',return_value=lineage),\
                patch('ibkr_etl.workflow_financial_inputs.load_bundle',side_effect=lambda *a,**k:b):
            before=inspect_financial_inputs(c,tenant_id=TENANT,as_of=ASOF)
            b['tables']['trades'][0]['net_cash_text']='11'
            after=inspect_financial_inputs(c,tenant_id=TENANT,as_of=ASOF)
        self.assertNotEqual(before['financial_lineage_sha256']['filtered'],after['financial_lineage_sha256']['filtered'])
        self.assertFalse(after['execution_authorized'])


if __name__=='__main__':unittest.main()
