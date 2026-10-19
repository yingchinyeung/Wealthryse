"""Daily factor returns in silver (docs/PHASE3_DATA.md, job ibkrv2stg-factor-returns).

The job downloads Kenneth French's daily five-factor and momentum files for the regions
fama_french.REGIONS names (us, developed, asia_pacific_ex_japan), stores each response as
received in bronze-market-data (feed kenneth-french, through raw_capture, before anything is
parsed), then upserts silver.factor_returns. fama_french itself is not changed: the website's
factor model still downloads on request until it is switched to read silver.

- Values are daily simple returns as fractions (the files are in percent). The monthly and
  annual sections, the notes and the copyright line are left out (fama_french.parse_french_csv).
- The vintage is the day the file was published (its Last-Modified header), or the day it was
  fetched when the server does not say. Every vintage is kept. A vintage holds the rows it added
  or changed: a value the latest earlier vintage already has, unchanged, is not stored again, so
  a weekly run over an unchanged file writes nothing. Readers take the latest vintage per date;
  the series as published on vintage V is, per date, the latest vintage on or before V.
- The ETF stand-ins (fama_french.load_proxy_factors) are stored only when asked (etf_proxy),
  marked source 'etf-proxy', and only for days after the region's latest Kenneth French date, so
  they fill the weeks the library has not published yet and never replace its figures.
- Each region is written in one transaction. The table comes from a held migration: until it has
  run the job stops before fetching anything and exits EXIT_HELD.

The library is free to download; its terms for commercial use are not yet checked, so the data
stays internal (raw_capture.FEED_LICENSES records this on every capture).
"""
from __future__ import annotations

import email.utils
import io
import os
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import fama_french, raw_capture

SETTING = "FACTOR_RETURNS_LOAD"  # off (default) or on
TABLE = "silver.factor_returns"
RELATION = "factor_returns"
RAW_FEED = "kenneth-french"
PROVIDER = "Kenneth French data library"
SOURCE = "kenneth-french"
PROXY_SOURCE = "etf-proxy"
PROXY_CAPTURE_FEED = "yahoo-crisis-history"  # yfinance adjusted-close history, as crisis_replay stores it
FACTORS = ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF", "MOM")
MOMENTUM_NAMES = frozenset({"Mom", "MOM", "WML"})
USER_AGENT = "Mozilla/5.0 (WealthRyse factor returns)"
EXIT_HELD = 65  # the held migration has not run: nothing was fetched or written

KNOWN_SQL = """
SELECT DISTINCT ON (factor, return_date) factor, return_date, value
FROM factor_returns
WHERE region = ? AND source = ? AND vintage < ? AND factor = ANY(?)
ORDER BY factor, return_date, vintage DESC
"""
LATEST_FRENCH_SQL = "SELECT MAX(return_date) AS latest FROM factor_returns WHERE region = ? AND source = ?"
UPSERT_SQL = """
INSERT INTO factor_returns AS f (region, factor, return_date, value, source, vintage, fetched_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (tenant_id, region, factor, return_date, vintage) DO UPDATE SET
    value = excluded.value, source = excluded.source, fetched_at = excluded.fetched_at
WHERE (f.value IS DISTINCT FROM excluded.value OR f.source IS DISTINCT FROM excluded.source)
  AND (f.source = excluded.source OR excluded.source = 'kenneth-french')
"""
# A library row replaces a stand-in stored under the same vintage (even with an equal value, so its
# source is right); a stand-in never replaces a library row.


