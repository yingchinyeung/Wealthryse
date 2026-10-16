from __future__ import annotations

import concurrent.futures
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Any, Callable, Mapping

from . import raw_capture
from .util import canonical_json, sha256_text, utc_now_iso


YAHOO_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
YAHOO_PROVIDER = "Yahoo Finance search profile"

# A historical symbol can disappear from live quote services after a corporate
# action. Overrides are therefore keyed by IBKR conid, not symbol alone, and
# cite a first-party or regulatory source. They remain provisional/reporting-
# blocked just like Yahoo classifications until a licensed provider replaces
# the development feed.
VERIFIED_HISTORICAL_OVERRIDES: dict[str, dict[str, str]] = {
    "10814": {
        "symbol": "OMI",
        "lookup_symbol": "OMI",
        "sector": "Healthcare",
        "industry": "Medical Distribution",
        "exchange": "NYQ",
        "source_url": (
            "https://www.sec.gov/Archives/edgar/data/75252/"
            "000155837025001940/omi-20241231x10k.htm"
        ),
        "evidence": "verified_historical_override:sec-2024-10-k",
    },
    # Shin Kong Financial Holding (TWSE 2888) was delisted on 24 July 2025
    # after its merger with Taishin Financial Holding.  Yahoo no longer
    # returns a live profile for the exact historical ticker.  TWSE identifies
    # both the issuer/code and its Finance and Insurance industry; the override
    # is deliberately bound to the exact IBKR conid observed in U16088606.
    "38768970": {
        "symbol": "2888.OLD",
        "lookup_symbol": "2888.TW",
        "sector": "Financial Services",
        "industry": "Finance and Insurance",
        "exchange": "TWSE",
        "source_url": (
            "https://www.twse.com.tw/staticFiles/news/news/tsecnews/"
            "8a8216d697769dcf0197b0abcc8900e4.pdf"
        ),
        "evidence": "verified_historical_override:twse-delisting-2025-06-27",
    },
    # Skydance distributed one SKYD WS warrant per SKYD Class B share (record
    # date 5 Oct 2026); regular-way NYSE trading began 14 Oct 2026, before Yahoo
    # published a profile for the warrant.  Each warrant converts into one SKYD
    # share, so it carries the underlying's sector.  Owner-approved on
    # 15 Oct 2026 and bound to the exact IBKR conid observed in U16088571.
    # Yahoo still wins once it classifies the warrant itself.
    "931131905": {
        "symbol": "SKYD WS",
        "lookup_symbol": "SKYD",
        "sector": "Communication Services",
        "industry": "Entertainment",
        "exchange": "NYQ",
        "source_url": "https://infomemo.theocc.com/infomemos?number=59906",
        "evidence": "owner_approved_underlying_override:occ-memo-59906",
    },
}


def classification_region(row: Mapping[str, Any]) -> str:
    """Return a stable client-facing region from the security's listing venue.

    The current provisional classification store does not retain issuer domicile.
    Region therefore means *listing region*, derived from the IBKR/Yahoo exchange
    and currency fields.  This is deterministic, auditable, and avoids guessing
    an issuer's economic exposure.
    """

    exchange = str(
        row.get("listing_exchange") or row.get("exchange") or ""
    ).strip().upper()
    currency = str(row.get("currency") or "").strip().upper()
    if exchange in {
        "NYSE",
        "NYQ",
        "NASDAQ",
        "NMS",
        "NGM",
        "NCM",
        "ARCA",
        "ASE",
        "AMEX",
        "BATS",
    } or currency in {"USD", "CAD", "MXN"}:
        return "North America"
    if exchange in {"SEHK", "HKG", "HKSE", "TWSE", "TWO", "TSEJ", "JPX", "TYO", "KRX", "KSC", "KOE", "ASX"} or currency in {
        "HKD",
        "CNY",
        "CNH",
        "TWD",
        "JPY",
        "KRW",
        "AUD",
        "NZD",
        "SGD",
    }:
        return "Asia Pacific"
    if exchange in {"LSE", "LON", "XETRA", "GER", "FRA", "PAR", "AMS", "MIL", "SWX"} or currency in {
        "GBP",
        "EUR",
        "CHF",
        "SEK",
        "NOK",
        "DKK",
    }:
        return "Europe"
    if currency in {"BRL", "ARS", "CLP", "COP", "PEN"}:
        return "Latin America"
    if currency in {"AED", "ILS", "SAR", "ZAR"}:
        return "Middle East & Africa"
    return "Other"


