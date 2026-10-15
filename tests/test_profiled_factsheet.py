"""Composite factsheets in the Alpha World MS and T designs, on synthetic data only.

Rendering needs the private MS and T design sources, which are kept out of git;
those tests skip where the sources are absent.
"""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader

from ibkr_etl import factsheet_tree
from ibkr_etl import profiled_factsheet as pf
from ibkr_etl import profiled_reporting as pr
from ibkr_etl.monthly_artifacts import MonthlyArtifactError, render_factsheet
from ibkr_etl.report_profiles import ReportProfileError, build_profile, profile_from_metadata

import test_profiled_composite as tpc
import test_profiled_reporting as tpr

ROOT = Path(__file__).resolve().parents[1]
T_SOURCE = ROOT / "templates/alpha-world-t/owner_template_20261008.pdf"
MS_SOURCE = ROOT / "templates/alpha-world-ms/completed_example.pdf"
MS_TEMPLATE = ROOT / "templates/alpha-world-ms/template.pdf"
STYLE = ROOT / "templates/alpha-world-t/ms_style_sections_v6.pdf"
SOURCES = all(path.exists() for path in (T_SOURCE, MS_SOURCE, MS_TEMPLATE, STYLE))
LONG_NAME = "Global Quant Basket & Hedge 2025"
LONG_TEXT = ("A fixed-unit composite that holds 91% of NAV in the China & Hong Kong account and shorts 49% of NAV in "
             "the Hang Seng Index price index, with the rest in constant cash (58%). Units never rebalance: each "
             "sleeve's weight drifts with its own return. Risk-free rate: average 1-month HIBOR. Reported in HKD "
             "from inception (3 March 2025); nothing here is a forecast or an offer.")


def payload(style, *, name=None, description=tpc.DESCRIPTION, **changes):
    """A frozen month-end snapshot of the synthetic composite, as the reporting snapshot assembles it."""
    if name:
        changes["display_name"] = name
    plan = (lambda: tpc.definition(**changes)) if changes else tpc.definition
    record = build_profile(definition=plan(), style=style, description=description)
    with patch.object(tpr, "definition", plan), \
            patch.object(tpr, "profile", lambda s: profile_from_metadata(tpc.SUBJECT, {"report_profile": record})):
        core, components = tpr.history(style)
    result, *_ = tpr.assemble(pr.tool_results(pr.enrich(core, components, plan())))
    result["synthetic_data"] = True
    return result


def sources(style):
    if style == "alpha-world-ms":
        return {"template_path": MS_TEMPLATE, "design_model_path": MS_SOURCE}
    return {"template_path": T_SOURCE}


class AboutTextTests(unittest.TestCase):
    def test_description_fills_the_ms_about_column(self):
        lines = pf.about_lines(LONG_TEXT)
        self.assertEqual(" ".join(" ".join(line) for line in lines), " ".join(LONG_TEXT.split()))
        self.assertLessEqual(len(lines), pf.ABOUT_LINES)
        from reportlab.pdfbase.pdfmetrics import stringWidth
        for line in lines:
            self.assertLessEqual(stringWidth(" ".join(line), "Helvetica", pf.ABOUT_SIZE), pf.ABOUT_RIGHT - pf.ABOUT_X)

    def test_text_the_column_cannot_hold_is_refused(self):
        with self.assertRaisesRegex(ValueError, "does not fit"):
            pf.about_lines("word " * 400)
        with self.assertRaisesRegex(ValueError, "too long"):
            pf.about_lines("x" * 120)


class ApprovalTests(unittest.TestCase):
    def test_names_and_descriptions_the_reports_cannot_print_are_refused_at_approval(self):
        for name, description, message in (("Alpha Wörld", tpc.DESCRIPTION, "plain letters"),
                                           ("A" * 49, tpc.DESCRIPTION, "48 characters"),
                                           ("Alpha World Asia", "Long China → short HSI", "cannot print")):
            with self.subTest(name=name, description=description):
                with self.assertRaisesRegex(ReportProfileError, message):
                    build_profile(definition=tpc.definition(display_name=name), style="alpha-world-t",
                                  description=description)

    def test_typographic_punctuation_in_a_description_is_accepted(self):
        record = build_profile(definition=tpc.definition(), style="alpha-world-ms",
                               description="Long China – hedged with the “Hang Seng”.")
        self.assertIn("“Hang Seng”", record["description"])


