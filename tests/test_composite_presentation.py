"""A composite's deck in the Alpha World MS or T design, on synthetic data only.

The full render needs the artifact runtime and the private deck template, which
are kept out of git; these tests cover the model, slide 18, the name fitting,
the adaptation of the reference renderer and the output gate's composite branch.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
import zipfile

from reportlab.pdfbase.pdfmetrics import stringWidth

import audit_composite_presentation
from audit_composite_presentation import js_percent, js_text, monthly_table
import validate_subject_presentation as gate
from ibkr_etl import composite_presentation as cp
from ibkr_etl import factsheet_tree
from ibkr_etl import profiled_factsheet as pf
from ibkr_etl.presentation_design import design_composite, platform_content_expectations

import test_profiled_composite as tpc
import test_profiled_factsheet as tpf

ROOT = Path(__file__).resolve().parents[1]
NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
STRATEGY = "iStrategy: Alpha World Asia"
COVER_WIDTH, COVER_SIZE = 5191557, 2800


def _shape(shape_id, text, width=cp.HEADER_WIDTH, size=None):
    size = f' sz="{size}"' if size else ""
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="TextBox {shape_id}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width}" cy="300000"/></a:xfrm></p:spPr>'
            f'<p:txBody><a:bodyPr wrap="square"/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" b="1"{size}/>'
            f'<a:t>{escape(text)}</a:t></a:r></a:p></p:txBody></p:sp>')


def _picture(shape_id):
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="{shape_id}" name="Picture {shape_id}"/><p:cNvPicPr/><p:nvPr/>'
            f'</p:nvPicPr><p:blipFill/><p:spPr/></p:pic>')


def _slide(*shapes):
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld {NS}><p:cSld><p:spTree><p:nvGrpSpPr>'
            f'<p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>{"".join(shapes)}'
            f'</p:spTree></p:cSld></p:sld>').encode()


# The v24 template's fee page, by its shape ids and the text each starts with (synthetic wording).
FEE_PAGE = {"14": "iStrategy Fees", "8": "Terms and Conditions", "572": "Rebalancing and Updates", "573": "Notes",
            "588": "Performance fees calculated as set out in the terms.", "560": "", "569": "", "574": "",
            "576": "Rebalancing happens quarterly.", "577": "Market Updates follow each month.",
            "578": "Rebalancing Explanations are sent by email."}


def deck(path, name="Alpha World Asia", *, slides=20, fee_page=None, headers=None):
    """A synthetic exported deck: the cover name, the strategy headers of slides 3 to 17 and the fee page."""

    page = dict(FEE_PAGE if fee_page is None else fee_page)
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        for number in range(1, slides + 1):
            if number == 2:
                data = _slide(_shape(13, name, COVER_WIDTH, COVER_SIZE), _shape(14, "iStrategy: "))
            elif 3 <= number <= 17:
                data = _slide(_shape(7, (headers or {}).get(number, f"iStrategy: {name}")), _shape(9, "Results"))
            elif number == 18:
                data = _slide(*(_shape(i, text) for i, text in page.items()),
                              *(_picture(i) for i in ("601", "602", "603")))
            else:
                data = _slide(_shape(2, f"Slide {number}"))
            package.writestr(f"ppt/slides/slide{number}.xml", data)
    return path


def odd_cash(style="alpha-world-ms", cash=46.345, long=0.92655, short=-0.39, **changes):
    """The synthetic composite with other sleeve sizes, so its constant cash is odd or borrowed."""
    fields = ("component_id", "component_name", "component_type", "coefficient", "source_currency", "price_convention")
    sleeves = [{key: row[key] for key in fields} for row in tpc.definition()["components"]]
    sleeves[0]["coefficient"], sleeves[1]["coefficient"] = long, short
    return tpf.payload(style, constant_cash=cash, components=sleeves, **changes)


def _node():
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    if not node:
        raise unittest.SkipTest("Node is required for the report runtime test")
    return node


def _run_node(code, *values):
    run = subprocess.run([_node(), "--input-type=module", "-e", code % tuple(json.dumps(v) for v in values)],
                         capture_output=True, text=True, timeout=60)
    if run.returncode:
        raise AssertionError(run.stderr)
    return run.stdout


def _sizes(data, text):
    """The run sizes of the shapes whose whole text is the given text."""

    found = []
    for shape in cp.SHAPE.findall(data.decode()):
        if "".join(re.findall(r"<a:t>([^<]*)</a:t>", shape)) == escape(text):
            found.append(re.search(r'<a:rPr\b[^>]*?\bsz="(\d+)"', shape))
    return [int(match.group(1)) if match else None for match in found]


class ModelTests(unittest.TestCase):
    def test_each_design_gets_mss_model_under_the_composites_own_identity(self):
        for style in ("alpha-world-ms", "alpha-world-t"):
            with self.subTest(style=style):
                snapshot = tpf.payload(style)
                before = copy.deepcopy(snapshot)
                model = cp.build_model(snapshot)
                self.assertEqual(snapshot, before)
                identity = model["identity"]
                self.assertEqual((model["version"], identity["strategy_name"], identity["style"]),
                                 (cp.VERSION, STRATEGY, style))
                self.assertEqual(identity["inception_note"], "March 2025 is a partial month from inception on "
                                                             "3 March. Monthly returns use observed HKD NAV.")
                note = identity["speaker_note"]
                self.assertTrue(note.startswith("Alpha World Asia, 2025-04-30. HKD fixed-unit composite from 3 March "
                                                "2025."))
                for foreign in ("Alpha World MS", "Alpha World T ", "design"):
                    self.assertNotIn(foreign, note)
                self.assertEqual({k: model["risk"][k] for k in snapshot["risk"]["metrics"]},
                                 snapshot["risk"]["metrics"])
                self.assertAlmostEqual(model["month_rows"][-1]["portfolio_monthly_return"],
                                       float(snapshot["monthly_reporting"]["portfolio_monthly_return"]), places=12)
                self.assertTrue(all(model["primary"][name] is None for name in cp.UNAVAILABLE))
                self.assertEqual((model["benchmark_available"], model["crisis_available"], model["constant_cash"]),
                                 (False, False, 58.0))
                self.assertEqual(model["composition"], snapshot["report_profile"]["composition"])

    def test_odd_and_borrowed_constant_cash_are_reported_as_approved(self):
        for cash, long, short in ((46.345, 0.92655, -0.39), (-20.0, 1.6, -0.4)):
            with self.subTest(cash=cash):
                model = cp.build_model(odd_cash(cash=cash, long=long, short=short))
                self.assertEqual(model["constant_cash"], cash)

    def test_inception_on_the_first_starts_the_returns_with_no_partial_month(self):
        self.assertEqual(cp.inception_note("2025-03-01"),
                         "Returns start at inception on 1 March 2025. Monthly returns use observed HKD NAV.")
        self.assertEqual(cp.inception_note("2025-01-20"),
                         "January 2025 is a partial month from inception on 20 January. Monthly returns use observed "
                         "HKD NAV.")

    def test_a_snapshot_the_deck_cannot_state_truthfully_is_refused(self):
        snapshot = tpf.payload("alpha-world-ms")

        def changed(change):
            copied = copy.deepcopy(snapshot)
            change(copied)
            return copied

        cases = (
            (lambda s: s.pop("report_profile"), "composite report profile is required"),
            (lambda s: s.update(quality_status="fail"), "non-failed"),
            (lambda s: s["risk"].update(crisis_replay={"scenarios": []}), "qualified composite HKD crisis replay"),
            (lambda s: s["holdings"]["holdings"][0].update(market_value_text="1000"), "reconcile to NAV"),
            (lambda s: s["holdings"]["holdings"][0].update(description="Another sleeve"), "approved composition"),
            (lambda s: s["holdings"]["holdings"][2].update(market_value_text="57.0"), "reconcile to NAV"),
            (lambda s: s.update(performance_comparison={"metrics": {"beta": 1.0}}), "benchmark results"),
            (lambda s: s["month_end_performance"].pop(), "monthly NAV"),
            (lambda s: s["monthly_reporting"].update(residual_contribution=0.001), "do not reconcile"),
        )
        for change, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    cp.build_model(changed(change))

    def test_the_deck_expectations_follow_the_composites_own_model(self):
        snapshot = tpf.payload("alpha-world-t")
        self.assertTrue(design_composite(snapshot))
        self.assertFalse(design_composite({"subject_id": "alpha-world-ms"}))
        self.assertFalse(design_composite({"subject_id": "U0000001"}))
        expected = platform_content_expectations(snapshot)
        held = sorted(snapshot["holdings"]["holdings"], key=lambda r: abs(float(r["market_value_text"])), reverse=True)
        self.assertEqual(expected["security_axis_labels"]["Top 10 components"], [r["description"] for r in held])
        self.assertEqual(expected["summary_table_rows"][6]["Net asset value"],
                         f"HKD {float(snapshot['portfolio_summary']['latest_nav']['total_text']):.2f}")
        snapshot["month_end_performance"].pop()
        with self.assertRaisesRegex(ValueError, "monthly NAV"):
            platform_content_expectations(snapshot)


# Approval's longest description (600 characters), in long words.
LONGEST = ("Momentum sleeves " * 40)[:600].rsplit(" ", 1)[0]


class AboutSizeTests(unittest.TestCase):
    def test_any_description_approval_accepts_fits_the_deck_page(self):
        self.assertEqual(cp.about_size(tpf.LONG_TEXT), 12.0)
        pf.about_lines(LONGEST)  # the factsheet prints it too
        self.assertEqual(cp.about_size(LONGEST), 11.0)
        with self.assertRaisesRegex(ValueError, "does not fit its deck page"):
            cp.about_size("word " * 400)


class PageTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def compose(self, snapshot, source=None, name="out.pptx"):
        source = source or deck(self.root / "in.pptx", snapshot["report_profile"]["display_name"])
        return cp.compose(source, self.root / name, snapshot)

    def test_slide_18_becomes_the_composites_description_and_tree(self):
        snapshot = tpf.payload("alpha-world-ms")
        receipt = self.compose(snapshot)
        layout = factsheet_tree.layout(snapshot["report_profile"]["composition"])
        self.assertEqual((receipt["status"], receipt["named_shapes"], receipt["about_size"]), ("passed", 17, 12.0))
        self.assertEqual((receipt["tree_boxes"], receipt["tree_lines"], receipt["tree_texts"]),
                         (len(layout.boxes), len(layout.lines), len(layout.texts)))
        output = self.root / "out.pptx"
        self.assertEqual(receipt["output_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        with zipfile.ZipFile(self.root / "in.pptx") as before, zipfile.ZipFile(output) as after:
            # A name that already fits leaves every other slide exactly as rendered.
            for number in (*range(1, 18), 19, 20):
                path = f"ppt/slides/slide{number}.xml"
                self.assertEqual(before.read(path), after.read(path))
            page = after.read(cp.SLIDE)
        ids = set(re.findall(r'<p:cNvPr id="(\d+)"', page.decode()))
        self.assertFalse(ids & set(cp.REMOVED))
        self.assertLessEqual(set(cp.KEPT), ids)
        # As tall as the strategy header every other slide inherits.
        self.assertEqual(_sizes(page, STRATEGY), [cp.INHERITED_SIZE])
        self.assertEqual(_sizes(page, snapshot["report_profile"]["description"]), [1200])
        text, boxes, lines = cp.page_text(output)
        for value in (*cp.TITLES, *(t.value for t in layout.texts)):
            self.assertIn(value, text)
        self.assertNotIn("Performance fees", text)

    def test_a_long_name_and_description_shrink_to_fit(self):
        snapshot = tpf.payload("alpha-world-t", name=tpf.LONG_NAME, description=LONGEST)
        receipt = self.compose(snapshot)
        self.assertEqual(receipt["about_size"], 11.0)
        strategy = f"iStrategy: {tpf.LONG_NAME}"
        with zipfile.ZipFile(self.root / "out.pptx") as package:
            cover = _sizes(package.read("ppt/slides/slide2.xml"), tpf.LONG_NAME)
            headers = [size for n in range(3, 19) for size in _sizes(package.read(f"ppt/slides/slide{n}.xml"),
                                                                       strategy)]
        self.assertEqual(len(headers), 16)
        insets = 2 * cp.TEXT_INSETS[0]
        for (width, size), text in (((COVER_WIDTH, cover[0]), tpf.LONG_NAME),
                                    *(((cp.HEADER_WIDTH, size), strategy) for size in headers)):
            self.assertLessEqual(stringWidth(text, "Helvetica-Bold", size / 100), (width - insets) / cp.EMU)
        self.assertEqual(len(set(headers)), 1)  # slide 18's header shrinks with the others
        self.assertLess(max(headers), cp.INHERITED_SIZE)
        self.assertLess(cover[0], COVER_SIZE)

    def test_the_composites_own_words_may_use_a_fee_page_term(self):
        description = "Long China and Hong Kong, hedged with the Hang Seng Index. Rebalancing never happens."
        receipt = self.compose(tpf.payload("alpha-world-ms", description=description))
        self.assertEqual(receipt["status"], "passed")
        self.assertIn(description, cp.page_text(self.root / "out.pptx")[0])

    def test_borrowed_cash_is_drawn_on_the_page(self):
        snapshot = odd_cash(cash=-20.0, long=1.6, short=-0.4)
        self.assertEqual(self.compose(snapshot)["status"], "passed")
        self.assertIn("CASH -20%", cp.page_text(self.root / "out.pptx")[0])

    def test_a_deck_that_is_not_the_approved_fee_template_is_refused(self):
        snapshot = tpf.payload("alpha-world-ms")
        missing = {k: v for k, v in FEE_PAGE.items() if k != "576"}
        reworded = {**FEE_PAGE, "577": "Model basis"}
        for number, (arguments, message) in enumerate((
                ({"fee_page": missing}, "approved template's fee page"),
                ({"fee_page": reworded}, "approved template's fee page"),
                ({"slides": 19}, "approved 20 slides"),
                ({"headers": {5: "iStrategy: Alpha World MS"}}, "missing from a header"))):
            with self.subTest(message=message, case=number):
                source = deck(self.root / f"bad{number}.pptx", **arguments)
                with self.assertRaisesRegex(ValueError, message):
                    self.compose(snapshot, source, name=f"bad{number}.out.pptx")
                self.assertFalse((self.root / f"bad{number}.out.pptx").exists())
        source = deck(self.root / "in.pptx")
        with self.assertRaisesRegex(ValueError, "must be a new file"):
            self.compose(snapshot, source, name="in.pptx")
        self.assertTrue(zipfile.is_zipfile(source))

    def test_the_read_back_refuses_a_page_missing_its_tree_or_keeping_fee_terms(self):
        snapshot = tpf.payload("alpha-world-ms")
        self.compose(snapshot)
        model = cp.build_model(snapshot)
        self.assertEqual(cp.verify_page(self.root / "out.pptx", model)["status"], "passed")
        sleeve = escape(snapshot["holdings"]["holdings"][0]["description"])
        for number, (old, new, message) in enumerate((
                (f"<a:t>{sleeve}</a:t>", "<a:t></a:t>", "missing"),
                ("<a:t>Portfolio composition</a:t>", "<a:t>Portfolio composition Rebalancing</a:t>", "Rebalancing"),
                ('name="Composition line', 'name="Line', "incomplete"))):
            with self.subTest(message=message):
                changed = self.root / f"changed{number}.pptx"
                with zipfile.ZipFile(self.root / "out.pptx") as source, zipfile.ZipFile(changed, "w") as target:
                    for info in source.infolist():
                        data = source.read(info)
                        if info.filename == cp.SLIDE:
                            self.assertIn(old, data.decode())
                            data = data.decode().replace(old, new).encode()
                        target.writestr(info, data)
                with self.assertRaisesRegex(ValueError, message):
                    cp.verify_page(changed, model)


class AuditTests(unittest.TestCase):
    """The audit's expectations are the renderer's own formatting, checked against node."""

    def test_rounding_and_percentages_match_javascript(self):
        values = [12.345, -12.345, 12.125, -12.125, 2.675, 1.005, 0.0078125, 0.0025, -0.0025, -0.0004, 0.0, -0.0,
                  58.0, 46.345, 0.5434229258077691, -1e-9, 123.456789]
        expected = json.loads(_run_node(
            "console.log(JSON.stringify(%s.map(v => [v.toFixed(2), v.toFixed(6), (v * 100).toFixed(1), "
            "(v * 100).toFixed(2)])));", values))
        self.assertEqual([[js_text(v, 2), js_text(v, 6), js_percent(v, 1)[:-1], js_percent(v, 2)[:-1]]
                          for v in values], expected)

    def test_slide_15_shows_the_years_the_adapted_renderer_writes(self):
        model = cp.build_model(tpf.payload("alpha-world-t"))

        def rows(*pairs):
            return [{"date": day, "portfolio_monthly_return": value} for day, value in pairs]

        cases = [
            ({"identity": model["identity"], "month_rows": model["month_rows"]}, "2025-04-30"),
            ({"identity": {"inception": "2026-11-16"},
              "month_rows": rows(("2026-11-30", 0.0025), ("2026-12-31", -0.0025), ("2027-01-29", 0.012345))},
             "2027-01-31"),
            ({"identity": {"inception": "2025-01-20"},
              "month_rows": rows(("2025-01-31", 0.01), ("2026-12-31", 0.0), ("2027-03-31", -0.0004))},
             "2027-03-31"),
        ]
        rendered = json.loads(_run_node("""import fs from 'node:fs';
