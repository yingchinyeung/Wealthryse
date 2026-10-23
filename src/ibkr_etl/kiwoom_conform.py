"""Kiwoom conform, read-only: rebuild one bronze run's silver rows from its stored answers and compare.

The collector (kiwoom_collector, and kiwoom_us_account for a USD account) writes Kiwoom's silver rows. With
KIWOOM_BRONZE=true it first stores every inquiry page it receives in bronze.kiwoom_responses (kiwoom_bronze),
under one ingestion_runs row per account per collector run; with KIWOOM_SILVER_FROM_BRONZE=true as well it
computes silver from each page as read back from bronze, never from the answer it received. This module
replays one such run: a KiwoomClient whose transport answers only from the stored pages (``ReplayTransport``),
driven by the collector's own functions, gives back the rows that run wrote, and they are compared with silver
by the collector's keys. Nothing is written and Kiwoom is never called.

The replay does what the run did:

  - The day comes from the run's started_at, with the collector's own rule: snapshot_day for a KRW account,
    us_snapshot_day for a USD one, whose movement list runs to that moment's date in Seoul. The bronze run
    opens moments after the collector's ``now``, and the daily run starts after 08:20 UTC, clear of every
    cut-off (06:40 UTC for KRW, 15:00 UTC for the Seoul date, 21:00 or 22:00 UTC for New York).
    ``day_matches_requests`` says whether the movement list the run asked for ends on that day.
  - KRW (a run that asked for kt00015, which only the KRW path does): collect(), whose trades and cash
    movements are what store() writes; then each past day the run asked for (ka01690) through balance_on,
    us_balances_on and snapshot_rows, as build_history does. The U.S. calls are replayed when the run made them.
  - USD (a run that asked for ust21100 but not kt00015): collect_today, us_transactions and
    us_movement_rows, as run_target stores them; then each past day the run asked for (ust21132) through
    collect_past_day, UsHistoryPositions and us_rows, as build_us_history does.
  - The account is the request_key's, and the path is read from the requests too, not from today's
    onboarding case, which may have moved on since the run.
  - A run with no movement list wrote none of the six tables (a connection check, or a load Kiwoom
    refused early) and has nothing to compare.
  - A stored refusal raises the same KiwoomError the live call raised. As in the collector it stops that
    part, and the live run wrote nothing for it: the account failed, or its history pass rolled back.

What the stored answers cannot say is handled openly, not guessed:

  - Which past days to fill, and the history start, came from the database at the time
    (days_with_snapshots, us_days_stored, the onboarding case). The replay reads them from the requests the
    run made: the days asked for (ka01690 qry_dt, ust21132 base_dt) and the first movement window's strt_dt.
  - Security keys are resolved with today's security master (known_identifiers). Its rows are only ever
    added, so the keys match unless a security was remapped by hand since the run.
  - A USD past day whose positions from the answers do not add up to Kiwoom's total: the collector then
    keeps positions an earlier pass stored, when those add up (stored_holdings). That is silver as it was,
    not an answer, so the replay keeps the answers' positions and counts the day
    (positions_not_from_answers); its positions may show as differences.
  - Bronze keeps the first answer when one request is asked twice in a run, so both askings replay it.
  - ka10081 asks for the chart up to the date on the client's clock; the replay's clock is started_at in
    UTC, the time zone the daily job's containers run in.
  - A request with no stored answer (bronze capture switched off mid-run, or a day derived differently)
    raises MissingAnswer and is counted. A past day missing an answer is left out of the comparison; a
    movement list missing one is not compared at all.

Silver is compared as it is now, so a later run that stored the same day or movement again shows up as a
difference here: the check means most for each account's latest run. A silver row counts against the run
when it lies in what the run covered: the movement list's dates, or the past days it replayed.

The result holds counts only, never figures, account numbers, request keys or bodies, and nothing is logged.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from .kiwoom_bronze import RUN_TYPE, TABLE, request_key
from .kiwoom_client import INQUIRY_APIS, REVOKE_PATH, TOKEN_PATH, KiwoomClient, KiwoomError, UsValuation, \
    stored_payload
from .kiwoom_collector import _KEYS, SEOUL, Securities, collect, known_identifiers, platform_account_id, \
    snapshot_day, snapshot_rows, windows
from .kiwoom_us_account import UsHistoryPositions, collect_past_day, collect_today, position_gap, \
    us_movement_rows, us_rows, us_snapshot_day
from .security_master import SecurityIdentifier

# Handed to the replayed client by the local token call. It is not a token, and nothing reads it.
REPLAY_TOKEN = "replay-only-not-a-token"
MOVEMENT_TABLES = ("trades", "cash_transactions")
SNAPSHOT_TABLES = ("position_snapshots", "cash_snapshots", "nav_snapshots", "fx_rates")
DATE_COLUMNS = {"position_snapshots": "snapshot_date", "cash_snapshots": "snapshot_date",
                "nav_snapshots": "snapshot_date", "fx_rates": "rate_date", "trades": "trade_date",
                "cash_transactions": "effective_date"}


class MissingAnswer(KiwoomError):
    """The run stored no answer for a request the replay makes. Counted, never fetched from Kiwoom."""


class NotABronzeRun(LookupError):
    """The run id names no Kiwoom bronze run (ingestion_runs.run_type 'kiwoom_rest_bronze')."""


def _canonical(fields: Mapping[str, object]) -> str:
    return json.dumps(dict(fields), sort_keys=True, ensure_ascii=False)


@dataclass(frozen=True)
class StoredPage:
    """One bronze.kiwoom_responses row, as the replay needs it."""

    api_id: str
    request: Mapping[str, str]  # request_key: the account and the request's query fields
    page_no: int
    cont_yn: str | None
    next_key: str | None
    http_status: int
    body: bytes

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "StoredPage":
        key = row["request_key"]
        if isinstance(key, (str, bytes, bytearray)):
            key = json.loads(key)
        return cls(str(row["api_id"]), {str(name): str(value) for name, value in dict(key).items()},
                   int(row["page_no"]), row["cont_yn"] or None, row["next_key"] or None, int(row["http_status"]),
                   bytes(row["body"]))

    def answer(self) -> object:
        """The body as the live transport handed it to the client: parsed, with the exact bytes in ``raw``."""

        return stored_payload(self.body)

    @property
    def refusal(self) -> bool:
        """The client raises on this page (KiwoomClient._post's rule)."""

        payload = self.answer()
        return (self.http_status != 200 or not isinstance(payload, Mapping)
                or str(payload.get("return_code", 0)).strip() != "0")


class ReplayTransport:
    """A KiwoomClient transport that answers from one bronze run's stored pages and never calls Kiwoom.

    A request is matched on its api-id and query fields (request_key, as bronze stored them) and its page:
    page 1 when it carries no next-key header, page N+1 when its next-key is the one page N was answered
    with. Each answer has the stored status, the stored cont-yn and next-key headers and the stored body.
    The token and revoke calls are answered here with a placeholder. A request with no stored page raises
    MissingAnswer.
    """

    def __init__(self, pages: Iterable[StoredPage | Mapping[str, Any]]) -> None:
        self._pages: dict[tuple[str, str], dict[int, StoredPage]] = {}
        accounts = set()
        for page in pages:
            page = page if isinstance(page, StoredPage) else StoredPage.from_row(page)
            self._pages.setdefault((page.api_id, _canonical(page.request)), {})[page.page_no] = page
            accounts.add(page.request.get("account", ""))
        if len(accounts) > 1:
            raise ValueError("A Kiwoom bronze run holds one account's answers")
        self.account = accounts.pop() if accounts else ""
        self.served: set[tuple[str, str, int]] = set()
        self.missing: set[tuple[str, str, str]] = set()
        self.refusals = 0

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes):
        path = urlsplit(url).path
        if path == TOKEN_PATH:
            return 200, {}, {"return_code": 0, "token": REPLAY_TOKEN, "expires_dt": "99991231235959"}
        if path == REVOKE_PATH:
            return 200, {}, {"return_code": 0}
        api_id = str(headers.get("api-id") or "")
        after = str(headers.get("next-key") or "")
        try:
            key = _canonical(request_key(self.account, json.loads(body)))
        except ValueError:  # a field bronze refuses to store, so no answer can be stored for it
            key = ""
        stored = self._pages.get((api_id, key), {})
        number = 1 if not after else next((n + 1 for n, page in stored.items() if page.next_key == after), 0)
        page = stored.get(number) if INQUIRY_APIS.get(api_id) == path else None
        if page is None:
            self.missing.add((api_id, key, after))
            raise MissingAnswer(f"The bronze run holds no answer for this {api_id} request")
        self.served.add((api_id, key, number))
        self.refusals += page.refusal
        paging = {name: value for name, value in (("cont-yn", page.cont_yn), ("next-key", page.next_key)) if value}
        return page.http_status, paging, page.answer()

    def asked(self, api_id: str) -> bool:
        return any(api == api_id for api, _ in self._pages)

    def fields(self, api_id: str, name: str) -> list[str]:
        """The values one query field took across the run's requests of one API, sorted."""

        return sorted({next(iter(pages.values())).request.get(name, "")
                       for (api, _), pages in self._pages.items() if api == api_id} - {""})

    def days(self, api_id: str, name: str) -> list[date]:
        return [datetime.strptime(value, "%Y%m%d").date() for value in self.fields(api_id, name)]

    def summary(self) -> dict[str, int]:
        pages = [page for stored in self._pages.values() for page in stored.values()]
        return {"stored": len(pages), "served": len(self.served), "unused": len(pages) - len(self.served),
                "missing": len(self.missing), "refusals_stored": sum(page.refusal for page in pages),
                "refusals_replayed": self.refusals}


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def replay_client(rows: Iterable[StoredPage | Mapping[str, Any]] | ReplayTransport, *,
                  now: datetime | None = None) -> KiwoomClient:
    """A KiwoomClient answered from stored pages only (rows, or a ReplayTransport): no waiting, no real token.

    ``now`` is the client's clock (ka10081 asks for the chart up to its date), read as UTC; it defaults to
    the current time.
    """

    transport = rows if isinstance(rows, ReplayTransport) else ReplayTransport(rows)
    moment = _utc(now or datetime.now(timezone.utc)).replace(tzinfo=None)  # the live client's clock is naive
    return KiwoomClient(environment="live", app_key="replay", app_secret="replay", transport=transport,
                        clock=lambda: moment, sleep=lambda _: None, min_interval_seconds=0)


