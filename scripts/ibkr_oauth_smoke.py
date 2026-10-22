"""Read-only check that the IBKR OAuth credentials work and return full daily closes.

Run on the owner's computer once IBKR has activated the consumer key:

    python scripts/ibkr_oauth_smoke.py "D:\\IBKR Data\\ibkr-oauth"

It reads the private keys and DH prime from the folder ibkr_oauth_keygen.py filled, asks
for the consumer key, access token and access token secret at hidden prompts (or takes
IBKR_OAUTH_CONSUMER_KEY, IBKR_OAUTH_ACCESS_TOKEN and IBKR_OAUTH_ACCESS_TOKEN_SECRET from the
environment), then:

1. gets a live session token and opens the brokerage session;
2. reads a month of daily bars for each benchmark and prints the last five closes beside
   Yahoo's for the same days (a difference over 0.05% is flagged);
3. searches IBKR for markets Yahoo serves today that IBKR may not (Taiex, Shanghai
   Composite, KODEX 200) and prints what it finds;
4. logs out.

Nothing is written anywhere, and no credential is printed. Paste the output back.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ibkr_etl import ibkr_web_market_data as md  # noqa: E402
from ibkr_etl import ibkr_web_oauth as oauth  # noqa: E402

YAHOO = {"SPX": "^GSPC", "COMP": "^IXIC", "HSI": "^HSI", "K200": "^KS200", "SPY": "SPY", "2800": "2800.HK"}
COVERAGE_PROBES = (("TAIEX", None), ("SSE Composite", None), ("KODEX 200", None), ("069500", "STK"))
TOLERANCE = Decimal("0.0005")


def credentials(folder: Path, prompt=getpass.getpass) -> oauth.OAuthCredentials:
    env = dict(os.environ)
    env["IBKR_OAUTH_SIGNATURE_KEY"] = str(folder / "private_signature.pem")
    env["IBKR_OAUTH_ENCRYPTION_KEY"] = str(folder / "private_encryption.pem")
    env["IBKR_OAUTH_DH_PRIME"] = (folder / "dh_prime.txt").read_text(encoding="ascii").strip()
    for name, label in (("IBKR_OAUTH_CONSUMER_KEY", "Consumer key"), ("IBKR_OAUTH_ACCESS_TOKEN", "Access token"),
                        ("IBKR_OAUTH_ACCESS_TOKEN_SECRET", "Access token secret")):
        if not env.get(name, "").strip():
            env[name] = prompt(label + " (hidden): ").strip()
    return oauth.OAuthCredentials.from_environment(env)


def yahoo_closes(symbol: str, days) -> dict:
    try:
        import yfinance as yf

        frame = yf.Ticker(symbol).history(start=min(days).isoformat(), period=None, auto_adjust=False)
        return {index.date(): Decimal(str(round(float(row["Close"]), 6)))
                for index, row in frame.iterrows() if row["Close"] == row["Close"]}
    except Exception as error:  # noqa: BLE001 - Yahoo is only the comparison
        print(f"    (Yahoo {symbol} unavailable: {type(error).__name__})")
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--benchmarks", default=",".join(md.DEFAULT_BENCHMARKS))
    parser.add_argument("--no-yahoo", action="store_true")
    args = parser.parse_args(argv)

    client = oauth.IbkrWebClient(credentials(args.folder))
    problems = 0
    with client:
        client.live_session_token()
        print("1. Live session token: OK")
        print("   Brokerage session:", client.open_brokerage_session())
        reader = md.WebMarketData(client)
        print("2. Daily closes (IBKR vs Yahoo)")
        for benchmark in md.parse_benchmarks(args.benchmarks):
            try:
                conid = reader.resolve(benchmark)
                bars = reader.daily_bars(benchmark, conid=conid)
            except oauth.OAuthError as error:
                problems += 1
                print(f"   {benchmark.label}: FAILED - {error}")
                continue
            print(f"   {benchmark.label} (conid {conid}): {len(bars)} bars")
            recent = bars[-5:]
            yahoo = {} if args.no_yahoo or not recent else yahoo_closes(
                YAHOO.get(benchmark.symbol, benchmark.symbol), [b.bar_date for b in recent])
            for bar in recent:
                other = yahoo.get(bar.bar_date)
                flag = ""
                if other is not None and other != 0 and abs(bar.close / other - 1) > TOLERANCE:
                    flag = "  <-- differs"
                    problems += 1
                print(f"     {bar.bar_date}  IBKR {bar.close}  Yahoo {other if other is not None else '-'}{flag}")
        print("3. Coverage search")
        for text, sec_type in COVERAGE_PROBES:
            try:
                found = reader.search(text, sec_type, by_name=sec_type is None) or []
            except oauth.OAuthError as error:
                print(f"   {text}: search failed - {error}")
                continue
            hits = [f"{row.get('symbol')}/{row.get('description')}/conid {row.get('conid')}"
                    for row in found[:5] if isinstance(row, dict)]
            print(f"   {text}: " + ("; ".join(hits) if hits else "nothing found"))
    print("4. Logged out.", "All checks passed." if not problems else f"{problems} problem(s) above.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
