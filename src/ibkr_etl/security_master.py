"""One identity per security across brokers.

The reporting tables key instruments by IBKR ``conid``. A security first seen
at IBKR keeps its conid as its platform key, so existing history is untouched.
A security first seen at another broker gets a namespaced key such as
``KRX:005930`` or ``US:AAPL``, which can never collide with a numeric conid.
When an ISIN is known, every broker's identifier for that ISIN resolves to the
same key, so combined holdings add Samsung at IBKR and Samsung at Kiwoom
together.

Kiwoom reports US stocks by ticker only, with no ISIN. IBKR listings on a US
exchange therefore also register their symbol as a ``us_ticker``, so a US
stock held at both brokers resolves to its conid key. A ticker can be reused
after a delisting or change in a rename, and nothing in Kiwoom's answer tells
the two apart, so the owner remaps it when that happens.

An identifier that resolves to two different keys is a conflict and blocks
reconciliation rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


SCHEMES = ("ibkr_conid", "isin", "krx_code", "us_ticker")
_PATTERNS = {
    "ibkr_conid": re.compile(r"^[0-9]{1,12}$"),
    "isin": re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"),
    # KRX short codes are six characters; newer listings may include letters.
    "krx_code": re.compile(r"^[0-9A-Z]{6}$"),
    # A US root symbol with an optional class or series suffix, as in BRK.B.
    "us_ticker": re.compile(r"^[A-Z][A-Z0-9]{0,5}(\.[A-Z0-9]{1,4})?$"),
}
KEY_PATTERN = re.compile(
    r"^([0-9]{1,12}|KRX:[0-9A-Z]{6}|ISIN:[A-Z]{2}[A-Z0-9]{9}[0-9]|US:[A-Z][A-Z0-9]{0,5}(\.[A-Z0-9]{1,4})?)$"
)
# IBKR listingExchange values for the US venues Kiwoom also reports.
IBKR_US_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"})


def _normalize(scheme: str, value: object) -> str:
    text = str(value or "").strip().upper()
    if scheme == "us_ticker":
        # IBKR writes share classes as "BRK B"; other sources use / or -.
        text = re.sub(r"[ /-]", ".", text)
    return text


def isin_is_valid(isin: str) -> bool:
    """ISO 6166 check digit (Luhn over the letters-to-digits expansion)."""

    candidate = str(isin or "").strip().upper()
    if not _PATTERNS["isin"].fullmatch(candidate):
        return False
    digits = "".join(str(int(ch, 36)) for ch in candidate[:-1])
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return (10 - total % 10) % 10 == int(candidate[-1])


@dataclass(frozen=True, order=True)
class SecurityIdentifier:
    scheme: str
    value: str

    def __post_init__(self) -> None:
        if self.scheme not in SCHEMES:
            raise ValueError(f"Unknown identifier scheme: {self.scheme}")
        normalized = _normalize(self.scheme, self.value)
        if not _PATTERNS[self.scheme].fullmatch(normalized):
            raise ValueError(f"Malformed {self.scheme}: {self.value!r}")
        if self.scheme == "isin" and not isin_is_valid(normalized):
            raise ValueError(f"ISIN check digit is wrong: {normalized}")
        object.__setattr__(self, "value", normalized)


class SecurityConflict(ValueError):
    """Identifiers for one holding point at two different securities."""


def new_security_key(identifiers: Sequence[SecurityIdentifier]) -> str:
    """Key for a security the platform has not seen before."""

    by_scheme = {item.scheme: item.value for item in identifiers}
    if "ibkr_conid" in by_scheme:
        return by_scheme["ibkr_conid"]
    if "krx_code" in by_scheme:
        return f"KRX:{by_scheme['krx_code']}"
    if "isin" in by_scheme:
        return f"ISIN:{by_scheme['isin']}"
    if "us_ticker" in by_scheme:
        return f"US:{by_scheme['us_ticker']}"
    raise ValueError("At least one identifier is required")


def ibkr_identifiers(*, conid: object, isin: object = None, symbol: object = None,
                     listing_exchange: object = None) -> list[SecurityIdentifier]:
    """Identifiers an IBKR Flex row gives for one security.

    The conid always; the ISIN when its check digit holds; the symbol as a
    us_ticker only for a US listing, since the same symbol means different
    securities on other exchanges.
    """

    identifiers = [SecurityIdentifier("ibkr_conid", str(conid or ""))]
    candidate = str(isin or "").strip().upper()
    if candidate and isin_is_valid(candidate):
        identifiers.append(SecurityIdentifier("isin", candidate))
    if str(listing_exchange or "").strip().upper() in IBKR_US_EXCHANGES and str(symbol or "").strip():
        identifiers.append(SecurityIdentifier("us_ticker", symbol))
    return identifiers


class SecurityMaster:
    """In-memory view of the security_identifiers table."""

    def __init__(self, mappings: Iterable[tuple[SecurityIdentifier, str]] = ()) -> None:
        self._keys: dict[SecurityIdentifier, str] = {}
        for identifier, key in mappings:
            self._add(identifier, key)

    def _add(self, identifier: SecurityIdentifier, key: str) -> None:
        if not KEY_PATTERN.fullmatch(key):
            raise ValueError(f"Malformed security key: {key}")
        existing = self._keys.get(identifier)
        if existing is not None and existing != key:
            raise SecurityConflict(f"{identifier.scheme} {identifier.value} maps to {existing} and {key}")
        self._keys[identifier] = key

    def resolve(self, identifiers: Sequence[SecurityIdentifier]) -> str | None:
        keys = {self._keys[item] for item in identifiers if item in self._keys}
        if len(keys) > 1:
            raise SecurityConflict("Identifiers resolve to different securities: " + ", ".join(sorted(keys)))
        return next(iter(keys), None)

    def register(self, identifiers: Sequence[SecurityIdentifier]) -> tuple[str, list[SecurityIdentifier]]:
        """Resolve or create the key and return the identifiers it newly gains."""

        if not identifiers:
            raise ValueError("At least one identifier is required")
        key = self.resolve(identifiers) or new_security_key(identifiers)
        added = sorted(item for item in set(identifiers) if item not in self._keys)
        for item in added:
            self._add(item, key)
        return key, added

    def mappings(self) -> Mapping[SecurityIdentifier, str]:
        return dict(self._keys)


def unmapped_holdings(
    holdings: Iterable[Mapping[str, object]],
    master: SecurityMaster,
) -> list[str]:
    """Labels of holdings no identifier resolves; feeds the securities_mapped check.

    Each holding carries ``identifiers`` (a list of SecurityIdentifier) and a
    ``label`` for the message. Conflicts count as unmapped.
    """

    missing: list[str] = []
    for holding in holdings:
        identifiers = list(holding.get("identifiers") or [])
        label = str(holding.get("label") or "unnamed holding")
        try:
            if not identifiers or master.resolve(identifiers) is None:
                missing.append(label)
        except SecurityConflict:
            missing.append(f"{label} (conflicting identifiers)")
    return missing
