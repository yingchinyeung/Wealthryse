# IBKR market data over the Web API (OAuth)

IBKR is to be the **primary** benchmark and FX source, with Yahoo (yfinance) as the
backup: IBKR's close is used wherever it has one, and Yahoo fills gaps, covers markets
IBKR does not list, and serves as the cross-check. Each run of a job signs in to IBKR's Web
API with first-party OAuth 1.0a, reads daily bars for the benchmarks, stores IBKR's
answers in bronze exactly as received, and logs out. There is no gateway machine, no
browser login and no weekly 2FA. It replaces the TWS gateway plan, which was retired
before it was ever deployed (`docs/LIVE_IBKR_DATA.md`).

Read-only: the client has no order or account-change calls.

## What is built

| Piece | Code |
|---|---|
| OAuth client: live session token, signing, brokerage session, logout | `src/ibkr_etl/ibkr_web_oauth.py` |
| Contract lookup and daily bars, raw copies to bronze | `src/ibkr_etl/ibkr_web_market_data.py` |
| Bronze feed | `ibkr-web-history` in the `bronze-market-data` container (`raw_capture.FEEDS`), freshness reported but never an incident |
| Key files for IBKR's OAuth page | `scripts/ibkr_oauth_keygen.py` |
| Read-only smoke test | `scripts/ibkr_oauth_smoke.py` |
| Key Vault secrets | `scripts/store_ibkr_oauth.py` |
| Tests (fake IBKR server checking every signature) | `tests/test_ibkr_web_oauth.py` |

**Not built yet:** the benchmark-daily job reading these secrets, and silver choosing
IBKR's close first, falling back to Yahoo, and flagging days where the two disagree. That
comes after the smoke test passes, so it is built against IBKR's real answers. Two
points to settle then: IBKR's stock and ETF daily bars are adjusted for splits but not
dividends, and IBKR's data has its own redistribution limits, so the "internal only"
label on benchmark data stays until licensing is confirmed.

## Owner steps

Run these on your PC from an up-to-date clone of main. Steps 2 to 4 happen on
IBKR's website and need no code.

1. **Make the key files** (about a minute):

   ```
   python scripts/ibkr_oauth_keygen.py "D:\IBKR Data\ibkr-oauth"
   ```

   This writes six files. Three are public and go to IBKR: `public_signature.pem`,
   `public_encryption.pem` and `dhparam.pem`. The other three (`private_signature.pem`,
   `private_encryption.pem` and `dh_prime.txt`) are private and go to Key Vault in step 6.

2. **Choose the IBKR login.** Use a separate user for the platform, added under
   *Settings → Users & Access Rights* in the IBKR portal. Each OAuth run opens a
   brokerage session for its user, which logs out that user's TWS, mobile or portal
   session. Market data subscriptions belong to a user, so the platform user needs
   its own.

3. **Subscribe that user to market data** under *Settings → Market Data
   Subscriptions*. You need US indices (S&P and Nasdaq), Hong Kong (HKEX securities
   and HKFE indices) and Korea (KRX/KSE indices), plus any exchange for ETFs you
   compare. An account held by a company is normally billed at professional rates,
   so check those prices. IBKR's personal-use terms don't cover passing index values
   on to clients.

4. **Register OAuth** while signed in as that user, at
   `https://ndcdyn.interactivebrokers.com/sso/Login?action=OAUTH&RL=1&ip2loc=US`
   (if you land on a different regional site, open your usual IBKR login page without
   signing in and add `&action=OAUTH` to its address):
   - accept the terms;
   - choose a 9-letter consumer key (A to Z) and save it;
   - upload `public_signature.pem`, `public_encryption.pem` and `dhparam.pem`;
   - click *Generate Token* and copy the **access token** and **access token secret**
     right away, because they are shown only once;
   - turn on *Enable OAuth Access*.

   Activation can take from a day to two weeks. Users report it often starts after
   IBKR's weekend restart. Until then the smoke test fails at step 1.

5. **Smoke test** (read-only; asks for the consumer key, token and secret at hidden
   prompts):

   ```
   python scripts/ibkr_oauth_smoke.py "D:\IBKR Data\ibkr-oauth"
   ```

   It prints IBKR's last five closes beside Yahoo's for S&P 500, Nasdaq Composite, Hang
   Seng, KOSPI 200, SPY and 2800.HK, then searches for Taiex, Shanghai Composite and
   KODEX 200. Paste the output back so the job wiring can be built against it.

6. **Store the credentials in Key Vault** (after `az login`; asks for the same three
   values):

   ```
   python scripts/store_ibkr_oauth.py "D:\IBKR Data\ibkr-oauth"
   ```

   This creates `ibkr-oauth-consumer-key`, `ibkr-oauth-access-token`,
   `ibkr-oauth-access-token-secret`, `ibkr-oauth-signature-key`,
   `ibkr-oauth-encryption-key` and `ibkr-oauth-dh-prime` in `ibkrv2stg-auhecwc4itaoo`.
   It writes them through Azure Resource Manager, the same way the other rollout
   scripts do. Afterwards, move the `ibkr-oauth` folder somewhere offline. Anyone with
   the private keys and the token can read this user's IBKR data.

## How a run works

1. Decrypt the access token secret with the private encryption key.
2. `POST /oauth/live_session_token`, signed RSA-SHA256, with a fresh Diffie-Hellman
   challenge. The run derives the live session token from IBKR's answer and checks it
   against IBKR's signature. The token lasts about 24 hours, and each run makes its own.
3. Sign every later call HMAC-SHA256 with that token. `POST /iserver/auth/ssodh/init`
   opens the brokerage session, then `GET /iserver/accounts` is read once (IBKR wants
   this before any market data call).
4. Resolve each benchmark (`IND:SPX:CBOE:USD`, the same format the TWS collector used)
   with `/iserver/secdef/search`, or use a pinned `@conid`. Read
   `/iserver/marketdata/history?period=1m&bar=1d`, retrying once if the first answer
   is empty, which IBKR sometimes sends at the start of a session.
5. Store each answer in bronze before it is parsed. Each bar is dated in its exchange's
   time zone, and a bar with no close is dropped rather than recorded as zero.
6. `POST /logout`.

Settings for jobs: `IBKR_OAUTH_CONSUMER_KEY`, `IBKR_OAUTH_ACCESS_TOKEN`,
`IBKR_OAUTH_ACCESS_TOKEN_SECRET`, `IBKR_OAUTH_SIGNATURE_KEY` and
`IBKR_OAUTH_ENCRYPTION_KEY` (PEM text or a file path), `IBKR_OAUTH_DH_PRIME` (hex), plus
the optional `IBKR_OAUTH_REALM` (default `limited_poa`) and `IBKR_WEB_API_BASE_URL`.

## Later uses of the same client

Cross-checking Flex positions and NAV each morning, IBKR's PortfolioAnalyst returns
as a check on factsheet figures, filling the website's live view through
`live_store.LiveStore`, IBKR industry data for sectors, and same-day executions.
Flex stays the book of record, because the Web API only keeps about a week of trades.
