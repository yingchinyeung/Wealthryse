"""Markup and browser script for the institutional analytics cards.

The cards sit on the account Performance tab (monthly returns calendar and
contribution to return), the Risk tab (return statistics, drawdowns and rolling
risk) and the Holdings tab (concentration). Kept as plain strings so the JavaScript braces stay readable.
ANALYTICS_SCRIPT runs inside the account page script after ACCOUNT_UI_SCRIPT and
relies on its helpers (account, esc, json, draw, metricCard, signedPct, pct2,
weightPct, chartColors).
"""

PERFORMANCE_ANALYTICS_CARDS = r"""
<div class="card wide" id="perf-calendar"><div class="label">Monthly returns</div><div class="muted">Time-weighted return for each calendar month and year, valued from the last NAV before the period. The same method as the period returns table.</div><div class="table-scroll"><table class="monthly-returns"><thead><tr><th class="text-col">Year</th><th>Jan</th><th>Feb</th><th>Mar</th><th>Apr</th><th>May</th><th>Jun</th><th>Jul</th><th>Aug</th><th>Sep</th><th>Oct</th><th>Nov</th><th>Dec</th><th>Total</th></tr></thead><tbody id="monthly-returns"></tbody></table></div><div id="monthly-returns-note" class="muted"></div></div>
<div class="card wide" id="perf-attribution"><div class="label">Contribution to return</div><div class="muted">How much each holding and sector added to or took from the portfolio's return in a month. Daily mark-to-market effects on the prior day's units are linked so they add up to the month's return; the residual holds cash, fees, FX and trading effects.</div><div class="actions"><select id="attribution-month" aria-label="Attribution month"></select></div><div id="attribution-summary" class="metrics"></div><div class="contributor-grid"><div><h3>Top contributors</h3><div class="table-scroll"><table><thead><tr><th class="text-col">Holding</th><th class="text-col">Sector</th><th>Contribution</th></tr></thead><tbody id="attribution-top"></tbody></table></div></div><div><h3>Top detractors</h3><div class="table-scroll"><table><thead><tr><th class="text-col">Holding</th><th class="text-col">Sector</th><th>Contribution</th></tr></thead><tbody id="attribution-bottom"></tbody></table></div></div></div><div class="label" style="margin-top:18px">By sector</div><div id="attribution-sectors" class="sector-bars"></div><div id="attribution-note" class="muted sector-note"></div></div>
"""

RISK_ANALYTICS_CARDS = r"""
<div class="card wide" id="analytics-statistics-card"><div class="label">Return and risk statistics</div><div id="analytics-period" class="muted">Loading…</div><div id="analytics-statistics" class="metrics"></div></div>
<div class="card wide" id="risk-drawdowns"><div class="label">Drawdowns</div><div class="muted">Fall from the previous high in cash-flow-adjusted portfolio value, on every NAV date.</div><div class="range-buttons" data-chart="#drawdown-chart" role="group" aria-label="Chart period"><button type="button" data-range="1m">1M</button><button type="button" data-range="3m">3M</button><button type="button" data-range="ytd">YTD</button><button type="button" data-range="1y">1Y</button><button type="button" data-range="all" class="active" aria-pressed="true">All</button></div><div class="chart-wrap"><canvas id="drawdown-chart" width="1100" height="260" aria-label="Drawdown chart"></canvas><div class="chart-tooltip" role="status" aria-live="polite"></div></div><div class="label" style="margin-top:18px">Deepest drawdowns</div><div class="table-scroll"><table><thead><tr><th class="text-col">Rank</th><th class="text-col">Peak</th><th class="text-col">Trough</th><th class="text-col">Recovered</th><th>Depth</th><th>Peak to trough (days)</th><th>Trough to recovery (days)</th><th>Length (days)</th></tr></thead><tbody id="drawdown-episodes"></tbody></table></div></div>
<div class="card half-card" id="risk-rolling"><div class="label">Rolling 3-month volatility</div><div class="muted">Annualised standard deviation of the latest 63 daily returns.</div><div class="chart-wrap"><canvas id="rolling-volatility-chart" width="540" height="240" aria-label="Rolling three-month volatility chart"></canvas><div class="chart-tooltip" role="status" aria-live="polite"></div></div><div id="rolling-volatility-note" class="muted"></div></div>
<div class="card half-card"><div class="label">Trailing 12-month return</div><div class="muted">Time-weighted return over the 12 months to each NAV date.</div><div class="chart-wrap"><canvas id="rolling-return-chart" width="540" height="240" aria-label="Trailing twelve-month return chart"></canvas><div class="chart-tooltip" role="status" aria-live="polite"></div></div><div id="rolling-return-note" class="muted"></div></div>
<div class="card wide"><div class="label">Rolling 6-month beta and correlation</div><div class="muted">Against the comparison benchmark chosen on the Performance tab (the primary benchmark unless you change it), using the latest 126 common-date daily returns.</div><div class="chart-wrap"><canvas id="rolling-beta-chart" width="1100" height="260" aria-label="Rolling beta and correlation chart"></canvas><div class="chart-tooltip" role="status" aria-live="polite"></div></div><div class="legend"><span><i class="swatch portfolio"></i>Beta</span><span><i class="swatch index"></i>Correlation</span></div><div id="rolling-beta-note" class="muted"></div></div>
<div class="card wide"><div id="analytics-methodology" class="muted analytics-methodology"></div></div>
"""

