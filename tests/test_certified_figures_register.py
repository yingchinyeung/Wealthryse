"""The draft certified-figures register must agree with the Metrics guide (a design proof for U5).

infra/medallion/certified-figures.json lists the figures to certify first, page by page,
and the three batches the pages move in before the November month-end
(docs/MEDALLION_CERTIFIED_FIGURES.md). Nothing reads it at run time and nothing is switched
on by it. These checks keep the draft honest while it is reviewed:

- every figure names a Metrics guide definition, and the guide places that definition on
  the figure's page (or the figure records the gap);
- every definition in the guide sections the first pages cover is certified or deferred
  with a reason, and every other section is named with a reason;
- every factsheet binding that names a guide definition is covered, with the same unit;
- every source is a gold or silver table in the catalog with a contract;
- the payload fields named for the existing gold sources are produced by today's code;
- every website page is moving in a batch, waits until after month-end, or is out of
  scope, and the batch dates keep clear of the no-move day and the month-end freeze.
"""
from __future__ import annotations

import ast
import json
import re
import unittest
from datetime import date
from pathlib import Path

from ibkr_etl import metrics_guide as guide
from ibkr_etl.factsheet_spec.bindings import CATALOGUE as BINDINGS

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "infra" / "medallion" / "certified-figures.json"
CATALOG = ROOT / "infra" / "medallion" / "catalog.json"
CONTRACTS = ROOT / "infra" / "medallion" / "contracts"
WEB = ROOT / "src" / "ibkr_etl" / "web.py"
FIGURE_ID = re.compile(r"[a-z]+\.[a-z0-9_]+")
LAYERS = ("silver", "gold")


def _register() -> dict:
    return json.loads(REGISTER.read_text(encoding="utf-8"))


def _catalog_targets() -> dict[str, str]:
    """'gold.risk_analytics_snapshots' -> layer, for every silver and gold table or view."""
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    targets = {}
    for key, entry in catalog["postgres"].items():
        if entry["layer"] in LAYERS:
            targets[f"{entry['target_schema']}.{key.split('.', 1)[1]}"] = entry["layer"]
    return targets


def _web_constants() -> dict[str, object]:
    """The website's page lists, read from web.py without importing it."""
    wanted = {"ACCOUNT_SECTIONS", "ACCOUNT_SUBPAGES", "PLATFORM_SECTIONS"}
    found = {}
    for node in ast.parse(WEB.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) in wanted:
                    found[target.id] = ast.literal_eval(node.value)
    return found


class CertifiedFiguresRegisterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.register = _register()
        cls.figures = cls.register["figures"]
        cls.pages = cls.register["pages"]

    def test_draft_and_shape(self) -> None:
        self.assertEqual(self.register["status"], "draft")
        units, kinds = set(self.register["units"]), set(self.register["kinds"])
        for figure_id, figure in self.figures.items():
            with self.subTest(figure=figure_id):
                self.assertRegex(figure_id, FIGURE_ID)
                self.assertIn(figure["unit"], units)
                self.assertIn(figure["kind"], kinds)
                self.assertIn(figure["page"], self.pages)
                self.assertEqual(bool(figure.get("periods")), figure["kind"] == "per_period")
                self.assertEqual(bool(figure.get("item")), figure["kind"] == "per_item")
                if "currency" in figure:
                    self.assertEqual((figure["unit"], figure["currency"]), ("money", "own"))
        orders = sorted(page["order"] for page in self.pages.values())
        self.assertEqual(orders, list(range(1, len(self.pages) + 1)))

    def test_every_figure_names_a_guide_definition_and_method(self) -> None:
        methods = {version for version, _ in guide.METHODOLOGY_VERSIONS} | set(self.register["methods"])
        for figure_id, figure in self.figures.items():
            with self.subTest(figure=figure_id):
                guide.definition(figure["guide"])  # KeyError if the guide has no such entry
                self.assertIn(figure["method"], methods)

    def test_the_guide_places_each_figure_on_its_page(self) -> None:
        gaps = []
        for figure_id, figure in self.figures.items():
            location = guide.metric_location(figure["guide"])
            on_page = any(place in location for place in self.pages[figure["page"]]["guide_locations"])
            with self.subTest(figure=figure_id, location=location):
                if figure.get("guide_gap"):
                    self.assertFalse(on_page, "guide_gap is recorded but the guide already agrees")
                    gaps.append(figure_id)
                else:
                    self.assertTrue(on_page)
        self.assertEqual(sorted(gaps), ["risk.maximum_drawdown", "stats.annualized_volatility"])

    def test_figure_list_covers_the_guide(self) -> None:
        certified = {figure["guide"] for figure in self.figures.values()}
        deferred = set(self.register["deferred"])
        not_first = set(self.register["sections_not_first"])
        self.assertFalse(certified & deferred, "a definition is both certified and deferred")
        self.assertEqual(deferred - set(guide.METRIC_ANCHORS), set())
        for section in guide.METRIC_SECTIONS:
            names = {metric.name for metric in section.metrics}
            with self.subTest(section=section.key):
                if section.key in not_first:
                    self.assertFalse(names & certified, "a section marked not-first has certified figures")
                else:
                    self.assertEqual(names - certified - deferred, set(), "neither certified nor deferred")
        self.assertEqual(not_first - {section.key for section in guide.METRIC_SECTIONS}, set())
        for reason in [*self.register["deferred"].values(), *self.register["sections_not_first"].values()]:
            self.assertGreater(len(reason), 20)

    def test_factsheet_bindings_are_covered_with_the_same_unit(self) -> None:
        claimed: dict[str, str] = {}
        for figure_id, figure in self.figures.items():
            for name in figure.get("factsheet", ()):
                self.assertNotIn(name, claimed, f"{name} is claimed twice")
                claimed[name] = figure_id
        factsheet_only = self.register["factsheet_only"]
        for name, binding in BINDINGS.items():
            if binding.guide is None:
                continue
            with self.subTest(binding=name):
                if name in factsheet_only:
                    self.assertNotIn(name, claimed)
                    continue
                self.assertIn(name, claimed)
                figure = self.figures[claimed[name]]
                self.assertEqual(figure["guide"], binding.guide)
                if binding.kind == "scalar":
                    self.assertEqual(figure["unit"], binding.unit)
                elif binding.kind == "series":
                    self.assertEqual(figure["kind"], "series")
                else:
                    self.assertIn(figure["kind"], ("per_item", "per_period"))
        self.assertEqual(set(claimed) - set(BINDINGS), set())

    def test_sources_are_catalogued_with_contracts(self) -> None:
        targets = _catalog_targets()
        for figure_id, figure in self.figures.items():
            names = list(figure.get("inputs", ()))
            if figure["source"] == "new":
                self.assertTrue(names, f"{figure_id} needs inputs")
            else:
                self.assertTrue(figure["source"].startswith("gold."), figure_id)
                self.assertTrue(figure.get("field"), figure_id)
                names.append(figure["source"])
            for name in names:
                with self.subTest(figure=figure_id, table=name):
                    layer, table = name.split(".", 1)
                    self.assertEqual(targets.get(name), layer)
                    self.assertTrue((CONTRACTS / layer / f"{table}.yaml").exists())

    def test_batches_fit_the_month_end_timeline(self) -> None:
        timeline = {key: value for key, value in self.register["timeline"].items()}
        day = date.fromisoformat
        freeze_start, freeze_end = map(day, timeline["freeze"])
        retry_start, retry_end = map(day, timeline["retry_days"])
        last_move = day(timeline["last_move"])
        self.assertLess(day(timeline["approval"]), day(timeline["shadow_from"]))
        self.assertEqual(last_move, retry_end)
        self.assertLess(last_move, freeze_start)
        self.assertLess(freeze_end, day(timeline["after_month_end"]))
        batched, previous = [], day(timeline["shadow_from"])
        for number, batch in sorted(self.register["batches"].items(), key=lambda item: int(item[0])):
            moves = day(batch["date"])
            with self.subTest(batch=number):
                self.assertGreater(moves, previous)
                self.assertLess(moves, retry_start)
                self.assertNotIn(batch["date"], timeline["no_moves"])
                for page_id in batch["pages"]:
                    self.assertEqual(self.pages[page_id]["batch"], int(number))
            batched += batch["pages"]
            previous = moves
        self.assertEqual(sorted(batched), sorted(self.pages))
        by_order = sorted(self.pages.values(), key=lambda page: page["order"])
        self.assertEqual([page["batch"] for page in by_order], sorted(page["batch"] for page in by_order))

    def test_pages_show_only_what_is_certified_by_then(self) -> None:
        deferred = self.register["deferred"]
        for page_id, page in self.pages.items():
            locations = page["guide_locations"]
            own = [figure_id for figure_id, figure in self.figures.items() if figure["page"] == page_id]
            shown = page.get("shows_from_other_pages", [])
            with self.subTest(page=page_id):
                self.assertTrue(own or shown, "a page moves with no certified figures")
                self.assertTrue(locations or page.get("note"), "a page the guide does not name needs a note")
                for figure_id in shown:
                    figure = self.figures[figure_id]
                    self.assertNotEqual(figure["page"], page_id)
                    self.assertLessEqual(self.pages[figure["page"]]["batch"], page["batch"],
                                         f"{figure_id} is not certified by this page's batch")
                    if locations:
                        self.assertTrue(any(place in guide.metric_location(figure["guide"])
                                            for place in locations), figure_id)
                for name in page["deferred_here"]:
                    self.assertIn(name, deferred)
                    self.assertTrue(any(place in guide.metric_location(name) for place in locations), name)

    def test_every_website_page_is_placed(self) -> None:
        web = _web_constants()
        text = WEB.read_text(encoding="utf-8")
        out_of_scope, later = self.register["out_of_scope_pages"], self.register["after_month_end"]
        self.assertFalse(set(out_of_scope) & set(later))
        moving: set[tuple[str, str | None]] = set()
        for page_id, page in self.pages.items():
            for route in page["routes"]:
                with self.subTest(page=page_id, route=route):
                    parts = route.strip("/").split("/")
                    if route == "/":
                        self.assertIn('@app.get("/", response_class=HTMLResponse)', text)
                    elif parts[0] == "accounts":
                        section, part = parts[2], (parts[3] if len(parts) > 3 else None)
                        self.assertIn(section, web["ACCOUNT_SECTIONS"])
                        if part is not None:
                            self.assertIn(part, [slug for slug, *_ in web["ACCOUNT_SUBPAGES"][section]])
                        moving.add((section, part))
                    else:
                        self.assertIn(parts[0], web["PLATFORM_SECTIONS"])
                        moving.add((parts[0], None))
            for api in page["apis"]:
                with self.subTest(page=page_id, api=api):
                    self.assertIn(f'@app.get("{api.replace("{id}", "{account_id}")}"', text)
        for section in [*web["ACCOUNT_SECTIONS"], *web["PLATFORM_SECTIONS"]]:
            with self.subTest(section=section):
                if section in out_of_scope or section in later:
                    self.assertFalse(any(placed == section for placed, _ in moving))
                    continue
                parts = [slug for slug, *_ in web["ACCOUNT_SUBPAGES"].get(section, ())]
                for part in parts or [None]:
                    self.assertTrue((section, part) in moving or part in later, f"{section}/{part} is not placed")
        for reason in [*out_of_scope.values(), *later.values()]:
            self.assertGreater(len(reason), 20)

    def test_named_payload_fields_come_from_todays_code(self) -> None:
        from ibkr_etl.benchmark_analytics import ComparisonRow, comparison_metrics, portfolio_risk_metrics

        rows, level, benchmark = [], 100.0, 100.0
        for day in range(1, 31):
            portfolio_return = 0.001 * ((day * 7) % 5 - 2) if day > 1 else None
            benchmark_return = 0.001 * ((day * 3) % 5 - 2) if day > 1 else None
            if day > 1:
                level *= 1 + portfolio_return
                benchmark *= 1 + benchmark_return
            rows.append(ComparisonRow(
                f"2026-01-{day:02d}", level * 1000, benchmark * 10, level, benchmark, portfolio_return,
                benchmark_return, None if day == 1 else portfolio_return - benchmark_return))
        risk = portfolio_risk_metrics(rows, 0.04)
        comparison = comparison_metrics(rows)
        for figure_id, figure in self.figures.items():
            field = figure.get("field", "")
            with self.subTest(figure=figure_id, field=field):
                if field.startswith("payload_json.metrics."):
                    self.assertIn(field.rsplit(".", 1)[1], risk)
                elif field.startswith("comparison_metrics:"):
                    self.assertIn(field.split(":", 1)[1], comparison)


if __name__ == "__main__":
    unittest.main()
