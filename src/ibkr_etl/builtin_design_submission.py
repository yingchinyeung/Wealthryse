"""Submit a built-in factsheet design for an onboarding case from the backend, without the designer.

A broker-account case with no built-in report style reports in the design its
owner submits in the factsheet designer (designed_reports.py). When the
platform already ships a design measured from the owner's exact template
(``factsheet_spec/designs/<id>.json`` with its ``template_sha256``) and the
template itself (``factsheet_spec/templates/<id>.pdf``), this records the same
submitted design the designer would: the template is stored by SHA-256, the
design is rendered once on the synthetic stress data and checked, and the draft
is stored and marked submitted. The historical factsheets then render from it
through designed_reports, from the account's own stored data.

Nothing is recorded when the case is not waiting for a design, the template
does not match the design, the design does not report in the case's currency,
or the check fails. Submitting again with the same design changes nothing.
"""
from __future__ import annotations

import hashlib
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

from . import factsheet_design_store as store
from .factsheet_spec import builder
from .factsheet_spec.spec import load_design, spec_sha256

ACTOR = "platform:built-in-design"


class BuiltInDesignError(ValueError):
    """Why the built-in design was not submitted for the case."""


def bundled_template(design_id: str) -> bytes:
    try:
        return resources.files("ibkr_etl.factsheet_spec").joinpath("templates", f"{design_id}.pdf").read_bytes()
    except FileNotFoundError as error:
        raise BuiltInDesignError(f"No template is shipped for the {design_id} design") from error


def _case(connection, tenant_id: str, case_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT c.case_id, c.target_subject_id, c.reporting_currency, c.stage, c.status, c.report_style, "
        "s.subject_id FROM broker_onboarding_cases c LEFT JOIN platform_subjects s "
        "ON s.tenant_id = c.tenant_id AND s.subject_id = c.target_subject_id "
        "WHERE c.tenant_id = ? AND c.case_id = ?", (tenant_id, case_id)).fetchone()
    if row is None:
        raise BuiltInDesignError("No such onboarding case")
    case = dict(row)
    if case["status"] == "cancelled" or case["report_style"] or case["stage"] != "build_history":
        raise BuiltInDesignError("The case is not waiting for a factsheet design (it must be at Build history, "
                                 "with no built-in report style)")
    if not case["subject_id"]:
        raise BuiltInDesignError("The case's subject is not registered yet (approval 1)")
    return case


def submit_built_in_design(connection, tenant_id: str, case_id: str, *, design_id: str,
                           template: bytes | None = None, title: str | None = None) -> dict[str, Any]:
    """Store and submit the built-in design for the case; return what was done. The caller commits."""

    case = _case(connection, tenant_id, case_id)
    subject_id, currency = str(case["target_subject_id"]), str(case["reporting_currency"])
    design = load_design(design_id)
    if currency not in design.get("currencies", []):
        raise BuiltInDesignError(f"The {design_id} design does not report in {currency}")
    template = bundled_template(design_id) if template is None else template
    digest = hashlib.sha256(template).hexdigest()
    if digest != design.get("template_sha256"):
        raise BuiltInDesignError(f"The template does not match the one the {design_id} design was measured from")
    title = title or str(design.get("title") or design_id)
    with tempfile.TemporaryDirectory(prefix="built-in-design-") as directory:
        path = Path(directory) / "template.pdf"
        path.write_bytes(template)
        # The designer's own build: it starts from the design measured from this exact template,
        # renders it on the synthetic stress data and checks it, with no agent.
        result = builder.build(path, None, None, design_id=design_id, title=title, currency=currency, agent=None)
    built = result.as_dict()
    spec = built.pop("spec")
    digest_spec = spec_sha256(spec)
    if result.status == "fail":
        raise BuiltInDesignError(f"The {design_id} design failed its checks on the stress data")
    current = connection.execute(
        "SELECT d.draft_id, v.spec_sha256 FROM factsheet_design_drafts d JOIN factsheet_design_versions v "
        "ON v.draft_id = d.draft_id AND v.version = d.current_version "
        "WHERE d.tenant_id = ? AND d.subject_id = ? AND d.status = 'submitted' "
        "ORDER BY d.updated_at DESC, d.created_at DESC LIMIT 1", (tenant_id, subject_id)).fetchone()
    if current is not None and str(current["spec_sha256"]) == digest_spec:
        return {"status": "unchanged", "draft_id": str(current["draft_id"]), "spec_sha256": digest_spec,
                "design_id": design_id}
    built["answered"] = []
    store.put_file(connection, tenant_id, digest, template)
    draft_id = store.create_draft(connection, tenant_id, subject_id, title=title, template_sha256=digest,
                                  example_sha256=None, more_examples=[], build=built, spec=spec,
                                  spec_sha256=digest_spec, actor=ACTOR, status="review")
    store.set_status(connection, tenant_id, draft_id, "submitted")
    return {"status": "submitted", "draft_id": draft_id, "spec_sha256": digest_spec, "design_id": design_id,
            "checks": (built["rounds"][-1].get("checks") or {}).get("status") if built.get("rounds") else None}
