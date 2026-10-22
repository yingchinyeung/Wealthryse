# Live IBKR data

The website can show live, read-only IBKR figures beside the daily Flex
pages: a status badge next to the As-of list, a "Live now" card on
Overview, live positions on Holdings, live balances on Cash and the last 24
hours of executions on Trades. The Flex statement stays the book of record.

**Status, 22 Oct 2026:** nothing fills the live tables. The TWS API gateway
(an IB Gateway VM with a collector beside it, from PR 58) was retired at the
owner's request before it was ever deployed: its Bicep file, VM deploy files,
collector image and collector code are gone. What stays, all switched off:

| Piece | Code |
|---|---|
| Tables, views, `live_view` switch | `migrations/postgres/063_live_ibkr_data.sql` (applied) |
| Writer role and its grants | `ibkr_live_collector`, `src/ibkr_etl/live_collector_access.py` |
| Writer | `src/ibkr_etl/live_store.py` (`LiveStore`, `markets_open`) |
| Website | `src/ibkr_etl/live_view.py`, `src/ibkr_etl/live_ui.py`, endpoints in `web.py` |
| Tests | `tests/test_live_data_db.py` (real PostgreSQL in CI), `tests/test_web_live.py` |

IBKR market data now comes through the Web API over OAuth, with no gateway
(`docs/IBKR_OAUTH_MARKET_DATA.md`). The same client could later fill these
tables (positions, balances, quotes) through `LiveStore`; that is not built.

## Safety properties that still hold

- **Least privilege.** The writer role can read and write the seven live_
  tables and nothing else. The website reads them through its existing
  read-only role.
- **Same access rules as the rest of the site.** Live data needs view access
  to the account and its `live_view` switch: `preview` shows it to owners
  and platform admins only, `active` to everyone who may view the account.
  The legacy allow-list and the database-outage fallback never see it.

## Freshness

| Badge | Meaning |
|---|---|
| Live | Snapshot within 3 minutes while a market is open (15 minutes otherwise) |
| Live · some prices delayed | As above, but IBKR sent delayed prices for some holdings |
| Live data is behind | The latest snapshot is older than that |
| Live data paused | The gateway is signed out or the collector has stopped |

The page asks again every 30 seconds while Hong Kong or US markets are open
and every 5 minutes otherwise.
