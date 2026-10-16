"""Make one IBKR Flex request from v2's network and keep the raw statement only.

Used once before the switch (medallion plan, "Token and test pull"): it proves
that v1.1's token and query work from v2's network, and that the statement has
every section v2 needs. It writes nothing to the database. The statement is
saved, create-only, to the raw container (bronze) under test-pulls/, and the
output shows counts, the sha256 and section results, never account numbers,
the token or the query ID.

Without --apply it only checks its inputs and makes no request. IBKR is asked
once; there is no retry, because a second request on the same token can lock it.

Environment: FLEX_TOKEN_VAULT_URI, AZURE_CLIENT_ID, AZURE_STORAGE_ACCOUNT_URL,
optional AZURE_RAW_CONTAINER (default ibkr-raw) and PORTFOLIO_TENANT_ID, and the
query ID in the variable named by --query-id-env.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import upload_bytes  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.flex_client import FlexClient, FlexError  # noqa: E402
from ibkr_etl.flex_discovery import _secret_value  # noqa: E402
from ibkr_etl.flex_recipe import check_flex_recipe  # noqa: E402

SECRET_NAME = re.compile(r"^ibkr-flex-source-[0-9a-f]{32}$")
QUERY_ID = re.compile(r"^[0-9]{1,20}$")
HKT = ZoneInfo("Asia/Hong_Kong")
# The daily IBKR requests (v1.1's and v2's) run in this window; stay out of it.
BLOCKED_UTC = (time(7, 0), time(9, 30))
TEST_PREFIX = "test-pulls"


def _blocked(now: datetime) -> bool:
    start, end = BLOCKED_UTC
    return start <= now.astimezone(timezone.utc).time() < end


def _window(to_date: date | None, days: int, now: datetime) -> tuple[date, date]:
    end = to_date or (now.astimezone(HKT).date() - timedelta(days=1))
    return end - timedelta(days=days - 1), end


def recipe_lines(content: bytes) -> tuple[str, list[str], int]:
    """Overall status, one line per section (worst status across accounts), account count."""

    result = check_flex_recipe(content)
    order = {"pass": 0, "warn": 1, "fail": 2}
    worst: dict[str, tuple[str, str, str]] = {}
    for findings in result.account_findings.values():
        for finding in (*result.statement_findings, *findings):
            current = worst.get(finding.section)
            if current is None or order[finding.status] > order[current[1]]:
                worst[finding.section] = (finding.label, finding.status, finding.message)
    lines = []
    for label, status, message in worst.values():
        # Messages name sections and fields only; record counts vary by account, so drop them.
        shown = "" if status == "pass" else f": {message}"
        lines.append(f"{status.upper():4} {label}{shown}")
    return result.status, lines, len(result.accounts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--token-secret-name", required=True, help="the token's name in v2's flex vault")
    parser.add_argument("--query-id-env", default="CUTOVER_FLEX_QUERY_ID",
                        help="environment variable that holds the query ID")
    parser.add_argument("--to-date", type=date.fromisoformat, help="last data date (default: yesterday HKT)")
    parser.add_argument("--days", type=int, default=14, help="days in the request, like the daily pull")
    parser.add_argument("--apply", action="store_true", help="make the request; without it nothing is sent")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    problems = []
    if not SECRET_NAME.fullmatch(args.token_secret_name):
        problems.append("token secret name must look like ibkr-flex-source-<32 hex>")
    query_id = os.environ.get(args.query_id_env, "").strip()
    if not QUERY_ID.fullmatch(query_id):
        problems.append(f"{args.query_id_env} is missing or not a query ID")
    for name in ("FLEX_TOKEN_VAULT_URI", "AZURE_CLIENT_ID", "AZURE_STORAGE_ACCOUNT_URL"):
        if not os.environ.get(name, "").strip():
            problems.append(f"{name} is not set")
    if not 1 <= args.days <= 365:
        problems.append("--days must be between 1 and 365")
    if _blocked(now):
        problems.append("refused between 07:00 and 09:30 UTC, when the daily IBKR requests run")
    start, end = _window(args.to_date, args.days, now)
    print(f"TEST_PULL window {start} to {end} ({args.days} days)")
    if problems:
        for problem in problems:
            print(f"TEST_PULL refused: {problem}")
        return 2
    if not args.apply:
        print("TEST_PULL dry run: inputs are valid; nothing was sent. Add --apply to make the one request.")
        return 0

    settings = load_settings()
    token = _secret_value(os.environ["FLEX_TOKEN_VAULT_URI"], args.token_secret_name,
                          os.environ["AZURE_CLIENT_ID"])
    try:
        client_settings = replace(settings, flex_token=token, flex_query_id=query_id)
        token = ""
        try:
            report = FlexClient(client_settings).request_report(start, end)
        except FlexError as error:
            # The message is IBKR's; it never contains the token or the query ID.
            print(f"TEST_PULL IBKR error: phase={error.phase} code={error.code or 'none'} "
                  f"lockout={error.lockout} message={str(error)[:200]}")
            print("RESULT: FAILED (no retry; do not run again before checking the code above)")
            return 3
    finally:
        token = ""
        client_settings = None

    content = report.content
    digest = hashlib.sha256(content).hexdigest()
    print(f"TEST_PULL received {len(content)} bytes, sha256 {digest}")
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    blob_name = (f"{TEST_PREFIX}/{settings.tenant_id}/{now:%Y/%m/%d}/"
                 f"flex_{start}_{end}_{stamp}_{digest[:12]}.xml")
    uploaded = upload_bytes(
        os.environ["AZURE_STORAGE_ACCOUNT_URL"], settings.azure_raw_container, blob_name, content,
        overwrite=False, content_type="application/xml",
        metadata={"purpose": "medallion-test-pull", "sha256": digest,
                  "requested_from": start.isoformat(), "requested_to": end.isoformat()},
    )
    if not uploaded:
        print("RESULT: FAILED (the statement arrived but could not be saved)")
        return 4
    print(f"TEST_PULL saved {settings.azure_raw_container}/{blob_name}")

    try:
        status, lines, accounts = recipe_lines(content)
    except ValueError as error:
        print(f"TEST_PULL recipe check could not read the statement: {error}")
        print("RESULT: FAILED")
        return 1
    print(f"TEST_PULL accounts in statement: {accounts}")
    for line in lines:
        print(f"TEST_PULL recipe {line}")
    print(json.dumps({"TEST_PULL_SUMMARY": {"bytes": len(content), "sha256": digest, "accounts": accounts,
                                            "recipe": status, "blob": blob_name}}, sort_keys=True))
    print(f"RESULT: {'PASSED' if status != 'fail' else 'FAILED (recipe)'}")
    return 0 if status != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
