"""Synthetic behavior checks for the installed physical onboarding entrypoint."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock, patch


class PhysicalOnboardingContract(unittest.TestCase):
    def setUp(self):
        path = Path(os.environ.get('ONBOARDING_TEST_APP','/app'))/'scripts/run_self_service_onboarding_analytics.py'
        self.module = runpy.run_path(str(path),run_name='qualification_import')
        self.main = self.module['main']; self.globals = self.main.__globals__

    def run_case(self, *, empty=False, mutate=None, provider_error=False, risk_error=False, no_latest=False):
        c = Mock()
        subject = {'subject_id':'synthetic-physical','subject_type':'broker_account','history_start_date':'2025-01-20'}
        c.execute.side_effect = ([Mock(fetchall=lambda:[])] if empty else
            [Mock(fetchall=lambda:[subject]), Mock(rowcount=1), Mock(fetchone=lambda:{'latest':None if no_latest else '2026-09-30'})])
        data = {'benchmark':{'comparison_rows':40,'benchmarks':[{'benchmark_id':'GSPC','quality_status':'pass'}]},
                'crisis':{'observations':100,'holdings_as_of':'2026-09-30'},'classification':{'unclassified':0}}
        if mutate: mutate(data)
        benchmark=Mock(return_value=data['benchmark'],side_effect=RuntimeError('synthetic provider failure') if provider_error else None)
        crisis=Mock(return_value=data['crisis']); composite=Mock(side_effect=AssertionError('Physical scope only'))
        sector=Mock(return_value=data['classification']); risk=Mock(return_value={'status':'pass'},side_effect=RuntimeError('synthetic risk failure') if risk_error else None)
        connect=Mock(return_value=c)
        failed=provider_error or risk_error or bool(mutate)
        output=io.StringIO()
        with patch.dict(os.environ,{'DATABASE_URL':'postgresql://synthetic.invalid/test','PORTFOLIO_TENANT_ID':'synthetic-tenant'},clear=True), \
             patch.dict(self.globals,connect=connect,refresh_and_materialize=benchmark,refresh_crisis_replay=crisis,
                        refresh_composite_crisis_replay=composite,refresh_sector_classifications_range=sector,build_risk_snapshot=risk), \
             contextlib.redirect_stdout(output):
            if failed:
                with self.assertRaises(RuntimeError): self.main()
            else: self.assertEqual(0,self.main())
        c.close.assert_called_once(); composite.assert_not_called()
        self.assertEqual(connect.call_args.args[2],'synthetic-tenant')
        if failed: c.rollback.assert_called_once()
        else: c.rollback.assert_not_called()
        if empty:
            benchmark.assert_not_called();c.commit.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())['subjects'],[])
        elif not provider_error:
            benchmark.assert_called_once();crisis.assert_called_once();sector.assert_called_once()
            sql,params=c.execute.call_args_list[1].args
            self.assertIn('risk_analytics_validated',sql)
            self.assertEqual(params[-1],'synthetic-physical')
            self.assertEqual(params[0],'fail' if mutate else 'pass')
            c.commit.assert_called_once()
        if no_latest or empty or provider_error or mutate: risk.assert_not_called()
        elif not risk_error:
            self.assertEqual(risk.call_args.kwargs['tenant_id'],'synthetic-tenant')
            self.assertEqual(risk.call_args.kwargs['as_of'],'2026-09-30')

    def test_missing_database_rejected_before_connection(self):
        with patch.dict(os.environ,{},clear=True), patch.dict(self.globals,connect=Mock(side_effect=AssertionError('no connection'))):
            with self.assertRaisesRegex(RuntimeError,'DATABASE_URL must be PostgreSQL'): self.main()

    def test_non_postgres_rejected_before_connection(self):
        with patch.dict(os.environ,{'DATABASE_URL':'sqlite://test'},clear=True), patch.dict(self.globals,connect=Mock(side_effect=AssertionError('no connection'))):
            with self.assertRaisesRegex(RuntimeError,'DATABASE_URL must be PostgreSQL'): self.main()

    def test_no_approved_subjects_no_calculation(self): self.run_case(empty=True)
    def test_success_preserves_scope_and_date(self): self.run_case()
    def test_short_comparison_fails(self): self.run_case(mutate=lambda d:d['benchmark'].update(comparison_rows=29))
    def test_failed_benchmark_fails(self): self.run_case(mutate=lambda d:d['benchmark']['benchmarks'][0].update(quality_status='fail'))
    def test_empty_crisis_fails(self): self.run_case(mutate=lambda d:d['crisis'].update(observations=0))
    def test_unclassified_holdings_fail(self): self.run_case(mutate=lambda d:d['classification'].update(unclassified=1))
    def test_provider_exception_rolls_back_and_closes(self): self.run_case(provider_error=True)
    def test_risk_exception_rolls_back_and_closes(self): self.run_case(risk_error=True)
    def test_absent_latest_does_not_fabricate_risk(self): self.run_case(no_latest=True)


if __name__=='__main__':unittest.main()
