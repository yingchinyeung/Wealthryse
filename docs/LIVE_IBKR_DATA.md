# Live IBKR data (TWS API)

The website can show live, read-only IBKR figures beside the daily Flex
pages: a status badge next to the As-of list, a "Live now" card on
Overview, live positions on Holdings, live balances on Cash and the last 24
hours of executions on Trades. The Flex statement stays the book of record.

Nothing is live until the gateway VM runs and an account's `live_view`
switch is set. The default is off.

## How it fits together

```
IBKR servers
   │ IB Gateway login (owner's IBKR username, Read-Only API, IB Key push)
   ▼
ibkrv2stg-ibkr-gateway (VM, no public IP, outbound through the NAT IP)
   ├─ ib-gateway container (gnzsnz/ib-gateway + IBC, pinned by digest)
   └─ collector container (python -m ibkr_etl.live_collector, readonly=True)
            │ ibkr_live_collector role: write access to the live_ tables only
            ▼
PostgreSQL: live_ tables and subject_live_* views (migration 063)
            │ website's existing read-only role
            ▼
ibkrv2stg-web: /api/portfolio/{account}/live, /live/positions, /live/trades
```

| Piece | Code |
|---|---|
| Tables, views, `live_view` switch | `migrations/postgres/063_live_ibkr_data.sql` |
| Collector role | `src/ibkr_etl/live_collector_access.py`, `scripts/provision_live_collector.py` |
| Collector | `src/ibkr_etl/live_collector.py`, `Dockerfile.live-collector`, `requirements-live.lock` |
| Website | `src/ibkr_etl/live_view.py`, `src/ibkr_etl/live_ui.py`, endpoints in `web.py` |
| VM | `infra/staging-ibkr-gateway.bicep`, `deploy/ibkr-gateway/` |
| Tests | `tests/test_live_collector.py`, `tests/test_live_data_db.py` (real PostgreSQL in CI), `tests/test_web_live.py`, `tests/test_ibkr_gateway_bicep.py` |

## Safety properties

- **Cannot trade.** The gateway runs with the Read-Only API setting, the
  collector connects with `readonly=True`, and the collector has no order
  call (a test checks its source).
- **Least privilege.** The collector's database role can read and write the
  seven live_ tables and nothing else; provisioning checks this and fails
  otherwise. The website reads them through its existing read-only role.
- **Same access rules as the rest of the site.** Live data needs view access
  to the account and its `live_view` switch: `preview` shows it to owners
  and platform admins only, `active` to everyone who may view the account.
  The legacy allow-list and the database-outage fallback never see it.
- **No secret on disk or in configuration.** The VM reads `ibkr-username`,
  `ibkr-password` and `database-live-url` from Key Vault at service start
  into tmpfs. Containers cannot reach the instance metadata endpoint. No
  port is published.
- **Bounded logins.** Every gateway login sends an IB Key push. Logins
  happen weekly (Monday 21:00 Hong Kong time), after a reboot, and when the
  gateway has been signed out for 10 minutes between 08:00 and 23:00 (for
  example after the owner signs in elsewhere), checked every 30 minutes.
  The nightly restart at 05:30 needs no push.

## Freshness

| Badge | Meaning |
|---|---|
| Live | Snapshot within 3 minutes while a market is open (15 minutes otherwise) |
| Live · some prices delayed | As above, but IBKR sent delayed prices for some holdings |
| Live data is behind | The latest snapshot is older than that |
| Live data paused | The gateway is signed out or the collector has stopped |

The page asks again every 30 seconds while Hong Kong or US markets are open
and every 5 minutes otherwise.

## Rollout

Each step that changes Azure or the database needs the owner's typed
approval naming the resources, and the project's Azure change slot.

1. Apply migration 063 and create the collector login in one execution of
   the existing `ibkrv2stg-bootstrap` job, from the owner's computer after
   `az login`:

   ```
   python scripts/rollout_live_data_stage1.py check
   python scripts/rollout_live_data_stage1.py start
   python scripts/rollout_live_data_stage1.py result --execution <name start printed>
   ```

   `start` creates `database-live-url` in Key Vault with a new random
   password if it is missing, and passes the database only its SCRAM
   verifier. The execution refuses, changing nothing, unless every applied
   migration matches main and nothing is pending except 063 and the
   deliberately held-back 054, 055, 059, 060 and 061 (063 does not depend on
   them; a later normal bootstrap from a main image would apply them all).
   Rerunning it is safe. (`scripts/provision_live_collector.py` does the same login step from
   inside the network, if that is ever easier.)
2. Release the website from main with the Deploy website workflow. Until a
   `live_view` switch is set, it shows no live data.
3. Store `ibkr-username` and `ibkr-password` in Key Vault.
4. Copy the gateway image into the main registry by digest; build and push
   `Dockerfile.live-collector`.
5. Network change: the `ibkr-gateway` subnet behind the NAT gateway.
6. Deploy `infra/staging-ibkr-gateway.bicep` (what-if first), paper mode
   first, then live.
7. Set `live_view` to `preview` for the accounts, check the pages, then
   `active` when viewers should see them.

To switch live data off for an account, set its `live_view` to `disabled`.
To stop the gateway, stop `ibkr-live.service` on the VM or delete the VM.
