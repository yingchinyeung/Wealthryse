"""Certified figures on the website pages (U5, part 5): off, shadow and on, page by page.

Synthetic data only. The certified view is replaced by an in-memory stand-in that answers
the reader's query the way gold.v_certified_figures does (newest published as-of date on or
before the asked date); tests/test_certified_pages_db.py runs the same reader on PostgreSQL.
"""
from __future__ import annotations

import copy
import json
import math
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from ibkr_etl import certified_pages as pages
from ibkr_etl import web
from ibkr_etl.benchmark_analytics import ComparisonRow, comparison_metrics
from ibkr_etl.holdings_view import enrich_holdings
from ibkr_etl.performance_periods import performance_periods
from ibkr_etl.portfolio_analytics import holdings_concentration, portfolio_analytics
from ibkr_etl.web_ui import CERTIFIED_LINE_SCRIPT

TENANT = "00000000-0000-0000-0000-000000000001"
SUBJECT = "U9100001"
LATEST, EARLIER = "2026-10-21", "2026-10-20"
SETTING = "CERTIFIED_FIGURES_PAGES"
LOGGER = "uvicorn.error.certified_figures"
VIEWER = {"email": "viewer@example.com", "object_id": "viewer-1", "role": "viewer"}
ACCESS = {"subject_id": SUBJECT, "reporting_currency": "USD", "base_currency": "USD",
          "history_start_date": "2025-01-02", "subject_type": "physical_account"}
LOG_KEYS = {"endpoint", "page", "mode", "status", "compared", "differ", "missing", "extra", "read_ms",
            "business_days_behind", "error"}


# --- Synthetic answers, made with today's functions where the page uses them ---------------


def _weekdays(start: str, through: str) -> list[str]:
    day, end, found = date.fromisoformat(start), date.fromisoformat(through), []
    while day <= end:
        if day.weekday() < 5:
            found.append(day.isoformat())
        day += timedelta(days=1)
    return found


def _nav(through: str) -> list[tuple[str, str]]:
    days = _weekdays("2025-01-02", through)
    return [(day, f"{1_000_000 * (1 + 0.0004 * index + 0.01 * math.sin(index / 7)):.2f}")
            for index, day in enumerate(days)]


def _summary(day):
    on = day or LATEST
    nav = dict(_nav(on))
    return {"account": {"account_id": SUBJECT, "base_currency": "USD", "account_type": "physical_account",
                        "last_seen_at": None},
            "nav_coverage": {"first_date": "2025-01-02", "last_date": on, "observations": len(nav)},
            "latest_nav": {"snapshot_date": on, "currency": "USD", "total_text": nav[on]},
            "current_positions": 3, "requested_as_of": day, "effective_as_of": on, "viewer": VIEWER["email"]}


def _periods(day):
    on = day or LATEST
    result = performance_periods(_nav(on), {"2026-03-02": 25_000.0}, as_of=on, custom_start=on, custom_end=on)
    return {"account_id": SUBJECT, "currency": "USD", **result, "requested_as_of": day,
            "flows_applicable": True, "flows_message": "", "methodology": "…", "viewer": VIEWER["email"]}


def _account_entry(day):
    nav = _nav(day or LATEST)
    entry = {"account_id": SUBJECT, "currency": "USD", "nav_date": nav[-1][0], "nav": float(nav[-1][1]),
             "mtd": None, "ytd": None}
    for period in performance_periods(nav, {}, as_of=day)["periods"]:
        if period["key"] in ("mtd", "ytd"):
            entry[period["key"]] = period["twr"]
    return entry


HOLDING_ROWS = (("1001", "AAA", "USD", "100", "20000.5", "15000", "5000.5", "Technology"),
                ("1002", "BBB", "HKD", "2000", "78000", "80000", "", "Financials"),
                ("1003", "CCC", "USD", "-50", "-4000", "-3500", "-500", "Health Care"))


def _holding_rows(on):
    return [{"snapshot_date": on, "conid": conid, "symbol": symbol, "description": symbol + " Inc",
             "currency": currency, "quantity_text": quantity, "mark_price_text": "1",
             "market_value_text": value, "cost_basis_text": cost, "unrealized_pnl_text": pnl, "sector": sector}
            for conid, symbol, currency, quantity, value, cost, pnl, sector in HOLDING_ROWS]


def _holdings(day):
    on = day or LATEST
    view = enrich_holdings(_holding_rows(on), reporting_currency="USD",
                           fx_rows=[{"rate_date": EARLIER, "from_currency": "HKD", "to_currency": "USD",
                                     "rate_text": "0.1286"}], nav_total="40000")
    view["fx_account_id"] = SUBJECT
    return {"account_id": SUBJECT, "requested_as_of": day, "snapshot_date": on, **view,
            "concentration": holdings_concentration(view)}


def _sectors(day):
    on = day or LATEST
    return {"account_id": SUBJECT, "requested_as_of": day, "as_of": on, "quality_status": "warn",
            "available": True, "position_date": on,
            "allocation": [{"sector": "Technology", "weight": 0.6, "holdings": 1, "market_value": 20000.5},
                           {"sector": "Financials", "weight": 0.4, "holdings": 1, "market_value": 10030.8}],
            "allocation_detail": [], "classification_coverage_pct": 100.0}


