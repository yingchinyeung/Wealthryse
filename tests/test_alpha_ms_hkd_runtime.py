"""MS core-metric readiness must not fabricate benchmark/onboarding acceptance."""
import unittest
from unittest.mock import Mock, patch

from ibkr_etl.reporting_snapshot import _collect_tool_results
import run_self_service_derived_refresh as derived


class HkdRuntimeTests(unittest.TestCase):
    def test_report_collection_never_queries_legacy_benchmark_comparisons(self):
        c=Mock()
        def result(connection,context,tool,args):
            self.assertEqual(context.allowed_subject_ids,frozenset({'alpha-world-ms'}))
            return {'as_of':'2026-09-30','quality_status':'pass','data':{},'citations':[]}
        with (patch('ibkr_etl.reporting_snapshot.execute_tool',side_effect=result) as execute,
                patch('ibkr_etl.reporting_snapshot.build_monthly_reporting_analytics',return_value={'data':{'available':True}})):
            results=_collect_tool_results(c,tenant_id='tenant',subject_id='alpha-world-ms',
                period_end='2026-09-30',reporting_currency='HKD')
        c.execute.assert_not_called()
        self.assertEqual(execute.call_count,6)
        self.assertNotIn('compare_with_benchmarks',[call.args[2] for call in execute.call_args_list])
        for name in ('primary_benchmark','secondary_benchmark'):
            self.assertEqual(results[name]['quality_status'],'warn')
            self.assertFalse(results[name]['data']['publication_allowed'])
            self.assertFalse(results[name]['data']['available'])
            self.assertEqual(results[name]['data']['series'],[])

    def test_ms_worker_builds_core_cache_without_approving_missing_capabilities(self):
        c=Mock();c.execute.return_value.fetchone.return_value={'latest':'2026-09-30'}
        with (patch('ibkr_etl.subject_read_contract.require_ms_hkd_contract') as contract,
                patch.object(derived,'build_risk_snapshot',return_value={'status':'rebuilt'}) as build,
                patch.object(derived,'refresh_and_materialize') as benchmark,
                patch.object(derived,'refresh_composite_crisis_replay') as crisis):
            evidence=derived._refresh_analytics_if_approved(c,{'subject_id':'alpha-world-ms'},tenant_id='tenant')
        contract.assert_called_once_with(c,tenant_id='tenant')
        build.assert_called_once_with(c,tenant_id='tenant',subject_id='alpha-world-ms',as_of='2026-09-30')
        benchmark.assert_not_called();crisis.assert_not_called()
        self.assertFalse(evidence['onboarding_activated'])
        self.assertFalse(evidence['acceptance_check_changed'])
        self.assertFalse(evidence['benchmark_comparison']['available'])
        self.assertEqual(c.execute.call_count,1)


if __name__=='__main__':unittest.main()
