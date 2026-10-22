"""WealthRyse Next, phase 1 of the page-by-page redesign: patterns every page shares.

Switched by WEB_NEXT_PAGES (off, preview or on; see web_next.py). Like the rest of
the new design it only rearranges what the page already shows. It reads the
numbers the page has loaded and calls no new endpoint, so no figure changes.

* The account header is smaller on subpages, and the subpage tabs stay in view.
* Method paragraphs fold behind an "About this" button.
* Charts can be read by keyboard (arrow keys) and by finger, with the high and
  low points labelled.
* Tables use lining figures, a row highlight and a compact or comfortable setting.
* "Loading…" text becomes a shimmer, and error text says what to do next.
* J and K (or a swipe on a phone) move between subpages, and each section
  reopens on the subpage you last used.
* Monthly returns and drawdowns open with a one-line answer.
"""

PAGES_CSS = r"""
/* Compact account header on subpages; the subpage tabs stay in view. */
html[data-nx-pages][data-nx-section]:not([data-nx-section="overview"]) .account-hero h1 { font-size: clamp(26px, 2.6vw, 36px) !important; }
html[data-nx-pages][data-nx-section]:not([data-nx-section="overview"]) main { padding-top: 22px; }
@media (min-width: 1024px) {
  html[data-nx-pages] .account-sticky { position: sticky !important; top: var(--nx-top) !important; z-index: 30; margin-top: 18px !important; background: var(--bg); }
  html[data-nx-pages] .account-sticky .nx-subtabs { margin-bottom: 0; }
}
/* About this: method notes fold away. */
.nx-about { margin-left: 10px; padding: 0; color: var(--muted); background: none; border: 0; border-bottom: 1px solid var(--line); cursor: pointer; font: 600 10px/1.4 var(--font-mission); letter-spacing: .16em; text-transform: uppercase; vertical-align: 1px; }
.nx-about:hover, .nx-about:focus-visible { color: var(--ink); border-color: var(--ink); outline: none; }
.nx-about[aria-expanded="true"]::after { content: " ×"; }
.nx-folded { display: none !important; }
/* Charts: focus ring, finger scrubbing and labelled extremes. */
html[data-nx-pages] canvas[data-nx-chart] { touch-action: pan-y; }
html[data-nx-pages] canvas[data-nx-chart]:focus-visible { outline: 1px solid var(--ink); outline-offset: 4px; }
.nx-mark { position: absolute; z-index: 1; transform: translate(-50%, -100%); padding: 0 0 7px; color: var(--ink); font: 600 10px/1.2 var(--font-mission); letter-spacing: .12em; text-transform: uppercase; white-space: nowrap; pointer-events: none; }
.nx-mark::after { position: absolute; left: 50%; bottom: 0; width: 7px; height: 7px; margin-left: -3.5px; content: ""; background: var(--bg); border: 1.5px solid var(--ink); border-radius: 50%; transform: translateY(50%); }
.nx-mark.low { transform: translate(-50%, 0); padding: 7px 0 0; }
.nx-mark.low::after { top: 0; bottom: auto; transform: translateY(-50%); }
.nx-mark.loss { color: var(--loss); }
.nx-mark.loss::after { border-color: var(--loss); }
.nx-mark span { color: var(--muted); font-weight: 600; }
.nx-mark.end { transform: translate(calc(-100% + 3.5px), -100%); }
.nx-mark.start { transform: translate(-3.5px, -100%); }
.nx-mark.low.end { transform: translate(calc(-100% + 3.5px), 0); }
.nx-mark.low.start { transform: translate(-3.5px, 0); }
.nx-mark.end::after { left: auto; right: 0; margin-left: 0; }
.nx-mark.start::after { left: 0; margin-left: 0; }
/* Tables. */
html[data-nx-pages] main td, html[data-nx-pages] main th { font-variant-numeric: tabular-nums lining-nums; }
html[data-nx-pages] main tbody tr:hover > * { background: var(--mission-hover, rgba(127, 127, 127, .08)); }
html.nx-dense[data-nx-pages] main td, html.nx-dense[data-nx-pages] main tbody th { padding-top: 5px !important; padding-bottom: 5px !important; font-size: 12.5px; }
/* Loading shimmer in place of "Loading…". */
.nx-skel { position: relative; min-width: 160px; color: transparent !important; user-select: none; }
.nx-skel::before { position: absolute; inset: 25% auto 25% 0; width: min(100%, 320px); content: ""; background: linear-gradient(90deg, var(--line), var(--line-strong, var(--line)), var(--line)); background-size: 200% 100%; animation: nx-shimmer 1.4s linear infinite; opacity: .55; }
td.nx-skel::before { left: 8px; }
@keyframes nx-shimmer { to { background-position: -200% 0; } }
@media (prefers-reduced-motion: reduce) { .nx-skel::before { animation: none; } }
/* Plain errors with a next step. */
.nx-fix { display: inline-flex; flex-wrap: wrap; gap: 8px; margin-left: 10px; vertical-align: middle; }
.nx-fix button { padding: 5px 9px; color: var(--ink); background: none; border: 1px solid var(--line-strong, var(--ink)); border-radius: 0; cursor: pointer; font: 600 10px/1.2 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.nx-fix button:hover, .nx-fix button:focus-visible { color: var(--bg); background: var(--ink); outline: none; }
/* Answer sentences on subpages. */
.nx-answer { margin: 4px 0 12px; max-width: 860px; color: var(--muted-strong, var(--muted)); font-size: 16px; line-height: 1.5; }
.nx-answer b { color: var(--ink); font-weight: 600; }
.nx-answer b.gain { color: var(--gain); }
.nx-answer b.loss { color: var(--loss); }
/* J and K hint on the subpage tabs. */
.nx-subtabs .nx-keys { flex: none; margin-left: auto; padding: 10px 0 9px; color: var(--muted); font: 600 10px/1 var(--font-mission); letter-spacing: .12em; text-transform: uppercase; }
.nx-subtabs .nx-keys kbd { padding: 1px 5px; border: 1px solid var(--line); font: inherit; }
"""