HOLDINGS_CONCENTRATION_CARD = r"""
<div class="card wide"><div class="label">Concentration</div><div id="concentration-summary" class="muted"></div><div id="concentration-metrics" class="metrics"></div><div class="label" style="margin-top:18px">Currency exposure</div><div id="currency-exposure" class="sector-bars"></div><div id="concentration-note" class="muted sector-note"></div></div>
"""

TRADING_ACTIVITY_CARD = r"""
<div class="card wide"><div class="label">Trading activity</div><div id="trading-summary-note" class="muted">Loading…</div><div id="trading-summary" class="metrics"></div><div class="table-scroll"><table><thead><tr><th class="text-col">Month</th><th>Trades</th><th>Purchases</th><th>Sales</th><th>Turnover</th><th>Commissions</th><th>Commissions / NAV</th><th>Realised P&amp;L</th></tr></thead><tbody id="trading-monthly"></tbody></table></div><div id="trading-note" class="muted"></div></div>
"""

CORRELATION_CARD = r"""
<div class="card wide" id="risk-correlation"><div class="label">Correlation between the largest holdings</div><div id="correlation-summary" class="muted">Loading…</div><div class="table-scroll"><table class="correlation-matrix"><thead id="correlation-head"></thead><tbody id="correlation-body"></tbody></table></div><div id="correlation-note" class="muted sector-note"></div></div>
"""

