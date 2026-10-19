"""Read-only Kiwoom REST API client for the onboarding pilot.

Kiwoom issues one app key and secret per account, with no read-only scope, so
this client is read-only by construction: it knows only the inquiry API IDs
listed in ``INQUIRY_APIS`` and the two OAuth paths, and refuses anything else
before a request is built. Order APIs, and the US currency exchange
application, are not reachable from this module.

One account holds Korean and US stocks. Korean holdings, cash and transactions
come from the domestic account API; US ones from the US account API, in their
own currency with Kiwoom's KRW conversion alongside.

Kiwoom answers only from IP addresses registered to the app key, so calls must
come from the platform's fixed outbound address. The mock host lists both
account APIs.

Field names follow Kiwoom's published REST API guide and are checked against
the mock server before any live use; anything missing fails loudly instead of
being read as zero.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Callable, Mapping

from .security_master import SecurityIdentifier


HOSTS = {"live": "https://api.kiwoom.com", "mock": "https://mockapi.kiwoom.com"}
TOKEN_PATH = "/oauth2/token"
REVOKE_PATH = "/oauth2/revoke"
ACCOUNT_PATH = "/api/dostk/acnt"
US_ACCOUNT_PATH = "/api/us/acnt"
CHART_PATH = "/api/dostk/chart"
INQUIRY_APIS: Mapping[str, str] = {
    "kt00001": ACCOUNT_PATH,  # deposit detail
    "kt00004": ACCOUNT_PATH,  # account valuation
    "kt00015": ACCOUNT_PATH,  # consolidated transaction history
    "kt00016": ACCOUNT_PATH,  # daily account return detail
    "kt00018": ACCOUNT_PATH,  # holdings valuation detail
    "ka01690": ACCOUNT_PATH,  # daily balance return: holdings and cash on a past date
    "ka10074": ACCOUNT_PATH,  # realised profit by day
    "ka10081": CHART_PATH,  # daily price chart: a stock's official KRX close by day
    "ust21070": US_ACCOUNT_PATH,  # US holdings
    "ust21100": US_ACCOUNT_PATH,  # US transaction history
    "ust21120": US_ACCOUNT_PATH,  # deposits and securities value by currency
    "ust21132": US_ACCOUNT_PATH,  # the same, on a past date
}
MAX_PAGES = 50
TOKEN_MARGIN = timedelta(minutes=5)

Transport = Callable[[str, Mapping[str, str], bytes], tuple[int, Mapping[str, str], Mapping[str, object]]]


class KiwoomError(RuntimeError):
    """A Kiwoom refusal or malformed answer. Never carries keys or tokens."""


# Kiwoom answered Python's default user agent with a bare HTTP 400; the fixed-address probe
# that got a token sent this one, as the platform's other HTTP clients do.
USER_AGENT = "IBKR-Portfolio-Analytics/2.1"


def _urllib_transport(url: str, headers: Mapping[str, str], body: bytes):
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            raw = error.read()
        except OSError:
            raw = b""
        try:
            payload = json.loads(raw)
        except ValueError:
            text = " ".join(raw.decode("utf-8", "replace").split())[:150]
            payload = {"return_msg": f"non-JSON answer: {text}" if text else ""}
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


def _currency(value: object, default: str) -> str:
    text = str(value or "").strip().upper() or default
    if len(text) != 3 or not text.isalpha():
        raise KiwoomError(f"Kiwoom returned a malformed currency code: {text!r}")
    return text


@dataclass(frozen=True)
class Holding:
    security: SecurityIdentifier
    name: str
    quantity: Decimal
    average_price: Decimal
    price: Decimal
    market_value: Decimal
    currency: str = "KRW"
    exchange: str | None = None
    market_value_krw: Decimal | None = None

    @property
    def code(self) -> str:
        return self.security.value

    @property
    def identifiers(self) -> list[SecurityIdentifier]:
        return [self.security]


# Kiwoom's totals (kt00004 prsm_dpst_aset_amt, ka01690 day_stk_asst) are after the tax and fee
# selling every holding would cost: 0.165% of holdings in 2025, 0.215% from 26 Dec 2025 (0.20% tax
# plus the fee). Up to this share of the holdings is read as that cost; ETFs pay no tax, so less is fine.
MAX_SELLING_COST_RATE = Decimal("0.0025")
# Kiwoom works the cost out per stock (kt00018 tax and sell_cmsn), each part rounded down: the
# 0.015% fee to 10 won, the tax to the won. From 26 Dec 2025 the tax is 0.05% and 0.15%, rounded
# down apart; before it, 0.15%. Matches Kiwoom to the won on every day checked.
SELLING_FEE_RATE = Decimal("0.00015")
SPLIT_TAX_FROM = date(2025, 12, 26)


def _floor(value: Decimal, step: int = 1) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def kiwoom_selling_cost(market_value: Decimal, day: date) -> Decimal:
    """Kiwoom's estimated cost of selling one KOSPI holding: securities tax plus fee."""

    if day >= SPLIT_TAX_FROM:
        tax = _floor(market_value * Decimal("0.0005")) + _floor(market_value * Decimal("0.0015"))
    else:
        tax = _floor(market_value * Decimal("0.0015"))
    return tax + _floor(market_value * SELLING_FEE_RATE, 10)


