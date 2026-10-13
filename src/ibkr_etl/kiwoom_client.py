"""Read-only Kiwoom REST API client for the onboarding pilot.

Kiwoom issues one app key and secret per account, with no read-only scope, so
this client is read-only by construction: it knows only the inquiry API IDs
listed in ``INQUIRY_APIS`` and the two OAuth paths, and refuses anything else
before a request is built. Order APIs are not reachable from this module.

Kiwoom answers only from IP addresses registered to the app key, so calls must
come from the platform's fixed outbound address. The mock host serves KRX only.

Field names follow Kiwoom's published REST API guide and are checked against
the mock server before any live use; anything missing fails loudly instead of
being read as zero.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping

from .security_master import SecurityIdentifier


HOSTS = {"live": "https://api.kiwoom.com", "mock": "https://mockapi.kiwoom.com"}
TOKEN_PATH = "/oauth2/token"
REVOKE_PATH = "/oauth2/revoke"
ACCOUNT_PATH = "/api/dostk/acnt"
INQUIRY_APIS: Mapping[str, str] = {
    "kt00001": ACCOUNT_PATH,  # deposit detail
    "kt00004": ACCOUNT_PATH,  # account valuation
    "kt00015": ACCOUNT_PATH,  # consolidated transaction history
    "kt00016": ACCOUNT_PATH,  # daily account return detail
    "kt00018": ACCOUNT_PATH,  # holdings valuation detail
    "ka01690": ACCOUNT_PATH,  # daily balance return
    "ka10074": ACCOUNT_PATH,  # realised profit by day
}
MAX_PAGES = 50
TOKEN_MARGIN = timedelta(minutes=5)

Transport = Callable[[str, Mapping[str, str], bytes], tuple[int, Mapping[str, str], Mapping[str, object]]]


class KiwoomError(RuntimeError):
    """A Kiwoom refusal or malformed answer. Never carries keys or tokens."""


def _urllib_transport(url: str, headers: Mapping[str, str], body: bytes):
    request = urllib.request.Request(url, data=body, method="POST", headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            payload = json.load(error)
        except (ValueError, OSError):
            payload = {}
        return error.code, {k.lower(): v for k, v in (error.headers or {}).items()}, payload


def kiwoom_number(value: object, *, unsigned: bool = False) -> Decimal:
    """Read Kiwoom's zero-padded, signed number strings.

    Price fields carry a leading + or - for the direction of the day's move,
    not the sign of the price, so callers pass ``unsigned=True`` for prices.
    """

    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        raise KiwoomError("Kiwoom returned an empty number")
    sign = -1 if text.startswith("-") else 1
    digits = text.lstrip("+-").lstrip("0") or "0"
    try:
        number = Decimal(digits if not digits.startswith(".") else "0" + digits)
    except InvalidOperation as error:
        raise KiwoomError(f"Kiwoom returned a malformed number: {text!r}") from error
    return number if unsigned else sign * number


def krx_code(value: object) -> str:
    """Kiwoom prefixes share codes with A (A005930); the KRX code is the six characters."""

    text = str(value or "").strip().upper()
    if len(text) == 7 and text[0] in {"A", "J", "Q"}:
        text = text[1:]
    return SecurityIdentifier("krx_code", text).value


@dataclass(frozen=True)
class Holding:
    krx_code: str
    name: str
    quantity: Decimal
    average_price: Decimal
    price: Decimal
    market_value: Decimal
    currency: str = "KRW"

    @property
    def identifiers(self) -> list[SecurityIdentifier]:
        return [SecurityIdentifier("krx_code", self.krx_code)]


@dataclass(frozen=True)
class AccountValuation:
    account_name: str
    # Cash after pending trades settle (D+2) when Kiwoom gives it, so a trade
    # bought today is not counted twice against the estimated assets.
    deposit: Decimal
    estimated_assets: Decimal
    holdings: tuple[Holding, ...]


@dataclass(frozen=True)
class CashMovement:
    trade_date: str
    description: str
    kind: str
    krx_code: str | None
    quantity: Decimal
    amount: Decimal
    currency: str


def _field(row: Mapping[str, object], name: str) -> object:
    if name not in row:
        raise KiwoomError(f"Kiwoom response is missing {name}")
    return row[name]


def _yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


class KiwoomClient:
    def __init__(
        self,
        *,
        environment: str,
        app_key: str,
        app_secret: str,
        transport: Transport = _urllib_transport,
        clock: Callable[[], datetime] = datetime.now,
        sleep: Callable[[float], None] = time.sleep,
        min_interval_seconds: float = 1.0,
    ) -> None:
        if environment not in HOSTS:
            raise ValueError("Kiwoom environment must be live or mock")
        if not app_key.strip() or not app_secret.strip():
            raise ValueError("Kiwoom app key and secret are required")
        self.environment = environment
        self._host = HOSTS[environment]
        self._app_key = app_key.strip()
        self._app_secret = app_secret.strip()
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._min_interval = max(0.0, min_interval_seconds)
        self._last_call: datetime | None = None
        self._token: str | None = None
        self._token_expires: datetime | None = None

    def __repr__(self) -> str:
        return f"KiwoomClient(environment={self.environment!r})"

    def _post(self, path: str, headers: Mapping[str, str], body: Mapping[str, object]):
        if path not in {TOKEN_PATH, REVOKE_PATH, *INQUIRY_APIS.values()}:
            raise KiwoomError("Only Kiwoom inquiry calls are allowed")
        if self._last_call is not None and self._min_interval:
            waited = (self._clock() - self._last_call).total_seconds()
            if waited < self._min_interval:
                self._sleep(self._min_interval - waited)
        self._last_call = self._clock()
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        status, response_headers, payload = self._transport(
            self._host + path,
            {"Content-Type": "application/json;charset=UTF-8", **headers},
            encoded,
        )
        if not isinstance(payload, Mapping):
            raise KiwoomError(f"Kiwoom returned a non-JSON answer (HTTP {status})")
        code = payload.get("return_code", 0)
        if status != 200 or str(code).strip() != "0":
            message = str(payload.get("return_msg") or "no message")[:200]
            raise KiwoomError(f"Kiwoom refused the call (HTTP {status}, code {code}): {message}")
        return response_headers, payload

    def _bearer(self) -> str:
        now = self._clock()
        if self._token and self._token_expires and now < self._token_expires - TOKEN_MARGIN:
            return self._token
        _, payload = self._post(TOKEN_PATH, {}, {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "secretkey": self._app_secret,
        })
        token = str(payload.get("token") or "")
        expires = str(payload.get("expires_dt") or "")
        if not token:
            raise KiwoomError("Kiwoom issued no access token")
        try:
            self._token_expires = datetime.strptime(expires, "%Y%m%d%H%M%S")
        except ValueError:
            self._token_expires = now + timedelta(hours=1)
        self._token = token
        return token

    def revoke(self) -> None:
        """Give the access token back at the end of a run."""

        if not self._token:
            return
        token, self._token, self._token_expires = self._token, None, None
        self._post(REVOKE_PATH, {}, {"appkey": self._app_key, "secretkey": self._app_secret, "token": token})

    def inquire(self, api_id: str, body: Mapping[str, object]) -> list[Mapping[str, object]]:
        """Run one inquiry, following Kiwoom's continuation keys; returns every page."""

        if api_id not in INQUIRY_APIS:
            raise KiwoomError(f"{api_id} is not an allowed Kiwoom inquiry")
        pages: list[Mapping[str, object]] = []
        continuation: dict[str, str] = {}
        for _ in range(MAX_PAGES):
            headers = {"authorization": f"Bearer {self._bearer()}", "api-id": api_id, **continuation}
            response_headers, payload = self._post(INQUIRY_APIS[api_id], headers, body)
            pages.append(payload)
            if str(response_headers.get("cont-yn", "N")).upper() != "Y":
                return pages
            next_key = str(response_headers.get("next-key") or "")
            if not next_key:
                raise KiwoomError("Kiwoom said more pages follow but gave no next key")
            continuation = {"cont-yn": "Y", "next-key": next_key}
        raise KiwoomError(f"{api_id} returned more than {MAX_PAGES} pages")

    def valuation(self) -> AccountValuation:
        """Holdings and cash today (kt00018 for holdings, kt00004 for the account)."""

        account = self.inquire("kt00004", {"qry_tp": "0", "dmst_stex_tp": "KRX"})[0]
        holdings: list[Holding] = []
        for page in self.inquire("kt00018", {"qry_tp": "1", "dmst_stex_tp": "KRX"}):
            for row in page.get("acnt_evlt_remn_indv_tot") or []:
                holdings.append(Holding(
                    krx_code=krx_code(_field(row, "stk_cd")),
                    name=str(_field(row, "stk_nm")).strip(),
                    quantity=kiwoom_number(_field(row, "rmnd_qty")),
                    average_price=kiwoom_number(_field(row, "pur_pric"), unsigned=True),
                    price=kiwoom_number(_field(row, "cur_prc"), unsigned=True),
                    market_value=kiwoom_number(_field(row, "evlt_amt")),
                ))
        return AccountValuation(
            account_name=str(account.get("acnt_nm") or "").strip(),
            deposit=kiwoom_number(account["d2_entra"] if "d2_entra" in account else _field(account, "entr")),
            estimated_assets=kiwoom_number(_field(account, "prsm_dpst_aset_amt")),
            holdings=tuple(holdings),
        )

    def transactions(self, start: date, end: date) -> list[CashMovement]:
        """Every trade, deposit, withdrawal and dividend between two dates (kt00015)."""

        if end < start:
            raise ValueError("End date is before start date")
        movements: list[CashMovement] = []
        body = {"strt_dt": _yyyymmdd(start), "end_dt": _yyyymmdd(end), "tp": "0", "stk_cd": "",
                "crnc_cd": "", "gds_tp": "0", "frgn_stex_code": "", "dmst_stex_tp": "%"}
        for page in self.inquire("kt00015", body):
            for row in page.get("trst_ovrl_trde_prps_array") or []:
                code = str(row.get("stk_cd") or "").strip()
                movements.append(CashMovement(
                    trade_date=datetime.strptime(str(_field(row, "trde_dt")), "%Y%m%d").date().isoformat(),
                    description=str(row.get("rmrk_nm") or "").strip(),
                    kind=str(row.get("io_tp_nm") or row.get("trde_kind_nm") or "").strip(),
                    krx_code=krx_code(code) if code else None,
                    quantity=kiwoom_number(row.get("trde_qty_jwa_cnt") or "0"),
                    amount=kiwoom_number(_field(row, "exct_amt")),
                    currency=str(row.get("crnc_cd") or "KRW").strip() or "KRW",
                ))
        return movements


def reconcile_valuation(valuation: AccountValuation, *, tolerance: Decimal = Decimal("1")) -> dict[str, object]:
    """Holdings plus deposit should equal Kiwoom's own estimated assets.

    Returns the check record for ``nav_reconciled``; a gap above the tolerance
    (KRW) fails instead of being explained away.
    """

    holdings_value = sum((item.market_value for item in valuation.holdings), Decimal("0"))
    computed = holdings_value + valuation.deposit
    gap = computed - valuation.estimated_assets
    status = "pass" if abs(gap) <= tolerance else "fail"
    message = (
        f"Holdings plus cash match Kiwoom's total of {valuation.estimated_assets:,} KRW."
        if status == "pass"
        else f"Holdings plus cash are {gap:+,} KRW away from Kiwoom's total; the platform will not guess why."
    )
    return {
        "status": status,
        "message": message,
        "evidence": {
            "holdings_value": str(holdings_value),
            "deposit": str(valuation.deposit),
            "broker_total": str(valuation.estimated_assets),
            "gap": str(gap),
            "holdings": len(valuation.holdings),
        },
    }
