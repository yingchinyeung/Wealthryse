"""WealthRyse Next, phase 4 of the page-by-page redesign: holdings and activity.

Shown to whoever WEB_NEXT_PAGES gives phase 4 (see web_next.py). It reads the
tables the page has already drawn and drives the page's own filter controls, so
every figure and download is the same one the classic page shows.

* Holdings: a map of the positions sized by value and shaded by gain or loss,
  column sets (Value, Performance, Risk, All) that the browser remembers, and a
  side sheet with every field of a position and a link to its trades.
* Cash: quick date ranges, type chips that narrow the list, and a sentence
  counting what is shown.
* Trades: Buys / Sells / All, quick date ranges, click a symbol to see only its
  trades (also from ?symbol= in the address), and a sentence on what is shown.
* Reports: the months as a shelf of cards, and the owner's design tools folded
  away under a heading.
* Exports: each download as a card saying what it holds and when this browser
  last downloaded it.
"""

HOLD_CSS = r"""
/* Holdings map. */
.nx-tree { position: relative; height: 260px; margin: 6px 0 16px; overflow: hidden; background: var(--bg); }
@media (max-width: 700px) { .nx-tree { height: 200px; } }
.nx-tile { position: absolute; display: flex; flex-direction: column; justify-content: space-between; padding: 6px 7px; overflow: hidden; color: var(--ink); text-align: left; background: var(--nx-tint, var(--line)); border: 0; border-right: 1px solid var(--bg); border-bottom: 1px solid var(--bg); border-radius: 0; cursor: pointer; font: inherit; transition: opacity .15s; }
.nx-tile b { font: 600 11px/1.1 var(--font-mission); letter-spacing: .12em; text-transform: uppercase; }
.nx-tile small { font-size: 11px; font-variant-numeric: tabular-nums; }
.nx-tile.tiny b, .nx-tile.tiny small, .nx-tile.small small { display: none; }
.nx-tile:hover, .nx-tile:focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; z-index: 1; }
.nx-tile.dim { opacity: .18; }
.nx-tree-key { display: flex; flex-wrap: wrap; gap: 14px; margin: -8px 0 14px; color: var(--muted); font-size: 12px; }
.nx-tree-key i { display: inline-block; width: 10px; height: 10px; margin: 0 6px -1px 0; }
/* Column sets. */
.table-tools .nx-seg { margin: 0 0 0 auto; }
table[data-nx-cols="value"] tr > :is(:nth-child(4), :nth-child(6), :nth-child(8), :nth-child(9), :nth-child(10), :nth-child(11)),
table[data-nx-cols="performance"] tr > :is(:nth-child(3), :nth-child(4), :nth-child(5), :nth-child(6), :nth-child(11)),
table[data-nx-cols="risk"] tr > :is(:nth-child(2), :nth-child(5), :nth-child(6), :nth-child(7), :nth-child(8), :nth-child(9)) { display: none; }
html[data-nx-pages] #holdings tr:not(.total-row) { cursor: pointer; }
html[data-nx-pages] #holdings tr:not(.total-row):focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; }
/* Position sheet. */
.nx-drawer { position: fixed; inset: 0 0 0 auto; width: min(420px, 100vw); max-width: none; height: 100vh; max-height: none; margin: 0; padding: 0; color: var(--ink); background: var(--bg); border: 0; border-left: 1px solid var(--line-strong, var(--line)); border-radius: 0; overflow-y: auto; }
.nx-drawer::backdrop { background: rgba(0, 0, 0, .45); }
@media (max-width: 700px) { .nx-drawer { inset: auto 0 0 0; width: 100vw; height: auto; max-height: 82vh; border-left: 0; border-top: 1px solid var(--line-strong, var(--line)); } }
.nx-drawer-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 20px; border-bottom: 1px solid var(--line); }
.nx-drawer-head b { font: 600 13px/1.2 var(--font-mission); letter-spacing: .16em; text-transform: uppercase; }
.nx-drawer-head button { min-width: 44px; min-height: 44px; color: var(--ink); background: none; border: 0; cursor: pointer; font-size: 22px; }
.nx-drawer-body { padding: 8px 20px 24px; }
.nx-drawer-body .nx-answer { margin: 8px 0 14px; font-size: 15px; }
.nx-drawer dl { display: grid; grid-template-columns: auto 1fr; gap: 0; margin: 0 0 18px; }
.nx-drawer dt, .nx-drawer dd { margin: 0; padding: 9px 0; border-bottom: 1px solid var(--line); }
.nx-drawer dt { color: var(--muted); font: 600 10px/1.6 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.nx-drawer dd { text-align: right; font-variant-numeric: tabular-nums; }
.nx-drawer .rsi-flag { margin-left: 6px; }
/* Activity chips. */
.nx-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 0; }
.nx-chips button { padding: 7px 10px; color: var(--ink); background: none; border: 1px solid var(--line-strong, var(--line)); border-radius: 0; cursor: pointer; font: 600 10px/1.2 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.nx-chips button[aria-pressed="true"], .nx-chips button:hover, .nx-chips button:focus-visible { color: var(--bg); background: var(--ink); outline: none; }
.nx-chips button small { margin-left: 6px; font-weight: 400; opacity: .7; }
html[data-nx-pages] #trade-history td:nth-child(3) strong { cursor: pointer; text-decoration: underline; text-decoration-color: var(--line-strong, var(--line)); text-underline-offset: 3px; }
/* Report shelf. */
.nx-shelf { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 0; margin: 14px 0 4px; border-top: 1px solid var(--line); border-left: 1px solid var(--line); }
.nx-shelf button { display: flex; flex-direction: column; gap: 8px; min-height: 92px; padding: 12px 14px; color: var(--ink); text-align: left; background: none; border: 0; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); border-radius: 0; cursor: pointer; font: inherit; }
.nx-shelf button b { font: 600 15px/1.1 var(--font-mission); letter-spacing: .08em; text-transform: uppercase; }
.nx-shelf button small { color: var(--muted); font-size: 12px; line-height: 1.35; }
.nx-shelf button[aria-pressed="true"] { color: var(--bg); background: var(--ink); }
.nx-shelf button[aria-pressed="true"] small { color: inherit; opacity: .75; }
.nx-shelf button:hover:not([aria-pressed="true"]), .nx-shelf button:focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; }
.nx-shelf .dot { display: inline-block; width: 7px; height: 7px; margin-right: 6px; border: 1px solid currentColor; border-radius: 50%; }
.nx-shelf .dot.ready { background: currentColor; }
html[data-nx-pages] #report-month.nx-quiet { display: none; }
details.nx-fold { margin: 22px 0 0; border-top: 1px solid var(--line); }
details.nx-fold > summary { padding: 14px 0; color: var(--muted); cursor: pointer; font: 600 10px/1.4 var(--font-mission); letter-spacing: .16em; text-transform: uppercase; }
/* Export cards. */
.actions.nx-dl { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 0; border-top: 1px solid var(--line); border-left: 1px solid var(--line); }
.actions.nx-dl > a.button { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; justify-content: flex-start; min-height: 104px; margin: 0; padding: 14px 16px; color: var(--ink); text-align: left; text-transform: none; letter-spacing: 0; background: none; border: 0; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.actions.nx-dl > a.button:hover, .actions.nx-dl > a.button:focus-visible { color: var(--bg); background: var(--ink); outline: none; }
.actions.nx-dl > a.button:hover small, .actions.nx-dl > a.button:focus-visible small { color: inherit; }
.actions.nx-dl b { font: 600 12px/1.2 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.actions.nx-dl small { color: var(--muted); font-size: 12px; font-weight: 400; line-height: 1.4; }
.actions.nx-dl small.when { margin-top: auto; font-size: 11px; }
"""

