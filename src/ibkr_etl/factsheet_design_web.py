"""The factsheet designer: upload a template, review the agents' draft, edit it in plain words, submit it.

Every route answers 404 unless FACTSHEET_DESIGN_AGENTS_ENABLED is "true", and
only owners reach it. The flow, for one account or portfolio:

1. The owner uploads the factsheet template (required), a completed example
   (optional; many new accounts have none) and other months' examples
   (optional). The template reader, the draft writer and the builder loop
   (``factsheet_spec.builder``) turn them into a design spec; the builder and
   checker agents run on the agent service when it is configured, and the
   reader's checked draft stands on its own when it is not.
2. The review page shows the preview, the reader's open questions and the
   checks. "Show what each part reads" outlines every figure, label and table.
3. The owner asks for changes in plain words. The editor agent proposes spec
   edits; code applies them, describes them, renders before and after and runs
   the checks (``factsheet_spec.editor``). Nothing changes until the owner
   accepts; protected wording and re-pointed figures need an explicit
   confirmation; undo restores the version before.
4. Submit turns the design into an ordinary report design request (062). It
   does not change any report: the release service still qualifies the design.

Previews use ``snapshot_for(subject_id, currency)`` when the website supplies
it, else the synthetic baseline (``factsheet_spec.stress.snapshot``), so no
account figures are needed to review a layout.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from html import escape
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from . import factsheet_design_store as store
from . import report_design_updates
from .factsheet_spec import builder, editor, stress
from .factsheet_spec.engine import render_preview
from .factsheet_spec.spec import spec_sha256

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_MORE_EXAMPLES = 6
# The website's ingress ends a request after about 240 seconds; the agent calls in one
# request share this budget, leaving time to read, render and stress-test the design.
AGENT_SECONDS = 150.0
MIN_CALL_SECONDS = 20.0
SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")


def enabled() -> bool:
    return os.environ.get("FACTSHEET_DESIGN_AGENTS_ENABLED", "").strip().lower() == "true"


class EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=3, max_length=editor.MAX_REQUEST_CHARS)
    expected_version: int = Field(ge=1)
    confirmed: bool = False


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: int = Field(ge=0, le=500)
    expected_version: int = Field(ge=1)
    # Empty means "that's right as it is": the question is marked answered and the design is unchanged.
    answer: str = Field(default="", max_length=editor.MAX_REQUEST_CHARS)


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = False


class VersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class WebDesignAgent:
    """The builder, checker and editor agents, reached through the agent service as the website.

    ``api`` is ``agent_web._AgentApi``. A builder step that fails leaves the draft as
    it is (the reader's checked draft stands); an editor call that fails is the
    owner's error to see.
    """

    def __init__(self, api: Any, user_object_id: str, subject_id: str, budget_seconds: float = AGENT_SECONDS) -> None:
        self.api, self.user, self.subject = api, user_object_id, subject_id
        # One website request may make several agent calls (the builder's rounds, then the
        # checker); together they stay inside this budget so the page answers before the
        # ingress cuts the request off.
        self.deadline = time.monotonic() + budget_seconds

    def _call(self, path: str, facts: Mapping[str, Any]) -> Mapping[str, Any]:
        import httpx

        remaining = self.deadline - time.monotonic()
        if remaining < MIN_CALL_SECONDS:
            raise HTTPException(status_code=504, detail="The design agents ran out of time for this request.")
        body = {"user_object_id": self.user, "subject_id": self.subject, "facts": dict(facts)}
        try:
            return self.api.request("POST", f"/v1/factsheet-designs/{path}", timeout=remaining, json=body)["answer"]
        except httpx.TimeoutException as error:
            raise HTTPException(status_code=504, detail="The design agents took too long to answer.") from error
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail="The design agents could not be reached.") from error

    def build_step(self, facts: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            return self._call("build-step", facts)
        except HTTPException as error:
            return {"edits": [], "notes": f"The builder agent was unavailable: {error.detail}"}

    def check(self, facts: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._call("check", facts)

    def edit(self, facts: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._call("edit", facts)


@contextmanager
def _files(paths: Mapping[str, bytes | None]) -> Iterator[dict[str, Path | None]]:
    """The design's PDFs on disk for the reader and renderer, removed afterwards."""
    with tempfile.TemporaryDirectory() as folder:
        out: dict[str, Path | None] = {}
        for name, content in paths.items():
            if content is None:
                out[name] = None
                continue
            target = Path(folder) / f"{name}.pdf"
            target.write_bytes(content)
            out[name] = target
        yield out


def _pdf(upload: UploadFile, what: str) -> bytes:
    content = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"The {what} is larger than 20 MB.")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail=f"The {what} must be a PDF.")
    return content


