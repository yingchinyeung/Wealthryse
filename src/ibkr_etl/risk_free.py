from __future__ import annotations

import math
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import Any

from . import raw_capture
from .reference_data import _get, _parse_treasury_date
from .util import utc_now_iso


TREASURY_FEED_BASE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/pages/xml"
)
SERIES_ID = "UST-PAR-3M"
PROVIDER = "U.S. Treasury Daily Treasury Par Yield Curve Rates"


@dataclass(frozen=True)
class TreasuryRateObservation:
    rate_date: str
    annual_rate: float


def treasury_year_url(year: int) -> str:
    query = urllib.parse.urlencode(
        {
            "data": "daily_treasury_yield_curve",
            "field_tdr_date_value": str(year),
        }
    )
    return f"{TREASURY_FEED_BASE_URL}?{query}"


def parse_treasury_3m_xml(xml: bytes) -> list[TreasuryRateObservation]:
    """Parse every dated 3-month par yield from a Treasury XML response."""
    root = ET.fromstring(xml)
    by_date: dict[str, TreasuryRateObservation] = {}
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].upper() != "PROPERTIES":
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
            rate_date = _parse_treasury_date(date_text).isoformat()
            annual_percent = float(rate_text)
        except (TypeError, ValueError):
            continue
        annual_rate = annual_percent / 100.0
        if not math.isfinite(annual_rate) or not -0.05 <= annual_rate <= 0.30:
            raise ValueError(
                f"Treasury 3-month rate is outside the validation range on {rate_date}"
            )
        observation = TreasuryRateObservation(rate_date, annual_rate)
        previous = by_date.get(rate_date)
        if previous is not None and previous != observation:
            raise ValueError(f"Conflicting Treasury observations for {rate_date}")
        by_date[rate_date] = observation
    if not by_date:
        raise RuntimeError("Treasury XML did not contain a dated 3-month par yield")
    return [by_date[key] for key in sorted(by_date)]


def fetch_treasury_3m_rates(
    start_date: date,
    end_date: date | None = None,
    *,
    timeout: int = 45,
) -> list[TreasuryRateObservation]:
    effective_end = end_date or date.today()
    if effective_end < start_date:
        raise ValueError("Risk-free end date precedes start date")
    observations: dict[str, TreasuryRateObservation] = {}
    for year in range(start_date.year, effective_end.year + 1):
        url = treasury_year_url(year)
        requested_at = raw_capture.utc_now()
        payload = _get(url, timeout=timeout)
        raw_capture.capture_http("treasury-par-yield", PROVIDER, url=url, body=payload,
                                 key=f"year-{year}", extension="xml", requested_at=requested_at)
        for observation in parse_treasury_3m_xml(payload):
            observed = date.fromisoformat(observation.rate_date)
            if start_date <= observed <= effective_end:
                observations[observation.rate_date] = observation
    if not observations:
        raise RuntimeError("No Treasury 3-month rates fall within the requested period")
    return [observations[key] for key in sorted(observations)]


def refresh_treasury_3m_rates(
    connection: Any,
    *,
    start_date: date,
    end_date: date | None = None,
    timeout: int = 45,
) -> dict[str, Any]:
    """Idempotently load validated official observations into PostgreSQL."""
    observations = fetch_treasury_3m_rates(
        start_date, end_date=end_date, timeout=timeout
    )
    fetched_at = utc_now_iso()
    try:
        for observation in observations:
            connection.execute(
                """
                INSERT INTO risk_free_rates(
                    rate_date, series_id, currency, tenor, annual_rate,
                    day_count_convention, provider, fetched_at, quality_status
                ) VALUES (?, ?, 'USD', '3-month', ?, 'annualized-par-yield', ?, ?, 'pass')
                ON CONFLICT(tenant_id, rate_date, series_id) DO UPDATE SET
                    currency = excluded.currency,
                    tenor = excluded.tenor,
                    annual_rate = excluded.annual_rate,
                    day_count_convention = excluded.day_count_convention,
                    provider = excluded.provider,
                    fetched_at = excluded.fetched_at,
                    quality_status = excluded.quality_status
                """,
                (
                    observation.rate_date,
                    SERIES_ID,
                    observation.annual_rate,
                    PROVIDER,
                    fetched_at,
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    latest = observations[-1]
    return {
        "series_id": SERIES_ID,
        "provider": PROVIDER,
        "source_url": TREASURY_FEED_BASE_URL,
        "requested_start": start_date.isoformat(),
        "requested_end": (end_date or date.today()).isoformat(),
        "observations_loaded": len(observations),
        "latest_observation_date": latest.rate_date,
        "latest_annual_rate": latest.annual_rate,
        "quality_status": "pass",
    }