def load_enabled(environ: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return str(environ.get(SETTING, "")).strip().lower() == "on"


# ---------------------------------------------------------------- fetching


@dataclass(frozen=True)
class Download:
    url: str
    body: bytes
    requested_at: datetime
    status: int | None = None
    content_type: str | None = None
    last_modified: str | None = None  # the Last-Modified header as the server sent it


def download(url: str, *, timeout: int = 60, attempts: int = 3,
             sleep: Callable[[float], None] = time.sleep) -> Download:
    """One file from the library, with its headers; retries network errors twice."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        requested_at = raw_capture.utc_now()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                headers = response.headers
                return Download(url, body, requested_at, getattr(response, "status", None),
                                headers.get("Content-Type"), headers.get("Last-Modified"))
        except Exception as error:  # noqa: BLE001 - network and HTTP errors retry alike
            last_error = error
            if attempt + 1 < attempts:
                sleep(2 ** attempt)
    raise fama_french.FactorDataError(f"Could not download {url}: {last_error}") from last_error


def vintage_of(got: Download) -> str:
    """The publication day from Last-Modified (UTC), else the day it was fetched."""
    try:
        published = email.utils.parsedate_to_datetime(str(got.last_modified))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return published.astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return got.requested_at.astimezone(timezone.utc).date().isoformat()


def csv_text(body: bytes) -> str:
    """The CSV inside a library zip, as text."""
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
            return archive.read(member).decode("latin-1")
    except (StopIteration, zipfile.BadZipFile, KeyError) as error:
        raise fama_french.FactorDataError(f"Not a factor library zip: {str(error) or 'no CSV inside'}") from error


def window_end(text: str) -> date | None:
    """The last daily date in a file, read without parsing its values (for raw_capture.settled)."""
    latest = None
    for line in text.splitlines():
        key = line.split(",", 1)[0].strip()
        if len(key) == 8 and key.isdigit() and (latest is None or key > latest):
            latest = key
    try:
        return None if latest is None else date(int(latest[:4]), int(latest[4:6]), int(latest[6:]))
    except ValueError:
        return None


def parse_factor_file(text: str, *, momentum: bool = False) -> dict[str, dict[str, float]]:
    """{ISO date: {factor: fraction}} from a daily file; only the factors in FACTORS are kept."""
    rows = fama_french.parse_french_csv(text, single_column="MOM" if momentum else None)
    kept: dict[str, dict[str, float]] = {}
    for day, values in rows.items():
        row = {}
        for name, value in values.items():
            name = "MOM" if momentum and name in MOMENTUM_NAMES else name
            if name in FACTORS:
                row[name] = value
        if row:
            kept[day] = row
    if not kept:
        raise fama_french.FactorDataError("The factor file has none of the expected factors.")
    return kept


def _capture(got: Download, region: str, name: str) -> str | None:
    """The response as received, in bronze, before it is parsed. A file whose data ended over a week
    ago (they always do: the library publishes a month at a time) is filed once under history/."""
    try:
        end = window_end(csv_text(got.body))
    except Exception:  # noqa: BLE001 - not readable: still captured, in the dated folder
        end = None
    return raw_capture.capture_http(
        RAW_FEED, PROVIDER, url=got.url, body=got.body, key=raw_capture.request_key(region, name.removesuffix(".zip")),
        status=got.status, content_type=got.content_type, extension="zip", requested_at=got.requested_at,
        history=end is not None and raw_capture.settled(end, now=got.requested_at))


# ---------------------------------------------------------------- storing


def table_exists(connection: Any) -> bool:
    """Whether the held migration has run (the name resolves for this login)."""
    row = connection.execute("SELECT to_regclass(?) AS relation", (RELATION,)).fetchone()
    connection.rollback()
    return row is not None and row["relation"] is not None


@dataclass(frozen=True)
class Batch:
    """One file's rows, ready to store."""

    rows: dict[str, dict[str, float]]  # {ISO date: {factor: fraction}}
    source: str
    vintage: str
    fetched_at: str


def changed_rows(connection: Any, region: str, batch: Batch) -> list[tuple]:
    """The batch's rows that the latest earlier vintage of the same source does not already have."""
    factors = sorted({factor for values in batch.rows.values() for factor in values})
    known = {(str(r["factor"]), str(r["return_date"])): float(r["value"]) for r in connection.execute(
        KNOWN_SQL, (region, batch.source, batch.vintage, factors)).fetchall()}
    rows = []
    for day, values in sorted(batch.rows.items()):
        for factor, value in sorted(values.items()):
            if known.get((factor, day)) != value:
                rows.append((region, factor, day, value, batch.source, batch.vintage, batch.fetched_at))
    return rows


def store(connection: Any, region: str, batches: Sequence[Batch]) -> dict[str, int]:
    """All of a region's batches in one transaction; returns rows compared and rows written."""
    raw = getattr(connection, "_connection", connection)
    try:
        candidates = []
        for batch in batches:
            candidates += changed_rows(connection, region, batch)
        written = 0
        if candidates:
            with raw.cursor() as cursor:
                cursor.executemany(UPSERT_SQL, candidates)
                written = max(0, cursor.rowcount)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"rows": sum(len(values) for batch in batches for values in batch.rows.values()),
            "new_or_changed": len(candidates), "written": written}


def latest_french_date(connection: Any, region: str) -> str | None:
    row = connection.execute(LATEST_FRENCH_SQL, (region, SOURCE)).fetchone()
    connection.rollback()
    return None if row is None or row["latest"] is None else str(row["latest"])


