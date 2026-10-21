"""SEC EDGAR 13F-HR information tables of a reviewed list of large US filers (phase 3 data).

docs/PHASE3_DATA_MACRO_13F.md. The job ibkrv2stg-institutional-holdings
(scripts/load_institutional_holdings.py) calls load(); nothing runs unless
INSTITUTIONAL_HOLDINGS_LOAD=on, and the table comes from a held migration
(infra/medallion/drafts/institutional_holdings.sql). SEC filings are public.

SEC fair access (https://www.sec.gov/os/accessing-edgar-data):
- every request declares a User-Agent naming the operator with a contact address, read from
  SEC_USER_AGENT and never written in code; without a usable one the job refuses to start;
- requests are paced to at most MAX_REQUESTS_PER_SECOND (4, well under SEC's 10 a second);
- a 429, a 5xx or a network error is retried with backoff, honouring Retry-After.

What is read for each filer: its submissions index (data.sec.gov/submissions/CIK##########.json,
and the older pages when the window reaches them), then for each 13F-HR or 13F-HR/A in the window
the filing index (index.json), the cover page (primary_doc.xml) and the information table (the
filing's other XML file). Each response is captured to bronze-market-data sec-13f/ before it is
parsed: the filing's documents once under history/ (an accession never changes), the submissions
index in its dated folder.

Parsing the information table:
- value is in thousands of dollars in filings made before 3 Jan 2023 and in dollars from then on
  (SEC's amendments to Form 13F, Release 34-95148, for filings made on or after 3 Jan 2023), so it
  is converted by the filing date;
- a filer reports one security on several lines (by manager and discretion): lines are summed per
  CUSIP and put/call; SH and PRN never mix, and a PRN line on a CUSIP the table also reports in SH
  is left out and counted;
- put_call is '' for the security itself, PUT or CALL for options on it.

Amendments, by accession (each filing keeps its own rows and nothing is deleted):
1. readers take each filer's latest accession for a quarter: the newest filed_at, then the highest
   accession;
2. a 13F-HR/A whose cover says RESTATEMENT is a complete report and is stored as it is;
3. one whose cover says NEW HOLDINGS only adds lines to the report before it, so it is stored as that
   report's holdings plus the new lines (summed where they meet), and the latest accession is still
   complete. If that earlier report is not stored, the amendment is skipped with a reason, never
   stored half;
4. an amendment that says neither is skipped with a reason;
5. 13F-NT notices have no information table and are not read.

Our instruments map to CUSIPs through instruments.isin: cusip_from_isin().
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import statistics
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import raw_capture
from .db import transaction
from .macro_series import DAILY_WINDOW, EXIT_DAILY_WINDOW, EXIT_HELD, in_daily_window  # noqa: F401 - one rule
from .reference_data import HttpBody, _ssl_context
from .util import utc_now_iso

SETTING = "INSTITUTIONAL_HOLDINGS_LOAD"  # off (default) or on
USER_AGENT_SETTING = "SEC_USER_AGENT"  # the operator's name, then a contact email address; required
TABLE = "silver.institutional_holdings"
RELATION = "public.institutional_holdings"
FEED = "sec-13f"
PROVIDER = "SEC EDGAR"
LICENCE = "sec-edgar-public-filing; U.S. Securities and Exchange Commission, EDGAR"
FILERS_FILE = Path(__file__).resolve().parent / "data" / "sec_13f_filers.json"
# CUSIPs for U.S. shares held through a broker that gives tickers but no ISIN (Kiwoom's U.S.
# account keys them US:<ticker>), reviewed by hand like the filer list.
TICKER_CUSIPS_FILE = Path(__file__).resolve().parent / "data" / "us_ticker_cusips.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{name}"
FORMS = ("13F-HR", "13F-HR/A")
SEC_LIMIT_PER_SECOND = 10  # SEC's published ceiling; never reached
MAX_REQUESTS_PER_SECOND = 4.0
ATTEMPTS = 4
BACKOFF_SECONDS = 2.0  # 2, 4, 8 seconds between attempts
MAX_BACKOFF_SECONDS = 60.0
TIMEOUT_SECONDS = 60
RETRY_CODES = frozenset({429, 500, 502, 503, 504})
DOLLARS_FROM = date(2023, 1, 3)  # filings made from this day report value in dollars, before it in thousands
# Every run reads the last 4 quarter ends and skips accessions already stored, so the first run is the
# backfill of the last 4 quarters and each later run adds the new quarter and any late amendment.
DEFAULT_QUARTERS = 4
DEFAULT_MINUTES = 45.0
MAX_INFORMATION_TABLE_CANDIDATES = 3
EXIT_REFUSED = 78  # EX_CONFIG: no usable SEC_USER_AGENT, or the filer list is not reviewed
ORIGINAL, RESTATEMENT, NEW_HOLDINGS = "original", "restatement", "new_holdings"

_CIK = re.compile(r"^[0-9]{10}$")
_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_CUSIP = re.compile(r"^[0-9A-Z]{9}$")
_ISIN = re.compile(r"^[A-Z]{2}[0-9A-Z]{9}[0-9]$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_QUARTER_ENDS = ("03-31", "06-30", "09-30", "12-31")


class UserAgentMissing(RuntimeError):
    """SEC_USER_AGENT is unset or does not name the operator with a contact address."""


class FilerListNotReviewed(RuntimeError):
    """The filer list has not been marked reviewed."""


class SecRequestError(RuntimeError):
    """SEC did not give the document after the retries, or refused the request."""


class SecNotFound(SecRequestError):
    """HTTP 404."""


class FilingSkipped(ValueError):
    """A filing that is read but not stored, with the reason."""


# ---------------------------------------------------------------- settings


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    """INSTITUTIONAL_HOLDINGS_LOAD=on. Anything else, a typo included, is off."""
    environ = os.environ if environ is None else environ
    return str(environ.get(SETTING, "")).strip().lower() == "on"


def user_agent(environ: Mapping[str, str] | None = None) -> str:
    """SEC_USER_AGENT, checked: a name and a contact email address, one line, at most 200 characters."""
    environ = os.environ if environ is None else environ
    raw = str(environ.get(USER_AGENT_SETTING, ""))
    if any(ord(character) < 32 for character in raw):
        raise UserAgentMissing(f"{USER_AGENT_SETTING} must be one line")
    text = " ".join(raw.split())
    words = text.split(" ") if text else []
    if not text or len(text) > 200 or not any(_EMAIL.match(word) for word in words) \
            or not any("@" not in word for word in words):
        raise UserAgentMissing(f"set {USER_AGENT_SETTING} to the operator's name and contact email address, "
                               "as SEC fair access asks (for example 'Company Name' followed by its contact email address)")
    return text


@dataclass(frozen=True)
class Filer:
    cik: str
    name: str


def load_filers(path: Path = FILERS_FILE, *, require_reviewed: bool = True) -> list[Filer]:
    """The filer list (CIK and name). Refuses one not marked reviewed unless require_reviewed is False."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if require_reviewed and data.get("reviewed") is not True:
        raise FilerListNotReviewed(f"{Path(path).name} is not marked reviewed: check every CIK on EDGAR, complete "
                                   "the list, then set reviewed, reviewed_by and reviewed_on")
    filers, seen = [], set()
    for item in data.get("filers") or []:
        cik, name = str(item.get("cik", "")).strip(), " ".join(str(item.get("name", "")).split())
        if not _CIK.match(cik) or not name or cik in seen:
            raise ValueError(f"{Path(path).name}: bad or repeated filer entry {item!r}")
        seen.add(cik)
        filers.append(Filer(cik, name))
    if not filers:
        raise ValueError(f"{Path(path).name} lists no filers")
    return filers


