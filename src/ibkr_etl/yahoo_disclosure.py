"""The owner's consent to send an account's ticker symbols to Yahoo Finance.

One disclosure, used by the onboarding wizard and the new onboarding cases alike. The sector job
and the other Yahoo readers look for an approved, unrevoked account_onboarding_approvals row with
this disclosure version before any ticker leaves the platform.
"""

from __future__ import annotations

from .util import canonical_json, sha256_text

VERSION = "yahoo-ticker-disclosure-v1"
DESTINATION = "Yahoo Finance via yfinance"
SCOPE = {
    "data": ["current_ticker_symbols", "historical_ticker_symbols"],
    "destination": DESTINATION,
    "excluded": [
        "raw_ibkr_files",
        "ibkr_account_identifiers",
        "flex_credentials",
        "database_credentials",
        "personal_information",
    ],
    "purposes": [
        "provisional_sector_classification",
        "crisis_period_price_retrieval",
    ],
}
SCOPE_SHA256 = sha256_text(canonical_json(SCOPE))
