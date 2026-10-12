"""Markup and browser script for the institutional analytics cards.

The cards sit on the account Performance tab (return statistics, monthly
returns calendar, drawdowns and rolling risk) and the Holdings tab
(concentration). Kept as plain strings so the JavaScript braces stay readable.
ANALYTICS_SCRIPT runs inside the account page script after ACCOUNT_UI_SCRIPT and
relies on its helpers (account, esc, json, draw, metricCard, signedPct, pct2,
weightPct, chartColors).
"""

PERFORMANCE_ANALYTICS_CARDS = r"""
<div class="card wide" id="analytics-statistics-card"><div class="label">Return and risk statistics</div><div id="analytics-period" class="muted">Loading…</div><div id="analytics-statistics" class="metrics"></div></div>
<div class="card wide"><div class="label">Monthly returns</div><div class="muted">Time-weighted return for each calendar month and year, valued from the last NAV before the period. The same method as the period returns table.</div><div class="table-scroll"><table class="monthly-returns"><thead><tr><th class="text-col">Year</th><th>Jan</th><th>Feb</th><th>Mar</th><th>Apr</th><th>May</th><th>Jun</th><th>Jul</th><th>Aug</th><th>Sep</th><th>Oct</th><th>Nov</th><th>Dec</th><th>Total</th></tr></thead><tbody id="monthly-returns"></tbody></table></div><div id="monthly-returns-note" class="muted"></div></div>
<div class="card wide"><div class="label">Drawdowns</div><div class="muted">Fall from the previous high in cash-flow-adjusted portfolio value, on every NAV date.</div><div class="range-buttons" data-chart="#drawdown-chart" role="group" aria-label="Chart period"><button type="button" data-range="1m">1M</button><button type="button" data-range="3m">3M</button><button type="button" data-range="ytd">YTD</button><button type="button" data-range="1y">1Y</button><button type="button" data-range="all" class="active" aria-pressed="true">All</button></div><div class="chart-wrap"><canvas id="drawdown-chart" width="1100" height="260" aria-label="Drawdown chart"></canvas><div class="chart-tooltip" role="status" aria-live="polite"></div></div><div class="label" style="margin-top:18px">Deepest drawdowns</div><div class="table-scroll"><table><thead><tr><th class="text-col">Rank</th><th class="text-col">Peak</th><th class="text-col">Trough</th><th class="text-col">Recovered</th><th>Depth</th><th>Peak to trough (days)</th><th>Trough to recovery (days)</th><th>Length (days)</th></tr></thead><tbody id="drawdown-episodes"></tbody></table></div></div>
<div class="card half-card"><div class="label">Rolling 3-month volatility</div><div class="muted">Annualised standard deviation of the latest 63 daily returns.</div><div class="chart-wrap"><canvas id="rolling-volatility-chart" width="540" height="240" aria-label="Rolling three-month volatility chart"></canvas><div class="chart-tooltip" role="status" aria-live="polite"></div></div><div id="rolling-volatility-note" class="muted"></div></div>
<div class="card half-card"><div class="label">Trailing 12-month return</div><div class="muted">Time-weighted return over the 12 months to each NAV date.</div><div class="chart-wrap"><canvas id="rolling-return-chart" width="540" height="240" aria-label="Trailing twelve-month return chart"></canvas><div class="chart-tooltip" role="status" aria-live="polite"></div></div><div id="rolling-return-note" class="muted"></div></div>
<div class="card wide"><div id="analytics-methodology" class="muted analytics-methodology"></div></div>
"""

HOLDINGS_CONCENTRATION_CARD = r"""
<div class="card wide"><div class="label">Concentration</div><div id="concentration-summary" class="muted"></div><div id="concentration-metrics" class="metrics"></div><div class="label" style="margin-top:18px">Currency exposure</div><div id="currency-exposure" class="sector-bars"></div><div id="concentration-note" class="muted sector-note"></div></div>
"""

