"""The factsheet design agents: reader, draft, compare, edits, builder loop, editor, storage, website and agent side.

Everything runs on synthetic templates (factsheet_design_fixtures) with fake
agents, so no client template and no model is needed. A last group re-reads the
real account templates when they are on this machine (private, never committed).
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import factsheet_design_fixtures as fx  # noqa: E402
from ibkr_etl import factsheet_design_store as store  # noqa: E402
from ibkr_etl import factsheet_design_web as web  # noqa: E402
from ibkr_etl.factsheet_spec import builder, compare, draft, editor, patch, reader  # noqa: E402
from ibkr_etl.factsheet_spec.spec import validate_spec  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _ops(spec):
    return [op for page in spec["pages"] for layer in page["layers"] for op in layer.get("ops", [])]


def _op(spec, binding):
    return next(op for op in _ops(spec) if isinstance(op.get("value"), dict) and op["value"].get("bind") == binding)


def _edit(op, target="", **fields):
    edit = {"op": op, "target": target, "property": "", "column": -1, "page": 0, "dx": 0, "dy": 0, "key": "",
            "value_json": ""}
    edit.update(fields)
    return edit


class Files(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [p.unlink() for p in self.folder.iterdir()] and self.folder.rmdir())
        self.template = fx.template(self.folder)
        self.example = fx.example(self.folder)


class ReaderTests(Files):
    def test_finds_the_values_and_the_holdings_table_from_template_and_example(self):
        found = reader.read(self.template, self.example)
        bound = {f.text: [c["binding"] for c in f.candidates][:1] for f in found["fields"] if f.kind != "text"}
        self.assertEqual(bound["[NAV]"], ["nav"])
        self.assertEqual(bound["[YTD]"], ["ytd_return"])
        self.assertEqual(bound["[SI]"], ["since_inception_return"])
        self.assertEqual(bound["[Date]"], ["period_end"])
        [table] = found["tables"]
        self.assertEqual((table["binding"], table["rows"], len(table["columns"])), ("top_holdings", 5, 3))
        # Measured from the header band; without a grid, columns start just before their text.
        self.assertEqual((table["x"], table["top"], table["header_height"]), (40, 664, 14))
        self.assertEqual([round(c["width"]) for c in table["columns"]], [180, 118, 102])
        self.assertEqual([c["align"] for c in table["columns"]], ["left", "left", "right"])

    def test_without_an_example_reads_the_placeholders_and_the_row_names(self):
        found = reader.read(self.template)
        [table] = found["tables"]
        self.assertEqual((table["binding"], len(table["columns"])), ("top_holdings", 3))
        date = next(f for f in found["fields"] if f.text == "[Date]")
        self.assertEqual(date.kind, "date")
        self.assertGreater(date.room, 200)  # a long month name has the whole line to print in

    def test_a_label_that_never_changes_is_not_a_value(self):
        other = fx.example(self.folder, "aug.pdf", date="31 August 2026", figures=("120,000.00", "6.10%", "18.20%"))
        found = reader.read(self.template, self.example, more_examples=[other])
        self.assertEqual(found["months_compared"], 2)
        self.assertFalse(any(f.text == "Net asset value" for f in found["fields"] if f.kind != "label"))


class DraftAndCompareTests(Files):
    def test_the_draft_is_a_valid_spec_and_reproduces_the_example_layout(self):
        result = builder.build(self.template, self.example, None, design_id="synthetic", title="Example",
                               currency="USD")
        validate_spec(result.spec)
        self.assertEqual(result.rounds[-1].comparison["score"], 1.0)
        self.assertFalse([s for s in result.stress if s["status"] == "fail"])
        self.assertEqual(result.status, "review")  # label-bound figures are put to the owner as questions
        self.assertTrue(any("net asset value" in q["question"] for q in result.questions))
        table = next(op for op in _ops(result.spec) if op["op"] == "table")
        self.assertEqual([c["value"]["field"] for c in table["columns"]], ["company_name", "sector", "weight"])
        self.assertEqual(table["style"]["text"], "#000000")

    def test_compare_names_what_moved_and_what_changed_style(self):
        result = builder.build(self.template, self.example, None, design_id="synthetic", title="Example",
                               currency="USD")
        spec = copy.deepcopy(result.spec)
        _op(spec, "nav")["y"] -= 30
        _op(spec, "ytd_return")["size"] = 9
        found = reader.read(self.template, self.example)
        pdf, _, comparison = builder._render(spec, builder.stress.snapshot(), self.template, None,
                                             found["example"], [])
        kinds = {f["kind"] for f in comparison["findings"]}
        self.assertIn("style", kinds)
        self.assertTrue(kinds & {"moved", "missing"})
        self.assertLess(comparison["score"], 1.0)

    def test_changed_regions_find_the_edit(self):
        result = builder.build(self.template, None, None, design_id="synthetic", title="Example", currency="USD")
        spec = copy.deepcopy(result.spec)
        _op(spec, "nav")["size"] = 16
        sample = builder.stress.snapshot()
        before, _ = builder.render_preview(result.spec, sample, template_path=self.template)
        after, _ = builder.render_preview(spec, sample, template_path=self.template)
        regions = compare.changed_regions(editor._inventory(before), editor._inventory(after))
        self.assertTrue(regions)
        self.assertTrue(all(r["page"] == 1 for r in regions))


class PatchTests(Files):
    def setUp(self):
        super().setUp()
        self.spec = builder.build(self.template, self.example, None, design_id="synthetic", title="Example",
                                  currency="USD").spec

    def test_edits_apply_and_are_described_by_code(self):
        nav = _op(self.spec, "nav")["id"]
        applied = patch.apply(self.spec, [_edit("set", nav, property="size", value_json="14"),
                                          _edit("move", nav, dx=5)])
        self.assertEqual(_op(applied.spec, "nav")["size"], 14)
        self.assertEqual(len(applied.changes), 2)
        self.assertNotEqual(applied.spec, self.spec)
        self.assertEqual(_op(self.spec, "nav")["size"], 12)  # the original is never changed

    def test_anything_outside_the_vocabulary_is_refused(self):
        nav = _op(self.spec, "nav")["id"]
        for bad in (_edit("set", nav, property="python", value_json='"x"'), _edit("set", "nope", property="size",
                                                                                  value_json="9"),
                    _edit("set", nav, property="value", value_json='{"bind": "not_a_metric"}')):
            with self.assertRaises(patch.PatchError):
                patch.apply(self.spec, [bad])

    def test_re_pointing_a_figure_is_guarded(self):
        nav = _op(self.spec, "nav")["id"]
        edit = _edit("set", nav, property="value", value_json='{"bind": "ytd_return"}')
        with self.assertRaises(patch.PatchError):
            patch.apply(self.spec, [edit])
        applied = patch.apply(self.spec, [edit], allow_guarded=True)
        self.assertTrue(applied.guarded)


class FakeAgent:
    def __init__(self, steps=(), objections=(), edits=None, question=""):
        self.steps, self.objections, self.edits, self.question = list(steps), list(objections), edits, question
        self.facts = []

    def build_step(self, facts):
        self.facts.append(("build", facts))
        return self.steps.pop(0) if self.steps else {"edits": [], "notes": ""}

    def check(self, facts):
        self.facts.append(("check", facts))
        return {"summary": "Looks right.", "objections": self.objections}

    def edit(self, facts):
        self.facts.append(("edit", facts))
        return {"summary": "Done.", "question": self.question, "edits": self.edits or []}


class BuilderLoopTests(Files):
    def test_the_builder_agent_repairs_what_the_comparison_found(self):
        example = fx.example(self.folder, "big.pdf", figure_size=14)
        first = builder.build(self.template, example, None, design_id="synthetic", title="Example", currency="USD")
        self.assertLess(first.rounds[-1].comparison["score"], builder.GOOD_ENOUGH)
        ids = [_op(first.spec, name)["id"] for name in ("nav", "ytd_return", "since_inception_return")]
        agent = FakeAgent(steps=[{"edits": [_edit("set", "nope", property="size", value_json="14")], "notes": ""},
                                 {"edits": [_edit("set", i, property="size", value_json="14") for i in ids],
                                  "notes": "the example's figures are 14pt"}])
        result = builder.build(self.template, example, None, design_id="synthetic", title="Example", currency="USD",
                               agent=agent)
        self.assertEqual(result.rounds[0].rejected is not None, True)
        self.assertEqual(agent.facts[1][1]["previous_edit_rejected"], result.rounds[0].rejected)
        self.assertEqual(result.rounds[-1].comparison["score"], 1.0)
        self.assertEqual(_op(result.spec, "nav")["size"], 14)
        check = next(f for kind, f in agent.facts if kind == "check")
        self.assertNotIn("notes", check)  # the checker never sees the builder's reasoning

    def test_the_checker_can_only_add_objections(self):
        result = builder.build(self.template, self.example, None, design_id="synthetic", title="Example",
                               currency="USD", agent=FakeAgent(objections=["The date looks too small."]))
        self.assertEqual(result.status, "review")
        self.assertEqual(result.checker["objections"], ["The date looks too small."])


class MeasuredDesignTests(Files):
    def _measured(self, currency="KRW"):
        import hashlib
        spec = copy.deepcopy(builder.build(self.template, self.example, None, design_id="m", title="M",
                                           currency=currency).spec)
        spec.update(design_id="measured", title="Measured", currencies=[currency], notes="measured by hand",
                    template_sha256=hashlib.sha256(Path(self.template).read_bytes()).hexdigest())
        return spec

    def test_a_template_with_a_measured_design_starts_from_it_with_no_repair_rounds(self):
        measured = self._measured()
        agent = FakeAgent(steps=[{"edits": [_edit("delete", "anything")], "notes": ""}])
        with mock.patch.object(builder, "built_in_designs", return_value=["measured"]), \
                mock.patch.object(builder, "load_design", return_value=copy.deepcopy(measured)):
            result = builder.build(self.template, self.example, None, design_id="kiwoom-1", title="Korea iStrategy",
                                   currency="KRW", agent=agent)
        self.assertEqual(result.spec["design_id"], "kiwoom-1")
        self.assertEqual(result.spec["title"], "Korea iStrategy")
        self.assertEqual(result.spec["notes"], "measured by hand")
        self.assertEqual(result.questions, [])
        self.assertIsNone(result.rounds[-1].comparison)
        self.assertEqual([kind for kind, _ in agent.facts], ["check"])  # never asked to repair it
        self.assertIsNotNone(result.preview_pdf)

    def test_another_currency_or_template_is_read_as_before(self):
        measured = self._measured("KRW")
        with mock.patch.object(builder, "built_in_designs", return_value=["measured"]), \
                mock.patch.object(builder, "load_design", return_value=copy.deepcopy(measured)):
            self.assertIsNone(builder.measured_design(self.template, "USD"))
            self.assertIsNotNone(builder.measured_design(self.template, "KRW"))
            self.assertIsNone(builder.measured_design(self.example, "KRW"))

    def test_the_korea_design_is_the_account_design_pinned_to_its_template_in_krw(self):
        from ibkr_etl.factsheet_spec.spec import load_design
        korea, account = load_design("korea-istrategy"), load_design("account-owner-template")
        self.assertEqual(korea["currencies"], ["KRW"])
        self.assertEqual(korea["template_sha256"],
                         "c286de93ca667ba1acc9d8d29647a8b4f9725a2d2235a45f27928b647542e780")
        self.assertEqual(korea["pages"], account["pages"])

    def test_an_empty_overlay_renders(self):
        from ibkr_etl.factsheet_spec import stress
        from ibkr_etl.factsheet_spec.engine import render_preview
        spec = self._measured("USD")
        for page in spec["pages"]:
            for layer in page["layers"]:
                if layer["kind"] == "overlay":
                    layer["ops"] = []
        pdf, result = render_preview(spec, stress.snapshot(currency="USD"), template_path=self.template)
        self.assertIsNone(result.get("error"))
        self.assertTrue(pdf)


class EditorTests(Files):
    def setUp(self):
        super().setUp()
        self.spec = builder.build(self.template, self.example, None, design_id="synthetic", title="Example",
                                  currency="USD").spec
        self.sample = builder.stress.snapshot()
        self.nav = _op(self.spec, "nav")["id"]

    def propose(self, agent, **kwargs):
        return editor.propose(self.spec, "make the fund size bigger", agent, snapshot=self.sample,
                              template_path=self.template, **kwargs)

    def test_a_ready_change_has_code_written_changes_and_both_previews(self):
        proposal = self.propose(FakeAgent(edits=[_edit("set", self.nav, property="size", value_json="16")]))
        self.assertEqual(proposal.status, "ready")
        self.assertEqual(len(proposal.changes), 1)
        self.assertTrue(proposal.before_pdf.startswith(b"%PDF") and proposal.after_pdf.startswith(b"%PDF"))
        self.assertTrue(proposal.regions)
        self.assertEqual(_op(self.spec, "nav")["size"], 12)  # nothing saved

    def test_a_question_changes_nothing(self):
        proposal = self.propose(FakeAgent(question="Which figure do you mean, the fund size or the NAV per unit?"))
        self.assertEqual((proposal.status, proposal.spec), ("question", None))

    def test_guarded_changes_wait_for_confirmation(self):
        agent = FakeAgent(edits=[_edit("set", self.nav, property="value", value_json='{"bind": "ytd_return"}')])
        self.assertEqual(self.propose(agent).status, "needs_confirmation")
        self.assertEqual(self.propose(agent, confirmed=True).status, "ready")

    def test_an_edit_outside_the_vocabulary_is_invalid(self):
        proposal = self.propose(FakeAgent(edits=[_edit("set", self.nav, property="script", value_json='"x"')]))
        self.assertEqual(proposal.status, "invalid")
        self.assertTrue(proposal.error)


SQLITE_DDL = """
CREATE TABLE factsheet_design_files (tenant_id TEXT, sha256 TEXT, content_type TEXT, byte_count INTEGER,
    content BLOB, created_at TEXT, PRIMARY KEY (tenant_id, sha256));
