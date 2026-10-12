import copy
import unittest
from datetime import date
from decimal import Decimal
from uuid import UUID
from unittest.mock import Mock,patch
from ibkr_etl.workflow_lineage import graph,nav_observation,load_graph,inspect_lineage
from ibkr_etl.subject_onboarding import (filtered_portfolio_definition,fixed_unit_composite_definition,
    configuration_sha256,dependencies_for_definition)

TENANT=str(UUID(int=1));ASOF='2026-10-05'


def fixture():
    filtered=filtered_portfolio_definition(subject_id='filtered-a',display_name='Filtered A',source_account_id='U12345678',
        base_currency='USD',history_start_date='2026-01-02',excluded_countries=['Taiwan'],excluded_exchanges=[],
        excluded_currencies=[],opening_cash={'USD':100},allowed_cash_currencies=['USD'],include_actual_interest=True)
    composite=fixed_unit_composite_definition(subject_id='composite-a',display_name='Composite A',base_currency='USD',
        normalization_date='2026-01-02',constant_cash=10,components=[dict(component_id='filtered-a',component_type='portfolio',
            coefficient=1,source_currency='USD',price_convention='nav')])
    definitions=[];dependencies=[]
    for index,d in enumerate((filtered,composite),1):
        definitions.append(dict(tenant_id=TENANT,subject_id=d['subject_id'],subject_type=d['subject_type'],
            calculation_kind=d['calculation_kind'],definition_id=str(UUID(int=index+10)),definition_version=1,
            configuration_json=d,configuration_sha256=configuration_sha256(d),approval_status='approved',
            effective_from='2026-01-02',effective_to=None))
        dependencies.extend(dict(tenant_id=TENANT,subject_id=e.subject_id,depends_on_subject_id=e.depends_on_subject_id,
            dependency_type=e.dependency_type,effective_from=e.effective_from,effective_to=None,
            configuration_json=e.configuration) for e in dependencies_for_definition(d))
    subjects=[dict(tenant_id=TENANT,subject_id=s,subject_type=k,lifecycle_status='active',base_currency='USD',
        history_start_date='2026-01-02') for s,k in [('U12345678','physical_account'),('filtered-a','filtered_portfolio'),
        ('composite-a','composite_portfolio')]]
    return dict(tenant_id=TENANT,as_of=ASOF,subjects=subjects,definitions=definitions,dependencies=dependencies,roots=['composite-a'])


def nav(subject,value='100',day=ASOF):
    return dict(tenant_id=TENANT,subject_id=subject,snapshot_date=day,currency='USD',total_text=value)


