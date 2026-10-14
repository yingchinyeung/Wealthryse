from __future__ import annotations

import json
import unittest
import copy
import hashlib
from unittest.mock import Mock, patch
from pathlib import Path

from ibkr_etl.benchmark_analytics import ComparisonRow, expected_tail_loss_dates
from ibkr_etl.risk_snapshots import (
    RISK_ANALYTICS_VERSION,
    _tail_position_dates,
    decoded_payload,
    risk_input_fingerprint,
    build_risk_snapshot,
    cache_reuse_status,
)
from ibkr_etl.benchmark_analytics import CALCULATION_VERSION
from ibkr_etl.util import canonical_json


def _row(day: str, portfolio_return: float | None) -> ComparisonRow:
    return ComparisonRow(
        observation_date=day,
        portfolio_value=100.0,
        benchmark_level=100.0,
        normalized_portfolio=100.0,
        normalized_benchmark=100.0,
        portfolio_return=portfolio_return,
        benchmark_return=0.0,
        excess_return=portfolio_return,
    )


class RiskSnapshotTests(unittest.TestCase):
    def test_new_year_and_historical_long_holiday_readiness(self):
        from ibkr_etl.risk_snapshots import validate_risk_input_readiness, RiskInputsNotReady
        inputs = self.inputs()
        inputs.update(as_of="2026-01-01", benchmark_id="TAIEX",
                      rows=[_row("2025-12-31", 0.01)], risk_free=None, crisis_replay=None)
        validate_risk_input_readiness(inputs)
        inputs.update(as_of="2025-01-31", rows=[_row("2025-01-22", 0.01)])
        validate_risk_input_readiness(inputs)
        inputs["rows"] = [_row("2025-01-23", 0.01)]
        with self.assertRaises(RiskInputsNotReady):
            validate_risk_input_readiness(inputs)

    def test_taiwan_emergency_closure_readiness_does_not_require_invented_price(self):
        from ibkr_etl.risk_snapshots import validate_risk_input_readiness, RiskInputsNotReady
        inputs = self.inputs()
        inputs.update(as_of="2026-07-10", benchmark_id="TAIEX",
                      rows=[_row("2026-07-09", 0.01)], risk_free=None, crisis_replay=None)
        validate_risk_input_readiness(inputs)
        for day in ("2026-07-08", "2026-07-10", "2026-07-13"):
            inputs["rows"] = [_row(day, 0.01)]
            with self.subTest(day=day), self.assertRaises(RiskInputsNotReady):
                validate_risk_input_readiness(inputs)
        inputs.update(benchmark_id="HSI", rows=[_row("2026-07-09", 0.01)])
        with self.assertRaises(RiskInputsNotReady):
            validate_risk_input_readiness(inputs)

    def inputs(self):
        return {"subject_id": "test-subject", "subject_type": "physical_account",
                "as_of": "2026-01-02", "benchmark_id": "test-benchmark",
                "rows": [_row("2026-01-02", 0.01)],
                "positions": [{"conid": "1", "currency": "USD", "market_value_text": "123.45"}],
                "risk_free": {"rate_date": "2026-01-02", "annual_rate": 0.04},
                "crisis_replay": {"maximum_drawdown": -0.3, "generated_at": "old"}}

    def test_ms_hkd_metrics_and_worker_timings_are_complete(self):
        from ibkr_etl.risk_snapshots import compute_risk_snapshot
        from ibkr_etl.alpha_world_ms_analytics import analyze
        from test_monthly_artifacts import _ms_snapshot
        snapshot=_ms_snapshot();inputs=self.inputs()
        from ibkr_etl.alpha_world_ms_cache import methodology_identity
        snapshot['methodology_identity']=methodology_identity()
        inputs.update(subject_id='alpha-world-ms',subject_type='composite_portfolio',as_of='2026-01-31',
            ms_hkd_snapshot=snapshot,benchmark_id=None,rows=[],
            risk_free=snapshot['risk']['risk_free'],positions=[],crisis_replay=None)
        expected=analyze(snapshot)[0]
        payload,timings,_=compute_risk_snapshot(Mock(),tenant_id='test',subject_id='alpha-world-ms',
            as_of='2026-01-31',loaded_inputs=(inputs,{'input_query_ms':1.,'tail_position_query_ms':0.}))
        self.assertEqual({k:payload['metrics'][k] for k in expected},expected)
        self.assertEqual(payload['runtime_version'],'alpha-world-ms-hkd-risk-runtime-v2')
        self.assertEqual(payload['reporting_currency'],'HKD')
        self.assertEqual(payload['quality_status'],'warn')
        self.assertIsNone(payload['crisis_replay'])
        self.assertGreaterEqual(timings['total_ms'],1.)

    def test_ms_full_nav_values_are_included_in_fingerprint(self):
        from test_monthly_artifacts import _ms_snapshot
        inputs=self.inputs();inputs.update(subject_id='alpha-world-ms',ms_hkd_snapshot=_ms_snapshot())
        digest=risk_input_fingerprint(inputs)
        changed=copy.deepcopy(inputs)
        changed['ms_hkd_snapshot']['performance']['series'][1]['nav']+=.001
        self.assertNotEqual(risk_input_fingerprint(changed),digest)

    def test_same_counts_and_dates_with_corrected_values_invalidate(self):
        original = self.inputs()
        baseline = risk_input_fingerprint(original)
        for field in ("nav", "benchmark", "position_fx", "risk_free", "crisis"):
            changed = copy.deepcopy(original)
            if field in {"nav", "benchmark"}:
                from dataclasses import replace
                changed["rows"][0] = replace(changed["rows"][0], **{
                    "portfolio_value" if field == "nav" else "benchmark_level": 101.0})
            elif field == "position_fx":
                changed["positions"][0]["market_value_text"] = "124.45"
            elif field == "risk_free":
                changed["risk_free"]["annual_rate"] = 0.041
            else:
                changed["crisis_replay"]["maximum_drawdown"] = -0.31
            with self.subTest(field=field):
                self.assertNotEqual(baseline, risk_input_fingerprint(changed))

    def test_fingerprint_ignores_generation_timestamp_not_values(self):
        inputs = self.inputs()
        baseline = risk_input_fingerprint(inputs)
        inputs["crisis_replay"]["generated_at"] = "new"
        self.assertEqual(baseline, risk_input_fingerprint(inputs))
        inputs["risk_free"]["annual_rate"] = float("nan")
        with self.assertRaises(ValueError):
            risk_input_fingerprint(inputs)

    def test_unchanged_valid_snapshot_skips_bootstrap_without_writing_reports(self):
        inputs = self.inputs()
        payload = {"metrics": {"volatility": 0.2}, "account_id":"test-subject",
                   "as_of":"2026-01-02", "benchmark_id":"test-benchmark",
                   "analytics_version":RISK_ANALYTICS_VERSION, "quality_status":"warn"}
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {
            "source_data_sha256": risk_input_fingerprint(inputs),
            "payload_sha256": hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
            "payload_json": payload, "quality_status": "warn", "comparison_version":CALCULATION_VERSION}
        with patch("ibkr_etl.risk_snapshots.load_risk_inputs", return_value=(inputs, {})), \
             patch("ibkr_etl.risk_snapshots.compute_risk_snapshot") as compute:
            result = build_risk_snapshot(connection, tenant_id="tenant", subject_id="test-subject",
                                         as_of="2026-01-02", only_if_changed=True)
        self.assertEqual(result["status"], "unchanged")
        compute.assert_not_called()
        self.assertEqual(connection.execute.call_count, 1)
        self.assertIn("SELECT", connection.execute.call_args.args[0])

    def test_corrupt_payload_or_failed_snapshot_cannot_be_reused(self):
        inputs = self.inputs()
        for quality in ("fail", "warn"):
            connection = Mock()
            connection.execute.return_value.fetchone.return_value = {
                "source_data_sha256": risk_input_fingerprint(inputs),
                "payload_sha256": "wrong", "payload_json": {}, "quality_status": quality,
                "comparison_version":CALCULATION_VERSION}
            with patch("ibkr_etl.risk_snapshots.load_risk_inputs", return_value=(inputs, {})), \
                 patch("ibkr_etl.risk_snapshots.compute_risk_snapshot", side_effect=RuntimeError("rebuild")):
                with self.assertRaisesRegex(RuntimeError, "rebuild"):
                    build_risk_snapshot(connection, tenant_id="tenant", subject_id="test-subject",
                                        as_of="2026-01-02", only_if_changed=True)

    def cache_record(self):
        payload = {"account_id":"test-subject", "as_of":"2026-01-02",
                   "benchmark_id":"test-benchmark", "analytics_version":RISK_ANALYTICS_VERSION,
                   "metrics":{}, "quality_status":"warn"}
        return {"payload_json":payload, "quality_status":"warn", "comparison_version":CALCULATION_VERSION,
                "payload_sha256":hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
                "source_data_sha256":risk_input_fingerprint(self.inputs())}

    def classify(self, record):
        return cache_reuse_status(record, inputs=self.inputs(), source_sha256=risk_input_fingerprint(self.inputs()))

    def test_missing_cache_requires_rebuild(self):
        self.assertEqual(self.classify(None), "missing")

    def test_valid_current_cache_reusable(self):
        self.assertEqual(self.classify(self.cache_record()), "current")

    def test_website_read_guard_rejects_cross_account_and_corrupt_results(self):
        from ibkr_etl.risk_snapshots import snapshot_read_status
        record = self.cache_record()
        self.assertEqual(snapshot_read_status(record, subject_id="test-subject", as_of="2026-01-02"), "current")
        self.assertEqual(snapshot_read_status(record, subject_id="other-subject", as_of="2026-01-02"), "payload_identity_mismatch")
        self.assertEqual(snapshot_read_status(record, subject_id="test-subject", as_of="2026-01-03"), "payload_identity_mismatch")
        record["payload_sha256"] = "wrong"
        self.assertEqual(snapshot_read_status(record, subject_id="test-subject", as_of="2026-01-02"), "payload_hash_mismatch")

    def test_website_read_guard_rejects_non_finite_nested_values_and_failed_quality(self):
        from ibkr_etl.risk_snapshots import snapshot_read_status
        for value in (float("nan"), float("inf"), float("-inf")):
            record = self.cache_record()
            record["payload_json"]["metrics"]["nested"] = {"value":value}
            self.assertEqual(snapshot_read_status(record, subject_id="test-subject", as_of="2026-01-02"), "invalid_payload")
        record = self.cache_record()
        record["quality_status"] = "fail"
        self.assertEqual(snapshot_read_status(record, subject_id="test-subject", as_of="2026-01-02"), "failed_quality")

    def test_malformed_json_and_nonobjects_are_cache_misses(self):
        for payload in ("{broken", "[]", "null", [], 42, {"value":float("inf")}):
            record = self.cache_record()
            record["payload_json"] = payload
            with self.subTest(payload=payload):
                self.assertEqual(self.classify(record), "invalid_payload")

    def test_wrong_identity_with_a_matching_hash_cannot_be_reused(self):
        for key in ("account_id", "as_of", "benchmark_id", "analytics_version"):
            record = self.cache_record()
            record["payload_json"][key] = "wrong"
            record["payload_sha256"] = hashlib.sha256(canonical_json(record["payload_json"]).encode()).hexdigest()
            self.assertEqual(self.classify(record), "payload_identity_mismatch")

    def test_version_quality_structure_and_hash_checks(self):
        for field, value, expected in (("comparison_version","old","comparison_version_changed"),
                ("quality_status","fail","failed_quality"), ("payload_sha256","wrong","payload_hash_mismatch"),
                ("source_data_sha256","corrected","inputs_changed")):
            record = self.cache_record()
            record[field] = value
            self.assertEqual(self.classify(record), expected)
        record = self.cache_record()
        record["payload_json"]["metrics"] = []
        self.assertEqual(self.classify(record), "invalid_payload_structure")

    def test_malformed_cache_reaches_rebuild_instead_of_aborting_on_json(self):
        connection = Mock()
        record = self.cache_record()
        record["payload_json"] = "{broken"
        connection.execute.return_value.fetchone.return_value = record
        with patch("ibkr_etl.risk_snapshots.load_risk_inputs", return_value=(self.inputs(), {})), \
             patch("ibkr_etl.risk_snapshots.compute_risk_snapshot", side_effect=RuntimeError("rebuild requested")):
            with self.assertRaisesRegex(RuntimeError, "rebuild requested"):
                build_risk_snapshot(connection, tenant_id="tenant", subject_id="test-subject",
                                    as_of="2026-01-02", only_if_changed=True)

    def test_tail_query_loads_only_tail_dates_and_predecessors(self) -> None:
        rows = [
            _row("2026-01-01", None),
            _row("2026-01-02", 0.01),
            _row("2026-01-03", -0.10),
            _row("2026-01-04", 0.02),
            _row("2026-01-05", -0.02),
        ]
        tail = expected_tail_loss_dates(rows)
        self.assertEqual(tail, {"2026-01-03"})
        self.assertEqual(
            _tail_position_dates(rows), ["2026-01-02", "2026-01-03"]
        )

    def test_snapshot_json_decoder_accepts_jsonb_mapping_and_text(self) -> None:
        payload = {"metrics": {"sharpe_ratio": 1.25}}
        self.assertEqual(decoded_payload(payload), payload)
        self.assertEqual(decoded_payload(json.dumps(payload)), payload)

    def test_daily_jobs_build_latest_risk_snapshot(self) -> None:
        daily = Path("scripts/run_cloud_job.py").read_text(encoding="utf-8")
        derived = Path("scripts/run_cloud_derived_rebuild.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("scripts/build_risk_snapshots.py", daily)
        self.assertIn("scripts/build_risk_snapshots.py", derived)
        self.assertEqual(RISK_ANALYTICS_VERSION, "risk-analytics-v2")

    def test_web_capacity_source_keeps_one_warm_replica(self) -> None:
        bicep = Path("infra/staging-web.bicep").read_text(encoding="utf-8")
        self.assertIn("minReplicas: 1", bicep)
        self.assertIn("maxReplicas: 2", bicep)
        self.assertIn("cpu: json('1.0')", bicep)
        self.assertIn("memory: '2Gi'", bicep)

    def test_risk_snapshot_job_runs_after_derived_refresh(self) -> None:
        bicep = Path("infra/staging-risk-snapshot-job.bicep").read_text(
            encoding="utf-8"
        )
        self.assertIn("cronExpression: '55 8 * * *'", bicep)
        self.assertIn("replicaRetryLimit: 0", bicep)
        self.assertIn("scripts/build_risk_snapshots.py", bicep)


if __name__ == "__main__":
    unittest.main()
