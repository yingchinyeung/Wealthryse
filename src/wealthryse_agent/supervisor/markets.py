"""The stock markets the market news watch follows, and where it reads their news.

The list starts with the markets the platform holds or compares against: the United States, Hong
Kong, mainland China, Taiwan (IBKR accounts and the benchmarks in ``ibkr_etl.market_calendar``) and
Korea (Kiwoom, KOSPI 200). Adding a market is one ``Market`` entry; tests fail if a calendar or a
benchmark market has none.

Every source is public news, never client data. Feed addresses have not been fetched from a
build sandbox; ``python -m wealthryse_agent.supervisor.market_news --probe`` reads each one once
and prints only its status and item count, so they are confirmed in staging before the job runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus, urlsplit


@dataclass(frozen=True)
class Feed:
    source_id: str  # short id, kept with every item read from it
    url: str
    kind: str = "rss"  # rss or atom; both are parsed by the same reader
    official: bool = False  # the exchange or regulator itself


@dataclass(frozen=True)
class Market:
    code: str  # ISO 10383 MIC, as in ibkr_etl.market_calendar
    name: str
    timezone: str
    currencies: tuple[str, ...]
    # IBKR listing_exchange codes (and Kiwoom's own) that trade on this market.
    exchanges: tuple[str, ...]
    feeds: tuple[Feed, ...]
    # Words for a dated search, in the market's own language as well as English. A search for a
    # date window (an unexplained number) uses these with the window's dates.
    search_terms: str
    search_language: str  # hl/gl for the news search, e.g. ko-KR
    # Other names the market goes by in news text, for the model's market check.
    aliases: tuple[str, ...] = ()


def news_search(terms: str, language: str, *, after: str | None = None, before: str | None = None) -> str:
    """A Google News RSS search URL. ``after`` and ``before`` (ISO dates) bound a dated search."""
    query = terms + (f" after:{after}" if after else "") + (f" before:{before}" if before else "")
    lang, _, country = language.partition("-")
    return (f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={language}"
            f"&gl={country or lang.upper()}&ceid={country or lang.upper()}:{lang}")


_US_TERMS = "(NYSE OR Nasdaq) (trading hours OR closure OR halt OR settlement OR closing auction OR rule change)"
_HK_TERMS = "HKEX (trading hours OR closure OR typhoon OR stamp duty OR settlement OR closing auction)"
_CN_TERMS = "(上交所 OR 深交所 OR 上海证券交易所 OR 深圳证券交易所) (交易时间 OR 休市 OR 印花税 OR 涨跌幅 OR 收盘)"
_TW_TERMS = "(證交所 OR 臺灣證券交易所 OR TWSE) (交易時間 OR 休市 OR 證交稅 OR 漲跌幅 OR 盤後)"
_KR_TERMS = "(한국거래소 OR KRX OR 넥스트레이드) (거래시간 OR 애프터마켓 OR 휴장 OR 거래세 OR 가격제한폭 OR 종가)"

MARKETS: tuple[Market, ...] = (
    Market("XNYS", "New York Stock Exchange", "America/New_York", ("USD",),
           ("NYSE", "ARCA", "AMEX", "NYSEARCA", "NYSEAMEX", "BATS", "IEX"),
           (Feed("sec-press", "https://www.sec.gov/news/pressreleases.rss", official=True),
            Feed("nasdaqtrader-headlines",
                 "https://www.nasdaqtrader.com/rss.aspx?feed=currentheadlines&categorylist=2,6,7", official=True),
            Feed("us-search", news_search(_US_TERMS, "en-US"))),
           _US_TERMS, "en-US", ("NYSE", "New York Stock Exchange", "Wall Street", "US stocks")),
    Market("XNAS", "Nasdaq", "America/New_York", ("USD",), ("NASDAQ", "ISLAND", "NMS"),
           (Feed("nasdaqtrader-headlines",
                 "https://www.nasdaqtrader.com/rss.aspx?feed=currentheadlines&categorylist=2,6,7", official=True),),
           _US_TERMS, "en-US", ("Nasdaq", "Wall Street", "US stocks")),
    Market("XHKG", "Hong Kong Exchanges", "Asia/Hong_Kong", ("HKD",), ("SEHK", "HKFE"),
           (Feed("hkex-news", "https://www.hkex.com.hk/Services/RSS-Feeds/News-Releases?sc_lang=en", official=True),
            Feed("hk-search", news_search(_HK_TERMS, "en-HK"))),
           _HK_TERMS, "en-HK", ("HKEX", "Hong Kong stock exchange", "Hang Seng")),
    Market("XSHG", "Shanghai Stock Exchange", "Asia/Shanghai", ("CNY", "CNH"), ("SEHKNTL", "SSE"),
           (Feed("cn-search", news_search(_CN_TERMS, "zh-CN")),),
           _CN_TERMS, "zh-CN", ("SSE", "上交所", "上海证券交易所", "A-shares", "A股")),
    Market("XSHE", "Shenzhen Stock Exchange", "Asia/Shanghai", ("CNY", "CNH"), ("SEHKSZSE", "SZSE"),
           (Feed("cn-search", news_search(_CN_TERMS, "zh-CN")),),
           _CN_TERMS, "zh-CN", ("SZSE", "深交所", "深圳证券交易所", "A-shares", "A股")),
    Market("XTAI", "Taiwan Stock Exchange", "Asia/Taipei", ("TWD",), ("TWSE", "TSE.TW"),
           (Feed("tw-search", news_search(_TW_TERMS, "zh-TW")),),
           _TW_TERMS, "zh-TW", ("TWSE", "證交所", "臺灣證券交易所", "台股", "TAIEX")),
    Market("XKRX", "Korea Exchange", "Asia/Seoul", ("KRW",), ("KSE", "KOSDAQ", "KRX", "NXT", "KIWOOM"),
           (Feed("yonhap-market", "https://en.yna.co.kr/RSS/market.xml"),
            Feed("hankyung-finance", "https://www.hankyung.com/feed/finance"),
            Feed("kr-search", news_search(_KR_TERMS, "ko-KR"))),
           _KR_TERMS, "ko-KR", ("KRX", "Korea Exchange", "한국거래소", "Nextrade", "넥스트레이드", "KOSPI", "코스피",
                                 "KOSDAQ", "코스닥")),
)
BY_CODE = {market.code: market for market in MARKETS}
_BY_EXCHANGE = {exchange.upper(): market.code for market in MARKETS for exchange in market.exchanges}
# A currency names one market only where the platform holds just one there.
_BY_CURRENCY = {"HKD": "XHKG", "TWD": "XTAI", "KRW": "XKRX", "USD": "XNYS"}


def feeds() -> list[tuple[Feed, tuple[str, ...]]]:
    """Each feed once, with every market it serves."""
    served: dict[str, tuple[Feed, list[str]]] = {}
    for market in MARKETS:
        for feed in market.feeds:
            served.setdefault(feed.url, (feed, []))[1].append(market.code)
    return [(feed, tuple(codes)) for feed, codes in served.values()]


def allowed_hosts() -> frozenset[str]:
    """The only hosts the watch reads from: its feeds' hosts. Article pages are read from these too."""
    return frozenset(urlsplit(feed.url).hostname or "" for feed, _ in feeds())


def market_for(*, exchange: str | None = None, currency: str | None = None) -> str | None:
    """The market a holding trades on: its listing exchange first, else its currency where that is unambiguous."""
    if exchange and (code := _BY_EXCHANGE.get(str(exchange).upper())):
        return code
    if currency:
        return _BY_CURRENCY.get(str(currency).upper())
    return None


def related(codes: set[str] | frozenset[str]) -> set[str]:
    """Markets whose news is shared: New York and Nasdaq, Shanghai and Shenzhen."""
    out = set(codes)
    for pair in (("XNYS", "XNAS"), ("XSHG", "XSHE")):
        if out & set(pair):
            out |= set(pair)
    return out