def reviewed_ticker_cusips(path: Path = TICKER_CUSIPS_FILE) -> dict[str, str]:
    """``{ticker: cusip}`` from the reviewed ticker list; empty while it is not marked reviewed, so a
    ticker-only U.S. share stays "not covered" until someone has checked its CUSIP. A malformed
    entry (bad CUSIP or check digit, repeated ticker) refuses the whole list."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if data.get("reviewed") is not True:
        return {}
    found: dict[str, str] = {}
    for ticker, entry in (data.get("tickers") or {}).items():
        key = str(ticker or "").strip().upper()
        cusip = str((entry or {}).get("cusip") if isinstance(entry, dict) else entry or "").strip().upper()
        valid = bool(_CUSIP.match(cusip)) and cusip[8].isdigit() and cusip_check_digit(cusip[:8]) == int(cusip[8])
        if not key or key in found or not valid:
            raise ValueError(f"{Path(path).name}: bad or repeated ticker entry {ticker!r}")
        found[key] = cusip
    return found


# ---------------------------------------------------------------- CUSIPs


def _character_value(character: str) -> int:
    if character.isdigit():
        return int(character)
    if "A" <= character <= "Z":
        return ord(character) - ord("A") + 10
    return {"*": 36, "@": 37, "#": 38}[character]


def cusip_check_digit(base: str) -> int:
    """The ninth character of a CUSIP from its first eight."""
    total = 0
    for index, character in enumerate(base.upper()):
        value = _character_value(character)
        if index % 2 == 1:
            value *= 2
        total += value // 10 + value % 10
    return (10 - total % 10) % 10


def isin_check_digit(base: str) -> int:
    """The twelfth character of an ISIN from its first eleven (Luhn over the letters as numbers)."""
    digits = "".join(str(_character_value(character)) for character in base.upper())
    total = 0
    for index, character in enumerate(reversed(digits)):
        value = int(character) * (2 if index % 2 == 0 else 1)
        total += value // 10 + value % 10
    return (10 - total % 10) % 10


def cusip_from_isin(isin: Any) -> str | None:
    """The CUSIP inside a US ISIN (isin[2:11]); None for a non-US or malformed ISIN.

    13F covers US-listed securities, whose ISIN is US + CUSIP + a check digit."""
    value = str(isin or "").strip().upper()
    if not _ISIN.match(value) or not value.startswith("US"):
        return None
    if isin_check_digit(value[:11]) != int(value[11]):
        return None
    return value[2:11]


def normalise_cusip(text: Any) -> tuple[str | None, bool]:
    """(the CUSIP, repaired), or (None, False). Upper case, spaces removed; a numeric CUSIP that lost its
    leading zeros (as spreadsheets do) is padded back only when the padded check digit is right."""
    value = re.sub(r"\s+", "", str(text or "")).upper()
    if _CUSIP.match(value):
        return value, False
    if re.fullmatch(r"[0-9]{7,8}", value):
        padded = value.zfill(9)
        if cusip_check_digit(padded[:8]) == int(padded[8]):
            return padded, True
    return None, False


# ---------------------------------------------------------------- requests


class Pacer:
    """Spaces request starts at least 1/per_second apart (an injectable clock for tests)."""

    def __init__(self, per_second: float = MAX_REQUESTS_PER_SECOND, *,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        if not 0 < per_second < SEC_LIMIT_PER_SECOND:
            raise ValueError(f"the request rate must be above 0 and below SEC's {SEC_LIMIT_PER_SECOND} a second")
        self.interval = 1.0 / per_second
        self.clock, self.sleep = clock, sleep
        self.waited = 0.0
        self._next: float | None = None

    def wait(self) -> None:
        now = self.clock()
        if self._next is not None and now < self._next:
            self.sleep(self._next - now)
            self.waited += self._next - now
            now = self.clock()
        self._next = now + self.interval


def _urlopen(request: urllib.request.Request, timeout: float) -> HttpBody:
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        body = HttpBody(response.read())
        body.status = getattr(response, "status", None)
        headers = getattr(response, "headers", None)
        body.content_type = headers.get("Content-Type") if headers is not None else None
        return body


def _retry_after(error: urllib.error.HTTPError) -> float | None:
    try:
        value = float(str((error.headers or {}).get("Retry-After", "")).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


class SecClient:
    """GETs from SEC EDGAR with the declared User-Agent, paced, retried, each response captured raw."""

    def __init__(self, agent: str, *, opener: Callable[[urllib.request.Request, float], bytes] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 per_second: float = MAX_REQUESTS_PER_SECOND, attempts: int = ATTEMPTS,
                 timeout: float = TIMEOUT_SECONDS) -> None:
        self.agent = user_agent({USER_AGENT_SETTING: agent})
        self.pacer = Pacer(per_second, clock=clock, sleep=sleep)
        self.sleep = sleep
        self.attempts, self.timeout = max(1, int(attempts)), timeout
        self._open = opener or _urlopen
        self.requests = self.retries = 0

    def get(self, url: str, *, key: str, extension: str, history: bool) -> bytes:
        last: BaseException | None = None
        for attempt in range(self.attempts):
            self.pacer.wait()
            self.requests += 1
            requested_at = raw_capture.utc_now()
            request = urllib.request.Request(url, headers={"User-Agent": self.agent, "Accept": "*/*"})
            delay = None
            try:
                body = self._open(request, self.timeout)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    raise SecNotFound(f"SEC has no {url} (HTTP 404)") from None
                if error.code not in RETRY_CODES:
                    hint = f"; check {USER_AGENT_SETTING}" if error.code == 403 else ""
                    raise SecRequestError(f"SEC refused {url}: HTTP {error.code}{hint}") from None
                last, delay = error, _retry_after(error)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                last = error
            else:
                # Raw first, exactly as received, before anything parses it.
                raw_capture.capture_http("sec-13f", PROVIDER, url=url, body=body, key=key, extension=extension,
                                         requested_at=requested_at, history=history, licence=LICENCE)
                return body
            if attempt + 1 < self.attempts:
                self.retries += 1
                self.sleep(min(MAX_BACKOFF_SECONDS,
                               delay if delay is not None else BACKOFF_SECONDS * 2 ** attempt))
        raise SecRequestError(f"SEC did not answer {url} after {self.attempts} attempts") from last


# ---------------------------------------------------------------- documents


def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _refuse_declarations(body: bytes) -> None:
    """EDGAR XML has no DTD; one with a DOCTYPE or ENTITY is refused before parsing (entity expansion)."""
    head = bytes(body).upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ValueError("an XML document with a DOCTYPE or ENTITY declaration is refused")


def _iso_day(text: str) -> str:
    text = text.strip()
    for pattern in ("%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"{text!r} is not a date")


def quarter_end(day: str) -> bool:
    return bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day or "")) and day[5:] in _QUARTER_ENDS


def quarter_ends(today: date, count: int) -> list[str]:
    """The last `count` quarter-end dates on or before today, newest first."""
    ends, year, month = [], today.year, today.month
    while len(ends) < count:
        for end_month, end_day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            day = date(year, end_month, end_day)
            if day <= today and len(ends) < count and (year < today.year or end_month <= month):
                ends.append(day.isoformat())
        year -= 1
    return ends


def _filings(arrays: Mapping[str, Any]) -> list[dict[str, str]]:
    accessions = list(arrays.get("accessionNumber") or [])
    columns = {key: list(arrays.get(key) or [""] * len(accessions))
               for key in ("form", "filingDate", "reportDate")}
    if any(len(values) != len(accessions) for values in columns.values()):
        raise ValueError("the submissions index has columns of different lengths")
    return [{"accession": str(accessions[i]).strip(), "form": str(columns["form"][i] or "").strip(),
             "filed_at": str(columns["filingDate"][i] or "").strip()[:10],
             "period": str(columns["reportDate"][i] or "").strip()[:10]} for i in range(len(accessions))]


def parse_submissions(body: bytes | str) -> dict[str, Any]:
    """The filer's name, its recent filings and the older pages (name, filingFrom, filingTo)."""
    payload = json.loads(body)
    filings = payload.get("filings") or {}
    return {"name": " ".join(str(payload.get("name") or "").split()),
            "filings": _filings(filings.get("recent") or {}),
            "pages": [{"name": str(page.get("name") or ""), "from": str(page.get("filingFrom") or "")[:10],
                       "to": str(page.get("filingTo") or "")[:10]} for page in filings.get("files") or []]}