@unittest.skipUnless(SOURCES, "private MS and T design sources are not in this checkout")
class FontTests(unittest.TestCase):
    def setUp(self):
        from ibkr_etl.alpha_world_t_sections import load_style
        self.fonts = pf._fonts(load_style())

    def test_the_design_bold_is_used_when_it_can_spell_the_name(self):
        run = pf.title_run("Alpha World Asia", self.fonts, "alpha-world-t")
        self.assertFalse(run["synthetic_bold"])
        self.assertEqual(run["size"], pf.DESIGNS["alpha-world-t"]["title"][2])
        run = pf.title_run(LONG_NAME, self.fonts, "alpha-world-ms")
        self.assertTrue(run["synthetic_bold"])

    def test_long_names_shrink_to_fit_and_never_overflow(self):
        name = "Wide World Momentum Basket With Many Words In It"
        title = pf.title_run(name, self.fonts, "alpha-world-ms")
        self.assertLess(title["size"], pf.DESIGNS["alpha-world-ms"]["title"][2])
        self.assertLessEqual(title["x"] + title["width"], pf.TITLE_RIGHT + 1e-9)
        kpi = pf.kpi_title_run(name, self.fonts)
        self.assertLessEqual(pf.KPI_TITLE[0] + self.fonts["kpi"].width(name, kpi["size"]), pf.KPI_TITLE_RIGHT)
        with self.assertRaisesRegex(ValueError, "do not carry"):
            pf.title_run("Alpha Wörld", self.fonts, "alpha-world-t")


@unittest.skipUnless(SOURCES, "private MS and T design sources are not in this checkout")
class RenderTests(unittest.TestCase):
    def test_each_design_renders_the_composites_identity_numbers_and_tree(self):
        authorities = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (T_SOURCE, MS_SOURCE)}
        for style in ("alpha-world-ms", "alpha-world-t"):
            for name, description in ((None, tpc.DESCRIPTION), (LONG_NAME, LONG_TEXT)):
                with self.subTest(style=style, name=name), tempfile.TemporaryDirectory() as root:
                    snapshot = payload(style, name=name, description=description)
                    before = copy.deepcopy(snapshot)
                    result = render_factsheet(snapshot, output_path=Path(root) / "composite.pdf", **sources(style))
                    self.assertEqual(snapshot, before)
                    self.assertEqual(result["layout_version"], pf.LAYOUT_VERSION)
                    self.assertEqual(result["layout_family"], f"{style}-composite")
                    self.assertEqual(result["pdf_accuracy_validation"]["status"], "pass")
                    self.assertEqual(result["title_font"], "synthetic-bold-georgia" if name else "georgia-bold")
                    self.assertFalse(result["publication_allowed"])
                    self.assertEqual(result["release_scope"], "internal_shareholder")
                    reader = PdfReader(result["output_path"])
                    first, second = (" ".join(page.extract_text().split()) for page in reader.pages)
                    display = name or "Alpha World Asia"
                    self.assertEqual(reader.metadata["/Title"], f"{display} Factsheet - 2025-04-30")
                    self.assertGreaterEqual(first.count(display), 2)
                    self.assertIn(" ".join(description.split()), first)
                    self.assertIn("March 2025", first)
                    self.assertIn("Multi-Tranche (including Taiwan)" if style == "alpha-world-t" else
                                  "Multi-Tranche Quantitative strategy", first)
                    if style == "alpha-world-ms":
                        self.assertNotIn("Taiwan", first)
                    metrics = result["chart_calculation"]["metrics"]
                    self.assertIn(f"{metrics['annualized_return']:+.2%}", first)
                    for text in factsheet_tree.layout(snapshot["report_profile"]["composition"]).texts:
                        self.assertIn(text.value, second)
                    for foreign in ("Alpha World Enhanced Index", "Alpha World T Enhanced Index", "Greater China",
                                    "S&P500", "130% LONG", "dynamic allocation framework"):
                        self.assertNotIn(foreign, first + second)
                    self.assertIn("Sprout Asset", second)
        for path, digest in authorities.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_wrong_source_failed_snapshot_and_existing_output_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "composite.pdf"
            cases = ((payload("alpha-world-t"), {"template_path": MS_SOURCE}, "approved design authority"),
                     (payload("alpha-world-ms"), {"template_path": MS_TEMPLATE}, "completed factsheet"),
                     (payload("alpha-world-ms"), {"template_path": MS_TEMPLATE, "design_model_path": T_SOURCE},
                      "approved design authority"),
                     ({**payload("alpha-world-t"), "quality_status": "fail"}, sources("alpha-world-t"),
                      "failed composite snapshot"))
            for snapshot, paths, message in cases:
                with self.subTest(message=message):
                    with self.assertRaisesRegex(MonthlyArtifactError, message):
                        render_factsheet(snapshot, output_path=target, **paths)
                    self.assertFalse(target.exists())
            target.write_bytes(b"existing")
            with self.assertRaisesRegex(MonthlyArtifactError, "new file"):
                render_factsheet(payload("alpha-world-t"), output_path=target, **sources("alpha-world-t"))
            self.assertEqual(target.read_bytes(), b"existing")

    def test_a_changed_profile_or_metric_is_refused(self):
        mutations = (lambda s: s["report_profile"].update(display_name="Someone else"),
                     lambda s: s["methodology_identity"].update(contract_sha256="0" * 64),
                     lambda s: s["risk"]["risk_free"]["observations"].pop(),
                     lambda s: s.update(period_start="2025-03-04"))
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as root:
                snapshot = payload("alpha-world-t")
                mutate(snapshot)
                with self.assertRaises(MonthlyArtifactError):
                    render_factsheet(snapshot, output_path=Path(root) / "composite.pdf", **sources("alpha-world-t"))


if __name__ == "__main__":
    unittest.main()