def _cash(day):
    on = day or LATEST
    return {"account_id": SUBJECT, "as_of": on, "balances_date": on,
            "balances": [{"snapshot_date": on, "currency": "BASE_SUMMARY", "ending_cash_text": "5120.75",
                          "settled_cash_text": "5000.25", "fx_rate_to_base_text": "1"},
                         {"snapshot_date": on, "currency": "HKD", "ending_cash_text": "1000",
                          "settled_cash_text": "1000", "fx_rate_to_base_text": "0.1286"}],
            "activity_from": "2025-01-02", "activity_to": on, "total_count": 1,
            "transactions": [{"effective_date": "2026-10-01", "amount_text": "120.5", "currency": "USD"}],
            "viewer": VIEWER["email"]}


def _analytics(day):
    return {"account_id": SUBJECT, "currency": "USD", "requested_as_of": day, "flows_applicable": True,
            **portfolio_analytics(_nav(day or LATEST), {}, as_of=day)}


def _comparison_rows(on):
    nav = _nav(on)
    rows, previous = [], None
    for index, (day, value) in enumerate(nav[-260:]):
        level = 5000 * (1 + 0.0003 * index + 0.008 * math.cos(index / 5))
        portfolio = float(value)
        if previous is None:
            first = (portfolio, level)
            returns = (None, None, None)
        else:
            p, b = portfolio / previous[0] - 1, level / previous[1] - 1
            returns = (p, b, p - b)
        rows.append(ComparisonRow(day, portfolio, level, 100 * portfolio / first[0], 100 * level / first[1], *returns))
        previous = (portfolio, level)
    return rows


def _benchmark(day):
    rows = _comparison_rows(day or LATEST)
    return {"account_id": SUBJECT, "as_of": day, "available": True, "publishable": False,
            "benchmark": {"benchmark_id": "GSPC", "symbol": "^GSPC", "name": "S&P 500"},
            "reporting_currency": "USD", "metrics": comparison_metrics(rows),
            "series": [{"date": row.observation_date, "portfolio": row.normalized_portfolio,
                        "benchmark": row.normalized_benchmark} for row in rows],
            "message": "", "viewer": VIEWER["email"]}


def _risk(day):
    on = day or LATEST
    shift = 0.0 if on == LATEST else 0.001
    names = [name for _, name in pages.RISK_FIELDS]
    metrics = {name: round(-0.05 + 0.01 * index + shift, 6) for index, name in enumerate(names)}
    metrics["daily_expected_tail_loss"] = metrics["cvar_95_daily"]
    metrics["period_end"] = on
    return {"account_id": SUBJECT, "as_of": on, "metrics": metrics, "risk_free": {"rate": 0.04},
            "cache_status": "hit", "snapshot_payload_sha256": "a" * 64, "viewer": VIEWER["email"]}


def _risk_details(day):
    on = day or LATEST
    return {"account_id": SUBJECT, "as_of": on, "cache_status": "hit", "snapshot_payload_sha256": "a" * 64,
            "expected_tail_loss_contributors": {
                "top": [{"conid": "1001", "symbol": "AAA", "description": "AAA Inc",
                         "expected_tail_loss_contribution": 0.0042}],
                "bottom": [{"conid": "1003", "symbol": "CCC", "description": "CCC Inc",
                            "expected_tail_loss_contribution": -0.0011}],
                "residual_contribution": 0.0007},
            "crisis_replay": None,
            "crisis_scenarios": {"holdings_as_of": on, "scenarios": [
                {"scenario_id": "gfc-2008", "label": "GFC", "available": True, "maximum_drawdown": -0.351}]},
            "unavailable_capabilities": {}}


def _records(day):
    on = day or LATEST
    return {"snapshot_date": on, "rows": _holding_rows(on)}


ANSWERS = {
    "accounts-overview": (_account_entry, "overview=on"),
    "summary": (_summary, "overview=on"),
    "performance-periods": (_periods, "overview=on"),
    "holdings": (_holdings, "holdings=on"),
    "sectors": (_sectors, "holdings=on"),
    "cash": (_cash, "cash=on"),
    "analytics": (_analytics, "performance=on,risk-statistics=on"),
    "benchmark-comparison": (_benchmark, "performance=on"),
    "risk": (_risk, "risk-snapshot=on"),
    "risk-details": (_risk_details, "risk-snapshot=on"),
    "combined-holdings": (_records, "holdings=on,combined-holdings=on"),
}


# --- A stand-in for gold.v_certified_figures -----------------------------------------------


def certified_rows(endpoint: str, answer: dict, as_of: str = LATEST, *, build: str = "b1",
                   published: datetime | None = None, subject: str = SUBJECT) -> list[dict]:
    """The rows a correct build would publish for the figures on ``answer``."""
    spec = pages.ENDPOINTS[endpoint]
    published = published or datetime(2026, 10, 22, 9, 52, tzinfo=timezone.utc)
    rows = {}
    for slot in spec.slots(answer):
        unit = pages.register.figure(slot.key[0]).unit
        value = slot.get()
        rows[slot.key] = {
            "subject": subject, "figure_id": slot.key[0], "period": slot.key[1], "item": slot.key[2],
            "benchmark_id": slot.key[3], "as_of_date": as_of, "unit": unit,
            "value": None if unit in ("date", "text") else value,
            "value_text": value if unit in ("date", "text") else None,
            "status": "ok" if value is not None else "unavailable", "build_id": build, "published_at": published}
    return list(rows.values())


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return [dict(row) for row in self.rows]