def parse_submissions_page(body: bytes | str) -> list[dict[str, str]]:
    return _filings(json.loads(body))


def in_window(filings: Iterable[Mapping[str, str]], since: str) -> list[dict[str, str]]:
    """13F-HR and 13F-HR/A filings for quarters from `since` on, oldest filing first."""
    chosen = {}
    for filing in filings:
        period = filing.get("period") or ""
        if filing.get("form") not in FORMS or not _ACCESSION.match(filing.get("accession") or ""):
            continue
        if (period or filing.get("filed_at") or "") < since:
            continue
        chosen[filing["accession"]] = dict(filing)
    return sorted(chosen.values(), key=lambda f: (f["filed_at"], f["accession"]))


def parse_filing_index(body: bytes | str) -> tuple[str, list[str]]:
    """(the cover page's file name, the other XML files that may be the information table)."""
    items = (json.loads(body).get("directory") or {}).get("item") or []
    names = [str(item.get("name") or "") for item in items]
    cover = next((name for name in names if name.lower() == "primary_doc.xml"), None)
    if cover is None:
        raise FilingSkipped("the filing has no primary_doc.xml cover page")
    others = sorted(name for name in names if name.lower().endswith(".xml") and name != cover)
    return cover, others


def parse_cover(body: bytes) -> dict[str, Any]:
    """The 13F cover page: quarter, amendment and report type, and the summary totals."""
    _refuse_declarations(body)
    root = ET.fromstring(body)
    found: dict[str, str] = {}
    for element in root.iter():
        name = _local(element.tag)
        if name not in found and (element.text or "").strip():
            found[name] = (element.text or "").strip()
    period_text = found.get("reportCalendarOrQuarter") or found.get("periodOfReport")
    if not period_text:
        raise FilingSkipped("the cover page gives no quarter")
    total = found.get("tableValueTotal")
    entries = found.get("tableEntryTotal")
    return {
        "period": _iso_day(period_text),
        "is_amendment": found.get("isAmendment", "").lower() in ("true", "y", "yes", "1"),
        "amendment_type": " ".join(found.get("amendmentType", "").upper().split()),
        "report_type": " ".join(found.get("reportType", "").upper().split()),
        "table_entry_total": int(entries) if entries and entries.isdigit() else None,
        "table_value_total": float(total) if total and re.fullmatch(r"[0-9]+(\.[0-9]+)?", total) else None,
    }


