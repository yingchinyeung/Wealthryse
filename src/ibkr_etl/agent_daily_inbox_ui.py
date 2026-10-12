"""Read-only owner inbox. No execution, approval or financial-report release."""

DAILY_INBOX_CONTENT = """
<div class="page-view active" data-section="ai-operations">
  <section class="card wide" aria-labelledby="daily-inbox-heading">
    <h2 id="daily-inbox-heading">Daily specialist activity reports</h2>
    <p>Reporting windows close at 18:00 Hong Kong time. Only verified, published
    reports appear here. Website availability and email delivery are tracked separately.</p>
    <p id="daily-inbox-status" role="status" aria-live="polite">Checking the daily-report inbox…</p>
    <button class="button" type="button" id="daily-inbox-refresh">Refresh reports</button>
    <ul id="daily-inbox-list" aria-busy="true"></ul>
    <h3 id="daily-report-title"></h3>
    <pre id="daily-report-text" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>
    <noscript>Enable JavaScript to read reports and receive automatic status updates.</noscript>
  </section>
</div>
"""

DAILY_INBOX_SCRIPT = r"""
<script>
(() => {
  const status = document.querySelector('#daily-inbox-status');
  const list = document.querySelector('#daily-inbox-list');
  const title = document.querySelector('#daily-report-title');
  const text = document.querySelector('#daily-report-text');
  const refresh = document.querySelector('#daily-inbox-refresh');
  const emailLabels = {pending:'Email pending', leased:'Email queued', sending:'Email submitting',
    accepted:'Email accepted by provider; delivery unconfirmed', delivered:'Email delivered',
    uncertain:'Email status uncertain — review needed', failed:'Email needs attention', not_queued:'Email not queued'};
  let stopped = false, busy = false, failures = 0, timer = null, controller = null;
  function clearReport() { title.textContent = ''; text.textContent = ''; }
  async function read(path) {
    const response = await fetch(path, {cache: 'no-store', credentials: 'same-origin', signal: controller.signal});
    if (response.status === 401 || response.status === 403) {
      stopped = true; list.replaceChildren(); clearReport();
      throw new Error('Access is no longer available. Sign in with an authorized owner account.');
    }
    if (!response.ok) throw new Error('Reports are temporarily unavailable. No new delivery is confirmed.');
    return response.json();
  }
  async function load(day) {
    if (stopped || busy || document.hidden) return;
    clearTimeout(timer); busy = true; refresh.disabled = true;
    list.setAttribute('aria-busy', 'true'); controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      if (day) {
        clearReport();
        const report = await read('/api/ai-operations/daily-reports/' + encodeURIComponent(day));
        if (stopped) return;
        if (report.report_date !== day || typeof report.text !== 'string' || typeof report.title !== 'string')
          throw new Error('The report response could not be verified.');
        title.textContent = report.title; text.textContent = report.text;
        status.textContent = 'Report opened. This does not approve changes or release financial reports.';
      } else {
        const data = await read('/api/ai-operations/daily-reports');
        if (stopped) return;
        if (!Array.isArray(data.reports) || data.reports.length > 30) throw new Error('Invalid inbox response.');
        list.replaceChildren();
        for (const item of data.reports) {
          if (!/^\d{4}-\d{2}-\d{2}$/.test(item.report_date) || item.website_state !== 'available')
            throw new Error('Invalid report listing.');
          const li = document.createElement('li'), button = document.createElement('button');
          button.type = 'button'; button.className = 'button'; button.textContent = item.report_date + ' · Read report';
          button.addEventListener('click', () => load(item.report_date));
          li.append(button, document.createTextNode(' — Available on website · ' + (emailLabels[item.email_state] || 'Email status unavailable')));
          list.append(li);
        }
        status.textContent = data.reports.length ? 'Inbox updated just now. Showing the latest 30 reports at most.'
          : 'No daily reports have been published yet. This does not confirm that the daily service is active.';
      }
      failures = 0;
    } catch (error) {
      failures++; list.replaceChildren(); clearReport();
      status.textContent = error.name === 'AbortError' ? 'Status check timed out. Retrying automatically.' : error.message;
    } finally {
      clearTimeout(timeout); controller = null; busy = false;
      list.setAttribute('aria-busy', 'false'); refresh.disabled = stopped;
      if (!stopped) timer = setTimeout(() => load(), Math.min(120000, 30000 * (2 ** failures)));
    }
  }
  refresh.addEventListener('click', () => load());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); controller?.abort(); });
  load();
})();
</script>
"""