HOLD_SCRIPT = r"""
(()=>{
const d=document,$=(s,r=d)=>r.querySelector(s),$$=(s,r=d)=>[...r.querySelectorAll(s)];
const main=$('main');if(!main||!$('#holdings'))return;
const store={get(k,f){try{const v=localStorage.getItem(k);return v==null?f:JSON.parse(v)}catch(e){return f}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=t=>{const s=String(t??'').replace(/[−–]/g,'-').replace(/[^0-9.+-]/g,'');const n=parseFloat(s);return Number.isFinite(n)?n:null};
const tone=n=>n>0?'gain':n<0?'loss':'';
const account=(location.pathname.match(/^\/accounts\/([^/]+)/)||[])[1]||'';
const say=(card,html,after)=>{if(!card)return;let p=$(':scope > .nx-answer[data-nx-p4]',card);if(!html){if(p)p.remove();return}
  if(!p){p=d.createElement('p');p.className='nx-answer';p.dataset.nxP4='';const ref=after||$(':scope > .label',card);ref?ref.after(p):card.prepend(p)}if(p.innerHTML!==html)p.innerHTML=html};
const iso=dt=>dt.toISOString().slice(0,10);
const asOfDate=()=>{const v=(($('#asof-select')||{}).value)||'';return /^\d{4}-\d{2}-\d{2}$/.test(v)?new Date(v+'T00:00:00Z'):new Date()};
const ranges=[['1m','1 month'],['3m','3 months'],['ytd','This year'],['12m','12 months'],['all','All']];
const rangeStart=key=>{const e=asOfDate(),s=new Date(e);if(key==='1m')s.setUTCMonth(s.getUTCMonth()-1);else if(key==='3m')s.setUTCMonth(s.getUTCMonth()-3);else if(key==='12m')s.setUTCFullYear(s.getUTCFullYear()-1);else if(key==='ytd'){s.setUTCMonth(0);s.setUTCDate(1)}else return null;return s};
const chipRow=(label,items,attr)=>{const box=d.createElement('div');box.className='nx-chips';box.setAttribute('role','group');box.setAttribute('aria-label',label);
  box.innerHTML=items.map(([k,t])=>`<button type="button" ${attr}="${esc(k)}" aria-pressed="false">${t}</button>`).join('');return box};
const press=(box,attr,key)=>$$('button',box).forEach(b=>b.setAttribute('aria-pressed',String(b.getAttribute(attr)===key)));
const setRange=(fromSel,toSel,key)=>{const from=$(fromSel),to=$(toSel);if(!from||!to)return;const s=rangeStart(key);to.value=to.max||to.value;from.value=s?iso(s)<from.min?from.min:iso(s):from.min||from.value};
const plural=(n,w)=>`${n} ${w}${n===1?'':'s'}`;

// Holdings: map, column sets and position sheet.
const hbody=$('#holdings'),htable=hbody.closest('table'),hcard=hbody.closest('.card');
const heads=()=>$$('thead th',htable).map(th=>th.textContent.trim());
const positions=()=>$$(':scope > tr',hbody).filter(tr=>!tr.classList.contains('total-row')&&tr.children.length>=12).map(tr=>({tr,symbol:(($('strong',tr.children[0])||tr.children[0]).textContent||'').trim(),
  value:num(tr.children[6].dataset.sort),pnl:num(tr.children[9].textContent),weight:num(tr.children[11].dataset.sort),name:tr.children[1].textContent.trim()}));
const squarify=(items,x,y,w,h)=>{const out=[];let rest=items.slice();
  const worst=(row,side)=>{const s=row.reduce((a,b)=>a+b.area,0),mx=Math.max(...row.map(r=>r.area)),mn=Math.min(...row.map(r=>r.area));return Math.max(side*side*mx/(s*s),s*s/(side*side*mn))};
  while(rest.length){const side=Math.min(w,h);let row=[rest[0]],i=1;while(i<rest.length&&worst(row.concat(rest[i]),side)<=worst(row,side)){row.push(rest[i]);i++}
    const s=row.reduce((a,b)=>a+b.area,0);if(w>=h){const cw=s/h;let cy=y;row.forEach(r=>{const ch=r.area/cw;out.push({...r,x,y:cy,w:cw,h:ch});cy+=ch});x+=cw;w-=cw}
    else{const rh=s/w;let cx=x;row.forEach(r=>{const cw=r.area/rh;out.push({...r,x:cx,y,w:cw,h:rh});cx+=cw});y+=rh;h-=rh}rest=rest.slice(i)}return out};
const tint=p=>{if(p===null)return 'var(--line)';const k=Math.min(1,Math.abs(p)/40),pc=Math.round(14+k*46);return `color-mix(in srgb, var(--${p<0?'loss':'gain'}) ${pc}%, var(--bg))`};
let treeSig='';
const tree=()=>{if(!hcard)return;const list=positions().filter(p=>p.value>0);let box=$(':scope > .nx-tree',hcard);
  if(list.length<2){if(box){box.remove();$(':scope > .nx-tree-key',hcard)?.remove()}treeSig='';return}
  if(!box){box=d.createElement('div');box.className='nx-tree';box.setAttribute('role','group');box.setAttribute('aria-label','Holdings map: tiles sized by value, shaded by gain or loss');const key=d.createElement('div');key.className='nx-tree-key';
    key.innerHTML='<span>Size: value</span><span><i style="background:color-mix(in srgb, var(--gain) 50%, var(--bg))"></i>Gain</span><span><i style="background:color-mix(in srgb, var(--loss) 50%, var(--bg))"></i>Loss</span><span>Deeper shade: larger P&amp;L %</span>';
    const tools=$(':scope > .table-tools',hcard);tools?tools.before(box,key):hcard.append(box,key);new ResizeObserver(()=>{treeSig='';tree()}).observe(box)}
  const W=box.clientWidth,H=box.clientHeight,sig=W+'x'+H+'|'+list.map(p=>p.symbol+p.value+p.pnl).join();if(sig===treeSig){dims();return}treeSig=sig;
  const total=list.reduce((a,b)=>a+b.value,0),items=list.sort((a,b)=>b.value-a.value).map(p=>({...p,area:p.value/total*W*H}));
  box.innerHTML=squarify(items,0,0,W,H).map(t=>{const size=t.w<46||t.h<30?'tiny':t.w<80||t.h<46?'small':'';return `<button type="button" class="nx-tile ${size}" data-nx-sym="${esc(t.symbol)}" style="left:${t.x.toFixed(1)}px;top:${t.y.toFixed(1)}px;width:${t.w.toFixed(1)}px;height:${t.h.toFixed(1)}px;--nx-tint:${tint(t.pnl)}" aria-label="${esc(t.symbol)}, ${esc(t.tr.children[11].textContent.trim())} of holdings, ${esc(t.tr.children[9].textContent.trim())}"><b>${esc(t.symbol)}</b><small>${esc(t.tr.children[11].textContent.trim())} · ${esc(t.tr.children[9].textContent.trim())}</small></button>`}).join('');dims()};
const dims=()=>{const box=$(':scope > .nx-tree',hcard);if(!box)return;const hidden=new Set(positions().filter(p=>p.tr.hidden).map(p=>p.symbol));$$('.nx-tile',box).forEach(t=>t.classList.toggle('dim',hidden.has(t.dataset.nxSym)))};
const COLS='wr-next-cols',sets=[['value','Value'],['performance','Performance'],['risk','Risk'],['all','All']];
const columns=()=>{const tools=$(':scope > .table-tools',hcard);if(!tools||$('.nx-seg',tools))return;const seg=d.createElement('div');seg.className='nx-seg';seg.setAttribute('role','group');seg.setAttribute('aria-label','Columns');
  seg.innerHTML=sets.map(([k,t])=>`<button type="button" data-nx-cols="${k}">${t}</button>`).join('');tools.append(seg);
  const apply=k=>{htable.dataset.nxCols=k;press(seg,'data-nx-cols',k)};apply(store.get(COLS,'all'));
  seg.addEventListener('click',e=>{const b=e.target.closest('[data-nx-cols]');if(!b)return;apply(b.dataset.nxCols);store.set(COLS,b.dataset.nxCols)})};
const rows=()=>positions().forEach(p=>{if(!p.tr.tabIndex||p.tr.tabIndex<0){p.tr.tabIndex=0;p.tr.setAttribute('aria-label',`${p.symbol}: open details`)}});
let drawer=null;
const sheet=sym=>{const p=positions().find(x=>x.symbol===sym);if(!p)return;const names=heads();
  const fields=names.map((n,i)=>i===0?'':`<dt>${esc(n)}</dt><dd>${p.tr.children[i].innerHTML}</dd>`).join('');
  const share=p.tr.children[11].textContent.trim(),gain=p.tr.children[8].textContent.trim(),pct=p.tr.children[9].textContent.trim();
  const line=`${esc(p.name||p.symbol)} is <b>${esc(share)}</b> of holdings${/\d/.test(gain)?`, ${p.pnl<0?'down':'up'} <b class="${tone(p.pnl)}">${esc(pct.replace(/^[+−-]/,''))}</b> (${esc(gain)}) on its cost`:''}.`;
  if(drawer)drawer.remove();drawer=d.createElement('dialog');drawer.className='nx-drawer';drawer.setAttribute('aria-label',`${sym} details`);
  drawer.innerHTML=`<div class="nx-drawer-head"><b>${esc(sym)}</b><button type="button" aria-label="Close">×</button></div><div class="nx-drawer-body"><p class="nx-answer">${line}</p><dl>${fields}</dl><div class="actions"><a class="button" href="/accounts/${encodeURIComponent(account)}/trades?symbol=${encodeURIComponent(sym)}">Trades in ${esc(sym)}</a></div></div>`;
  d.body.appendChild(drawer);const back=d.activeElement;$('button',drawer).addEventListener('click',()=>drawer.close());drawer.addEventListener('click',e=>{if(e.target===drawer)drawer.close()});
  drawer.addEventListener('close',()=>{drawer.remove();drawer=null;if(back&&back.focus)back.focus()});drawer.showModal()};
hcard&&hcard.addEventListener('click',e=>{const t=e.target.closest('[data-nx-sym]');if(t)return sheet(t.dataset.nxSym);const tr=e.target.closest('#holdings > tr');if(tr&&!e.target.closest('a,button,input'))sheet((($('strong',tr.children[0])||tr.children[0]||{}).textContent||'').trim())});
hbody.addEventListener('keydown',e=>{if(e.key!=='Enter'&&e.key!==' ')return;const tr=e.target.closest('#holdings > tr');if(!tr||tr.classList.contains('total-row'))return;e.preventDefault();sheet((($('strong',tr.children[0])||tr.children[0]).textContent||'').trim())});
const hf=$('#holdings-filter');if(hf)hf.addEventListener('input',()=>requestAnimationFrame(dims));
const holdings=()=>{columns();rows();tree()};

// Cash: quick ranges, type chips and a sentence.
const cbody=$('#cash-activity'),ccard=cbody&&cbody.closest('.card');let cashType='',cashTypeSig='';
const cashTools=()=>{const ctl=$('#cash-ledger-controls');if(!ctl||$('.nx-chips[data-nx-range]',ctl))return;const box=chipRow('Quick ranges',ranges,'data-nx-cr');box.dataset.nxRange='';$('.filter-grid',ctl).before(box);
  box.addEventListener('click',e=>{const b=e.target.closest('[data-nx-cr]');if(!b)return;setRange('#cash-from','#cash-to',b.dataset.nxCr);press(box,'data-nx-cr',b.dataset.nxCr);$('#cash-apply')?.click()});
  ['#cash-from','#cash-to'].forEach(s=>$(s)?.addEventListener('input',()=>press(box,'data-nx-cr','')))};
const cash=()=>{if(!cbody||!ccard)return;cashTools();const all=$$(':scope > tr',cbody).filter(tr=>tr.children.length>=6);let types=$('#nx-cash-types');
  const count={};all.forEach(tr=>{const t=tr.children[2].textContent.trim();if(t)count[t]=(count[t]||0)+1});const names=Object.keys(count).sort((a,b)=>count[b]-count[a]);
  if(cashType&&!count[cashType])cashType='';
  const sig=names.map(n=>n+count[n]).join('|')+'|'+cashType;
  if(names.length<2){if(types)types.remove()}else if(sig!==cashTypeSig||!types){if(!types){types=d.createElement('div');types.id='nx-cash-types';types.className='nx-chips';types.setAttribute('role','group');types.setAttribute('aria-label','Activity type');$('#cash-ledger-table').before(types)}
    types.innerHTML=`<button type="button" data-nx-ct="" aria-pressed="${!cashType}">All<small>${all.length}</small></button>`+names.map(n=>`<button type="button" data-nx-ct="${esc(n)}" aria-pressed="${n===cashType}">${esc(n)}<small>${count[n]}</small></button>`).join('')}
  cashTypeSig=sig;all.forEach(tr=>{const hide=!!cashType&&tr.children[2].textContent.trim()!==cashType;if(tr.hidden!==hide)tr.hidden=hide});
  const shown=all.filter(tr=>!tr.hidden);if(!shown.length)return say(ccard,'',$('#cash-ledger-controls'));
  const net={};shown.forEach(tr=>{const c=tr.children[4].textContent.trim(),v=num(tr.children[5].textContent);if(c&&v!==null)net[c]=(net[c]||0)+v});
  const dates=shown.map(tr=>tr.children[0].textContent.trim()).filter(Boolean).sort(),f=v=>(v>0?'+':v<0?'−':'')+new Intl.NumberFormat('en-US',{maximumFractionDigits:2}).format(Math.abs(v));
  const nets=Object.entries(net).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,3).map(([c,v])=>`<b class="${tone(v)}">${f(v)} ${esc(c)}</b>`);
  say(ccard,`<b>${plural(shown.length,'entry').replace('entrys','entries')}</b>${cashType?` of type <b>${esc(cashType)}</b>`:''} from <b>${esc(dates[0])}</b> to <b>${esc(dates.at(-1))}</b>.${nets.length?` Net of these entries: ${nets.join(', ')}.`:''}`,$('#cash-ledger-controls'))};
ccard&&ccard.addEventListener('click',e=>{const b=e.target.closest('[data-nx-ct]');if(!b)return;cashType=b.dataset.nxCt;cashTypeSig='';cash()});

// Trades: side, quick ranges, symbol filter and a sentence.
const tbody=$('#trade-history'),tcard=tbody&&tbody.closest('.card');
const applyTrades=()=>$('#trade-apply')?.click();
const tradeTools=()=>{const ctl=$('#trade-filter-controls');if(!ctl||$('#nx-trade-chips')||ctl.hidden)return;const side=$('#trade-side');
  const wrap=d.createElement('div');wrap.id='nx-trade-chips';const seg=d.createElement('div');seg.className='nx-seg';seg.setAttribute('role','group');seg.setAttribute('aria-label','Side');
  seg.innerHTML=[['','All'],['BUY','Buys'],['SELL','Sells']].map(([k,t])=>`<button type="button" data-nx-side="${k}">${t}</button>`).join('');const rs=chipRow('Quick ranges',ranges,'data-nx-tr');rs.style.margin='0 0 12px';
  wrap.append(seg,rs);ctl.before(wrap);const sync=()=>press(seg,'data-nx-side',side?side.value:'');sync();side&&side.addEventListener('change',sync);
  seg.addEventListener('click',e=>{const b=e.target.closest('[data-nx-side]');if(!b||!side)return;side.value=b.dataset.nxSide;sync();applyTrades()});
  rs.addEventListener('click',e=>{const b=e.target.closest('[data-nx-tr]');if(!b)return;setRange('#trade-from','#trade-to',b.dataset.nxTr);press(rs,'data-nx-tr',b.dataset.nxTr);applyTrades()});
  ['#trade-from','#trade-to'].forEach(s=>$(s)?.addEventListener('input',()=>press(rs,'data-nx-tr','')))};
const symInput=$('#trade-symbol'),wanted=new URLSearchParams(location.search).get('symbol');let wantedDone=!wanted;
if(wanted&&symInput)symInput.value=wanted.slice(0,64);
const trades=()=>{if(!tbody||!tcard)return;tradeTools();const all=$$(':scope > tr',tbody).filter(tr=>tr.children.length>=11);
  if(!wantedDone&&all.length){wantedDone=true;if(!all.every(tr=>tr.children[2].textContent.toUpperCase().includes(wanted.toUpperCase()))){symInput.value=wanted;applyTrades();return}}
  let chip=$('#nx-trade-sym');const sym=symInput?symInput.value.trim():'';
  if(sym){if(!chip){chip=d.createElement('div');chip.id='nx-trade-sym';chip.className='nx-chips';$('#trade-note').before(chip)}const h=`<button type="button" data-nx-clear aria-pressed="true" aria-label="Show all symbols">Symbol: ${esc(sym)} ×</button>`;if(chip.innerHTML!==h)chip.innerHTML=h}else if(chip)chip.remove();
  if(!all.length)return say(tcard,'',$('#trade-source-note'));
  const buys=all.filter(tr=>/buy/i.test(tr.children[3].textContent)).length,sells=all.filter(tr=>/sell/i.test(tr.children[3].textContent)).length,by={};
  all.forEach(tr=>{const s=(($('strong',tr.children[2])||tr.children[2]).textContent||'').trim();by[s]=(by[s]||0)+1});const top=Object.entries(by).sort((a,b)=>b[1]-a[1]),dates=all.map(tr=>tr.children[0].textContent.trim()).sort();
  say(tcard,`<b>${plural(all.length,'trade')}</b> from <b>${esc(dates[0])}</b> to <b>${esc(dates.at(-1))}</b>: ${buys} ${buys===1?'buy':'buys'} and ${sells} ${sells===1?'sale':'sales'}${top.length>1?` across ${top.length} instruments. Most traded: <b>${esc(top[0][0])}</b> (${top[0][1]})`:''}.`,$('#trade-source-note'))};
tcard&&tcard.addEventListener('click',e=>{if(e.target.closest('[data-nx-clear]')){symInput.value='';applyTrades();return}const s=e.target.closest('#trade-history td:nth-child(3) strong');if(s&&symInput){symInput.value=s.textContent.trim();applyTrades()}});

// Reports: month shelf and folded design tools.
const shelf=()=>{const sel=$('#report-month');if(!sel)return;const card=sel.closest('.card'),opts=[...sel.options];let box=$('#nx-shelf');
  if(opts.length<2){if(box)box.remove();sel.classList.remove('nx-quiet');return}
  if(!box){box=d.createElement('div');box.id='nx-shelf';box.className='nx-shelf';box.setAttribute('role','group');box.setAttribute('aria-label','Report months');sel.closest('.actions').before(box);
    box.addEventListener('click',e=>{const b=e.target.closest('[data-nx-month]');if(!b)return;sel.value=b.dataset.nxMonth;sel.dispatchEvent(new Event('change',{bubbles:true}));shelf()})}
  sel.classList.add('nx-quiet');const fmt=v=>{const m=/^(\d{4})-(\d{2})-\d{2}$/.exec(v);return m?['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][Number(m[2])-1]+' '+m[1]:v};
  const h=opts.map(o=>{const [date,...rest]=o.textContent.split(' — '),status=rest.join(' — ');return `<button type="button" data-nx-month="${esc(o.value)}" aria-pressed="${o.value===sel.value}"><b>${esc(fmt(date.trim()))}</b><small><span class="dot${/^ready$/i.test(status.trim())?' ready':''}"></span>${esc(status||date)}</small></button>`}).join('');
  if(box.innerHTML!==h)box.innerHTML=h;
  const tools=$('#report-design-controls');if(tools&&!tools.closest('details.nx-fold')){const det=d.createElement('details');det.className='nx-fold';det.innerHTML='<summary>Report design tools (owner)</summary>';tools.before(det);det.append(tools)}};

// Exports: cards with what each file holds and when it was last downloaded here.
const DL='wr-next-exports',about={'export-performance':'Daily values and returns up to the selected date.','export-holdings':'Every position on the selected date.','export-cash':'Cash activity up to the selected date.','export-trades-csv':'Executed trades up to the selected date.','export-reporting-snapshot':'The frozen month-end figures behind the reports.'};
const exportsCard=()=>{const a=$('#export-performance');if(!a)return;const row=a.closest('.actions');if(!row)return;if(!row.classList.contains('nx-dl')){row.classList.add('nx-dl');
    $$(':scope > a',row).forEach(x=>{const key=x.id||(/\/trades$/.test(x.getAttribute('href')||'')?'trade-controls':'');const text=x.textContent.trim();x.dataset.nxKey=key||text;
      x.innerHTML=`<b>${esc(text)}</b><small>${esc(about[x.id]||(key==='trade-controls'?'Filter trades by date, symbol or side before downloading.':''))}</small>${x.id?'<small class="when"></small>':''}`});
    row.addEventListener('click',e=>{const x=e.target.closest('a[id]');if(!x)return;const seen=store.get(DL,{});seen[account+'|'+x.id]=new Date().toISOString();store.set(DL,seen);exportsCard()})}
  const seen=store.get(DL,{});$$(':scope > a[id] small.when',row).forEach(s=>{const t=seen[account+'|'+s.parentElement.id],txt=t?'Last downloaded here '+new Date(t).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'}):'Not downloaded on this device yet';if(s.textContent!==txt)s.textContent=txt})};

const run=()=>{holdings();cash();trades();shelf();exportsCard()};
let pending=false;const tick=()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;obs.disconnect();try{run()}finally{obs.observe(main,{childList:true,subtree:true,characterData:true})}})};
const obs=new MutationObserver(tick);run();obs.observe(main,{childList:true,subtree:true,characterData:true});
})();
"""
