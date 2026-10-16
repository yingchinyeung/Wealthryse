"""Independent formula checker: the guide's formulas recomputed separately, and the weekly guide_check job."""
from __future__ import annotations

import os
import random
import re
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")

from ibkr_etl import metrics_guide as guide  # noqa: E402
from ibkr_etl import metrics_guide_reference as reference  # noqa: E402
from wealthryse_agent import jobs  # noqa: E402
from wealthryse_agent.runtime import TOOL_LABELS, Turn  # noqa: E402
from wealthryse_agent.tool_client import ToolCallFailed  # noqa: E402
from wealthryse_agent.tools import REGISTRY  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _series(days=600, seed=7):
    random.seed(seed)
    nav, flows, value, day = {}, {}, 1_000_000.0, date(2024, 1, 2)
    for i in range(days):
        if day.weekday() < 5:
            flow = 25_000.0 if i % 61 == 0 and i else 0.0
            value = value * (1 + random.gauss(0.0003, 0.011)) + flow
            nav[day.isoformat()] = value
            if flow:
                flows[day.isoformat()] = flow
        day += timedelta(days=1)
    return nav, flows


class ReferenceTest(unittest.TestCase):
    def test_every_check_names_a_guide_definition(self):
        for metric, _key, _group in reference.CHECKS:
            self.assertIn(metric, guide.METRIC_ANCHORS)
        for metric, _key, _agent in reference.AGENT_CHECKS:
            self.assertIn(metric, guide.METRIC_ANCHORS)

    def test_the_website_agrees_with_the_guide_today(self):
        nav, flows = _series()
        result = reference.conformance(nav, flows)
        self.assertEqual(result["mismatches"], 0, [c for c in result["checks"] if c["status"] != "match"])
        self.assertEqual(result["checked"], len(reference.CHECKS))

    def test_a_website_change_that_leaves_the_guide_behind_is_caught(self):
        nav, flows = _series()
        with mock.patch("ibkr_etl.portfolio_analytics.TRADING_DAYS_PER_YEAR", 260):
            result = reference.conformance(nav, flows)
        differing = {c["figure"] for c in result["checks"] if c["status"] == "mismatch"}
        self.assertEqual(differing, {"annualized_volatility", "downside_deviation", "sortino_ratio"})

    def test_the_analysts_annualisation_is_checked_over_its_own_window(self):
        nav, flows = _series()
        days = sorted(nav)
        start, end = days[-253], days[-1]
        window = {d: v for d, v in nav.items() if start <= d <= end}
        expected = reference.statistics(reference.daily_returns(window, {d: f for d, f in flows.items() if start < d <= end}), start)
        analyst = {"from_date": start, "to_date": end, "total_return": expected["total_return"],
                   "annualized_volatility": expected["annualized_volatility"] * (260 / 252) ** 0.5,
                   "downside_deviation": expected["downside_deviation"], "max_drawdown": expected["maximum_drawdown"]}
        checks = [c for c in reference.conformance(nav, flows, analyst)["checks"] if c["source"] == "analyst get_risk_metrics"]
        self.assertEqual({c["figure"]: c["status"] for c in checks},
                         {"total_return": "match", "annualized_volatility": "mismatch", "downside_deviation": "match",
                          "maximum_drawdown": "match"})

    def test_the_reference_shares_no_code_with_the_website(self):
        source = (ROOT / "src/ibkr_etl/metrics_guide_reference.py").read_text(encoding="utf-8")
        top = source.split("def website_figures")[0]
        self.assertNotIn("portfolio_analytics", top.replace("``", ""))
        self.assertNotIn("benchmark_analytics", top)
        self.assertNotIn("import statistics", source)


class _Store:
    def __init__(self):
        self.items = {}

    def inbox_exists(self, kind, account_id, key):
        return (kind, account_id, key) in self.items

    def add_inbox(self, *, kind, account_id, period_key, title, body, citations, run_id):
        self.items[(kind, account_id, period_key)] = (title, body, citations)
        return True


class _Agent:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        kind = "answer" if self.ok else "rejected"
        yield {"type": kind, "turn": Turn(answer="Volatility differs by 0.01 [T1].", citations=[{"id": "T1"}])}