@dataclass(frozen=True)
class AccountValuation:
    account_name: str
    # Cash on a trade-date basis: a trade moves it on the trade date.
    deposit: Decimal
    # Kiwoom's own total, after its estimated selling costs (see MAX_SELLING_COST_RATE).
    estimated_assets: Decimal
    holdings: tuple[Holding, ...]
    # Cash already settled (D+0), when Kiwoom gives it apart from the deposit.
    settled_deposit: Decimal | None = None
    # Kiwoom's own value of the holdings for its total (kt00016 scrt_evlt_amt_to), when read:
    # the holdings at the official KRX close.
    securities_value: Decimal | None = None
    # True when ka01690 listed after-market prices and the holdings were repriced at the close.
    repriced_at_close: bool = False

    @property
    def holdings_value(self) -> Decimal:
        return sum((item.market_value for item in self.holdings), Decimal("0"))

    @property
    def valued_holdings(self) -> Decimal:
        """The holdings as Kiwoom's total values them."""

        return self.holdings_value if self.securities_value is None else self.securities_value

    @property
    def price_basis_gap(self) -> Decimal:
        """Kiwoom's value of the holdings less their value at the prices held here."""

        return self.valued_holdings - self.holdings_value

    @property
    def selling_cost(self) -> Decimal:
        """What Kiwoom's total leaves out of its holdings value plus cash: its estimated cost of selling."""

        return self.valued_holdings + self.deposit - self.estimated_assets


@dataclass(frozen=True)
class CurrencyBalance:
    """One currency's cash and securities value in the US account API."""

    currency: str
    deposit: Decimal
    securities_value: Decimal
    krw_rate: Decimal


@dataclass(frozen=True)
class UsValuation:
    holdings: tuple[Holding, ...]
    balances: tuple[CurrencyBalance, ...]


@dataclass(frozen=True)
class CashMovement:
    trade_date: str
    description: str
    kind: str
    security: SecurityIdentifier | None
    quantity: Decimal
    amount: Decimal
    currency: str
    # Kiwoom's KRW settlement for a movement in another currency.
    amount_krw: Decimal | None = None


def _field(row: Mapping[str, object], name: str) -> object:
    if name not in row:
        raise KiwoomError(f"Kiwoom response is missing {name}")
    return row[name]