# Rebuild ---------------------------------------------------------------------------


@dataclass
class Rebuild:
    """The silver rows one bronze run wrote, rebuilt from its answers, and what the run covered.

    ``kind`` is "load" (the run loaded the account), "no_silver" (no movement list was stored, so the run
    wrote none of the six tables: a connection check, or a load refused early) or "empty". ``parts`` says
    for "movements" and "history" whether they were rebuilt, or "missing" (an answer the run did not store),
    "refused" (a stored refusal) or "failed" (an answer the collector could not read); only rebuilt parts
    are compared.
    """

    account_id: str = ""
    kind: str = "empty"
    market: str | None = None
    parts: dict[str, str] = field(default_factory=dict)
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    window: tuple[str, str] | None = None  # the movement list's first and last date
    days: tuple[str, ...] = ()  # the past days replayed, closed ones included
    history: dict[str, int] = field(default_factory=dict)
    day_matches_requests: bool | None = None

    def compared(self) -> list[str]:
        tables = list(MOVEMENT_TABLES) if self.parts.get("movements") == "rebuilt" else []
        return tables + (list(SNAPSHOT_TABLES) if self.parts.get("history") == "rebuilt" and self.days else [])

    def bounds(self, table: str) -> tuple[str, str]:
        dates = [str(row[DATE_COLUMNS[table]])[:10] for row in self.rows.get(table, [])]
        dates += list(self.window or ()) if table in MOVEMENT_TABLES else [self.days[0], self.days[-1]]
        return min(dates), max(dates)

    def covers(self, table: str, row: Mapping[str, Any]) -> bool:
        day = str(row.get(DATE_COLUMNS[table]) or "")[:10]
        if table in MOVEMENT_TABLES:
            return self.window is not None and self.window[0] <= day <= self.window[1]
        return day in self.days