import {adaptReferenceRenderer} from %s;
const source = fs.readFileSync(%s, 'utf8');
const code = adaptReferenceRenderer(source, 'file:///runtime/artifact_tool.mjs');
const percent = source.split('\\n').find(line => line.startsWith('const percent = '));
const slide15 = text => {
  const start = text.indexOf('const monthlyByYear = new Map();');
  const end = text.indexOf('\\n]);', text.indexOf('setTable("tb/a9srud0n"', start)) + 4;
  return new Function('msModel', 'periodEnd', 'portfolioMonthRows', 'setTable', percent + '\\n' + text.slice(start, end));
};
// The composite adapter and the MS, T and account decks share one year rule.
console.log(JSON.stringify([code, source].map(slide15).map(block => %s.map(([model, periodEnd]) => {
  let written; block(model, periodEnd, model.month_rows, (id, rows) => { written = rows; }); return written;
}))));""", (ROOT / "scripts/composite_presentation_runtime.mjs").as_uri(),
            str(ROOT / "scripts/render_monthly_presentation.mjs"), cases))
        adapted, reference = rendered
        self.assertEqual(adapted, reference)
        rendered = adapted
        for (case, period_end), table in zip(cases, rendered):
            with self.subTest(period_end=period_end):
                self.assertEqual(table[1:], monthly_table(case, period_end))
        self.assertEqual([table[1][0] for table in rendered], ["2025", "2026", "2026"])
        self.assertEqual(rendered[1][2][1], "1.2%")

    def test_axis_limits_are_read_from_the_value_axis(self):
        chart = ET.fromstring(
            f'<c:chartSpace xmlns:c="{audit_composite_presentation.C}"><c:catAx><c:scaling><c:max val="9"/>'
            '</c:scaling></c:catAx><c:valAx><c:scaling><c:min val="-20"/><c:max val="1"/></c:scaling></c:valAx>'
            '</c:chartSpace>')
        self.assertEqual((audit_composite_presentation._axis_limit(chart, "min"),
                          audit_composite_presentation._axis_limit(chart, "max")), (-20.0, 1.0))
        chart.find(f".//{{{audit_composite_presentation.C}}}valAx/{{{audit_composite_presentation.C}}}scaling").clear()
        self.assertIsNone(audit_composite_presentation._axis_limit(chart, "max"))


class GateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.output, self.snapshot = root / "deck.pptx", root / "snapshot.json"
        self.output.write_bytes(b"synthetic deck")
        self.snapshot.write_text(json.dumps(tpf.payload("alpha-world-t")), encoding="utf-8")
        self.sha = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        output_sha = hashlib.sha256(self.output.read_bytes()).hexdigest()
        workbooks = patch.object(gate, "verify_ms_chart_workbooks", return_value={
            "status": "passed", "embedded_workbooks_verified": 6, "owner_slides": [6, 7, 8, 9, 10, 11],
            "output_sha256": output_sha})
        workbooks.start()
        self.addCleanup(workbooks.stop)

    def run_gate(self, financial, strategy=STRATEGY):
        with patch.object(audit_composite_presentation, "audit", return_value=financial) as audit, \
                patch.object(gate, "audit", side_effect=AssertionError("MS's own audit")):
            result = gate.validate_subject_presentation(self.output, self.snapshot, subject_id="alpha-world-asia",
                                                        period_end="2025-04-30", expected_strategy=strategy)
        audit.assert_called_once_with(self.output, self.snapshot)
        return result

    def financial(self, **changes):
        return {"status": "passed", "financial_charts_verified": 6, "risk_metrics_verified": 7,
                "snapshot_sha256": self.sha, "generic_validation": {"status": "passed", "slides": 20}, **changes}

    def test_a_composite_deck_passes_the_composite_audit_under_its_own_contract(self):
        result = self.run_gate(self.financial())
        self.assertEqual(result["content_contract_version"], cp.CONTRACT_VERSION)
        self.assertEqual(result["content_requirements"], list(cp.CONTENT_REQUIREMENTS))
        self.assertEqual((result["snapshot_sha256"], result["composite_financial_audit"]["risk_metrics_verified"]),
                         (self.sha, 7))

    def test_another_name_or_an_incomplete_audit_is_refused(self):
        for financial, strategy, message in (
                (self.financial(), "iStrategy: Alpha World MS", "its own report profile"),
                (self.financial(snapshot_sha256="0" * 64), STRATEGY, "bound to another snapshot"),
                (self.financial(financial_charts_verified=5), STRATEGY, "incomplete")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    with patch.object(audit_composite_presentation, "audit", return_value=financial):
                        gate.validate_subject_presentation(self.output, self.snapshot, subject_id="alpha-world-asia",
                                                           period_end="2025-04-30", expected_strategy=strategy)


class RuntimeTests(unittest.TestCase):
    def test_the_reference_renderer_is_adapted_in_memory_for_the_composite(self):
        node = os.environ.get("NODE_BINARY") or shutil.which("node")
        self.assertTrue(node, "Node is required for the report runtime test")
        runtime = ROOT / "scripts/composite_presentation_runtime.mjs"
        reference = ROOT / "scripts/render_monthly_presentation.mjs"
        with tempfile.TemporaryDirectory() as folder:
            adapted = Path(folder) / "adapted.mjs"
            code = """import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {REFERENCE_SHA256,adaptReferenceRenderer,compositeStrategyName} from %s;