class FakeView:
    """Answers READ_SQL like the view: rows of the newest as-of date on or before the asked one."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.subjects: list[str] = []

    def execute(self, sql, params=None):
        if not isinstance(params, dict):
            return _Result([])
        self.subjects.append(params["subject"])
        wanted = [row for row in self.rows
                  if row["subject"] == params["subject"] and row["figure_id"] in params["figures"]
                  and row["benchmark_id"] in params["benchmarks"]
                  and (params["as_of"] is None or row["as_of_date"] <= params["as_of"])]
        if not wanted:
            return _Result([])
        newest = max(row["as_of_date"] for row in wanted)
        return _Result([row for row in wanted if row["as_of_date"] == newest])

    @contextmanager
    def connect(self, tenant_id):
        assert tenant_id == TENANT
        yield self


def _forbidden_connect(tenant_id):
    raise AssertionError("the certified view must not be read")


def _same_shape(test: unittest.TestCase, today, shown, path="answer"):
    """Same keys at every level and the same list lengths; a value may change, and may be
    filled where today's is null, but never changes type otherwise."""
    if isinstance(today, dict):
        test.assertIsInstance(shown, dict, path)
        test.assertEqual(set(shown) - {"certified"} if path == "answer" else set(shown), set(today), path)
        for key in today:
            _same_shape(test, today[key], shown[key], f"{path}.{key}")
    elif isinstance(today, (list, tuple)):
        test.assertEqual(len(shown), len(today), path)
        for index, (left, right) in enumerate(zip(today, shown)):
            _same_shape(test, left, right, f"{path}[{index}]")
    elif today is not None and shown is not None:
        numeric = (int, float)
        if isinstance(today, numeric) and not isinstance(today, bool):
            test.assertIsInstance(shown, numeric, path)
        else:
            test.assertIsInstance(shown, type(today), path)


def _log_lines(captured) -> list[dict]:
    lines = []
    for message in captured.output:
        text = message.split(":", 2)[2]
        prefix, _, body = text.partition(" ")
        assert prefix == pages.LOG_PREFIX, text
        lines.append(json.loads(body))
    return lines


# --- Per page: off, shadow and on ----------------------------------------------------------


