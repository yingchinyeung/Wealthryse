"""WealthRyse Next, phase 2 of the page-by-page redesign: performance and risk.

Shown to whoever WEB_NEXT_PAGES gives phase 2 (see web_next.py). Like phase 1 it
only rearranges and restates what the page has already loaded. It calls no new
endpoint and computes no new figure: every bar, label and sentence reads a value
that is already on the page.

* Monthly returns: the best and worst months are outlined, and a month opens its
  contribution to return.
* Contribution to return: a waterfall from the start of the month to the end,
  a sentence naming the biggest movers, and earlier and later month buttons.
* Period returns: bars in the TWR column, quick ranges for the custom period,
  and the custom period calculates as soon as both dates are set.
* Benchmark: a plain empty state when none is set.
* Contributions: a timeline of money in and out with yearly totals, and the same
  dates marked on the NAV chart so new money isn't mistaken for performance.
* Risk snapshot: a one-line verdict and two meters; unavailable tiles fold into
  one note with a next step.
* Statistics: grouped into returns, risk and efficiency, each with a one-line
  meaning.
* Drawdowns: each row shades its stretch of the chart, and pointing at the chart
  picks out its row. A bar shows how long each one lasted.
* Rolling risk: one crosshair reads all three charts at once.
* Correlation: holdings that move together are grouped, with a sentence naming
  the closest and the least alike.
"""

PERF_CSS = r"""
/* Monthly returns: best and worst outlined, months open their attribution. */
html[data-nx-pages] td.nx-best { box-shadow: inset 0 0 0 1.5px var(--gain); }
html[data-nx-pages] td.nx-worst { box-shadow: inset 0 0 0 1.5px var(--loss); }
html[data-nx-pages] td.nx-drill { cursor: pointer; }
html[data-nx-pages] td.nx-drill:hover, html[data-nx-pages] td.nx-drill:focus-visible { outline: 1px solid var(--ink); outline-offset: -1px; }
/* Waterfall. */
.nx-wf { margin: 18px 0 6px; }
.nx-wf-row { display: grid; grid-template-columns: minmax(96px, 180px) 1fr 76px; gap: 12px; align-items: center; min-height: 26px; }
.nx-wf-row + .nx-wf-row { border-top: 1px solid var(--line); }
.nx-wf-name { overflow: hidden; color: var(--ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.nx-wf-name.sum { font: 600 10px/1.4 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.nx-wf-track { position: relative; height: 12px; }
.nx-wf-track::before { position: absolute; top: -6px; bottom: -6px; left: var(--zero); width: 1px; content: ""; background: var(--line-strong, var(--line)); }
.nx-wf-bar { position: absolute; top: 0; bottom: 0; min-width: 2px; background: var(--gain); }
.nx-wf-bar.neg { background: var(--loss); }
.nx-wf-bar.sum { background: var(--ink); }
.nx-wf-val { text-align: right; font-size: 13px; font-variant-numeric: tabular-nums; }
.nx-wf-val.gain { color: var(--gain); }
.nx-wf-val.loss { color: var(--loss); }
.nx-step { display: inline-flex; gap: 8px; margin-left: 8px; vertical-align: middle; }
.nx-step button, .nx-seg button, .nx-presets button { padding: 7px 10px; color: var(--ink); background: none; border: 1px solid var(--line-strong, var(--line)); border-radius: 0; cursor: pointer; font: 600 10px/1.2 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.nx-step button:disabled { opacity: .35; cursor: default; }
.nx-step button:not(:disabled):hover, .nx-seg button:hover, .nx-presets button:hover,
.nx-step button:focus-visible, .nx-seg button:focus-visible, .nx-presets button:focus-visible { color: var(--bg); background: var(--ink); outline: none; }
.nx-seg { display: inline-flex; margin: 0 0 14px; }
.nx-seg button + button { border-left: 0; }
.nx-seg button[aria-pressed="true"] { color: var(--bg); background: var(--ink); }
.nx-presets { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 0; }
/* Period returns: a bar under each TWR. */
.nx-bar { display: block; height: 3px; margin: 6px 0 0 auto; width: var(--w); background: var(--gain); }
.nx-bar.neg { background: var(--loss); }
/* Drawdowns. */
.nx-uw { display: block; height: 3px; margin-top: 6px; width: var(--w); background: var(--ink); opacity: .55; }
html[data-nx-pages] tr[data-nx-peak] { cursor: default; }
html[data-nx-pages] tr.nx-hot > * { background: var(--mission-hover, rgba(127, 127, 127, .12)) !important; }
.nx-band { position: absolute; z-index: 0; background: var(--loss); opacity: .12; pointer-events: none; }
/* Correlation. */
html[data-nx-pages] .correlation-matrix th.nx-x { color: var(--ink); text-decoration: underline; text-underline-offset: 4px; }
/* Risk snapshot. */
.nx-gone { display: none !important; }
.nx-gaps { margin: 14px 0 0; color: var(--muted-strong, var(--muted)); font-size: 13px; line-height: 1.5; }
.nx-meters { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px 32px; margin: 4px 0 18px; }
.nx-meter-label { display: flex; justify-content: space-between; color: var(--muted); font: 600 10px/1.4 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.nx-meter-label b { color: var(--ink); font-weight: 600; }
.nx-meter-track { position: relative; height: 2px; margin: 12px 0 8px; background: var(--line-strong, var(--line)); }
.nx-meter-track i { position: absolute; top: -5px; width: 2px; height: 12px; margin-left: -1px; background: var(--ink); }
.nx-meter-ends { display: flex; justify-content: space-between; color: var(--muted); font-size: 11px; }
/* Statistics groups. */
.nx-ghead { grid-column: 1 / -1; margin: 18px 0 0; color: var(--muted); font: 600 10px/1.4 var(--font-mission); letter-spacing: .16em; text-transform: uppercase; }
.nx-ghead:first-child { margin-top: 0; }
/* Contributions timeline and NAV chart flow marks. */
.nx-flows { margin: 6px 0 16px; }
.nx-flows svg { display: block; width: 100%; height: 110px; overflow: visible; }
.nx-flows .in { fill: var(--gain); }
.nx-flows .out { fill: var(--loss); }
.nx-flows .axis { stroke: var(--line-strong, var(--line)); }
.nx-flow-axis { position: relative; height: 16px; margin-top: 4px; }
.nx-flow-axis span { position: absolute; color: var(--muted); font: 600 10px/1 var(--font-mission); letter-spacing: .12em; }
.nx-flow-key { margin: 8px 0 0; color: var(--muted); font-size: 12px; }
.nx-flow-key i { display: inline-block; width: 0; height: 0; margin: 0 6px 0 2px; border: 5px solid transparent; vertical-align: 1px; }
.nx-flow-key i.in { border-bottom: 7px solid var(--gain); border-top: 0; }
.nx-flow-key i.out { border-top: 7px solid var(--loss); border-bottom: 0; margin-left: 14px; }
.nx-years { display: flex; flex-wrap: wrap; gap: 8px 28px; margin: 10px 0 0; font-size: 13px; color: var(--muted-strong, var(--muted)); }
.nx-years b { color: var(--ink); font-weight: 600; }
.nx-flowmark { position: absolute; z-index: 1; width: 0; height: 0; margin-left: -5px; border: 5px solid transparent; pointer-events: auto; }
.nx-flowmark.in { border-bottom: 7px solid var(--gain); border-top: 0; }
.nx-flowmark.out { border-top: 7px solid var(--loss); border-bottom: 0; }
"""