def _facts(mismatches):
    checks = [{"metric": "Annualized volatility", "figure": "annualized_volatility", "source": "website statistics",
               "guide_formula_value": 0.1, "shown_value": 0.11, "difference": 0.01, "status": "mismatch"}][:mismatches]
    checks += [{"metric": name, "figure": key, "source": "website statistics", "guide_formula_value": 0.2,
                "shown_value": 0.2, "difference": 0.0, "status": "match"}
               for name, key in (("Total return", "total_return"), ("Win rate", "win_rate"))]
    return {"data": {"checks": checks, "checked": len(checks), "mismatches": mismatches, "observations": 400,
                     "period_start": "2025-01-02", "period_end": "2026-10-15"},
            "citations": [{"source": "subject_nav_series", "account_id": "U1"}]}


class GuideCheckJobTest(unittest.TestCase):
    def test_a_mismatch_gets_a_weekly_card_with_the_analysts_explanation(self):
        store, agent, tools = _Store(), _Agent(), mock.Mock()
        tools.call.return_value = _facts(1)
        tools.list_tools.return_value = []
        written = jobs.guide_check(store, tools, agent, ["U1"], {}, today=date(2026, 10, 19))
        self.assertEqual(written, 1)
        title, body, _ = store.items[("guide_check", "U1", "2026-W43")]
        self.assertEqual(title, "Metrics guide check: 1 of 3 figures differ")
        self.assertIn("0.01 [T1]", body)
        self.assertEqual(agent.calls[0]["allowed_tools"], ["get_guide_conformance", "get_metric_definition", "calculate"])
        self.assertIn("search_metrics_guide", [t["name"] for t in agent.calls[0]["tools"].list_tools()])
        self.assertEqual(jobs.guide_check(store, tools, agent, ["U1"], {}, today=date(2026, 10, 20)), 0)

    def test_an_unchecked_explanation_falls_back_to_the_facts(self):
        store, tools = _Store(), mock.Mock()
        tools.call.return_value = _facts(1)
        jobs.guide_check(store, tools, _Agent(ok=False), ["U1"], {}, today=date(2026, 10, 19))
        body = store.items[("guide_check", "U1", "2026-W43")][1]
        self.assertIn("Annualized volatility (website statistics): guide formula 0.1, shown 0.11", body)

    def test_all_clear_is_posted_once_a_month_and_short_histories_are_skipped(self):
        store, agent, tools = _Store(), _Agent(), mock.Mock()
        tools.call.return_value = _facts(0)
        self.assertEqual(jobs.guide_check(store, tools, agent, ["U1"], {}, today=date(2026, 10, 5)), 1)
        self.assertEqual(jobs.guide_check(store, tools, agent, ["U1"], {}, today=date(2026, 10, 12)), 0)
        self.assertEqual(store.items[("guide_check", "U1", "2026-10")][0], "Metrics guide check: all 2 figures match")
        self.assertEqual(agent.calls, [])
        tools.call.side_effect = ToolCallFailed("not_enough_history", "x")
        self.assertEqual(jobs.guide_check(_Store(), tools, agent, ["U2"], {}, today=date(2026, 10, 5)), 0)


class GuideCheckWiringTest(unittest.TestCase):
    def test_tool_is_for_the_guide_check_job_only(self):
        spec = REGISTRY["get_guide_conformance"]
        self.assertEqual(spec.only_jobs, frozenset({"guide_check"}))
        self.assertIn("get_guide_conformance", TOOL_LABELS)

    def test_job_is_listed_everywhere_a_job_must_be(self):
        from wealthryse_agent.access import JOBS

        self.assertIn("guide_check", JOBS)
        self.assertIn('"guide_check"', (ROOT / "scripts/install_agent_schema.py").read_text(encoding="utf-8"))
        self.assertIn("job: 'guide_check'", (ROOT / "infra/agent-apps.bicep").read_text(encoding="utf-8"))
        self.assertIn("guide_check:'Guide check'", (ROOT / "src/ibkr_etl/agent_web.py").read_text(encoding="utf-8"))
        self.assertIn("guide_check", (ROOT / "docs/AGENTS.md").read_text(encoding="utf-8"))
        migration = (ROOT / "migrations/agent/016_guide_check.sql").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"'guide_check'", migration)), 2)
        self.assertNotIn("NOT VALID", migration)
        earlier = (ROOT / "migrations/agent/015_export_agent.sql").read_text(encoding="utf-8")
        self.assertEqual(earlier.count("NOT VALID;"), 2)
        self.assertEqual(sorted(p.name for p in (ROOT / "migrations/agent").glob("*.sql"))[-1], "016_guide_check.sql")


if __name__ == "__main__":
    unittest.main()