class _PageCase:
    ENDPOINT = ""

    def setUp(self):
        self.make, self.on_setting = ANSWERS[self.ENDPOINT]
        self.spec = pages.ENDPOINTS[self.ENDPOINT]
        self.calls: list = []

    def build(self, day):
        self.calls.append(day)
        return self.make(day)

    def serve(self, setting, view=None, requested=None):
        environ = {} if setting is None else {SETTING: setting}
        connect = view.connect if view is not None else _forbidden_connect
        with patch.object(pages, "_connect", connect):
            return pages.serve(self.ENDPOINT, tenant_id=TENANT, subject_id=SUBJECT, requested_as_of=requested,
                               build=self.build, environ=environ)

    def shadow_setting(self):
        return self.on_setting.replace("=on", "=shadow")

    def changed(self, rows):
        """Move one numeric figure of each group, so the test sees which value is shown."""
        moved = {}
        for group in self.spec.groups:
            for row in rows:
                slot_writes = any(slot.write and slot.key == (row["figure_id"], row["period"], row["item"],
                                                              row["benchmark_id"])
                                  for slot in self.spec.slots(self.make(None)))
                if (row["figure_id"] in group.figure_ids and row["value"] is not None and slot_writes
                        and row["figure_id"] not in {key[0] for key in moved}):
                    row["value"] = row["value"] + 1.25
                    moved[(row["figure_id"], row["period"], row["item"], row["benchmark_id"])] = row["value"]
                    break
        return moved

    def test_off_is_todays_answer_with_no_read_and_no_log(self):
        today = self.make(None)
        for setting in (None, "", "  ", "nonsense", "other-page=on"):
            with self.subTest(setting=setting), self.assertNoLogs(LOGGER, level="DEBUG"):
                self.assertEqual(self.serve(setting), today)
        self.assertEqual(set(self.calls), {None})

    def test_off_while_the_other_pages_are_on(self):
        others = ",".join(f"{page}=on" for page in pages.register.page_modes("")
                          if page not in {group.page for group in self.spec.groups})
        with self.assertNoLogs(LOGGER, level="DEBUG"):
            self.assertEqual(self.serve(others), self.make(None))

    def test_shadow_shows_todays_answer_and_logs_a_match(self):
        today = self.make(None)
        view = FakeView(certified_rows(self.ENDPOINT, today))
        with self.assertLogs(LOGGER, level="INFO") as captured:
            self.assertEqual(self.serve(self.shadow_setting(), view), today)
        lines = _log_lines(captured)
        self.assertEqual({line["page"] for line in lines}, {group.page for group in self.spec.groups})
        for line in lines:
            self.assertEqual((line["mode"], line["status"]), ("shadow", "match"), line)
            self.assertGreater(line["compared"], 0)
        self.assertEqual(self.calls, [None])

    def test_shadow_logs_figure_ids_and_counts_only(self):
        today = self.make(None)
        rows = certified_rows(self.ENDPOINT, today)
        moved = self.changed(rows)
        with self.assertLogs(LOGGER, level="INFO") as captured:
            self.assertEqual(self.serve(self.shadow_setting(), FakeView(rows)), today)
        lines = _log_lines(captured)
        differ = {figure: count for line in lines for figure, count in line["differ"].items()}
        self.assertEqual(differ, {key[0]: 1 for key in moved})
        for text, line in zip(captured.output, lines):
            self.assertLessEqual(set(line), LOG_KEYS)
            self.assertNotIn(SUBJECT, text)
            self.assertNotIn("1001", text)  # a holding id
            for value in moved.values():
                self.assertNotIn(repr(value)[:6], text)

    def test_on_shows_certified_values_in_todays_shape(self):
        today = self.make(None)
        rows = certified_rows(self.ENDPOINT, today)
        moved = self.changed(rows)
        self.assertTrue(moved)
        with self.assertNoLogs(LOGGER, level="INFO"):
            shown = self.serve(self.on_setting, FakeView(rows))
        _same_shape(self, today, shown)
        block = shown["certified"]
        self.assertEqual(block["as_of"], LATEST)
        self.assertEqual(block["label"], "Certified · as of 21 Oct 2026")
        self.assertIsNone(block["note"])
        self.assertEqual(block["freshness"]["status"], "fresh")
        self.assertEqual(block["build_id"], "b1")
        self.assertEqual(set(block["pages"]), {group.page for group in self.spec.groups})
        slots = {slot.key: slot for slot in self.spec.slots(shown)}
        for key, value in moved.items():
            self.assertEqual(slots[key].get(), slots[key].converted(value) if slots[key].kind != "decimal_text"
                             else float(slots[key].converted(value)), key)
        # Everything else on the answer is today's.
        for key, slot in slots.items():
            if key not in moved:
                self.assertTrue(pages._same(slot.get(), {s.key: s for s in self.spec.slots(today)}[key].get(),
                                            pages.register.figure(key[0]).unit), key)
        self.assertEqual(self.calls, [None])

    def test_on_with_matching_figures_adds_only_the_marker(self):
        today = self.make(None)
        shown = self.serve(self.on_setting, FakeView(certified_rows(self.ENDPOINT, today)))
        self.assertIn("certified", shown)
        self.assertEqual({key: value for key, value in shown.items() if key != "certified"}, today)

    def test_on_when_held_back_shows_the_last_published_with_a_note(self):
        earlier = self.make(EARLIER)
        view = FakeView(certified_rows(self.ENDPOINT, earlier, as_of=EARLIER))
        with self.assertLogs(LOGGER, level="INFO") as captured:
            shown = self.serve(self.on_setting, view)
        self.assertEqual(self.calls, [None, EARLIER])
        block = shown["certified"]
        self.assertEqual(block["as_of"], EARLIER)
        self.assertEqual(block["note"], "Not yet updated for 21 Oct 2026")
        self.assertEqual(block["freshness"]["status"], "behind")
        self.assertEqual(block["freshness"]["business_days_behind"], 1)
        self.assertEqual(self.spec.effective(shown), self.spec.effective(earlier))
        self.assertTrue(all(line["status"] == "behind" for line in _log_lines(captured)))

    def test_on_falls_back_to_today_when_the_read_fails(self):
        def failing(tenant_id):
            raise TimeoutError("statement timeout")

        today = self.make(None)
        with patch.object(pages, "_connect", failing), self.assertLogs(LOGGER, level="INFO") as captured:
            shown = pages.serve(self.ENDPOINT, tenant_id=TENANT, subject_id=SUBJECT, requested_as_of=None,
                                build=self.build, environ={SETTING: self.on_setting})
        self.assertEqual(shown, today)
        self.assertTrue(all(line["status"] == "error" and line["error"] == "TimeoutError"
                            for line in _log_lines(captured)))

    def test_on_without_certified_figures_shows_today(self):
        today = self.make(None)
        with self.assertLogs(LOGGER, level="INFO") as captured:
            self.assertEqual(self.serve(self.on_setting, FakeView()), today)
        self.assertTrue(all(line["status"] == "none" for line in _log_lines(captured)))


def _page_case(endpoint: str) -> type:
    name = "".join(part.title() for part in endpoint.split("-")) + "PageTests"
    return type(name, (_PageCase, unittest.TestCase), {"ENDPOINT": endpoint})


for _endpoint in ANSWERS:
    globals()[_page_case(_endpoint).__name__] = _page_case(_endpoint)
del _endpoint


# --- The setting, the register and the log --------------------------------------------------


