"""The spec-driven factsheet renderer: built-in specs paint what the hand-written renderers paint."""
from __future__ import annotations

import copy
import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream
from reportlab.pdfgen import canvas

from ibkr_etl import monthly_artifacts as legacy
from ibkr_etl.factsheet_spec import bindings, checks, engine, paint, stress
from ibkr_etl.factsheet_spec.spec import SpecError, built_in_designs, load_design, spec_sha256, validate_spec
from test_monthly_artifacts import _ms_snapshot, _snapshot, _write_alpha_world_source

ROOT = Path(__file__).resolve().parents[1]
T_SOURCE = ROOT / "templates/alpha-world-t/owner_template_20261008.pdf"
T_STYLE = ROOT / "templates/alpha-world-t/ms_style_sections_v6.pdf"


def _blank_template(path: Path, size=(595.5, 842.25), text: str | None = None) -> Path:
    if text is None:
        writer = PdfWriter()
        writer.add_blank_page(width=size[0], height=size[1])
        writer.add_blank_page(width=size[0], height=size[1])
        with path.open("wb") as stream:
            writer.write(stream)
        return path
    document = canvas.Canvas(str(path), pagesize=size)
    document.setFont("Helvetica", 8)
    document.drawString(300, 60, text)
    document.showPage()
    document.showPage()
    document.save()
    return path


def _same_paint(test: unittest.TestCase, left: Path, right: Path) -> None:
    a, b = PdfReader(left), PdfReader(right)
    test.assertEqual(len(a.pages), len(b.pages))
    for index, (pa, pb) in enumerate(zip(a.pages, b.pages)):
        difference = paint.first_difference(paint.paint_ops(pa), paint.paint_ops(pb))
        test.assertIsNone(difference, f"page {index + 1} first differs at {difference}")


def _with_overflow(spec: dict, overflow: str) -> dict:
    spec = copy.deepcopy(spec)
    for page in spec["pages"]:
        for layer in page["layers"]:
            for op in layer.get("ops", []):
                if op["op"] == "table":
                    op["overflow"] = overflow
    return spec


class SpecValidationTests(unittest.TestCase):
    def test_every_built_in_design_is_valid_and_hash_stable(self):
        self.assertEqual(built_in_designs(), ["account-owner-template", "alpha-world-ms", "alpha-world-t"])
        for design in built_in_designs():
            spec = load_design(design)
            self.assertEqual(spec_sha256(spec), spec_sha256(copy.deepcopy(spec)))

    def test_rejects_anything_outside_the_vocabulary(self):
        base = load_design("account-owner-template")
        cases = {
            "unknown value": lambda s: s["pages"][0]["layers"][0]["ops"][0].update(value="{bank_balance}"),
            "unknown binding": lambda s: s["pages"][0]["layers"][0]["ops"][0].update(value={"bind": "made_up"}),
            "unknown operation": lambda s: s["pages"][0]["layers"][0]["ops"].append({"id": "x", "op": "script"}),
            "unknown component": lambda s: s["pages"][1]["layers"][0]["ops"][2].update(name="exec"),
            "unknown font": lambda s: s["pages"][0]["layers"][0]["ops"][0].update(font="Comic Sans"),
            "duplicate id": lambda s: s["pages"][0]["layers"][0]["ops"][1].update(id="p1.date"),
            "unknown column": lambda s: s["pages"][0]["layers"][0]["ops"][1]["columns"][0].update(
                value={"field": "salary"}),
            "unknown format": lambda s: s["pages"][0]["layers"][0]["ops"][0].update(value="{period_end|eval}"),
            "unknown field": lambda s: s.update(code="print(1)"),
        }
        for name, mutate in cases.items():
            with self.subTest(name), self.assertRaises(SpecError):
                spec = copy.deepcopy(base)
                mutate(spec)
                validate_spec(spec)

    def test_every_figure_binding_names_a_metrics_guide_definition(self):
        from ibkr_etl import metrics_guide
        for binding in bindings.CATALOGUE.values():
            if binding.unit in ("ratio", "number", "money"):
                self.assertIsNotNone(binding.guide, binding.name)
                metrics_guide.definition(binding.guide)