def _attempt(transport: ReplayTransport, work: Callable[[], Any]) -> tuple[str, Any]:
    before = transport.refusals
    try:
        return "rebuilt", work()
    except MissingAnswer:
        return "missing", None
    except (KiwoomError, ValueError):
        # What stops the collector's part (kiwoom_collector.run, _history_record) stops the replay's.
        return ("refused" if transport.refusals > before else "failed"), None


def _first_day(transport: ReplayTransport, api_id: str) -> date:
    return datetime.strptime(transport.fields(api_id, "strt_dt")[0], "%Y%m%d").date()


def _ends_on(transport: ReplayTransport, api_id: str, day: date) -> bool:
    return transport.fields(api_id, "end_dt")[-1:] == [day.strftime("%Y%m%d")]


def rebuild(transport: ReplayTransport, *, started_at: datetime,
            known: Mapping[SecurityIdentifier, str]) -> Rebuild:
    """The rows one bronze run wrote to the six silver tables, from its stored answers. Pure: reads no database.

    ``known`` is the security master's identifiers (kiwoom_collector.known_identifiers).
    """

    started_at = _utc(started_at)
    client = replay_client(transport, now=started_at)
    result = Rebuild(account_id=platform_account_id(transport.account) if transport.account else "")
    if not transport.account:
        return result  # no answers stored
    if transport.asked("kt00015"):
        _korea(result, client, transport, started_at, known)
    elif transport.asked("ust21100"):
        _usa(result, client, transport, started_at, known)
    else:
        result.kind = "no_silver"
        if transport.asked("kt00004"):  # kiwoom_collector.run's connection check
            result.parts["check"], _ = _attempt(transport, client.valuation)
    return result


