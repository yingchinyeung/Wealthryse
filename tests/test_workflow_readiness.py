import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

from ibkr_etl.workflow_readiness import PREREQUISITES, StageEvidence, readiness


class WorkflowReadinessTests(unittest.TestCase):
    def evidence(self):
        evidence = {}
        for index, (stage, parents) in enumerate(PREREQUISITES.items()):
            evidence[stage] = StageEvidence("subject", stage, "succeeded", True,
                date(2026, 9, 30), f"{index:064x}",
                {parent: evidence[parent].output_sha256 for parent in parents})
        return evidence

    def ready(self, evidence, expected=date(2026, 9, 30)):
        return readiness("assurance", subject_id="subject", expected_date=expected, evidence=evidence)

    def test_complete_chain_and_holiday_no_new_data_are_valid(self):
        evidence = self.evidence()
        evidence["ingestion"] = replace(evidence["ingestion"], status="unchanged")
        self.assertEqual(self.ready(evidence), (True, ()))

    def test_success_or_unchanged_does_not_make_old_data_current(self):
        for status in ("succeeded", "unchanged"):
            evidence = self.evidence()
            evidence["ingestion"] = replace(evidence["ingestion"], status=status)
            self.assertFalse(self.ready(evidence, date(2026, 10, 1))[0])

    def test_upstream_correction_invalidates_transitive_dependants(self):
        for stage in ("ingestion", "benchmarks_fx", "derived", "risk"):
            evidence = self.evidence()
            evidence[stage] = replace(evidence[stage], output_sha256="f" * 64)
            self.assertFalse(self.ready(evidence)[0])

    def test_cross_account_missing_and_failed_evidence_block(self):
        for change in ("subject", "missing", "failed", "unvalidated"):
            evidence = self.evidence()
            if change == "missing":
                del evidence["ingestion"]
            else:
                evidence["ingestion"] = replace(evidence["ingestion"], **{
                    "subject_id": "wrong"} if change == "subject" else {
                    "status": "failed"} if change == "failed" else {"validated": False})
            self.assertFalse(self.ready(evidence)[0])

    def test_ingestion_worker_cannot_claim_or_certify_analytics(self):
        source = Path("scripts/run_institutional_flex_worker.py").read_text(encoding="utf-8")
        allowed = source.split("allowed_kinds=(", 1)[1].split("),", 1)[0]
        self.assertNotIn("analytics_rebuild", allowed)
        self.assertIn("cannot certify downstream analytics completion", source)

    def test_future_successful_data_never_makes_current_cycle_ready(self):
        evidence=self.evidence()
        evidence['ingestion']=replace(evidence['ingestion'],through_date=date(2026,10,1))
        self.assertIn('ingestion: future data',self.ready(evidence)[1])

    def test_closed_exchange_and_current_nav_have_separate_reviewed_expectations(self):
        evidence=self.evidence()
        for name in ('ingestion','reconciliation','derived','risk','reports'):
            evidence[name]=replace(evidence[name],through_date=date(2026,10,5))
        policy={name:row.through_date for name,row in evidence.items() if name!='assurance'}
        self.assertFalse(self.ready(evidence,date(2026,10,5))[0])
        self.assertEqual(readiness('assurance',subject_id='subject',expected_date=date(2026,10,5),
            expected_dates=policy,evidence=evidence),(True,()))
        evidence['ingestion']=replace(evidence['ingestion'],through_date=date(2026,9,30))
        self.assertFalse(readiness('assurance',subject_id='subject',expected_date=date(2026,10,5),
            expected_dates=policy,evidence=evidence)[0])

    def test_date_policy_must_be_complete_typed_nonfuture_and_known(self):
        policy={name:date(2026,9,30) for name in PREREQUISITES if name!='assurance'}
        for change in ('missing','extra','future','string'):
            modified=dict(policy)
            if change=='missing':del modified['ingestion']
            elif change=='extra':modified['unknown']=date(2026,9,30)
            else:modified['ingestion']=date(2026,10,1) if change=='future' else '2026-09-30'
            with self.assertRaises(ValueError):
                readiness('assurance',subject_id='subject',expected_date=date(2026,9,30),
                    expected_dates=modified,evidence=self.evidence())


if __name__ == "__main__":
    unittest.main()