class AccountParityTests(unittest.TestCase):
    """With legacy truncation switched on, the account spec paints exactly what the old renderer paints."""

    def test_fixture_and_stress_cases_paint_identically(self):
        spec = _with_overflow(load_design("account-owner-template"), "clip")
        cases = [("fixture", _snapshot())] + [
            (case.case_id, case.build("USD")) for case in stress.CASES
            if case.case_id in ("december_inception", "five_years", "fivefold", "long_names", "first_month")]
        with tempfile.TemporaryDirectory() as root:
            template = _blank_template(Path(root, "template.pdf"))
            for name, snapshot in cases:
                with self.subTest(name):
                    snapshot["subject_id"] = "U16088606" if name != "fixture" else snapshot["subject_id"]
                    old, new = Path(root, f"{name}-old.pdf"), Path(root, f"{name}-new.pdf")
                    legacy.render_factsheet(copy.deepcopy(snapshot), template_path=template, output_path=old)
                    engine.render(spec, snapshot, template_path=template, output_path=new, run_checks=False)
                    _same_paint(self, old, new)

    def test_the_default_spec_shrinks_long_names_instead_of_cutting_them(self):
        spec = load_design("account-owner-template")
        snapshot = stress.snapshot(subject_id="U16088606", names=stress.LONG_NAMES)
        with tempfile.TemporaryDirectory() as root:
            template = _blank_template(Path(root, "template.pdf"))
            result = engine.render(spec, snapshot, template_path=template, output_path=Path(root, "out.pdf"))
            printed = [run.text for page in PdfReader(Path(root, "out.pdf")).pages for run in paint.text_runs(page)]
        self.assertNotEqual(result["checks"]["status"], "fail")
        self.assertFalse([text for text in printed if text.endswith("...") and text != "..."])
        self.assertTrue(any(text.startswith("Consolidated Semiconductor") for text in printed))

    def test_every_stress_case_renders_and_passes(self):
        spec = load_design("account-owner-template")
        with tempfile.TemporaryDirectory() as root:
            template = _blank_template(Path(root, "template.pdf"))
            for outcome in stress.run(spec, template_path=template):
                with self.subTest(outcome["case"]):
                    self.assertNotEqual(outcome["status"], "fail", outcome["detail"])


class GeneratedCheckTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.template = _blank_template(Path(self.root.name, "template.pdf"))
        self.spec = load_design("account-owner-template")

    def render(self, spec, snapshot=None, template=None):
        return engine.render(spec, snapshot or _snapshot(), template_path=template or self.template,
                             output_path=Path(self.root.name, "out.pdf"))

    def test_a_clean_render_passes_and_warns_that_no_currency_is_stated(self):
        result = self.render(self.spec)
        by_id = {c["id"]: c["status"] for c in result["checks"]["checks"]}
        self.assertEqual(by_id["printed"], "pass")
        self.assertEqual(by_id["figures"], "pass")
        self.assertEqual(by_id["currency"], "warn")

    def test_truncation_fails_and_leaves_no_file(self):
        spec = _with_overflow(self.spec, "clip")
        with self.assertRaisesRegex(engine.RenderError, "cut short"):
            self.render(spec, stress.snapshot(subject_id="U16088606", names=stress.LONG_NAMES))
        self.assertFalse(Path(self.root.name, "out.pdf").exists())

    def test_a_stale_date_left_in_the_template_fails(self):
        stale = _blank_template(Path(self.root.name, "stale.pdf"), text="Data as of 31 March 2027")
        with self.assertRaisesRegex(engine.RenderError, "after the report"):
            self.render(self.spec, template=stale)

    def test_removed_template_text_is_gone_not_covered(self):
        stale = _blank_template(Path(self.root.name, "stale.pdf"), text="Data as of 31 March 2027")
        spec = copy.deepcopy(self.spec)
        spec["pages"][0]["layers"].insert(0, {"kind": "remove", "boxes": [[290, 50, 200, 20]]})
        result = self.render(spec, template=stale)
        self.assertEqual(result["variable_content_removed"][0]["text"], 1)
        printed = [run.text for run in paint.text_runs(PdfReader(Path(self.root.name, "out.pdf")).pages[0])]
        self.assertNotIn("Data as of 31 March 2027", printed)

    def test_overlapping_values_fail(self):
        spec = copy.deepcopy(self.spec)
        ops = spec["pages"][0]["layers"][0]["ops"]
        ops.append({**ops[-1], "id": "p1.copy", "x": ops[-1]["x"] + 3})
        with self.assertRaisesRegex(engine.RenderError, "overlap"):
            self.render(spec)

    def test_a_printed_figure_that_disagrees_with_the_snapshot_fails(self):
        ctx = bindings.build_context(_snapshot())
        drawn = engine.Drawn(texts=[{"id": "p1.trailing_return", "page": 0, "text": "99.99 %", "font": "Times-Bold",
                                     "size": 10.0, "x": 0.0, "y": 0.0,
                                     "value": {"bind": "trailing_twelve_month_return",
                                               "format": {"kind": "percent", "digits": 2, "suffix": " %"}}}])
        out = Path(self.root.name, "x.pdf")
        engine.render(self.spec, _snapshot(), template_path=self.template, output_path=out, run_checks=False)
        report = checks.run(out, self.spec, ctx, drawn)
        self.assertEqual({c["id"]: c["status"] for c in report["checks"]}["figures"], "fail")

    def test_remove_covers_every_text_operator(self):
        buffer = io.BytesIO()
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        content = ContentStream(None, writer)
        from pypdf.generic import NumberObject, TextStringObject
        content.operations = [([], b"BT"), ([NumberObject(12)], b"TL"), ([NumberObject(10), NumberObject(100)], b"Td"),
                              ([TextStringObject("kept")], b"Tj"), ([TextStringObject("gone")], b"'"), ([], b"ET")]
        page.replace_contents(content)
        counts = engine.remove_content(page, [[0, 80, 200, 15]], images=False)
        self.assertEqual(counts["text"], 1)
        writer.write(buffer)


