import copy
import os
import unittest
from unittest.mock import Mock, patch
from ibkr_etl import alpha_world_t_onboarding as onboarding


def benchmark():
    return {'account_id':'alpha-world-t','reporting_currency':'HKD','status':'success',
        'publication_allowed':False, 'benchmarks':[
            {'benchmark_id':key,'mapping_type':role,'native_currency':currency,
             'reporting_currency':'HKD','available':True,'quality_status':'warn','comparison_rows':443}
            for key,role,currency in (('HSI','primary','HKD'),('GSPC','secondary','USD'))]}


class OnboardingTests(unittest.TestCase):
    def test_pair_rejects_missing_duplicate_wrong_currency_role_or_coverage(self):
        self.assertEqual(onboarding.validate_benchmark_result(benchmark()),benchmark())
        variants=[]
        for key,value in (('mapping_type','primary'),('reporting_currency','USD'),
                          ('quality_status','fail'),('comparison_rows',29),('available',False)):
            item=benchmark();item['benchmarks'][1][key]=value;variants.append(item)
        item=benchmark();item['benchmarks'].pop();variants.append(item)
        item=benchmark();item['benchmarks'][1]=copy.deepcopy(item['benchmarks'][0]);variants.append(item)
        item=benchmark();item['publication_allowed']=True;variants.append(item)
        for item in variants:
            with self.subTest(item=item), self.assertRaises(ValueError):
                onboarding.validate_benchmark_result(item)

    def run_acceptance(self, *, stale=False, cases=1, updated=1, risk_invalid=False):
        connection=Mock()
        stored=[{'benchmark_id':key,'latest':'2026-09-29' if stale else '2026-09-30',
                 'observations':443} for key in ('HSI','GSPC')]
        connection.execute.side_effect=[
            Mock(fetchall=lambda:[{'case_id':'case'}]*cases),
            Mock(fetchone=lambda:{'latest':'2026-09-30'}), Mock(fetchall=lambda:stored),
            Mock(rowcount=updated)]
        crisis={'qualification_status':'qualified_internal','holdings_as_of':'2026-09-30',
                'observations':121,'calculation_version':'version','input_sha256':'sha','limitations':'internal'}
        with patch.dict(os.environ,{'PORTFOLIO_TENANT_ID':'tenant'}), \
             patch.object(onboarding,'require_contract'), \
             patch.object(onboarding,'compute_risk_snapshot',return_value=({'crisis_replay':crisis},{},'sha')), \
             patch.object(onboarding,'validate_payload',side_effect=ValueError('invalid risk') if risk_invalid else None), \
             patch.object(onboarding,'_refresh_and_materialize',return_value=benchmark()) as refresh, \
             patch.object(onboarding,'store_risk_snapshot') as store:
            if stale or cases!=1 or updated!=1 or risk_invalid:
                with self.assertRaises(ValueError):
                    onboarding.refresh_onboarding_analytics(connection,tenant_id='tenant')
                if stale or cases!=1 or risk_invalid:store.assert_not_called()
            else:
                result=onboarding.refresh_onboarding_analytics(connection,tenant_id='tenant')
                self.assertFalse(result['onboarding_activated']);self.assertFalse(result['report_approvals_changed'])
                self.assertTrue(result['internal_shareholder_only']);store.assert_called_once()
                self.assertFalse(refresh.call_args.kwargs['commit'])
                self.assertEqual(connection.execute.call_args.args[1][-1],'case')
        connection.commit.assert_not_called()
        return connection

    def test_pass_is_t_scoped_and_never_activates_or_commits(self):self.run_acceptance()
    def test_stale_comparisons_never_pass(self):self.run_acceptance(stale=True)
    def test_stored_check_reads_only_the_contract_benchmarks(self):
        # Website-only comparison rows (WEBSITE_ALL_BENCHMARKS) have no mapping row.
        connection=self.run_acceptance()
        sql=connection.execute.call_args_list[2].args[0]
        self.assertIn('FROM portfolio_benchmark_mapping m',sql)
        self.assertIn('m.benchmark_id=c.benchmark_id',sql)
    def test_no_or_ambiguous_case_never_passes(self):
        for cases in (0,2):self.run_acceptance(cases=cases)
    def test_missing_check_fails(self):self.run_acceptance(updated=0)
    def test_invalid_risk_never_passes(self):self.run_acceptance(risk_invalid=True)
    def test_tenant_mismatch_rejected_before_query(self):
        c=Mock()
        with patch.dict(os.environ,{'PORTFOLIO_TENANT_ID':'other'}),self.assertRaises(ValueError):
            onboarding.refresh_onboarding_analytics(c,tenant_id='tenant')
        c.execute.assert_not_called()


if __name__=='__main__':unittest.main()
