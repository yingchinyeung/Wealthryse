from __future__ import annotations

import time
import ssl
import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

from .config import Settings


BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
LOGGER = logging.getLogger(__name__)

POLL_PENDING_CODES = {
    "1001", "1003", "1004", "1005", "1006", "1007", "1008", "1009",
    "1019", "1021",
}
LOCKOUT_CODES = {"1010", "1011", "1012", "1013", "1014", "1015", "1016", "1017", "1018", "1020"}
LOCKOUT_PHRASES = (
    "too many failed attempts",
    "too many requests",
    "invalid credential",
    "invalid token",
    "not authenticated",
    "temporarily locked",
    "locked out",
)


def is_lockout_error(code: str | None, message: str | None) -> bool:
    normalized = (message or "").casefold()
    return code in LOCKOUT_CODES or any(phrase in normalized for phrase in LOCKOUT_PHRASES)


class FlexError(RuntimeError):
    def __init__(
        self,
        message: str,
        code: str | None = None,
        retryable: bool = False,
        *,
        phase: str = "unknown",
        lockout: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.phase = phase
        self.lockout = lockout


@dataclass(frozen=True)
class FlexReport:
    content: bytes
    content_type: str
    reference_code: str


class FlexClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ssl_context = ssl.create_default_context()
        # Python 3.13+ enables X509 strict mode by default. Some otherwise trusted
        # Windows/network root certificates omit a non-security-critical metadata
        # flag and are rejected only by that mode. Keep CA and hostname verification
        # fully enabled while allowing those Windows trust-store certificates.
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            self.ssl_context.verify_flags &= ~ssl.VERIFY_X509_STRICT

    def _get(self, path: str, params: dict[str, str], *, phase: str) -> tuple[bytes, str]:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{BASE_URL}{path}?{query}",
            headers={"User-Agent": self.settings.user_agent, "Accept": "application/xml,text/xml,*/*"},
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.settings.request_timeout_seconds,
                context=self.ssl_context,
            ) as response:
                return response.read(), response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            raise FlexError(
                f"IBKR returned HTTP {exc.code}",
                retryable=phase == "get_statement" and exc.code >= 500,
                phase=phase,
            ) from exc
        except urllib.error.URLError as exc:
            raise FlexError(
                f"Could not reach IBKR: {exc.reason}",
                retryable=phase == "get_statement",
                phase=phase,
            ) from exc

    @staticmethod
    def _child_text(root: ET.Element, name: str) -> str | None:
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1].lower() == name.lower():
                return element.text
        return None

    @staticmethod
    def _response_status(content: bytes) -> tuple[str | None, str | None, str | None]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return None, None, None
        status = FlexClient._child_text(root, "Status")
        code = FlexClient._child_text(root, "ErrorCode")
        message = FlexClient._child_text(root, "ErrorMessage")
        return status, code, message

    def request_report(self, from_date: date, to_date: date) -> FlexReport:
        self.settings.require_flex_credentials()
        send_content, _ = self._get(
            "/SendRequest",
            {
                "t": self.settings.flex_token,
                "q": self.settings.flex_query_id,
                "fd": from_date.strftime("%Y%m%d"),
                "td": to_date.strftime("%Y%m%d"),
                "v": "3",
            },
            phase="send_request",
        )
        try:
            root = ET.fromstring(send_content)
        except ET.ParseError as exc:
            raise FlexError("IBKR SendRequest returned invalid XML", phase="send_request") from exc
        status = self._child_text(root, "Status")
        if status != "Success":
            code = self._child_text(root, "ErrorCode")
            message = self._child_text(root, "ErrorMessage") or "Unknown IBKR Flex error"
            raise FlexError(
                message,
                code=code,
                phase="send_request",
                lockout=is_lockout_error(code, message),
            )
        reference_code = self._child_text(root, "ReferenceCode")
        if not reference_code:
            raise FlexError(
                "IBKR response did not contain a reference code", phase="send_request"
            )

        deadline = time.monotonic() + self.settings.max_wait_seconds
        delay = 5.0
        while True:
            try:
                content, content_type = self._get(
                    "/GetStatement",
                    {"t": self.settings.flex_token, "q": reference_code, "v": "3"},
                    phase="get_statement",
                )
            except FlexError as exc:
                if not exc.retryable or time.monotonic() + delay > deadline:
                    raise
                LOGGER.warning(
                    "IBKR Flex poll transport error phase=get_statement code=%s attempt=poll; retrying same reference in %.1fs",
                    exc.code or "none",
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * 1.7, 30.0)
                continue
            status, code, message = self._response_status(content)
            if status != "Fail" and not code:
                return FlexReport(content, content_type, reference_code)
            if is_lockout_error(code, message):
                raise FlexError(
                    message or "IBKR rejected statement retrieval",
                    code=code,
                    phase="get_statement",
                    lockout=True,
                )
            if code not in POLL_PENDING_CODES:
                raise FlexError(
                    message or "IBKR could not retrieve the statement",
                    code=code,
                    phase="get_statement",
                )
            if time.monotonic() + delay > deadline:
                raise FlexError(
                    f"Timed out waiting for IBKR statement: {message or code}",
                    code=code,
                    phase="get_statement",
                )
            LOGGER.info(
                "IBKR statement pending phase=get_statement code=%s attempt=poll; polling same reference in %.1fs",
                code or "none",
                delay,
            )
            time.sleep(delay)
            delay = min(delay * 1.7, 30.0)
