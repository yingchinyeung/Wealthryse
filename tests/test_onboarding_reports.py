from __future__ import annotations

import copy
from datetime import date
import hashlib
import unittest

from ibkr_etl.onboarding_reports import (
    APPROVED_PRESENTATION_MODEL,
    OnboardingReportError,
    build_historical_report_batch_validation,
    discover_completed_reporting_periods,
    historical_report_evidence_sha256,
    source_period_evidence,
    template_evidence,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, dates):
        self.dates = dates
        self.params = None

    def execute(self, sql, params):
        self.params = params
        return _Result([{"snapshot_date": value} for value in self.dates])


def _templates():
    return [
        {
            "report_type": "factsheet",
            "asset_role": "template",
            "version_label": "owner-v1",
            "approval_status": "validated",
            "sha256": "a" * 64,
            "byte_count": 100,
            "is_current": True,
        },
        {
            "report_type": "presentation",
            "asset_role": "template",
            "version_label": "owner-v1",
            "approval_status": "validated",
            "sha256": "b" * 64,
            "byte_count": 200,
            "is_current": True,
        },
        {
            "report_type": "factsheet",
            "asset_role": "completed_example",
            "version_label": "owner-v1",
            "approval_status": "validated",
            "sha256": "c" * 64,
            "byte_count": 300,
            "is_current": True,
        },
    ]


def _ready_payload():
    periods = [
        {
            "reporting_month": "2025-01",
            "period_end": "2025-01-31",
            "holdings_as_of": "2025-01-31",
        },
        {
            "reporting_month": "2025-02",
            "period_end": "2025-02-28",
            "holdings_as_of": "2025-02-28",
        },
    ]
    templates = _templates()
    templates[1]["sha256"] = (
        "2cfa0f334585627f695d8c60fb7fada7307a9d9769d8850402699a354073fea6"
    )
    template_state = template_evidence(templates, subject_id="U16088606")
    source_state = source_period_evidence(
        subject_id="U16088606", reporting_start="2025-01-20", periods=periods
    )
    batch = {
        "batch_id": "11111111-1111-1111-1111-111111111111",
        "expected_month_count": 2,
        "expected_artifact_count": 4,
        "template_evidence_sha256": template_state["evidence_sha256"],
        "source_evidence_sha256": source_state["evidence_sha256"],
        "status": "awaiting_owner_approval",
        "validated_artifact_count": 4,
        "manifest_sha256": "c" * 64,
    }
    items = []
    for period in periods:
        for report_type, extension, character in (
            ("factsheet", ".pdf", "d"),
            ("presentation", ".pptx", "e"),
        ):
            items.append(
                {
                    "period_end": period["period_end"],
                    "holdings_as_of": period["holdings_as_of"],
                    "report_type": report_type,
                    "snapshot_id": "22222222-2222-2222-2222-222222222222",
                    "generation_run_id": len(items) + 1,
                    "status": "validated",
                    "container_name": "internal-reports",
                    "blob_name": (
                        f"review/U16088606/{period['period_end']}/"
                        f"U16088606-{period['period_end']}{extension}"
                    ),
                    "artifact_sha256": character * 64,
                    "byte_count": 1000,
                    "template_sha256": (
                        template_state[report_type]["render_contract_sha256"]
                        if report_type == "factsheet"
                        else template_state[report_type]["sha256"]
                    ),
                    "validation_json": {"passed": True},
                }
            )
    return {
        "subject": {
            "subject_id": "U16088606",
            "history_start_date": "2025-01-20",
        },
        "templates": templates,
        "periods": periods,
        "batch": batch,
        "items": items,
        "container": "internal-reports",
    }


