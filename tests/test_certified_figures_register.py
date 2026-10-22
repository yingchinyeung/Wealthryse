"""The certified-figures register must agree with the Metrics guide (medallion upgrade U5).

infra/medallion/certified-figures.json lists the figures to certify first, page by page,
and the five batches the pages move in before the November month-end
(docs/MEDALLION_CERTIFIED_FIGURES.md). src/ibkr_etl/certified_figures.py reads it, through an
identical copy in the package. These checks keep it honest:

- the packaged copy is identical to the reviewed file, and the module's API reads it;
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

from ibkr_etl import certified_figures as cf
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
    return cf.register()


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
                self.assertEqual(bool(figure.get("periods")), figure["kind"] in ("per_period", "per_period_item"))
                self.assertEqual(bool(figure.get("item")), figure["kind"] in ("per_item", "per_period_item"))
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
        # The owner's 18 Oct plan: the remaining switch-ons, one setting a day, Mon 26 to Thu 29.
        switch_start, switch_end = map(day, timeline["october_switch_ons"])
        last_move = day(timeline["last_move"])
        self.assertLessEqual(switch_end, last_move)
        self.assertLess(day(timeline["approval"]), day(timeline["shadow_from"]))
        self.assertEqual(last_move, retry_end)
        self.assertLess(last_move, freeze_start)
        self.assertLess(freeze_end, day(timeline["after_month_end"]))
        batched, previous = [], day(timeline["shadow_from"])
        for number, batch in sorted(self.register["batches"].items(), key=lambda item: int(item[0])):
            moves = day(batch["date"])
            with self.subTest(batch=number):
                self.assertGreater(moves, previous)
                if batch["pages"] == ["charts"]:
                    # The charts move on the last move day, after the retries of the pages they sit on.
                    self.assertEqual(moves, last_move)
                else:
                    self.assertTrue(moves < retry_start or switch_start <= moves <= switch_end, moves)
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



class CertifiedFiguresModuleTests(unittest.TestCase):
    """The API the build, the website readers and the agents import."""

    def tearDown(self) -> None:
        cf.reload()

    def test_the_register_is_found_in_the_repository_the_setting_or_the_image(self) -> None:
        import tempfile
        from unittest.mock import patch

        self.assertEqual(cf.medallion_dir({}), ROOT / "infra" / "medallion")
        self.assertEqual(cf.register_path({}), REGISTER)
        self.assertEqual(cf.register(), json.loads(REGISTER.read_text(encoding="utf-8")))
        self.assertEqual(cf.medallion_dir({"MEDALLION_DIR": "/somewhere/medallion"}), Path("/somewhere/medallion"))
        self.assertEqual(cf.medallion_dir({"MEDALLION_DIR": "  "}), ROOT / "infra" / "medallion")
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "medallion"
            image.mkdir()
            (image / cf.REGISTER_NAME).write_bytes(REGISTER.read_bytes())
            # As in the image: no repository folder, the register at /app/medallion.
            with patch.object(cf, "REPOSITORY_DIR", Path(folder) / "absent"), patch.object(cf, "IMAGE_DIR", image):
                self.assertEqual(cf.medallion_dir({}), image)
                cf.reload()
                self.assertEqual(len(cf.figures()), 84)
                # MEDALLION_DIR first, when it has the register.
                elsewhere = Path(folder) / "elsewhere"
                with patch.dict("os.environ", {"MEDALLION_DIR": str(elsewhere)}):
                    self.assertEqual(cf.register_path(), image / cf.REGISTER_NAME)
                    elsewhere.mkdir()
                    (elsewhere / cf.REGISTER_NAME).write_bytes(REGISTER.read_bytes())
                    self.assertEqual(cf.register_path(), elsewhere / cf.REGISTER_NAME)
            # As in the website overlay or the agents image: no folder has it, so the packaged copy.
            with patch.object(cf, "REPOSITORY_DIR", Path(folder) / "absent"), \
                    patch.object(cf, "IMAGE_DIR", Path(folder) / "absent-too"), \
                    patch.dict("os.environ", {"MEDALLION_DIR": ""}):
                self.assertEqual(cf.register_path(), cf.PACKAGED_REGISTER)
                cf.reload()
                self.assertEqual(len(cf.figures()), 84)
                # As in the website: its release ships only .py files, so the copy as code.
                with patch.object(cf, "PACKAGED_REGISTER", Path(folder) / "absent.json"):
                    self.assertEqual(cf.register_path(), cf.PACKAGED_MODULE)
                    cf.reload()
                    self.assertEqual(cf.register(), json.loads(REGISTER.read_text(encoding="utf-8")))
                    self.assertEqual(len(cf.figures()), 84)
        cf.reload()
        self.assertEqual(len(cf.figures()), 84)

    def test_the_packaged_copy_is_the_register(self) -> None:
        # After changing the register: python -m ibkr_etl.certified_figures --write
        packaged = Path(cf.__file__).with_name("certified_figures_register.json")
        self.assertEqual(packaged.read_bytes(), REGISTER.read_bytes())
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"certified_figures_register.json"', pyproject)

    def test_the_copy_as_code_is_the_register(self) -> None:
        # The website release ships only .py files (deploy/website_release.py); refresh with --write.
        from ibkr_etl import certified_figures_register_data as packaged

        self.assertEqual(packaged.TEXT, REGISTER.read_text(encoding="utf-8"))
        self.assertEqual(cf.PACKAGED_MODULE.read_text(encoding="utf-8"),
                         cf.module_source(REGISTER.read_text(encoding="utf-8")))
        self.assertEqual(cf.main([]), 0)
        with self.assertRaises(ValueError):
            cf.module_source("{\"a\": \"'''\"}")

    def test_figures_carry_their_definition_and_version(self) -> None:
        figures = cf.figures()
        self.assertEqual(list(figures), list(cf.register()["figures"]))
        self.assertEqual(len(figures), 84)
        sharpe = cf.figure("risk.sharpe_ratio")
        self.assertEqual((sharpe.label, sharpe.definition_id, sharpe.link, sharpe.unit, sharpe.page, sharpe.batch),
                         ("Sharpe ratio", "metric-sharpe-ratio", "/metrics-guide#metric-sharpe-ratio", "number",
                          "risk-snapshot", 3))
        self.assertRegex(sharpe.definition_version, r"^[0-9a-f]{12}$")
        self.assertEqual(sharpe.definition_version, cf.definition_version("Sharpe ratio"))
        self.assertNotEqual(sharpe.definition_version, cf.definition_version("Sortino ratio"))
        for figure in figures.values():
            with self.subTest(figure=figure.id):
                self.assertEqual(figure.definition_id, guide.METRIC_ANCHORS[figure.guide])
                self.assertTrue(figure.source_tables)
        labels = [figure.label for figure in figures.values()]
        self.assertEqual(len(labels), len(set(labels)), "two figures share a label")
        self.assertEqual(cf.figure("stats.maximum_drawdown").label, "Maximum drawdown (every NAV date)")
        self.assertEqual(cf.figure("risk.maximum_drawdown").label, "Maximum drawdown (benchmark dates)")
        self.assertEqual(cf.figure("stats.best_day").label, "Best day")

    def test_wording_changes_the_definition_version(self) -> None:
        from dataclasses import replace
        from unittest.mock import patch

        before = cf.definition_version("Sharpe ratio")
        changed = replace(guide.definition("Sharpe ratio"), meaning="Something else.")
        with patch.dict(guide._BY_NAME, {"Sharpe ratio": changed}):
            self.assertNotEqual(cf.definition_version("Sharpe ratio"), before)

    def test_unknown_ids_name_the_closest(self) -> None:
        with self.assertRaises(KeyError) as caught:
            cf.figure("risk.sharpe_ration")
        self.assertIsInstance(caught.exception, cf.UnknownFigure)
        self.assertIn("risk.sharpe_ratio", caught.exception.closest)
        self.assertIn("risk.sharpe_ratio", str(caught.exception))
        with self.assertRaises(KeyError):
            cf.page("trades")

    def test_pages_batches_and_what_each_page_shows(self) -> None:
        self.assertEqual(list(cf.pages()), ["overview", "holdings", "cash", "performance", "risk-snapshot",
                                            "risk-statistics", "combined-holdings", "attribution", "charts"])
        self.assertEqual(cf.batch_for_page("performance"), cf.Batch(2, date(2026, 10, 23)))
        overview = [figure.id for figure in cf.figures_for_page("overview")]
        self.assertEqual(overview[0], "overview.as_of")
        self.assertEqual(overview[-2:], ["holdings.holding_weight", "holdings.sector_allocation"])
        combined = [figure.id for figure in cf.figures_for_page("combined-holdings")]
        self.assertEqual(combined, ["holdings.quantity", "holdings.market_value", "holdings.cost_basis",
                                    "holdings.unrealized_pnl"])
        per_page = sum(len([f for f in cf.figures().values() if f.page == page]) for page in cf.pages())
        self.assertEqual(per_page, 84)

    def test_page_modes_default_off_and_ignore_typos(self) -> None:
        self.assertEqual(set(cf.page_modes(None).values()), {"off"})
        self.assertEqual(set(cf.page_modes("").values()), {"off"})
        modes = cf.page_modes(" Overview=on, holdings=shadow,cash=ON,performance=maybe,trades=on,risk-snapshot")
        self.assertEqual({page: mode for page, mode in modes.items() if mode != "off"},
                         {"overview": "on", "holdings": "shadow", "cash": "on"})
        served = {figure.page for figure in cf.served_figures(modes)}
        self.assertEqual(served, {"overview", "cash"})
        self.assertEqual(cf.served_figures(cf.page_modes("")), [])

    def test_periods(self) -> None:
        twr, monthly = cf.figure("overview.twr"), cf.figure("performance.monthly_return")
        for period in ("mtd", "qtd", "ytd", "year_2025", "since_inception"):
            self.assertTrue(cf.period_allowed(twr, period), period)
        for period in ("", "month_2026-10", "year_25", "1y"):
            self.assertFalse(cf.period_allowed(twr, period), period)
        self.assertTrue(cf.period_allowed(monthly, "month_2026-10"))
        self.assertFalse(cf.period_allowed(monthly, "month_2026-13"))
        self.assertTrue(cf.period_allowed(cf.figure("risk.sharpe_ratio"), ""))
        self.assertFalse(cf.period_allowed(cf.figure("risk.sharpe_ratio"), "ytd"))

    def test_calculation_versions(self) -> None:
        self.assertEqual(cf.calculation_version(cf.figure("risk.sharpe_ratio"), "risk-analytics-v2"),
                         "risk-analytics-v2+benchmark-comparison-v1")
        self.assertEqual(cf.calculation_version(cf.figure("stats.skewness")), "portfolio-analytics-v1")
        self.assertEqual(cf.calculation_version(cf.figure("stats.skewness"), "portfolio-analytics-v1"),
                         "portfolio-analytics-v1")
        self.assertEqual(cf.calculation_version(cf.figure("cash.ending_cash"), "silver.subject_cash_balance_series@1.0.0"),
                         "imported+silver.subject_cash_balance_series@1.0.0")

    def test_freshness(self) -> None:
        self.assertEqual(cf.freshness("2026-10-21", "2026-10-21")["status"], "fresh")
        behind = cf.freshness(date(2026, 10, 16), date(2026, 10, 20), reason="The build was held.")
        self.assertEqual((behind["status"], behind["business_days_behind"], behind["reason"]),
                         ("behind", 2, "The build was held."))
        self.assertEqual(cf.freshness("2026-10-09", "2026-10-16")["status"], "late")
        self.assertEqual(cf.freshness("2026-10-12", "2026-10-16")["status"], "behind")
        self.assertNotIn("reason", cf.freshness("2026-10-16", "2026-10-16", reason="x"))


if __name__ == "__main__":
    unittest.main()
