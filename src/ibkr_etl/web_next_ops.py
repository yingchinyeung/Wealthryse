"""WealthRyse Next, phase 5 of the page-by-page redesign: guide and operations pages.

Shown to whoever WEB_NEXT_PAGES gives phase 5 (see web_next.py). Like the other
phases it only reads what the page already shows and presses the page's own
controls; it calls nothing new and changes no figure.

* System health: a strip that sums up every section and jumps to it, a live
  "checked N seconds ago" with Check now, a sentence on benchmarks and on
  assurance with problems listed first, and the burn-in as a row of days.
* Metrics guide: matches marked in the names, the search bar
  stays in reach on wide screens, and the section chips follow the scroll.
* Agent inbox: status and type chips with counts, items that need a decision
  marked and listed first, and J / K to move between items.
* Account onboarding and IBKR connections: an "On this page" bar that follows
  the scroll, and long explanations shortened to two lines with More.
"""

OPS_CSS = r"""
/* Section strip and on-this-page bar. */
.nx-strip, .nx-toc { display: flex; flex-wrap: wrap; gap: 0; margin: 16px 0 0; border-top: 1px solid var(--line); border-left: 1px solid var(--line); }
.nx-strip a, .nx-toc a { display: flex; align-items: center; gap: 8px; padding: 10px 14px; color: var(--ink); text-decoration: none; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); font: 600 10px/1.3 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.nx-strip a:hover, .nx-strip a:focus-visible, .nx-toc a:hover, .nx-toc a:focus-visible, .nx-toc a[aria-current="true"] { color: var(--bg); background: var(--ink); outline: none; }
.nx-strip b { font: 600 13px/1 var(--font-mission); letter-spacing: .04em; }
.nx-dot { display: inline-block; flex: none; width: 8px; height: 8px; border: 1px solid currentColor; border-radius: 50%; }
.nx-dot.pass { background: currentColor; }
.nx-dot.warn { border-style: dashed; }
.nx-dot.fail { color: var(--loss); background: var(--loss); border-color: var(--loss); }
.nx-toc { position: sticky; top: 0; z-index: 5; margin: 0 0 18px; background: var(--bg); }
@media (min-width: 901px) { html[data-design="next"] .nx-toc { top: 60px; } }
.nx-toc { flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; }
.nx-toc a { flex: none; }
[data-section="account-onboarding"] > .card, [data-section="ibkr-connections"] > .card, [data-section="system-health"] > .card { scroll-margin-top: 120px; }
/* Live check time. */
.nx-live { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 12px 0 0; color: var(--muted); font-size: 13px; }
.nx-live button { padding: 7px 10px; color: var(--ink); background: none; border: 1px solid var(--line-strong, var(--line)); border-radius: 0; cursor: pointer; font: 600 10px/1.2 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.nx-live button:hover, .nx-live button:focus-visible { color: var(--bg); background: var(--ink); outline: none; }
.nx-live .nx-pulse { width: 7px; height: 7px; background: var(--ink); border-radius: 50%; animation: nx-pulse 2s ease-in-out infinite; }
@keyframes nx-pulse { 50% { opacity: .2; } }
@media (prefers-reduced-motion: reduce) { .nx-live .nx-pulse { animation: none; } }
/* Burn-in days. */
.nx-days { display: grid; grid-template-columns: repeat(var(--nx-n, 14), minmax(0, 1fr)); gap: 3px; max-width: 560px; margin: 14px 0 6px; }
.nx-days i { height: 22px; border: 1px solid var(--line-strong, var(--line)); }
.nx-days i.pass { background: var(--ink); border-color: var(--ink); }
.nx-days i.fail { background: var(--loss); border-color: var(--loss); }
.nx-days-ends { display: flex; justify-content: space-between; max-width: 560px; color: var(--muted); font-size: 12px; }
details.nx-fold { margin: 14px 0 0; border-top: 1px solid var(--line); }
details.nx-fold > summary { padding: 12px 0; color: var(--muted); cursor: pointer; font: 600 10px/1.4 var(--font-mission); letter-spacing: .16em; text-transform: uppercase; }
/* Shortened explanations. */
.nx-clamp { display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.nx-more { padding: 0; margin: 0 0 12px; color: var(--ink); background: none; border: 0; cursor: pointer; font: 600 10px/1.4 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; text-decoration: underline; text-underline-offset: 3px; }
/* Metrics guide. */
@media (min-width: 901px) { html[data-nx-pages] .metric-guide-toolbar { position: sticky; top: 60px; z-index: 5; } }
.metric-definition h3 mark { color: var(--bg); background: var(--ink); padding: 0 2px; }
html[data-nx-pages] .metric-jump a[aria-current="true"] { color: var(--bg); background: var(--ink); }
.metric-definition.nx-flash { animation: nx-flash 1.6s ease-out; }
@keyframes nx-flash { from { box-shadow: inset 0 0 0 2px var(--ink); } to { box-shadow: inset 0 0 0 2px transparent; } }
/* Agent inbox. */
html[data-nx-pages] .inbox-filters label { display: none; }
.nx-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 0; }
.nx-chips button { padding: 7px 10px; color: var(--ink); background: none; border: 1px solid var(--line-strong, var(--line)); border-radius: 0; cursor: pointer; font: 600 10px/1.2 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.nx-chips button[aria-pressed="true"], .nx-chips button:hover, .nx-chips button:focus-visible { color: var(--bg); background: var(--ink); outline: none; }
.nx-chips button small { margin-left: 6px; font-weight: 400; opacity: .7; }
.inbox-item.nx-decide { border-left: 3px solid var(--ink); }
.nx-flag { display: inline-block; margin: 0 0 8px; padding: 3px 7px; color: var(--bg); background: var(--ink); font: 600 10px/1.2 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.inbox-item:focus-visible, .inbox-item.nx-focus { outline: 2px solid var(--ink); outline-offset: -2px; }
"""

