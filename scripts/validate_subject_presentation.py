"""Shared output gate for historical and monthly presentation workers.

Read-only: validation precedes upload/registration and never changes sources.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from audit_alpha_ms_presentation import audit
from ibkr_etl.ms_chart_workbooks import verify_ms_chart_workbooks
from ibkr_etl.presentation_design import (
    design_composite,
    platform_content_expectations,
    presentation_content_contract_version,
    presentation_content_requirements,
)
from validate_monthly_presentation import validate


def validate_subject_presentation(
    output: Path, snapshot: Path, *, subject_id: str, period_end: str,
    expected_strategy: str | None,
) -> dict[str, object]:
    raw = snapshot.read_bytes()
    output_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    payload = json.loads(raw)
    if payload.get("subject_id") != subject_id or payload.get("period_end") != period_end:
        raise ValueError("Presentation snapshot subject or period differs from the requested job")
    contract = presentation_content_contract_version(subject_id)
    requirements = list(presentation_content_requirements(subject_id))
    if subject_id not in ("alpha-world-ms", "alpha-world-t") and design_composite(payload):
        # A composite in the Alpha World MS or T design: MS's deck checks on its own model and identity.
        from audit_composite_presentation import audit as audit_composite
        from ibkr_etl import composite_presentation
        if expected_strategy != "iStrategy: " + str(payload["report_profile"]["display_name"]).strip():
            raise ValueError("A composite's deck requires the strategy name of its own report profile")
        financial = audit_composite(output, snapshot)
        if (financial.get("status") != "passed" or financial.get("financial_charts_verified") != 6
                or financial.get("risk_metrics_verified") != 7
                or financial.get("snapshot_sha256") != hashlib.sha256(raw).hexdigest()):
            raise ValueError("The composite's exported financial audit is incomplete or bound to another snapshot")
        workbooks = verify_ms_chart_workbooks(output, require_format=True)
        if (workbooks.get("status") != "passed" or workbooks.get("embedded_workbooks_verified") != 6
                or workbooks.get("owner_slides") != [6, 7, 8, 9, 10, 11]
                or workbooks.get("output_sha256") != output_sha256):
            raise ValueError("The composite's chart workbook acceptance is incomplete or bound to another output")
        result = {**financial["generic_validation"], "composite_financial_audit": financial,
                  "composite_chart_workbook_audit": workbooks}
        contract = composite_presentation.CONTRACT_VERSION
        requirements = list(composite_presentation.CONTENT_REQUIREMENTS)
    elif subject_id == "alpha-world-ms":
        if expected_strategy != "iStrategy: Alpha World MS":
            raise ValueError("MS presentation requires the approved Alpha World MS identity")
        if payload.get("reporting_currency") != "HKD":
            raise ValueError("Legacy USD MS presentation input is not accepted")
        financial = audit(output, snapshot)
        if (financial.get("status") != "passed"
                or financial.get("financial_charts_verified") != 6
                or financial.get("risk_metrics_verified") != 7
                or financial.get("snapshot_sha256") != hashlib.sha256(raw).hexdigest()):
            raise ValueError("MS exported financial audit is incomplete or bound to another snapshot")
        result = dict(financial["generic_validation"])
        result["ms_financial_audit"] = financial
        if snapshot.read_bytes() != raw or hashlib.sha256(output.read_bytes()).hexdigest() != output_sha256:
            raise ValueError("Presentation or frozen input changed during validation")
        workbooks = verify_ms_chart_workbooks(output, require_format=True)
        if (workbooks.get("status") != "passed"
                or workbooks.get("embedded_workbooks_verified") != 6
                or workbooks.get("owner_slides") != [6, 7, 8, 9, 10, 11]
                or workbooks.get("output_sha256") != output_sha256):
            raise ValueError("MS chart workbook acceptance is incomplete or bound to another output")
        result["ms_chart_workbook_audit"] = workbooks
    elif subject_id == 'alpha-world-t':
        if expected_strategy != 'iStrategy: Alpha World T' or payload.get('reporting_currency') != 'HKD':
            raise ValueError('T presentation requires the approved HKD Alpha World T identity')
        from audit_alpha_t_private_presentation import audit as audit_t
        financial=audit_t(output,snapshot)
        if (financial['status']!='passed' or financial['financial_charts_verified']!=6
            or financial['risk_metrics_verified']!=13 or financial['source_sha256']!=hashlib.sha256(raw).hexdigest()):
            raise ValueError('T financial output audit is incomplete')
        workbooks=verify_ms_chart_workbooks(output,require_format=True)
        if (workbooks['status']!='passed' or workbooks['embedded_workbooks_verified']!=6
            or workbooks['owner_slides']!=[6,7,8,9,10,11] or workbooks['output_sha256']!=output_sha256):
            raise ValueError('T editable chart evidence is incomplete')
        import zipfile
        from validate_monthly_presentation import _validate_rebased_performance_axis
        with zipfile.ZipFile(output) as package:
            performance_axis = _validate_rebased_performance_axis(package, set(package.namelist()))
        result={'status':'passed','slides':20,'t_financial_audit':financial,'t_chart_workbook_audit':workbooks,
                'performance_axis':performance_axis}
    else:
        expectations = platform_content_expectations(payload)
        result = validate(
            output, expected_period=period_end, expected_strategy=expected_strategy,
            expected_security_axis_labels=expectations["security_axis_labels"],
            expected_summary_table_rows=expectations["summary_table_rows"],
            expected_slide_text=expectations["slide_text"],
        )
    if snapshot.read_bytes() != raw or hashlib.sha256(output.read_bytes()).hexdigest() != output_sha256:
        raise ValueError("Presentation or frozen input changed during validation")
    return {
        **result,
        "content_contract_version": contract,
        "content_requirements": requirements,
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "output_sha256": output_sha256,
    }