def candidate_symbols(symbol: str, listing_exchange: str, currency: str) -> list[str]:
    raw = symbol.strip().upper().replace(" ", "-")
    # IBKR can retain bookkeeping aliases after a corporate action or while a
    # receivable is outstanding.  Yahoo knows the listed underlying, not the
    # synthetic ``.REC``/``.OLD`` alias.  Keep the IBKR symbol in our audit
    # record while limiting external lookup to the normalized underlying.
    for bookkeeping_suffix in (".REC", ".OLD"):
        if raw.endswith(bookkeeping_suffix):
            raw = raw[: -len(bookkeeping_suffix)]
            break
    exchange = listing_exchange.strip().upper()
    currency = currency.strip().upper()
    candidates: list[str] = []
    if exchange == "SEHK" or currency == "HKD":
        numeric = raw.lstrip("0") or "0"
        candidates.append(f"{numeric.zfill(4)}.HK")
    elif exchange == "TSEJ" or currency == "JPY":
        candidates.append(f"{raw}.T")
    elif exchange == "ASX" or currency == "AUD":
        candidates.append(f"{raw}.AX")
    elif exchange == "KRX" or currency == "KRW":
        candidates.extend((f"{raw.zfill(6)}.KS", f"{raw.zfill(6)}.KQ"))
    elif exchange in {"TWSE", "CORPACT"} or currency == "TWD":
        candidates.extend((f"{raw}.TW", f"{raw}.TWO"))
    elif exchange == "LSE" or currency == "GBP":
        candidates.append(f"{raw}.L")
    candidates.append(raw)
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _fetch_yahoo_search(symbol: str, timeout: int) -> Mapping[str, Any]:
    query = urllib.parse.urlencode(
        {
            "q": symbol,
            "quotesCount": "10",
            "newsCount": "0",
            "enableFuzzyQuery": "false",
        }
    )
    url = f"{YAHOO_SEARCH_URL}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "IBKR-Portfolio-Platform-Staging/2.0",
        },
    )
    requested_at = raw_capture.utc_now()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        status = getattr(response, "status", None)
    raw_capture.capture_http("yahoo-sector-search", YAHOO_PROVIDER, url=url, body=body,
                             key=symbol, status=status, extension="json",
                             requested_at=requested_at)
    return json.loads(body.decode("utf-8"))


def _fetch_yfinance_profile(symbol: str) -> Mapping[str, Any]:
    import yfinance as yf

    requested_at = raw_capture.utc_now()
    profile = yf.Ticker(symbol).get_info()
    raw_capture.capture_document("yahoo-sector-profile", "Yahoo Finance via yfinance",
                                 payload=profile, request={"symbol": symbol, "method": "get_info"},
                                 key=raw_capture.request_key(symbol, "info"), requested_at=requested_at)
    return profile


