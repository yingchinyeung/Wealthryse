"""Factsheet names and sectors for the U.S. stocks the U.S. iStrategy account holds through Kiwoom.

Kiwoom names a U.S. holding by its ticker only. Yahoo is the source of truth, as
for every other account: the sector job stores Yahoo's English company name
(long_name) and its sector and industry with each classification. Yahoo's
labels are not the ones the U.S. iStrategy factsheet prints, so Yahoo's
industry is mapped to the sector and sub-sector of the owner's 31 Aug 2026
factsheet, and the industries the account held in other months follow the same
groups. Yahoo's industry decides each holding's row: the owner chose Yahoo's
current label for QXO (Industrial Distribution, so Capital Goods) over the
Software & Services row the 31 Aug factsheet printed. Every holding prints
"United States" as its region. A holding with no Yahoo name, or an industry the
map does not know, stops the render and names the ticker, so a guessed name or
sector never reaches a factsheet.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

REGION = "United States"

IT, IND, FIN, HC, CD, CS, TEL, EN, UT, MAT = (
    "Information Technology", "Industrials", "Financials", "Health Care", "Consumer Discretionary",
    "Consumer Staples", "Telecommunication Services", "Energy", "Utilities", "Materials")
HARDWARE = "Technology Hardware & Equipment"
SEMIS = "Semiconductors & Semiconductor Equipment"
SOFTWARE = "Software & Services"
PHARMA = "Pharmaceuticals, Biotechnology & Life Sciences"
HC_EQUIPMENT = "Health Care Equipment & Services"
DIVERSIFIED = "Diversified Financials"
MEDIA_ENT = "Media & Entertainment"

# Yahoo industry: (sector, sub-sector) as the U.S. iStrategy factsheet prints them.
INDUSTRIES: dict[str, tuple[str, str]] = {
    "Computer Hardware": (IT, HARDWARE),
    "Communication Equipment": (IT, HARDWARE),
    "Electronic Components": (IT, HARDWARE),
    "Consumer Electronics": (IT, HARDWARE),
    "Scientific & Technical Instruments": (IT, HARDWARE),
    "Semiconductors": (IT, SEMIS),
    "Semiconductor Equipment & Materials": (IT, SEMIS),
    "Software - Application": (IT, SOFTWARE),
    "Software - Infrastructure": (IT, SOFTWARE),
    "Information Technology Services": (IT, SOFTWARE),
    "Capital Markets": (FIN, DIVERSIFIED),
    "Credit Services": (FIN, DIVERSIFIED),
    "Financial Data & Stock Exchanges": (FIN, DIVERSIFIED),
    "Asset Management": (FIN, DIVERSIFIED),
    "Banks - Diversified": (FIN, "Banks"),
    "Banks - Regional": (FIN, "Banks"),
    "Biotechnology": (HC, PHARMA),
    "Drug Manufacturers - General": (HC, PHARMA),
    "Drug Manufacturers - Specialty & Generic": (HC, PHARMA),
    "Healthcare Plans": (HC, PHARMA),
    "Medical Devices": (HC, HC_EQUIPMENT),
    "Medical Instruments & Supplies": (HC, HC_EQUIPMENT),
    "Medical Care Facilities": (HC, HC_EQUIPMENT),
    "Diagnostics & Research": (HC, HC_EQUIPMENT),
    "Specialty Retail": (CD, "Retailing"),
    "Discount Stores": (CD, "Retailing"),
    "Apparel Retail": (CD, "Retailing"),
    "Internet Retail": (CD, "Retailing"),
    "Department Stores": (CD, "Retailing"),
    "Home Improvement Retail": (CD, "Retailing"),
    "Auto Parts": (CD, "Retailing"),
    "Restaurants": (CD, "Consumer Services"),
    "Travel Services": (CD, "Consumer Services"),
    "Lodging": (CD, "Consumer Services"),
    "Resorts & Casinos": (CD, "Consumer Services"),
    "Auto Manufacturers": (CD, "Automobiles & Components"),
    "Furnishings, Fixtures & Appliances": (CD, "Consumer Durables & Apparel"),
    "Publishing": (CD, "Media"),
    "Engineering & Construction": (IND, "Capital Goods"),
    "Aerospace & Defense": (IND, "Capital Goods"),
    "Specialty Industrial Machinery": (IND, "Capital Goods"),
    "Electrical Equipment & Parts": (IND, "Capital Goods"),
    "Industrial Distribution": (IND, "Capital Goods"),
    "Conglomerates": (IND, "Capital Goods"),
    "Specialty Business Services": (IND, "Commercial & Professional Services"),
    "Consulting Services": (IND, "Commercial & Professional Services"),
    "Airports & Air Services": (IND, "Transportation"),
    "Integrated Freight & Logistics": (IND, "Transportation"),
    "Entertainment": (TEL, MEDIA_ENT),
    "Electronic Gaming & Multimedia": (TEL, MEDIA_ENT),
    "Internet Content & Information": (TEL, MEDIA_ENT),
    "Telecom Services": (TEL, "Telecommunication Services"),
    "Oil & Gas Integrated": (EN, "Oil, Gas & Consumable Fuels"),
    "Oil & Gas E&P": (EN, "Oil, Gas & Consumable Fuels"),
    "Uranium": (EN, "Energy"),
    "Utilities - Regulated Electric": (UT, "Electric Utilities"),
    "Grocery Stores": (CS, "Food & Staples Retailing"),
    "Confectioners": (CS, "Food Beverage & Tobacco"),
    "Packaged Foods": (CS, "Food Beverage & Tobacco"),
    "Beverages - Non-Alcoholic": (CS, "Food Beverage & Tobacco"),
    "Household & Personal Products": (CS, "Household & Personal Products"),
    "Gold": (MAT, "Metals & Mining"),
    "Other Industrial Metals & Mining": (MAT, "Metals & Mining"),
}

class UsIstrategySecurityError(ValueError):
    pass


def lookup(holding: Mapping[str, Any], classification: Mapping[str, Any] | None) -> tuple[str, str, str]:
    """(English name, sector, sub-sector) for a U.S. holding from its Yahoo classification."""
    symbol = str(holding.get("symbol") or "").strip().upper()
    classification = classification or {}
    name = " ".join(str(classification.get("long_name") or "").split())
    if not name or not name.isascii():
        raise UsIstrategySecurityError(
            f"Yahoo has no English name stored for {symbol}; run the sector job for the account")
    # Yahoo's search writes some industries with a dash ("Banks—Diversified") where its profile
    # writes " - " ("Banks - Diversified"); the sector job stores whichever answered.
    industry = re.sub(r"\s*[–—]\s*", " - ", " ".join(str(classification.get("sub_sector") or "").split()))
    try:
        sector, sub_sector = INDUSTRIES[industry]
    except KeyError:
        raise UsIstrategySecurityError(
            f"No U.S. iStrategy sector for {symbol}, Yahoo industry {industry or 'missing'}; "
            "add it to ibkr_etl/us_istrategy_securities.py") from None
    return name, sector, sub_sector
