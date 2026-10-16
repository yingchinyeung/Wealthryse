"""Live IBKR panels on the account pages, added only when live_view allows it.

The panels sit beside the As-of pages rather than inside them: a status badge
next to the As-of list, a "Live now" card on Overview, live positions on
Holdings, live balances on Cash and the last 24 hours of executions on
Trades. They poll the read-only /live endpoints every 30 seconds while a
market is open and every 5 minutes otherwise, back off after errors, pause
while the tab is hidden and stop when the page is left.
"""
from __future__ import annotations

import json

LIVE_CSS = r"""
.live-badge.freshness { margin-top: 0; align-self: center; white-space: nowrap; }
.live-card .label .live-asof { margin-left: 6px; font-weight: 500; text-transform: none; letter-spacing: 0; }
.live-card .metrics { margin-top: 10px; }
.live-card .live-note { margin-top: 10px; }
.live-source { display: inline-block; padding: 1px 7px; border-radius: 8px; font-size: 11px; font-weight: 650; color: var(--muted-strong); background: rgba(15, 23, 42, .06); }
.live-source.delayed, .live-flag { color: #8a5a00; background: rgba(194, 137, 0, .12); }
.live-flag { display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 8px; font-size: 11px; font-weight: 650; }
@media screen {
  .live-source { background: rgba(255, 255, 255, .08); }
  .live-source.delayed, .live-flag { color: #fcd34d; background: rgba(252, 211, 77, .12); }
}
"""