def lookup_classification(
    holding: Mapping[str, Any],
    *,
    timeout: int = 20,
    fetcher: Callable[[str, int], Mapping[str, Any]] = _fetch_yahoo_search,
    profile_fetcher: Callable[[str], Mapping[str, Any]] = _fetch_yfinance_profile,
) -> dict[str, Any]:
    candidates = candidate_symbols(
        str(holding.get("symbol") or ""),
        str(holding.get("listing_exchange") or ""),
        str(holding.get("currency") or ""),
    )
    normalized = {symbol.upper() for symbol in candidates}
    last_error = "no_matching_classified_quote"
    for candidate in candidates:
        try:
            payload = fetcher(candidate, timeout)
        except Exception as error:
            last_error = type(error).__name__
            continue
        quotes = list(payload.get("quotes") or [])
        quotes.sort(
            key=lambda quote: (
                str(quote.get("symbol") or "").upper() not in normalized,
                not bool(quote.get("sector")),
            )
        )
        for quote in quotes:
            lookup_symbol = str(quote.get("symbol") or "").upper()
            sector = str(quote.get("sector") or "").strip()
            if lookup_symbol not in normalized or not sector:
                continue
            return {
                "conid": str(holding["conid"]),
                "symbol": str(holding.get("symbol") or holding["conid"]),
                "lookup_symbol": lookup_symbol,
                "sector": sector,
                "industry": str(quote.get("industry") or "").strip(),
                "exchange": str(quote.get("exchange") or "").strip(),
                "status": "classified",
                "quality_status": "warn",
                "license_status": "restricted",
                "source_url": YAHOO_SEARCH_URL,
                "response_sha256": sha256_text(canonical_json(dict(quote))),
                "error_code": None,
                "checked_at": utc_now_iso(),
            }
    override = VERIFIED_HISTORICAL_OVERRIDES.get(str(holding["conid"]))
    if override is not None:
        return {
            "conid": str(holding["conid"]),
            "symbol": override["symbol"],
            "lookup_symbol": override["lookup_symbol"],
            "sector": override["sector"],
            "industry": override["industry"],
            "exchange": override["exchange"],
            "status": "classified",
            "quality_status": "warn",
            "license_status": "restricted",
            "source_url": override["source_url"],
            "response_sha256": sha256_text(canonical_json(override)),
            "error_code": override["evidence"],
            "checked_at": utc_now_iso(),
        }
    for candidate in candidates:
        try:
            profile = dict(profile_fetcher(candidate))
        except Exception as error:
            last_error = type(error).__name__
            continue
        sector = str(profile.get("sector") or "").strip()
        profile_symbol = str(profile.get("symbol") or candidate).upper()
        if not sector or profile_symbol not in normalized:
            continue
        evidence = {
            key: profile.get(key)
            for key in ("symbol", "longName", "sector", "industry", "exchange")
        }
        return {
            "conid": str(holding["conid"]),
            "symbol": str(holding.get("symbol") or holding["conid"]),
            "lookup_symbol": profile_symbol,
            "sector": sector,
            "industry": str(profile.get("industry") or "").strip(),
            "exchange": str(profile.get("exchange") or "").strip(),
            "status": "classified",
            "quality_status": "warn",
            "license_status": "restricted",
            "source_url": f"yfinance:TICKER({candidate}):get_info",
            "response_sha256": sha256_text(canonical_json(evidence)),
            "error_code": "yfinance_profile_fallback",
            "checked_at": utc_now_iso(),
        }
    return {
        "conid": str(holding["conid"]),
        "symbol": str(holding.get("symbol") or holding["conid"]),
        "lookup_symbol": None,
        "sector": None,
        "industry": None,
        "exchange": None,
        "status": "unclassified",
        "quality_status": "warn",
        "license_status": "restricted",
        "source_url": YAHOO_SEARCH_URL,
        "response_sha256": None,
        "error_code": last_error,
        "checked_at": utc_now_iso(),
    }