class LineageTests(unittest.TestCase):
    def test_exact_recursive_dependency_first_graph(self):
        data=fixture();before=copy.deepcopy(data);result=graph(**data)
        self.assertEqual(result['order'],['U12345678','filtered-a','composite-a'])
        self.assertEqual(len(result['edges']),3);self.assertEqual(data,before)

    def test_missing_extra_overlap_and_modified_dependencies_fail_closed(self):
        for kind in ('missing','extra','overlap','configuration'):
            data=fixture()
            if kind=='missing':data['dependencies'].pop()
            elif kind in ('extra','overlap'):
                row=copy.deepcopy(data['dependencies'][0])
                if kind=='extra':row['depends_on_subject_id']='other-account'
                data['dependencies'].append(row)
            else:data['dependencies'][-1]['configuration_json']['coefficient']=2
            with self.subTest(kind=kind),self.assertRaises(ValueError):graph(**data)

    def test_definition_hash_and_identity_are_checked(self):
        for key,value in [('configuration_sha256','f'*64),('subject_type','physical_account'),('calculation_kind','unknown')]:
            data=fixture();data['definitions'][0][key]=value
            with self.assertRaises(ValueError):graph(**data)

    def test_approval_effective_dates_and_ambiguity_are_checked(self):
        for kind in ('unapproved','future','ended','overlapping'):
            data=fixture()
            if kind=='unapproved':data['definitions'][0]['approval_status']='draft'
            elif kind=='future':data['definitions'][0]['effective_from']='2026-10-06'
            elif kind=='ended':data['definitions'][0]['effective_to']='2026-09-30'
            else:data['definitions'].append(copy.deepcopy(data['definitions'][0]))
            with self.assertRaises(ValueError):graph(**data)

    def test_cross_tenant_records_never_enter_lineage(self):
        for collection in ('subjects','definitions','dependencies'):
            data=fixture();data[collection][0]['tenant_id']=str(UUID(int=2))
            with self.assertRaises(ValueError):graph(**data)

    def test_unknown_and_inactive_parent_are_rejected(self):
        data=fixture();data['subjects'].pop(0)
        with self.assertRaises(ValueError):graph(**data)
        data=fixture();data['subjects'][0]['lifecycle_status']='paused'
        with self.assertRaises(ValueError):graph(**data)

    def test_component_type_and_currency_must_match_registry(self):
        data=fixture();data['subjects'][0]['subject_type']='composite_portfolio'
        with self.assertRaises(ValueError):graph(**data)
        data=fixture();data['subjects'][1]['base_currency']='HKD'
        with self.assertRaises(ValueError):graph(**data)

    def test_effective_cycle_is_rejected(self):
        data=fixture()
        d=fixed_unit_composite_definition(subject_id='filtered-a',display_name='Cycle',base_currency='USD',
            normalization_date='2026-01-02',constant_cash=10,components=[dict(component_id='composite-a',
                component_type='portfolio',coefficient=1,source_currency='USD',price_convention='nav')])
        data['subjects'][1]['subject_type']='composite_portfolio'
        data['definitions'][0].update(subject_type=d['subject_type'],calculation_kind=d['calculation_kind'],
            configuration_json=d,configuration_sha256=configuration_sha256(d))
        data['dependencies']=[r for r in data['dependencies'] if r['subject_id']!='filtered-a']
        e=dependencies_for_definition(d)[0]
        data['dependencies'].append(dict(tenant_id=TENANT,subject_id=e.subject_id,depends_on_subject_id=e.depends_on_subject_id,
            dependency_type=e.dependency_type,effective_from=e.effective_from,effective_to=None,configuration_json=e.configuration))
        with self.assertRaisesRegex(ValueError,'cycle'):graph(**data)

    def test_new_definition_identity_changes_graph_even_with_same_values(self):
        data=fixture();original=graph(**data)['graph_sha256']
        data['definitions'][0]['definition_id']=str(UUID(int=99))
        self.assertNotEqual(graph(**data)['graph_sha256'],original)

    def test_same_count_value_correction_changes_producer_fingerprint(self):
        plan=graph(**fixture());subject='U12345678'
        a=nav_observation(plan,subject,[nav(subject)]);b=nav_observation(plan,subject,[nav(subject,'101')])
        self.assertEqual(a['observations'],b['observations']);self.assertNotEqual(a['output_sha256'],b['output_sha256'])

    def test_nav_scope_currency_date_values_and_duplicates_are_checked(self):
        plan=graph(**fixture());subject='U12345678'
        for key,value in [('tenant_id',str(UUID(int=2))),('subject_id','other'),('currency','HKD'),
                ('snapshot_date','2026-10-06'),('snapshot_date','2025-12-31'),('total_text','NaN'),('total_text','0'),('total_text','invalid')]:
            with self.assertRaises(ValueError):nav_observation(plan,subject,[dict(nav(subject),**{key:value})])
        with self.assertRaises(ValueError):nav_observation(plan,subject,[nav(subject),nav(subject)])
        with self.assertRaises(ValueError):nav_observation(plan,subject,[])

    def test_stale_values_are_visible_not_approved(self):
        plan=graph(**fixture());subject='U12345678'
        self.assertFalse(nav_observation(plan,subject,[nav(subject,day='2026-10-02')])['coverage_verified'])

    def test_readonly_loader_rejects_wrong_scope_or_mutable_transactions(self):
        c=Mock()
        for change in ({'tenant_id':str(UUID(int=2))},{'read_only':'off'},{'isolation':'read committed'}):
            c.execute.return_value.fetchone.return_value=dict(tenant_id=TENANT,read_only='on',isolation='repeatable read',**{})|change
            with self.assertRaises(ValueError):load_graph(c,tenant_id=TENANT,as_of=ASOF)

    def test_real_outputs_bind_ancestors_and_never_authorize_execution(self):
        plan=graph(**fixture());c=Mock()
        state={'source':'100'}
        def query(sql,args):
            result=Mock();subject=args[1]
            result.fetchall.return_value=[nav(subject,state['source'] if subject=='U12345678' else '100')]
            return result
        c.execute.side_effect=query
        with patch('ibkr_etl.workflow_lineage.load_graph',return_value=plan):
            before=inspect_lineage(c,tenant_id=TENANT,as_of=ASOF)
            state['source']='101';after=inspect_lineage(c,tenant_id=TENANT,as_of=ASOF)
        self.assertNotEqual(before['root_lineage_sha256'],after['root_lineage_sha256'])
        self.assertTrue(before['observed_coverage_verified']);self.assertFalse(before['execution_authorized'])
        self.assertFalse(before['all_consumed_inputs_verified']);self.assertNotIn('100',str(before))

    def market_plan(self):
        data=fixture();prior=data['definitions'][1]['configuration_json']
        d=fixed_unit_composite_definition(subject_id='composite-a',display_name='Composite A',base_currency='USD',
            normalization_date='2026-01-02',constant_cash=10,components=prior['components']+[
                dict(component_id='HSI',component_type='benchmark',coefficient=1,
                    source_currency='HKD',price_convention='price_index_close')])
        data['definitions'][1]['configuration_json']=d
        data['definitions'][1]['configuration_sha256']=configuration_sha256(d)
        e=dependencies_for_definition(d)[-1]
        data['dependencies'].append(dict(tenant_id=TENANT,subject_id=e.subject_id,depends_on_subject_id=e.depends_on_subject_id,
            dependency_type=e.dependency_type,effective_from=e.effective_from,effective_to=None,configuration_json=e.configuration))
        return graph(**data)

    def inspect_market(self,row):
        plan=self.market_plan();c=Mock()
        def query(sql,args):
            result=Mock()
            result.fetchall.return_value=[row] if 'FROM benchmark_prices' in sql else [nav(args[1])]
            return result
        c.execute.side_effect=query
        with patch('ibkr_etl.workflow_lineage.load_graph',return_value=plan):
            return inspect_lineage(c,tenant_id=TENANT,as_of=ASOF)

    def market_row(self):
        return dict(tenant_id=UUID(TENANT),benchmark_id='HSI',observation_date=date(2026,10,5),
            level=Decimal('25000.125'),currency='HKD',provider='Yahoo Finance via yfinance',
            observation_status='provisional_close',quality_status='pass')

    def test_native_postgres_market_types_are_canonical_and_corrections_propagate(self):
        row=self.market_row();original=copy.deepcopy(row)
        a=self.inspect_market(row);b=self.inspect_market(dict(original,level=Decimal('25001.125')))
        self.assertTrue(a['observed_coverage_verified'])
        self.assertNotEqual(a['root_lineage_sha256'],b['root_lineage_sha256'])
        self.assertEqual(a['market_outputs']['HSI']['observations'],b['market_outputs']['HSI']['observations'])
        self.assertFalse(a['all_consumed_inputs_verified']);self.assertFalse(a['execution_authorized'])

    def test_market_currency_and_nonfinite_values_fail_closed(self):
        for field,value in [('currency','USD'),('level',Decimal('NaN')),('level','invalid')]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.inspect_market(dict(self.market_row(),**{field:value}))


if __name__=='__main__':unittest.main()
