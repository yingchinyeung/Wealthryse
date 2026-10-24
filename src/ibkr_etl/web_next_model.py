"""WealthRyse Next, phase 3 of the page-by-page redesign: modelling.

Shown to whoever WEB_NEXT_PAGES gives phase 3 (see web_next.py). The models
already run when the page opens; this makes them respond to their inputs and
read more plainly. It calls the same model endpoints with the same inputs the
Run buttons send, and restates figures the page has already shown.

* Wealth projection: recalculates when an input changes (number boxes wait until
  you stop typing), sliders for horizon and monthly contribution, the target
  drawn on the chart, an answer sentence, and two saved scenarios to compare.
* Risk outlook and market regimes: an answer sentence each.
* What-if: a slider beside each weight, before and after bars of the mix, the
  comparison reruns once you let go, and a sentence on what changes.
* Optimisation: reruns when the cap changes, a sentence comparing the mixes,
  and the frontier's points select their row.
* Factor model: reruns when the region changes, diverging bars for betas, a
  bar for the share explained, and a sentence on the main leanings.
* Stress test: scenarios as cards, each keeping its result once run, and a
  sentence on the selected one.
"""

MODEL_CSS = r"""
/* High and low labels don't suit fan charts or backtests. */
html[data-nx-pages] #model-projection .nx-mark, html[data-nx-pages] #model-risk .nx-mark, html[data-nx-pages] #model-regimes .nx-mark { display: none; }
/* Sliders. */
.nx-slider { display: block; width: 100%; margin: 10px 0 0; accent-color: var(--ink); }
.nx-slider-ends { display: flex; justify-content: space-between; color: var(--muted); font-size: 11px; }
#whatif-holdings td .nx-slider { width: 140px; margin: 0 10px 0 0; vertical-align: middle; display: inline-block; }
@media (max-width: 700px) { #whatif-holdings td .nx-slider { display: none; } }
/* Target line on the projection. */
.nx-target { position: absolute; z-index: 1; height: 0; border-top: 1px dashed var(--ink); pointer-events: none; }
.nx-target span { position: absolute; right: 0; bottom: 4px; color: var(--ink); font: 600 10px/1 var(--font-mission); letter-spacing: .12em; text-transform: uppercase; white-space: nowrap; }
/* Scenarios. */
.nx-scen { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 14px 0 0; }
.nx-scen button, .nx-cards button.nx-card { padding: 7px 10px; color: var(--ink); background: none; border: 1px solid var(--line-strong, var(--line)); border-radius: 0; cursor: pointer; font: 600 10px/1.2 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.nx-scen button:hover, .nx-scen button:focus-visible { color: var(--bg); background: var(--ink); outline: none; }
.nx-compare { width: 100%; margin: 12px 0 4px; border-collapse: collapse; font-size: 13px; }
.nx-compare th, .nx-compare td { padding: 7px 10px; border-bottom: 1px solid var(--line); text-align: right; font-variant-numeric: tabular-nums; }
.nx-compare th:first-child, .nx-compare td:first-child { text-align: left; color: var(--muted); }
.nx-compare thead th { color: var(--muted); font: 600 10px/1.4 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
/* What-if mix bars. */
.nx-mix { margin: 14px 0 6px; }
.nx-mix-row { display: grid; grid-template-columns: 76px 1fr; gap: 12px; align-items: center; margin: 6px 0; }
.nx-mix-row > span:first-child { color: var(--muted); font: 600 10px/1.4 var(--font-mission); letter-spacing: .14em; text-transform: uppercase; }
.nx-mix-bar { display: flex; height: 14px; overflow: hidden; background: var(--line); }
.nx-mix-bar i { display: block; height: 100%; background: var(--ink); border-right: 1px solid var(--bg); }
.nx-mix-bar i:nth-child(even) { opacity: .62; }
.nx-mix-bar i.cash { background: transparent; border-right: 0; }
/* Optimisation: the selected mix. */
html[data-nx-pages] #optimise-portfolios tr.nx-pick > * { background: var(--mission-hover, rgba(127, 127, 127, .14)) !important; }
#optimise-frontier circle.point { cursor: pointer; }
/* Factor model. */
.nx-div { position: relative; display: block; width: 120px; height: 8px; margin: 6px 0 0; }
.nx-div::before { position: absolute; top: -3px; bottom: -3px; left: 50%; width: 1px; content: ""; background: var(--line-strong, var(--line)); }
.nx-div i { position: absolute; top: 0; bottom: 0; background: var(--ink); }
.nx-div i.neg { background: var(--loss); }
.nx-share { margin: 6px 0 16px; }
.nx-share-bar { display: flex; height: 10px; margin: 8px 0 6px; background: var(--line); }
.nx-share-bar i { display: block; height: 100%; background: var(--ink); }
.nx-share-ends { display: flex; justify-content: space-between; color: var(--muted); font-size: 12px; }
/* Stress cards. */
.nx-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 10px; margin: 12px 0 14px; }
.nx-cards button.nx-card { display: flex; flex-direction: column; gap: 10px; min-height: 92px; padding: 14px; text-align: left; }
.nx-card b { font: 500 24px/1 var(--font-mission, inherit); letter-spacing: 0; text-transform: none; }
.nx-card b.gain { color: var(--gain); }
.nx-card b.loss { color: var(--loss); }
.nx-card small { color: var(--muted); font: 500 11px/1.3 inherit; letter-spacing: 0; text-transform: none; }
.nx-cards button.nx-card[aria-pressed="true"] { border-color: var(--ink); box-shadow: inset 0 0 0 1px var(--ink); }
.nx-cards button.nx-card:hover, .nx-cards button.nx-card:focus-visible { border-color: var(--ink); outline: none; }
"""