def value_multiplier(filed_at: str) -> int:
    """Thousands before 3 Jan 2023, dollars from then on, by the filing date."""
    return 1000 if date.fromisoformat(filed_at[:10]) < DOLLARS_FROM else 1


def _number(text: str | None) -> float | None:
    if text is None or not re.fullmatch(r"[0-9]+(\.[0-9]+)?", text.strip()):
        return None
    value = float(text.strip())
    return value if math.isfinite(value) else None


def _resolve_types(lines: Mapping[tuple[str, str, str], dict[str, Any]],
                   counts: dict[str, int]) -> list[dict[str, Any]]:
    """One row per CUSIP and put/call: SH wins over PRN on the same key, which is counted as left out."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for (cusip, put_call, shares_type), line in sorted(lines.items(), key=lambda item: item[0][2] != "SH"):
        key = (cusip, put_call)
        if key in rows:
            counts["prn_left_out_beside_sh"] = counts.get("prn_left_out_beside_sh", 0) + 1
            continue
        rows[key] = {"cusip": cusip, "put_call": put_call, "shares_type": shares_type, **line}
    return [rows[key] for key in sorted(rows)]


def parse_information_table(body: bytes, *, filed_at: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The information table as rows (cusip, put_call, shares_type, shares, value_usd, issuer), summed per
    CUSIP and put/call, with counts of what was repaired or left out. Raises FilingSkipped when the
    document is not an information table."""
    _refuse_declarations(body)
    multiplier = value_multiplier(filed_at)
    counts: dict[str, int] = {"lines": 0}
    lines: dict[tuple[str, str, str], dict[str, Any]] = {}
    first = True
    for event, element in ET.iterparse(io.BytesIO(bytes(body)), events=("start", "end")):
        if event == "start":
            if first and _local(element.tag) != "informationTable":
                raise FilingSkipped(f"not an information table (root {_local(element.tag)})")
            first = False
            continue
        if _local(element.tag) != "infoTable":
            continue
        counts["lines"] += 1
        fields: dict[str, str] = {}
        for child in element.iter():
            fields.setdefault(_local(child.tag), (child.text or "").strip())
        element.clear()
        cusip, repaired = normalise_cusip(fields.get("cusip"))
        value, shares = _number(fields.get("value")), _number(fields.get("sshPrnamt"))
        shares_type = fields.get("sshPrnamtType", "").upper()
        put_call = fields.get("putCall", "").upper()
        problem = ("cusip_invalid" if cusip is None else "value_invalid" if value is None
                   else "shares_invalid" if shares is None
                   else "shares_type_invalid" if shares_type not in ("SH", "PRN")
                   else "put_call_invalid" if put_call not in ("", "PUT", "CALL") else None)
        if problem:
            counts[problem] = counts.get(problem, 0) + 1
            continue
        if repaired:
            counts["cusip_repaired"] = counts.get("cusip_repaired", 0) + 1
        line = lines.setdefault((cusip, put_call, shares_type), {
            "issuer": " ".join(fields.get("nameOfIssuer", "").split())[:300], "shares": 0.0, "value_usd": 0.0})
        line["shares"] += shares
        line["value_usd"] += value * multiplier
    if first:
        raise FilingSkipped("the information table is empty")
    rows = _resolve_types(lines, counts)
    details: dict[str, Any] = {"value_unit": "thousands" if multiplier == 1000 else "dollars",
                               "rows": len(rows), **counts}
    # A reported value far from a share price points at the wrong unit; reported, never changed.
    prices = [row["value_usd"] / row["shares"] for row in rows
              if row["shares_type"] == "SH" and not row["put_call"] and row["shares"] > 0]
    if len(prices) >= 5:
        median = statistics.median(prices)
        details["median_value_per_share"] = round(median, 4)
        if median < 0.5 or median > 100000:
            details["unit_warning"] = "values per share look like the wrong unit; check this filing"
    return rows, details