def _yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def chart_closes(pages: list[Mapping[str, object]]) -> dict[str, Decimal]:
    """Each day's close (YYYYMMDD) from ka10081 daily chart pages. Pure, so bronze can be re-read."""

    closes: dict[str, Decimal] = {}
    for page in pages:
        for row in page.get("stk_dt_pole_chart_qry") or []:
            dt = str(row.get("dt") or "").strip()
            if dt and str(row.get("cur_prc") or "").strip():
                closes[dt] = kiwoom_number(row["cur_prc"], unsigned=True)
    return closes


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
        self._closes: dict[str, dict[str, Decimal]] = {}

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
            for secret in (self._app_key, self._app_secret):
                message = message.replace(secret, "***")
            call = headers.get("api-id") or path
            raise KiwoomError(f"Kiwoom refused the call {call} (HTTP {status}, code {code}): {message}")
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

    def inquire(self, api_id: str, body: Mapping[str, object], *,
                max_pages: int | None = None) -> list[Mapping[str, object]]:
        """Run one inquiry, following Kiwoom's continuation keys; returns every page.

        ``max_pages`` stops early on purpose, for answers that run on into the past (price charts).
        """

        if api_id not in INQUIRY_APIS:
            raise KiwoomError(f"{api_id} is not an allowed Kiwoom inquiry")
        pages: list[Mapping[str, object]] = []
        continuation: dict[str, str] = {}
        for _ in range(MAX_PAGES):
            if max_pages is not None and len(pages) >= max_pages:
                return pages
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
                    security=SecurityIdentifier("krx_code", krx_code(_field(row, "stk_cd"))),
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
            settled_deposit=kiwoom_number(account["entr"]) if "d2_entra" in account and "entr" in account else None,
        )

    def balance_on(self, day: date) -> AccountValuation | None:
        """Holdings and cash at the close of a past trading day (ka01690), or None on a day KRX was closed.

        Kiwoom answers a closed day with another day's figures, so an answer
        dated differently from the day asked for is read as closed.

        The deposit (dbst_bal) already moves on the trade date, so it is the
        day's cash. The total (day_stk_asst) is Kiwoom's value of the holdings
        plus cash, after its estimated selling costs. Kiwoom's value of the
        holdings is read from the day's account return (kt00016): from 14 Sep
        2026 it can differ from the holdings at the closing prices ka01690 lists.
        """

        pages = self.inquire("ka01690", {"qry_dt": _yyyymmdd(day)})
        first = pages[0]
        answered = str(first.get("dt") or "").strip()
        if not answered and not str(first.get("day_stk_asst") or "").strip():
            # Kiwoom rebuilds its daily records 01:00-03:00 and 04:00-07:00 KST and answers with
            # empty figures meanwhile ("전일자 자동일지 데이터 생성 중입니다").
            raise KiwoomError(f"ka01690 has no figures for {day.isoformat()} while Kiwoom rebuilds its daily records "
                              f"(01:00-03:00 and 04:00-07:00 Korea time): {str(first.get('return_msg') or '')[:120]}")
        if answered and answered != _yyyymmdd(day):
            return None
        holdings: list[Holding] = []
        for page in pages:
            for row in page.get("day_bal_rt") or []:
                code = str(row.get("stk_cd") or "").strip()
                if not code:
                    continue
                holdings.append(Holding(
                    security=SecurityIdentifier("krx_code", krx_code(code)),
                    name=str(_field(row, "stk_nm")).strip(),
                    quantity=kiwoom_number(_field(row, "rmnd_qty")),
                    average_price=kiwoom_number(_field(row, "buy_uv"), unsigned=True),
                    price=kiwoom_number(_field(row, "cur_prc"), unsigned=True),
                    market_value=kiwoom_number(_field(row, "evlt_amt")),
                ))
        deposit = kiwoom_number(_field(first, "dbst_bal"))
        total = kiwoom_number(_field(first, "day_stk_asst"))
        detail = self.inquire("kt00016", {"fr_dt": _yyyymmdd(day), "to_dt": _yyyymmdd(day)})[0]
        detail_cash = kiwoom_number(_field(detail, "entr_to"))
        if detail_cash != deposit:
            raise KiwoomError(f"kt00016 cash {detail_cash:,} differs from ka01690 cash {deposit:,} on {day.isoformat()}")
        held = tuple(holding for holding in holdings if holding.quantity)
        repriced = False
        securities_value = kiwoom_number(_field(detail, "scrt_evlt_amt_to"))
        if sum((item.market_value for item in held), Decimal("0")) != securities_value:
            # From 14 Sep 2026 (KRX trading to 20:00) ka01690 lists each stock at its after-market
            # price; Kiwoom's total values it at the official close. Use the close when that adds
            # up to Kiwoom's value of the holdings, to the won; otherwise keep the listed prices.
            closed = self._at_official_close(held, day)
            if closed is not None and sum((item.market_value for item in closed), Decimal("0")) == securities_value:
                held, repriced = closed, True
        return AccountValuation(
            account_name="",
            deposit=deposit,
            estimated_assets=total,
            holdings=held,
            securities_value=securities_value,
            repriced_at_close=repriced,
        )

    def closing_price(self, code: str, day: date) -> Decimal | None:
        """A stock's official KRX close on a past day (ka10081, unadjusted), or None if not listed.

        One page reaches back far past any history run, so each stock is read once per run and
        again only for a later day.
        """

        wanted = _yyyymmdd(day)
        closes = self._closes.get(code)
        if closes is None or (wanted not in closes and wanted > max(closes, default="")):
            # The plain KRX code (no _NX/_AL suffix) and unadjusted prices: KRX's official close as it was.
            pages = self.inquire("ka10081", {"stk_cd": code, "base_dt": wanted, "upd_stkpc_tp": "0"}, max_pages=1)
            closes = self._closes[code] = chart_closes(pages)
        return closes.get(wanted)

    def _at_official_close(self, holdings: tuple[Holding, ...], day: date) -> tuple[Holding, ...] | None:
        repriced: list[Holding] = []
        for item in holdings:
            price = self.closing_price(item.code, day)
            if price is None:
                return None
            repriced.append(replace(item, price=price, market_value=item.quantity * price))
        return tuple(repriced)

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
                    security=SecurityIdentifier("krx_code", krx_code(code)) if code else None,
                    quantity=kiwoom_number(row.get("trde_qty_jwa_cnt") or "0"),
                    amount=kiwoom_number(_field(row, "exct_amt")),
                    currency=str(row.get("crnc_cd") or "KRW").strip() or "KRW",
                ))
        return movements

    def us_valuation(self) -> UsValuation:
        """US holdings (ust21070) and each currency's cash and securities value (ust21120)."""

        holdings: list[Holding] = []
        for page in self.inquire("ust21070", {"stex_tp": "", "stk_cd": ""}):
            for row in page.get("result_list") or []:
                holdings.append(Holding(
                    security=SecurityIdentifier("us_ticker", _field(row, "stk_cd")),
                    name=str(_field(row, "frgn_stk_nm")).strip(),
                    quantity=kiwoom_number(_field(row, "poss_qty")),
                    average_price=kiwoom_number(_field(row, "frgn_stk_book_uv"), unsigned=True),
                    price=kiwoom_number(_field(row, "now_pric"), unsigned=True),
                    market_value=kiwoom_number(_field(row, "evlt_amt")),
                    currency=_currency(_field(row, "crnc_code"), "USD"),
                    exchange=str(row.get("stex_nm") or "").strip() or None,
                    market_value_krw=kiwoom_number(_field(row, "evlt_amt_krw")),
                ))
        balances = self._balances(self.inquire("ust21120", {"cmsn_incl_tp": "0", "exrt_tp": "0"}))
        return UsValuation(holdings=tuple(holdings), balances=balances)

    def us_balances_on(self, day: date) -> tuple[CurrencyBalance, ...]:
        """Each currency's cash and securities value at the close of a past date (ust21132)."""

        return self._balances(self.inquire("ust21132", {"base_dt": _yyyymmdd(day)}))

    @staticmethod
    def _balances(pages: list[Mapping[str, object]]) -> tuple[CurrencyBalance, ...]:
        balances: list[CurrencyBalance] = []
        for page in pages:
            for row in page.get("result_list") or []:
                balances.append(CurrencyBalance(
                    currency=_currency(_field(row, "crnc_code"), ""),
                    deposit=kiwoom_number(_field(row, "fx_entr")),
                    securities_value=kiwoom_number(_field(row, "evlt_amt")),
                    krw_rate=kiwoom_number(_field(row, "crnc_rt"), unsigned=True),
                ))
        currencies = [item.currency for item in balances]
        if len(set(currencies)) != len(currencies):
            raise KiwoomError("Kiwoom listed a currency twice")
        return tuple(balances)

    def us_transactions(self, start: date, end: date) -> list[CashMovement]:
        """Every US trade, deposit, currency exchange and dividend between two dates (ust21100)."""

        if end < start:
            raise ValueError("End date is before start date")
        movements: list[CashMovement] = []
        body = {"strt_dt": _yyyymmdd(start), "end_dt": _yyyymmdd(end), "tp": "0", "stex_tp": "",
                "stk_cd": "", "krw_repl_skip_yn": "N"}
        for page in self.inquire("ust21100", body):
            for row in page.get("result_list") or []:
                code = str(row.get("stk_cd") or "").strip()
                currency = _currency(row.get("crnc_code"), "KRW")
                foreign = currency != "KRW"
                movements.append(CashMovement(
                    trade_date=datetime.strptime(str(_field(row, "deal_dt")), "%Y%m%d").date().isoformat(),
                    description=str(row.get("rmrk_nm") or "").strip(),
                    kind=str(row.get("deal_kind_nm") or "").strip(),
                    security=SecurityIdentifier("us_ticker", code) if code else None,
                    quantity=kiwoom_number(row.get("deal_qty") or "0"),
                    amount=kiwoom_number(_field(row, "fc_exct_amt" if foreign else "exct_amt")),
                    currency=currency,
                    amount_krw=kiwoom_number(_field(row, "exct_amt")) if foreign else None,
                ))
        return movements


