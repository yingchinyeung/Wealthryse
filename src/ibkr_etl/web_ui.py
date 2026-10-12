"""Browser scripts for the account, home and combined holdings pages.

Kept as plain strings (not f-strings) so the JavaScript braces stay readable.
ACCOUNT_UI_SCRIPT runs inside the account page script and relies on its
helpers (account, esc, fmt, pct, json, reportingCurrency, historyStartDate).
"""

# Sortable and filterable tables. Standalone: used on every data table page.
TABLE_SCRIPT = r"""
<script>
(()=>{
const parse=text=>{const clean=String(text).replace(/[−–]/g,'-').replace(/[,\s%+]/g,'');if(!/^-?\d*\.?\d+(e-?\d+)?$/i.test(clean))return null;return Number(clean)};
const cellKey=(row,index)=>{const cell=row.cells[index];return cell?(cell.dataset.sort??cell.textContent.trim()):''};
const sortable=row=>!row.classList.contains('total-row')&&!(row.cells.length===1&&row.cells[0].colSpan>1);
const observers=new WeakMap();
const labelCells=table=>{const names=Array.from(table.tHead.rows[0].cells).map(th=>th.textContent.trim());Array.from(table.tBodies[0]?.rows||[]).forEach(row=>Array.from(row.cells).forEach((cell,i)=>{if(cell.colSpan===1&&names[i])cell.dataset.label=names[i]}))};
const applySort=table=>{
  const body=table.tBodies[0],index=Number(table.dataset.sortIndex),dir=table.dataset.sortDir;
  if(!body||!Number.isFinite(index)||!dir)return;
  const rows=Array.from(body.rows),movable=rows.filter(sortable);if(movable.length<2)return;
  const blank=value=>value===''||value==='—';
  movable.sort((a,b)=>{const x=cellKey(a,index),y=cellKey(b,index);if(blank(x)||blank(y))return blank(x)-blank(y);const nx=parse(x),ny=parse(y),order=nx!==null&&ny!==null?nx-ny:x.localeCompare(y,undefined,{numeric:true,sensitivity:'base'});return dir==='asc'?order:-order});
  const observer=observers.get(body);if(observer)observer.disconnect();
  movable.forEach(row=>body.appendChild(row));rows.filter(row=>!sortable(row)&&row.classList.contains('total-row')).forEach(row=>body.appendChild(row));
  if(observer)observer.observe(body,{childList:true});
};
const applyFilter=table=>{
  const input=table.filterInput;if(!input||!table.tBodies[0])return;
  const term=input.value.trim().toLowerCase();let shown=0;
  Array.from(table.tBodies[0].rows).forEach(row=>{if(!sortable(row))return;const match=!term||row.textContent.toLowerCase().includes(term);row.hidden=!match;if(match)shown++});
  if(table.filterCount)table.filterCount.textContent=term?`${shown} matching`:'';
};
const setHeader=(table,index,dir)=>{Array.from(table.tHead.rows[0].cells).forEach((th,i)=>th.setAttribute('aria-sort',i===index?(dir==='asc'?'ascending':'descending'):'none'))};
const sortBy=(th)=>{const table=th.closest('table'),index=th.cellIndex,current=Number(table.dataset.sortIndex)===index?table.dataset.sortDir:null,numericColumn=th.classList.contains('text-col')?false:index>1,dir=current?(current==='asc'?'desc':'asc'):(numericColumn?'desc':'asc');table.dataset.sortIndex=index;table.dataset.sortDir=dir;setHeader(table,index,dir);applySort(table)};
document.addEventListener('click',event=>{const th=event.target.closest('table.sortable thead th');if(th)sortBy(th)});
document.addEventListener('click',e=>document.querySelectorAll('details.platform-menu[open]').forEach(d=>{if(!d.contains(e.target))d.removeAttribute('open')}));
document.addEventListener('keydown',event=>{const th=event.target.closest&&event.target.closest('table.sortable thead th');if(th&&(event.key==='Enter'||event.key===' ')){event.preventDefault();sortBy(th)}});
window.wrTable=(bodySelector,options={})=>{
  const body=document.querySelector(bodySelector);if(!body)return;const table=body.closest('table');if(!table||table.classList.contains('sortable'))return;
  table.classList.add('sortable');Array.from(table.tHead.rows[0].cells).forEach(th=>{th.tabIndex=0;th.setAttribute('aria-sort','none');th.title='Sort by this column'});
  if(options.filter){const input=document.querySelector(options.filter);if(input){table.filterInput=input;table.filterCount=options.count?document.querySelector(options.count):null;input.addEventListener('input',()=>applyFilter(table))}}
  if(options.stack!==false)table.classList.add('stack-mobile');labelCells(table);
  const observer=new MutationObserver(()=>{labelCells(table);applySort(table);applyFilter(table)});observer.observe(body,{childList:true});observers.set(body,observer);
};
})();
</script>
"""