def compose_new_holdings(base: Sequence[Mapping[str, Any]], added: Sequence[Mapping[str, Any]]) -> list[dict]:
    """A NEW HOLDINGS amendment's complete table: the earlier report's rows plus the added lines, summed
    where CUSIP, put/call and share type meet. An added line of the other share type is left out."""
    rows = {(row["cusip"], row["put_call"]): dict(row) for row in base}
    for line in added:
        key = (line["cusip"], line["put_call"])
        if key not in rows:
            rows[key] = dict(line)
        elif rows[key]["shares_type"] == line["shares_type"]:
            rows[key]["shares"] = float(rows[key]["shares"]) + float(line["shares"])
            rows[key]["value_usd"] = float(rows[key]["value_usd"]) + float(line["value_usd"])
    return [rows[key] for key in sorted(rows)]


def amendment_kind(form: str, cover: Mapping[str, Any]) -> str:
    if form == "13F-HR":
        return ORIGINAL
    if cover.get("amendment_type") == "RESTATEMENT":
        return RESTATEMENT
    if cover.get("amendment_type") == "NEW HOLDINGS":
        return NEW_HOLDINGS
    raise FilingSkipped("the amendment says neither RESTATEMENT nor NEW HOLDINGS")


# ---------------------------------------------------------------- silver