def _korea(result: Rebuild, client: KiwoomClient, transport: ReplayTransport, started_at: datetime,
           known: Mapping[SecurityIdentifier, str]) -> None:
    result.kind, result.market = "load", "KRW"
    today = snapshot_day(started_at)
    start = _first_day(transport, "kt00015")
    include_us = transport.asked("ust21070")  # collect() asks for the U.S. account first when it is included
    result.window = (start.isoformat(), today.isoformat())
    result.day_matches_requests = _ends_on(transport, "kt00015", today)
    status, collected = _attempt(transport, lambda: collect(
        client, broker_account_id=transport.account, history_start=start, today=today, known=known,
        include_us=include_us))
    result.parts["movements"] = status
    if collected is not None:
        result.rows.update({table: collected.rows[table] for table in MOVEMENT_TABLES})
    if status in ("refused", "failed"):
        return  # the live run failed the account here, before any history pass
    securities = Securities(known=dict(known))

    def past_day(day: date):
        valuation = client.balance_on(day)
        if valuation is None:
            return None  # KRX was closed
        us = UsValuation(holdings=(), balances=client.us_balances_on(day)) if include_us else None
        return snapshot_rows(result.account_id, day, valuation, us, securities)

    _history(result, transport, transport.days("ka01690", "qry_dt"), past_day)


def _usa(result: Rebuild, client: KiwoomClient, transport: ReplayTransport, started_at: datetime,
         known: Mapping[SecurityIdentifier, str]) -> None:
    result.kind, result.market = "load", "USD"
    day = us_snapshot_day(started_at)
    seoul_today = started_at.astimezone(SEOUL).date()
    start = _first_day(transport, "ust21100")
    result.window = (start.isoformat(), seoul_today.isoformat())
    result.day_matches_requests = _ends_on(transport, "ust21100", seoul_today)

    def movements():
        valuation, _ = collect_today(client, day=day)
        listed = [item for first, last in windows(start, seoul_today) for item in client.us_transactions(first, last)]
        moved, _ = us_movement_rows(result.account_id, listed, Securities(known=dict(known)))
        return valuation, listed, moved

    status, collected = _attempt(transport, movements)
    result.parts["movements"] = status
    days = transport.days("ust21132", "base_dt")
    if collected is None:
        if days and status == "missing":
            result.parts["history"] = "missing"  # a past day's positions need the movement list
        return
    valuation, listed, moved = collected
    result.rows.update(moved)
    positions = UsHistoryPositions(client, listed, since=start, through=day,
                                   held_today=[holding.code for holding in valuation.holdings])
    securities = Securities(known=dict(known))
    if days:
        result.history["positions_not_from_answers"] = 0

    def past_day(past: date):
        figures = collect_past_day(client, past)
        holdings = positions(past, figures)
        if holdings is None or abs(position_gap(figures, holdings)) > Decimal("0.01") * max(1, len(holdings)):
            # build_us_history would keep positions an earlier pass stored, if they add up: not an answer.
            result.history["positions_not_from_answers"] += 1
        return us_rows(result.account_id, figures, holdings or (), securities)

    _history(result, transport, days, past_day)


def _history(result: Rebuild, transport: ReplayTransport, days: list[date],
             past_day: Callable[[date], Mapping[str, list[dict[str, Any]]] | None]) -> None:
    if not days:
        return
    rows: dict[str, list[dict[str, Any]]] = {table: [] for table in SNAPSHOT_TABLES}
    replayed, closed, missing = [], [], []

    def replay() -> None:
        for day in days:  # in date order, as the collector fills them
            try:
                built = past_day(day)
            except MissingAnswer:
                missing.append(day)  # left out of the comparison
                continue
            if built is None:
                closed.append(day)
                continue
            replayed.append(day)
            for table in SNAPSHOT_TABLES:
                rows[table].extend(built[table])

    status, _ = _attempt(transport, replay)
    if status == "rebuilt" and not (replayed or closed):
        status = "missing"
    result.parts["history"] = status
    result.history.update(asked=len(days), replayed=len(replayed), closed=len(closed), missing=len(missing))
    if status == "rebuilt":
        result.rows.update(rows)
        result.days = tuple(sorted(day.isoformat() for day in replayed + closed))