_SCRIPT = r"""
<script>
(() => {
  const config = __CONFIG__;
  const base = `/api/portfolio/${encodeURIComponent(config.account)}/live`;
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const num = (n, digits = 2) => n == null ? '—' : new Intl.NumberFormat('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits}).format(n);
  const qty = n => n == null ? '—' : new Intl.NumberFormat('en-US', {maximumFractionDigits: 4}).format(n);
  const signed = (n, suffix = '') => n == null ? '—' : `<span class="${n > 0 ? 'gain' : n < 0 ? 'loss' : ''}">${n > 0 ? '+' : ''}${num(n)}${suffix}</span>`;
  const percent = n => n == null ? '—' : signed(n * 100, '%');
  const hkt = (iso, withDay = false) => iso ? new Intl.DateTimeFormat('en-GB', {...(withDay ? {day: 'numeric', month: 'short'} : {}), hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Hong_Kong'}).format(new Date(iso)) + ' HKT' : '';
  const labels = {
    live: ['', 'Live'], delayed: ['late', 'Live · some prices delayed'],
    behind: ['late', 'Live data is behind'], paused: ['stale', 'Live data paused'],
    unavailable: ['late', 'Live data not started'],
  };
  const notes = {
    paused: 'The IBKR connection reconnects by itself. Signing in to IBKR elsewhere pauses it until you sign out there.',
    behind: 'The latest live snapshot is older than expected. Figures below are from the time shown.',
    unavailable: 'No live snapshot has arrived for this account yet.',
    delayed: 'Some prices are delayed by IBKR because the exchange has no live subscription; they are marked in Holdings.',
  };

  const badge = document.createElement('div');
  badge.className = 'freshness live-badge'; badge.setAttribute('role', 'status'); badge.hidden = true;
  document.querySelector('.account-hero-controls')?.prepend(badge);
  const card = (section, html) => {
    const view = document.querySelector(`.page-view[data-section="${section}"]`);
    if (!view) return null;
    const node = document.createElement('div');
    node.className = 'card wide live-card'; node.hidden = true; node.innerHTML = html;
    view.prepend(node);
    return node;
  };
  const panels = {
    overview: card('overview', '<div class="label">Live now<span class="live-asof"></span></div><div class="metrics live-metrics"></div><div class="muted live-note"></div>'),
    holdings: card('holdings', '<div class="label">Live positions<span class="live-asof"></span></div><div class="muted live-note">Prices and values in each instrument\'s currency, straight from IBKR. The As-of table below stays the book of record.</div><div class="table-scroll"><table><thead><tr><th>Symbol</th><th>Description</th><th>Quantity</th><th>Price</th><th>Today</th><th>Currency</th><th>Market value</th><th>Unrealized P&amp;L</th><th>Prices</th></tr></thead><tbody class="live-rows"></tbody></table></div>'),
    cash: card('cash', '<div class="label">Live cash<span class="live-asof"></span></div><div class="metrics live-metrics"></div>'),
    trades: card('trades', '<div class="label">Trades in the last 24 hours<span class="live-asof"></span></div><div class="muted live-note">Executions seen by the live IBKR connection. "Not in statement yet" clears once the daily IBKR statement includes the trade.</div><div class="table-scroll"><table class="trade-table"><thead><tr><th>Time</th><th>Instrument</th><th>Side</th><th>Quantity</th><th>Price</th><th>Currency</th><th>Commission</th><th>Realized P&amp;L</th></tr></thead><tbody class="live-rows"></tbody></table></div>'),
  };
  const panel = panels[config.section] || null;
  let stopped = false, busy = false, failures = 0, timer = null, controller = null, pollSeconds = 300;

  async function read(path) {
    const response = await fetch(path, {cache: 'no-store', credentials: 'same-origin', signal: controller.signal});
    if (response.status === 401 || response.status === 403 || response.status === 404) {
      stopped = true; throw new Error('closed');
    }
    if (!response.ok) throw new Error('unavailable');
    return response.json();
  }
  function showSummary(summary) {
    const [tone, text] = labels[summary.state] || labels.unavailable;
    badge.className = 'freshness live-badge' + (tone ? ' ' + tone : '');
    badge.textContent = text + (summary.observed_at ? ' · ' + hkt(summary.observed_at) : '');
    badge.title = notes[summary.state] || '';
    badge.hidden = false;
    pollSeconds = summary.poll_seconds || 300;
    const account = summary.account;
    if (panels.overview) {
      panels.overview.hidden = false;
      panels.overview.querySelector('.live-asof').textContent = summary.observed_at ? ' · ' + hkt(summary.observed_at) : '';
      panels.overview.querySelector('.live-note').textContent = notes[summary.state] || '';
      const cur = account ? esc(account.base_currency) : '';
      panels.overview.querySelector('.live-metrics').innerHTML = account ? [
        ['Net liquidation value', `${num(account.net_liquidation)} ${cur}`],
        ['Today\'s P&amp;L', `${signed(account.daily_pnl)} ${cur}`],
        ['Unrealized P&amp;L', `${signed(account.unrealized_pnl)} ${cur}`],
        ['Cash', `${num(account.total_cash)} ${cur}`],
      ].map(([label, value]) => `<div class="metric"><div class="label">${label}</div><div class="value">${value}</div></div>`).join('') : '';
    }
    if (panels.cash && config.section === 'cash') {
      panels.cash.hidden = !account;
      panels.cash.querySelector('.live-asof').textContent = summary.observed_at ? ' · ' + hkt(summary.observed_at) : '';
      panels.cash.querySelector('.live-metrics').innerHTML = account ? account.cash_by_currency.map(x =>
        `<div class="metric"><div class="label">${esc(x.currency)} balance</div><div class="value">${num(x.balance)} ${esc(x.currency)}</div></div>`
      ).join('') + `<div class="metric"><div class="label">Total cash</div><div class="value">${num(account.total_cash)} ${esc(account.base_currency)}</div></div>` : '';
    }
  }
  function showPositions(data) {
    panels.holdings.hidden = false;
    panels.holdings.querySelector('.live-asof').textContent = data.observed_at ? ' · ' + hkt(data.observed_at) : '';
    panels.holdings.querySelector('.live-rows').innerHTML = data.positions.length ? data.positions.map(x => `<tr>
      <td><strong>${esc(x.symbol)}</strong></td><td>${esc(x.description || x.sec_type)}</td><td>${qty(x.quantity)}</td>
      <td>${x.price == null ? '—' : num(x.price, x.price < 10 ? 4 : 2)}</td><td>${percent(x.change_pct)}</td><td>${esc(x.currency)}</td>
      <td>${num(x.market_value)}</td><td>${signed(x.unrealized_pnl)}</td>
      <td><span class="live-source ${x.price_source === 'delayed' ? 'delayed' : ''}">${{live: 'Live', delayed: 'Delayed', frozen: 'Last close', none: 'No price'}[x.price_source] || ''}</span></td>
    </tr>`).join('') : '<tr><td colspan="9" class="muted">No open positions in the latest live snapshot.</td></tr>';
  }
  function showTrades(data) {
    panels.trades.hidden = false;
    panels.trades.querySelector('.live-rows').innerHTML = data.trades.length ? data.trades.map(x => `<tr>
      <td>${esc(hkt(x.executed_at, true))}</td><td><strong>${esc(x.symbol)}</strong>${x.confirmed ? '' : '<span class="live-flag">Not in statement yet</span>'}</td>
      <td>${x.side === 'BUY' ? 'Buy' : 'Sell'}</td><td>${qty(x.quantity)}</td><td>${num(x.price, 4)}</td><td>${esc(x.currency)}</td>
      <td>${x.commission == null ? '—' : num(x.commission) + ' ' + esc(x.commission_currency || '')}</td><td>${signed(x.realized_pnl)}</td>
    </tr>`).join('') : '<tr><td colspan="8" class="muted">No executions in the last 24 hours.</td></tr>';
  }
  function hideAll() {
    badge.hidden = true;
    Object.values(panels).forEach(node => { if (node) node.hidden = true; });
  }
  async function load() {
    if (stopped || busy || document.hidden) return;
    clearTimeout(timer); busy = true; controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const summary = await read(base);
      showSummary(summary);
      if (config.section === 'holdings') showPositions(await read(base + '/positions'));
      if (config.section === 'trades') showTrades(await read(base + '/trades'));
      failures = 0;
    } catch (error) {
      if (stopped) { hideAll(); return; }
      failures++;
      badge.className = 'freshness live-badge late'; badge.hidden = false;
      badge.textContent = 'Live data unavailable · retrying';
    } finally {
      clearTimeout(timeout); controller = null; busy = false;
      if (!stopped) timer = setTimeout(load, failures ? Math.min(300000, 30000 * (2 ** (failures - 1))) : pollSeconds * 1000);
    }
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); controller?.abort(); });
  load();
})();
</script>
"""


def live_assets(account_id: str, section: str) -> str:
    """The style and script for one account page; JSON-encoded so no value can break out."""
    config = json.dumps({"account": account_id, "section": section}).replace("<", "\\u003c")
    return f"<style>{LIVE_CSS}</style>" + _SCRIPT.replace("__CONFIG__", config)