LOADED_SQL = "SELECT DISTINCT accession FROM institutional_holdings WHERE filer_cik = ?"
BASE_SQL = """
SELECT accession FROM institutional_holdings
WHERE filer_cik = ? AND period = ? AND (filed_at < ? OR (filed_at = ? AND accession < ?))
GROUP BY accession, filed_at ORDER BY filed_at DESC, accession DESC LIMIT 1
"""
ROWS_SQL = ("SELECT cusip, issuer, shares, shares_type, value_usd, put_call FROM institutional_holdings "
            "WHERE filer_cik = ? AND period = ? AND accession = ?")
DELETE_SQL = "DELETE FROM institutional_holdings WHERE filer_cik = ? AND period = ? AND accession = ?"
INSERT_SQL = """
INSERT INTO institutional_holdings (filer_cik, filer_name, period, cusip, issuer, shares, shares_type,
    value_usd, put_call, accession, filed_at, fetched_at)
SELECT ?, ?, ?, r.cusip, r.issuer, r.shares, r.shares_type, r.value_usd, r.put_call, ?, ?, ?
FROM unnest(?::text[], ?::text[], ?::float8[], ?::text[], ?::float8[], ?::text[])
    AS r(cusip, issuer, shares, shares_type, value_usd, put_call)
ON CONFLICT (tenant_id, filer_cik, period, cusip, put_call, accession) DO UPDATE SET
    filer_name = excluded.filer_name, issuer = excluded.issuer, shares = excluded.shares,
    shares_type = excluded.shares_type, value_usd = excluded.value_usd, filed_at = excluded.filed_at,
    fetched_at = excluded.fetched_at
"""
# What readers do (docs/PHASE3_DATA_MACRO_13F.md): each filer's latest accession for a quarter.
LATEST_SQL = """
SELECT h.* FROM institutional_holdings h
JOIN (SELECT DISTINCT ON (filer_cik, period) filer_cik, period, accession FROM institutional_holdings
      ORDER BY filer_cik, period, filed_at DESC, accession DESC) l
  ON l.filer_cik = h.filer_cik AND l.period = h.period AND l.accession = h.accession
"""