PERF_SCRIPT = r"""
(()=>{
const d=document,$=(s,r=d)=>r.querySelector(s),$$=(s,r=d)=>[...r.querySelectorAll(s)];
const main=$('main');if(!main)return;
const store={get(k,f){try{const v=localStorage.getItem(k);return v==null?f:JSON.parse(v)}catch(e){return f}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=t=>{const s=String(t??'').replace(/[−–]/g,'-').replace(/[^0-9.+-]/g,'');const n=parseFloat(s);return Number.isFinite(n)?n:null};
const signed=(n,dp=2)=>(n>0?'+':n<0?'−':'')+Math.abs(n).toFixed(dp);
const tone=n=>n>0?'gain':n<0?'loss':'';
const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const iso=t=>{const x=String(t||'').match(/(\d{1,2}) ([A-Za-z]{3})[a-z]* (\d{4})/);if(x){const m=MONTHS.indexOf(x[2]);return m<0?null:`${x[3]}-${String(m+1).padStart(2,'0')}-${x[1].padStart(2,'0')}`}const y=String(t||'').match(/\d{4}-\d{2}-\d{2}/);return y?y[0]:null};
const shown=el=>!!(el&&(el.offsetWidth||el.offsetHeight));
const typing=t=>t&&t.closest&&t.closest('input,textarea,select,[contenteditable="true"]');
const charts=typeof chartState!=='undefined'&&typeof renderChart==='function'?chartState:null;
const plot=c=>({ml:100,w:c.clientWidth-122,top:18,h:c.clientHeight-66});
const indexAt=(c,e,n)=>{const r=c.getBoundingClientRect(),x=(e.clientX-r.left)*c.clientWidth/r.width,p=plot(c);return Math.max(0,Math.min(n-1,Math.round((x-p.ml)/p.w*Math.max(1,n-1))))};
const dateIndex=(series,date)=>{const i=series.findIndex(r=>r.date>=date);return i};
const say=(card,html,after)=>{if(!card)return;let p=$(':scope > .nx-answer[data-nx-p2]',card);if(!html){if(p)p.remove();return}
  if(!p){p=d.createElement('p');p.className='nx-answer';p.dataset.nxP2='';const ref=after||$(':scope > .label',card);ref?ref.after(p):card.prepend(p)}if(p.innerHTML!==html)p.innerHTML=html};
const go=href=>{const tab=$(`.nx-subtabs a[href$="${href}"]`)||$(`.account-nav a[data-part-link][href$="${href}"]`);if(tab)tab.click()};

// Monthly returns: outline the best and worst month; a month opens its attribution.
const calendar=()=>{const body=$('#monthly-returns');if(!body)return;const sel=$('#attribution-month'),key=body.textContent+'|'+(sel?sel.options.length:0);if(body.dataset.nxSig===key)return;
  const months=sel?new Set([...sel.options].map(o=>o.value)):new Set(),cells=[];
  $$('tr',body).forEach(tr=>{const y=((tr.querySelector('th')||{}).textContent||'').trim();[...tr.querySelectorAll('td')].forEach((td,i)=>{td.classList.remove('nx-best','nx-worst','nx-drill');
    if(i>11||td.classList.contains('year-cell')||!/\d/.test(td.textContent))return;const v=num(td.firstChild&&td.firstChild.nodeType===3?td.firstChild.textContent:td.textContent);if(v===null)return;
    const month=`${y}-${String(i+1).padStart(2,'0')}`;cells.push({td,v});
    if(months.has(month)){td.classList.add('nx-drill');td.tabIndex=0;td.dataset.nxMonth=month;td.setAttribute('aria-label',`${MONTHS[i]} ${y}: ${td.textContent.trim()}. Open contribution to return for this month.`)}})});
  if(cells.length>2){cells.reduce((a,b)=>b.v>a.v?b:a).td.classList.add('nx-best');cells.reduce((a,b)=>b.v<a.v?b:a).td.classList.add('nx-worst')}
  body.dataset.nxSig=key};
const openMonth=td=>{const sel=$('#attribution-month');if(!sel)return;sel.value=td.dataset.nxMonth;sel.dispatchEvent(new Event('change',{bubbles:true}));go('/performance/attribution')};
main.addEventListener('click',e=>{const td=e.target.closest('td[data-nx-month]');if(td)openMonth(td)});
main.addEventListener('keydown',e=>{const td=e.target.closest&&e.target.closest('td[data-nx-month]');if(td&&(e.key==='Enter'||e.key===' ')){e.preventDefault();openMonth(td)}});

// Contribution to return: waterfall, sentence, earlier and later month.
const attribution=()=>{const top=$('#attribution-top'),bottom=$('#attribution-bottom'),summary=$('#attribution-summary'),sel=$('#attribution-month');if(!top||!bottom||!summary||!sel)return;
  const card=top.closest('.card'),key=top.textContent+'|'+bottom.textContent+'|'+summary.textContent+'|'+sel.value+'|'+sel.options.length;if(card.dataset.nxSig===key)return;card.dataset.nxSig=key;
  if(!$('.nx-step',card)){const box=d.createElement('span');box.className='nx-step';box.innerHTML='<button type="button" data-nx-month-step="1" aria-label="Earlier month">‹ Earlier</button><button type="button" data-nx-month-step="-1" aria-label="Later month">Later ›</button>';sel.after(box)}
  $$('[data-nx-month-step]',card).forEach(b=>{const i=sel.selectedIndex+Number(b.dataset.nxMonthStep);b.disabled=i<0||i>=sel.options.length});
  const rows=t=>$$('tr',t).map(tr=>{const c=tr.querySelector('td[data-sort]');if(!c)return null;const v=num(c.textContent);const name=((tr.querySelector('strong')||tr.children[0]||{}).textContent||'').trim();return v!==null?{name,v}:null}).filter(Boolean);
  const metric=word=>{const m=$$('.metric',summary).find(x=>((x.querySelector('.label')||{}).textContent||'').toLowerCase().includes(word));return m?num((m.querySelector('.value')||{}).textContent):null};
  const pos=rows(top),neg=rows(bottom),total=metric('portfolio return'),held=metric('holdings contribution'),rest=metric('residual');
  let wf=$(':scope > .nx-wf',card);
  if(total===null||!pos.length&&!neg.length){if(wf)wf.remove();say(card,'');return}
  const items=[...pos,...neg];const listed=items.reduce((t,x)=>t+x.v,0);
  if(held!==null&&Math.abs(held-listed)>=0.005)items.push({name:'Other holdings',v:held-listed});
  if(rest!==null&&Math.abs(rest)>=0.005)items.push({name:'Cash, fees, FX and trading',v:rest});
  let run=0;const steps=items.map(x=>{const s={...x,from:run,to:run+x.v};run+=x.v;return s});
  const ends=[0,total,...steps.flatMap(s=>[s.from,s.to])],lo=Math.min(...ends),hi=Math.max(...ends),span=(hi-lo)||1,at=v=>(100*(v-lo)/span).toFixed(2)+'%';
  const bar=(a,b,cls)=>`<span class="nx-wf-bar ${cls}" style="left:${at(Math.min(a,b))};width:calc(${at(Math.max(a,b))} - ${at(Math.min(a,b))})"></span>`;
  const row=(name,a,b,v,cls,sum)=>`<div class="nx-wf-row"><span class="nx-wf-name${sum?' sum':''}" title="${esc(name)}">${esc(name)}</span><span class="nx-wf-track" style="--zero:${at(0)}">${bar(a,b,cls)}</span><span class="nx-wf-val ${tone(v)}">${signed(v)}%</span></div>`;
  const html=`<div class="label">From the start of the month to the end</div>`+steps.map(s=>row(s.name,s.from,s.to,s.v,s.v<0?'neg':'')).join('')+row('Month return',0,total,total,'sum',true);
  if(!wf){wf=d.createElement('div');wf.className='nx-wf';summary.after(wf)}wf.innerHTML=html;
  const month=sel.options[sel.selectedIndex]?sel.options[sel.selectedIndex].textContent:'This month';
  const best=pos[0],worst=neg[0];const parts=[];
  if(best)parts.push(`<b>${esc(best.name)}</b> added the most (<b class="gain">${signed(best.v)}</b> points)`);
  if(worst)parts.push(`<b>${esc(worst.name)}</b> took the most (<b class="loss">${signed(worst.v)}</b>)`);
  say(card,`In <b>${esc(month)}</b> the portfolio returned <b class="${tone(total)}">${signed(total)}%</b>. ${parts.join(' and ')}.${rest!==null?` Cash, fees, FX and trading ${rest<0?'took':'added'} <b class="${tone(rest)}">${signed(rest)}</b>.`:''}`)};
const stepMonth=dir=>{const sel=$('#attribution-month');if(!sel)return;const i=sel.selectedIndex+dir;if(i<0||i>=sel.options.length)return;sel.selectedIndex=i;sel.dispatchEvent(new Event('change',{bubbles:true}))};
main.addEventListener('click',e=>{const b=e.target.closest('[data-nx-month-step]');if(b)stepMonth(Number(b.dataset.nxMonthStep))});
d.addEventListener('keydown',e=>{if(typing(e.target)||e.metaKey||e.ctrlKey||e.altKey||(e.target.closest&&e.target.closest('canvas')))return;const card=$('#perf-attribution');if(!shown(card))return;
  if(e.key==='ArrowLeft'){e.preventDefault();stepMonth(1)}else if(e.key==='ArrowRight'){e.preventDefault();stepMonth(-1)}});

// Period returns: bars in the TWR column, quick ranges, calculate when both dates are set.
const periods=()=>{const body=$('#period-returns');if(body){const table=body.closest('table'),key=body.textContent;if(table&&body.dataset.nxSig!==key){
    const heads=$$('thead th',table).map(th=>th.textContent.trim().toLowerCase()),col=heads.indexOf('twr');
    if(col>=0){const cells=$$('tr',body).map(tr=>tr.children[col]).filter(Boolean),vals=cells.map(c=>/\d/.test(c.textContent)?num(c.textContent):null),max=Math.max(...vals.filter(v=>v!==null).map(Math.abs),1e-9);
      cells.forEach((c,i)=>{let b=$(':scope > .nx-bar',c);if(vals[i]===null){if(b)b.remove();return}if(!b){b=d.createElement('span');b.className='nx-bar';b.setAttribute('aria-hidden','true');c.appendChild(b)}b.classList.toggle('neg',vals[i]<0);b.style.setProperty('--w',(100*Math.abs(vals[i])/max).toFixed(1)+'%')})}
    body.dataset.nxSig=body.textContent}}
  const from=$('#period-from'),to=$('#period-to'),apply=$('#period-apply');if(!from||!to||!apply||from.dataset.nxAuto!==undefined)return;from.dataset.nxAuto='';
  const calc=()=>{if(from.value&&to.value&&from.value<=to.value)apply.click()};from.addEventListener('change',calc);to.addEventListener('change',calc);
  const box=d.createElement('div');box.className='nx-presets';box.setAttribute('role','group');box.setAttribute('aria-label','Quick ranges');
  box.innerHTML=[['30','Last 30 days'],['90','Last 90 days'],['ytd','This year'],['365','Last 12 months'],['ly','Last year']].map(([k,l])=>`<button type="button" data-nx-range="${k}">${l}</button>`).join('');
  (apply.closest('.actions')||apply.parentElement).after(box);
  box.addEventListener('click',e=>{const b=e.target.closest('[data-nx-range]');if(!b)return;const asof=($('#asof-select')||{}).value||new Date().toISOString().slice(0,10);
    const end=new Date(asof+'T00:00:00Z'),k=b.dataset.nxRange,day=x=>x.toISOString().slice(0,10);let s,t=end;
    if(k==='ytd')s=new Date(Date.UTC(end.getUTCFullYear(),0,1));else if(k==='ly'){s=new Date(Date.UTC(end.getUTCFullYear()-1,0,1));t=new Date(Date.UTC(end.getUTCFullYear()-1,11,31))}else{s=new Date(end);s.setUTCDate(s.getUTCDate()-Number(k))}
    let a=day(s),z=day(t);if(from.min&&a<from.min)a=from.min;if(to.max&&z>to.max)z=to.max;from.value=a;to.value=z;calc()})};

// Benchmark: say plainly when none is set.
const benchmark=()=>{const sel=$('#benchmark-select');if(!sel)return;const card=sel.closest('.card');if(!card)return;
  const usable=[...sel.options].filter(o=>o.value&&!o.disabled).length,none=!usable||/not configured/i.test(($('#benchmark')||{}).textContent||'');let e=$(':scope > .nx-empty',card);
  if(!none){if(e)e.remove();return}if(e)return;e=d.createElement('div');e.className='nx-empty';
  e.innerHTML=`<p class="nx-answer"><b>No comparison benchmark is set for this account yet.</b> With one, this page shows the return above the market, how closely the portfolio tracks it, and how much of the market's rises and falls it captured.</p>`+(usable?'<span class="nx-fix" style="margin-left:0"><button type="button" data-nx-pick>Choose a benchmark</button></span>':'');
  card.appendChild(e)};
main.addEventListener('click',e=>{if(!e.target.closest('[data-nx-pick]'))return;const sel=$('#benchmark-select');if(!sel)return;sel.focus();try{sel.showPicker&&sel.showPicker()}catch(x){}});

// Contributions: a timeline of money in and out, yearly totals, and the same dates on the NAV chart.
let flowRows=[];
const flows=()=>{const body=$('#flows-statement');if(!body)return;const key=body.textContent;if(body.dataset.nxSig===key)return;body.dataset.nxSig=key;
  const card=body.closest('.card'),table=body.closest('table'),unit=((($$('thead th',table).pop()||{}).textContent||'').match(/\(([A-Z]{3})\)/)||[])[1]||'';
  flowRows=$$('tr',body).map(tr=>{const t=[...tr.children].map(c=>c.textContent.trim());if(t.length<3||!/^\d{4}-\d{2}-\d{2}$/.test(t[0]))return null;const a=Math.abs(num(t[2])||0);return a?{date:t[0],v:/withdraw/i.test(t[1])?-a:a,kind:t[1]}:null}).filter(Boolean).sort((a,b)=>a.date<b.date?-1:1);
  let box=$(':scope > .nx-flows',card);navFlows();
  if(!flowRows.length){if(box)box.remove();return}
  const money=v=>new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(Math.abs(v));
  const t0=Date.parse(flowRows[0].date),asof=($('#asof-select')||{}).value,t1=Math.max(Date.parse(flowRows.at(-1).date),asof?Date.parse(asof):0),span=Math.max(1,t1-t0),max=Math.max(...flowRows.map(r=>Math.abs(r.v)));
  const W=1000,H=110,mid=55,x=dt=>12+(W-24)*(Date.parse(dt)-t0)/span;
  const bars=flowRows.map(r=>{const h=Math.max(2,50*Math.abs(r.v)/max),cx=x(r.date);return `<rect class="${r.v>0?'in':'out'}" x="${(cx-4).toFixed(1)}" y="${r.v>0?mid-h:mid}" width="8" height="${h.toFixed(1)}"><title>${esc(r.kind)} ${money(r.v)} ${esc(unit)} on ${esc(r.date)}</title></rect>`}).join('');
  const years=[...new Set(flowRows.map(r=>r.date.slice(0,4)))];
  const ticks=years.map(y=>{const dt=y+'-01-01',px=Date.parse(dt)<t0?12:x(dt);return `<span style="left:${(100*px/W).toFixed(2)}%">${y}</span>`}).join('');
  if(!box){box=d.createElement('div');box.className='nx-flows';(table.closest('.table-scroll')||table).before(box)}
  box.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Money in and out over time"><line class="axis" x1="0" x2="${W}" y1="${mid}" y2="${mid}" vector-effect="non-scaling-stroke"/>${bars}</svg><div class="nx-flow-axis">${ticks}</div>`+
    `<div class="nx-years">${years.map(y=>{const n=flowRows.filter(r=>r.date.startsWith(y)).reduce((t,r)=>t+r.v,0);return `<span>${y}: <b class="${tone(n)}">${n<0?'−':'+'}${money(n)}</b> ${esc(unit)} net</span>`}).join('')}</div>`};
const navFlows=()=>{const c=$('#chart');if(!c||!charts)return;const box=c.parentElement;$$('.nx-flowmark',box).forEach(x=>x.remove());let key=box.nextElementSibling&&box.nextElementSibling.classList.contains('nx-flow-key')?box.nextElementSibling:null;
  const s=charts.get('#chart');if(!s||!s.series||s.series.length<2||!flowRows.length){if(key)key.remove();return}
  if(!key){key=d.createElement('div');key.className='nx-flow-key';key.innerHTML='<i class="in"></i>Money in <i class="out"></i>Money out. These are deposits and withdrawals, not performance.';box.after(key)}
  const p=plot(c),n=s.series.length,first=s.series[0].date,last=s.series[n-1].date;
  flowRows.forEach(r=>{if(r.date<first||r.date>last)return;const i=dateIndex(s.series,r.date);if(i<0)return;const m=d.createElement('span');m.className='nx-flowmark '+(r.v>0?'in':'out');
    m.style.left=(c.offsetLeft+p.ml+i*p.w/Math.max(1,n-1))+'px';m.style.top=(c.offsetTop+p.top+p.h-8)+'px';m.title=`${r.kind} on ${r.date}: money ${r.v>0?'in':'out'}, not performance`;box.appendChild(m)})};

// Risk snapshot: a verdict, two meters, and the gaps in one note.
const riskSnap=()=>{const box=$('#risk-metrics');if(!box)return;const card=box.closest('.card');if(!card)return;const etlTop=$('#etl-top'),etlBottom=$('#etl-bottom');
  const key=box.textContent+'|'+(etlTop?etlTop.textContent:'')+'|'+(etlBottom?etlBottom.textContent:'');if(card.dataset.nxSig===key)return;card.dataset.nxSig=key;
  const tiles=$$('.metric',box),name=t=>((t.querySelector('.label')||{}).textContent||'').replace('↗','').trim(),value=t=>((t.querySelector('.value')||{}).textContent||'').trim();
  const find=w=>tiles.find(t=>name(t).toLowerCase().startsWith(w)),val=w=>{const t=find(w);return t?value(t):''};
  const gaps=[];tiles.forEach(t=>{const off=/^unavailable$/i.test(value(t));t.classList.toggle('nx-gone',off);if(off)gaps.push(name(t))});
  const blank=b=>b&&$$('tr',b).every(tr=>/^\s*(unavailable|—|)\s*$/i.test(tr.textContent));
  const etlBox=etlTop&&etlTop.closest('.contributor-grid');
  if(etlBox&&card.contains(etlBox)&&etlBox.contains(etlBottom)){const off=blank(etlTop)&&blank(etlBottom);etlBox.classList.toggle('nx-gone',off);if(off)gaps.push('Expected Tail Loss contributors')}
  let note=$(':scope > .nx-gaps',card);
  if(gaps.length){if(!note){note=d.createElement('div');note.className='nx-gaps';box.after(note)}const asof=$('#asof-select'),can=asof&&asof.selectedIndex<asof.options.length-1;
    note.innerHTML=`<b>Not ready for this date:</b> ${gaps.map(esc).join(', ')}. The other figures are complete.${can?'<span class="nx-fix"><button type="button" data-nx-earlier>Use an earlier date</button></span>':''}`}else if(note)note.remove();
  const vol=num(val('annualized volatility')),sharpe=num(val('sharpe')),varDay=val('var 95'),etl=val('daily expected tail loss');
  let meters=$(':scope > .nx-meters',card);
  if(vol===null){say(card,'');if(meters)meters.remove();return}
  const level=vol<10?'low':vol<20?'moderate':'high';
  say(card,`Risk is <b>${level}</b>: the portfolio typically swings about <b>${esc(val('annualized volatility'))}</b> a year.${/\d/.test(varDay)?` On a bad day (1 in 20) it loses about <b class="loss">${esc(varDay.replace('-','−'))}</b>`:''}${/\d/.test(etl)?`, and the worst 5% of days average <b class="loss">${esc(etl.replace('-','−'))}</b>.`:/\d/.test(varDay)?'.':''}${sharpe!==null?` Each unit of risk earned <b>${esc(val('sharpe'))}</b> of return above cash (Sharpe ratio).`:''}`);
  const meter=(label,shown,pos,left,right)=>`<div><div class="nx-meter-label"><span>${label}</span><b>${esc(shown)}</b></div><div class="nx-meter-track"><i style="left:${Math.max(0,Math.min(100,pos)).toFixed(1)}%"></i></div><div class="nx-meter-ends"><span>${left}</span><span>${right}</span></div></div>`;
  if(!meters){meters=d.createElement('div');meters.className='nx-meters';box.before(meters)}
  meters.innerHTML=meter('Volatility',val('annualized volatility'),100*vol/40,'Calm · 0%','Wild · 40%+')+(sharpe!==null?meter('Sharpe ratio',val('sharpe'),100*sharpe/3,'0','3+'):'')};

// Statistics: grouped, each with a one-line meaning.
const GROUPS=[['Returns',/^(best|worst|positive months|average gain)/i],['Risk',/(drawdown|volatility|downside|skew|kurtosis)/i],['Efficiency',/^(sortino|calmar|gain\/loss)/i]];
const MEANING=[[/^sortino/i,'Return per unit of downside risk. Higher is better.'],[/^calmar/i,'Yearly return divided by the deepest fall. Higher is better.'],[/^maximum drawdown/i,'The deepest fall from a previous high.'],[/^current drawdown/i,'How far below its last high the portfolio is now.'],[/volatility/i,'How much returns swing in a typical year.'],[/^downside/i,'Volatility counting only the falls.'],[/^skew/i,'Below zero means falls tend to be sharper than rises.'],[/kurtosis/i,'Above zero means more extreme days than a normal bell curve.'],[/^best day/i,'The single best day.'],[/^worst day/i,'The single worst day.'],[/^best month/i,'The best calendar month.'],[/^worst month/i,'The worst calendar month.'],[/^positive months/i,'Share of months that ended up.'],[/^average gain/i,'Typical gain in an up month, and loss in a down month.'],[/^gain\/loss/i,'Average up month divided by average down month. Above 1, gains outweigh losses.']];
const stats=()=>{const box=$('#analytics-statistics');if(!box)return;const tiles=$$(':scope > .metric',box),label=t=>((t.querySelector('.label')||{}).textContent||'').replace('↗','').trim();
  const key=tiles.map(t=>t.textContent).join('|');if(!tiles.length||box.dataset.nxSig===key)return;
  $$(':scope > .nx-ghead',box).forEach(h=>h.remove());const used=new Set();
  GROUPS.forEach(([title,re])=>{const mine=tiles.filter(t=>!used.has(t)&&re.test(label(t)));if(!mine.length)return;const h=d.createElement('div');h.className='nx-ghead';h.textContent=title;box.appendChild(h);mine.forEach(t=>{used.add(t);box.appendChild(t)})});
  tiles.filter(t=>!used.has(t)).forEach(t=>box.appendChild(t));
  tiles.forEach(t=>{const m=MEANING.find(([re])=>re.test(label(t)));if(m&&!t.title)t.title=m[1]+' Open the metrics guide for the full definition.'});
  box.dataset.nxSig=key};

// Drawdowns: rows and chart point at each other; a bar shows each one's length.
const DD='#drawdown-chart';
const drawdownRows=()=>{const body=$('#drawdown-episodes');if(!body)return;const key=body.textContent;if(body.dataset.nxSig===key)return;const table=body.closest('table');if(!table)return;
  const heads=$$('thead th',table).map(th=>th.textContent.trim().toLowerCase()),col=w=>heads.findIndex(h=>h.startsWith(w)),L=col('length'),P=col('peak'),R=col('recovered');
  const rows=$$('tr',body).filter(tr=>tr.children.length>Math.max(L,P,R)),lens=rows.map(tr=>num(tr.children[L].textContent)),max=Math.max(...lens.filter(v=>v!==null),1);
  rows.forEach((tr,k)=>{const peak=iso(tr.children[P].textContent);if(!peak)return;tr.dataset.nxPeak=peak;tr.dataset.nxEnd=iso(tr.children[R].textContent)||'';tr.tabIndex=0;
    const td=tr.children[L];let b=$(':scope > .nx-uw',td);if(lens[k]===null){if(b)b.remove();return}if(!b){b=d.createElement('span');b.className='nx-uw';b.setAttribute('aria-hidden','true');td.appendChild(b)}b.style.setProperty('--w',(100*lens[k]/max).toFixed(1)+'%')});
  body.dataset.nxSig=body.textContent};
const band=tr=>{const c=$(DD);if(!c||!charts)return;const box=c.parentElement;$$('.nx-band',box).forEach(x=>x.remove());if(!tr)return;const s=charts.get(DD);if(!s||!s.series||s.series.length<2)return;
  const n=s.series.length,i0=dateIndex(s.series,tr.dataset.nxPeak),end=tr.dataset.nxEnd;let i1=end?dateIndex(s.series,end):n-1;if(i0<0)return;if(i1<0)i1=n-1;
  const p=plot(c),x=i=>c.offsetLeft+p.ml+i*p.w/Math.max(1,n-1),el=d.createElement('div');el.className='nx-band';el.style.left=x(i0)+'px';el.style.width=Math.max(2,x(i1)-x(i0))+'px';el.style.top=(c.offsetTop+p.top)+'px';el.style.height=p.h+'px';box.appendChild(el)};
const hot=tr=>{$$('#drawdown-episodes tr.nx-hot').forEach(x=>x!==tr&&x.classList.remove('nx-hot'));if(tr)tr.classList.add('nx-hot')};
main.addEventListener('pointerover',e=>{const tr=e.target.closest('#drawdown-episodes tr[data-nx-peak]');if(tr){hot(tr);band(tr)}});
main.addEventListener('pointerout',e=>{const tr=e.target.closest('#drawdown-episodes tr[data-nx-peak]');if(tr&&!tr.contains(e.relatedTarget)){hot(null);band(null)}});
main.addEventListener('focusin',e=>{const tr=e.target.closest&&e.target.closest('#drawdown-episodes tr[data-nx-peak]');if(tr){hot(tr);band(tr)}});
main.addEventListener('focusout',e=>{const tr=e.target.closest&&e.target.closest('#drawdown-episodes tr[data-nx-peak]');if(tr){hot(null);band(null)}});
const ddc=$(DD);
if(ddc&&charts){ddc.addEventListener('pointermove',e=>{const s=charts.get(DD);if(!s||!s.series||!s.series.length)return;const date=s.series[indexAt(ddc,e,s.series.length)].date,last=s.series.at(-1).date;
    const tr=$$('#drawdown-episodes tr[data-nx-peak]').find(r=>r.dataset.nxPeak<=date&&date<=(r.dataset.nxEnd||last));hot(tr||null);band(tr||null)});
  ddc.addEventListener('pointerleave',()=>{hot(null);band(null)})}

// Rolling risk: one crosshair reads all three charts.
const GROUP=['#rolling-volatility-chart','#rolling-return-chart','#rolling-beta-chart'];
if(charts)GROUP.forEach(id=>{const c=$(id);if(!c)return;
  const follow=e=>{const s=charts.get(id);if(!s||!s.series||!s.series.length)return;const date=s.series[indexAt(c,e,s.series.length)].date;
    GROUP.forEach(o=>{if(o===id)return;const t=charts.get(o);if(!t||!t.series||!t.series.length||!shown($(o)))return;if(date<t.series[0].date){renderChart(o);return}let j=dateIndex(t.series,date);if(j<0)j=t.series.length-1;renderChart(o,j)})};
  c.addEventListener('pointermove',follow);c.addEventListener('pointerleave',()=>GROUP.forEach(o=>{if(o!==id&&charts.get(o))renderChart(o)}))});

// Correlation: group holdings that move together, and say which pair is closest.
const correlation=()=>{const head=$('#correlation-head'),body=$('#correlation-body');if(!head||!body)return;const key=head.textContent+'|'+body.textContent;if(body.dataset.nxSig===key)return;
  const card=body.closest('.card'),hcells=$$('th',head).slice(1),syms=hcells.map(th=>th.textContent.trim()),rows=$$('tr',body);
  if(syms.length<3||rows.length!==syms.length){body.dataset.nxSig=key;say(card,'');return}
  const M={},rowOf={},cellOf={};rows.forEach(tr=>{const s=tr.querySelector('th').textContent.trim();rowOf[s]=tr;M[s]={};cellOf[s]={};[...tr.querySelectorAll('td')].forEach((td,j)=>{cellOf[s][syms[j]]=td;M[s][syms[j]]=/\d/.test(td.textContent)?num(td.textContent):null})});
  if(!body.dataset.nxOrig||body.dataset.nxOrig.split(',').sort().join()!==[...syms].sort().join())body.dataset.nxOrig=syms.join(',');
  const pairs=[];syms.forEach((a,i)=>syms.slice(i+1).forEach(b=>{const v=M[a][b];if(v!=null)pairs.push({a,b,v})}));
  let order=body.dataset.nxOrig.split(',');
  if(store.get('wr-next-corr','grouped')==='grouped'){let groups=order.map(s=>[s]);const sim=(x,y)=>{let t=0,k=0;x.forEach(p=>y.forEach(q=>{const v=M[p][q];if(v!=null){t+=v;k++}}));return k?t/k:-2};
    while(groups.length>1){let bi=0,bj=1,bv=-Infinity;for(let i=0;i<groups.length;i++)for(let j=i+1;j<groups.length;j++){const v=sim(groups[i],groups[j]);if(v>bv){bv=v;bi=i;bj=j}}groups[bi]=groups[bi].concat(groups[bj]);groups.splice(bj,1)}order=groups[0]}
  const th0=$('th',head);head.querySelector('tr').replaceChildren(th0,...order.map(s=>hcells[syms.indexOf(s)]));
  order.forEach(s=>{const tr=rowOf[s];tr.replaceChildren(tr.querySelector('th'),...order.map(t=>cellOf[s][t]));body.appendChild(tr)});
  if(card&&!$('.nx-seg',card)){const seg=d.createElement('div');seg.className='nx-seg';seg.setAttribute('role','group');seg.setAttribute('aria-label','Order');seg.innerHTML='<button type="button" data-nx-corr="grouped">Grouped</button><button type="button" data-nx-corr="original">Largest first</button>';(body.closest('.table-scroll')||body.closest('table')).before(seg)}
  $$('[data-nx-corr]',card||d).forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.nxCorr===store.get('wr-next-corr','grouped'))));
  if(pairs.length>1){const hi=pairs.reduce((x,y)=>y.v>x.v?y:x),lo=pairs.reduce((x,y)=>y.v<x.v?y:x),f=v=>v.toFixed(2).replace('-','−');
    say(card,`<b>${esc(hi.a)}</b> and <b>${esc(hi.b)}</b> move together most (<b>${f(hi.v)}</b>), and <b>${esc(lo.a)}</b> and <b>${esc(lo.b)}</b> the least (<b>${f(lo.v)}</b>).${store.get('wr-next-corr','grouped')==='grouped'?' Holdings that move together sit next to each other.':''}`,$('#correlation-summary'))}
  body.dataset.nxSig=head.textContent+'|'+body.textContent};
main.addEventListener('click',e=>{const b=e.target.closest('[data-nx-corr]');if(!b)return;store.set('wr-next-corr',b.dataset.nxCorr);const body=$('#correlation-body');if(body)body.dataset.nxSig='';correlation()});
main.addEventListener('pointerover',e=>{const td=e.target.closest('#correlation-body td');$$('.correlation-matrix th.nx-x').forEach(x=>x.classList.remove('nx-x'));if(!td)return;const tr=td.parentElement,i=[...tr.children].indexOf(td),th=$('#correlation-head tr');tr.firstElementChild.classList.add('nx-x');if(th&&th.children[i])th.children[i].classList.add('nx-x')});

// Charts re-draw on range changes: keep the flow marks in place.
if(charts){const set=charts.set.bind(charts);charts.set=(k,v)=>{set(k,v);if(k==='#chart')setTimeout(navFlows,70);if(k===DD)band(null);return charts};addEventListener('resize',()=>{clearTimeout(navFlows.t);navFlows.t=setTimeout(navFlows,160)})}

const run=()=>{calendar();attribution();periods();benchmark();flows();riskSnap();stats();drawdownRows();correlation()};
let pending=false;const later=()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;obs.disconnect();try{run()}finally{obs.observe(main,{childList:true,subtree:true,characterData:true})}})};
const obs=new MutationObserver(later);run();obs.observe(main,{childList:true,subtree:true,characterData:true});
})();
"""