CREATE TABLE factsheet_design_drafts (draft_id TEXT PRIMARY KEY, tenant_id TEXT, subject_id TEXT, title TEXT,
    template_sha256 TEXT, example_sha256 TEXT, more_examples_json TEXT, status TEXT, current_version INTEGER,
    build_json TEXT, created_by TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE factsheet_design_versions (tenant_id TEXT, draft_id TEXT, version INTEGER, spec_json TEXT,
    spec_sha256 TEXT, parent_version INTEGER, source TEXT, summary TEXT, changes_json TEXT, checks_json TEXT,
    created_by TEXT, created_at TEXT, PRIMARY KEY (draft_id, version));
CREATE TABLE factsheet_design_proposals (proposal_id TEXT PRIMARY KEY, tenant_id TEXT, draft_id TEXT,
    base_version INTEGER, request TEXT, proposal_json TEXT, spec_json TEXT, status TEXT, created_by TEXT,
    created_at TEXT);
"""


def _sqlite() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(SQLITE_DDL)
    return connection


class StoreTests(unittest.TestCase):
    def test_versions_append_and_a_stale_editor_is_refused(self):
        db = _sqlite()
        self.addCleanup(db.close)
        store.put_file(db, "t", "a" * 64, b"%PDF-1")
        store.put_file(db, "t", "a" * 64, b"%PDF-1")  # stored once
        draft_id = store.create_draft(db, "t", "S1", title="T", template_sha256="a" * 64, example_sha256=None,
                                      more_examples=[], build={"checks": None}, spec={"v": 1}, spec_sha256="b" * 64,
                                      actor="o", status="review")
        self.assertEqual(store.get_file(db, "t", "a" * 64), b"%PDF-1")
        two = store.add_version(db, "t", draft_id, spec={"v": 2}, spec_sha256="c" * 64, source="editor",
                                summary="s", changes=["x"], checks=None, actor="o", expected_version=1)
        self.assertEqual(two, 2)
        with self.assertRaises(store.DesignStoreError):
            store.add_version(db, "t", draft_id, spec={"v": 3}, spec_sha256="d" * 64, source="editor",
                              summary="s", changes=[], checks=None, actor="o", expected_version=1)
        self.assertEqual([v["version"] for v in store.versions(db, draft_id)], [1, 2])
        self.assertIsNone(store.draft(db, "other-tenant", draft_id))
        store.set_status(db, "t", draft_id, "submitted")
        with self.assertRaises(store.DesignStoreError):
            store.add_version(db, "t", draft_id, spec={"v": 3}, spec_sha256="d" * 64, source="editor",
                              summary="s", changes=[], checks=None, actor="o", expected_version=2)

    def test_the_migration_is_additive_and_tenant_isolated(self):
        sql = (ROOT / "migrations/postgres/067_factsheet_design_agents.sql").read_text()
        self.assertNotRegex(sql.upper(), r"\bDROP TABLE|\bDELETE\b|\bALTER TABLE \w+ (DROP|RENAME)")
        for table in ("factsheet_design_files", "factsheet_design_drafts", "factsheet_design_versions",
                      "factsheet_design_proposals"):
            self.assertIn(f"CREATE POLICY tenant_isolation ON {table}", sql)
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", sql)
        bootstrap = (ROOT / "scripts/bootstrap_postgres.py").read_text()
        self.assertIn("GRANT SELECT, INSERT ON TABLE factsheet_design_files, factsheet_design_versions", bootstrap)

    def test_the_release_waits_for_the_migration_before_the_switch(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("website_release", ROOT / "deploy/website_release.py")
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        self.assertEqual(release.SWITCHES_NEEDING_MIGRATIONS["FACTSHEET_DESIGN_AGENTS_ENABLED"],
                         ("067_factsheet_design_agents.sql",))


class FakeApi:
    def __init__(self, edits=None):
        self.calls, self.edits = [], edits or []

    def request(self, method, path, timeout=30.0, **kwargs):
        self.calls.append((path, kwargs["json"]))
        if path.endswith("build-step"):
            return {"answer": {"edits": [], "notes": ""}}
        if path.endswith("check"):
            return {"answer": {"summary": "ok", "objections": []}}
        return {"answer": {"summary": "Bigger fund size.", "question": "", "edits": self.edits}}


class WebTests(Files):
    OWNER = {"object_id": "00000000-0000-0000-0000-000000000001", "email": "o@example.com", "role": "owner"}

    def setUp(self):
        super().setUp()
        self.db = _sqlite()
        self.addCleanup(self.db.close)
        self.api = FakeApi()
        self.allowed = {"S1"}
        self.onboarding = {}
        app = FastAPI()

        @contextlib.contextmanager
        def write_database():
            yield self.db
            self.db.commit()

        def check_subject(subject_id, user):
            if subject_id not in self.allowed:
                raise web.HTTPException(status_code=403, detail="Owner access is required")

        web.install(app, owner_user=lambda: self.OWNER, check_subject=check_subject, write_database=write_database,
                    portable=lambda c: c, render_page=lambda user, **k: f"<html>{k['content']}{k['script']}</html>",
                    tenant_id="t", agent_api=lambda: self.api, onboarding_case=self.onboarding.get)
        self.client = TestClient(app)
        patcher = mock.patch.dict(os.environ, {"FACTSHEET_DESIGN_AGENTS_ENABLED": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.base = "/api/factsheet-designs/S1/drafts"

    def create(self, **files):
        uploads = {"template": ("t.pdf", self.template.read_bytes(), "application/pdf")}
        uploads.update(files)
        response = self.client.post(self.base, data={"title": "Example Growth Fund", "currency": "usd"}, files=uploads)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["draft_id"]

    def test_switched_off_everything_is_not_found(self):
        with mock.patch.dict(os.environ, {"FACTSHEET_DESIGN_AGENTS_ENABLED": "false"}):
            self.assertEqual(self.client.get("/accounts/S1/factsheet-designer").status_code, 404)
            self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_another_account_and_a_non_pdf_are_refused(self):
        self.assertEqual(self.client.get("/api/factsheet-designs/S2/drafts").status_code, 403)
        bad = self.client.post(self.base, data={"title": "x"}, files={"template": ("t.pdf", b"hello", "text/plain")})
        self.assertEqual(bad.status_code, 422)

    def test_draft_review_edit_accept_undo_and_submit(self):
        page = self.client.get("/accounts/S1/factsheet-designer")
        self.assertIn("fd-new", page.text)
        draft_id = self.create(example=("e.pdf", self.example.read_bytes(), "application/pdf"))
        self.assertEqual([c[0] for c in self.api.calls], ["/v1/factsheet-designs/check"])
        state = self.client.get(f"{self.base}/{draft_id}").json()
        self.assertEqual((state["status"], state["current_version"]), ("review", 1))
        self.assertTrue(state["questions"])
        preview = self.client.get(f"{self.base}/{draft_id}/versions/1/preview.pdf")
        self.assertTrue(preview.content.startswith(b"%PDF"))
        marked = self.client.get(f"{self.base}/{draft_id}/versions/1/preview.pdf?highlight_parts=true")
        self.assertNotEqual(marked.content, preview.content)

        nav = next(o["id"] for o in state["outline"] if o.get("value") == {"bind": "nav", "format": {
            "kind": "money", "digits": 2, "none": "N/A"}} or (isinstance(o.get("value"), dict)
                                                          and o["value"].get("bind") == "nav"))
        self.api.edits = [_edit("set", nav, property="size", value_json="16")]
        proposal = self.client.post(f"{self.base}/{draft_id}/edits",
                                    json={"request": "make the fund size bigger", "expected_version": 1}).json()
        self.assertEqual(proposal["status"], "ready")
        for side in ("before", "after"):
            shown = self.client.get(f"{self.base}/{draft_id}/proposals/{proposal['proposal_id']}/{side}.pdf")
            self.assertTrue(shown.content.startswith(b"%PDF"))
        self.assertEqual(self.client.get(f"{self.base}/{draft_id}").json()["current_version"], 1)  # not yet
        accepted = self.client.post(f"{self.base}/{draft_id}/proposals/{proposal['proposal_id']}/accept", json={})
        self.assertEqual(accepted.json()["current_version"], 2)
        again = self.client.post(f"{self.base}/{draft_id}/proposals/{proposal['proposal_id']}/accept", json={})
        self.assertEqual(again.status_code, 409)
        stale = self.client.post(f"{self.base}/{draft_id}/edits", json={"request": "smaller", "expected_version": 1})
        self.assertEqual(stale.status_code, 409)

        undone = self.client.post(f"{self.base}/{draft_id}/undo", json={"expected_version": 2}).json()
        self.assertEqual(undone["current_version"], 3)
        versions = self.client.get(f"{self.base}/{draft_id}").json()["versions"]
        self.assertEqual(versions[0]["spec_sha256"], versions[2]["spec_sha256"])
        self.assertEqual([v["source"] for v in versions], ["builder", "editor", "undo"])
        # Undoing again steps further back, not forward to the edit: version 1 is the first.
        again = self.client.post(f"{self.base}/{draft_id}/undo", json={"expected_version": 3})
        self.assertEqual(again.status_code, 409, again.text)

        answered = self.client.post(f"{self.base}/{draft_id}/answers",
                                    json={"question": 0, "answer": "", "expected_version": 3}).json()
        self.assertEqual(answered["status"], "answered")
        self.assertTrue(self.client.get(f"{self.base}/{draft_id}").json()["questions"][0]["answered"])

        with mock.patch.object(web.report_design_updates, "create_request",
                               return_value={"request_id": "11111111-1111-1111-1111-111111111111"}) as created:
            submitted = self.client.post(f"{self.base}/{draft_id}/submit", json={"expected_version": 3})
        self.assertEqual(submitted.json()["status"], "submitted")
        self.assertIn(draft_id, created.call_args.args[4])
        refused = self.client.post(f"{self.base}/{draft_id}/edits", json={"request": "bigger", "expected_version": 3})
        self.assertEqual(refused.status_code, 409)

    def test_during_onboarding_the_submitted_design_is_for_the_case_not_a_design_request(self):
        live = self.client.get("/accounts/S1/factsheet-designer").text
        self.assertNotIn("Part of onboarding", live)
        self.assertIn('name="currency" value="USD"', live)
        self.onboarding["S1"] = {"case_id": "11111111-2222-3333-4444-555555555555", "reporting_currency": "HKD"}
        page = self.client.get("/accounts/S1/factsheet-designer").text
        self.assertIn("Part of onboarding", page)
        self.assertIn('href="/account-onboarding/cases/11111111-2222-3333-4444-555555555555"', page)
        self.assertIn('name="currency" value="HKD"', page)
        draft_id = self.create()
        with mock.patch.object(web.report_design_updates, "create_request") as created:
            submitted = self.client.post(f"{self.base}/{draft_id}/submit", json={"expected_version": 1})
        self.assertEqual(submitted.json(), {"status": "submitted", "used_for": "onboarding"})
        created.assert_not_called()
        self.assertEqual(self.client.get(f"{self.base}/{draft_id}").json()["status"], "submitted")

    def test_guarded_changes_need_confirmation_to_accept(self):
        draft_id = self.create()
        state = self.client.get(f"{self.base}/{draft_id}").json()
        nav = next(o["id"] for o in state["outline"] if isinstance(o.get("value"), dict)
                   and o["value"].get("bind") == "nav")
        self.api.edits = [_edit("set", nav, property="value", value_json='{"bind": "ytd_return"}')]
        proposal = self.client.post(f"{self.base}/{draft_id}/edits",
                                    json={"request": "show the YTD return there", "expected_version": 1}).json()
        self.assertEqual(proposal["status"], "needs_confirmation")
        url = f"{self.base}/{draft_id}/proposals/{proposal['proposal_id']}/accept"
        self.assertEqual(self.client.post(url, json={}).status_code, 409)
        self.assertEqual(self.client.post(url, json={"confirmed": True}).status_code, 200)

    def test_without_the_agent_service_the_draft_still_works(self):
        self.api = None
        draft_id = self.create()
        state = self.client.get(f"{self.base}/{draft_id}").json()
        self.assertFalse(state["agents_available"])
        edit = self.client.post(f"{self.base}/{draft_id}/edits", json={"request": "bigger", "expected_version": 1})
        self.assertEqual(edit.status_code, 503)

    def test_an_unreachable_agent_service_keeps_the_draft_and_explains_the_edit(self):
        import httpx

        class Down(FakeApi):
            def request(self, method, path, timeout=30.0, **kwargs):
                self.calls.append((path, kwargs["json"]))
                raise httpx.ConnectError("connection refused")

        self.api = Down()
        draft_id = self.create()
        state = self.client.get(f"{self.base}/{draft_id}").json()
        self.assertIn("unavailable", state["checker"])
        edit = self.client.post(f"{self.base}/{draft_id}/edits", json={"request": "bigger", "expected_version": 1})
        self.assertEqual(edit.status_code, 502, edit.text)

    def test_the_agent_calls_share_one_time_budget(self):
        agent = web.WebDesignAgent(FakeApi(), "u", "S1", budget_seconds=0)
        self.assertEqual(agent.build_step({})["edits"], [])
        with self.assertRaises(web.HTTPException) as raised:
            agent.check({})
        self.assertEqual(raised.exception.status_code, 504)
        timeouts = []

        class Timed(FakeApi):
            def request(self, method, path, timeout=30.0, **kwargs):
                timeouts.append(timeout)
                return super().request(method, path, timeout=timeout, **kwargs)

        web.WebDesignAgent(Timed(), "u", "S1").check({})
        self.assertLessEqual(timeouts[0], web.AGENT_SECONDS)

    def test_the_designer_page_is_not_taken_by_the_account_page_route(self):
        import ibkr_etl.web as site
        from starlette.routing import Match

        scope = {"type": "http", "path": "/accounts/S1/factsheet-designer", "method": "GET"}
        first = next(r for r in site.app.router.routes if r.matches(scope)[0] == Match.FULL)
        self.assertEqual(first.endpoint.__name__, "designer_page")


def _final(payload):
    message = SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")])
    return SimpleNamespace(status="completed", output=[message], output_text=json.dumps(payload),
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


class FakeModel:
    def __init__(self, responses):
        self.responses = SimpleNamespace(create=self._create)
        self.queue, self.requests = list(responses), []

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.queue.pop(0)


class AgentSideTests(unittest.TestCase):
    def _agent(self, responses):
        from wealthryse_agent.config import ModelSettings
        from wealthryse_agent.runtime import Agent

        self.model = FakeModel(responses)
        return Agent(ModelSettings(endpoint="https://example.invalid", deployment="analyst", router_deployment="",
                                   region="southeastasia", processing_scope="apac-data-zone",
                                   managed_identity_client_id="x", max_tool_calls=3), model_client=self.model)

    def test_each_agent_answers_in_a_strict_schema_and_the_checker_never_sees_notes(self):
        from wealthryse_agent.factsheet_design import build_step, check_design, edit_design

        answer, turn = build_step(self._agent([_final({"notes": "n", "edits": []})]), {"round": 1})
        self.assertEqual(answer["edits"], [])
        request = self.model.requests[0]
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertNotIn("tools", request)
        self.assertEqual(turn.usage["input_tokens"], 10)
        check_design(self._agent([_final({"summary": "s", "objections": []})]), {"notes": "secret", "spec": {}})
        self.assertNotIn("secret", self.model.requests[0]["input"])
        edit_design(self._agent([_final({"summary": "s", "question": "", "edits": []})]),
                    {"request": "make it bigger", "outline": []})
        self.assertIn("The owner asks: make it bigger", self.model.requests[0]["input"])

    def test_unusable_output_is_an_error_not_an_edit(self):
        from wealthryse_agent.factsheet_design import build_step
        from wealthryse_agent.runtime import AgentError

        broken = _final({})
        broken.output_text = "not json"
        with self.assertRaises(AgentError):
            build_step(self._agent([broken]), {"round": 1})
        with self.assertRaises(AgentError):
            build_step(self._agent([]), {"round": 1, "big": "x" * 500_000})

    def test_the_service_endpoints_record_usage_and_refuse_oversized_facts(self):
        from wealthryse_agent.agent_server import Services, create_app
        from wealthryse_agent.config import RuntimeSettings, ServiceAuthSettings

        class Store:
            runs = []

            def tokens_used_today(self):
                return 0

            def start_run(self, job, correlation_id):
                self.runs.append([job, None])
                return len(self.runs)

            def finish_run(self, run_id, **kwargs):
                self.runs[run_id - 1][1] = kwargs

        store_ = Store()
        agent = self._agent([_final({"summary": "s", "question": "", "edits": []})])
        services = Services(auth=ServiceAuthSettings("", "", frozenset(), disabled_for_local_tests=True),
                            runtime=RuntimeSettings("", "", "", 100000, 90), tenant_id="t",
                            store_factory=lambda: store_, agent_factory=lambda: agent, tools_factory=None)
        services._store = store_
        client = TestClient(create_app(services))
        body = {"user_object_id": WebTests.OWNER["object_id"], "subject_id": "S1", "facts": {"request": "bigger"}}
        answer = client.post("/v1/factsheet-designs/edit", json=body)
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["answer"]["summary"], "s")
        self.assertEqual(store_.runs[0][0], "factsheet_editor")
        big = client.post("/v1/factsheet-designs/check", json={**body, "facts": {"x": "y" * 500_000}})
        self.assertEqual(big.status_code, 413)


LEARNING = ROOT / "private-inputs/learning-set"
US_TECH = ROOT / "private-inputs/U16088571/monthly factsheet template.pdf"


@unittest.skipUnless(US_TECH.exists() and LEARNING.exists(), "the private account templates are not on this machine")
class PrivateTemplateTests(unittest.TestCase):
    def test_the_reader_measures_the_account_design_exactly(self):
        found = reader.read(US_TECH, LEARNING / "U16088571_Factsheet_2026-09-30.pdf")
        tables = {t["binding"]: t for t in found["tables"]}
        holdings = tables["top_holdings"]
        self.assertEqual((round(holdings["x"]), round(holdings["top"], 2), holdings["header_height"]),
                         (27, 616.88, 10.4))
        self.assertEqual([round(c["width"]) for c in holdings["columns"]], [112, 34, 57, 113, 23])


if __name__ == "__main__":
    unittest.main()