def aggregate_sector_allocation(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    detail_totals: dict[tuple[str, str, str], float] = defaultdict(float)
    detail_counts: dict[tuple[str, str, str], int] = defaultdict(int)
    classifications: list[dict[str, Any]] = []
    gross = classified_gross = 0.0
    unclassified: list[str] = []
    checked_at: list[str] = []
    for row in rows:
        market_value = abs(float(row.get("market_value_text") or 0.0))
        gross += market_value
        sector = str(row.get("sector") or "").strip()
        if sector:
            region = classification_region(row)
            sub_sector = str(row.get("industry") or "Other").strip() or "Other"
            totals[sector] += market_value
            counts[sector] += 1
            detail_key = (region, sector, sub_sector)
            detail_totals[detail_key] += market_value
            detail_counts[detail_key] += 1
            classified_gross += market_value
            classifications.append(
                {
                    "conid": str(row.get("conid") or ""),
                    "symbol": str(row.get("symbol") or row.get("conid") or "Unknown"),
                    "region": region,
                    "sector": sector,
                    "sub_sector": sub_sector,
                    "market_value": market_value,
                    "weight": None,
                }
            )
            if row.get("checked_at"):
                checked_at.append(str(row["checked_at"]))
        else:
            unclassified.append(str(row.get("symbol") or row.get("conid") or "Unknown"))
    allocation = [
        {
            "sector": sector,
            "market_value": value,
            "weight": value / gross if gross else None,
            "holdings": counts[sector],
        }
        for sector, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]
    allocation_detail = [
        {
            "region": key[0],
            "sector": key[1],
            "sub_sector": key[2],
            "market_value": value,
            "weight": value / gross if gross else None,
            "holdings": detail_counts[key],
        }
        for key, value in sorted(
            detail_totals.items(), key=lambda item: item[1], reverse=True
        )
    ]
    for classification in classifications:
        classification["weight"] = (
            classification["market_value"] / gross if gross else None
        )
    classifications.sort(key=lambda item: item["market_value"], reverse=True)
    return {
        "available": bool(rows) and not unclassified,
        "allocation": allocation,
        "allocation_detail": allocation_detail,
        "classifications": classifications,
        "gross_market_value": gross,
        "classified_market_value": classified_gross,
        "classification_coverage_pct": 100.0 * classified_gross / gross if gross else 0.0,
        "holdings": len(rows),
        "classified_holdings": len(rows) - len(unclassified),
        "unclassified_symbols": sorted(unclassified),
        "classification_checked_at": max(checked_at) if checked_at else None,
        "provider": YAHOO_PROVIDER,
        "license_status": "restricted",
        "publication_allowed": False,
    }


def _classify_and_store(
    connection,
    holdings: list[dict[str, Any]],
    *,
    workers: int,
    timeout: int,
    fetcher: Callable[[str, int], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(int(workers), 6))
    ) as executor:
        futures = [
            executor.submit(
                lookup_classification,
                holding,
                timeout=timeout,
                fetcher=fetcher,
            )
            for holding in holdings
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    for result in results:
        connection.execute(
            """
            INSERT INTO instrument_classifications(
                conid, provider, symbol, lookup_symbol, sector, industry,
                exchange, status, quality_status, license_status, source_url,
                response_sha256, error_code, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, conid, provider) DO UPDATE SET
                symbol = excluded.symbol,
                lookup_symbol = excluded.lookup_symbol,
                sector = excluded.sector,
                industry = excluded.industry,
                exchange = excluded.exchange,
                status = excluded.status,
                quality_status = excluded.quality_status,
                license_status = excluded.license_status,
                source_url = excluded.source_url,
                response_sha256 = excluded.response_sha256,
                error_code = excluded.error_code,
                checked_at = excluded.checked_at
            """,
            (
                result["conid"],
                YAHOO_PROVIDER,
                result["symbol"],
                result["lookup_symbol"],
                result["sector"],
                result["industry"],
                result["exchange"],
                result["status"],
                result["quality_status"],
                result["license_status"],
                result["source_url"],
                result["response_sha256"],
                result["error_code"],
                result["checked_at"],
            ),
        )
    connection.commit()
    return results


def refresh_sector_classifications(
    connection,
    *,
    subject_id: str,
    as_of: str,
    workers: int = 4,
    timeout: int = 20,
    fetcher: Callable[[str, int], Mapping[str, Any]] = _fetch_yahoo_search,
) -> dict[str, Any]:
    unified = not connection.__class__.__module__.startswith("sqlite3") or connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = 'subject_position_series'"
    ).fetchone() is not None
    relation = "subject_position_series" if unified else "position_snapshots"
    subject_column = "subject_id" if unified else "account_id"
    position_date_row = connection.execute(
        f"SELECT MAX(snapshot_date) AS latest FROM {relation} WHERE {subject_column} = ? AND snapshot_date <= ?",
        (subject_id, as_of),
    ).fetchone()
    position_date = position_date_row["latest"] if position_date_row else None
    if position_date is None:
        raise ValueError("No positions are available on or before the requested date")
    holdings = [
        dict(row)
        for row in connection.execute(
            """
            SELECT DISTINCT p.conid, COALESCE(i.symbol, p.conid) AS symbol,
                   COALESCE(i.listing_exchange, '') AS listing_exchange,
                   COALESCE(p.currency, i.currency, '') AS currency
            FROM {relation} p
            LEFT JOIN instruments i ON i.conid = p.conid
            WHERE p.{subject_column} = ? AND p.snapshot_date = ?
              AND COALESCE(NULLIF(p.quantity_text, ''), '0')::numeric <> 0
            ORDER BY p.conid
            """.format(relation=relation, subject_column=subject_column),
            (subject_id, position_date),
        ).fetchall()
    ]
    results = _classify_and_store(
        connection,
        holdings,
        workers=workers,
        timeout=timeout,
        fetcher=fetcher,
    )
    classified = sum(result["status"] == "classified" for result in results)
    return {
        "subject_id": subject_id,
        "position_date": str(position_date),
        "provider": YAHOO_PROVIDER,
        "license_status": "restricted",
        "tickers_transmitted": len(holdings),
        "classified": classified,
        "unclassified": len(results) - classified,
        "unclassified_symbols": sorted(
            result["symbol"] for result in results if result["status"] != "classified"
        ),
        "unclassified_contracts": sorted(
            (
                {
                    "conid": result["conid"],
                    "symbol": result["symbol"],
                    "error_code": result["error_code"],
                }
                for result in results
                if result["status"] != "classified"
            ),
            key=lambda item: (item["symbol"], item["conid"]),
        ),
    }


def refresh_sector_classifications_range(
    connection,
    *,
    subject_id: str,
    start_date: str,
    end_date: str,
    workers: int = 4,
    timeout: int = 20,
    fetcher: Callable[[str, int], Mapping[str, Any]] = _fetch_yahoo_search,
) -> dict[str, Any]:
    """Classify every previously unclassified holding seen in a date range."""

    unified = not connection.__class__.__module__.startswith("sqlite3") or connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = 'subject_position_series'"
    ).fetchone() is not None
    relation = "subject_position_series" if unified else "position_snapshots"
    subject_column = "subject_id" if unified else "account_id"
    all_rows = connection.execute(
        """
        SELECT DISTINCT p.conid, COALESCE(i.symbol, p.conid) AS symbol,
               COALESCE(i.listing_exchange, '') AS listing_exchange,
               COALESCE(p.currency, i.currency, '') AS currency,
               CASE WHEN existing.status = 'classified' THEN 1 ELSE 0 END AS classified
        FROM {relation} p
        LEFT JOIN instruments i ON i.conid = p.conid
        LEFT JOIN instrument_classifications existing
          ON existing.conid = p.conid AND existing.provider = ?
        WHERE p.{subject_column} = ? AND p.snapshot_date BETWEEN ? AND ?
          AND CAST(COALESCE(NULLIF(p.quantity_text, ''), '0') AS NUMERIC) <> 0
        ORDER BY p.conid
        """.format(relation=relation, subject_column=subject_column),
        (YAHOO_PROVIDER, subject_id, start_date, end_date),
    ).fetchall()
    holdings = [dict(row) for row in all_rows if not bool(row["classified"])]
    results = _classify_and_store(
        connection,
        holdings,
        workers=workers,
        timeout=timeout,
        fetcher=fetcher,
    )
    classified = sum(result["status"] == "classified" for result in results)
    return {
        "subject_id": subject_id,
        "start_date": start_date,
        "end_date": end_date,
        "provider": YAHOO_PROVIDER,
        "license_status": "restricted",
        "historical_unique_holdings": len(all_rows),
        "previously_classified": len(all_rows) - len(holdings),
        "tickers_transmitted": len(holdings),
        "classified": classified,
        "unclassified": len(results) - classified,
        "unclassified_symbols": sorted(
            result["symbol"] for result in results if result["status"] != "classified"
        ),
        "unclassified_contracts": sorted(
            (
                {
                    "conid": result["conid"],
                    "symbol": result["symbol"],
                    "error_code": result["error_code"],
                }
                for result in results
                if result["status"] != "classified"
            ),
            key=lambda item: (item["symbol"], item["conid"]),
        ),
    }
