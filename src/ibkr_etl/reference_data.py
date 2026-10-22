from __future__ import annotations

import concurrent.futures
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import connect as database_connect


HKT = timezone(timedelta(hours=8), name="Asia/Hong_Kong")
TREASURY_XML_URL = "https://home.treasury.gov/sites/default/files/interest-rates/yield.xml"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
USER_AGENT = "IBKR-Portfolio-Analytics/2.0"


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


class HttpBody(bytes):
    """Response bytes that also carry the HTTP status and content type."""

    status: int | None = None
    content_type: str | None = None


def _get(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,application/xml,text/xml,*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        body = HttpBody(response.read())
        body.status = getattr(response, "status", None)
        headers = getattr(response, "headers", None)
        body.content_type = headers.get("Content-Type") if headers is not None else None
        return body


def load_reference_data(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 2, "risk_free": {}, "sectors": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {"version": 2, "risk_free": {}, "sectors": {}}
    data.setdefault("version", 1)
    data.setdefault("risk_free", {})
    data.setdefault("sectors", {})
    return data


def save_reference_data(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_treasury_date(value: str) -> date:
    normalized = value.strip()
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for date_format in ("%m-%d-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(normalized, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported Treasury date: {value}")


def _parse_usd_risk_free_xml(xml: bytes) -> tuple[str, float]:
    root = ET.fromstring(xml)
    observations: list[tuple[str, float]] = []
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1].upper()
        if local_name not in {"PROPERTIES", "G_NEW_DATE"}:
            continue
        values = {
            child.tag.rsplit("}", 1)[-1].upper(): (child.text or "").strip()
            for child in element.iter()
            if (child.text or "").strip()
        }
        date_text = values.get("NEW_DATE")
        rate_text = values.get("BC_3MONTH")
        if not date_text or not rate_text:
            continue
        try:
            observation_date = _parse_treasury_date(date_text)
            annual_percent = float(rate_text)
        except (ValueError, TypeError):
            continue
        observations.append((observation_date.isoformat(), annual_percent))
    if not observations:
        raise RuntimeError("Treasury XML did not contain a 3-month par yield")
    return max(observations, key=lambda item: item[0])


def fetch_usd_risk_free_rate(timeout: int = 30) -> dict[str, Any]:
    observation_date, annual_percent = _parse_usd_risk_free_xml(
        _get(TREASURY_XML_URL, timeout=timeout)
    )
    return {
        "currency": "USD",
        "tenor": "3-month",
        "annual_rate": annual_percent / 100.0,
        "annual_percent": annual_percent,
        "observation_date": observation_date,
        "source": "U.S. Treasury Daily Treasury Par Yield Curve Rates",
        "source_url": TREASURY_XML_URL,
        "fetched_at": datetime.now(HKT).isoformat(timespec="seconds"),
    }


def _candidate_symbols(symbol: str, exchange: str, currency: str) -> list[str]:
    raw = symbol.strip().upper()
    exchange = exchange.strip().upper()
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
    return list(dict.fromkeys(candidates))


def _lookup_sector(holding: dict[str, str], timeout: int) -> dict[str, Any]:
    candidates = _candidate_symbols(
        holding["symbol"], holding["listing_exchange"], holding["currency"]
    )
    normalized_candidates = {candidate.upper() for candidate in candidates}
    last_error = "No matching classified quote"
    for candidate in candidates:
        query = urllib.parse.urlencode(
            {"q": candidate, "quotesCount": "10", "newsCount": "0", "enableFuzzyQuery": "false"}
        )
        try:
            payload = json.loads(_get(f"{YAHOO_SEARCH_URL}?{query}", timeout=timeout))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            last_error = type(error).__name__
            continue
        quotes = payload.get("quotes") or []
        quotes.sort(
            key=lambda quote: (
                str(quote.get("symbol", "")).upper() not in normalized_candidates,
                not bool(quote.get("sector")),
            )
        )
        for quote in quotes:
            if str(quote.get("symbol") or "").upper() not in normalized_candidates:
                continue
            sector = str(quote.get("sector") or "").strip()
            if not sector:
                continue
            return {
                "conid": holding["conid"],
                "symbol": holding["symbol"],
                "lookup_symbol": str(quote.get("symbol") or candidate),
                "sector": sector,
                "industry": str(quote.get("industry") or "").strip(),
                "country": str(quote.get("country") or "").strip(),
                "country_checked": True,
                "exchange": str(quote.get("exchange") or "").strip(),
                "status": "classified",
                "source": "Yahoo Finance search profile",
                "source_url": YAHOO_SEARCH_URL,
                "checked_at": datetime.now(HKT).isoformat(timespec="seconds"),
            }
    return {
        "conid": holding["conid"],
        "symbol": holding["symbol"],
        "sector": "",
        "industry": "",
        "country": "",
        "country_checked": True,
        "status": "unclassified",
        "error": last_error,
        "source": "Yahoo Finance search profile",
        "source_url": YAHOO_SEARCH_URL,
        "checked_at": datetime.now(HKT).isoformat(timespec="seconds"),
    }


def _latest_holdings(
    database_path: Path,
    database_url: str = "",
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
) -> list[dict[str, str]]:
    connection = database_connect(database_path, database_url, tenant_id)
    try:
        return [
            {
                "conid": row["conid"],
                "symbol": row["symbol"] or row["conid"],
                "listing_exchange": row["listing_exchange"] or "",
                "currency": row["currency"] or "",
            }
            for row in connection.execute(
                """
                SELECT DISTINCT p.conid, i.symbol, i.listing_exchange, p.currency
                FROM v_latest_positions AS p
                LEFT JOIN instruments AS i ON i.conid = p.conid
                ORDER BY p.conid
                """
            )
        ]
    finally:
        connection.close()


def refresh_reference_data(
    database_path: Path,
    cache_path: Path,
    *,
    max_sector_lookups: int = 25,
    workers: int = 6,
    timeout: int = 20,
    force_risk_free: bool = False,
    database_url: str = "",
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
) -> dict[str, Any]:
    cache = load_reference_data(cache_path)
    warnings: list[str] = []
    risk_free_updated = False
    today = datetime.now(HKT).date().isoformat()
    usd = cache["risk_free"].get("USD") or {}
    if force_risk_free or not str(usd.get("fetched_at", "")).startswith(today):
        try:
            cache["risk_free"]["USD"] = fetch_usd_risk_free_rate(timeout=max(timeout, 30))
            risk_free_updated = True
        except Exception as error:
            warnings.append(f"Risk-free refresh failed; cached value retained ({type(error).__name__})")

    holdings = _latest_holdings(database_path, database_url, tenant_id)
    missing: list[dict[str, str]] = []
    stale_unclassified: list[dict[str, str]] = []
    retry_before = (date.fromisoformat(today) - timedelta(days=30)).isoformat()
    for holding in holdings:
        cached = cache["sectors"].get(holding["conid"])
        if (
            cached
            and cached.get("symbol") == holding["symbol"]
            and cached.get("sector")
            and cached.get("country_checked")
        ):
            continue
        if not cached or cached.get("symbol") != holding["symbol"]:
            missing.append(holding)
            continue
        if cached.get("sector") and not cached.get("country_checked"):
            missing.append(holding)
            continue
        checked_date = str(cached.get("checked_at") or "")[:10]
        if not checked_date or checked_date <= retry_before:
            stale_unclassified.append(holding)
    pending = missing + stale_unclassified
    selected = pending[: max(0, max_sector_lookups)]
    classified = 0
    if selected:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 10))) as executor:
            futures = {
                executor.submit(_lookup_sector, holding, timeout): holding for holding in selected
            }
            for future in concurrent.futures.as_completed(futures):
                holding = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    result = {
                        **holding,
                        "sector": "",
                        "industry": "",
                        "status": "unclassified",
                        "error": type(error).__name__,
                        "checked_at": datetime.now(HKT).isoformat(timespec="seconds"),
                    }
                cache["sectors"][holding["conid"]] = result
                classified += bool(result.get("sector"))
    cache["version"] = 2
    cache["updated_at"] = datetime.now(HKT).isoformat(timespec="seconds")
    save_reference_data(cache_path, cache)
    current_conids = {holding["conid"] for holding in holdings}
    covered = sum(
        bool(cache["sectors"].get(conid, {}).get("sector")) for conid in current_conids
    )
    return {
        "risk_free_updated": risk_free_updated,
        "risk_free_available": bool(cache["risk_free"].get("USD", {}).get("annual_rate") is not None),
        "sector_lookups_attempted": len(selected),
        "sector_lookups_classified": classified,
        "sector_coverage": covered,
        "current_holdings": len(current_conids),
        "remaining_sector_lookups": max(0, len(pending) - len(selected)),
        "warnings": warnings,
    }