# Compare ---------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def compare(result: Rebuild, silver: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Counts per table: matched, differ (and which columns), only_in_rebuild and only_in_silver.

    Rows pair on the collector's upsert keys (kiwoom_collector._KEYS). Every column the collector writes is
    compared exactly, as text: silver holds the collector's own text, so a reformatted number is a
    difference too. Left out: row_sha256 and last_seen_run_id, the write's bookkeeping (a hash of those same
    columns, and the silver run that wrote the row, not this bronze run), and any column the collector does
    not write. A silver row with no rebuilt partner counts only when the run covered it (Rebuild.covers).
    """

    counts: dict[str, dict[str, Any]] = {}
    for table in result.compared():
        keys = _KEYS[table]
        built = {tuple(_text(row[name]) for name in keys): row for row in result.rows.get(table, [])}
        stored = {tuple(_text(row.get(name)) for name in keys): row for row in silver.get(table, [])}
        columns: Counter = Counter()
        matched = differ = 0
        for key, row in built.items():
            found = stored.get(key)
            if found is None:
                continue
            changed = [name for name in row if name not in keys and _text(row[name]) != _text(found.get(name))]
            columns.update(changed)
            differ += bool(changed)
            matched += not changed
        counts[table] = {
            "matched": matched, "differ": differ, "only_in_rebuild": len(built.keys() - stored.keys()),
            "only_in_silver": sum(1 for key, row in stored.items() if key not in built and result.covers(table, row)),
            "differ_columns": dict(sorted(columns.items())),
        }
    return counts


def summary(result: Rebuild, transport: ReplayTransport, tables: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Counts only: never a figure, account number, request key or body.

    ``answers`` counts the run's stored pages: served to the replay, unused by it, requests it made that
    have no stored page (missing), stored refusals and refusals replayed. ``clean`` means no missing answer
    and no difference in any compared table.
    """

    answers = transport.summary()
    return {
        "kind": result.kind, "market": result.market, "day_matches_requests": result.day_matches_requests,
        "parts": dict(result.parts),
        "history_days": dict(result.history), "answers": answers, "tables": dict(tables),
        "clean": not answers["missing"] and all(
            not (item["differ"] or item["only_in_rebuild"] or item["only_in_silver"]) for item in tables.values()),
    }


# Database (reads only) -------------------------------------------------------------


def _moment(value: Any) -> datetime:
    return _utc(value if isinstance(value, datetime) else datetime.fromisoformat(str(value)))


def silver_rows(connection, result: Rebuild) -> dict[str, list[dict[str, Any]]]:
    """The account's silver rows each compared table holds between the dates the run covered."""

    found: dict[str, list[dict[str, Any]]] = {}
    for table in result.compared():
        first, last = result.bounds(table)
        rows = connection.execute(
            f"SELECT * FROM {table} WHERE account_id = ? AND {DATE_COLUMNS[table]} BETWEEN ? AND ?",
            (result.account_id, first, last),
        ).fetchall()
        found[table] = [{name: row[name] for name in row.keys()} for row in rows]
    return found


def check_run(connection, run_id: int) -> dict[str, Any]:
    """Rebuild one bronze run's silver rows from its stored answers and compare them with silver. Reads only."""

    run = connection.execute("SELECT run_id, run_type, started_at, status FROM ingestion_runs WHERE run_id = ?",
                             (int(run_id),)).fetchone()
    if run is None or run["run_type"] != RUN_TYPE:
        raise NotABronzeRun(f"Run {int(run_id)} is not a Kiwoom bronze run ({RUN_TYPE})")
    rows = connection.execute(
        f"SELECT api_id, request_key, page_no, cont_yn, next_key, http_status, body FROM {TABLE} "
        "WHERE run_id = ? ORDER BY response_id", (int(run_id),)).fetchall()
    transport = ReplayTransport(StoredPage.from_row(row) for row in rows)
    result = rebuild(transport, started_at=_moment(run["started_at"]), known=known_identifiers(connection))
    tables = compare(result, silver_rows(connection, result))
    return {"run_id": int(run_id), "run_status": str(run["status"]), **summary(result, transport, tables)}


def latest_runs(connection) -> list[int]:
    """Each Kiwoom account's latest successful bronze run (PostgreSQL)."""

    rows = connection.execute(
        f"SELECT DISTINCT ON (k.connection_id, k.request_key->>'account') k.run_id FROM {TABLE} k "
        "JOIN ingestion_runs r ON r.run_id = k.run_id WHERE r.run_type = ? AND r.status = 'success' "
        "ORDER BY k.connection_id, k.request_key->>'account', k.run_id DESC", (RUN_TYPE,)).fetchall()
    return sorted(int(row["run_id"]) for row in rows)
