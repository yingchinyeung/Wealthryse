from contextlib import contextmanager
import hashlib
import unittest
from unittest.mock import Mock, patch

from ibkr_etl import risk_refresh
from ibkr_etl.risk_snapshots import (RISK_ANALYTICS_VERSION, RiskInputsNotReady,
    risk_input_fingerprint, validate_risk_input_readiness, build_risk_snapshot)
from ibkr_etl.benchmark_analytics import CALCULATION_VERSION, ComparisonRow
from ibkr_etl.util import canonical_json


@contextmanager
def acquired_lock(*args,**kwargs):
    yield True


def inputs(day="2026-10-06", observation="2026-09-30", benchmark="SHCOMP"):
    return {"subject_id":"subject", "subject_type":"physical_account","as_of":day,
        "benchmark_id":benchmark,"rows":[ComparisonRow(observation,100,100,100,100,None,None,None)],
        "positions":[],"risk_free":None,"crisis_replay":None}


class ReadinessTests(unittest.TestCase):
    def test_holiday_last_session_valid_and_reopening_stale_blocked(self):
        validate_risk_input_readiness(inputs())
        with self.assertRaises(RiskInputsNotReady):
            validate_risk_input_readiness(inputs(day="2026-10-08"))

    def test_stale_closed_future_uncovered_missing_and_duplicate_block(self):
        cases=[inputs(observation="2026-09-29"),inputs(observation="2026-10-01"),
               inputs(observation="2026-10-08"),inputs(day="2027-01-04")]
        empty=inputs();empty["rows"]=[];cases.append(empty)
        duplicate=inputs();duplicate["rows"]*=2;cases.append(duplicate)
        for case in cases:
            with self.subTest(case=case),self.assertRaises(RiskInputsNotReady):
                validate_risk_input_readiness(case)

    def test_prior_calendar_policy_remains_explicit_four_days(self):
        validate_risk_input_readiness(inputs(day="2024-01-06",observation="2024-01-02"))
        with self.assertRaises(RiskInputsNotReady):
            validate_risk_input_readiness(inputs(day="2024-01-07",observation="2024-01-02"))

    def test_covered_2025_dates_require_latest_session_not_four_day_fallback(self):
        validate_risk_input_readiness(inputs(day="2025-01-06",observation="2025-01-06"))
        with self.assertRaisesRegex(RiskInputsNotReady, "required exchange session"):
            validate_risk_input_readiness(inputs(day="2025-01-06",observation="2025-01-02"))

    def test_future_rate_and_crisis_block(self):
        for key,value in [("risk_free",{"rate_date":"2026-10-07"}),
                          ("crisis_replay",{"holdings_as_of":"2026-10-07"})]:
            data=inputs();data[key]=value
            with self.assertRaises(RiskInputsNotReady):validate_risk_input_readiness(data)

    def test_stale_input_cannot_reuse_cache_or_start_bootstrap(self):
        connection=Mock()
        with patch('ibkr_etl.risk_snapshots.load_risk_inputs',return_value=(inputs(day="2026-10-08"),{})),\
             patch('ibkr_etl.risk_snapshots.compute_risk_snapshot') as compute:
            with self.assertRaises(RiskInputsNotReady):
                build_risk_snapshot(connection,tenant_id="tenant",subject_id="subject",as_of="2026-10-08",only_if_changed=True)
        compute.assert_not_called();connection.execute.assert_not_called()

    def test_nonfinite_latest_input_is_actionable_without_reusing_cache(self):
        data=inputs();data['positions']=[{'value':float('nan')}]
        connection=Mock()
        with patch('ibkr_etl.risk_snapshots.load_risk_inputs',return_value=(data,{})),\
             patch('ibkr_etl.risk_snapshots.compute_risk_snapshot') as compute:
            with self.assertRaisesRegex(RiskInputsNotReady,'Risk input values failed deterministic validation'):
                build_risk_snapshot(connection,tenant_id='tenant',subject_id='subject',as_of='2026-10-06',only_if_changed=True)
        compute.assert_not_called();connection.execute.assert_not_called()


