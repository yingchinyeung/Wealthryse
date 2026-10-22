"""The design agents' loop: read, draft, render, compare, repair; then stress and a cold check.

``build`` turns an uploaded template (and, when there is one, a completed
example) into a design spec ready for the owner to review:

1. the template reader inventories both files and finds the values, tables and
   charts (``reader.read``), and code writes the first draft (``draft.draft_spec``);
2. the draft is rendered for the example's month and compared with the example
   by what each prints (``compare.against_example``), and the generated checks run;
3. the builder agent sees the findings and the failed checks and answers with
   spec edits only (``patch``); the edits are applied, validated and rendered
   again, for a few rounds, keeping the best render;
4. the stress suite renders the result for every synthetic edge case;
5. the checker agent, which did not build the spec, reads only the facts (checks,
   comparison, stress results, open questions) and can add objections. It cannot
   clear a failed check: a failure stays a failure whatever it says.

The agents are injected (``DesignAgent``), so the loop runs, and is tested, with
no model at all; with ``agent=None`` the result is the reader's draft, checked.
"""
from __future__ import annotations

import copy
import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from . import bindings, components, compare, draft, patch, reader, stress
from .engine import render_preview
from .spec import built_in_designs, load_design, spec_sha256

MAX_ROUNDS = 6
GOOD_ENOUGH = 0.985
MAX_FINDINGS_SHOWN = 40


class DesignAgent(Protocol):
    def build_step(self, facts: Mapping[str, Any]) -> Mapping[str, Any]:
        """{"edits": [...], "notes": str} for the next round."""

    def check(self, facts: Mapping[str, Any]) -> Mapping[str, Any]:
        """{"summary": str, "objections": [str]} on the finished draft."""


def vocabulary() -> dict[str, Any]:
    """What a spec may say, for the agents' instructions: bindings, formats, components and edits."""
    return {
        "bindings": [{"name": b.name, "kind": b.kind, "label": b.label, "unit": b.unit, "fields": list(b.fields)}
                     for b in bindings.CATALOGUE.values()],
        "formats": list(bindings.FORMAT_KINDS), "pipes": sorted(bindings._PIPES),
        "components": components.describe(),
        "edits": {
            "set": "change one property of an operation: target, property, value",
            "move": "shift an operation: target, dx, dy (points; up is positive)",
            "set_column": "change one table column: target, column (0-based), property, value",
            "add": "add an operation on a page: page (1-based), value (the operation)",
            "delete": "remove an operation: target",
            "set_text": "change owner text: key, value",
            "add_box": "clear the template's own text in a box: page, value [x, y, width, height]",
            "delete_box": "stop clearing a box: page, value",
        },
        "settable": {kind: sorted(props) for kind, props in patch.SETTABLE.items()},
        "column_settable": sorted(patch.COLUMN_SETTABLE),
    }


