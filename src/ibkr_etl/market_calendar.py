"""Versioned exchange-session calendars for observation-date monitoring.

Exchange closures are facts; ingestion deadlines are a separate operating
policy. This module does not dispatch jobs, forward-fill prices or alter returns.
Coverage is explicit: unknown years must never silently become weekdays-only.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


CALENDAR_VERSION = "exchange-sessions-2025-2027-r3"
HKT = ZoneInfo("Asia/Hong_Kong")
NEW_YORK = ZoneInfo("America/New_York")


class CalendarCoverageError(ValueError):
    pass


@dataclass(frozen=True)
class SessionCalendar:
    market: str
    timezone: str
    coverage_start: date
    coverage_end: date
    closed: frozenset[date]
    half_days: frozenset[date]
    sources: tuple[str, ...]

    def is_session(self, day: date) -> bool:
        if not self.coverage_start <= day <= self.coverage_end:
            raise CalendarCoverageError(f"{self.market} calendar does not cover {day}")
        return day.weekday() < 5 and day not in self.closed

    def on_or_before(self, day: date) -> date:
        while not self.is_session(day):
            day -= timedelta(days=1)
        return day


@dataclass(frozen=True)
class EmergencyClosure:
    """Reviewed market-wide closure, not an inference from missing prices.

    Keep the dated announcement distinct from the exchange's general disaster
    policy. Regional office closures alone do not prove the market was closed.
    Adding a record requires source review and the normal release process.
    """

    market: str
    day: date
    reason: str
    announcement_source: str
    policy_source: str


def _days(text: str, year: int = 2026) -> frozenset[date]:
    return frozenset(date.fromisoformat(str(year) + "-" + value) for value in text.split())


US_CLOSED = _days("01-01 01-19 02-16 04-03 05-25 06-19 07-03 09-07 11-26 12-25")
US_HALF = _days("11-27 12-24")
NY_SOURCE = "https://www.nyse.com/trade/hours-calendars"
NASDAQ_SOURCE = "https://www.nasdaqtrader.com/trader.aspx?id=Calendar"
HK_SOURCE = "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2025/ce_SEHK_CT_075_2025.pdf"
SH_SOURCE = "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml"
TW_SOURCE = "https://www.twse.com.tw/rwd/en/holidaySchedule/holidaySchedule?response=json&queryYear=2026"

# Read and visually checked archived exchange calendars, not current pages
# silently returned for an old query. TWSE's request parameter is date; its
# response queryYear was independently checked to be 2025.
HISTORICAL_SOURCES = {
    "XNYS": "https://www.nyse.com/publicdocs/ICE_NYSE_2025_Yearly_Trading_Calendar.pdf",
    "XNAS": "https://www.nasdaqtrader.com/content/technicalsupport/2025tradingcalendar.pdf",
    "XHKG": "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2024/cc_SEHK_CT_063_2024.pdf",
    "XSHG": "https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20241223_10767110.shtml",
    "XTAI": "https://www.twse.com.tw/rwd/en/holidaySchedule/holidaySchedule?response=json&date=20250101",
}
US_2025 = _days("01-01 01-20 02-17 04-18 05-26 06-19 07-04 09-01 11-27 12-25", 2025)
HISTORICAL_CLOSED = {
    "XNYS": US_2025,
    "XNAS": US_2025,
    "XHKG": _days("01-01 01-29 01-30 01-31 04-04 04-18 04-21 05-01 05-05 07-01 "
                  "10-01 10-07 10-29 12-25 12-26", 2025),
    "XSHG": _days("01-01 01-28 01-29 01-30 01-31 02-01 02-02 02-03 02-04 "
                  "04-04 04-05 04-06 05-01 05-02 05-03 05-04 05-05 05-31 06-01 06-02 "
                  "10-01 10-02 10-03 10-04 10-05 10-06 10-07 10-08", 2025),
    "XTAI": _days("01-01 01-23 01-24 01-27 01-28 01-29 01-30 01-31 02-28 04-03 04-04 "
                  "05-01 05-30 05-31 09-28 09-29 10-06 10-10 10-24 10-25 12-25", 2025),
}
HISTORICAL_HALF = {
    "XNYS": _days("07-03 11-28 12-24", 2025),
    "XNAS": _days("07-03 11-28 12-24", 2025),
    "XHKG": _days("01-28 12-24 12-31", 2025),
    "XSHG": frozenset(), "XTAI": frozenset(),
}


def _calendar(market, timezone, closed, half_days, source):
    return SessionCalendar(market, timezone, date(2025, 1, 1), date(2026, 12, 31),
                           closed | HISTORICAL_CLOSED[market],
                           half_days | HISTORICAL_HALF[market], (source, HISTORICAL_SOURCES[market]))


CALENDARS = {
    "XNYS": _calendar("XNYS", "America/New_York", US_CLOSED, US_HALF, NY_SOURCE),
    "XNAS": _calendar("XNAS", "America/New_York", US_CLOSED, US_HALF, NASDAQ_SOURCE),
    "XHKG": _calendar("XHKG", "Asia/Hong_Kong",
        _days("01-01 02-17 02-18 02-19 04-03 04-06 04-07 05-01 05-25 06-19 07-01 10-01 10-19 12-25"),
        _days("02-16 12-24 12-31"), HK_SOURCE),
    "XSHG": _calendar("XSHG", "Asia/Shanghai",
        _days("01-01 01-02 01-03 02-15 02-16 02-17 02-18 02-19 02-20 02-21 02-22 02-23 "
              "04-04 04-05 04-06 05-01 05-02 05-03 05-04 05-05 06-19 06-20 06-21 "
              "09-25 09-26 09-27 10-01 10-02 10-03 10-04 10-05 10-06 10-07"), frozenset(), SH_SOURCE),
    "XTAI": _calendar("XTAI", "Asia/Taipei",
        _days("01-01 02-12 02-13 02-15 02-16 02-17 02-18 02-19 02-20 02-27 02-28 "
              "04-03 04-04 04-05 04-06 05-01 06-19 09-25 09-28 10-09 10-10 10-25 10-26 12-25"),
        frozenset(), TW_SOURCE),
}

# MS requires Hong Kong and NYSE sessions. Extend only those verified markets;
# an unverified 2027 Shanghai/Taiwan/Nasdaq calendar must still fail closed.
# HKEX's weekday/public-holiday rule is combined with the gazetted 2027 dates.
HK_2027_SOURCE = "https://www.info.gov.hk/gia/general/202605/15/P2026051400300p.htm"
HK_TRADING_RULE_SOURCE = "https://www.hkex.com.hk/Global/Exchange/FAQ/Securities-Market/Trading?sc_lang=en"
HK_2027_CLOSED = _days("01-01 02-06 02-08 02-09 03-26 03-27 03-29 04-05 05-01 "
                       "05-13 06-09 07-01 09-16 10-01 10-08 12-25 12-27", 2027)
for _market, _closed, _half, _sources in (
    ("XNYS", _days("01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24", 2027),
     _days("11-26", 2027), (NY_SOURCE,)),
    ("XHKG", HK_2027_CLOSED, _days("02-05 12-24 12-31", 2027),
     (HK_2027_SOURCE, HK_TRADING_RULE_SOURCE)),
):
    _existing = CALENDARS[_market]
    CALENDARS[_market] = replace(_existing, coverage_end=date(2027, 12, 31),
        closed=_existing.closed | _closed, half_days=_existing.half_days | _half,
        sources=tuple(dict.fromkeys(_existing.sources + _sources)))

# Bank fixing publication is not a stock-exchange closure policy. Capture the
# ordinary HK public holidays BEFORE applying reviewed exchange emergencies.
HK_BANK_CALENDAR = replace(CALENDARS["XHKG"], market="HKAB-HIBOR", half_days=frozenset())

# The planned annual TWSE holiday schedule does not include emergency closures.
# The securities association's dated underwriting notices explicitly state that
# the centralized market closed on ROC 115/07/10, postponing the affected work.
# This exception never inserts a July 10 price or modifies an IBKR observation.
EMERGENCY_CLOSURES = (
    EmergencyClosure("XTAI", date(2026, 7, 10), "Typhoon Bavi: centralized market closed",
                     "https://www.twsa.org.tw/",
                     "https://www.twse.com.tw/zh/clearing/suspended.html"),
    EmergencyClosure("XNYS", date(2025, 1, 9), "National Day of Mourning for President Carter",
                     "https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx",
                     HISTORICAL_SOURCES["XNYS"]),
    EmergencyClosure("XNAS", date(2025, 1, 9), "National Day of Mourning for President Carter",
                     "https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2025-1",
                     HISTORICAL_SOURCES["XNAS"]),
)


def apply_emergency_closures(calendars, closures):
    """Return a new registry; reject unscoped, duplicate or undocumented facts."""
    result = dict(calendars)
    seen = set()
    for closure in closures:
        key = (closure.market, closure.day)
        calendar = result.get(closure.market)
        if (key in seen or calendar is None or not closure.reason.strip()
                or not closure.announcement_source.startswith("https://")
                or not closure.policy_source.startswith("https://")
                or not calendar.coverage_start <= closure.day <= calendar.coverage_end
                or closure.day.weekday() >= 5 or closure.day in calendar.closed):
            raise CalendarCoverageError("Invalid reviewed emergency closure")
        seen.add(key)
        result[closure.market] = replace(calendar,
            closed=calendar.closed | frozenset((closure.day,)),
            sources=tuple(dict.fromkeys(calendar.sources +
                          (closure.announcement_source, closure.policy_source))))
    return result


CALENDARS = apply_emergency_closures(CALENDARS, EMERGENCY_CLOSURES)
BENCHMARK_MARKETS = {"GSPC":"XNYS", "IXIC":"XNAS", "HSI":"XHKG", "SHCOMP":"XSHG", "TAIEX":"XTAI"}


def benchmark_session_on_or_before(benchmark_id: str, cutoff: date) -> date:
    """Resolve the last exchange session without inventing observations.

    The caller supplies its own data cutoff; wall-clock producer deadlines are
    intentionally separate. Unmapped markets and uncovered years fail closed.
    """
    market = BENCHMARK_MARKETS.get(benchmark_id)
    if market is None:
        raise CalendarCoverageError("No calendar mapping for " + benchmark_id)
    return CALENDARS[market].on_or_before(cutoff)


def expected_benchmark_date(benchmark_id: str, *, now: datetime) -> dict:
    """Mirror the current 16:30 HKT producer with 20min runtime + 15min grace.