MODEL_SCRIPT = r"""
(()=>{
const d=document,$=(s,r=d)=>r.querySelector(s),$$=(s,r=d)=>[...r.querySelectorAll(s)];
const main=$('main');if(!main||!$('#model-projection'))return;
const store={get(k,f){try{const v=localStorage.getItem(k);return v==null?f:JSON.parse(v)}catch(e){return f}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=t=>{const s=String(t??'').replace(/[−–]/g,'-').replace(/[^0-9.+-]/g,'');const n=parseFloat(s);return Number.isFinite(n)?n:null};
const tone=n=>n>0?'gain':n<0?'loss':'';
const account=(location.pathname.match(/^\/accounts\/([^/]+)/)||[])[1]||'';
const charts=typeof chartState!=='undefined'?chartState:null;
const say=(card,html,after)=>{if(!card)return;let p=$(':scope > .nx-answer[data-nx-p3]',card);if(!html){if(p)p.remove();return}
  if(!p){p=d.createElement('p');p.className='nx-answer';p.dataset.nxP3='';const ref=after||$(':scope > .label',card);ref?ref.after(p):card.prepend(p)}if(p.innerHTML!==html)p.innerHTML=html};
const tiles=box=>$$('.metric',box).map(m=>({label:(($('.label',m)||{}).textContent||'').replace('↗','').trim(),value:(($('.value',m)||{}).textContent||'').trim(),el:m}));
const tile=(box,re)=>(tiles(box).find(t=>re.test(t.label))||{}).value||'';
const unitOf=t=>(String(t).match(/\b([A-Z]{3})\b/)||[])[1]||'';
const short=t=>{const n=num(t),u=unitOf(t);if(n===null)return esc(t);const a=Math.abs(n),f=(v,s)=>(v>=100?v.toFixed(0):v>=10?v.toFixed(1):v.toFixed(2)).replace(/\.0+$/,'')+s;
  const body=a>=1e9?f(a/1e9,'bn'):a>=1e6?f(a/1e6,'m'):a>=1e4?f(a/1e3,'k'):new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(a);return (n<0?'−':'')+body+(u?' '+u:'')};
const later=(fn,ms)=>{let t;return(...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),ms)}};
const click=id=>{const b=$(id);if(b&&!b.disabled)b.click()};

// Wealth projection: live inputs, sliders, target line, sentence and scenarios.
const years=$('#projection-years'),flow=$('#projection-flow'),target=$('#projection-target'),assumed=$('#projection-return');
const runProjection=later(()=>click('#projection-run'),650);
if(years)years.addEventListener('change',()=>click('#projection-run'));
[flow,target,assumed].forEach(i=>i&&i.addEventListener('input',runProjection));
if(years&&!$('.nx-slider',years.parentElement)){const opts=[...years.options],r=d.createElement('input');r.type='range';r.className='nx-slider';r.min='0';r.max=String(opts.length-1);r.step='1';r.value=String(years.selectedIndex);r.setAttribute('aria-label','Projection horizon');
  r.addEventListener('input',()=>{years.selectedIndex=Number(r.value)});r.addEventListener('change',()=>years.dispatchEvent(new Event('change',{bubbles:true})));years.addEventListener('change',()=>{r.value=String(years.selectedIndex)});
  const ends=d.createElement('div');ends.className='nx-slider-ends';ends.innerHTML=`<span>${esc(opts[0].textContent)}</span><span>${esc(opts.at(-1).textContent)}</span>`;years.after(r,ends)}
const flowSlider=()=>{if(!flow||$('.nx-slider',flow.parentElement))return;const start=num((($('#projection-note')||{}).textContent||'').match(/Starting value ([0-9,.]+)/)?.[1]);if(!start)return;
  const raw=start/150,mag=10**Math.floor(Math.log10(raw)),span=Math.ceil(raw/mag)*mag,r=d.createElement('input');r.type='range';r.className='nx-slider';r.min=String(-span);r.max=String(span);r.step=String(span/50);r.value=String(Math.max(-span,Math.min(span,Number(flow.value)||0)));r.setAttribute('aria-label','Monthly contribution');
  r.addEventListener('input',()=>{flow.value=r.value});r.addEventListener('change',()=>click('#projection-run'));flow.addEventListener('input',()=>{const v=Number(flow.value);if(Number.isFinite(v))r.value=String(v)});
  const ends=d.createElement('div');ends.className='nx-slider-ends';const fmt=v=>new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(v);ends.innerHTML=`<span>Withdraw ${fmt(span)}</span><span>Add ${fmt(span)}</span>`;flow.after(r,ends)};
const targetLine=()=>{const c=$('#projection-chart');if(!c||!charts)return;const box=c.parentElement;$$('.nx-target',box).forEach(x=>x.remove());const t=Number(target&&target.value),s=charts.get('#projection-chart');
  if(!(t>0)||!s||!s.series||!s.series.length||typeof chartBounds!=='function')return;const vals=s.lines.flatMap(l=>s.series.map(r=>Number(r[l.key])).filter(Number.isFinite)),b=chartBounds(vals,s.options||{});if(!(b.hi>b.lo)||t<b.lo||t>b.hi)return;
  const h=c.clientHeight-66,y=c.offsetTop+18+(b.hi-t)*h/(b.hi-b.lo),el=d.createElement('div');el.className='nx-target';el.style.left=(c.offsetLeft+100)+'px';el.style.width=(c.clientWidth-122)+'px';el.style.top=y+'px';el.innerHTML=`<span>Target ${esc(short(target.value))}</span>`;box.appendChild(el)};
const SCEN='wr-next-scenarios';
const snapshot=()=>{const m=$('#projection-metrics');return{years:years?years.options[years.selectedIndex].textContent:'',flow:flow&&flow.value?flow.value:'0',target:target&&target.value?target.value:'—',ret:assumed&&assumed.value?assumed.value+'%':'History',
  median:tile(m,/^median/i),weak:tile(m,/^weak/i),strong:tile(m,/^strong/i),chance:tile(m,/target/i)||'—'}};
const projection=()=>{const card=$('#model-projection'),m=$('#projection-metrics');if(!card||!m)return;flowSlider();const key=m.textContent+'|'+(target?target.value:'');if(card.dataset.nxSig===key)return;card.dataset.nxSig=key;
  const med=tile(m,/^median/i),weak=tile(m,/^weak/i),strong=tile(m,/^strong/i),chance=tile(m,/chance of reaching/i),out=tile(m,/running out/i),label=(tiles(m).find(t=>/^median/i.test(t.label))||{}).label||'';
  const when=(label.match(/in (.+)$/)||[])[1]||'the horizon';
  if(!/\d/.test(med))say(card,'');else say(card,`In <b>${esc(when)}</b> the middle outcome is <b>${short(med)}</b>. One run in 20 ends below <b>${short(weak)}</b>, and one in 20 above <b>${short(strong)}</b>.${/\d/.test(chance)?` You reach the target in <b>${esc(chance)}</b> of runs.`:''}${/\d/.test(out)?` The money runs out in <b class="loss">${esc(out)}</b> of runs.`:''}`);
  setTimeout(targetLine,80);
  let bar=$(':scope > .nx-scen',card);if(!bar){bar=d.createElement('div');bar.className='nx-scen';const at=$('#projection-run').closest('.actions');(at||m).after(bar)}
  const saved=store.get(SCEN,{})[account]||{};
  bar.innerHTML='<button type="button" data-nx-save="A">Save as A</button><button type="button" data-nx-save="B">Save as B</button>'+(saved.A||saved.B?'<button type="button" data-nx-save="clear">Clear</button>':'')+
    (saved.A&&saved.B?`<table class="nx-compare"><thead><tr><th></th><th>A</th><th>B</th></tr></thead><tbody>${[['Horizon','years'],['Monthly contribution','flow'],['Target','target'],['Return assumption','ret'],['Middle outcome','median'],['Weak case','weak'],['Strong case','strong'],['Chance of the target','chance']].map(([l,k])=>`<tr><td>${l}</td><td>${['median','weak','strong'].includes(k)?short(saved.A[k]):esc(saved.A[k])}</td><td>${['median','weak','strong'].includes(k)?short(saved.B[k]):esc(saved.B[k])}</td></tr>`).join('')}</tbody></table>`:saved.A||saved.B?`<span class="muted">Saved ${saved.A?'A':'B'}. Change the inputs and save the other to compare.</span>`:'')};
main.addEventListener('click',e=>{const b=e.target.closest('[data-nx-save]');if(!b)return;const all=store.get(SCEN,{}),mine=all[account]||{};if(b.dataset.nxSave==='clear')delete all[account];else{mine[b.dataset.nxSave]=snapshot();all[account]=mine}store.set(SCEN,all);const card=$('#model-projection');if(card)card.dataset.nxSig='';projection()});
if(target)target.addEventListener('input',later(targetLine,200));
if(charts){const set=charts.set.bind(charts);charts.set=(k,v)=>{set(k,v);if(k==='#projection-chart')setTimeout(targetLine,70);return charts};addEventListener('resize',later(targetLine,160))}

// Risk outlook and regimes: one sentence each.
const riskOutlook=()=>{const card=$('#model-risk'),m=$('#model-risk-metrics');if(!card||!m)return;const key=m.textContent;if(card.dataset.nxSig===key)return;card.dataset.nxSig=key;
  const f=tile(m,/^forecast volatility/i),lr=tile(m,/^long-run/i),v=(tiles(m).find(t=>/^one-day var \(95/i.test(t.label))||{}).el,e=tile(m,/^one-day expected tail/i),mv=tile(m,/^one-month var/i);
  if(!/\d/.test(f)){say(card,'');return}const fv=num(f),lv=num(lr),cmp=lv===null?'':Math.abs(fv-lv)<.5?', close to its long-run level':fv>lv?`, above its long-run <b>${esc(lr)}</b>`:`, below its long-run <b>${esc(lr)}</b>`;
  const vp=v?(($('.value',v)||{}).childNodes[0]||{}).textContent:'',vm=v?(($('.value .muted',v)||{}).textContent||''):'';
  say(card,`Over the next month the portfolio is expected to swing about <b>${esc(f)}</b> a year${cmp}. On 1 day in 20 it could lose <b class="loss">${esc(vp)}</b>${vm?` (about ${short(vm)})`:''} or more${/\d/.test(mv)?`, and in 1 month in 20 <b class="loss">${esc(((tiles(m).find(t=>/^one-month var/i.test(t.label))||{}).el?.querySelector('.value')?.childNodes[0]?.textContent)||mv)}</b> or more`:''}.`)};
const regimes=()=>{const card=$('#model-regimes'),m=$('#regime-metrics');if(!card||!m)return;const key=m.textContent;if(card.dataset.nxSig===key)return;card.dataset.nxSig=key;
  const t=tiles(m),now=t.find(x=>/^regime now/i.test(x.label)),soon=t.find(x=>/in a month/i.test(x.label)),loss=t.find(x=>/loss over/i.test(x.label));if(!now||!now.value){say(card,'');return}
  const p=(now.el.getAttribute('title')||'').match(/[\d.]+%/);
  say(card,`The portfolio is in its <b>${esc(now.value.toLowerCase())}</b> regime now${p?` (${esc(p[0])} likely)`:''}.${soon?` The ${esc(soon.label.replace(/^chance of /i,'chance of being ').replace(/ in a month$/i,''))} in a month is <b>${esc(soon.value)}</b>.`:''}${loss?` The chance of a loss over 3 months is <b>${esc(loss.value)}</b>.`:''}`)};

// What-if: sliders, mix bars, rerun on release, sentence.
const runWhatif=later(()=>click('#whatif-run'),500);
const whatif=()=>{const body=$('#whatif-holdings'),card=$('#model-whatif');if(!body||!card)return;
  $$('input[data-conid]',body).forEach(input=>{if(input.dataset.nxSlid!==undefined)return;input.dataset.nxSlid='';const cur=num((input.closest('tr').children[1]||{}).textContent)||0,max=Math.max(20,Math.ceil(cur*2.5/5)*5);
    const r=d.createElement('input');r.type='range';r.className='nx-slider';r.min='0';r.max=String(max);r.step='0.1';r.value=String(Math.min(max,Number(input.value)||0));r.setAttribute('aria-label',(input.getAttribute('aria-label')||'Weight')+' slider');
    r.addEventListener('input',()=>{input.value=Number(r.value).toFixed(2);input.dispatchEvent(new Event('input',{bubbles:true}))});r.addEventListener('change',runWhatif);
    input.addEventListener('input',()=>{const v=Number(input.value);if(Number.isFinite(v))r.value=String(v);mix()});input.addEventListener('change',runWhatif);input.before(r)});
  mix();
  const res=$('#whatif-results'),key=res?res.textContent:'';if(card.dataset.nxSig===key)return;card.dataset.nxSig=key;if(!res)return;
  const row=re=>{const tr=$$('tr',res).find(x=>re.test((x.children[0]||{}).textContent||''));return tr?[...tr.children].map(c=>c.textContent.trim()):null};
  const vol=row(/^volatility/i),dd=row(/^maximum drawdown/i),ret=row(/^annual return/i);if(!vol){say(card,'');return}
  const same=[vol,dd,ret].every(r=>!r||r[1]===r[2]);
  say(card,same?'The proposed mix is the same as today’s. Drag a weight, and the comparison updates when you let go.':`The proposed mix would move volatility from <b>${esc(vol[1])}</b> to <b>${esc(vol[2])}</b>${dd?`, the worst fall from <b>${esc(dd[1])}</b> to <b>${esc(dd[2])}</b>`:''}${ret?`, and the past year's return from <b>${esc(ret[1])}</b> to <b>${esc(ret[2])}</b>`:''}.`,$('#whatif-total'))};
const mix=()=>{const body=$('#whatif-holdings'),total=$('#whatif-total');if(!body||!total)return;const rows=$$('tr',body).map(tr=>{const i=$('input[data-conid]',tr);const name=(($('strong',tr)||{}).textContent||'').trim();return{name,now:(num((tr.children[1]||{}).textContent)||0),next:i?Math.max(0,Number(i.value)||0):null}}).filter(r=>r.next!==null);
  if(!rows.length)return;let box=$('#model-whatif > .nx-mix');if(!box){box=d.createElement('div');box.className='nx-mix';total.before(box)}
  const bar=(k)=>{const sum=rows.reduce((t,r)=>t+r[k],0),scale=Math.max(100,sum);return rows.map(r=>`<i style="width:${(100*r[k]/scale).toFixed(2)}%" title="${esc(r.name)} ${r[k].toFixed(1)}%"></i>`).join('')+(sum<100?`<i class="cash" style="width:${(100-sum).toFixed(2)}%" title="Cash ${(100-sum).toFixed(1)}%"></i>`:'')};
  box.innerHTML=`<div class="nx-mix-row"><span>Now</span><span class="nx-mix-bar" role="img" aria-label="Current mix">${bar('now')}</span></div><div class="nx-mix-row"><span>Proposed</span><span class="nx-mix-bar" role="img" aria-label="Proposed mix">${bar('next')}</span></div>`};

// Optimisation: rerun on cap change, sentence, frontier points select rows.
const cap=$('#optimise-cap');if(cap)cap.addEventListener('input',later(()=>click('#optimise-run'),700));
const optimise=()=>{const body=$('#optimise-portfolios'),card=$('#model-optimise');if(!body||!card)return;const key=body.textContent;if(card.dataset.nxSig===key)return;card.dataset.nxSig=key;
  const rows=$$('tr',body).map(tr=>({tr,name:(tr.children[0]||{}).textContent.replace('●','').trim(),ret:(tr.children[1]||{}).textContent.trim(),vol:(tr.children[2]||{}).textContent.trim(),sharpe:(tr.children[3]||{}).textContent.trim()}));
  const cur=rows.find(r=>/current/i.test(r.name)),ms=rows.find(r=>/sharpe/i.test(r.name)),mv=rows.find(r=>/variance/i.test(r.name));if(!cur||!ms){say(card,'');return}
  say(card,`The best return per unit of risk (maximum Sharpe) aims for <b>${esc(ms.ret)}</b> at <b>${esc(ms.vol)}</b> volatility, against <b>${esc(cur.ret)}</b> at <b>${esc(cur.vol)}</b> for today's mix.${mv?` The lowest-risk mix brings volatility down to <b>${esc(mv.vol)}</b>.`:''} Select a point on the chart to find its row.`)};
main.addEventListener('click',e=>{const c=e.target.closest('#optimise-frontier circle');if(!c)return;const name=(($('title',c)||{}).textContent||'').split(':')[0].trim();const tr=$$('#optimise-portfolios tr').find(r=>(r.children[0]||{}).textContent.includes(name));$$('#optimise-portfolios tr.nx-pick').forEach(r=>r.classList.remove('nx-pick'));if(tr){tr.classList.add('nx-pick');tr.scrollIntoView({block:'nearest',behavior:'smooth'})}});

// Factor model: rerun on region change, diverging bars, share explained, sentence.
const region=$('#factor-region');if(region)region.addEventListener('change',()=>click('#factor-run'));
const factors=()=>{const body=$('#factor-exposures'),m=$('#factor-metrics'),card=$('#model-factors');if(!body||!m||!card)return;const key=body.textContent+'|'+m.textContent;if(body.dataset.nxSig===key)return;
  const rows=$$('tr',body).map(tr=>({tr,label:(($('strong',tr)||tr.children[0]||{}).textContent||'').trim(),beta:num((tr.children[1]||{}).textContent),sig:!/not significant/i.test(tr.textContent)})).filter(r=>r.beta!==null);
  const max=Math.max(1,...rows.map(r=>Math.abs(r.beta)));
  rows.forEach(r=>{const td=r.tr.children[1];let b=$(':scope > .nx-div',td);if(!b){b=d.createElement('span');b.className='nx-div';b.setAttribute('aria-hidden','true');td.appendChild(b)}const w=50*Math.abs(r.beta)/max;b.innerHTML=`<i class="${r.beta<0?'neg':''}" style="left:${r.beta<0?50-w:50}%;width:${w.toFixed(1)}%"></i>`});
  const r2=num(tile(m,/r²|explained/i));let share=$(':scope > .nx-share',card);
  if(r2!==null){if(!share){share=d.createElement('div');share.className='nx-share';m.after(share)}share.innerHTML=`<div class="nx-share-bar" role="img" aria-label="${r2}% explained by factors"><i style="width:${Math.max(0,Math.min(100,r2))}%"></i></div><div class="nx-share-ends"><span>Explained by the factors ${esc(tile(m,/r²|explained/i))}</span><span>Unexplained ${(100-r2).toFixed(2)}%</span></div>`}else if(share)share.remove();
  const mkt=rows.find(r=>/market/i.test(r.label)),lean=rows.filter(r=>r!==mkt&&r.sig).sort((a,b)=>Math.abs(b.beta)-Math.abs(a.beta)).slice(0,2),alpha=tiles(m).find(t=>/^alpha/i.test(t.label));
  const f=n=>n.toFixed(2).replace('-','−');
  say(card,rows.length?`${mkt?`The market drives most of the returns (beta <b>${f(mkt.beta)}</b>${mkt.beta>1.05?', a little more than the market itself':mkt.beta<.95?', a little less than the market itself':''}).`:''}${lean.length?` Beyond that it leans ${lean.map(r=>`${r.beta>0?'toward':'away from'} <b>${esc(r.label.toLowerCase())}</b> (${f(r.beta)})`).join(' and ')}.`:' No other factor matters much.'}${alpha&&/\d/.test(alpha.value)?` Unexplained return (alpha) is <b>${esc(alpha.value)}</b> a year${/t-statistic\s+([\d.−-]+)/.test(alpha.el.getAttribute('title')||'')&&Math.abs(num((alpha.el.getAttribute('title')||'').match(/t-statistic\s+([\d.−-]+)/)[1]))<2?', which is not statistically reliable':''}.`:''}`:'');
  body.dataset.nxSig=body.textContent+'|'+m.textContent};

// Stress test: scenario cards that keep their results.
const scen=$('#stress-scenario'),results={};let lastAsof='';
const stress=()=>{const card=$('#model-factors'),m=$('#stress-metrics');if(!scen||!m||!card)return;const asof=(($('#asof-select')||{}).value)||'';if(asof!==lastAsof){lastAsof=asof;Object.keys(results).forEach(k=>delete results[k])}
  const first=tiles(m)[0];if(first&&/^portfolio move/i.test(first.label)&&/\d/.test(first.value))results[scen.value]={move:first.value,money:tile(m,/^in money/i)};
  let grid=$('.nx-cards',card);if(!grid){grid=d.createElement('div');grid.className='nx-cards';grid.setAttribute('role','group');grid.setAttribute('aria-label','Stress scenarios');scen.closest('.filter-grid').before(grid)}
  const html=[...scen.options].map(o=>{const r=results[o.value],n=r?num(r.move):null;return `<button type="button" class="nx-card" data-nx-scenario="${esc(o.value)}" aria-pressed="${o.value===scen.value}"><span>${esc(o.textContent)}</span>${r?`<b class="${tone(n)}">${esc(r.move)}</b><small>${/\d/.test(r.money)?esc(short(r.money)):''}</small>`:`<small>${o.value==='custom'?'Set your own shocks':'Select to see the impact'}</small>`}</button>`}).join('');
  if(grid.innerHTML!==html)grid.innerHTML=html;
  const r=results[scen.value];const label=scen.options[scen.selectedIndex]?scen.options[scen.selectedIndex].textContent:'';
  let p=$('#nx-stress-say');if(r){if(!p){p=d.createElement('p');p.id='nx-stress-say';p.className='nx-answer';grid.after(p)}const html2=`If this happened (<b>${esc(label.toLowerCase())}</b>), the portfolio would move about <b class="${tone(num(r.move))}">${esc(r.move)}</b>${/\d/.test(r.money)?` (${short(r.money)})`:''}, judging by how it has responded to these factors.`;if(p.innerHTML!==html2)p.innerHTML=html2}else if(p)p.remove()};
main.addEventListener('click',e=>{const b=e.target.closest('[data-nx-scenario]');if(!b||!scen)return;scen.value=b.dataset.nxScenario;scen.dispatchEvent(new Event('change',{bubbles:true}));stress()});
if(scen)scen.addEventListener('change',()=>{if(scen.value!=='custom')click('#stress-run')});

// The factor card holds two subpages; what this adds follows its neighbour in or out of view.
const parts=()=>$$('#model-factors > .nx-answer[data-nx-p3], #model-factors > .nx-share, #model-factors > .nx-cards, #model-factors > #nx-stress-say').forEach(el=>{const prev=el.previousElementSibling;if(prev)el.classList.toggle('part-off',prev.classList.contains('part-off'))});
const run=()=>{projection();riskOutlook();regimes();whatif();optimise();factors();stress();parts()};
let pending=false;const tick=()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;obs.disconnect();try{run()}finally{obs.observe(main,{childList:true,subtree:true,characterData:true})}})};
const obs=new MutationObserver(tick);run();obs.observe(main,{childList:true,subtree:true,characterData:true});
})();
"""