const source=fs.readFileSync(%s,'utf8');
assert.equal(createHash('sha256').update(source).digest('hex'),REFERENCE_SHA256);
const code=adaptReferenceRenderer(source,'file:///runtime/artifact_tool.mjs');
for (const gone of ['Frongello',"'Alpha World MS'",'alpha_world_ms_presentation','audit_alpha_ms_presentation',
                    "snapshot.subject_id === 'alpha-world-ms'",'from "@oai/artifact-tool"'])
  assert.ok(!code.includes(gone),gone);
for (const gone of ['max: 0.7,','["2025", ...(monthlyByYear']) assert.ok(!code.includes(gone),gone);
for (const kept of ['firstMonthlyYear + 1','Math.floor(msModel.constant_cash / 10) * 10',
                    'Math.ceil(Math.max(...sector.allocation.map(row => Number(row.weight))) * 10) / 10'])
  assert.ok(code.includes(kept),kept);
for (const kept of ['ibkr_etl.composite_presentation','scripts/audit_composite_presentation.py',
                    'msModel.identity.speaker_note','msModel.identity.inception_note',
                    'Fixed-unit contribution to return','from "file:///runtime/artifact_tool.mjs"'])
  assert.ok(code.includes(kept),kept);
assert.throws(()=>adaptReferenceRenderer(source.replace('Equity contribution','Equity'),'file:///x'),
              /Reference renderer text changed/);
fs.writeFileSync(%s,code);
const snapshot={subject_id:'alpha-world-asia',
  report_profile:{subject_id:'alpha-world-asia',style:'alpha-world-t',display_name:' Alpha World Asia '}};
assert.equal(compositeStrategyName(snapshot),'iStrategy: Alpha World Asia');
const profile=snapshot.report_profile;
for (const refused of [
  {subject_id:'alpha-world-ms',report_profile:{...profile,subject_id:'alpha-world-ms'}},
  {...snapshot,report_profile:{...profile,subject_id:'another-composite'}},
  {...snapshot,report_profile:{...profile,style:'standard'}},
  {...snapshot,report_profile:{...profile,display_name:' '}},
  {subject_id:'alpha-world-asia'}])
  assert.throws(()=>compositeStrategyName(refused),/report profile/);
""" % tuple(json.dumps(value) for value in (runtime.as_uri(), str(reference), str(adapted)))
            run = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True,
                                 timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            for script in (adapted, ROOT / "scripts/render_composite_presentation.mjs"):
                checked = subprocess.run([node, "--check", str(script)], capture_output=True, text=True, timeout=60)
                self.assertEqual(checked.returncode, 0, checked.stderr)


if __name__ == "__main__":
    unittest.main()
