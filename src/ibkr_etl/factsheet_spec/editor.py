"""Edit a design in plain language: the request, the agent's spec edits, and a before-and-after.

The owner writes what they want ("make the holdings table text bigger", "move
the date under the title", "rename Top 5 Products to Largest Holdings"). The
editor agent answers with either one clarifying question or a list of spec
edits (``patch``) and a one-line summary. Code then:

1. applies the edits and validates the result, refusing anything outside the
   vocabulary;
2. lists guarded areas the edits touch (protected wording, a disclaimer or
   benchmark, or a figure re-pointed at a different metric), which need the
   owner's explicit confirmation;
3. renders the design before and after for the same snapshot, runs the generated
   checks on the new render, and finds the regions that changed;
4. describes every change in its own words, so what the owner approves is what
   the code will do, not the model's account of it.

Nothing is saved here. ``propose`` returns a proposal; the website stores it as
a new spec version only when the owner accepts it, and undo restores the parent.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from . import compare, patch, reader
from .builder import vocabulary
from .engine import render_preview
from .spec import spec_sha256

MAX_REQUEST_CHARS = 2000


class EditorAgent(Protocol):
    def edit(self, facts: Mapping[str, Any]) -> Mapping[str, Any]:
        """{"summary": str, "question": str, "edits": [...]} for one request."""


@dataclass
class Proposal:
    status: str                      # "question", "ready", "needs_confirmation", "invalid", "failed_checks"
    request: str
    summary: str = ""
    question: str = ""
    edits: list[dict[str, Any]] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    guarded: list[str] = field(default_factory=list)
    spec: dict[str, Any] | None = None
    regions: list[dict[str, Any]] = field(default_factory=list)
    checks: dict[str, Any] | None = None
    error: str | None = None
    before_pdf: bytes = b""
    after_pdf: bytes = b""

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "request": self.request, "summary": self.summary, "question": self.question,
                "edits": self.edits, "changes": self.changes, "guarded": self.guarded,
                "spec_sha256": spec_sha256(self.spec) if self.spec else None, "regions": self.regions,
                "checks": self.checks, "error": self.error}


def _inventory(pdf: bytes) -> reader.Inventory:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(pdf)
        handle.flush()
        return reader.inventory(handle.name)


def outline(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every operation in a few words, so the agent can name what the owner means."""
    items = []
    for number, page in enumerate(spec["pages"], 1):
        for layer in page["layers"]:
            for op in layer.get("ops", []) if layer["kind"] == "overlay" else []:
                entry = {"id": op["id"], "page": number, "op": op["op"], "role": op.get("role")}
                for key in ("x", "y", "top", "size", "font", "font_size", "align", "color", "value", "rows", "name",
                            "box"):
                    if key in op:
                        entry[key] = op[key]
                if op["op"] == "table":
                    entry["columns"] = [{"header": c.get("header"), "width": c["width"], "align": c.get("align")}
                                        for c in op["columns"]]
                    if "heading" in op:
                        entry["heading"] = op["heading"].get("value")
                items.append(entry)
    return items


def propose(spec: Mapping[str, Any], request: str, agent: EditorAgent, *, snapshot: Mapping[str, Any],
            template_path: str | Path, example_path: str | Path | None = None, confirmed: bool = False,
            history: list[Mapping[str, str]] | None = None) -> Proposal:
    """The agent's answer to one request, applied, checked and rendered before and after; nothing saved."""
    text = request.strip()
    if not 3 <= len(text) <= MAX_REQUEST_CHARS:
        return Proposal("invalid", text, error=f"Describe the change in 3 to {MAX_REQUEST_CHARS:,} characters.")
    answer = agent.edit({"request": text, "outline": outline(spec), "owner_text": spec.get("owner_text", {}),
                         "vocabulary": vocabulary(), "history": list(history or [])[-6:]})
    summary = str(answer.get("summary") or "")[:400]
    question = str(answer.get("question") or "").strip()[:400]
    edits = list(answer.get("edits") or [])
    if question and not edits:
        return Proposal("question", text, summary=summary, question=question)
    if not edits:
        return Proposal("invalid", text, summary=summary,
                        error="That isn't something this design can change yet. A developer can add it; "
                              "describe it in a design request.")
    try:
        applied = patch.apply(spec, edits, allow_guarded=True)
    except patch.PatchError as error:
        return Proposal("invalid", text, summary=summary, edits=edits, error=str(error))
    before_pdf, before = render_preview(spec, snapshot, template_path=template_path, example_path=example_path)
    after_pdf, after = render_preview(applied.spec, snapshot, template_path=template_path, example_path=example_path)
    regions = compare.changed_regions(_inventory(before_pdf), _inventory(after_pdf)) if before_pdf and after_pdf else []
    proposal = Proposal("ready", text, summary=summary, question=question, edits=edits, changes=applied.changes,
                        guarded=applied.guarded, spec=applied.spec, regions=regions, checks=after.get("checks"),
                        error=after.get("error"), before_pdf=before_pdf, after_pdf=after_pdf)
    if after.get("error"):
        proposal.status = "failed_checks"
    elif applied.guarded and not confirmed:
        proposal.status = "needs_confirmation"
    return proposal