class SettingTests(unittest.TestCase):
    def test_unset_or_empty_reads_nothing(self):
        for environ in ({}, {SETTING: ""}, {SETTING: " , "}):
            self.assertEqual(pages._active_groups(pages.ENDPOINTS["summary"], environ), [])

    def test_modes_per_page(self):
        modes = pages.page_modes({SETTING: "overview=on, Holdings=SHADOW,cash=maybe,unknown=on"})
        self.assertEqual(modes["overview"], "on")
        self.assertEqual(modes["holdings"], "shadow")
        self.assertEqual(modes["cash"], "off")
        self.assertNotIn("unknown", modes)

    def test_combined_holdings_waits_for_holdings(self):
        self.assertEqual(pages.page_modes({SETTING: "combined-holdings=on"})["combined-holdings"], "shadow")
        self.assertEqual(pages.page_modes({SETTING: "holdings=shadow,combined-holdings=on"})["combined-holdings"],
                         "shadow")
        self.assertEqual(pages.page_modes({SETTING: "holdings=on,combined-holdings=on"})["combined-holdings"], "on")

    def test_every_page_and_figure_is_read_somewhere(self):
        read = {figure_id for spec in pages.ENDPOINTS.values() for group in spec.groups
                for figure_id in group.figure_ids}
        register = set(pages.register.figures())
        self.assertLessEqual(read, register)
        # The rebased levels stay in their gold table and chart; data freshness is worked out
        # in the browser from the newest NAV date, as today.
        self.assertEqual(register - read, {"benchmark.rebased_level", "overview.data_freshness"})
        governed = {group.page for spec in pages.ENDPOINTS.values() for group in spec.groups}
        self.assertEqual(governed, set(pages.register.page_modes("")))

    def test_figures_follow_the_page_that_owns_them(self):
        for endpoint, spec in pages.ENDPOINTS.items():
            for group in spec.groups:
                for figure_id in group.figure_ids:
                    owner = pages.register.figure(figure_id).page
                    self.assertIn(owner, {group.page, pages.BORROWS.get(group.page)}, (endpoint, figure_id))


class ExtraRowsTests(unittest.TestCase):
    def shadow(self, endpoint, rows, setting):
        make = ANSWERS[endpoint][0]
        with patch.object(pages, "_connect", FakeView(rows).connect), \
                self.assertLogs(LOGGER, level="INFO") as captured:
            pages.serve(endpoint, tenant_id=TENANT, subject_id=SUBJECT, requested_as_of=None, build=make,
                        environ={SETTING: setting})
        return _log_lines(captured)[0]

    def test_a_certified_holding_the_page_does_not_have_is_counted(self):
        rows = certified_rows("holdings", _holdings(None))
        rows.append({**next(row for row in rows if row["figure_id"] == "holdings.quantity"), "item": "4004"})
        line = self.shadow("holdings", rows, "holdings=shadow")
        self.assertEqual((line["status"], line["extra"]), ("differ", {"holdings.quantity": 1}))

    def test_contributors_beyond_the_top_and_bottom_five_are_not_extra(self):
        rows = certified_rows("risk-details", _risk_details(None))
        rows.append({**next(row for row in rows if row["figure_id"] == "risk.etl_contribution"), "item": "4004"})
        line = self.shadow("risk-details", rows, "risk-snapshot=shadow")
        self.assertEqual((line["status"], line["extra"]), ("match", {}))


class ReaderTests(unittest.TestCase):
    def test_reads_values_by_unit_and_marks_unavailable(self):
        view = FakeView([
            {"subject": SUBJECT, "figure_id": "overview.nav", "period": "", "item": "", "benchmark_id": "",
             "as_of_date": LATEST, "unit": "money", "value": 12.5, "value_text": None, "status": "ok",
             "build_id": "b2", "published_at": datetime(2026, 10, 22, 10, tzinfo=timezone.utc)},
            {"subject": SUBJECT, "figure_id": "overview.as_of", "period": "", "item": "", "benchmark_id": "",
             "as_of_date": LATEST, "unit": "date", "value": None, "value_text": LATEST, "status": "ok",
             "build_id": "b1", "published_at": datetime(2026, 10, 22, 9, tzinfo=timezone.utc)},
            {"subject": SUBJECT, "figure_id": "overview.twr", "period": "ytd", "item": "", "benchmark_id": "",
             "as_of_date": LATEST, "unit": "ratio", "value": 0.1, "value_text": None, "status": "unavailable",
             "build_id": "b1", "published_at": datetime(2026, 10, 22, 9, tzinfo=timezone.utc)},
        ])
        found = pages.read_certified(view, tenant_id=TENANT, subject_id=SUBJECT,
                                     figure_ids=("overview.nav", "overview.as_of", "overview.twr"))
        self.assertEqual(found.as_of, LATEST)
        self.assertEqual(found.values[("overview.nav", "", "", "")], 12.5)
        self.assertEqual(found.values[("overview.as_of", "", "", "")], LATEST)
        self.assertIsNone(found.values[("overview.twr", "ytd", "", "")])
        self.assertEqual((found.build_id, found.published_at), ("b2", "2026-10-22T10:00:00+00:00"))
        self.assertIsNone(pages.read_certified(FakeView(), tenant_id=TENANT, subject_id=SUBJECT,
                                               figure_ids=("overview.nav",)))

    def test_the_query_reads_the_view_only(self):
        self.assertIn("FROM gold.v_certified_figures", pages.READ_SQL)
        self.assertNotIn("gold.certified_figures ", pages.READ_SQL)
        self.assertNotIn("certified_builds", pages.READ_SQL)

    def test_the_read_has_a_short_limit(self):
        self.assertLessEqual(pages.READ_TIMEOUT_MS, 500)
        self.assertLessEqual(pages.CONNECT_TIMEOUT_S, 2)

    def test_money_matches_to_the_cent_and_ratios_to_one_part_in_a_billion(self):
        self.assertTrue(pages._same(100.004, 100.0, "money"))
        self.assertFalse(pages._same(100.006, 100.0, "money"))
        self.assertTrue(pages._same(0.1234567891, 0.12345678910001, "ratio"))
        self.assertFalse(pages._same(0.1234, 0.1235, "ratio"))
        self.assertTrue(pages._same(None, None, "ratio"))
        self.assertFalse(pages._same(None, 0.0, "ratio"))
        self.assertTrue(pages._same("2026-10-21", "2026-10-21", "date"))

    def test_decimal_text_keeps_todays_format(self):
        self.assertEqual(pages._decimal_text(1250000.25), "1250000.25")
        self.assertEqual(pages._decimal_text(100.0), "100")
        self.assertEqual(pages._decimal_text(1e-7), "0.0000001")