def reconcile_valuation(valuation: AccountValuation, *, tolerance: Decimal = Decimal("1")) -> dict[str, object]:
    """Holdings plus deposit, less Kiwoom's estimated selling cost, should equal Kiwoom's total.

    Returns the check record for ``nav_reconciled``. The cost may be anywhere from
    nothing to MAX_SELLING_COST_RATE of the holdings; any other gap (KRW) fails
    instead of being explained away.
    """

    holdings_value = valuation.holdings_value
    cost = valuation.selling_cost
    allowed = valuation.valued_holdings * MAX_SELLING_COST_RATE
    status = "pass" if -tolerance <= cost <= allowed + tolerance else "fail"
    if status == "pass":
        message = (f"Holdings plus cash match Kiwoom's total of {valuation.estimated_assets:,} KRW"
                   + (f" after its estimated selling cost of {cost:,} KRW." if cost > tolerance else "."))
    elif cost < 0:
        message = f"Kiwoom's total is {-cost:,} KRW above holdings plus cash; the platform will not guess why."
    else:
        message = (f"Kiwoom's total is {cost:,} KRW below holdings plus cash, more than its selling cost "
                   f"could be ({allowed:,.0f} KRW); the platform will not guess why.")
    return {
        "status": status,
        "message": message,
        "evidence": {
            "holdings_value": str(holdings_value),
            "deposit": str(valuation.deposit),
            "broker_total": str(valuation.estimated_assets),
            "kiwoom_holdings_value": str(valuation.valued_holdings),
            "selling_cost": str(cost),
            "gap": str(cost),
            "holdings": len(valuation.holdings),
        },
    }