class BindingTests(unittest.TestCase):
    def test_returns_and_formats(self):
        ctx = bindings.build_context(_snapshot())
        self.assertAlmostEqual(ctx.scalars["since_inception_return"], 0.10, places=6)
        self.assertEqual(bindings.format_value(0.12345, {"kind": "percent", "digits": 2, "signed": True}), "+12.35%")
        self.assertEqual(bindings.format_value(None, {"kind": "percent"}), "N/A")
        self.assertEqual(bindings.render_text("{period_end|date_long}", ctx), "31 January 2026")
        self.assertEqual(bindings.resolve({"choose": [{"when": "is_composite", "value": "A"}, {"value": "B"}]}, ctx), "B")

    def test_the_snapshot_is_never_changed(self):
        snapshot = _snapshot()
        before = copy.deepcopy(snapshot)
        bindings.build_context(snapshot)
        self.assertEqual(snapshot, before)


class AlphaWorldMSParityTests(unittest.TestCase):
    def test_ms_spec_paints_what_the_ms_renderer_paints(self):
        snapshot = _ms_snapshot()
        with tempfile.TemporaryDirectory() as root:
            model = Path(root, "model.pdf")
            _write_alpha_world_source(model)
            sha = hashlib.sha256(model.read_bytes()).hexdigest()
            spec = copy.deepcopy(load_design("alpha-world-ms"))
            spec["example_sha256"] = sha
            spec["metadata"]["/LayoutAuthoritySHA256"] = sha
            old, new = Path(root, "old.pdf"), Path(root, "new.pdf")
            with patch.object(legacy, "ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256", sha):
                legacy.render_factsheet(copy.deepcopy(snapshot), template_path=model, design_model_path=model,
                                        output_path=old)
                result = engine.render(spec, snapshot, template_path=model, example_path=model, output_path=new)
            _same_paint(self, old, new)
        self.assertNotEqual(result["checks"]["status"], "fail")
        self.assertEqual(result["pdf_accuracy_validation"]["status"], "pass")


@unittest.skipUnless(T_SOURCE.exists() and T_STYLE.exists(), "private T template is not in this checkout")
class AlphaWorldTParityTests(unittest.TestCase):
    def test_t_spec_paints_what_the_t_renderer_paints(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from qualify_alpha_world_t_factsheet import synthetic_snapshot
        from ibkr_etl.alpha_world_t_factsheet import render as t_render
        spec = load_design("alpha-world-t")
        with tempfile.TemporaryDirectory() as root:
            for period_end in ("2025-12-31", "2026-09-30"):
                with self.subTest(period_end):
                    snapshot = synthetic_snapshot(period_end)
                    old, new = Path(root, f"{period_end}-old.pdf"), Path(root, f"{period_end}-new.pdf")
                    t_render(copy.deepcopy(snapshot), template_path=T_SOURCE, output_path=old)
                    result = engine.render(spec, snapshot, template_path=T_SOURCE, output_path=new)
                    _same_paint(self, old, new)
                    self.assertEqual(result["pdf_accuracy_validation"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