# Shared number helpers for the account page: fixed two-decimal money, a real
# minus sign, and green/red for gains and losses.
_NUMBER_HELPERS = r"""
const minus=text=>String(text).replace(/^-/,'−');
const money=n=>n==null||n===''||!Number.isFinite(Number(n))?'—':minus(new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(n)));
const compactMoney=n=>n==null||!Number.isFinite(Number(n))?'—':minus(new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:2}).format(Number(n)));
const tone=n=>{const v=Number(n);return n==null||n===''||!Number.isFinite(v)||Math.abs(v)<1e-12?'':v>0?'gain':'loss'};
const pct2=n=>n==null||!Number.isFinite(Number(n))?'—':minus(new Intl.NumberFormat('en-US',{style:'percent',minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(n)));
const weightPct=n=>n==null||!Number.isFinite(Number(n))?'—':minus(new Intl.NumberFormat('en-US',{style:'percent',minimumFractionDigits:1,maximumFractionDigits:1}).format(Number(n)));
const signedPct=n=>{if(n==null||!Number.isFinite(Number(n)))return '—';const v=Number(n),text=pct2(v);return Math.abs(v)<5e-5?text.replace('−',''):(v>0?'+':'')+text};
const signedMoney=n=>{const text=money(n);return text!=='—'&&Number(n)>0?'+'+text:text};
const toned=(text,n)=>{const cls=tone(n);return cls?`<span class="${cls}">${text}</span>`:text};
"""