OPS_SCRIPT = r"""
(()=>{
const d=document,$=(s,r=d)=>r.querySelector(s),$$=(s,r=d)=>[...r.querySelectorAll(s)];
const main=$('main');if(!main)return;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const say=(card,html,after)=>{if(!card)return;let p=$(':scope > .nx-answer[data-nx-p5]',card);if(!html){if(p)p.remove();return}
  if(!p){p=d.createElement('p');p.className='nx-answer';p.dataset.nxP5='';const ref=after||$(':scope > .label',card);ref?ref.after(p):card.prepend(p)}if(p.innerHTML!==html)p.innerHTML=html};
const typing=e=>e.target.closest&&e.target.closest('input,textarea,select,[contenteditable="true"]');
const label=card=>((($(':scope > .label',card)||{}).textContent)||'').replace(/about this/i,'').trim();
const anchor=(card,n)=>{if(!card.id)card.id='nx-sec-'+n;return card.id};
const spy=(links,targets)=>{if(!('IntersectionObserver' in window))return;const seen=new Map();const io=new IntersectionObserver(es=>{es.forEach(e=>seen.set(e.target,e.isIntersecting?e.boundingClientRect.top:null));
  const top=[...seen].filter(([,t])=>t!==null).sort((a,b)=>Math.abs(a[1])-Math.abs(b[1]))[0];links().forEach(a=>a.setAttribute('aria-current',String(!!top&&a.getAttribute('href')==='#'+top[0].id)))},{rootMargin:'-80px 0px -55% 0px'});targets.forEach(t=>io.observe(t))};

// Long explanations: two lines and More.
const clamp=root=>$$('.card > p.muted, .card > .muted:not([id]):not(.certified-line)',root).forEach(p=>{if(p.dataset.nxClamp||p.textContent.trim().length<220||p.querySelector('input,select,button,a'))return;p.dataset.nxClamp='';p.classList.add('nx-clamp');if(p.scrollHeight<=p.clientHeight+2){p.classList.remove('nx-clamp');return}
  const b=d.createElement('button');b.type='button';b.className='nx-more';b.textContent='More';b.setAttribute('aria-expanded','false');b.addEventListener('click',()=>{const open=p.classList.toggle('nx-clamp');b.textContent=open?'More':'Less';b.setAttribute('aria-expanded',String(!open))});p.after(b)});

// On-this-page bar for long operations pages.
const tocPage=$('[data-section="account-onboarding"], [data-section="ibkr-connections"]');
if(tocPage){const cards=$$(':scope > .card',tocPage).filter(c=>label(c));if(cards.length>3){const nav=d.createElement('nav');nav.className='nx-toc';nav.setAttribute('aria-label','On this page');
  nav.innerHTML=cards.map((c,i)=>`<a href="#${esc(anchor(c,i))}">${esc(label(c))}</a>`).join('');tocPage.before(nav);spy(()=>$$('a',nav),cards)}clamp(tocPage)}

// System health.
const health=$('[data-section="system-health"]');let checkedAt=0,lastNote='';
const ago=()=>{if(!checkedAt)return 'Not checked yet';const s=Math.round((Date.now()-checkedAt)/1000);return s<10?'Checked just now':s<60?`Checked ${s} seconds ago`:`Checked ${Math.round(s/60)} minute${s<90?'':'s'} ago`};
const pillState=el=>{const c=(el&&el.className)||'';return /is-(fail|critical|error)/.test(c)?'fail':/is-(warn|warning)/.test(c)?'warn':/is-pass/.test(c)?'pass':''};
const healthRun=()=>{if(!health)return;const hero=$('#health-overview')?.closest('.card');const note=$('#health-note');
  if(note&&note.textContent!==lastNote){lastNote=note.textContent;if(/Last checked/.test(lastNote))checkedAt=Date.now()}
  if(hero&&!$('.nx-live',hero)){const live=d.createElement('div');live.className='nx-live';live.innerHTML='<span class="nx-pulse" aria-hidden="true"></span><span class="nx-ago" aria-live="off"></span>'+(typeof refreshHealth==='function'?'<button type="button">Check now</button>':'');
    $('#health-overview').after(live);$('button',live)?.addEventListener('click',()=>{$('.nx-ago',live).textContent='Checking…';refreshHealth()});setInterval(()=>{const a=$('.nx-ago',live);if(a)a.textContent=ago()},1000)}
  const incidents=$$('#health-incidents .health-incident'),findings=$$('#health-findings > p .ops-pill'),bench=$$('#benchmark-health > .status-item'),assure=$$('#assurance-summary > .status-item');
  const benchOk=bench.filter(b=>pillState($('.ops-pill',b))==='pass'),assureOk=assure.filter(a=>pillState($('.ops-pill',a))==='pass');
  const burn=(($$('#burn-in-summary .status-item').find(x=>/consecutive/i.test(x.textContent))||{}).textContent||'').match(/(\d+)\s*\/\s*(\d+)/);
  const sec=(el)=>el?anchor(el.closest('.card'),'h'+[...health.children].indexOf(el.closest('.card'))):'';
  const crit=incidents.some(i=>/is-critical/.test(i.className));
  const items=[[incidents.length?(crit?'fail':'warn'):'pass','Incidents',incidents.length,sec($('#health-incidents'))],[findings.length?(findings.some(f=>pillState(f)==='fail')?'fail':'warn'):'pass','Account findings',findings.length,sec($('#health-findings'))],
    [bench.length&&benchOk.length===bench.length?'pass':'warn','Benchmarks current',`${benchOk.length}/${bench.length}`,sec($('#benchmark-health'))],[assure.length&&assureOk.length===assure.length?'pass':'warn','Portfolios passing',`${assureOk.length}/${assure.length}`,sec($('#assurance-summary'))]];
  if(burn)items.push([Number(burn[1])>=Number(burn[2])?'pass':'warn','Burn-in days',`${burn[1]}/${burn[2]}`,sec($('#burn-in-summary'))]);
  if(hero&&bench.length+assure.length){let strip=$(':scope > .nx-strip',hero);if(!strip){strip=d.createElement('nav');strip.className='nx-strip';strip.setAttribute('aria-label','Sections');$('.nx-live',hero)?.after(strip)}
    const h=items.map(([s,t,v,id])=>`<a href="#${esc(id)}"><span class="nx-dot ${s}" aria-hidden="true"></span>${esc(t)} <b>${esc(v)}</b></a>`).join('');if(strip.innerHTML!==h)strip.innerHTML=h}
  // Problems first, with a sentence.
  const firstBad=(box,list)=>{const bad=list.filter(x=>pillState($('.ops-pill',x))!=='pass');bad.reverse().forEach(x=>{if(box.firstElementChild!==x)box.prepend(x)})};
  const bbox=$('#benchmark-health');if(bbox&&bench.length){firstBad(bbox,bench);const bad=bench.filter(b=>!benchOk.includes(b));
    say(bbox.closest('.card'),`<b>${benchOk.length} of ${bench.length}</b> benchmarks are current.${bad.length?' '+bad.map(b=>`<b>${esc(((($('.label',b)||{}).textContent)||'').toUpperCase())}</b> last updated ${esc(($('strong',b)||{}).textContent)}`).join(', ')+'.':''}`)}
  const abox=$('#assurance-summary');if(abox&&assure.length){firstBad(abox,assure);const bad=assure.filter(a=>!assureOk.includes(a));
    say(abox.closest('.card'),`<b>${assureOk.length} of ${assure.length}</b> portfolios pass their checks.${bad.length?' Needs a look: '+bad.map(a=>`<b>${esc(($('.label',a)||{}).textContent)}</b>`).join(', ')+'.':''}`,$('#assurance-status'))}
  // Burn-in as a row of days.
  const res=$('#burn-in-results');if(res&&burn){const card=res.closest('.card'),n=Math.min(60,Number(burn[2])),k=Math.min(n,Number(burn[1]));let row=$('.nx-days',card);
    if(!row){row=d.createElement('div');row.className='nx-days';row.setAttribute('role','img');const ends=d.createElement('div');ends.className='nx-days-ends';$('#burn-in-summary').after(row,ends)}
    row.style.setProperty('--nx-n',n);row.setAttribute('aria-label',`${k} of ${n} consecutive passing days`);const h=Array.from({length:n},(_,i)=>`<i class="${i<k?'pass':''}"></i>`).join('');if(row.innerHTML!==h)row.innerHTML=h;
    const left=n-k,ends=row.nextElementSibling,t=`<span>${k} of ${n} days passed</span><span>${left?`${left} more passing day${left===1?'':'s'} to finish`:'Complete'}</span>`;if(ends.innerHTML!==t)ends.innerHTML=t;
    if(!res.closest('details.nx-fold')&&$('h3',res)){const det=d.createElement('details');det.className='nx-fold';det.innerHTML='<summary>Daily results</summary>';res.before(det);det.append(res)}}
};

// Metrics guide.
const search=$('#metric-search');
if(search){const heads=$$('.metric-definition h3');heads.forEach(h=>h.dataset.nxText=h.textContent);
  const mark=()=>{const q=search.value.trim().toLowerCase();heads.forEach(h=>{const t=h.dataset.nxText,i=q?t.toLowerCase().indexOf(q):-1;const html=i<0?esc(t):esc(t.slice(0,i))+'<mark>'+esc(t.slice(i,i+q.length))+'</mark>'+esc(t.slice(i+q.length));if(h.innerHTML!==html)h.innerHTML=html})};
  search.addEventListener('input',mark);mark();
  const chips=$$('.metric-jump a');spy(()=>chips,$$('.metric-guide-section'));
  const flash=()=>{const t=location.hash&&d.getElementById(decodeURIComponent(location.hash.slice(1)));if(t&&t.classList.contains('metric-definition')){t.classList.remove('nx-flash');void t.offsetWidth;t.classList.add('nx-flash')}};
  window.addEventListener('hashchange',flash);setTimeout(flash,300)}

// Agent inbox.
const inbox=$('[data-section="agent-inbox"]');let inboxSig='';
const decide=card=>!!card.querySelector('[data-d="approved"]')&&!!card.querySelector('[data-d="dismissed"]')&&!card.querySelector('[data-d="read"]')&&/hold|disagree|blocked/i.test(card.textContent);
const inboxRun=()=>{if(!inbox)return;const f=$('#filter'),k=$('#kind'),box=$('#items');if(!f||!k||!box)return;const summary=$('#inbox-summary');
  let chips=$('#nx-inbox-chips');if(!chips){chips=d.createElement('div');chips.id='nx-inbox-chips';$('.inbox-filters',inbox).after(chips);chips.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;const sel=b.dataset.nxF!=null?f:k;sel.value=b.dataset.nxF??b.dataset.nxK;sel.dispatchEvent(new Event('change',{bubbles:true}));inboxSig='';inboxRun()})}
  const cards=$$(':scope > .inbox-item',box);cards.forEach(c=>{if(!c.hasAttribute('tabindex'))c.tabIndex=0;const need=decide(c);c.classList.toggle('nx-decide',need);let flag=$(':scope > .nx-flag',c);if(need&&!flag){flag=d.createElement('span');flag.className='nx-flag';flag.textContent='Needs your decision';c.prepend(flag)}else if(!need&&flag)flag.remove()});
  cards.filter(c=>c.classList.contains('nx-decide')).reverse().forEach(c=>{if(box.firstElementChild!==c)box.prepend(c)});
  const sig=[...f.options].map(o=>o.value).join()+'|'+[...k.options].map(o=>o.value).join()+'|'+f.value+'|'+k.value+'|'+(summary?summary.textContent:'')+'|'+cards.length;if(sig===inboxSig)return;inboxSig=sig;
  const statusRow=[...f.options].map(o=>`<button type="button" data-nx-f="${esc(o.value)}" aria-pressed="${o.value===f.value}">${esc(o.textContent)}</button>`).join('');
  const kindRow=k.options.length>2?[...k.options].map(o=>`<button type="button" data-nx-k="${esc(o.value)}" aria-pressed="${o.value===k.value}">${esc(o.textContent)}</button>`).join(''):'';
  chips.innerHTML=`<div class="nx-chips" role="group" aria-label="Show">${statusRow}</div>${kindRow?`<div class="nx-chips" role="group" aria-label="Type">${kindRow}</div>`:''}`;
  const need=cards.filter(c=>c.classList.contains('nx-decide')).length;if(summary&&need&&!/decision/.test(summary.textContent))summary.textContent+=` · ${need} need${need===1?'s':''} your decision`};
if(inbox)d.addEventListener('keydown',e=>{if(typing(e)||e.metaKey||e.ctrlKey||e.altKey||!/^[jk]$/i.test(e.key))return;const cards=$$('#items > .inbox-item');if(!cards.length)return;const i=cards.indexOf(d.activeElement.closest?d.activeElement.closest('.inbox-item'):null);
  const n=e.key.toLowerCase()==='j'?Math.min(cards.length-1,i+1):Math.max(0,i<0?0:i-1);cards[n].focus();cards[n].scrollIntoView({block:'nearest'});e.preventDefault()});

const run=()=>{healthRun();inboxRun();if(health)clamp(health)};
let pending=false;const tick=()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;obs.disconnect();try{run()}finally{obs.observe(main,{childList:true,subtree:true,characterData:true})}})};
const obs=new MutationObserver(tick);run();obs.observe(main,{childList:true,subtree:true,characterData:true});
})();
"""