The producer currently caps *all* markets at its US 18:30 cut-off date. Do not
expect same-day Asian data it was never configured to fetch. A schedule or
producer-policy change must update this versioned monitor in the same release.
The 35min allowance is our operational policy, not a Yahoo delivery guarantee.
"""
    if now.tzinfo is None:
        raise ValueError("An aware observation time is required")
    market = BENCHMARK_MARKETS.get(benchmark_id)
    if market is None:
        raise CalendarCoverageError("No calendar mapping for " + benchmark_id)
    local = now.astimezone(HKT)
    cycle = datetime.combine(local.date(), time(16, 30), HKT)
    if local < cycle + timedelta(minutes=35):
        cycle -= timedelta(days=1)
    eastern = cycle.astimezone(NEW_YORK)
    cap = eastern.date() - timedelta(days=int(eastern.time() < time(18, 30)))
    expected = benchmark_session_on_or_before(benchmark_id, cap)
    return {"calendar_version":CALENDAR_VERSION, "market":market,
            "cycle_at":cycle.isoformat(), "deadline_at":(cycle+timedelta(minutes=35)).isoformat(),
            "expected_date":expected.isoformat(), "producer_cutoff_date":cap.isoformat()}


def check_benchmark_dates(evidence):
    """Shadow-only checks; no global all-data-fresh conclusion is returned."""
    now = datetime.fromisoformat(str(evidence["capture"]["captured_at"]))
    subjects = {row["subject_id"]:row for row in evidence["subjects"]}
    enabled = {r["subject_id"] for r in evidence["registrations"]
               if r["capability"] == "benchmark_refresh" and r["operating_state"] == "active"}
    checks = []
    for row in evidence["benchmarks"]:
        subject = subjects.get(row["subject_id"])
        if not subject or subject["lifecycle_status"] != "active" or row["subject_id"] not in enabled:
            continue
        item = {"subject_id":row["subject_id"], "benchmark_id":row["benchmark_id"],
                "observed_date":row.get("benchmark_usable_date")}
        try:
            expected = expected_benchmark_date(row["benchmark_id"], now=now)
            item.update(expected)
            observed = date.fromisoformat(row["benchmark_usable_date"]) if row.get("benchmark_usable_date") else None
            local_day = now.astimezone(ZoneInfo(CALENDARS[expected["market"]].timezone)).date()
            item["state"] = ("missing" if observed is None else "future_observation" if observed > local_day
                else "unexpected_non_session" if not CALENDARS[expected["market"]].is_session(observed)
                else "behind_expected_session" if observed < date.fromisoformat(expected["expected_date"])
                else "current_for_scheduled_cycle")
            item["quality"] = "pass" if row.get("benchmark_pass_date") == row.get("benchmark_usable_date") else "warn"
        except CalendarCoverageError:
            item["state"] = "calendar_unverified"
        checks.append(item)
    return {"mode":"shadow", "checks":checks,
            "unverified_scopes":["IBKR NAV publication calendar", "FX provider calendar", "Treasury publication calendar",
                                 "unregistered emergency exchange closures", "provider data corrections"]}
