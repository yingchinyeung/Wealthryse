import unittest
from pathlib import Path

import qualify_ms_website_fresh_release_runtime as fresh


class FreshNativeRuntime(unittest.TestCase):
    def test_rollback_uses_only_temp_mapping_and_preserves_comparisons(self):
        code='def check():\n'+fresh.ROLLBACK_CHECK
        compile(code,'mapping-rollback-check','exec')
        self.assertIn('DELETE FROM pg_temp.portfolio_benchmark_mapping',code)
        self.assertIn('INSERT INTO pg_temp.portfolio_benchmark_mapping SELECT * FROM public.',code)
        self.assertNotIn('DELETE FROM public.',code)
        self.assertNotIn('UPDATE public.',code)
        self.assertNotIn('DELETE FROM pg_temp.performance_comparison_daily',code)
        self.assertIn('ROLLBACK TO SAVEPOINT ms_mapping_rollback',code)
        self.assertIn('current_comparisons',code)

    def test_rollback_insert_has_one_anchor_in_qualified_runner(self):
        source=(fresh.runtime.candidate.rehearsal.STAGE/'frozen-runner.py').read_text(encoding='utf-8')
        self.assertEqual(source.count('        raw.rollback()\n        after=fingerprint()'),1)
        self.assertEqual(source.count("        'secondary_failure_rollback_verified':True,"),1)
        self.assertNotEqual(fresh.STAGE,fresh.runtime.STAGE)

