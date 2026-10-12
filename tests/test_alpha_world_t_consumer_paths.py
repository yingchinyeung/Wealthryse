import unittest
from unittest.mock import Mock,patch
from datetime import date
from dataclasses import replace
from ibkr_etl import provisional_benchmark as provisional
from ibkr_etl.website_benchmarks import website_comparison_version,T_HKD_COMPARISON_VERSION,require_t_website_benchmarks
from ibkr_etl.agent_tools import AgentToolContext,AgentToolError,execute_tool
from test_ms_website_comparisons import specs
import run_self_service_derived_refresh as worker

def t_specs():
    return tuple(replace(s,mapping_type='primary' if s.benchmark_id=='HSI' else 'secondary') for s in specs())

class TConsumerTests(unittest.TestCase):
    def test_t_comparison_contract_and_atomic_pair(self):
        require_t_website_benchmarks(t_specs())
        with self.assertRaises(ValueError):require_t_website_benchmarks(specs())
        self.assertEqual(website_comparison_version('alpha-world-t','HKD'),T_HKD_COMPARISON_VERSION)
        with self.assertRaises(ValueError):website_comparison_version('alpha-world-t','USD')
        with self.assertRaises(ValueError):require_t_website_benchmarks(specs()[:1])
        c=Mock()
        with patch.object(provisional,'_refresh_and_materialize',side_effect=RuntimeError('HSI missing')):
            with self.assertRaises(RuntimeError):provisional.refresh_and_materialize(c,account_id='alpha-world-t')
        self.assertEqual([x.args[0] for x in c.execute.call_args_list],[
            'SAVEPOINT t_website_benchmark_refresh','ROLLBACK TO SAVEPOINT t_website_benchmark_refresh',
            'RELEASE SAVEPOINT t_website_benchmark_refresh'])
        c.commit.assert_not_called()

    def test_t_missing_secondary_fails_without_partial_commit(self):
        c=Mock()
        with patch.object(provisional,'_account_contract',return_value=('HKD','HKD',date(2025,1,20))), \
             patch.object(provisional,'mapped_benchmarks',return_value=t_specs()), \
             patch('ibkr_etl.alpha_world_t_analytics.require_contract') as guard, \
             patch.object(provisional,'_portfolio_values',return_value={'2026-09-30':100}), \
             patch.object(provisional,'_external_flows',return_value={}), \
             patch.object(provisional,'fetch_benchmark_yfinance',side_effect=[[('2026-09-30',100)],RuntimeError('empty')]):
            with self.assertRaisesRegex(RuntimeError,'Required benchmark HSI'):
                provisional.refresh_and_materialize(c,account_id='alpha-world-t')
        guard.assert_called_once();c.commit.assert_not_called()

    def test_worker_never_activates_or_uses_legacy_crisis(self):
        c=Mock();c.execute.return_value.fetchone.return_value={'latest':'2026-09-30'}
        with patch('ibkr_etl.alpha_world_t_analytics.require_contract') as guard, \
             patch.object(worker,'build_risk_snapshot',return_value={'status':'passed'}) as build, \
             patch.object(worker,'refresh_composite_crisis_replay') as legacy, \
             patch.object(worker,'refresh_and_materialize') as comparison:
            result=worker._refresh_analytics_if_approved(c,{'subject_id':'alpha-world-t'},tenant_id='tenant')
        guard.assert_called_once_with(c,tenant_id='tenant')
        build.assert_called_once_with(c,tenant_id='tenant',subject_id='alpha-world-t',as_of='2026-09-30')
        legacy.assert_not_called();comparison.assert_not_called()
        self.assertFalse(result['onboarding_activated']);self.assertFalse(result['acceptance_check_changed'])

    def test_tools_reject_tenant_injection_and_unapproved_t(self):
        c=Mock();context=AgentToolContext('tenant','actor',frozenset({'alpha-world-t'}),'correlation')
        with self.assertRaises(AgentToolError) as error:
            execute_tool(c,context,'get_risk_metrics',{'subject_id':'alpha-world-t','_verified_tenant_id':'other'})
        self.assertEqual(error.exception.code,'unexpected_arguments');c.execute.assert_not_called()
        with patch('ibkr_etl.alpha_world_t_analytics.require_contract',side_effect=ValueError('unapproved')) as guard:
            with self.assertRaises(AgentToolError) as error:
                execute_tool(c,context,'get_risk_metrics',{'subject_id':'alpha-world-t'})
        self.assertEqual(error.exception.code,'t_hkd_contract_not_ready')
        guard.assert_called_once_with(c,tenant_id='tenant');c.execute.assert_not_called()

if __name__=='__main__':unittest.main()