ANALYTICS_SCRIPT = r"""
const analyticsDate=value=>{if(!value)return '—';const d=new Date(String(value).slice(0,10)+'T00:00:00Z');return Number.isNaN(d.getTime())?String(value):new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}).format(d)};
const analyticsMonth=value=>{if(!value)return '—';const d=new Date(String(value).slice(0,7)+'-01T00:00:00Z');return new Intl.DateTimeFormat('en-GB',{month:'short',year:'numeric',timeZone:'UTC'}).format(d)};
const ratio=n=>n==null||!Number.isFinite(Number(n))?'—':minus(new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(n)));
const heatStyle=n=>{if(n==null||!Number.isFinite(Number(n)))return '';const v=Number(n),alpha=Math.min(1,Math.abs(v)/0.08)*0.42+0.06;return Math.abs(v)<5e-5?'':` style="background:${v>0?`rgba(15,122,74,${alpha.toFixed(2)})`:`rgba(180,35,24,${alpha.toFixed(2)})`}"`};
const calendarCell=cell=>{if(!cell)return '<td class="heat-cell empty-cell"></td>';const failed=cell.status==='fail'||cell.twr==null;const marks=(cell.partial?'†':'')+(cell.to_date?'*':'');return `<td class="heat-cell"${failed?'':heatStyle(cell.twr)} title="${esc(`${analyticsDate(cell.start_date)} to ${analyticsDate(cell.end_date)}${(cell.issues||[]).length?' · '+cell.issues.join(' '):''}`)}" data-sort="${failed?'':cell.twr}">${failed?'n/a':esc(signedPct(cell.twr))}${marks?`<sup>${marks}</sup>`:''}</td>`};
const clearAnalytics=message=>{document.querySelector('#analytics-period').textContent=message;['#analytics-statistics','#monthly-returns','#drawdown-episodes'].forEach(s=>document.querySelector(s).innerHTML='');['#drawdown-chart','#rolling-volatility-chart','#rolling-return-chart'].forEach(id=>draw(id,[],[],{emptyMessage:'Analytics are unavailable for this date.'}));['#monthly-returns-note','#rolling-volatility-note','#rolling-return-note','#analytics-methodology'].forEach(s=>document.querySelector(s).textContent='')};
const renderAnalytics=async asOf=>{
  let a;
  try{a=await fetch(`/api/portfolio/${account}/analytics?as_of=${encodeURIComponent(asOf)}`).then(json)}catch(error){clearAnalytics('Portfolio analytics are unavailable for this date.');return}
  if(!a.available){clearAnalytics(`Portfolio analytics are unavailable: ${(a.issues||[]).join(' ')||'not enough NAV history.'}`);return}
  const s=a.statistics,dd=a.drawdown;
  document.querySelector('#analytics-period').textContent=`From ${analyticsDate(s.period_start)} to ${analyticsDate(s.period_end)} · ${s.return_observations} daily returns on every NAV date · ${a.currency||reportingCurrency}. The risk snapshot above uses benchmark-aligned dates only, so figures shown in both can differ slightly.`;
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
  fillAttributionMonths(a.monthly_calendar);
  document.querySelector('#monthly-returns-note').textContent='† History starts after the period began, so the return runs from the first NAV. * Period to date. Hover a cell for its exact NAV dates.';
  draw('#drawdown-chart',dd.series.map(x=>({date:x.date,drawdown:100*x.drawdown})),[{key:'drawdown',color:'#b42318',label:'Drawdown'}],{yLabel:'Drawdown (%)',unit:'%',niceYTicks:true,targetYIntervals:5,emptyMessage:'Drawdowns need at least two NAV dates.'});
  document.querySelector('#drawdown-episodes').innerHTML=dd.episodes.map((x,i)=>`<tr><td>${i+1}</td><td>${esc(analyticsDate(x.peak_date))}</td><td>${esc(analyticsDate(x.trough_date))}</td><td>${x.recovered?esc(analyticsDate(x.recovery_date)):'<span class="muted">Not yet</span>'}</td><td data-sort="${x.depth}"><span class="loss">${esc(pct2(x.depth))}</span></td><td>${esc(x.days_to_trough)}</td><td>${x.days_to_recover==null?'—':esc(x.days_to_recover)}</td><td>${esc(x.length_days)}${x.recovered?'':' <span class="muted">(ongoing)</span>'}</td></tr>`).join('')||'<tr><td colspan="8">No drawdown has occurred in this history.</td></tr>';
  const vol=a.rolling.filter(x=>x.volatility!=null).map(x=>({date:x.date,volatility:100*x.volatility})),trailing=a.rolling.filter(x=>x.trailing_return!=null).map(x=>({date:x.date,trailing_return:100*x.trailing_return}));
  draw('#rolling-volatility-chart',vol,[{key:'volatility',color:chartColors.portfolio,label:'Rolling 3-month volatility'}],{yLabel:'Volatility (%)',unit:'%',niceYTicks:true,targetYIntervals:5,emptyMessage:'Needs at least 63 daily returns.'});
  draw('#rolling-return-chart',trailing,[{key:'trailing_return',color:chartColors.portfolio,label:'Trailing 12-month return'}],{yLabel:'Return (%)',unit:'%',niceYTicks:true,targetYIntervals:5,emptyMessage:'Needs at least 12 months of NAV history.'});
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
const contributionRows=items=>items.map(x=>`<tr><td><strong>${esc(x.symbol||x.conid)}</strong><div class="muted">${esc(x.description||'')}</div></td><td>${esc(x.sector||'')}</td><td data-sort="${x.contribution}">${toned(esc(signedPct(x.contribution)),x.contribution)}</td></tr>`).join('')||'<tr><td colspan="3">None this month.</td></tr>';
const renderAttribution=async month=>{
  const summary=document.querySelector('#attribution-summary'),note=document.querySelector('#attribution-note');
  ['#attribution-top','#attribution-bottom','#attribution-sectors'].forEach(x=>document.querySelector(x).innerHTML='');summary.innerHTML='';
  if(!month){note.textContent='No months are available.';return}
  note.textContent='Loading…';
  let r;try{r=await fetch(`/api/portfolio/${account}/attribution?${new URLSearchParams({month,as_of:document.querySelector('#asof-select').value})}`).then(json)}catch(error){note.textContent='Contribution to return is unavailable for this month.';return}
  if(!r.available){note.textContent=r.message||'Contribution to return is unavailable for this month.';return}
  const rows=r.security_contributions||[],positive=rows.filter(x=>x.contribution>0).sort((a,b)=>b.contribution-a.contribution).slice(0,10),negative=rows.filter(x=>x.contribution<0).sort((a,b)=>a.contribution-b.contribution).slice(0,10);
  summary.innerHTML=[['Contribution to return',signedPct(r.portfolio_monthly_return),'Portfolio return (attribution basis)'],['Contribution to return',signedPct(r.equity_contribution),'Holdings contribution'],['Contribution to return',signedPct(r.residual_contribution),'Residual (cash, fees, FX, trading)'],['Contribution to return',r.cash_activity?money(r.cash_activity.total_dividends)+' '+esc(r.reporting_currency||reportingCurrency):'—','Dividends received']].map(metricCard).join('');
  document.querySelector('#attribution-top').innerHTML=contributionRows(positive);document.querySelector('#attribution-bottom').innerHTML=contributionRows(negative);
  const sectors=(r.sector_contributions||[]).slice().sort((a,b)=>b.contribution-a.contribution),top=Math.max(...sectors.map(x=>Math.abs(x.contribution)),1e-9);
  document.querySelector('#attribution-sectors').innerHTML=sectors.map(x=>`<div class="sector-row"><span class="sector-name"><strong>${esc(x.sector)}</strong></span><span class="sector-track" aria-hidden="true"><span class="sector-fill${x.contribution<0?' negative':''}" style="width:${(100*Math.abs(x.contribution)/top).toFixed(1)}%"></span></span><span class="sector-weight">${toned(esc(signedPct(x.contribution)),x.contribution)}</span></div>`).join('')||'<div class="empty">No sector contributions.</div>';
  note.textContent=`${analyticsDate(r.period_start)} to ${analyticsDate(r.period_end)} · ${r.daily_observations} daily observations. ${r.message||''} The attribution return uses dates with both NAV and positions, so it can differ slightly from the monthly returns calendar.`;
};
document.querySelector('#attribution-month').addEventListener('change',event=>renderAttribution(event.target.value));
const fillAttributionMonths=calendarRows=>{const select=document.querySelector('#attribution-month'),current=select.value,months=calendarRows.flatMap(row=>row.months.map((cell,i)=>cell?`${row.year}-${String(i+1).padStart(2,'0')}`:null).filter(Boolean).reverse());select.innerHTML=months.map(m=>`<option value="${m}">${esc(analyticsMonth(m))}</option>`).join('');if(months.includes(current))select.value=current;if(activeSection==='performance')void renderAttribution(select.value)};
const renderRollingBenchmark=bc=>{const rows=(bc&&bc.rolling||[]).filter(x=>x.beta!=null&&x.correlation!=null);draw('#rolling-beta-chart',rows,rows.length?[{key:'beta',color:chartColors.portfolio,label:'Beta'},{key:'correlation',color:chartColors.benchmark,label:'Correlation'}]:[],{yLabel:'Beta / correlation',niceYTicks:true,targetYIntervals:5,emptyMessage:bc?'Needs at least 126 common-date returns with the benchmark.':'No validated benchmark comparison is available for this date.'});document.querySelector('#rolling-beta-note').textContent=bc?(rows.length?`Benchmark: ${bc.benchmark.name}. Beta is the covariance with the benchmark divided by its variance; 1.0 moves one-for-one. Correlation runs from −1 to +1.`:'Needs at least 126 common-date returns with the benchmark.'):'No validated benchmark comparison is available for this date, so rolling beta cannot be shown.'};
const bp=n=>n==null||!Number.isFinite(Number(n))?'—':`${(Number(n)*10000).toFixed(1)} bp`;
const renderTradingActivity=async asOf=>{
  const note=document.querySelector('#trading-summary-note');if(!note)return;
  let t;try{t=await fetch(`/api/portfolio/${account}/trading-activity?as_of=${encodeURIComponent(asOf)}`).then(json)}catch(error){note.textContent='Trading activity is unavailable for this date.';return}
  const body=document.querySelector('#trading-monthly'),summary=document.querySelector('#trading-summary');
  if(!t.available){note.textContent=t.message||'No trading activity is available.';summary.innerHTML='';body.innerHTML='';document.querySelector('#trading-note').textContent='';return}
  const c=t.currency,y=t.trailing_12_months;
  note.textContent=`Last ${y.months} months to ${analyticsDate(t.effective_as_of)}, in ${c}.`;
  summary.innerHTML=[['Portfolio turnover',pct2(y.annualized_turnover),'Annualised turnover'],['Commissions / NAV',bp(y.commission_ratio),'Commissions / average NAV'],['Trading activity',`${money(y.commissions)} ${c}`,'Commissions paid'],['Trading activity',`${signedMoney(y.realized_pnl)} ${c}`,'Realised P&L'],['Trading activity',String(y.trades),'Trades']].map(metricCard).join('');
  body.innerHTML=t.monthly.slice(0,24).map(x=>`<tr><td>${esc(analyticsMonth(x.month))}</td><td>${x.trades}</td><td>${money(x.purchases)}</td><td>${money(x.sales)}</td><td>${pct2(x.turnover)}</td><td>${money(x.commissions)}</td><td>${bp(x.commission_ratio)}</td><td data-sort="${x.realized_pnl}">${toned(signedMoney(x.realized_pnl),x.realized_pnl)}</td></tr>`).join('');
  document.querySelector('#trading-note').textContent=`Turnover is the lesser of purchases and sales divided by average NAV, so new money that is simply invested does not count. Values are converted to ${c} with the dated FX rate on or before each trade. Realised P&L is as reported by IBKR. bp = basis points (0.01%). The table shows the latest 24 months; the CSV export below has every trade.${(t.missing_fx_currencies||[]).length?` Trades in ${t.missing_fx_currencies.join(', ')} have no usable FX rate and are left out.`:''}`;
};
const corrStyle=v=>{if(v==null)return '';const a=Math.min(1,Math.abs(v))*0.55+0.05;return ` style="background:${v>=0?`rgba(38,70,166,${a.toFixed(2)})`:`rgba(217,119,6,${a.toFixed(2)})`};color:${Math.abs(v)>0.6?'#fff':'inherit'}"`};
const renderCorrelation=async asOf=>{
  const summary=document.querySelector('#correlation-summary');if(!summary)return;
  const head=document.querySelector('#correlation-head'),body=document.querySelector('#correlation-body'),note=document.querySelector('#correlation-note');
  let r;try{r=await fetch(`/api/portfolio/${account}/holdings-correlation?as_of=${encodeURIComponent(asOf)}`).then(json)}catch(error){summary.textContent='Correlation is unavailable for this date.';return}
  if(!r.available){summary.textContent=r.message||'Not enough shared price history to measure correlation.';head.innerHTML='';body.innerHTML='';note.textContent='';return}
  summary.textContent=`Daily price returns of the ${r.holdings.length} largest holdings from ${analyticsDate(r.window_start)} to ${analyticsDate(r.window_end)}. Average pairwise correlation: ${ratio(r.average_correlation)}.`;
  head.innerHTML=`<tr><th class="text-col"></th>${r.holdings.map(h=>`<th>${esc(h.symbol)}</th>`).join('')}</tr>`;
  body.innerHTML=r.holdings.map((h,i)=>`<tr><th class="text-col" scope="row">${esc(h.symbol)}</th>${r.matrix[i].map((c,j)=>`<td class="corr-cell"${i===j?'':corrStyle(c.correlation)} title="${c.observations} shared daily returns">${i===j?'—':c.correlation==null?'n/a':ratio(c.correlation)}</td>`).join('')}</tr>`).join('');
  note.textContent=`Returns come from each holding's daily mark price in its own currency, so currency moves are not included. Each pair uses the days both were held; pairs with fewer than 40 shared days show n/a.${r.excluded_price_breaks?` ${r.excluded_price_breaks} one-day price moves above 50% were treated as splits or other corporate actions and left out.`:''}`;
};
wrTable('#drawdown-episodes');wrTable('#trading-monthly');wrTable('#attribution-top',{stack:false});wrTable('#attribution-bottom',{stack:false});
"""