# --- Through the website's routes ----------------------------------------------------------


@contextmanager
def _no_database():
    yield object()


class RouteTests(unittest.TestCase):
    """Each route builds its answer through _certified; unset, that is today's builder, once."""

    ROUTES = (
        ("summary", "_portfolio_summary", lambda: web.portfolio_summary(SUBJECT, as_of=LATEST, user=VIEWER),
         (SUBJECT, ACCESS, LATEST, VIEWER)),
        ("performance-periods", "_performance_periods",
         lambda: web.performance_periods_view(SUBJECT, as_of=LATEST, start=LATEST, end=LATEST, user=VIEWER),
         (SUBJECT, ACCESS, LATEST, LATEST, LATEST, VIEWER)),
        ("analytics", "_portfolio_analytics", lambda: web.portfolio_analytics_view(SUBJECT, as_of=LATEST, user=VIEWER),
         (SUBJECT, ACCESS, LATEST)),
        ("holdings", "_holdings", lambda: web.holdings(SUBJECT, as_of=LATEST, user=VIEWER), (SUBJECT, ACCESS, LATEST)),
        ("sectors", "_sector_allocation", lambda: web.sector_allocation(SUBJECT, "request", as_of=LATEST, user=VIEWER),
         (SUBJECT, "request", LATEST, VIEWER)),
        ("cash", "_cash_activity", lambda: web.cash_activity(SUBJECT, from_date="2026-01-01", to_date=LATEST, user=VIEWER),
         (SUBJECT, ACCESS, LATEST, "2026-01-01", VIEWER)),
        ("benchmark-comparison", "_benchmark_comparison",
         lambda: web.benchmark_comparison(SUBJECT, benchmark_id="GSPC", as_of=LATEST, user=VIEWER),
         (SUBJECT, "GSPC", LATEST, VIEWER)),
        ("risk", "_risk_analytics", lambda: web.risk_analytics(SUBJECT, as_of=LATEST, response=None, user=VIEWER),
         (SUBJECT, LATEST, None, VIEWER)),
        ("risk-details", "_risk_analytics_details",
         lambda: web.risk_analytics_details(SUBJECT, as_of=LATEST, response=None, user=VIEWER),
         (SUBJECT, LATEST, None)),
    )

    def setUp(self):
        for patcher in (patch.object(web, "_portfolio_access_context", return_value=ACCESS),
                        patch.object(web, "_database", _no_database),
                        patch.object(pages, "_connect", _forbidden_connect)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_unset_calls_todays_builder_once_and_returns_its_answer(self):
        for endpoint, builder, call, expected in self.ROUTES:
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {SETTING: ""}), \
                    patch.object(web, builder, return_value={"today": endpoint}) as built, \
                    self.assertNoLogs(LOGGER, level="DEBUG"):
                self.assertEqual(call(), {"today": endpoint})
                built.assert_called_once_with(*expected)

    def test_on_route_answers_carry_the_certified_block(self):
        for endpoint, builder, call, _ in self.ROUTES:
            make, setting = ANSWERS[endpoint]
            view = FakeView(certified_rows(endpoint, make(None)))
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {SETTING: setting}), \
                    patch.object(pages, "_connect", view.connect), \
                    patch.object(web, builder, side_effect=lambda *args: make(None)):
                answer = call()
            self.assertEqual(answer["certified"]["as_of"], LATEST)

    def test_the_access_check_runs_before_any_read(self):
        with patch.dict(os.environ, {SETTING: "overview=on"}), \
                patch.object(web, "_portfolio_access_context", side_effect=HTTPException(status_code=404)), \
                self.assertRaises(HTTPException):
            web.portfolio_summary(SUBJECT, user=VIEWER)

    def test_the_overview_day_change_follows_a_held_back_date(self):
        view = FakeView(certified_rows("performance-periods", _periods(EARLIER), as_of=EARLIER))
        calls = []

        def built(account_id, access, day, start, end, user):
            calls.append((day, start, end))
            return _periods(day)

        with patch.dict(os.environ, {SETTING: "overview=on"}), patch.object(pages, "_connect", view.connect), \
                patch.object(web, "_performance_periods", side_effect=built), self.assertLogs(LOGGER, level="INFO"):
            answer = web.performance_periods_view(SUBJECT, as_of=LATEST, start=LATEST, end=LATEST, user=VIEWER)
        self.assertEqual(calls, [(LATEST, LATEST, LATEST), (EARLIER, EARLIER, EARLIER)])
        self.assertEqual(answer["requested_as_of"], LATEST)
        self.assertEqual(answer["certified"]["note"], "Not yet updated for 21 Oct 2026")