class HistoricalAuditTests(unittest.TestCase):
    def fixture(self,dates=1,changed=False):
        data=inputs()
        payload={"account_id":"subject","as_of":"2026-10-06","benchmark_id":"SHCOMP",
                 "analytics_version":RISK_ANALYTICS_VERSION,"quality_status":"warn","metrics":{}}
        existing={"source_data_sha256":"0"*64 if changed else risk_input_fingerprint(data),
            "payload_sha256":hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
            "payload_json":payload,"quality_status":"warn","comparison_version":CALCULATION_VERSION}
        connection=Mock()
        def execute(sql,args=()):
            return Mock(fetchall=lambda:[{"subject_id":"subject","as_of_date":"2026-10-06"}]*dates,
                        fetchone=lambda:existing,rowcount=1)
        connection.execute.side_effect=execute
        return connection,data

    def audit(self,connection,data,**kwargs):
        with patch.object(risk_refresh,'risk_date_lock',acquired_lock),\
             patch.object(risk_refresh,'load_risk_inputs',return_value=(data,{})):
            return risk_refresh.audit_risk_history(connection,tenant_id="tenant",subjects=["subject"],**kwargs)

    def test_current_inputs_rotate_metadata_without_bootstrap_or_source_writes(self):
        connection,data=self.fixture()
        with patch.object(risk_refresh,'build_risk_snapshot') as build:
            result=self.audit(connection,data)
        self.assertEqual(result["checked"],1);self.assertEqual(result["pending"],0)
        build.assert_not_called()
        updates=[call for call in connection.execute.call_args_list if 'UPDATE' in call.args[0]]
        self.assertEqual(len(updates),1)
        self.assertIn('UPDATE risk_analytics_snapshots',updates[0].args[0])
        self.assertFalse(updates[0].args[1][1])
        query=connection.execute.call_args_list[0].args[0]
        self.assertIn("NOT IN ('blocked','insufficient_history')",query)
        self.assertIn("source_checked_at' ASC NULLS FIRST",query)

    def test_corrected_values_invalidate_before_bounded_rebuild(self):
        connection,data=self.fixture(dates=3,changed=True)
        with patch.object(risk_refresh,'build_risk_snapshot',return_value={"status":"rebuilt"}) as build:
            result=self.audit(connection,data,audit_limit=3,rebuild_limit=2)
        self.assertEqual(result["rebuilt"],2);self.assertEqual(result["pending"],1)
        self.assertEqual(build.call_count,2)
        invalidations=[call for call in connection.execute.call_args_list if 'UPDATE' in call.args[0] and call.args[1][1]]
        self.assertEqual(len(invalidations),3)
        self.assertTrue(all('source_data_sha256=?' in call.args[0] for call in invalidations))

    def test_inspection_does_not_write_even_when_corrected(self):
        connection,data=self.fixture(changed=True)
        with patch.object(risk_refresh,'build_risk_snapshot') as build:
            result=self.audit(connection,data,inspect_only=True)
        self.assertEqual(result["pending"],1);build.assert_not_called()
        self.assertFalse(any('UPDATE' in call.args[0] for call in connection.execute.call_args_list))

    def test_unready_and_nonfinite_inputs_are_withheld_not_rebuilt(self):
        connection,data=self.fixture()
        for bad in (inputs(day="2026-10-08"),dict(data,positions=[{"value":float('nan')}] )):
            with patch.object(risk_refresh,'build_risk_snapshot') as build:
                result=self.audit(connection,bad)
            self.assertEqual(result["blocked"],1);build.assert_not_called()

    def test_limits_fail_before_database_query(self):
        connection=Mock()
        for limits in ({"audit_limit":0},{"audit_limit":101},{"audit_limit":True},
                       {"rebuild_limit":-1},{"rebuild_limit":21}):
            with self.assertRaises(ValueError):
                risk_refresh.audit_risk_history(connection,tenant_id='tenant',subjects=['subject'],**limits)
        connection.execute.assert_not_called()

    def test_expected_inception_absence_withheld_without_failure_or_bootstrap(self):
        connection,data=self.fixture()
        data['rows']=[]
        with patch.object(risk_refresh,'inception_without_comparisons',return_value=True),\
             patch.object(risk_refresh,'build_risk_snapshot') as build:
            result=self.audit(connection,data)
        self.assertEqual((result['unavailable'],result['blocked'],result['pending']),(1,0,0))
        build.assert_not_called()
        invalidations=[call for call in connection.execute.call_args_list if 'UPDATE' in call.args[0]]
        self.assertTrue(invalidations[0].args[1][1])

    def test_only_first_nav_empty_returns_with_valid_contract_can_be_unavailable(self):
        connection=Mock();connection.execute.return_value.fetchone.return_value={'first_date':'2026-10-06'}
        data=inputs();data['rows']=[]
        scope=dict(tenant_id='tenant',subject_id='subject',as_of='2026-10-06')
        with patch.object(risk_refresh,'load_risk_inputs',return_value=(data,{})):
            self.assertTrue(risk_refresh.inception_without_comparisons(connection,**scope))
            connection.execute.return_value.fetchone.return_value={'first_date':'2026-10-05'}
            self.assertFalse(risk_refresh.inception_without_comparisons(connection,**scope))
            data['benchmark_id']=None
            self.assertFalse(risk_refresh.inception_without_comparisons(connection,**scope))

    def test_invalid_rebuilt_output_remains_blocked(self):
        connection,data=self.fixture(changed=True)
        with patch.object(risk_refresh,'build_risk_snapshot',return_value={'status':'rebuilt','quality_status':'fail'}):
            result=self.audit(connection,data)
        self.assertEqual(result['blocked'],1)
        self.assertTrue(connection.execute.call_args.args[1][1])

    def test_lock_released_after_exception(self):
        connection=Mock();connection.execute.return_value.fetchone.return_value={"acquired":True}
        with self.assertRaises(RuntimeError):
            with risk_refresh.risk_date_lock(connection,tenant_id='tenant',subject_id='subject',as_of='2026-10-06'):
                raise RuntimeError('worker interrupted')
        self.assertIn('pg_advisory_unlock',connection.execute.call_args.args[0])
        connection.rollback.assert_called()

    def test_cli_default_is_active_registered_only_and_no_immediate_retry(self):
        from pathlib import Path
        source=Path('scripts/build_risk_snapshots.py').read_text()
        self.assertIn("s.lifecycle_status='active'",source)
        self.assertIn("r.operating_state='active'",source)
        self.assertIn('not args.subject',source)
        self.assertIn('historical["pending"]',source)

    def test_cli_rejects_invalid_limits_before_connecting(self):
        import build_risk_snapshots as cli
        for values in (['--history-audit-limit','0'],['--history-rebuild-limit','21']):
            with patch('sys.argv',['build_risk_snapshots']+values),patch.object(cli,'connect') as connect:
                with self.assertRaises(SystemExit):cli.main()
                connect.assert_not_called()


if __name__=='__main__':unittest.main()