PAGES_SCRIPT = r"""
(()=>{
const d=document,root=d.documentElement,$=(s,r=d)=>r.querySelector(s),$$=(s,r=d)=>[...r.querySelectorAll(s)];
const store={get(k,f){try{const v=localStorage.getItem(k);return v==null?f:JSON.parse(v)}catch(e){return f}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const typing=t=>t&&t.closest&&t.closest('input,textarea,select,[contenteditable="true"]');
const m=location.pathname.match(/^\/accounts\/([^/]+)\/([^/]+)(?:\/([^/]+))?/);
root.dataset.nxPages='';
if(m)root.dataset.nxSection=m[2];
const main=$('main');if(!main)return;

// Remember the subpage each section was left on, and reopen it from the menus.
const last=store.get('wr-next-last',{});
if(m&&m[3]){last[decodeURIComponent(m[1])+'/'+m[2]]=location.pathname;store.set('wr-next-last',last)}
const remember=()=>{const x=location.pathname.match(/^\/accounts\/([^/]+)\/([^/]+)\/([^/]+)/);if(x){last[decodeURIComponent(x[1])+'/'+x[2]]=location.pathname;store.set('wr-next-last',last)}};
addEventListener('popstate',remember);
const reopen=()=>$$('a.nx-link, .nx-tabbar a, .account-nav a.account-nav-link').forEach(a=>{const h=a.getAttribute('href')||'',x=h.match(/^\/accounts\/([^/]+)\/([^/?#]+)\/?$/);if(!x)return;const saved=last[decodeURIComponent(x[1])+'/'+x[2]];if(saved&&saved.startsWith(`/accounts/${x[1]}/${x[2]}/`))a.setAttribute('href',saved)});
reopen();

// J and K, or a swipe on a phone, move between subpages.
const tabs=()=>$$('.nx-subtabs a');
const step=dir=>{const list=tabs();if(list.length<2)return;let i=list.findIndex(a=>a.getAttribute('aria-current')==='page');if(i<0)i=0;const next=list[i+dir];if(!next)return;
  const classic=$(`.account-nav a[data-part-link][href="${CSS.escape(next.getAttribute('href'))}"]`);
  list.forEach(a=>a.removeAttribute('aria-current'));next.setAttribute('aria-current','page');
  const go=()=>{classic?classic.click():location.assign(next.getAttribute('href'));remember()};
  if(d.startViewTransition&&!matchMedia('(prefers-reduced-motion: reduce)').matches)d.startViewTransition(go);else go()};
const subtabs=$('.nx-subtabs');
if(subtabs&&tabs().length>1){subtabs.insertAdjacentHTML('beforeend','<span class="nx-keys" aria-hidden="true"><kbd>J</kbd> <kbd>K</kbd> to move</span>');
  subtabs.addEventListener('click',e=>{const a=e.target.closest('a');if(!a||e.metaKey||e.ctrlKey||e.shiftKey||e.button)return;const classic=$(`.account-nav a[data-part-link][href="${CSS.escape(a.getAttribute('href'))}"]`);if(!classic)return;e.preventDefault();tabs().forEach(t=>t.removeAttribute('aria-current'));a.setAttribute('aria-current','page');classic.click();remember()})}
d.addEventListener('keydown',e=>{if(typing(e.target)||e.metaKey||e.ctrlKey||e.altKey||d.querySelector('dialog[open]'))return;if(e.key==='j'||e.key==='J')step(1);else if(e.key==='k'||e.key==='K')step(-1)});
let touch=null;
main.addEventListener('touchstart',e=>{const t=e.touches[0];touch=e.touches.length===1&&!e.target.closest('.table-scroll,canvas,input,select,textarea,.nx-subtabs,[data-nx-chart]')?{x:t.clientX,y:t.clientY}:null},{passive:true});
main.addEventListener('touchend',e=>{if(!touch)return;const t=e.changedTouches[0],dx=t.clientX-touch.x,dy=t.clientY-touch.y;touch=null;if(Math.abs(dx)>90&&Math.abs(dy)<40)step(dx<0?1:-1)},{passive:true});

// Method paragraphs fold behind "About this".
const fold=()=>$$('.card > .label + .muted:not([id]):not([data-nx-fold])',main).forEach(p=>{p.dataset.nxFold='';if(p.textContent.trim().length<90)return;
  const label=p.previousElementSibling,b=d.createElement('button');b.type='button';b.className='nx-about';b.textContent='About this';b.setAttribute('aria-expanded','false');
  p.classList.add('nx-folded');b.addEventListener('click',()=>{const open=p.classList.toggle('nx-folded');b.setAttribute('aria-expanded',String(!open))});label.appendChild(b)});

// Loading text becomes a shimmer.
const LOADING=/^(Loading|Calculating|Checking)\b[^.]{0,40}(…|\.\.\.)$/;
const skel=()=>{$$('.nx-skel',main).forEach(el=>{if(!LOADING.test(el.textContent.trim()))el.classList.remove('nx-skel')});
  $$('.muted, .value, td, .empty, h1',main).forEach(el=>{if(el.children.length||el.classList.contains('nx-skel'))return;if(LOADING.test(el.textContent.trim()))el.classList.add('nx-skel')})};

// Errors say what happened and what to do.
const asof=$('#asof-select');
const earlier=()=>{if(!asof||asof.selectedIndex>=asof.options.length-1)return;asof.selectedIndex+=1;asof.dispatchEvent(new Event('change',{bubbles:true}))};
const FIXES=[
  [/^The model could not run \(error (\d+)\)\.?$/,()=>'This model is not available right now.',['retry']],
  [/^(.*?) (?:are|is) unavailable: HTTP \d+\.?$/,x=>`${x[1]} are not available right now.`,['retry']],
  [/^(.*?) could not be loaded\.?(?: Refresh the page or pick another As of date\.?)?$/,x=>`${x[1]} could not be loaded.`,['retry','earlier']],
  [/^(.*?) (?:are|is) unavailable for this date\.?$/,x=>`${x[1]} ${/s$/.test(x[1])?'are':'is'} not ready for this date.`,['earlier']],
  [/^No (.*?) (?:is )?available for this date\.?$/,x=>`No ${x[1]} for this date yet.`,['earlier']],
];
const fixErrors=()=>$$('.muted, .empty, td, .value, .notice, .error',main).forEach(el=>{if(el.children.length&&!el.querySelector(':scope > .nx-fix'))return;
  const own=[...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim();if(!own||own.length>220)return;
  if(el.dataset.nxFixed===own)return;
  for(const [re,say,actions] of FIXES){const x=own.match(re);if(!x)continue;
    const text=say(x),acts=actions.filter(a=>a!=='earlier'||(asof&&asof.selectedIndex<asof.options.length-1));
    el.textContent=text;el.dataset.nxFixed=text;
    if(acts.length){const box=d.createElement('span');box.className='nx-fix';box.innerHTML=acts.map(a=>a==='retry'?'<button type="button" data-nx-retry>Try again</button>':'<button type="button" data-nx-earlier>Use an earlier date</button>').join('');el.appendChild(box)}
    return}});
main.addEventListener('click',e=>{if(e.target.closest('[data-nx-retry]'))location.reload();else if(e.target.closest('[data-nx-earlier]'))earlier()});

// Answer sentences: restate what the table already shows.
const pct=t=>{const n=parseFloat(String(t).replace(/[^0-9.+−-]/g,'').replace('−','-'));return Number.isFinite(n)?n:null};
const signed=n=>(n>0?'+':n<0?'−':'')+Math.abs(n).toFixed(2)+'%';
const answer=(card,html)=>{if(!card)return;let p=$(':scope > .nx-answer',card);if(!html){if(p)p.remove();return}if(!p){p=d.createElement('p');p.className='nx-answer';const anchor=$(':scope > .table-scroll, :scope > .chart-wrap',card);anchor?card.insertBefore(p,anchor):card.appendChild(p)}if(p.innerHTML!==html)p.innerHTML=html};
const monthly=()=>{const body=$('#monthly-returns');if(!body)return;const card=body.closest('.card'),heads=$$('thead th',body.closest('table')).map(th=>th.textContent.trim());
  const cells=[];$$('tr',body).forEach(tr=>{const year=(tr.querySelector('th')||{}).textContent;[...tr.querySelectorAll('td')].forEach((td,i)=>{const h=heads[i+1];if(!h||/total|year/i.test(h))return;const v=pct(td.textContent);if(v!==null&&/\d/.test(td.textContent))cells.push({label:`${h} ${String(year||'').trim()}`,v})})});
  if(cells.length<3)return answer(card,'');const up=cells.filter(c=>c.v>0).length,best=cells.reduce((a,b)=>b.v>a.v?b:a),worst=cells.reduce((a,b)=>b.v<a.v?b:a);
  answer(card,`<b>${up} of ${cells.length}</b> months were up. The best was <b>${esc(best.label)}</b> at <b class="gain">${signed(best.v)}</b>, and the worst was <b>${esc(worst.label)}</b> at <b class="${worst.v<0?'loss':'gain'}">${signed(worst.v)}</b>.`)};
const drawdowns=()=>{const body=$('#drawdown-episodes');if(!body)return;const card=body.closest('.card'),table=body.closest('table');if(!table)return;const heads=$$('thead th',table).map(th=>th.textContent.trim().toLowerCase());
  const row=$('tr',body);if(!row||row.children.length<3)return answer(card,'');const cell=name=>{const i=heads.findIndex(h=>h.startsWith(name));return i<0?'':(row.children[i]||{}).textContent.trim()};
  const depth=cell('depth'),peak=cell('peak'),trough=cell('trough'),rec=cell('recovered');if(!depth||!/\d/.test(depth))return answer(card,'');
  answer(card,`The deepest fall was <b class="loss">${esc(depth)}</b>, from <b>${esc(peak)}</b> to <b>${esc(trough)}</b>, ${rec&&/\d/.test(rec)?`and it recovered on <b>${esc(rec)}</b>.`:'and it has not fully recovered yet.'}`)};

// Charts: keyboard and finger reading, and the high and low labelled.
const charts=typeof chartState!=='undefined'&&typeof renderChart==='function'?chartState:null;
const fmt=(v,unit)=>new Intl.NumberFormat('en-US',{maximumFractionDigits:Math.abs(v)>=1000?0:2}).format(v)+(unit&&unit!=='%'?' '+unit:unit==='%'?'%':'');
const marks=id=>{const c=$(id);if(!c||!charts)return;const box=c.parentElement;$$('.nx-mark',box).forEach(x=>x.remove());const s=charts.get(id);if(!s||!s.series||s.series.length<3||!s.lines||!s.lines.length||typeof chartBounds!=='function')return;
  const line=s.lines[0],has=v=>v!=null&&Number.isFinite(Number(v)),pts=s.series.map((r,i)=>({i,v:has(r[line.key])?Number(r[line.key]):null,date:r.date})).filter(p=>p.v!==null);if(pts.length<3)return;
  const values=s.lines.flatMap(l=>s.series.filter(r=>has(r[l.key])).map(r=>Number(r[l.key])));const b=chartBounds(values,s.options||{});if(!(b.hi>b.lo))return;
  const cw=c.clientWidth,ch=c.clientHeight,ml=100,mr=22,mt=18,mb=48,w=cw-ml-mr,h=ch-mt-mb,ox=c.offsetLeft,oy=c.offsetTop;
  const at=p=>({x:ox+ml+p.i*w/Math.max(1,s.series.length-1),y:oy+mt+(b.hi-p.v)*h/(b.hi-b.lo)});
  const hi=pts.reduce((a,p)=>p.v>a.v?p:a),lo=pts.reduce((a,p)=>p.v<a.v?p:a),unit=(s.options||{}).unit||'',dd=/drawdown/i.test(id);
  const put=(p,cls,label)=>{const q=at(p),el=d.createElement('div');el.className='nx-mark '+cls;el.style.left=q.x+'px';if(q.x>ox+cw-90)el.classList.add('end');else if(q.x<ox+ml+60)el.classList.add('start');el.style.top=q.y+'px';el.innerHTML=label;box.appendChild(el)};
  if(dd){if(lo.v<0)put(lo,'low loss',`Deepest ${esc(fmt(lo.v,unit))} <span>${esc(lo.date)}</span>`);return}
  put(hi,'high',`High ${esc(fmt(hi.v,unit))}`);if(lo.i!==hi.i)put(lo,'low',`Low ${esc(fmt(lo.v,unit))}`)};
if(charts){
  const ids=new Set();const queue=id=>{ids.add(id);clearTimeout(queue.t);queue.t=setTimeout(()=>ids.forEach(marks),60)};
  const set=charts.set.bind(charts);charts.set=(k,v)=>{set(k,v);queue(k);return charts};[...charts.keys()].forEach(queue);
  addEventListener('resize',()=>{clearTimeout(queue.r);queue.r=setTimeout(()=>ids.forEach(marks),150)});
  const hover=new Map();
  const wire=c=>{if(c.dataset.nxChart!==undefined)return;c.dataset.nxChart='';const id='#'+c.id;c.tabIndex=0;c.setAttribute('role','img');
    c.setAttribute('aria-label',(c.getAttribute('aria-label')||'Chart')+'. Use the left and right arrow keys to read values.');
    const len=()=>{const s=charts.get(id);return s&&s.series?s.series.length:0};
    const show=i=>{const n=len();if(!n)return;i=Math.max(0,Math.min(n-1,i));hover.set(id,i);renderChart(id,i)};
    c.addEventListener('keydown',e=>{const n=len();if(!n)return;let i=hover.has(id)?hover.get(id):n-1;const big=Math.max(1,Math.round(n/20));
      if(e.key==='ArrowRight')i+=e.shiftKey?big:1;else if(e.key==='ArrowLeft')i-=e.shiftKey?big:1;else if(e.key==='Home')i=0;else if(e.key==='End')i=n-1;else if(e.key==='Escape'){hover.delete(id);renderChart(id);return}else return;e.preventDefault();show(i)});
    c.addEventListener('blur',()=>{hover.delete(id);renderChart(id)});
    const finger=e=>{if(e.pointerType==='mouse')return;const n=len();if(!n)return;const r=c.getBoundingClientRect(),x=(e.clientX-r.left)*c.clientWidth/r.width;show(Math.round((x-100)/(c.clientWidth-122)*Math.max(1,n-1)))};
    c.addEventListener('pointerdown',finger);c.addEventListener('pointermove',finger)};
  const wireAll=()=>$$('canvas[id]',main).forEach(wire);wireAll();
}

// Table density, from the menu under your initial.
const menu=$('.nx-menu');
const setDense=on=>{root.classList.toggle('nx-dense',on);store.set('wr-next-dense',on);const b=$('[data-nx-dense]');if(b)b.textContent=on?'Comfortable tables':'Compact tables'};
if(menu){const b=d.createElement('button');b.type='button';b.setAttribute('role','menuitem');b.dataset.nxDense='';const first=$('[data-nx-theme]',menu);first?first.after(b):menu.insertBefore(b,menu.children[1]||null);b.addEventListener('click',()=>setDense(!root.classList.contains('nx-dense')))}
setDense(!!store.get('wr-next-dense',false));

// Keep up with what the page loads.
const run=()=>{fold();skel();fixErrors();monthly();drawdowns()};
let pending=false;const later=()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;obs.disconnect();try{run();reopen()}finally{obs.observe(main,{childList:true,subtree:true,characterData:true})}})};
const obs=new MutationObserver(later);run();obs.observe(main,{childList:true,subtree:true,characterData:true});
})();
"""
