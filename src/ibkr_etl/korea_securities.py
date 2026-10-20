"""English names and factsheet sectors for KRX listings held through Kiwoom.

Kiwoom names a holding in Korean (SK하이닉스), which the factsheet fonts cannot
draw, and Yahoo's sector labels are not the ones the Korea iStrategy factsheet
uses. This is the reference for every KRX code the account has held: the
English name and the sector and sub-sector as the owner's 31 Aug 2026
factsheet prints them. A KRX holding that is missing here stops the render and
names the code, so a Korean name or a guessed sector never reaches a factsheet.
"""
from __future__ import annotations

from typing import Any, Mapping

REGION = "South Korea"

IT, IND, MAT, FIN, HC, CD, TEL, EN, UT = (
    "Information Technology", "Industrials", "Materials", "Financials", "Health Care",
    "Consumer Discretionary", "Telecommunication Services", "Energy", "Utilities")
SEMIS, HARDWARE, SOFTWARE = ("Semiconductors & Semiconductor Equipment", "Technology Hardware & Equipment",
                             "Software & Services")
PHARMA = "Pharmaceuticals, Biotechnology & Life Sciences"

# KRX code: (English name, sector, sub-sector). The first 25 are the 31 Aug 2026 holdings, whose
# labels reproduce that factsheet's sector table; the rest are the account's other holdings.
SECURITIES: dict[str, tuple[str, str, str]] = {
    "000660": ("SK hynix Inc.", IT, SEMIS),
    "005930": ("Samsung Electronics Co., Ltd.", IT, HARDWARE),
    "329180": ("HD Hyundai Heavy Industries Co.,Ltd.", IND, "Machinery"),
    "020150": ("Iljin Materials Co Ltd", IND, "Capital Goods"),
    "353200": ("DAEDUCK ELECTRONICS Co., Ltd.", IT, HARDWARE),
    "086790": ("Hana Financial Group Inc.", FIN, "Banks"),
    "023530": ("Lotte Shopping Co., Ltd.", CD, "Retailing"),
    "012630": ("HDC Holdings Co., Ltd.", IND, "Capital Goods"),
    "055550": ("Shinhan Financial Group Co., Ltd.", FIN, "Banks"),
    "000210": ("DL Holdings Co., Ltd.", MAT, "Materials"),
    "000270": ("Kia Corporation", CD, "Automobiles & Components"),
    "001060": ("JW Pharmaceutical Corporation", HC, PHARMA),
    "004000": ("Lotte Fine Chemical Co., Ltd.", MAT, "Materials"),
    "036570": ("NCSOFT Corporation", TEL, "Entertainment"),
    "267250": ("HD Hyundai Co., Ltd.", EN, "Energy"),
    "027410": ("BGF Co., Ltd.", MAT, "Materials"),
    "068270": ("Celltrion, Inc.", HC, PHARMA),
    "035420": ("NAVER Corporation", TEL, "Media & Entertainment"),
    "103140": ("Poongsan Corporation", MAT, "Metals & Mining"),
    "034730": ("SK Inc.", IND, "Industrial Conglomerates"),
    "000720": ("Hyundai Engineering & Construction Co., Ltd.", IND, "Capital Goods"),
    "018260": ("Samsung SDS Co., Ltd.", IT, SOFTWARE),
    "005490": ("POSCO Holdings Inc.", MAT, "Materials"),
    "017670": ("SK Telecom Co., Ltd.", TEL, "Telecommunication Services"),
    "015760": ("Korea Electric Power Corporation", UT, "Electric Utilities"),
    "000100": ("Yuhan Corporation", HC, PHARMA),
    "000990": ("DB HiTek Co., Ltd.", IT, SEMIS),
    "003550": ("LG Corp.", IND, "Industrial Conglomerates"),
    "004020": ("Hyundai Steel Company", MAT, "Metals & Mining"),
    "005380": ("Hyundai Motor Company", CD, "Automobiles & Components"),
    "006360": ("GS Engineering & Construction Corp.", IND, "Capital Goods"),
    "008770": ("Hotel Shilla Co., Ltd.", CD, "Retailing"),
    "009150": ("Samsung Electro-Mechanics Co., Ltd.", IT, HARDWARE),
    "009240": ("Hanssem Co., Ltd.", CD, "Consumer Durables & Apparel"),
    "012750": ("S-1 Corporation", IND, "Commercial & Professional Services"),
    "024110": ("Industrial Bank of Korea", FIN, "Banks"),
    "028260": ("Samsung C&T Corporation", IND, "Industrial Conglomerates"),
    "032640": ("LG Uplus Corp.", TEL, "Telecommunication Services"),
    "034220": ("LG Display Co., Ltd.", IT, HARDWARE),
    "035720": ("Kakao Corp.", TEL, "Media & Entertainment"),
    "047040": ("Daewoo Engineering & Construction Co., Ltd.", IND, "Capital Goods"),
    "066570": ("LG Electronics Inc.", CD, "Consumer Durables & Apparel"),
    "069620": ("Daewoong Pharmaceutical Co., Ltd.", HC, PHARMA),
    "079160": ("CJ CGV Co., Ltd.", TEL, "Entertainment"),
    "096770": ("SK Innovation Co., Ltd.", EN, "Energy"),
    "105560": ("KB Financial Group Inc.", FIN, "Banks"),
    "138930": ("BNK Financial Group Inc.", FIN, "Banks"),
    "196170": ("Alteogen Inc.", HC, PHARMA),
    "251270": ("Netmarble Corporation", TEL, "Entertainment"),
    "316140": ("Woori Financial Group Inc.", FIN, "Banks"),
}


class KoreaSecurityError(ValueError):
    pass


def is_krx(holding: Mapping[str, Any]) -> bool:
    currency = str(holding.get("native_currency") or holding.get("currency") or "").strip().upper()
    symbol = str(holding.get("symbol") or "").strip()
    return currency == "KRW" and len(symbol) == 6 and symbol.isalnum()


def lookup(holding: Mapping[str, Any]) -> tuple[str, str, str]:
    """(English name, sector, sub-sector) for a KRX holding, or an error naming the code."""
    symbol = str(holding.get("symbol") or "").strip().upper()
    try:
        return SECURITIES[symbol]
    except KeyError:
        raise KoreaSecurityError(
            f"No English name and sector for KRX code {symbol}; add it to ibkr_etl/korea_securities.py") from None