def relation_present(connection: Any) -> bool:
    """False until the held migration has created the table (the job then exits EXIT_HELD)."""
    row = connection.execute("SELECT to_regclass(?) IS NOT NULL AS present", (RELATION,)).fetchone()
    connection.rollback()
    return bool(row["present"] if isinstance(row, Mapping) else row[0])


def loaded_accessions(connection: Any, cik: str) -> set[str]:
    rows = connection.execute(LOADED_SQL, (cik,)).fetchall()
    connection.rollback()
    return {str(row["accession"]) for row in rows}


def store_filing(connection: Any, *, cik: str, filer_name: str, filing: Mapping[str, str], kind: str,
                 rows: Sequence[Mapping[str, Any]], fetched_at: str) -> dict[str, Any]:
    """One filing in one transaction: its rows replace whatever its accession had (a rerun writes the
    same rows). A NEW HOLDINGS amendment is first completed with the report before it."""
    period, accession, filed_at = filing["period"], filing["accession"], filing["filed_at"]
    base = None
    with transaction(connection):
        # One writer per filer and quarter, so an amendment never reads a half-written report.
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(?))",
                           (f"institutional_holdings:{cik}:{period}",))
        if kind == NEW_HOLDINGS:
            found = connection.execute(BASE_SQL, (cik, period, filed_at, filed_at, accession)).fetchone()
            if found is None:
                raise FilingSkipped("a NEW HOLDINGS amendment whose earlier report is not stored")
            base = str(found["accession"])
            rows = compose_new_holdings(
                [dict(row) for row in connection.execute(ROWS_SQL, (cik, period, base)).fetchall()], rows)
        connection.execute(DELETE_SQL, (cik, period, accession))
        connection.execute(INSERT_SQL, (
            cik, filer_name[:300], period, accession, filed_at, fetched_at,
            [row["cusip"] for row in rows], [row["issuer"] for row in rows],
            [float(row["shares"]) for row in rows], [row["shares_type"] for row in rows],
            [float(row["value_usd"]) for row in rows], [row["put_call"] for row in rows]))
    return {"rows": len(rows), "base_accession": base}


# ---------------------------------------------------------------- the run


def _folder(accession: str) -> str:
    return accession.replace("-", "")


def fetch_submissions(client: SecClient, cik: str, since: str) -> dict[str, Any]:
    """The filer's name and every 13F-HR(/A) filing for quarters from `since` on."""
    name = f"CIK{cik}.json"
    found = parse_submissions(client.get(SUBMISSIONS_URL.format(name=name), key=f"submissions_CIK{cik}",
                                         extension="json", history=False))
    filings = list(found["filings"])
    for page in found["pages"]:
        if page["name"] and page["to"] >= since:  # an older page that reaches into the window
            filings += parse_submissions_page(client.get(SUBMISSIONS_URL.format(name=page["name"]),
                                                         key=f"submissions_{page['name']}", extension="json",
                                                         history=False))
    return {"name": found["name"], "filings": in_window(filings, since)}