def reconcile_us_valuation(valuation: UsValuation, *, cent: Decimal = Decimal("0.01")) -> dict[str, object]:
    """US holdings should add up to Kiwoom's own securities value in each currency.

    Each holding's quantity times price must also match its value, which
    catches a field read in the wrong unit. Rounding allows one cent per
    holding. Returns the check record for ``nav_reconciled``.
    """

    balances = {item.currency: item for item in valuation.balances}
    by_currency: dict[str, list[Holding]] = {}
    for holding in valuation.holdings:
        by_currency.setdefault(holding.currency, []).append(holding)
    problems: list[str] = []
    currencies: dict[str, dict[str, str | int]] = {}
    for currency in sorted(set(by_currency) | {c for c, b in balances.items() if b.securities_value}):
        held = by_currency.get(currency, [])
        holdings_value = sum((item.market_value for item in held), Decimal("0"))
        balance = balances.get(currency)
        if balance is None:
            problems.append(f"Kiwoom gave no {currency} total to check {len(held)} holdings against.")
            continue
        gap = holdings_value - balance.securities_value
        currencies[currency] = {
            "holdings_value": str(holdings_value),
            "broker_securities_value": str(balance.securities_value),
            "deposit": str(balance.deposit),
            "gap": str(gap),
            "holdings": len(held),
        }
        if abs(gap) > cent * max(1, len(held)):
            problems.append(f"{currency} holdings are {gap:+,} {currency} away from Kiwoom's total.")
    mispriced = [
        item.code for item in valuation.holdings
        if abs(item.quantity * item.price - item.market_value) > max(cent, abs(item.market_value) / 200)
    ]
    if mispriced:
        problems.append("Quantity times price does not match the value for " + ", ".join(mispriced) + ".")
    status = "fail" if problems else "pass"
    message = (
        " ".join(problems) + " The platform will not guess why."
        if problems
        else "US holdings match Kiwoom's securities value in "
        + (", ".join(currencies) if currencies else "every currency") + "."
    )
    return {"status": status, "message": message,
            "evidence": {"currencies": currencies, "mispriced": mispriced}}