def highlight(pdf: bytes, drawn: Mapping[str, Any], spec: Mapping[str, Any]) -> bytes:
    """The preview with an outline on every part the design draws: figures blue, fixed words grey, tables green."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    bound: set[str] = set()
    for page in spec["pages"]:
        for layer in page["layers"]:
            for op in layer.get("ops", []) if layer["kind"] == "overlay" else []:
                value = op.get("value")
                if isinstance(value, Mapping) or (isinstance(value, str) and "{" in value):
                    bound.add(op["id"])
    source = PdfReader(io.BytesIO(pdf))
    writer = PdfWriter()
    for number, page in enumerate(source.pages):
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(width, height))
        c.setLineWidth(0.6)
        for box in [b for b in drawn.get("boxes", []) if b["page"] == number]:
            c.setStrokeColorRGB(0.1, 0.6, 0.3)
            c.rect(*box["box"], stroke=1, fill=0)
        for text in [t for t in drawn.get("texts", []) if t["page"] == number]:
            if "." in text["id"] and text["id"].rsplit(".", 1)[-1].startswith("c"):
                continue  # table cells: the table's own outline covers them
            w = stringWidth(text["text"], text["font"], text["size"])
            if text["id"] in bound:
                c.setStrokeColorRGB(0.12, 0.43, 0.92)
                c.setFillColorRGB(0.12, 0.43, 0.92, 0.12)
            else:
                c.setStrokeColorRGB(0.5, 0.5, 0.5)
                c.setFillColorRGB(0.5, 0.5, 0.5, 0.08)
            c.rect(text["x"] - 1, text["y"] - text["size"] * 0.25, w + 2, text["size"] * 1.15, stroke=1, fill=1)
        c.showPage()
        c.save()
        buffer.seek(0)
        page.merge_page(PdfReader(buffer).pages[0])
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def install(app: FastAPI, *, owner_user: Callable[..., dict[str, Any]],
            check_subject: Callable[[str, dict[str, Any]], None], write_database: Callable[[], Any],
            portable: Callable[[Any], Any], render_page: Callable[..., str], tenant_id: str,
            agent_api: Callable[[], Any | None] = lambda: None,
            snapshot_for: Callable[[str, str], Mapping[str, Any] | None] | None = None,
            onboarding_case: Callable[[str], Mapping[str, Any] | None] = lambda subject_id: None) -> None:
    """Add the designer's routes. Each answers 404 while the switch is off.

    check_subject(subject_id, user) raises when the owner may not design for that
    account; write_database() opens the website's write connection and
    portable(connection) adapts it to ``?`` placeholders; agent_api() gives the
    agent service client, or None when it is not configured. onboarding_case(subject_id)
    gives the open onboarding case (case_id, reporting_currency) of a subject that is
    still being onboarded, else None: its submitted design is what its historical
    factsheets are rendered from (designed_reports.py), so submitting it opens no
    report design request.
    """

    def design_user(user: dict[str, Any] = Depends(owner_user)) -> dict[str, Any]:
        if not enabled():
            raise HTTPException(status_code=404, detail="Not found")
        return user

    def subject(subject_id: str, user: dict[str, Any]) -> None:
        if not SUBJECT.fullmatch(subject_id):
            raise HTTPException(status_code=404, detail="Not found")
        check_subject(subject_id, user)

    def actor(user: Mapping[str, Any]) -> str:
        return str(user.get("object_id") or user.get("email") or "owner")

    def agent(user: Mapping[str, Any], subject_id: str) -> WebDesignAgent | None:
        api = agent_api()
        return None if api is None else WebDesignAgent(api, str(user.get("object_id") or "").lower(), subject_id)

    @contextmanager
    def database() -> Iterator[Any]:
        try:
            with write_database() as connection:
                yield portable(connection)
        except store.DesignStoreError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except HTTPException:
            raise
        except Exception as error:
            # Two changes to one design at once: the second one's version number is taken.
            if any(kind.__name__ == "IntegrityError" for kind in type(error).__mro__):
                raise HTTPException(status_code=409, detail="The design changed since you opened it. "
                                                            "Reload and try again.") from error
            if any(kind.__module__.startswith("psycopg") for kind in type(error).__mro__[:-1]):
                raise HTTPException(status_code=503, detail="The design store is not available.") from error
            raise

    def sample(subject_id: str, currency: str) -> Mapping[str, Any]:
        found = snapshot_for(subject_id, currency) if snapshot_for else None
        return found or stress.snapshot(subject_id=subject_id, currency=currency)

    def load(connection: Any, subject_id: str, draft_id: uuid.UUID) -> dict[str, Any]:
        row = store.draft(connection, tenant_id, str(draft_id))
        if row is None or row["subject_id"] != subject_id:
            raise HTTPException(status_code=404, detail="Design not found")
        return row

    def template_bytes(connection: Any, row: Mapping[str, Any]) -> dict[str, bytes | None]:
        return {"template": store.get_file(connection, tenant_id, row["template_sha256"]),
                "example": store.get_file(connection, tenant_id, row["example_sha256"])
                if row.get("example_sha256") else None}

    def currency_of(spec: Mapping[str, Any]) -> str:
        return (spec.get("currencies") or ["USD"])[0]

    def render(spec: Mapping[str, Any], files: Mapping[str, bytes | None], subject_id: str) -> tuple[bytes, dict]:
        with _files({"template": files["template"]}) as paths:
            return render_preview(spec, sample(subject_id, currency_of(spec)), template_path=paths["template"])

    def pdf_response(content: bytes, name: str) -> Response:
        return Response(content, media_type="application/pdf", headers={
            "Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff"})

    @app.get("/accounts/{subject_id}/factsheet-designer", response_class=HTMLResponse)
    def designer_page(subject_id: str, user: dict[str, Any] = Depends(design_user)) -> HTMLResponse:
        subject(subject_id, user)
        case = onboarding_case(subject_id)
        currency = str(case["reporting_currency"]) if case and CURRENCY.fullmatch(str(case["reporting_currency"])) else "USD"
        content = ((ONBOARDING_NOTE.replace("{CASE_PAGE}", escape(f"/account-onboarding/cases/{case['case_id']}", quote=True))
                    if case else "")
                   + PAGE_BODY.replace("{SUBJECT}", escape(subject_id)).replace("{CURRENCY}", currency))
        script = PAGE_SCRIPT.replace("{SUBJECT_JSON}", '"' + subject_id + '"')
        return HTMLResponse(render_page(user, active="accounts", title="Factsheet designer",
                                        description="Upload a factsheet template; the design agents draft it, "
                                                    "you review and edit it in plain words, then submit it for release.",
                                        content=content, script=script))

    @app.get("/api/factsheet-designs/{subject_id}/drafts")
    def list_drafts(subject_id: str, user: dict[str, Any] = Depends(design_user)) -> dict[str, Any]:
        subject(subject_id, user)
        with database() as connection:
            return {"drafts": store.drafts(connection, tenant_id, subject_id),
                    "agents_available": agent_api() is not None}

    @app.post("/api/factsheet-designs/{subject_id}/drafts")
    def create_draft(subject_id: str, template: UploadFile = File(...), example: UploadFile | None = File(None),
                     more_examples: list[UploadFile] | None = File(None), title: str = Form(...),
                     currency: str = Form("USD"), user: dict[str, Any] = Depends(design_user)) -> dict[str, Any]:
        subject(subject_id, user)
        title = title.strip()
        if not 1 <= len(title) <= 120:
            raise HTTPException(status_code=422, detail="Give the design a title of up to 120 characters.")
        currency = currency.strip().upper()
        if not CURRENCY.fullmatch(currency):
            raise HTTPException(status_code=422, detail="The currency is a three-letter code such as USD.")
        uploads = [u for u in (more_examples or []) if u is not None and u.filename]
        if len(uploads) > MAX_MORE_EXAMPLES:
            raise HTTPException(status_code=422, detail=f"Add at most {MAX_MORE_EXAMPLES} other months' examples.")
        template_pdf = _pdf(template, "template")
        example_pdf = _pdf(example, "completed example") if example is not None and example.filename else None
        others = [_pdf(u, "other month's example") for u in uploads]
        design_id = re.sub(r"[^a-z0-9_.-]", "-", subject_id.lower())[:60] + "-" + uuid.uuid4().hex[:8]
        with _files({"template": template_pdf, "example": example_pdf,
                     **{f"more{i}": o for i, o in enumerate(others)}}) as paths:
            result = builder.build(paths["template"], paths["example"], None, design_id=design_id, title=title,
                                   currency=currency, more_examples=[paths[f"more{i}"] for i in range(len(others))],
                                   agent=agent(user, subject_id))
        built = result.as_dict()
        spec = built.pop("spec")
        built["answered"] = []
        digests = [hashlib.sha256(c).hexdigest() for c in [template_pdf, example_pdf, *others] if c is not None]
        with database() as connection:
            for content in [template_pdf, example_pdf, *others]:
                if content is not None:
                    store.put_file(connection, tenant_id, hashlib.sha256(content).hexdigest(), content)
            draft_id = store.create_draft(
                connection, tenant_id, subject_id, title=title, template_sha256=digests[0],
                example_sha256=digests[1] if example_pdf is not None else None,
                more_examples=digests[2 if example_pdf is not None else 1:], build=built, spec=spec,
                spec_sha256=spec_sha256(spec), actor=actor(user),
                status="failed" if result.status == "fail" else "review")
        return {"draft_id": draft_id, "status": result.status, "questions": result.questions,
                "checker": result.checker}

    @app.get("/api/factsheet-designs/{subject_id}/drafts/{draft_id}")
    def get_draft(subject_id: str, draft_id: uuid.UUID, user: dict[str, Any] = Depends(design_user)) -> dict:
        subject(subject_id, user)
        with database() as connection:
            row = load(connection, subject_id, draft_id)
            current = store.version(connection, str(draft_id), row["current_version"])
            history = store.versions(connection, str(draft_id))
        build = row["build"]
        answered = set(build.get("answered") or [])
        return {"draft_id": str(draft_id), "title": row["title"], "status": row["status"],
                "current_version": row["current_version"], "has_example": bool(row.get("example_sha256")),
                "build_status": build.get("status"), "checker": build.get("checker"),
                "questions": [dict(q, index=i, answered=i in answered) for i, q in enumerate(build.get("questions", []))],
                "stress": [{k: v for k, v in s.items() if k in ("case", "label", "status", "detail")}
                           for s in build.get("stress", [])],
                "rounds": [{"round": r["round"], "score": (r.get("comparison") or {}).get("score"),
                            "changes": r.get("changes", []), "rejected": r.get("rejected")}
                           for r in build.get("rounds", [])],
                "checks": current["checks"], "outline": editor.outline(current["spec"]),
                "versions": history, "agents_available": agent_api() is not None}

    @app.get("/api/factsheet-designs/{subject_id}/drafts/{draft_id}/versions/{number}/preview.pdf")
    def version_preview(subject_id: str, draft_id: uuid.UUID, number: int, highlight_parts: bool = False,
                        user: dict[str, Any] = Depends(design_user)) -> Response:
        subject(subject_id, user)
        with database() as connection:
            row = load(connection, subject_id, draft_id)
            spec = store.version(connection, str(draft_id), number)["spec"]
            files = template_bytes(connection, row)
        pdf, result = render(spec, files, subject_id)
        if not pdf:
            raise HTTPException(status_code=422, detail=result.get("error") or "This version did not render.")
        if highlight_parts:
            pdf = highlight(pdf, result.get("drawn") or {}, spec)
        return pdf_response(pdf, f"design-v{number}.pdf")

    def propose(subject_id: str, draft_id: uuid.UUID, request: str, expected: int, confirmed: bool,
                user: Mapping[str, Any], question: int | None = None) -> dict[str, Any]:
        designer = agent(user, subject_id)
        if designer is None:
            raise HTTPException(status_code=503, detail="The design agents are not configured, so edits in plain "
                                                        "words are unavailable. The draft itself still works.")
        with database() as connection:
            row = load(connection, subject_id, draft_id)
            if row["status"] not in ("review", "failed"):
                raise HTTPException(status_code=409, detail="This design was submitted; start a new draft to change it")
            if int(row["current_version"]) != expected:
                raise HTTPException(status_code=409, detail="The design changed since you opened it. Reload and try again.")
            spec = store.version(connection, str(draft_id), expected)["spec"]
            files = template_bytes(connection, row)
            history = store.recent_requests(connection, str(draft_id))
        with _files({"template": files["template"]}) as paths:
            result = editor.propose(spec, request, designer, snapshot=sample(subject_id, currency_of(spec)),
                                    template_path=paths["template"], confirmed=confirmed, history=history)
        out = result.as_dict()
        if question is not None:
            out["question_index"] = question
        proposal_id = None
        if result.spec is not None and result.status in ("ready", "needs_confirmation"):
            with database() as connection:
                proposal_id = store.add_proposal(connection, str(draft_id), expected, result.request, out,
                                                 result.spec, actor(user))
        return {**out, "proposal_id": proposal_id}

    @app.post("/api/factsheet-designs/{subject_id}/drafts/{draft_id}/edits")
    def request_edit(subject_id: str, draft_id: uuid.UUID, body: EditBody,
                     user: dict[str, Any] = Depends(design_user)) -> dict[str, Any]:
        subject(subject_id, user)
        return propose(subject_id, draft_id, body.request, body.expected_version, body.confirmed, user)

    @app.post("/api/factsheet-designs/{subject_id}/drafts/{draft_id}/answers")
    def answer_question(subject_id: str, draft_id: uuid.UUID, body: AnswerBody,
                        user: dict[str, Any] = Depends(design_user)) -> dict[str, Any]:
        subject(subject_id, user)
        with database() as connection:
            row = load(connection, subject_id, draft_id)
            questions = row["build"].get("questions", [])
            if body.question >= len(questions):
                raise HTTPException(status_code=404, detail="Question not found")
            if not body.answer.strip():
                build = dict(row["build"])
                build["answered"] = sorted(set(build.get("answered") or []) | {body.question})
                store.update_build(connection, tenant_id, str(draft_id), build)
                return {"status": "answered", "proposal_id": None}
        asked = questions[body.question]
        request = (f"The design asked (page {asked.get('page')}): {asked['question']} "
                   f"The owner answered: {body.answer.strip()}")
        return propose(subject_id, draft_id, request[:editor.MAX_REQUEST_CHARS], body.expected_version, False, user,
                       question=body.question)

    def proposal_row(connection: Any, subject_id: str, draft_id: uuid.UUID, proposal_id: uuid.UUID) -> dict:
        load(connection, subject_id, draft_id)
        found = store.proposal(connection, str(draft_id), str(proposal_id))
        if found is None:
            raise HTTPException(status_code=404, detail="Proposal not found")
        return found

    @app.get("/api/factsheet-designs/{subject_id}/drafts/{draft_id}/proposals/{proposal_id}/{side}.pdf")
    def proposal_preview(subject_id: str, draft_id: uuid.UUID, proposal_id: uuid.UUID, side: str,
                         user: dict[str, Any] = Depends(design_user)) -> Response:
        subject(subject_id, user)
        if side not in ("before", "after"):
            raise HTTPException(status_code=404, detail="Not found")
        with database() as connection:
            row = load(connection, subject_id, draft_id)
            found = proposal_row(connection, subject_id, draft_id, proposal_id)
            spec = found["spec"] if side == "after" else store.version(
                connection, str(draft_id), found["base_version"])["spec"]
            files = template_bytes(connection, row)
        pdf, result = render(spec, files, subject_id)
        if not pdf:
            raise HTTPException(status_code=422, detail=result.get("error") or "This version did not render.")
        return pdf_response(pdf, f"proposal-{side}.pdf")

    @app.post("/api/factsheet-designs/{subject_id}/drafts/{draft_id}/proposals/{proposal_id}/accept")
    def accept_proposal(subject_id: str, draft_id: uuid.UUID, proposal_id: uuid.UUID, body: AcceptBody,
                        user: dict[str, Any] = Depends(design_user)) -> dict[str, Any]:
        subject(subject_id, user)
        with database() as connection:
            row = load(connection, subject_id, draft_id)
            found = proposal_row(connection, subject_id, draft_id, proposal_id)
            if found["status"] != "pending" or found["spec"] is None:
                raise HTTPException(status_code=409, detail="This proposal was already closed.")
            details = found["proposal"]
            if details.get("guarded") and not body.confirmed:
                raise HTTPException(status_code=409, detail="This change touches protected wording or figures; "
                                                            "confirm it to go ahead.")
            question = details.get("question_index")
            number = store.add_version(
                connection, tenant_id, str(draft_id), spec=found["spec"], spec_sha256=spec_sha256(found["spec"]),
                source="answer" if question is not None else "editor", summary=details.get("summary") or
                found["request"], changes=details.get("changes", []), checks=details.get("checks"),
                actor=actor(user), expected_version=found["base_version"])
            store.close_proposal(connection, str(draft_id), str(proposal_id), "accepted")
            if question is not None:
                build = dict(row["build"])
                build["answered"] = sorted(set(build.get("answered") or []) | {int(question)})
                store.update_build(connection, tenant_id, str(draft_id), build)
        return {"current_version": number}

    @app.post("/api/factsheet-designs/{subject_id}/drafts/{draft_id}/proposals/{proposal_id}/discard")
    def discard_proposal(subject_id: str, draft_id: uuid.UUID, proposal_id: uuid.UUID,
                         user: dict[str, Any] = Depends(design_user)) -> dict[str, Any]:
        subject(subject_id, user)
        with database() as connection:
            proposal_row(connection, subject_id, draft_id, proposal_id)
            store.close_proposal(connection, str(draft_id), str(proposal_id), "discarded")
        return {"discarded": True}

    @app.post("/api/factsheet-designs/{subject_id}/drafts/{draft_id}/undo")
    def undo(subject_id: str, draft_id: uuid.UUID, body: VersionBody,
             user: dict[str, Any] = Depends(design_user)) -> dict[str, Any]:
        subject(subject_id, user)
        with database() as connection:
            load(connection, subject_id, draft_id)
            current = store.version(connection, str(draft_id), body.expected_version)
            if current["parent_version"] is None:
                raise HTTPException(status_code=409, detail="This is the first version; there is nothing to undo.")
            parent = store.version(connection, str(draft_id), current["parent_version"])
            # The undo version takes the restored version's own parent, so undoing again steps further back.
            number = store.add_version(
                connection, tenant_id, str(draft_id), spec=parent["spec"], spec_sha256=parent["spec_sha256"],
                source="undo", summary=f"Back to version {parent['version']}", changes=[], checks=parent["checks"],
                actor=actor(user), expected_version=body.expected_version, parent_version=parent["parent_version"])
        return {"current_version": number}

    @app.post("/api/factsheet-designs/{subject_id}/drafts/{draft_id}/submit")
    def submit(subject_id: str, draft_id: uuid.UUID, body: VersionBody,
               user: dict[str, Any] = Depends(design_user)) -> dict[str, Any]:
        subject(subject_id, user)
        with database() as connection:
            row = load(connection, subject_id, draft_id)
            if row["status"] != "review":
                raise HTTPException(status_code=409, detail="Only a design under review can be submitted.")
            if int(row["current_version"]) != body.expected_version:
                raise HTTPException(status_code=409, detail="The design changed since you opened it. Reload and try again.")
            current = store.version(connection, str(draft_id), body.expected_version)
            if (current["checks"] or {}).get("status") == "fail":
                raise HTTPException(status_code=409, detail="This version fails its checks; fix it before submitting.")
            if onboarding_case(subject_id) is not None:
                # Still being onboarded: the onboarding run renders its historical factsheets from this design.
                store.set_status(connection, tenant_id, str(draft_id), "submitted")
                return {"status": "submitted", "used_for": "onboarding"}
            description = (f"New factsheet design from the factsheet designer: {row['title']}. "
                           f"Draft {draft_id}, version {current['version']}, spec {current['spec_sha256']}.")
            try:
                request = report_design_updates.create_request(connection, tenant_id, subject_id, uuid.uuid4(),
                                                               description, actor(user))
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            store.set_status(connection, tenant_id, str(draft_id), "submitted")
        return {"status": "submitted", "request_id": str(request["request_id"])}


ONBOARDING_NOTE = """<div class="card wide"><div class="label">Part of onboarding</div><p>This account is still being onboarded. The design you submit here is the one its historical factsheets are rendered from, for you to review and approve on the <a href="{CASE_PAGE}">onboarding case</a>. Until you approve them there, submitting a newer design renders them again.</p></div>
"""

PAGE_BODY = """<div class="card wide fd"><div class="label">New design for {SUBJECT}</div>
<form id="fd-new" class="fd-form"><label>Title <input name="title" required maxlength="120" placeholder="For example: Greater China factsheet"></label>
<label>Currency <input name="currency" value="{CURRENCY}" maxlength="3" pattern="[A-Za-z]{3}" style="width:5em"></label>
<label>Template (PDF, required) <input type="file" name="template" accept="application/pdf" required></label>
<label>Completed example (PDF, optional) <input type="file" name="example" accept="application/pdf"></label>
<label>Other months' examples (PDF, optional, up to 6) <input type="file" name="more_examples" accept="application/pdf" multiple></label>
<div><button class="button" type="submit">Draft the design</button> <span class="muted" role="status" id="fd-new-status"></span></div></form></div>
<div class="card wide"><div class="label">Designs</div><div id="fd-list" class="muted">Loading…</div></div>
<div class="card wide" id="fd-draft" hidden></div>"""

PAGE_SCRIPT = r"""<style>.fd-form{display:grid;gap:8px;max-width:640px}.fd-form label{display:grid;gap:4px}
.fd-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}.fd-cols iframe{width:100%;height:640px;border:1px solid var(--border,#ccc);border-radius:8px}
.fd-q{margin:6px 0;padding:8px;border-radius:8px;background:var(--surface,#f4f5f7)}.fd-q.done{opacity:.6}.fd textarea,#fd-draft textarea{width:100%;min-height:60px;font:inherit;box-sizing:border-box}
.fd-pill{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12px;border:1px solid var(--border,#ccc)}</style><script>
(()=>{'use strict';const S={SUBJECT_JSON},base=`/api/factsheet-designs/${encodeURIComponent(S)}/drafts`;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const call=async(url,opts={})=>{const r=await fetch(url,{credentials:'same-origin',...opts});let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw new Error(d.detail||'That did not go through. Try again.');return d};
const post=(url,body)=>call(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const box=document.querySelector('#fd-draft');let state=null,highlightOn=false;
async function list(){const d=await call(base).catch(e=>({drafts:[],error:e.message}));const el=document.querySelector('#fd-list');
 el.innerHTML=d.error?esc(d.error):d.drafts.length?d.drafts.map(x=>`<div><a href="#" data-id="${x.draft_id}">${esc(x.title)}</a> <span class="fd-pill">${esc(x.status)}</span> <span class="muted">version ${x.current_version} · ${esc(x.updated_at)}</span></div>`).join(''):'No designs yet.';
 el.querySelectorAll('[data-id]').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();open(a.dataset.id)}))}
const preview=v=>`${base}/${state.draft_id}/versions/${v}/preview.pdf${highlightOn?'?highlight_parts=true':''}`;
async function open(id){state=await call(`${base}/${id}`);box.hidden=false;const s=state,editable=s.status==='review'||s.status==='failed';
 const checks=s.checks||{},failed=(checks.checks||[]).filter(c=>c.status!=='pass');
 box.innerHTML=`<div class="label">${esc(s.title)} · version ${s.current_version} <span class="fd-pill">${esc(s.status)}</span></div>
 <p>${s.build_status==='pass'?'The draft passed every check.':s.build_status==='fail'?'The draft fails a check; ask for a change below or answer the questions.':'The draft needs your review.'} ${s.has_example?'':'It was drafted from the template alone, so check every figure.'}</p>
 ${failed.length?`<p><strong>Checks to look at:</strong> ${failed.map(c=>esc(c.id+": "+(c.detail||c.status))).join('; ')}</p>`:''}
 ${(s.checker&&s.checker.objections||[]).length?`<p><strong>The checker agent noted:</strong></p><ul>${s.checker.objections.map(o=>`<li>${esc(o)}</li>`).join('')}</ul>`:''}
 <div id="fd-questions">${s.questions.map(q=>`<div class="fd-q${q.answered?' done':''}" data-q="${q.index}"><div>${esc(q.question)}</div>${q.answered||!editable?'':`<textarea maxlength="2000" placeholder="Your answer, in plain words"></textarea><button class="button" data-a="answer">Answer</button> <button class="button" data-a="keep">It's right as it is</button>`}</div>`).join('')}</div>
 <div><label><input type="checkbox" id="fd-hl" ${highlightOn?'checked':''}> Show what each part reads (figures blue, fixed words grey, tables green)</label></div>
 <iframe id="fd-preview" title="Preview" src="${preview(s.current_version)}" style="width:100%;height:720px;border:1px solid var(--border,#ccc);border-radius:8px"></iframe>
 ${editable?`<h3>Change something</h3>${s.agents_available?'':'<p class="muted">Edits in plain words need the design agents, which are not configured here.</p>'}<textarea id="fd-req" maxlength="2000" placeholder="For example: make the holdings table text a little bigger"></textarea><div><button class="button" id="fd-ask">Show me the change</button> <button class="button" id="fd-undo" ${s.current_version>1?'':'disabled'}>Undo last change</button> <button class="button" id="fd-submit">Submit for release</button></div>`:''}
 <div id="fd-proposal" role="status"></div>
 <details><summary>History</summary><ol>${s.versions.map(v=>`<li>${esc(v.summary)} <span class="muted">(${esc(v.source)}, ${esc(v.created_at)})</span>${(v.changes||[]).length?'<ul>'+v.changes.map(c=>`<li>${esc(c)}</li>`).join('')+'</ul>':''}</li>`).join('')}</ol></details>`;
 box.querySelector('#fd-hl').addEventListener('change',e=>{highlightOn=e.target.checked;box.querySelector('#fd-preview').src=preview(s.current_version)});
 box.querySelectorAll('[data-q]').forEach(q=>q.querySelectorAll('[data-a]').forEach(b=>b.addEventListener('click',async()=>{const t=q.querySelector('textarea');
  try{const d=await post(`${base}/${s.draft_id}/answers`,{question:+q.dataset.q,expected_version:s.current_version,answer:b.dataset.a==='keep'?'':t.value});d.status==='answered'?open(s.draft_id):show(d)}catch(e){alert(e.message)}})));
 if(!editable)return;
 box.querySelector('#fd-ask').addEventListener('click',async e=>{const out=box.querySelector('#fd-proposal');e.target.disabled=true;out.textContent='Working out the change…';
  try{show(await post(`${base}/${s.draft_id}/edits`,{request:box.querySelector('#fd-req').value,expected_version:s.current_version}))}catch(err){out.textContent=err.message}finally{e.target.disabled=false}});
 box.querySelector('#fd-undo').addEventListener('click',async()=>{try{await post(`${base}/${s.draft_id}/undo`,{expected_version:s.current_version});open(s.draft_id)}catch(e){alert(e.message)}});
 box.querySelector('#fd-submit').addEventListener('click',async()=>{if(!confirm('Submit this version? It is checked again before any report uses it.'))return;
  try{await post(`${base}/${s.draft_id}/submit`,{expected_version:s.current_version});open(s.draft_id);list()}catch(e){alert(e.message)}})}
function show(p){const out=box.querySelector('#fd-proposal'),s=state;
 if(p.status==='question'){out.innerHTML=`<p><strong>Before I change anything:</strong> ${esc(p.question)}</p>`;return}
 if(!p.proposal_id){out.innerHTML=`<p>${esc(p.error||p.summary||'That change could not be made.')}</p>${(p.changes||[]).length?'<ul>'+p.changes.map(c=>`<li>${esc(c)}</li>`).join('')+'</ul>':''}`;return}
 const u=`${base}/${s.draft_id}/proposals/${p.proposal_id}`;
 out.innerHTML=`<h3>Proposed change</h3><p>${esc(p.summary)}</p><ul>${p.changes.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>
 ${p.guarded.length?`<p><strong>This touches protected parts:</strong> ${p.guarded.map(esc).join('; ')}</p><label><input type="checkbox" id="fd-confirm"> I confirm these protected changes</label>`:''}
 <div class="fd-cols"><div><div class="label">Before</div><iframe title="Before" src="${u}/before.pdf"></iframe></div><div><div class="label">After</div><iframe title="After" src="${u}/after.pdf"></iframe></div></div>
 <button class="button" id="fd-accept">Keep this change</button> <button class="button" id="fd-discard">Discard</button>`;
 out.querySelector('#fd-accept').addEventListener('click',async()=>{const c=out.querySelector('#fd-confirm');try{await post(`${u}/accept`,{confirmed:!!(c&&c.checked)});open(s.draft_id);list()}catch(e){alert(e.message)}});
 out.querySelector('#fd-discard').addEventListener('click',async()=>{await post(`${u}/discard`,{}).catch(()=>{});out.innerHTML=''})}
document.querySelector('#fd-new').addEventListener('submit',async e=>{e.preventDefault();const f=e.target,st=document.querySelector('#fd-new-status'),b=f.querySelector('button');
 b.disabled=true;st.textContent='Reading the template and drafting the design. This can take a few minutes…';
 try{const d=await call(base,{method:'POST',body:new FormData(f)});st.textContent='Drafted.';f.reset();await list();open(d.draft_id)}catch(err){st.textContent=err.message}finally{b.disabled=false}});
list();})();</script>"""