class AccountsListRouteTests(unittest.TestCase):
    DIRECTORY = [{"subject_id": SUBJECT, "reporting_currency": "USD"},
                 {"subject_id": "U9100002", "reporting_currency": "HKD"}]

    def run_list(self, setting, view):
        def access(subject_id, user):
            if subject_id == "U9100002":
                raise HTTPException(status_code=404, detail="hidden")
            return ACCESS

        with patch.dict(os.environ, {SETTING: setting}), \
                patch.object(web, "_account_directory", return_value=self.DIRECTORY), \
                patch.object(web, "_portfolio_access_context", side_effect=access), \
                patch.object(web, "_database", _no_database), \
                patch.object(web, "_subject_nav_values", side_effect=lambda c, s, a, through: _nav(through or LATEST)), \
                patch.object(web, "_subject_external_flows", return_value={}), \
                patch.object(pages, "_connect", view.connect):
            return web.accounts_overview(user=VIEWER)["accounts"]

    def test_unset_is_todays_list(self):
        view = FakeView()
        with patch.object(pages, "_connect", _forbidden_connect):
            listed = self.run_list("", view)
        self.assertEqual(listed[0], _account_entry(None))
        self.assertEqual(listed[1], {"account_id": "U9100002", "currency": "HKD", "nav": None,
                                     "nav_date": None, "mtd": None, "ytd": None})

    def test_on_shows_certified_figures_and_never_reads_a_hidden_account(self):
        rows = certified_rows("accounts-overview", _account_entry(None))
        rows += certified_rows("accounts-overview", _account_entry(None), subject="U9100002")
        for row in rows:
            if row["figure_id"] == "overview.nav":
                row["value"] = 999.0
        view = FakeView(rows)
        listed = self.run_list("overview=on", view)
        self.assertEqual(listed[0]["nav"], 999.0)
        self.assertEqual(listed[0]["certified"]["as_of"], LATEST)
        self.assertNotIn("certified", listed[1])
        self.assertEqual(set(view.subjects), {SUBJECT})


class CombinedHoldingsRouteTests(unittest.TestCase):
    def run_combined(self, setting, view):
        def enrich(connection, subject_id, rows, *, subject_type, reporting_currency, nav_currency):
            return enrich_holdings(rows, reporting_currency=reporting_currency,
                                   fx_rows=[{"rate_date": EARLIER, "from_currency": "HKD", "to_currency": "USD",
                                             "rate_text": "0.1286"}])

        with patch.dict(os.environ, {SETTING: setting}), \
                patch.object(web, "_account_directory", return_value=[{"subject_id": SUBJECT}]), \
                patch.object(web, "_portfolio_access_context", return_value=ACCESS), \
                patch.object(web, "_database", _no_database), \
                patch.object(web, "_latest_subject_holdings", side_effect=lambda c, s, day: _holding_rows(day or LATEST)), \
                patch.object(web, "_enrich_subject_holdings", side_effect=enrich), \
                patch.object(pages, "_connect", view.connect):
            return web.combined_holdings(currency="USD", user=VIEWER)

    def test_unset_is_todays_sum(self):
        with patch.object(pages, "_connect", _forbidden_connect):
            today = self.run_combined("", FakeView())
        self.assertNotIn("certified", today)
        self.assertEqual(self.run_combined("holdings=on,combined-holdings=on", FakeView())["holdings"],
                         today["holdings"])

    def test_on_adds_up_certified_records_with_todays_sum(self):
        today = self.run_combined("", FakeView())
        rows = certified_rows("combined-holdings", _records(None))
        for row in rows:
            if row["figure_id"] == "holdings.market_value" and row["item"] == "1001":
                row["value"] = 30000.5
        shown = self.run_combined("holdings=on,combined-holdings=on", FakeView(rows))
        _same_shape(self, today, shown)
        aaa = next(item for item in shown["holdings"] if item["conid"] == "1001")
        self.assertEqual(aaa["reporting_market_value_text"], "30000.5")
        self.assertEqual(aaa["reporting_unrealized_pnl_text"], "5000.5")  # IBKR's reported P&L, unchanged
        self.assertEqual(shown["certified"]["accounts"], 1)
        self.assertNotEqual(shown["gross_holdings_text"], today["gross_holdings_text"])

    def test_on_waits_for_holdings(self):
        rows = certified_rows("combined-holdings", _records(None))
        for row in rows:
            if row["figure_id"] == "holdings.market_value":
                row["value"] += 1
        with self.assertLogs(LOGGER, level="INFO") as captured:
            shown = self.run_combined("combined-holdings=on", FakeView(rows))
        self.assertNotIn("certified", shown)
        self.assertEqual(_log_lines(captured)[0]["mode"], "shadow")


