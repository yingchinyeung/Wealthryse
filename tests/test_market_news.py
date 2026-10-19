"""The market news watch and the link from numbers that don't add up to market news.

No database, network or model: feeds, articles and the model are fakes, and the market news store is
in memory. The case it must catch is KRX's continuous after-market trading until 20:00 Korea time
from 14 Sep 2026, which moved a Korean account's per-stock prices away from the official closes
that day. All account figures are synthetic.
"""
from __future__ import annotations

import copy
import json
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from ibkr_etl import market_calendar  # noqa: E402
from wealthryse_agent import operations, tools  # noqa: E402
from wealthryse_agent.config import ModelSettings  # noqa: E402
from wealthryse_agent.month_team.agents import TeamModel  # noqa: E402
from wealthryse_agent.onboarding_reconcile import Reconciler, reconciliation_days  # noqa: E402
from wealthryse_agent.supervisor import market_news, markets, news_link, registry, routing  # noqa: E402
from wealthryse_agent.tools import ToolError  # noqa: E402

APAC = ModelSettings(endpoint="https://wr-agents-sea.services.ai.azure.com", deployment="wr-reports-apac",
                     router_deployment="", region="southeastasia", processing_scope="apac-data-zone",
                     managed_identity_client_id="x")
HANKYUNG = "https://www.hankyung.com/feed/finance"
ARTICLE_URL = "https://www.hankyung.com/article/202609043639i"
# A synthetic article in the style of the Korean financial press, announcing the change.
ARTICLE = ("<html><body><script>var x = 1;</script><h1>한국거래소, 애프터마켓 연속거래 도입</h1>"
           "<p>한국거래소는 9월 14일부터 정규장 종료 후 오후 8시(20:00)까지 애프터마켓 연속거래를 시작한다고 밝혔다. "
           "넥스트레이드와의 경쟁에 대응하기 위한 조치다.</p></body></html>")