ACCOUNT_UI_SCRIPT = _NUMBER_HELPERS + r"""
const weightBar=w=>w==null||!Number.isFinite(Number(w))?'—':`<span class="weight-cell"><span class="weight-bar" style="width:${Math.max(2,Math.min(48,Math.abs(Number(w))*48/0.25)).toFixed(1)}px"></span>${esc(weightPct(w))}</span>`;
const holdingsRows=h=>{
  const rows=h.holdings||[];if(!rows.length)return '<tr><td colspan="11">No holdings snapshot is available on or before this date.</td></tr>';
  const body=rows.map(x=>`<tr><td><strong>${esc(x.symbol||x.conid)}</strong></td><td>${esc(x.description||'')}</td><td>${esc(x.sector||'')}</td><td>${esc(x.currency||'')}</td><td>${fmt(x.holding_kind==='synthetic_exposure'?x.coefficient:x.quantity_text)}</td><td>${money(x.market_value_text)}</td><td data-sort="${x.fx_missing?'':esc(x.reporting_market_value_text??'')}">${x.fx_missing?'<span class="muted" title="No dated FX rate is available for this currency">FX unavailable</span>':money(x.reporting_market_value_text)}</td><td>${money(x.cost_basis_text)}</td><td>${toned(signedMoney(x.unrealized_pnl_text),x.unrealized_pnl_text)}</td><td>${toned(signedPct(x.unrealized_pnl_pct),x.unrealized_pnl_pct)}</td><td data-sort="${x.weight??''}">${weightBar(x.weight)}</td></tr>`).join('');
  const values=rows.map(x=>Number(x.reporting_market_value_text)).filter(Number.isFinite),weights=rows.map(x=>Number(x.weight)).filter(Number.isFinite);
  if(!values.length)return body;
  const partial=rows.some(x=>x.fx_missing||x.reporting_market_value_text==null);
  return body+`<tr class="total-row"><td><strong>Total</strong></td><td class="muted">${rows.length} holdings${partial?' · partial, some values lack FX':''}</td><td></td><td></td><td></td><td></td><td><strong>${money(values.reduce((a,b)=>a+b,0))}</strong></td><td></td><td></td><td></td><td><strong>${esc(weightPct(weights.reduce((a,b)=>a+b,0)))}</strong></td></tr>`;
};
const sectorBars=(items,limit)=>{
  const shown=limit?items.slice(0,limit):items,top=Math.max(...shown.map(x=>Number(x.weight)||0),0.0001);
  return shown.map(x=>`<div class="sector-row"><span class="sector-name">${esc(x.sector)}</span><span class="sector-track" aria-hidden="true"><span class="sector-fill" style="width:${(100*(Number(x.weight)||0)/top).toFixed(1)}%"></span></span><span class="sector-weight">${esc(weightPct(x.weight))}</span>${limit?'':`<span class="sector-meta">${esc(x.holdings??'')} holding${x.holdings===1?'':'s'} · ${money(x.market_value)}</span>`}</div>`).join('');
};
const sectorRequests=new Map();
const loadSectors=asOf=>{if(!sectorRequests.has(asOf))sectorRequests.set(asOf,fetch(`/api/portfolio/${account}/sectors?as_of=${encodeURIComponent(asOf)}`).then(json));return sectorRequests.get(asOf)};
const sectorSource=s=>s.classification_basis==='composite sleeve type'?'Grouped by sleeve type from the approved composite calculation.':'Source: Yahoo Finance sector labels, approved for internal analysis only and not for client distribution.';
const renderSectors=async asOf=>{
  const bars=document.querySelector('#sector-bars'),summary=document.querySelector('#sector-summary'),note=document.querySelector('#sector-note'),detail=document.querySelector('#sector-detail');
  if(!bars)return;bars.innerHTML='<div class="skeleton-line"></div><div class="skeleton-line"></div><div class="skeleton-line"></div>';summary.textContent='';note.textContent='';detail.innerHTML='';
  try{
    const s=await loadSectors(asOf),items=s.allocation||[];
    if(!items.length){bars.innerHTML=`<div class="empty">${esc(s.reason||'No sector data is available for this date.')}</div>`;return}
    bars.innerHTML=sectorBars(items);
    const coverage=Number(s.classification_coverage_pct);
    summary.textContent=`${items.length} sectors · positions as of ${s.position_date||'—'} · weights are shares of gross holdings value, cash excluded`;
    detail.innerHTML=(s.allocation_detail||[]).map(x=>`<tr><td>${esc(x.sector)}</td><td>${esc(x.sub_sector)}</td><td>${esc(x.region||'')}</td><td>${esc(x.holdings??'')}</td><td>${money(x.market_value)}</td><td data-sort="${x.weight??''}">${esc(weightPct(x.weight))}</td></tr>`).join('');
    note.textContent=(Number.isFinite(coverage)&&coverage<99.995?`${coverage.toFixed(1)}% of holdings value has a sector. Unclassified: ${(s.unclassified_symbols||[]).join(', ')}. `:'')+sectorSource(s);
  }catch(error){bars.innerHTML='<div class="empty">Sector allocation is unavailable for this date.</div>'}
};
const sparkline=(svg,series)=>{
  const points=series.slice(-260).map(x=>Number(x.nav)).filter(Number.isFinite);if(points.length<2){svg.innerHTML='';return}
  const lo=Math.min(...points),hi=Math.max(...points),span=hi-lo||1,w=320,h=64,xy=points.map((v,i)=>[i*w/(points.length-1),4+(h-8)*(hi-v)/span]);
  const line=xy.map((p,i)=>`${i?'L':'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' '),last=xy[xy.length-1];
  svg.innerHTML=`<path d="${line} L${w} ${h} L0 ${h}Z" class="spark-area"></path><path d="${line}" class="spark-line"></path><circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="3" class="spark-dot"></circle>`;
};
const renderOverview=async(asOf,s)=>{
  const returns=document.querySelector('#overview-returns');if(!returns)return;
  document.querySelector('#overview-asof').textContent=`As of ${s.effective_as_of||asOf} · ${s.latest_nav.currency} reporting`;
  const holdingsList=document.querySelector('#overview-holdings'),sectorList=document.querySelector('#overview-sectors');
  const params=new URLSearchParams({as_of:asOf,start:asOf,end:asOf});
  const [periods,series,holdings,sectors]=await Promise.allSettled([
    fetch(`/api/portfolio/${account}/performance/periods?${params}`).then(json),
    fetch(`/api/portfolio/${account}/performance?as_of=${encodeURIComponent(asOf)}`).then(json),
    fetch(`/api/portfolio/${account}/holdings?as_of=${encodeURIComponent(asOf)}`).then(json),
    loadSectors(asOf)]);
  if(periods.status==='fulfilled'){
    const r=periods.value,by=Object.fromEntries((r.periods||[]).map(x=>[x.key,x])),ok=x=>x&&x.status!=='fail'&&x.status!=='empty',day=r.custom;
    document.querySelector('#overview-day').innerHTML=ok(day)&&day.twr!=null?`${toned(signedPct(day.twr),day.twr)} <span class="muted">day</span>`:'';
    const tile=(label,value,raw,note)=>`<div class="metric"><div class="label">${esc(label)}</div><div class="value">${raw===undefined?value:toned(value,raw)}</div>${note?`<div class="muted">${note}</div>`:''}</div>`;
    const twr=key=>ok(by[key])?signedPct(by[key].twr):'—',since=by.since_inception,ytd=by.ytd;
    returns.innerHTML=[
      tile('Month to date',twr('mtd'),ok(by.mtd)?by.mtd.twr:null),
      tile('Quarter to date',twr('qtd'),ok(by.qtd)?by.qtd.twr:null),
      tile('Year to date',twr('ytd'),ok(ytd)?ytd.twr:null),
      tile('Since inception',twr('since_inception'),ok(since)?since.twr:null,ok(since)&&since.start_date?`from ${esc(since.start_date)}`:''),
      tile('IRR, annualised',ok(since)&&since.irr_annualized!=null?signedPct(since.irr_annualized):'—',ok(since)?since.irr_annualized:null,'since inception, money-weighted'),
      tile('Net flows this year',ok(ytd)&&r.flows_applicable!==false?(Number(ytd.net_flows)>0?'+':'')+compactMoney(ytd.net_flows):'—',undefined,r.flows_applicable===false?'not applicable':esc(r.currency||''))
    ].join('');
  }else returns.innerHTML='<div class="empty">Period returns are unavailable for this date.</div>';
  if(series.status==='fulfilled')sparkline(document.querySelector('#overview-spark'),series.value.series||[]);
  if(holdings.status==='fulfilled'){
    const top=(holdings.value.holdings||[]).filter(x=>x.weight!=null).sort((a,b)=>Math.abs(b.weight)-Math.abs(a.weight)).slice(0,5);
    holdingsList.innerHTML=top.map(x=>`<div class="sector-row"><span class="sector-name"><strong>${esc(x.symbol||x.conid)}</strong> <span class="muted">${esc(x.description||'')}</span></span><span class="sector-track" aria-hidden="true"><span class="sector-fill" style="width:${(100*Math.abs(x.weight)/Math.abs(top[0].weight)).toFixed(1)}%"></span></span><span class="sector-weight">${esc(weightPct(x.weight))}</span></div>`).join('')||'<div class="empty">No holdings on this date.</div>';
  }else holdingsList.innerHTML='<div class="empty">Holdings are unavailable for this date.</div>';
  if(sectors.status==='fulfilled'&&(sectors.value.allocation||[]).length)sectorList.innerHTML=sectorBars(sectors.value.allocation,5);
  else sectorList.innerHTML='<div class="empty">Sector allocation is unavailable for this date.</div>';
};
const chartSource=new Map(),chartRanges=new Map();
const rangeStart=(range,last)=>{const d=new Date(last+'T00:00:00Z');if(range==='ytd')return `${last.slice(0,4)}-01-01`;const months={'1m':1,'3m':3,'1y':12}[range];if(!months)return null;d.setUTCMonth(d.getUTCMonth()-months);return d.toISOString().slice(0,10)};
const rangeSeries=(id,series,options)=>{
  const start=series.length?rangeStart(chartRanges.get(id)||'all',series[series.length-1].date):null;if(!start)return series;
  const shown=series.filter(x=>x.date>=start);if(shown.length<2)return series;
  if(options.yLabel!=='Rebased value')return shown;
  const first=shown[0];return shown.map(row=>Object.fromEntries(Object.entries(row).map(([k,v])=>[k,k!=='date'&&Number.isFinite(Number(v))&&Number(first[k])?100*Number(v)/Number(first[k]):v])));
};
document.querySelectorAll('.range-buttons').forEach(group=>group.addEventListener('click',event=>{
  const button=event.target.closest('button[data-range]');if(!button)return;const id=group.dataset.chart;chartRanges.set(id,button.dataset.range);
  group.querySelectorAll('button').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button))});
  const source=chartSource.get(id);if(source)draw(id,source.series,source.lines,source.options);
}));
let resizeTimer=null;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>chartState.forEach((_,id)=>renderChart(id)),120)});
const asOfKey=`wealthryse-asof-${account}`;
const storedAsOf=dates=>{try{const value=sessionStorage.getItem(asOfKey);return value&&dates.includes(value)?value:null}catch(error){return null}};
const rememberAsOf=value=>{try{sessionStorage.setItem(asOfKey,value)}catch(error){}};
const showFreshness=latest=>{
  const node=document.querySelector('#freshness');if(!node||!latest)return;
  const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Hong_Kong',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const back=day=>{const d=new Date(day+'T00:00:00Z');do{d.setUTCDate(d.getUTCDate()-1)}while(d.getUTCDay()===0||d.getUTCDay()===6);return d.toISOString().slice(0,10)};
  const expected=back(parts),grace=back(expected);
  const label=new Date(latest+'T00:00:00Z').toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
  const state=latest>=expected?'current':latest>=grace?'late':'stale';
  node.className=`freshness ${state}`;
  node.innerHTML=`Data as of ${esc(label)}${state==='late'?' · one business day behind':state==='stale'?' · daily refresh overdue · <a href="/system-health">Check system health</a>':''}`;
  node.hidden=false;
};
wrTable('#holdings',{filter:'#holdings-filter',count:'#holdings-filter-count'});
wrTable('#sector-detail',{stack:false});wrTable('#trade-history');wrTable('#cash-activity');wrTable('#flows-statement',{stack:false});
"""