def field_summary(found: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = []
    for item in found["fields"]:
        if item.kind in ("chart",):
            continue
        items.append({"field": item.field_id, "page": item.page + 1, "kind": item.kind,
                      "template_text": item.text[:80], "example_text": (item.example_text or "")[:80],
                      "box": item.box, "label": item.label, "align": item.align,
                      "candidates": [{k: v for k, v in c.items() if k != "format"} for c in item.candidates[:3]]})
    return items


@dataclass
class Round:
    number: int
    spec_sha256: str
    checks: dict[str, Any] | None
    error: str | None
    comparison: dict[str, Any] | None
    edits: list[dict[str, Any]] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    rejected: str | None = None
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        comparison = None
        if self.comparison is not None:
            comparison = {k: v for k, v in self.comparison.items() if k != "findings"}
            comparison["findings"] = self.comparison["findings"][:MAX_FINDINGS_SHOWN]
        return {"round": self.number, "spec_sha256": self.spec_sha256, "checks": self.checks, "error": self.error,
                "comparison": comparison, "edits": self.edits, "changes": self.changes, "rejected": self.rejected,
                "notes": self.notes}


@dataclass
class BuildResult:
    spec: dict[str, Any]
    questions: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    charts: list[dict[str, Any]]
    rounds: list[Round]
    stress: list[dict[str, Any]]
    checker: dict[str, Any]
    status: str
    preview_pdf: bytes = b""

    def as_dict(self) -> dict[str, Any]:
        return {"spec": self.spec, "spec_sha256": spec_sha256(self.spec), "questions": self.questions,
                "fields": self.fields, "tables": [{k: v for k, v in t.items() if k != "columns"} | {
                    "columns": [{k: v for k, v in c.items() if k not in ("fields",)} for c in t["columns"]]}
                    for t in self.tables],
                "charts": self.charts, "rounds": [r.as_dict() for r in self.rounds], "stress": self.stress,
                "checker": self.checker, "status": self.status}


def _score(entry: Round) -> tuple[int, float]:
    failed = entry.error is not None or (entry.checks or {}).get("status") == "fail"
    return (0 if failed else 1, (entry.comparison or {}).get("score", 0.0))


def _render(spec: Mapping[str, Any], sample: Mapping[str, Any], template_path: str | Path,
            example_path: str | Path | None, example_inv: reader.Inventory | None,
            charts: Sequence[Mapping[str, Any]]) -> tuple[bytes, dict[str, Any], dict[str, Any] | None]:
    pdf, result = render_preview(spec, sample, template_path=template_path, example_path=example_path)
    comparison = None
    if pdf and example_inv is not None:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            handle.write(pdf)
            handle.flush()
            comparison = compare.against_example(reader.inventory(handle.name), example_inv, ignore=charts)
    return pdf, result, comparison


def measured_design(template_path: str | Path, currency: str) -> dict[str, Any] | None:
    """The built-in design measured from this exact template (by SHA-256) in this currency, if there is one."""
    digest = hashlib.sha256(Path(template_path).read_bytes()).hexdigest()
    for design_id in built_in_designs():
        design = load_design(design_id)
        if design.get("template_sha256") == digest and currency in design.get("currencies", []):
            return copy.deepcopy(design)
    return None


def build(template_path: str | Path, example_path: str | Path | None = None,
          snapshot: Mapping[str, Any] | None = None, *, design_id: str, title: str, currency: str,
          more_examples: Sequence[str | Path] = (), agent: DesignAgent | None = None, rounds: int = 4,
          inputs: str = "account") -> BuildResult:
    """Read, draft, then let the builder agent repair the draft by spec edits; stress it; check it cold.

    ``snapshot`` is the example month's frozen snapshot when there is one; without
    it the reader binds by labels and the previews use the synthetic baseline.
    """
    found = reader.read(template_path, example_path, snapshot, more_examples=more_examples)
    drafted = draft.draft_spec(found, design_id=design_id, title=title, currency=currency, inputs=inputs)
    spec = drafted["spec"]
    measured = measured_design(template_path, currency)
    if measured is not None:
        # The platform already has a design measured from this exact template: start from it, not
        # from the reader's guesses. It is still rendered, stressed and checked below, with no repair rounds.
        spec = {**measured, "design_id": design_id, "title": title}
        drafted = {"spec": spec, "questions": []}
    sample = snapshot or stress.snapshot(currency=currency)
    # A measured design was checked against its completed example when it was made; an example
    # exported again shifts the template's own text by a few points, which is not the design's doing.
    example_inv = None if measured is not None else found["example"]
    # Without the example month's snapshot the preview prints other names and dates than the example,
    # so those cells are left out of the comparison; figures still match by shape and place.
    ignore = list(found["charts"]) + ([] if snapshot is not None else [
        {"page": f.page, "box": f.box} for f in found["fields"] if f.kind in ("text", "date", "commentary")])
    history: list[Round] = []
    best: tuple[tuple[int, float], dict[str, Any], bytes] | None = None
    feedback: str | None = None
    for number in range(1, max(1, min(rounds, MAX_ROUNDS)) + 1):
        pdf, result, comparison = _render(spec, sample, template_path, None, example_inv, ignore)
        entry = Round(number, spec_sha256(spec), result.get("checks"), result.get("error"), comparison)
        history.append(entry)
        if best is None or _score(entry) > best[0]:
            best = (_score(entry), spec, pdf)
        failed = entry.error is not None or (entry.checks or {}).get("status") == "fail"
        if agent is None or measured is not None or (not failed and (comparison is None or comparison["score"] >= GOOD_ENOUGH)):
            break
        if number == min(rounds, MAX_ROUNDS):
            break
        step = agent.build_step({
            "round": number, "design_id": design_id, "spec": spec, "vocabulary": vocabulary(),
            "fields": field_summary(found), "tables": [{k: v for k, v in t.items() if k != "columns"} for t in
                                                       found["tables"]],
            "charts": found["charts"], "questions": drafted["questions"],
            "render_error": entry.error,
            "failed_checks": [c for c in (entry.checks or {}).get("checks", []) if c["status"] != "pass"],
            "comparison": None if comparison is None else {
                "score": comparison["score"], "matched": comparison["matched"], "spans": comparison["spans"],
                "findings": comparison["findings"][:MAX_FINDINGS_SHOWN]},
            "previous_edit_rejected": feedback,
        })
        edits = list(step.get("edits") or [])
        entry.notes = str(step.get("notes") or "")[:2000]
        entry.edits = edits
        if not edits:
            break
        try:
            applied = patch.apply(spec, edits, allow_guarded=True)
        except patch.PatchError as error:
            entry.rejected = str(error)
            feedback = str(error)
            continue
        feedback = None
        spec = applied.spec
        entry.changes = applied.changes
    assert best is not None
    _, spec, pdf = best
    outcomes = [{k: v for k, v in o.items() if k != "pdf_bytes"} for o in stress.run(
        spec, template_path=template_path, example_path=None)]
    final = next(r for r in reversed(history) if r.spec_sha256 == spec_sha256(spec))
    failed = final.error is not None or (final.checks or {}).get("status") == "fail" or any(
        o["status"] == "fail" for o in outcomes)
    checker: dict[str, Any] = {"summary": "", "objections": []}
    if agent is not None:
        try:
            verdict = agent.check({
                "design_id": design_id, "checks": final.checks, "render_error": final.error,
                "comparison": None if final.comparison is None else {
                    "score": final.comparison["score"], "findings": final.comparison["findings"][:MAX_FINDINGS_SHOWN]},
                "stress": outcomes, "questions": drafted["questions"], "fields": field_summary(found),
                "spec": spec,
            })
            checker = {"summary": str(verdict.get("summary") or "")[:2000],
                       "objections": [str(o)[:500] for o in (verdict.get("objections") or [])][:20]}
        except Exception as error:  # the fixed checks stand on their own
            checker = {"summary": "", "objections": [], "unavailable": str(error)[:200]}
    status = "fail" if failed else ("review" if checker["objections"] or drafted["questions"] else "pass")
    return BuildResult(spec=spec, questions=drafted["questions"], fields=field_summary(found),
                       tables=found["tables"], charts=found["charts"], rounds=history, stress=outcomes,
                       checker=checker, status=status, preview_pdf=pdf)
