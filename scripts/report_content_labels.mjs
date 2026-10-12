// Deterministic labels only. Never change NAV, cash transactions or returns.
export function cashActivityNote(cash, formatMoney) {
  const fields = ['total_dividends', 'interest_received', 'interest_paid',
    'fees_and_withholding', 'other_internal_cash', 'net_listed_cash_activity'];
  if (fields.some(key => typeof cash[key] !== 'number' || !Number.isFinite(cash[key]))) {
    throw new Error('Complete finite cash activity is required');
  }
  const financing = cash.interest_received + cash.interest_paid + cash.fees_and_withholding;
  const total = cash.total_dividends + financing + cash.other_internal_cash;
  if (Math.abs(total - cash.net_listed_cash_activity) > 0.005) {
    throw new Error('Cash activity does not reconcile');
  }
  return `Dividends ${formatMoney(cash.total_dividends)}, net interest/fees ${formatMoney(financing)} and other internal cash ${formatMoney(cash.other_internal_cash)} total ${formatMoney(cash.net_listed_cash_activity)}.`;
}

export function monthlyCalendarNote(rows, series, formatDate) {
  if (!rows?.length || !series?.length) throw new Error('Monthly comparison history is required');
  const start = series[0].date;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || new Date(start).toISOString().slice(0,10) !== start) {
    throw new Error('Invalid comparison start date');
  }
  const first = rows[0];
  if (first.date.slice(0,7) !== start.slice(0,7)) throw new Error('Monthly start does not match comparison history');
  const firstMonth = new Intl.DateTimeFormat('en-GB', {month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(start));
  return first.portfolio_monthly_return == null
    ? `${firstMonth} has no monthly return because no prior month-end comparison exists. Common history starts ${formatDate(start)}.`
    : `Common history starts ${formatDate(start)}. Monthly returns use the frozen comparison series.`;
}

export function attributionResidualNote(monthly, formatPoints) {
  if (!Number.isFinite(monthly.residual_contribution)) throw new Error('Finite residual required');
  const usual = `Security effects use prior-day units and price changes. The ${formatPoints(monthly.residual_contribution)} residual captures cash, fees, FX, trading and unmatched position effects.`;
  if (monthly.method_version !== 'monthly-reporting-attribution-v2-full-nav-coverage') return usual;
  const c = monthly.position_coverage;
  if (!c || !Number.isInteger(c.nav_intervals) || c.nav_intervals < 1 ||
      c.nav_intervals !== monthly.daily_observations || !Number.isInteger(c.matched_position_intervals) ||
      c.matched_position_intervals < 1 || !Array.isArray(c.residual_only_intervals) ||
      !Array.isArray(c.missing_position_dates) ||
      c.matched_position_intervals + c.residual_only_intervals.length !== c.nav_intervals) {
    throw new Error('Reconciled position coverage required');
  }
  for (const dates of [c.residual_only_intervals, c.missing_position_dates]) {
    if (new Set(dates).size !== dates.length || dates.some((d, i) =>
      typeof d !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(d) ||
      !Number.isFinite(Date.parse(d)) || new Date(d).toISOString().slice(0,10) !== d ||
      (i > 0 && dates[i-1] >= d))) throw new Error('Valid ordered coverage dates required');
  }
  if (c.residual_only_intervals.some(d => d < monthly.period_start || d > monthly.period_end)) {
    throw new Error('Residual interval outside reporting period');
  }
  const count = c.residual_only_intervals.length;
  return count === 0 ? usual : `All ${c.nav_intervals} NAV intervals are included. ${count} without matched positions ${count === 1 ? 'is' : 'are'} residual-only. The ${formatPoints(monthly.residual_contribution)} residual also includes cash, fees, FX and trading; no holdings are invented.`;
}