# Account directory cards: fills in NAV and returns after the page loads.
HOME_SCRIPT = r"""
<script>
(()=>{
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const minus=text=>String(text).replace(/^-/,'−');
const pct=n=>n==null||!Number.isFinite(Number(n))?'—':(Number(n)>=5e-5?'+':'')+minus(new Intl.NumberFormat('en-US',{style:'percent',minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(n))).replace(/^−(0\.00%)$/,'$1');
const tone=n=>n==null||!Number.isFinite(Number(n))||Math.abs(Number(n))<5e-5?'':Number(n)>0?'gain':'loss';
const money=(n,currency)=>{if(n==null||!Number.isFinite(Number(n)))return '—';try{return minus(new Intl.NumberFormat('en-US',{style:'currency',currency,maximumFractionDigits:0}).format(Number(n)))}catch(error){return minus(new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(Number(n)))+' '+currency}};
const date=value=>{if(!value)return '—';const d=new Date(value+'T00:00:00Z');return Number.isNaN(d.getTime())?value:d.toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'})};
document.addEventListener('click',e=>document.querySelectorAll('details.platform-menu[open]').forEach(d=>{if(!d.contains(e.target))d.removeAttribute('open')}));
fetch('/api/accounts/overview').then(r=>r.ok?r.json():Promise.reject(r.status)).then(data=>{
  const latest=data.accounts.map(a=>a.nav_date).filter(Boolean).sort().pop();
  document.querySelectorAll('[data-account-stats]').forEach(node=>{
    const a=data.accounts.find(x=>x.account_id===node.dataset.accountStats);if(!a)return;
    node.querySelector('[data-nav]').textContent=money(a.nav,a.currency);
    node.querySelector('[data-asof]').textContent=a.nav_date?`as of ${date(a.nav_date)}`:'No NAV yet';
    node.querySelector('[data-returns]').innerHTML=['mtd','ytd'].map(key=>`<span><span class="account-stat-label">${key.toUpperCase()}</span> <span class="${tone(a[key])}">${esc(pct(a[key]))}</span></span>`).join('');
    if(a.nav_date&&latest&&a.nav_date<latest)node.querySelector('[data-asof]').classList.add('behind');
  });
  const summary=document.querySelector('#accounts-summary');
  if(summary)summary.innerHTML=`<span><strong>${data.accounts.length}</strong> account${data.accounts.length===1?'':'s'}</span><span>Data through <strong>${esc(date(latest))}</strong></span>${data.accounts.filter(a=>a.nav_date&&a.nav_date<latest).length?`<span class="behind">${data.accounts.filter(a=>a.nav_date&&a.nav_date<latest).length} behind the latest date</span>`:''}`;
}).catch(()=>{document.querySelectorAll('[data-account-stats] [data-nav]').forEach(node=>node.textContent='')});
})();
</script>
"""