class CombinedBlockTests(unittest.TestCase):
    def test_the_stalest_account_sets_the_line(self):
        fresh = {"as_of": LATEST, "label": "Certified · as of 21 Oct 2026", "note": None, "figures": 4,
                 "freshness": {"status": "fresh", "business_days_behind": 0}, "pages": ["combined-holdings"],
                 "build_id": "b2", "published_at": "2026-10-22T10:00:00+00:00", "link": "/metrics-guide"}
        behind = {**fresh, "as_of": EARLIER, "label": "Certified · as of 20 Oct 2026",
                  "note": "Not yet updated for 21 Oct 2026", "build_id": "b1",
                  "freshness": {"status": "behind", "business_days_behind": 1},
                  "published_at": "2026-10-21T10:00:00+00:00"}
        block = pages.combined_block([fresh, behind])
        self.assertEqual((block["as_of"], block["note"], block["freshness"]["status"]),
                         (EARLIER, "Not yet updated for 21 Oct 2026", "behind"))
        self.assertEqual((block["figures"], block["accounts"], block["build_id"]), (8, 2, "b2"))


# --- The "Certified · as of" line -----------------------------------------------------------


NODE = shutil.which("node") or ("/opt/node22/bin/node" if os.path.exists("/opt/node22/bin/node") else None)
LINE_CHECK = r"""
const make=(cls)=>{const el={className:cls||'',children:[],style:{},innerHTML:'',parent:null,
  appendChild(child){child.parent=el;el.children.push(child);return child},
  remove(){const list=el.parent.children;list.splice(list.indexOf(el),1)},
  closest(sel){let node=el;while(node){if(sel==='.card'&&node.className.split(' ').includes('card'))return node;node=node.parent}return null},
  querySelector(sel){return el.children.find(c=>c.className.split(' ').includes('certified-line'))||null}};return el};
globalThis.document={createElement:()=>make('')};
__SCRIPT__
const card=make('card'),target=card.appendChild(make('metrics'));
certifiedLine(target,{label:'Certified · as of 21 Oct 2026',note:'Not yet updated for 22 Oct 2026',link:'/metrics-guide'});
const first=card.children.length===2?card.children[1].innerHTML:'';
certifiedLine(target,{label:'Certified · as of 21 Oct 2026',note:null,link:'/metrics-guide'});
const second=card.children.length===2?card.children[1].innerHTML:'';
certifiedLine(target,null);
console.log(JSON.stringify({first,second,after:card.children.length,
  pick:[certifiedFor({pages:['performance']},'performance')!==null,certifiedFor({pages:['performance']},'risk-statistics')===null,certifiedFor(undefined,'x')===null]}));
"""


@unittest.skipUnless(NODE, "node is not installed")
class CertifiedLineScriptTests(unittest.TestCase):
    def test_line_shows_the_date_the_note_and_the_guide_link_and_clears(self):
        with tempfile.TemporaryDirectory() as folder:
            script = os.path.join(folder, "line.js")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(LINE_CHECK.replace("__SCRIPT__", CERTIFIED_LINE_SCRIPT))
            result = json.loads(subprocess.run([NODE, script], capture_output=True, text=True, check=True).stdout)
        self.assertIn("Certified · as of 21 Oct 2026", result["first"])
        self.assertIn("<strong>Not yet updated for 22 Oct 2026</strong>", result["first"])
        self.assertIn('href="/metrics-guide"', result["first"])
        self.assertNotIn("Not yet updated", result["second"])
        self.assertEqual(result["after"], 1)
        self.assertEqual(result["pick"], [True, True, True])

    def test_every_page_script_draws_the_line(self):
        owner = {"email": "owner@example.com", "role": "owner"}
        account = web._render_account_page(owner, account_id=web.PILOT_ACCOUNT_ID, section="overview")
        for marker in ("certifiedLine(document.querySelector('#overview-asof'),s.certified)",
                       "certifiedLine(document.querySelector('#holdings'),h.certified)",
                       "certifiedLine(document.querySelector('#cash-balances'),result.certified)",
                       "certifiedLine(document.querySelector('#period-returns'),r.certified)",
                       "certifiedLine(document.querySelector('#benchmark-metrics'),bc.certified)",
                       "certifiedLine(document.querySelector('#risk-metrics'),risk.certified)",
                       "certifiedFor(a.certified,'performance')", "certifiedFor(a.certified,'risk-statistics')",
                       "certifiedLine(bars,s.certified)", "certifiedLine(metrics,c&&c.available?h.certified:null)"):
            self.assertIn(marker, account)
        self.assertIn("certifiedLine(document.querySelector('#combined-holdings'),data.certified)",
                      web._render_platform_page(owner, section="combined-holdings"))
        self.assertIn("a.certified.note||'Certified'", web.home(None, owner))


if __name__ == "__main__":
    unittest.main()