def default_proxies(region: str) -> dict[str, Any]:
    """The ETF stand-ins, their fund histories stored in bronze first like the crisis windows'."""
    from .crisis_replay import fetch_adjusted_history

    def fetch_history(symbol: str, start: date, end: date) -> Mapping[str, float]:
        return fetch_adjusted_history(symbol, start_date=start, end_date=end, capture_feed=PROXY_CAPTURE_FEED)

    return fama_french.load_proxy_factors(region, fetch_history)


# ---------------------------------------------------------------- the run


def load_region(connection: Any, region: str, *, fetch: Callable[[str], Download] = download,
                etf_proxy: bool = False, proxies: Callable[[str], Mapping[str, Any]] | None = None,
                now: datetime | None = None) -> dict[str, Any]:
    """Fetch, capture, parse and store one region. Never raises for a fetch or parse problem: it is
    reported in the result's files; a database error rolls the region back and is raised."""
    if region not in fama_french.REGIONS:
        raise ValueError(f"Unknown factor region {region!r}")
    _, five_file, momentum_file = fama_french.REGIONS[region]
    files, batches = [], []
    for name, momentum in ((five_file, False), (momentum_file, True)):
        entry: dict[str, Any] = {"file": name}
        try:
            got = fetch(fama_french.BASE_URL + name)
            entry["captured"] = _capture(got, region, name)
            rows = parse_factor_file(csv_text(got.body), momentum=momentum)
            batch = Batch(rows, SOURCE, vintage_of(got),
                          got.requested_at.astimezone(timezone.utc).isoformat(timespec="seconds"))
            batches.append(batch)
            entry.update(vintage=batch.vintage, days=len(rows), first_date=min(rows), latest_date=max(rows))
        except Exception as error:  # noqa: BLE001 - one file never stops the other or the next region
            entry["error"] = f"{type(error).__name__}: {error}"[:300]
        files.append(entry)
    result: dict[str, Any] = {"region": region, "files": files, **store(connection, region, batches)}
    if etf_proxy:
        result["etf_proxy"] = _store_proxies(connection, region, proxies or default_proxies, now=now)
    return result


def _store_proxies(connection: Any, region: str, proxies: Callable[[str], Mapping[str, Any]], *,
                   now: datetime | None) -> dict[str, Any]:
    moment = (now or raw_capture.utc_now()).astimezone(timezone.utc)
    try:
        factors = dict(proxies(region).get("factors") or {})
    except Exception as error:  # noqa: BLE001 - the stand-ins are optional
        return {"error": f"{type(error).__name__}: {error}"[:300]}
    after = latest_french_date(connection, region)
    rows = {day: {f: v for f, v in values.items() if f in FACTORS}
            for day, values in factors.items() if after is None or day > after}
    rows = {day: values for day, values in rows.items() if values}
    batch = Batch(rows, PROXY_SOURCE, moment.date().isoformat(), moment.isoformat(timespec="seconds"))
    return {"after": after, "days": len(rows), **store(connection, region, [batch] if rows else [])}


def load(connection: Any, regions: Iterable[str] | None = None, *, fetch: Callable[[str], Download] = download,
         etf_proxy: bool = False, proxies: Callable[[str], Mapping[str, Any]] | None = None,
         record: Any = None, now: datetime | None = None) -> dict[str, Any]:
    """Every region in turn; one region's failure never stops the next."""
    if record is None:
        from . import lineage

        record = lineage.current()
    results, failed = [], []
    for region in list(regions or fama_french.REGIONS):
        try:
            result = load_region(connection, region, fetch=fetch, etf_proxy=etf_proxy, proxies=proxies, now=now)
        except Exception as error:  # noqa: BLE001
            if getattr(error, "sqlstate", None) == "42P01":
                raise  # the table went away: the caller reports it as held
            result = {"region": region, "error": f"{type(error).__name__}: {error}"[:500], "written": 0}
        if result.get("error") or any("error" in item for item in result.get("files", ())) \
                or "error" in (result.get("etf_proxy") or {}):
            failed.append(region)
        results.append(result)
    written = sum(int(item.get("written") or 0) + int((item.get("etf_proxy") or {}).get("written") or 0)
                  for item in results)
    record.output(TABLE, rows=written)
    return {"regions": results, "written": written, "failed": failed, "etf_proxy": bool(etf_proxy)}
