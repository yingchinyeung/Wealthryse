"""Exact T/MS transaction and role guards; no provider or database calls."""
import copy
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ibkr_etl import provisional_benchmark as benchmark
from ibkr_etl.website_benchmarks import require_t_website_benchmarks, website_comparison_version


class BenchmarkAtomicity(unittest.TestCase):
    def test_success_commits_only_after_complete_pair(self):
        for subject, prefix in (('alpha-world-t','t'),('alpha-world-ms','ms')):
            with self.subTest(subject=subject):
                c=Mock(); result={'status':'success'}
                with patch.object(benchmark,'_refresh_and_materialize',return_value=result) as inner:
                    self.assertIs(benchmark.refresh_and_materialize(c,account_id=subject,start_date=date(2025,1,20)),result)
                    self.assertFalse(inner.call_args.kwargs['commit'])
                self.assertEqual([call.args[0] for call in c.execute.call_args_list],
                    [f'SAVEPOINT {prefix}_website_benchmark_refresh',f'RELEASE SAVEPOINT {prefix}_website_benchmark_refresh'])
                c.commit.assert_called_once()

    def test_partial_pair_rolls_back_without_commit(self):
        for subject, prefix in (('alpha-world-t','t'),('alpha-world-ms','ms')):
            with self.subTest(subject=subject):
                c=Mock()
                with patch.object(benchmark,'_refresh_and_materialize',side_effect=RuntimeError('secondary unavailable')):
                    with self.assertRaises(RuntimeError):benchmark.refresh_and_materialize(c,account_id=subject,start_date=date(2025,1,20))
                c.commit.assert_not_called()
                self.assertEqual([call.args[0] for call in c.execute.call_args_list],
                    [f'SAVEPOINT {prefix}_website_benchmark_refresh',f'ROLLBACK TO SAVEPOINT {prefix}_website_benchmark_refresh',f'RELEASE SAVEPOINT {prefix}_website_benchmark_refresh'])

    def test_other_accounts_keep_legacy_transaction_path(self):
        c=Mock()
        with patch.object(benchmark,'_refresh_and_materialize',return_value={}) as inner:
            benchmark.refresh_and_materialize(c,account_id='U16088571',start_date=date(2025,1,20))
            self.assertNotIn('commit',inner.call_args.kwargs)
        c.execute.assert_not_called();c.commit.assert_not_called()

    def test_t_role_and_currency_are_exact(self):
        specs=[SimpleNamespace(benchmark_id=key,symbol=symbol,currency=currency,
                 reporting_currency='HKD',mapping_type=role,return_convention='price')
               for key,symbol,currency,role in [('HSI','^HSI','HKD','primary'),('GSPC','^GSPC','USD','secondary')]]
        require_t_website_benchmarks(specs)
        for key,value in [('mapping_type','primary'),('reporting_currency','USD'),('return_convention','total')]:
            changed=copy.deepcopy(specs);setattr(changed[1],key,value)
            with self.assertRaises(ValueError):require_t_website_benchmarks(changed)
        with self.assertRaises(ValueError):require_t_website_benchmarks(specs[:1])
        with self.assertRaises(ValueError):website_comparison_version('alpha-world-t','USD')
        self.assertEqual(website_comparison_version('alpha-world-ms','HKD'),'benchmark-comparison-v2-ms-hkd')
        self.assertEqual(website_comparison_version('U16088571','USD'),'benchmark-comparison-v1')

if __name__=='__main__':unittest.main()