def fetch_filing(client: SecClient, cik: str, filing: Mapping[str, str]) -> dict[str, Any]:
    """The cover page and the information table of one filing, each captured before it is parsed."""
    accession = filing["accession"]
    base = {"cik": int(cik), "folder": _folder(accession)}
    index = client.get(ARCHIVE_URL.format(**base, name="index.json"), key=f"{cik}_{accession}_index",
                       extension="json", history=True)
    cover_name, candidates = parse_filing_index(index)
    cover = parse_cover(client.get(ARCHIVE_URL.format(**base, name=cover_name),
                                   key=f"{cik}_{accession}_primary_doc", extension="xml", history=True))
    for name in candidates[:MAX_INFORMATION_TABLE_CANDIDATES]:
        body = client.get(ARCHIVE_URL.format(**base, name=name), key=f"{cik}_{accession}_{name}",
                          extension="xml", history=True)
        try:
            rows, details = parse_information_table(body, filed_at=filing["filed_at"])
        except FilingSkipped:
            continue  # another XML file in the filing; try the next
        return {"cover": cover, "rows": rows, "details": {**details, "document": name}}
    raise FilingSkipped("the filing has no information table")


def load(connection: Any, client: SecClient, filers: Sequence[Filer], *, since: str, refresh: bool = False,
         stop: Callable[[], bool] | None = None, emit: Callable[[dict[str, Any]], None] | None = None
         ) -> dict[str, Any]:
    """Every filer's 13F-HR(/A) filings for quarters from `since` on, each filing in its own transaction.
    Accessions already stored are skipped unless refresh. One filer or filing that fails never stops
    the others. `stop` is asked before each filer and filing (the time budget, the daily window)."""
    fetched_at = utc_now_iso()
    stored, skipped, failed, not_started = [], [], [], []
    already = 0
    for filer in filers:
        if stop is not None and stop():
            not_started.append({"filer_cik": filer.cik, "reason": "stopped: time budget or daily window"})
            continue
        try:
            found = fetch_submissions(client, filer.cik, since)
            done = set() if refresh else loaded_accessions(connection, filer.cik)
        except Exception as error:  # noqa: BLE001 - one filer never stops the others
            connection.rollback()
            failed.append({"filer_cik": filer.cik, "error": f"{type(error).__name__}: {error}"[:500]})
            continue
        name = found["name"] or filer.name
        for filing in found["filings"]:
            where = {"filer_cik": filer.cik, "accession": filing["accession"], "form": filing["form"],
                     "period": filing["period"], "filed_at": filing["filed_at"]}
            if filing["accession"] in done:
                already += 1
                continue
            if stop is not None and stop():
                not_started.append({**where, "reason": "stopped: time budget or daily window"})
                continue
            try:
                document = fetch_filing(client, filer.cik, filing)
                cover = document["cover"]
                if filing["period"] and cover["period"] != filing["period"]:
                    raise FilingSkipped(f"the cover page says {cover['period']}, EDGAR's index {filing['period']}")
                filing = {**filing, "period": cover["period"]}
                if not quarter_end(filing["period"]):
                    raise FilingSkipped(f"{filing['period']} is not a quarter end")
                if "NOTICE" in cover["report_type"]:
                    raise FilingSkipped("a 13F notice has no holdings")
                if not document["rows"]:
                    raise FilingSkipped("the information table has no usable lines")
                kind = amendment_kind(filing["form"], cover)
                result = store_filing(connection, cik=filer.cik, filer_name=name, filing=filing, kind=kind,
                                      rows=document["rows"], fetched_at=fetched_at)
            except FilingSkipped as reason:
                connection.rollback()
                line = {**where, "reason": str(reason)}
                skipped.append(line)
                if emit is not None:
                    emit({"status": "skipped", **line})
                continue
            except Exception as error:  # noqa: BLE001
                connection.rollback()
                line = {**where, "error": f"{type(error).__name__}: {error}"[:500]}
                failed.append(line)
                if emit is not None:
                    emit({"status": "failed", **line})
                continue
            line = {**where, "period": filing["period"], "kind": kind, **result, **document["details"]}
            stored.append(line)
            if emit is not None:
                emit({"status": "stored", **line})
    return {"since": since, "filers": len(filers), "stored": len(stored),
            "rows_stored": sum(item["rows"] for item in stored), "already_stored": already,
            "skipped": skipped, "failed": failed, "not_started": not_started,
            "requests": client.requests, "retries": client.retries,
            "seconds_paced": round(client.pacer.waited, 3)}