class OnboardingHistoricalReportTests(unittest.TestCase):
    def test_discovers_only_completed_months_and_uses_latest_common_date(self):
        connection = _Connection(
            ["2025-01-20", "2025-01-31", "2025-02-27", "2025-08-29"]
        )
        with self.assertRaisesRegex(OnboardingReportError, "2025-03"):
            discover_completed_reporting_periods(
                connection,
                subject_id="U16088606",
                reporting_start="2025-01-20",
                today=date(2025, 9, 15),
            )
        self.assertEqual(connection.params[2], "2025-08-31")

    def test_ready_batch_is_integrity_bound(self):
        evidence = build_historical_report_batch_validation(**_ready_payload())
        self.assertTrue(evidence["ready_for_owner_approval"])
        copy_without_hash = dict(evidence)
        digest = copy_without_hash.pop("evidence_sha256")
        self.assertEqual(digest, historical_report_evidence_sha256(copy_without_hash))

    def test_report_design_sources_are_explicit_and_fail_closed(self):
        state = template_evidence(_templates())
        self.assertEqual(state["factsheet"]["design_source"], "subject_completed_factsheet")
        self.assertEqual(state["factsheet"]["sha256"], "c" * 64)
        self.assertEqual(
            state["factsheet"]["supporting_template"]["sha256"], "a" * 64
        )
        self.assertRegex(state["factsheet"]["render_contract_sha256"], r"^[0-9a-f]{64}$")
        changed_template = _templates()
        changed_template[0]["sha256"] = "d" * 64
        self.assertNotEqual(
            state["factsheet"]["render_contract_sha256"],
            template_evidence(changed_template)["factsheet"][
                "render_contract_sha256"
            ],
        )
        self.assertEqual(
            state["presentation"]["design_source"],
            "platform_approved_u16088571_presentation",
        )
        self.assertEqual(state["presentation"]["reference_subject_id"], "U16088571")
        self.assertEqual(state["presentation"]["slide_count"], 20)
        greater_china = _templates()
        greater_china[1]["sha256"] = (
            "2cfa0f334585627f695d8c60fb7fada7307a9d9769d8850402699a354073fea6"
        )
        subject_state = template_evidence(
            greater_china, subject_id="U16088606"
        )
        self.assertEqual(
            subject_state["presentation"]["design_source"],
            "approved_u16088571_platform_layout_with_subject_identity",
        )
        self.assertEqual(
            subject_state["presentation"]["version_label"],
            "u16088606-greater-china-platform-v24-evidence-layout-v1",
        )
        self.assertEqual(
            subject_state["presentation"]["sha256"],
            APPROVED_PRESENTATION_MODEL["sha256"],
        )
        self.assertEqual(
            subject_state["presentation"]["identity_evidence_sha256"],
            "2cfa0f334585627f695d8c60fb7fada7307a9d9769d8850402699a354073fea6",
        )
        with self.assertRaisesRegex(OnboardingReportError, "factsheet:completed_example"):
            template_evidence(
                [
                    row
                    for row in _templates()
                    if row["asset_role"] != "completed_example"
                ]
            )

    def test_new_subject_requires_an_integrity_bound_presentation_identity(self):
        templates = _templates()
        with self.assertRaisesRegex(OnboardingReportError, "strategy identity contract"):
            template_evidence(templates, subject_id="U99999999")
        strategy_name = "iStrategy: International Quality"
        identity = "".join(character for character in strategy_name.casefold() if character.isalnum())
        templates[1]["metadata_json"] = {
            "presentation_contract": {
                "schema_version": 1,
                "strategy_name": strategy_name,
                "strategy_identity_sha256": hashlib.sha256(identity.encode()).hexdigest(),
                "visible_text_sha256": "d" * 64,
                "strategy_marker_count": 4,
                "slide_count": 20,
                "layout_policy": "platform_standard",
                "source_template_sha256": "b" * 64,
                "owner_approval_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "owner_approval_scope_sha256": "e" * 64,
                "identity_source": "owner_approved_subject_display_name",
            }
        }
        state = template_evidence(templates, subject_id="U99999999")
        self.assertEqual(state["presentation"]["strategy_name"], strategy_name)
        self.assertEqual(
            state["presentation"]["sha256"],
            APPROVED_PRESENTATION_MODEL["sha256"],
        )
        self.assertEqual(state["presentation"]["layout_policy"], "platform_standard")
        self.assertEqual(
            state["presentation"]["identity_evidence_sha256"], "b" * 64
        )
        templates[1]["metadata_json"]["presentation_contract"][
            "layout_policy"
        ] = "subject_template"
        with self.assertRaisesRegex(OnboardingReportError, "must use the approved"):
            template_evidence(templates, subject_id="U99999999")
        templates[1]["metadata_json"]["presentation_contract"][
            "layout_policy"
        ] = "platform_standard"
        changed = copy.deepcopy(templates)
        changed[1]["sha256"] = "f" * 64
        with self.assertRaisesRegex(OnboardingReportError, "bound to another template"):
            template_evidence(changed, subject_id="U99999999")

    def test_missing_pair_or_stale_template_fails_closed(self):
        payload = _ready_payload()
        payload["items"] = payload["items"][:-1]
        payload["batch"]["template_evidence_sha256"] = "f" * 64
        evidence = build_historical_report_batch_validation(**payload)
        failed = {
            row["check_id"]
            for row in evidence["results"]
            if row["status"] == "fail"
        }
        self.assertIn("template_versions_locked", failed)
        self.assertIn("report_pair_inventory_complete", failed)
        self.assertIn("all_artifacts_validated", failed)
        self.assertFalse(evidence["ready_for_owner_approval"])

    def test_invalid_blob_scope_or_layout_validation_fails_closed(self):
        payload = _ready_payload()
        changed = copy.deepcopy(payload)
        changed["items"][0]["blob_name"] = "released/U16088606/2025-01-31/a.pdf"
        changed["items"][1]["validation_json"] = {"passed": False}
        evidence = build_historical_report_batch_validation(**changed)
        failed = {
            row["check_id"]
            for row in evidence["results"]
            if row["status"] == "fail"
        }
        self.assertIn("all_artifacts_validated", failed)


if __name__ == "__main__":
    unittest.main()