QUOTE = "한국거래소는 9월 14일부터 정규장 종료 후 오후 8시(20:00)까지 애프터마켓 연속거래를 시작한다고 밝혔다."
RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>한국경제 증권</title>
<item><title>한국거래소, 9월 14일부터 애프터마켓 연속거래 도입</title><link>{ARTICLE_URL}</link>
<description>거래시간이 오후 8시까지 늘어난다</description><pubDate>Fri, 04 Sep 2026 09:00:00 +0900</pubDate></item>
<item><title>반도체주 강세에 코스피 상승</title><link>https://www.hankyung.com/article/1</link>
<description>외국인 순매수</description><pubDate>Fri, 04 Sep 2026 15:40:00 +0900</pubDate></item>
</channel></rss>""".encode()
EVENT_ANSWER = {"events": [{"market": "XKRX", "kind": "trading_hours", "effective_date": "2026-09-14",
                            "end_date": "", "summary": "KRX starts continuous after-market trading until 20:00 "
                            "Korea time after the regular session.", "quote": QUOTE}]}


class Feeds:
    """The news sites: the Hankyung feed and its article; every other feed is empty."""

    def __init__(self, pages=None):
        self.pages = {HANKYUNG: RSS, ARTICLE_URL: ARTICLE.encode()} if pages is None else pages
        self.read = []

    def __call__(self, url):
        self.read.append(url)
        if url in self.pages:
            return self.pages[url]
        return b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'


class Responses:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def create(self, **request):
        self.calls.append(request)
        answer = self.answers.pop(0) if self.answers else {"events": []}
        return SimpleNamespace(output_text=json.dumps(answer), status="completed",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=10))


def team_model(*answers):
    responses = Responses(*answers)
    return TeamModel(APAC, tokens_left=1_000_000, client=SimpleNamespace(responses=responses)), responses


class MemoryNews:
    """MarketNewsStore in memory."""

    def __init__(self):
        self.items, self.events, self.asked, self.open = {}, [], [], []

    def ready(self):
        return True

    def seen(self, digests):
        return set(digests) & set(self.items)

    def add_item(self, item, status):
        if item.digest in self.items:
            return None
        self.items[item.digest] = (item, status)
        return len(self.items)

    def add_event(self, item_id, event):
        key = (event["market"], event["kind"], event["effective_date"], event["source_url"])
        if any((e["market"], e["kind"], e["effective_date"], e["source_url"]) == key for e in self.events):
            return None
        self.events.append({**event, "event_id": f"e{len(self.events) + 1}"})
        return self.events[-1]["event_id"]

    def requests(self, limit=10):
        return list(self.open)

    def searched(self, request_id, items):
        self.open = [r for r in self.open if r["request_id"] != request_id]

    def ask(self, market, start, end, ref):
        self.asked.append((market, start, end, ref))
        return True


# A Korean account at a broker that values holdings at its own price list.
CASE_ID = "aaaaaaaa-0000-4000-8000-0000000000k1"
CASE = {"case_id": CASE_ID, "subject_id": "K0000001", "broker": "kiwoom", "broker_account_id": "K0000001",
        "display_name": "Synthetic Korean account", "reporting_currency": "KRW", "history_start_date": "2026-08-03",
        "base_currency": "KRW"}
SECURITIES = [{"conid": "005930", "first_held": "2026-08-03", "last_held": "2026-09-14", "position_currency": "KRW",
               "symbol": "005930", "description": "SYNTHETIC KR ELECTRONICS", "asset_category": "STK",
               "currency": "KRW", "listing_exchange": "KSE", "isin": None}]


def kr_nav_days(last: date):
    first = date(2026, 8, 3)
    return [d.isoformat() for d in (first + timedelta(days=n) for n in range((last - first).days + 1))
            if d.weekday() < 5]


def kr_day(on, *, broker_stock="80000000", listed_value="80000000"):
    cash = 10000000
    total = int(broker_stock) + cash
    return {"date": on,
            "nav": [{"currency": "KRW", "asset_class": "TOTAL", "total_text": str(total), "cash_text": str(cash),
                     "stock_text": broker_stock, "options_text": None, "futures_text": None, "bonds_text": None,
                     "accruals_text": "0"}],
            "positions": [{"conid": "005930", "currency": "KRW", "quantity_text": "1000", "mark_price_text": "80000",
                           "market_value_text": listed_value}],
            "cash": [{"currency": "KRW", "ending_cash_text": str(cash), "fx_rate_to_base_text": "1"}],
            "fx_rates": [], "corporate_actions": [], "activity": []}


class KoreanAccount:
    """The tool server for the synthetic Korean case, with the market events the watch stored."""

    def __init__(self, events, *, last=date(2026, 9, 14), broken=("2026-09-14",)):
        self.events, self.broken, self.last = events, set(broken), last
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "get_onboarding_broker_overview":
            return {"data": {"case": dict(CASE), "nav": [{"snapshot_date": d, "currency": "KRW", "total_text": "1"}
                                                          for d in kr_nav_days(self.last)],
                             "activity": [{"kind": "trade", "count": 3, "first": "2026-08-03", "last": "2026-09-10"}],
                             "securities": copy.deepcopy(SECURITIES), "security_identifiers": []}}
        if name == "get_onboarding_broker_day":
            on = arguments["date"]
            # From the change, the broker's price list gives a different value from the official close.
            return {"data": kr_day(on, listed_value="80788800") if on in self.broken else kr_day(on)}
        if name == "get_market_events":
            start, end = arguments["from_date"], arguments["to_date"]
            return {"data": {"events": [e for e in self.events if e["market"] == arguments.get("market", e["market"])
                                        and start <= e["effective_date"] <= end]}}
        raise AssertionError(name)


def watch_once(feeds=None, *answers):
    news = MemoryNews()
    model, responses = team_model(*(answers or (EVENT_ANSWER,)))
    result = market_news.run(news, feeds or Feeds(), lambda: model)
    return news, result, responses


class MarketListTests(unittest.TestCase):
    def test_every_calendar_and_benchmark_market_and_korea_is_followed(self):
        wanted = set(market_calendar.CALENDARS) | set(market_calendar.BENCHMARK_MARKETS.values()) | {"XKRX"}
        self.assertEqual(wanted - set(markets.BY_CODE), set())
        for market in markets.MARKETS:
            self.assertTrue(market.feeds, market.code)
            self.assertRegex(market.code, r"^X[A-Z]{3}$")

    def test_feeds_are_https_and_their_hosts_are_the_only_ones_read(self):
        hosts = markets.allowed_hosts()
        for feed, codes in markets.feeds():
            self.assertTrue(feed.url.startswith("https://"), feed.url)
            self.assertRegex(feed.source_id, r"^[a-z0-9-]{1,40}$")
            self.assertTrue(codes)
        self.assertIn("www.hankyung.com", hosts)
        self.assertNotIn("alphasquare.co.kr", hosts)

    def test_holdings_map_to_markets_by_exchange_then_currency(self):
        self.assertEqual(markets.market_for(exchange="KSE"), "XKRX")
        self.assertEqual(markets.market_for(exchange="SEHK", currency="USD"), "XHKG")
        self.assertEqual(markets.market_for(exchange="UNKNOWN", currency="KRW"), "XKRX")
        self.assertIsNone(markets.market_for(currency="CNY"))  # Shanghai or Shenzhen: not guessed
        self.assertEqual(markets.related({"XNAS"}), {"XNAS", "XNYS"})

    def test_a_dated_search_bounds_the_window(self):
        url = markets.news_search("KRX 거래시간", "ko-KR", after="2026-08-24", before="2026-09-16")
        self.assertIn("after%3A2026-08-24", url)
        self.assertIn("before%3A2026-09-16", url)
        self.assertIn("hl=ko-KR", url)

    def test_the_watch_is_a_draft_level_operations_specialist_reading_public_news(self):
        member = registry.BY_NAME["market_news"]
        self.assertEqual((member.team, member.kind, member.autonomy, member.deployment),
                         ("operations", "specialist", "draft", "reports"))
        self.assertEqual(registry.JOB_MEMBER["market_news"], "market_news")
        self.assertEqual(registry.TOOL_LAYERS["get_market_events"], "external")
        self.assertIn("get_market_events", registry.BY_NAME["holdings_performance"].tools)

    def test_news_and_market_rule_questions_go_to_the_specialist_with_the_tool(self):
        for question in ("Was there any news behind the drop on 14 Sep?", "Did the exchange trading hours change?",
                         "Was the market closed on Friday?"):
            self.assertIn("holdings_performance", routing.route_chat(question).specialists, question)


class ReadingTests(unittest.TestCase):
    def test_rss_items_are_read_and_a_doctype_is_refused(self):
        feed = markets.Feed("hankyung-finance", HANKYUNG)
        items = market_news.parse_feed(RSS, feed, ("XKRX",))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].url, ARTICLE_URL)
        self.assertEqual(items[0].published_at, datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc))
        self.assertTrue(market_news.worth_reading(items[0]))
        self.assertFalse(market_news.worth_reading(items[1]))
        bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel></channel></rss>'
        with self.assertRaises(market_news.FetchRefused):
            market_news.parse_feed(bomb, feed, ("XKRX",))

    def test_atom_entries_are_read(self):
        atom = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>HKEX trading hours '
                b'change</title><link href="https://www.hkex.com.hk/n/1"/><updated>2026-09-01T08:00:00Z</updated>'
                b'<summary>From 1 October</summary></entry></feed>')
        items = market_news.parse_feed(atom, markets.Feed("hkex-news", "https://www.hkex.com.hk/x"), ("XHKG",))
        self.assertEqual((items[0].title, items[0].url), ("HKEX trading hours change", "https://www.hkex.com.hk/n/1"))

    def test_the_fetcher_refuses_hosts_not_in_its_feeds(self):
        fetch = market_news.default_fetch(frozenset({"www.hankyung.com"}))
        with self.assertRaises(market_news.FetchRefused):
            fetch("https://alphasquare.co.kr/home/insight/posts/1")
        with self.assertRaises(market_news.FetchRefused):
            fetch("http://www.hankyung.com/feed/finance")

    def test_day_forms_do_not_match_a_longer_day(self):
        self.assertTrue(market_news.date_in_text(date(2026, 9, 14), "9월 14일부터", None))
        self.assertTrue(market_news.date_in_text(date(2026, 9, 14), "from Sept. 14", None) is False)
        self.assertTrue(market_news.date_in_text(date(2026, 9, 14), "from Sep 14, 2026", None))
        self.assertFalse(market_news.date_in_text(date(2026, 9, 1), "from Sep 14, 2026", None))
        self.assertFalse(market_news.date_in_text(date(2026, 9, 1), "9/14 start", None))
        published = datetime(2026, 9, 14, 1, tzinfo=timezone.utc)
        self.assertTrue(market_news.date_in_text(date(2026, 9, 14), "오늘부터 시행", published))
        self.assertFalse(market_news.date_in_text(date(2026, 9, 15), "오늘부터 시행", published))


class WatchTests(unittest.TestCase):
    def test_the_krx_after_market_announcement_becomes_an_event(self):
        news, result, responses = watch_once()
        self.assertEqual(len(responses.calls), 1)  # the stock-rally item never reaches the model
        request = responses.calls[0]
        self.assertEqual(request["model"], "wr-reports-apac")
        self.assertIn("오후 8시", request["input"][0]["content"][0]["text"])  # the article was read
        self.assertNotIn("var x", request["input"][0]["content"][0]["text"])
        [event] = news.events
        self.assertEqual((event["market"], event["kind"], event["effective_date"]),
                         ("XKRX", "trading_hours", "2026-09-14"))
        self.assertEqual(event["source_url"], ARTICLE_URL)
        self.assertEqual(result.counts["events"], 1)
        self.assertEqual(sorted(s for _, s in news.items.values()), ["event", "skipped"])

    def test_a_second_run_reads_nothing_new(self):
        news, _, _ = watch_once()
        model, responses = team_model(EVENT_ANSWER)
        result = market_news.run(news, Feeds(), lambda: model)
        self.assertEqual(responses.calls, [])
        self.assertEqual(result.counts["new_items"], 0)

    def test_code_drops_what_the_article_does_not_say(self):
        wrong_quote = {"events": [{**EVENT_ANSWER["events"][0], "quote": "한국거래소는 9월 14일부터 거래를 중단한다."}]}
        wrong_day = {"events": [{**EVENT_ANSWER["events"][0], "effective_date": "2026-09-15"}]}
        wrong_market = {"events": [{**EVENT_ANSWER["events"][0], "market": "XHKG"}]}
        new_number = {"events": [{**EVENT_ANSWER["events"][0], "summary": "KRX trades until 21:00."}]}
        for answer in (wrong_quote, wrong_day, wrong_market, new_number):
            news, result, _ = watch_once(None, answer)
            self.assertEqual(news.events, [], answer)
            self.assertEqual(result.counts["events_not_confirmed"], 1)

    def test_an_unexplained_window_is_searched_in_the_markets_own_language(self):
        news = MemoryNews()
        news.open = [{"request_id": 1, "market": "XKRX", "window_start": "2026-09-01", "window_end": "2026-09-14"}]
        feeds = Feeds()
        model, _ = team_model()
        result = market_news.run(news, feeds, lambda: model)
        searches = [u for u in feeds.read if "after%3A2026-08-11" in u and "before%3A2026-09-16" in u]
        self.assertEqual(len(searches), 1)
        self.assertIn("hl=ko-KR", searches[0])
        self.assertEqual(news.open, [])
        self.assertEqual(result.counts["searches"], 1)

    def test_an_unread_feed_is_recorded_and_the_others_carry_on(self):
        class Flaky(Feeds):
            def __call__(self, url):
                if "yna.co.kr" in url:
                    raise OSError("down")
                return super().__call__(url)

        news, result, _ = watch_once(Flaky())
        self.assertIn("yonhap-market", result.unread)
        self.assertEqual(len(news.events), 1)

    def test_the_owner_card_and_the_run_record(self):
        news, result, _ = watch_once()
        key, title, body = market_news.card(result.events[0])
        self.assertEqual(key, "XKRX:trading_hours:2026-09-14")
        self.assertEqual(title, "Korea Exchange: trading hours from 14 Sep 2026")
        self.assertIn(QUOTE, body)
        self.assertIn("www.hankyung.com", body)
        rows = market_news.tree(result, mode_="watch", correlation_id="c1", status="done")
        self.assertEqual([r["role"] for r in rows], ["supervisor", "event_dispatcher", "market_news"])
        self.assertEqual(rows[0]["detail"]["markets"], ["XKRX"])
        self.assertEqual(rows[0]["detail"]["layer"], "external")
        self.assertNotIn(QUOTE, json.dumps(rows))  # the record keeps counts and names, not text

    def test_mode_is_off_unless_named(self):
        self.assertEqual(market_news.mode({}), "off")
        self.assertEqual(market_news.mode({"AGENT_MARKET_NEWS": "LIVE"}), "live")
        self.assertEqual(market_news.mode({"AGENT_MARKET_NEWS": "on"}), "off")


def stored_event(**changes):
    news, _, _ = watch_once()
    return {**news.events[0], **changes}


class LinkTests(unittest.TestCase):
    def test_the_kiwoom_gap_links_to_the_krx_change_the_same_day(self):
        issue = news_link.Issue("valuation_gap", frozenset({"XKRX"}), date(2026, 9, 14), date(2026, 9, 11))
        [link] = news_link.link(issue, [stored_event()])
        self.assertEqual((link.strength, link.days_before), ("same_day", 0))
        self.assertEqual(link.as_dict()["wording"],
                         "This starts on 14 Sep 2026, the day of a Korea Exchange change: KRX starts continuous "
                         "after-market trading until 20:00 Korea time after the regular session (source: "
                         "www.hankyung.com).")

    def test_other_markets_kinds_and_days_are_not_linked(self):
        event = stored_event()
        hk = news_link.Issue("valuation_gap", frozenset({"XHKG"}), date(2026, 9, 14), date(2026, 9, 11))
        cost = news_link.Issue("cost_change", frozenset({"XKRX"}), date(2026, 9, 14), date(2026, 9, 11))
        before = news_link.Issue("valuation_gap", frozenset({"XKRX"}), date(2026, 9, 10), date(2026, 9, 9))
        long_after = news_link.Issue("valuation_gap", frozenset({"XKRX"}), date(2026, 10, 15), date(2026, 10, 14))
        for issue in (hk, cost, before, long_after):
            self.assertEqual(news_link.link(issue, [event]), [], issue)

    def test_a_wide_window_links_and_says_when_it_began(self):
        issue = news_link.Issue("valuation_gap", frozenset({"XKRX"}), date(2026, 9, 30), date(2026, 8, 31))
        [link] = news_link.link(issue, [stored_event()])
        self.assertEqual((link.strength, link.days_before), ("in_window", 16))
        self.assertTrue(link.as_dict()["wording"].startswith("It began between 1 Sep 2026 and 30 Sep 2026; on 14 Sep "
                                                             "2026, Korea Exchange: KRX starts"))

    def test_a_closure_explains_a_missing_price_on_the_days_it_covers(self):
        closure = {"market": "XTAI", "kind": "closure", "effective_date": "2026-07-10", "end_date": None,
                   "summary": "Typhoon Bavi: centralized market closed", "source_url": "https://x.example/1",
                   "source_id": "tw-search"}
        hit = news_link.Issue("missing_price", frozenset({"XTAI"}), date(2026, 7, 10), date(2026, 7, 9))
        miss = news_link.Issue("missing_price", frozenset({"XTAI"}), date(2026, 7, 14), date(2026, 7, 13))
        self.assertEqual(news_link.link(hit, [closure])[0].strength, "covers_day")
        self.assertEqual(news_link.link(miss, [closure]), [])

    def test_shared_news_counts_for_new_york_and_nasdaq(self):
        event = {"market": "XNYS", "kind": "closure", "effective_date": "2025-01-09", "end_date": None,
                 "summary": "National Day of Mourning", "source_url": "https://x.example/2", "source_id": "us-search"}
        issue = news_link.Issue("missing_price", frozenset({"XNAS"}), date(2025, 1, 9), date(2025, 1, 8))
        self.assertEqual(len(news_link.link(issue, [event])), 1)


class ReconcileTests(unittest.TestCase):
    """The onboarding NAV check, end to end: news watch, then the reconciliation agent."""

    def reconcile(self, account, unexplained=None):
        model, responses = team_model({"cause": "not_found", "explanation": "Nothing explains it."})
        reconciler = Reconciler(lambda: model, unexplained=unexplained)
        overview, days = reconciler.gather(account.call, CASE_ID)
        return reconciler.reconcile(overview, days, today=date(2026, 9, 15)), responses

    def test_the_14_september_break_is_explained_by_the_krx_change(self):
        account = KoreanAccount([stored_event()])
        self.assertEqual(reconciliation_days(account.call("get_onboarding_broker_overview", {})["data"],
                                             Reconciler(lambda: None).settings), ["2026-08-31", "2026-09-14"])
        result, responses = self.reconcile(account)
        nav = result.checks["nav_reconciled"]
        [broken] = [d for d in nav["evidence"]["days"] if d.get("cause")]
        self.assertEqual((broken["date"], broken["side"], broken["cause"]), ("2026-09-14", "positions", "market_event"))
        self.assertTrue(broken["explanation"].startswith("This starts on 14 Sep 2026, the day of a Korea Exchange "
                                                         "change: KRX starts continuous after-market trading"))
        self.assertEqual(broken["market_events"][0]["source_url"], ARTICLE_URL)
        self.assertIn("This starts on 14 Sep 2026", nav["message"])
        # The model saw the event among the facts it may word from.
        facts = json.loads(responses.calls[0]["input"][0]["content"][0]["text"])
        self.assertEqual(facts["market_events"][0]["effective_date"], "2026-09-14")
        asked = [c for c in account.calls if c[0] == "get_market_events"]
        self.assertTrue(asked and all(a["market"] == "XKRX" for _, a in asked))

    def test_a_break_with_no_event_asks_for_a_search_of_its_window(self):
        news = MemoryNews()
        account = KoreanAccount([], last=date(2026, 8, 31), broken=("2026-08-31",))
        result, _ = self.reconcile(account, lambda issue, links: market_news.ask_for_unexplained(news, issue, links))
        [broken] = [d for d in result.checks["nav_reconciled"]["evidence"]["days"] if d.get("cause")]
        self.assertEqual(broken["cause"], "not_found")
        self.assertEqual(news.asked, [("XKRX", date(2026, 8, 31), date(2026, 8, 31), f"onboarding:{CASE_ID}")])

    def test_a_new_event_changes_the_data_digest_so_the_case_is_checked_again(self):
        from wealthryse_agent.onboarding_reconcile import data_digest

        reconciler = Reconciler(lambda: None)
        without = data_digest(*reconciler.gather(KoreanAccount([]).call, CASE_ID), date(2026, 9, 15))
        with_event = data_digest(*reconciler.gather(KoreanAccount([stored_event()]).call, CASE_ID), date(2026, 9, 15))
        self.assertNotEqual(without, with_event)


class OperationsTests(unittest.TestCase):
    def test_an_incident_sees_market_changes_from_ten_days_before_it_began(self):
        calls = []

        def call(name, arguments):
            calls.append((name, arguments))
            if name == "get_market_events":
                return {"data": {"events": [stored_event()]}}
            return {"data": {"ingestion_runs": [], "admin_runs": [], "admin_actions": []}}

        incident = {"incident_id": 7, "incident_key": "silver-quality", "first_seen_at": "2026-09-15T01:00:00+00:00",
                    "last_seen_at": "2026-09-16T01:00:00+00:00"}
        sources = operations.platform_sources(call, incident)
        self.assertEqual(sources[-1].source_id, "market:events")
        self.assertEqual(calls[-1], ("get_market_events", {"from_date": "2026-09-05", "to_date": "2026-09-16"}))

    def test_with_the_tool_off_the_incident_sources_are_as_before(self):
        def call(name, arguments):
            if name == "get_market_events":
                raise RuntimeError("unknown_tool")
            return {"data": {"ingestion_runs": [], "admin_runs": [], "admin_actions": []}}

        sources = operations.platform_sources(call, {"first_seen_at": "2026-09-15T01:00:00+00:00"})
        self.assertEqual([s.source_id for s in sources],
                         ["platform:incident", "platform:imports", "platform:admin_runs", "platform:admin_actions"])


class ToolTests(unittest.TestCase):
    class Connection:
        def __init__(self):
            self.queries = []

        def execute(self, sql, params):
            self.queries.append((sql, params))
            return SimpleNamespace(fetchall=lambda: [{
                "event_id": "e1", "market": "XKRX", "kind": "trading_hours", "effective_date": date(2026, 9, 14),
                "end_date": None, "summary": "s", "quote": "q", "source_url": ARTICLE_URL,
                "source_id": "hankyung-finance", "official": False, "published_at": None}])

    def test_the_tool_is_off_unless_switched_on(self):
        spec = tools.REGISTRY["get_market_events"]
        self.assertFalse(spec.needs_account)
        self.assertFalse(spec.only_jobs)
        self.assertFalse(tools.market_events_enabled({}))
        self.assertTrue(tools.market_events_enabled({"AGENT_MARKET_EVENTS": "on"}))

    def test_it_reads_one_market_over_a_bounded_window(self):
        connection = self.Connection()
        result = tools.get_market_events(connection, {"market": "xkrx", "from_date": "2026-09-01",
                                                      "to_date": "2026-09-30"})
        self.assertEqual(result["data"]["events"][0]["market"], "XKRX")
        self.assertEqual(connection.queries[0][1], ("2026-09-01", "2026-09-30", "XKRX", "XKRX"))
        self.assertEqual(result["citations"][0]["url"], ARTICLE_URL)
        for bad in ({"from_date": "2026-01-01", "to_date": "2026-09-30"}, {"from_date": "2026-09-30",
                                                                           "to_date": "2026-09-01"},
                    {"market": "KRX'--"}):
            with self.assertRaises(ToolError):
                tools.get_market_events(connection, bad)


if __name__ == "__main__":
    unittest.main()
