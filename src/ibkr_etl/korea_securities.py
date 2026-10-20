"""Factsheet names and sectors for KRX listings held through Kiwoom, from Yahoo.

Kiwoom names a holding in Korean (SK하이닉스), which the factsheet fonts cannot
draw. Yahoo is the source of truth, as for every other account: the sector job
stores Yahoo's English company name (long_name) and its sector and industry
with each classification. Yahoo's labels are not the ones the Korea iStrategy
factsheet prints, so Yahoo's industry is mapped to the sector and sub-sector of
the owner's 31 Aug 2026 factsheet; the map reproduces that month's sector table.
A KRX holding with no Yahoo name, or an industry the map does not know, stops
the render and names the code, so a Korean name or a guessed sector never
reaches a factsheet.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

REGION = "South Korea"

IT, IND, MAT, FIN, HC, CD, TEL, EN, UT = (
    "Information Technology", "Industrials", "Materials", "Financials", "Health Care",
    "Consumer Discretionary", "Telecommunication Services", "Energy", "Utilities")
HARDWARE = "Technology Hardware & Equipment"
PHARMA = "Pharmaceuticals, Biotechnology & Life Sciences"

# Yahoo industry: (sector, sub-sector) as the Korea iStrategy factsheet prints them.
INDUSTRIES: dict[str, tuple[str, str]] = {
    "Semiconductors": (IT, "Semiconductors & Semiconductor Equipment"),
    "Semiconductor Equipment & Materials": (IT, "Semiconductors & Semiconductor Equipment"),
    "Consumer Electronics": (IT, HARDWARE),
    "Electronic Components": (IT, HARDWARE),
    "Communication Equipment": (IT, HARDWARE),
    "Computer Hardware": (IT, HARDWARE),
    "Information Technology Services": (IT, "Software & Services"),
    "Software - Application": (IT, "Software & Services"),
    "Software - Infrastructure": (IT, "Software & Services"),
    "Aerospace & Defense": (IND, "Machinery"),
    "Specialty Industrial Machinery": (IND, "Machinery"),
    "Farm & Heavy Construction Machinery": (IND, "Machinery"),
    "Electrical Equipment & Parts": (IND, "Capital Goods"),
    "Engineering & Construction": (IND, "Capital Goods"),
    "Building Products & Equipment": (IND, "Capital Goods"),
    "Conglomerates": (IND, "Industrial Conglomerates"),
    "Security & Protection Services": (IND, "Commercial & Professional Services"),
    "Metal Fabrication": (MAT, "Metals & Mining"),
    "Other Industrial Metals & Mining": (MAT, "Metals & Mining"),
    "Chemicals": (MAT, "Materials"),
    "Specialty Chemicals": (MAT, "Materials"),
    "Steel": (MAT, "Materials"),
    "Banks - Regional": (FIN, "Banks"),
    "Banks - Diversified": (FIN, "Banks"),
    "Department Stores": (CD, "Retailing"),
    "Specialty Retail": (CD, "Retailing"),
    "Auto Manufacturers": (CD, "Automobiles & Components"),
    "Auto Parts": (CD, "Automobiles & Components"),
    "Furnishings, Fixtures & Appliances": (CD, "Consumer Durables & Apparel"),
    "Drug Manufacturers - Specialty & Generic": (HC, PHARMA),
    "Drug Manufacturers - General": (HC, PHARMA),
    "Biotechnology": (HC, PHARMA),
    "Electronic Gaming & Multimedia": (TEL, "Entertainment"),
    "Entertainment": (TEL, "Entertainment"),
    "Internet Content & Information": (TEL, "Media & Entertainment"),
    "Telecom Services": (TEL, "Telecommunication Services"),
    "Oil & Gas Refining & Marketing": (EN, "Energy"),
    "Utilities - Regulated Electric": (UT, "Electric Utilities"),
}


class KoreaSecurityError(ValueError):
    pass


def is_krx(holding: Mapping[str, Any]) -> bool:
    currency = str(holding.get("native_currency") or holding.get("currency") or "").strip().upper()
    symbol = str(holding.get("symbol") or "").strip()
    return currency == "KRW" and len(symbol) == 6 and symbol.isalnum()


def lookup(holding: Mapping[str, Any], classification: Mapping[str, Any] | None) -> tuple[str, str, str]:
    """(English name, sector, sub-sector) for a KRX holding from its Yahoo classification."""
    symbol = str(holding.get("symbol") or "").strip().upper()
    classification = classification or {}
    name = " ".join(str(classification.get("long_name") or "").split())
    if not name or not name.isascii():
        raise KoreaSecurityError(
            f"Yahoo has no English name stored for KRX code {symbol}; run the sector job for the account")
    # Yahoo's search writes some industries with a dash ("Banks—Regional") where its profile
    # writes " - " ("Banks - Regional"); the sector job stores whichever answered.
    industry = re.sub(r"\s*[\u2013\u2014]\s*", " - ", " ".join(str(classification.get("sub_sector") or "").split()))
    try:
        sector, sub_sector = INDUSTRIES[industry]
    except KeyError:
        raise KoreaSecurityError(
            f"No Korea iStrategy sector for KRX code {symbol}, Yahoo industry {industry or 'missing'}; "
            "add it to ibkr_etl/korea_securities.py") from None
    return name, sector, sub_sector