ANALYTICS_SCRIPT = r"""
const analyticsDate=value=>{if(!value)return '—';const d=new Date(String(value).slice(0,10)+'T00:00:00Z');return Number.isNaN(d.getTime())?String(value):new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}).format(d)};
const analyticsMonth=value=>{if(!value)return '—';const d=new Date(String(value).slice(0,7)+'-01T00:00:00Z');return new Intl.DateTimeFormat('en-GB',{month:'short',year:'numeric',timeZone:'UTC'}).format(d)};
const ratio=n=>n==null||!Number.isFinite(Number(n))?'—':minus(new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(n)));
const heatStyle=n=>{if(n==null||!Number.isFinite(Number(n)))return '';const v=Number(n),alpha=Math.min(1,Math.abs(v)/0.08)*0.42+0.06;return Math.abs(v)<5e-5?'':` style="background:${v>0?`rgba(15,122,74,${alpha.toFixed(2)})`:`rgba(180,35,24,${alpha.toFixed(2)})`}"`};
const calendarCell=cell=>{if(!cell)return '<td class="heat-cell empty-cell"></td>';const failed=cell.status==='fail'||cell.twr==null;const marks=(cell.partial?'†':'')+(cell.to_date?'*':'');return `<td class="heat-cell"${failed?'':heatStyle(cell.twr)} title="${esc(`${analyticsDate(cell.start_date)} to ${analyticsDate(cell.end_date)}${(cell.issues||[]).length?' · '+cell.issues.join(' '):''}`)}" data-sort="${failed?'':cell.twr}">${failed?'n/a':esc(signedPct(cell.twr))}${marks?`<sup>${marks}</sup>`:''}</td>`};
const clearAnalytics=message=>{document.querySelector('#analytics-period').textContent=message;['#analytics-statistics','#monthly-returns','#drawdown-episodes'].forEach(s=>document.querySelector(s).innerHTML='');['#drawdown-chart','#rolling-volatility-chart','#rolling-return-chart'].forEach(id=>draw(id,[],[]));['#monthly-returns-note','#rolling-volatility-note','#rolling-return-note','#analytics-methodology'].forEach(s=>document.querySelector(s).textContent='')};
const renderAnalytics=async asOf=>{
  let a;
  try{a=await fetch(`/api/portfolio/${account}/analytics?as_of=${encodeURIComponent(asOf)}`).then(json)}catch(error){clearAnalytics('Portfolio analytics are unavailable for this date.');return}
  if(!a.available){clearAnalytics(`Portfolio analytics are unavailable: ${(a.issues||[]).join(' ')||'not enough NAV history.'}`);return}
  const s=a.statistics,dd=a.drawdown;
  document.querySelector('#analytics-period').textContent=`From ${analyticsDate(s.period_start)} to ${analyticsDate(s.period_end)} · ${s.return_observations} daily returns on every NAV date · ${a.currency||reportingCurrency}. The Risk tab uses benchmark-aligned dates only, so its figures can differ slightly.`;
  const dayValue=x=>x?`${signedPct(x.return)} · ${analyticsDate(x.date)}`:'—',monthValue=x=>x?`${signedPct(x.return)} · ${analyticsMonth(x.month)}`:'—';
  document.querySelector('#analytics-statistics').innerHTML=[
    ['Sortino ratio',ratio(s.sortino_ratio),'Sortino ratio (0% target)'],
    ['Calmar ratio',s.calmar_ratio==null?'Needs 1 year':ratio(s.calmar_ratio)],
    ['Maximum drawdown (since inception)',pct2(dd.maximum_drawdown)],
    ['Current drawdown',pct2(dd.current_drawdown)],
    ['Annualized volatility',pct2(s.annualized_volatility),'Annualised volatility (all NAV dates)'],
    ['Downside deviation',pct2(s.downside_deviation),'Downside deviation (annualised)'],
    ['Skewness',ratio(s.skewness)],
    ['Excess kurtosis',ratio(s.excess_kurtosis)],
    ['Best and worst day',dayValue(s.best_day),'Best day'],
    ['Best and worst day',dayValue(s.worst_day),'Worst day'],
    ['Best and worst month',monthValue(s.best_month),'Best month'],
    ['Best and worst month',monthValue(s.worst_month),'Worst month'],
    ['Positive months',s.positive_month_ratio==null?'—':`${pct2(s.positive_month_ratio)} · ${s.positive_months} of ${s.measured_months}`],
    ['Average gain and loss month',`${signedPct(s.average_gain_month)} / ${signedPct(s.average_loss_month)}`,'Average gain / loss month'],
    ['Gain/loss ratio',ratio(s.gain_loss_ratio)],
  ].map(metricCard).join('');
  document.querySelector('#monthly-returns').innerHTML=a.monthly_calendar.map(row=>`<tr><th class="text-col" scope="row">${esc(row.year)}</th>${row.months.map(calendarCell).join('')}${calendarCell(row.year_return).replace('heat-cell','heat-cell year-cell')}</tr>`).join('')||'<tr><td colspan="14">No monthly history is available.</td></tr>';
  document.querySelector('#monthly-returns-note').textContent='† History starts after the period began, so the return runs from the first NAV. * Period to date. Hover a cell for its exact NAV dates.';
  draw('#drawdown-chart',dd.series.map(x=>({date:x.date,drawdown:100*x.drawdown})),[{key:'drawdown',color:'#b42318',label:'Drawdown'}],{yLabel:'Drawdown (%)',unit:'%',niceYTicks:true,targetYIntervals:5});
  document.querySelector('#drawdown-episodes').innerHTML=dd.episodes.map((x,i)=>`<tr><td>${i+1}</td><td>${esc(analyticsDate(x.peak_date))}</td><td>${esc(analyticsDate(x.trough_date))}</td><td>${x.recovered?esc(analyticsDate(x.recovery_date)):'<span class="muted">Not yet</span>'}</td><td data-sort="${x.depth}"><span class="loss">${esc(pct2(x.depth))}</span></td><td>${esc(x.days_to_trough)}</td><td>${x.days_to_recover==null?'—':esc(x.days_to_recover)}</td><td>${esc(x.length_days)}${x.recovered?'':' <span class="muted">(ongoing)</span>'}</td></tr>`).join('')||'<tr><td colspan="8">No drawdown has occurred in this history.</td></tr>';
  const vol=a.rolling.filter(x=>x.volatility!=null).map(x=>({date:x.date,volatility:100*x.volatility})),trailing=a.rolling.filter(x=>x.trailing_return!=null).map(x=>({date:x.date,trailing_return:100*x.trailing_return}));
  draw('#rolling-volatility-chart',vol,[{key:'volatility',color:chartColors.portfolio,label:'Rolling 3-month volatility'}],{yLabel:'Volatility (%)',unit:'%',niceYTicks:true,targetYIntervals:5});
  draw('#rolling-return-chart',trailing,[{key:'trailing_return',color:chartColors.portfolio,label:'Trailing 12-month return'}],{yLabel:'Return (%)',unit:'%',niceYTicks:true,targetYIntervals:5});
  document.querySelector('#rolling-volatility-note').textContent=vol.length?'':'Needs at least 63 daily returns.';
  document.querySelector('#rolling-return-note').textContent=trailing.length?'':'Needs at least 12 months of NAV history.';
  document.querySelector('#analytics-methodology').textContent=`Methodology: ${a.methodology}`;
};
const renderConcentration=h=>{
  const c=h&&h.concentration,metrics=document.querySelector('#concentration-metrics'),bars=document.querySelector('#currency-exposure'),summary=document.querySelector('#concentration-summary'),note=document.querySelector('#concentration-note');
  if(!metrics)return;
  if(!c||!c.available){summary.textContent=(c&&c.reason)||'Concentration is not available for this portfolio.';metrics.innerHTML='';bars.innerHTML='';note.textContent='';return}
  summary.textContent=`${c.weighted_count} weighted holdings as of ${analyticsDate(h.snapshot_date)}. Weights are shares of ${c.weight_basis==='nav'?'net asset value':'gross holdings value'}.`;
  metrics.innerHTML=[
    ['Largest holding',`${c.largest.symbol||'—'} · ${weightPct(c.largest.weight)}`],
    ['Top 5 weight',weightPct(c.top_5_weight)],
    ['Top 10 weight',weightPct(c.top_10_weight)],
    ['Effective number of holdings',c.effective_holdings==null?'—':ratio(c.effective_holdings)],
  ].map(metricCard).join('');
  const rows=[...c.currency_exposure];if(c.residual_weight!=null&&Math.abs(c.residual_weight)>=5e-4)rows.push({currency:'Cash and other',weight:c.residual_weight});
  const top=Math.max(...rows.map(x=>Math.abs(x.weight)),1e-9);
  bars.innerHTML=rows.map(x=>`<div class="sector-row"><span class="sector-name"><strong>${esc(x.currency)}</strong></span><span class="sector-track" aria-hidden="true"><span class="sector-fill" style="width:${(100*Math.abs(x.weight)/top).toFixed(1)}%"></span></span><span class="sector-weight">${esc(weightPct(x.weight))}</span></div>`).join('');
  note.textContent=`Currency is each holding's trading currency. Effective number of holdings is 1 ÷ the sum of squared gross weights: the number of equal-sized positions with the same concentration.${c.weighted_count<c.holdings_count?` ${c.holdings_count-c.weighted_count} holding(s) without a usable FX rate are excluded.`:''}`;
};
wrTable('#drawdown-episodes');
"""
