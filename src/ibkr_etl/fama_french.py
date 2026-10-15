"""Daily Fama-French factor returns from Kenneth French's data library.

The library publishes free daily five-factor (Mkt-RF, SMB, HML, RMW, CMA, RF)
and momentum files as zipped CSVs, in percent. They are downloaded on demand,
parsed into decimal returns and kept in memory for a day, since the library is
updated about once a month. Nothing is stored in the database.
"""
from __future__ import annotations

import io
import threading
import time
import urllib.request
import zipfile
from typing import Any

BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
REGIONS = {
    "us": ("United States", "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip", "F-F_Momentum_Factor_daily_CSV.zip"),
    "developed": ("Developed markets", "Developed_5_Factors_Daily_CSV.zip", "Developed_Mom_Factor_Daily_CSV.zip"),
    "asia_pacific_ex_japan": (
        "Asia Pacific ex Japan",
        "Asia_Pacific_ex_Japan_5_Factors_Daily_CSV.zip",
        "Asia_Pacific_ex_Japan_MOM_Factor_Daily_CSV.zip",
    ),
}
DEFAULT_REGION = "developed"
CACHE_SECONDS = 24 * 3600
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


class FactorDataError(RuntimeError):
    """The factor library could not be reached or read."""


def parse_french_csv(text: str, *, single_column: str | None = None) -> dict[str, dict[str, float]]:
    """Daily rows of a French data-library CSV as ``{ISO date: {factor: decimal}}``.

    The files start with free-text notes, then a header row whose first cell is
    empty, then rows keyed by an eight-digit date. Monthly or annual sections
    (six- or four-digit keys) and the trailing copyright line are skipped.
    ``single_column`` renames a one-factor file's column (the momentum files
    call it ``Mom`` or ``WML``).
    """

    header: list[str] | None = None
    rows: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split(",")]
        if len(cells) < 2:
            continue
        if cells[0] == "" and any(cells[1:]):
            if header is None:
                header = cells[1:]
            continue
        key = cells[0]
        if header is None or len(key) != 8 or not key.isdigit():
            continue
        values: dict[str, float] = {}
        for name, raw in zip(header, cells[1:]):
            try:
                value = float(raw)
            except ValueError:
                continue
            if value <= -99.99:  # the library's missing-value marker
                continue
            values[single_column if single_column and len(header) == 1 else name] = value / 100.0
        if values:
            rows[f"{key[:4]}-{key[4:6]}-{key[6:]}"] = values
    if not rows:
        raise FactorDataError("The factor file has no daily rows.")
    return rows


def _download_csv(name: str, timeout: int) -> str:
    request = urllib.request.Request(BASE_URL + name, headers={"User-Agent": "Mozilla/5.0 (WealthRyse factor model)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
            return archive.read(member).decode("latin-1")
    except Exception as error:  # network, HTTP, zip or missing-member errors all mean "unavailable"
        raise FactorDataError(f"Could not download {name} from the Kenneth French data library: {error}") from error


def load_factors(region: str = DEFAULT_REGION, *, timeout: int = 30, now: float | None = None) -> dict[str, Any]:
    """Merged daily factors for a region; momentum is optional and noted when missing."""

    if region not in REGIONS:
        raise FactorDataError(f"Unknown factor region {region!r}.")
    moment = time.time() if now is None else now
    with _lock:
        cached = _cache.get(region)
        if cached and moment - cached[0] < CACHE_SECONDS:
            return cached[1]
    label, five_file, momentum_file = REGIONS[region]
    factors = parse_french_csv(_download_csv(five_file, timeout))
    momentum_note = ""
    try:
        momentum = parse_french_csv(_download_csv(momentum_file, timeout), single_column="MOM")
        for day, row in factors.items():
            if day in momentum and "MOM" in momentum[day]:
                row["MOM"] = momentum[day]["MOM"]
    except FactorDataError as error:
        momentum_note = f"Momentum is left out: {error}"
    result = {
        "region": region,
        "region_label": label,
        "source_url": BASE_URL.removesuffix("ftp/") + "data_library.html",
        "factors": factors,
        "latest_date": max(factors),
        "momentum_note": momentum_note,
    }
    with _lock:
        _cache[region] = (moment, result)
    return result
