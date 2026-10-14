"""Website side of the month-end agent team: the Agent review panel, the Month-end page and its inbox cards.

Off unless MONTH_TEAM_UI is "owner", and then for owners only: the team's
cards ask Ying to decide, so nobody else sees them. The panel sits on the
account's Reports tab above the existing release checklist. Once the team is
live, Release asks for confirmation while the team has an open objection to
that month; it never blocks.

Batch release on the Month-end page is Ying's browser calling the Reports
tab's own release-preview and release endpoints, one account at a time, for
reports the team passed with no open objection. Every server-side check
still runs, and the team itself still has no route to a release. Everything is read through the agent service, exactly as
chat is, with the website's managed identity naming the signed-in person.

Installed by ``agent_web.install``; no other website code changes.
"""
from __future__ import annotations

import os
import re
from html import escape
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

MONTH_TEAM_KINDS = frozenset({"month_end_report", "month_end_hold", "month_end_dispute"})
PANEL_PATHS = re.compile(r"^/accounts/[^/]+/reports$")
PERIOD = r"^\d{4}-\d{2}-\d{2}$"
MODE = r"^(month|replay)$"


def enabled() -> bool:
    return os.environ.get("MONTH_TEAM_UI", "off").strip().lower() == "owner"


def is_month_owner(user: dict[str, Any]) -> bool:
    return enabled() and user.get("role") == "owner" and bool(str(user.get("object_id") or ""))


def install(app: FastAPI, authorized_user: Callable[..., dict[str, Any]],
            allowed_accounts: Callable[[dict[str, Any]], list[str]], theme_css: str, api: Any) -> None:
    def month_user(user: dict[str, Any] = Depends(authorized_user)) -> dict[str, Any]:
        if not is_month_owner(user):
            raise HTTPException(status_code=404, detail="Not found")
        return user

    def oid(user: dict[str, Any]) -> str:
        return str(user["object_id"]).lower()

    @app.get("/api/agents/month-end/status")
    def month_status(user: dict[str, Any] = Depends(authorized_user)) -> dict[str, Any]:
        return {"enabled": is_month_owner(user)}

    @app.get("/api/agents/month-end/runs")
    def month_runs(period_end: str | None = Query(default=None, pattern=PERIOD),
                   mode: str = Query(default="month", pattern=MODE),
                   user: dict[str, Any] = Depends(month_user)) -> Any:
        params = {"user_object_id": oid(user), "mode": mode}
        if period_end:
            params["period_end"] = period_end
        return api.request("GET", "/v1/month-end/runs", params=params)

    @app.get("/api/agents/month-end/runs/{account_id}/{period_end}")
    def month_run(account_id: str, period_end: str, mode: str = Query(default="month", pattern=MODE),
                  user: dict[str, Any] = Depends(month_user)) -> Any:
        if not re.match(PERIOD, period_end):
            raise HTTPException(status_code=400, detail="period_end must be YYYY-MM-DD.")
        if account_id not in allowed_accounts(user):
            raise HTTPException(status_code=403, detail="You do not have access to that account.")
        return api.request("GET", f"/v1/month-end/runs/{account_id}/{period_end}",
                           params={"user_object_id": oid(user), "mode": mode})

    @app.get("/agents/month-team.js")
    def month_panel() -> Response:
        return Response(PANEL_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.get("/month-end", response_class=HTMLResponse)
    def month_page(user: dict[str, Any] = Depends(month_user)) -> str:
        return MONTH_PAGE.replace("{THEME}", theme_css).replace("{VIEWER}", escape(str(user.get("email") or "")))


_SHARED_JS = r"""
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const STAGES={data:'Checking data',held:'Waiting for your decision',words:'Writing commentary',package:'Waiting for the package',checking:'Checking files',ready:'Checked'};
const VERDICT={clean:'Clean',fixable:'Waiting for the next sync',blocked:'Blocked'};
const COMMENTARY={agreed:'Agreed',disputed:'Writer and reviewer disagree',writer_upheld:"You kept the writer's text",reviewer_upheld:'You agreed with the reviewer'};
const stage=r=>r.attempts>=3?'Stopped after repeated failures':(STAGES[r.stage]||r.stage);
"""

PANEL_JS = r"""(()=>{
'use strict';
const m=location.pathname.match(/^\/accounts\/([^/]+)\/reports$/);if(!m)return;
const account=decodeURIComponent(m[1]);
""" + _SHARED_JS + r"""
const TARGET={commentary:'Commentary',factsheet:'Factsheet',presentation:'Presentation',data:'Data'};
const AGENT={coordinator:'Coordinator',data:'Data',analyst:'Analyst',writer:'Writer',reviewer:'Reviewer',visual_qa:'Visual QA'};
const css=`.mt-panel{margin:22px 0 4px;padding:16px;border:1px solid var(--border,#dce2eb);border-radius:14px}
.mt-panel h3{margin:0 0 6px;display:flex;gap:8px;align-items:center}.mt-chip{font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;background:var(--surface,#eef1f5)}
.mt-grid{display:grid;grid-template-columns:minmax(120px,max-content) 1fr;gap:4px 14px;margin:12px 0;font-size:14px}.mt-grid dt{opacity:.7}.mt-grid dd{margin:0}
.mt-obj{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-top:1px solid var(--border,#dce2eb);font-size:14px}.mt-obj.settled{opacity:.6}
.mt-page{position:relative;flex:none;width:42px;height:59px;border:1px solid var(--border,#b8c2cf);border-radius:3px;background:var(--card,#fff)}
.mt-page span{position:absolute;border:2px solid #d64550;background:rgba(214,69,80,.15)}
.mt-fig{display:inline-block;font-size:11px;padding:0 6px;margin:0 2px;border-radius:999px;background:var(--surface,#eef1f5);cursor:help}
.mt-panel details{margin-top:10px}.mt-panel summary{cursor:pointer;font-weight:600}.mt-panel table{border-collapse:collapse;font-size:13px;width:100%}
.mt-panel td,.mt-panel th{padding:4px 6px;border-bottom:1px solid var(--border,#dce2eb);text-align:left;vertical-align:top}
.mt-warn{padding:8px 12px;border-radius:10px;background:rgba(232,131,58,.14);margin:8px 0}`;
fetch('/api/agents/month-end/status',{credentials:'same-origin'}).then(r=>r.ok?r.json():{enabled:false}).then(s=>{if(s.enabled)init()}).catch(()=>{});
function init(){
 const anchor=document.querySelector('#report-owner-review'),select=document.querySelector('#report-month');if(!anchor||!select)return;
 const style=document.createElement('style');style.textContent=css;document.head.appendChild(style);
 const panel=document.createElement('section');panel.className='mt-panel';panel.setAttribute('aria-label','Agent review');panel.hidden=true;
 anchor.parentNode.insertBefore(panel,anchor);
 let request=0,shown='',current=null;
 const all='<a href="/month-end">All accounts</a>';
 const head=run=>!run?`<h3>Agent review</h3>`:run.live
  ?`<h3>Agent review</h3><p class="muted">The month-end team checked this month's files and wrote its commentary. Release below as usual; it asks you to confirm while an objection is open. ${all}</p>`
  :`<h3>Agent review <span class="mt-chip">Shadow</span></h3><p class="muted">The month-end team checked this month beside today's process. Nothing here changes the files or the release checklist below. ${all}</p>`;
 async function load(){const period=select.value;if(!period||period===shown)return;shown=period;current=null;const n=++request;panel.hidden=false;panel.innerHTML=head()+'<p class="muted">Loading…</p>';
  try{const r=await fetch(`/api/agents/month-end/runs/${encodeURIComponent(account)}/${encodeURIComponent(period)}`,{credentials:'same-origin'});if(n!==request)return;
   if(r.status===404){panel.innerHTML=head()+`<p class="muted">The team has not checked ${esc(period)} yet. ${all}</p>`;return}
   if(!r.ok)throw new Error();const run=await r.json();if(n!==request)return;current=run;panel.innerHTML=head(run)+render(run)}catch(e){if(n===request)panel.innerHTML=head()+'<p class="muted">The agent review could not be loaded.</p>'}}
 select.addEventListener('change',load);new MutationObserver(load).observe(select,{childList:true});load();
 // Release warns, never blocks: runs before the page's own handler and stops it only if Ying cancels.
 document.addEventListener('click',e=>{
  if(!e.target.closest||!e.target.closest('#report-release')||!current||!current.live||current.period_end!==select.value)return;
  const open=(current.objections||[]).filter(o=>o.status==='open').length;
  const adds=[...document.querySelectorAll('#report-release-list input[data-report-type]')].some(i=>i.checked&&!i.defaultChecked);
  if(open&&adds&&!confirm(`The month-end team has ${open} open objection${open===1?'':'s'} to ${current.period_end}. Release anyway?`)){e.preventDefault();e.stopImmediatePropagation()}
 },true);
}
function fileLink(run,target){const f=((run.package||{}).files||[]).find(x=>x.report_type===target);return f?`/api/accounts/${encodeURIComponent(account)}/reporting/review-artifacts/${encodeURIComponent(f.generation_run_id)}/download`:''}
function render(run){
 const v=run.verdicts||{},figs=(run.figures||{}).figures||[],sources=(run.figures||{}).sources||[],byId=Object.fromEntries(figs.map(f=>[f.id,f]));
 const src=f=>{const s=sources[f.source]||{};return `${(s.tool||'').replace(/_/g,' ')}${s.as_of?' as of '+s.as_of:''}`};
 let h='';
 if(run.attempts>=3)h+=`<div class="mt-warn">Stopped at “${esc(STAGES[run.stage]||run.stage)}” after repeated failures (${esc(run.last_error||'unknown')}). It resumes when retried.</div>`;
 h+=`<dl class="mt-grid"><dt>Stage</dt><dd>${esc(stage(run))}</dd><dt>Data</dt><dd>${esc(VERDICT[run.data_verdict]||'Not checked')}</dd>`+
  `<dt>Figures</dt><dd>${figs.length?figs.length+' cited figures':'Not built'}</dd><dt>Commentary</dt><dd>${esc(COMMENTARY[run.commentary_status]||(v.deck_has_commentary===false?'None: this deck has no commentary line':'Not drafted'))}${run.commentary_rounds?' · '+run.commentary_rounds+' round(s)':''}</dd>`+
  `<dt>Printed figures</dt><dd>${esc(v.reviewer_summary||'Not checked yet')}</dd><dt>Pages</dt><dd>${esc(v.visual_qa_summary||'Not checked yet')}</dd></dl>`;
 const obj=(run.objections||[]).slice().sort((a,b)=>(a.status==='open'?0:1)-(b.status==='open'?0:1));
 if(obj.length){h+=`<h4>Objections (${obj.filter(o=>o.status==='open').length} open)</h4>`+obj.map(o=>{const r=o.region,link=o.target==='factsheet'||o.target==='presentation'?fileLink(run,o.target):'';
  return `<div class="mt-obj${o.status==='open'?'':' settled'}">${r?`<div class="mt-page" title="Where on page ${esc(o.page)}"><span style="left:${r.x*100}%;top:${r.y*100}%;width:${Math.max(r.w*100,4)}%;height:${Math.max(r.h*100,3)}%"></span></div>`:''}<div><strong>${esc(TARGET[o.target]||o.target)}${o.page?', page '+esc(o.page):''}</strong> · ${esc(AGENT[o.raised_by]||o.raised_by)}<br>${esc(o.message)}${o.status!=='open'?`<br><small>${esc(o.status==='overruled'?'Overruled':'Settled')}: ${esc(o.resolution||'')}</small>`:''}${link?` <a href="${esc(link)}">Review file</a>`:''}</div></div>`}).join('')}
 else if(v.reviewer)h+='<p>No objections.</p>';
 const c=run.commentary;
 if(c&&c.sentences){const chips=s=>(s.figures||[]).map(id=>byId[id]?`<span class="mt-fig" title="${esc(byId[id].label+' · from '+src(byId[id]))}">${esc(id)} ${esc(byId[id].display)}</span>`:'').join('');
  const group=(name,title)=>{const items=c.sentences.filter(s=>s.field===name);return items.length?`<p><strong>${title}</strong></p><ul>${items.map(s=>`<li>${esc(s.text)} ${chips(s)}</li>`).join('')}</ul>`:''};
  const summary=c.sentences.find(s=>s.field==='executive_summary');
  const used=!run.live?' (not used during shadow)':v.commentary_in_report===true?' (in the deck)':v.commentary_in_report===false?' (not in the deck)':'';
  h+=`<details><summary>The team's commentary${used}</summary>${summary?`<p>${esc(summary.text)} ${chips(summary)}</p>`:''}${group('performance_observations','Performance')}${group('risk_observations','Risk')}</details>`}
 if(figs.length)h+=`<details><summary>Figures and sources</summary><table><thead><tr><th>Id</th><th>Figure</th><th>Value</th><th>Source</th></tr></thead><tbody>${figs.map(f=>`<tr><td>${esc(f.id)}</td><td>${esc(f.label)}</td><td>${esc(f.display)}</td><td>${esc(src(f))}</td></tr>`).join('')}</tbody></table>${((run.figures||{}).unavailable||[]).length?`<p class="muted">Unavailable: ${esc(run.figures.unavailable.join(', '))}</p>`:''}</details>`;
 const steps=run.steps||[];
 if(steps.length)h+=`<details><summary>Every step (${steps.length})</summary><table><tbody>${steps.map(s=>`<tr><td>${esc(String(s.created_at||'').slice(0,16).replace('T',' '))}</td><td>${esc(AGENT[s.agent_name]||s.agent_name)}${s.round>1?' · round '+esc(s.round):''}</td><td>${esc(s.outcome)}</td><td>${esc(s.summary)}</td></tr>`).join('')}</tbody></table></details>`;
 return h}
})();"""

MONTH_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Month-end</title><style>{THEME}
.mt-table{border-collapse:collapse;width:100%;font-size:14px}.mt-table th,.mt-table td{padding:8px;border-bottom:1px solid var(--border,#dce2eb);text-align:left;vertical-align:top}
.mt-wrap{overflow-x:auto}.mt-log:empty{display:none}.mt-log{margin:12px 0;padding:10px 14px;border:1px solid var(--border,#dce2eb);border-radius:10px;font-size:14px}.mt-need{font-weight:600}.mt-banner{padding:10px 14px;border-radius:10px;background:rgba(232,131,58,.14);margin:12px 0}
</style></head><body><header><strong class="brand-title">Month-end</strong><div class="session">{VIEWER} · <a href="/agent-inbox">Agent inbox</a> · <a href="/">Accounts</a></div></header>
<main><h1>Month-end</h1><p class="muted">Every account the month-end team is checking, by stage. The team never releases anything: release each report on its Reports tab as usual.</p>
<div class="actions"><label>Run <select id="mode"><option value="month">This month</option><option value="replay">Replay of released months</option></select></label>
<label>Month <select id="period"></select></label><label>Show <select id="filter"><option value="">All accounts</option><option value="need">Needs you</option><option value="clean">Clean, not yet released</option></select></label>
<button id="batch" class="button" type="button" hidden>Release clean reports</button></div>
<div id="banner"></div><div id="batch-log" class="mt-log" role="status" aria-live="polite"></div><section class="card wide"><div id="summary" class="muted">Loading…</div><div class="mt-wrap"><table class="mt-table"><thead><tr><th>Account</th><th>Stage</th><th>Data</th><th>Commentary</th><th>Files</th><th>Open objections</th><th>Updated</th></tr></thead><tbody id="rows"></tbody></table></div></section></main>
<script>
""" + _SHARED_JS + r"""
const needs=r=>r.attempts>=3||r.stage==='held'||r.commentary_status==='disputed'||r.open_objections>0;
const RELEASED=['released_internal','published'],done=new Set();
// Clean: live, checked, nothing open, every check passed, the team's commentary in the deck (unless the deck has no
// commentary line), a file not yet released.
const clean=r=>{const v=r.verdicts||{},files=(r.package||{}).files||[];return r.mode==='month'&&r.live&&r.stage==='ready'&&r.attempts<3&&!r.open_objections
 &&r.data_verdict==='clean'&&v.reviewer==='pass'&&v.visual_qa==='pass'
 &&(v.deck_has_commentary===false||(['agreed','writer_upheld'].includes(r.commentary_status)&&v.commentary_in_report===true))&&files.some(f=>!RELEASED.includes(f.status))&&!done.has(r.subject_id+'|'+r.period_end)};
const fingerprint=list=>list.map(f=>f.report_type+':'+String(f.sha256||f.output_sha256||'').toLowerCase()).sort().join(',');
// One account through the Reports tab's own endpoints, released only if its files are the ones the team checked.
async function releaseOne(r){
 const base=`/api/accounts/${encodeURIComponent(r.subject_id)}/reporting/monthly/${encodeURIComponent(r.period_end)}`;
 const p=await fetch(base+'/release-preview',{credentials:'same-origin'}),preview=await p.json().catch(()=>({}));
 if(!p.ok)return 'not released: '+String(preview.detail||'HTTP '+p.status).slice(0,160);
 const current=(preview.artifacts||[]).filter(a=>!a.superseded);
 if(preview.snapshot_id!==(r.package||{}).snapshot_id||fingerprint(current)!==fingerprint((r.package||{}).files||[]))return 'not released: the files changed after the team checked them';
 if(current.length&&current.every(a=>a.approved)){done.add(r.subject_id+'|'+r.period_end);return 'already released'}
 if(!preview.release_eligible)return 'not released: '+String(preview.release_blocker||'not eligible').slice(0,160);
 const q=await fetch(base+'/release',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({snapshot_id:preview.snapshot_id,package_sha256:preview.package_sha256,selection_version:preview.selection_version,selected_report_types:[...new Set(current.map(a=>a.report_type))]})});
 const result=await q.json().catch(()=>({}));if(!q.ok)return 'not released: '+String(result.detail||'HTTP '+q.status).slice(0,160);
 done.add(r.subject_id+'|'+r.period_end);return result.status==='unchanged'?'already released':'released'}
async function batch(){const list=all.filter(clean);if(!list.length)return;
 if(!confirm(`Release ${list.length} report${list.length===1?'':'s'} for ${list[0].period_end}? The team passed each with no open objection, and each goes through the same checks as the Release button on its Reports tab.`))return;
 const button=document.querySelector('#batch'),log=document.querySelector('#batch-log');button.disabled=true;const lines=[];
 for(const r of list){lines.push(`${esc(r.subject_id)}: releasing…`);log.innerHTML=lines.join('<br>');
  let outcome;try{outcome=await releaseOne(r)}catch(e){outcome='not released: the request failed'}
  lines[lines.length-1]=`${esc(r.subject_id)}: ${esc(outcome)}`;log.innerHTML=lines.join('<br>')}
 const released=lines.filter(l=>l.endsWith(': released')).length;lines.unshift(`<strong>${released} of ${list.length} released.</strong>`);log.innerHTML=lines.join('<br>');button.disabled=false;render()}
const files=r=>{const v=r.verdicts||{};if(!v.reviewer)return '—';const word={pass:'Passed',objection:'Objections',skipped:'Not looked at',failed:'Failed'};return `Figures: ${word[v.reviewer]||v.reviewer} · Pages: ${word[v.visual_qa]||v.visual_qa}`};
let all=[];
async function load(period){const mode=document.querySelector('#mode').value;const q=new URLSearchParams({mode});if(period)q.set('period_end',period);
 const r=await fetch('/api/agents/month-end/runs?'+q,{credentials:'same-origin'});if(!r.ok){document.querySelector('#summary').textContent='The month-end runs could not be loaded.';return}
 const data=await r.json();all=data.runs;const job=(data.team||{}).latest_job;const banner=document.querySelector('#banner');
 banner.innerHTML=(job&&job.status==='failed'?`<div class="mt-banner">${job.error_code==='budget_exhausted'?"Paused: the team's monthly token budget is used up. No check was skipped; it resumes when budget is added.":'The last team run failed ('+esc(job.error_code||'unknown')+'). The next scheduled run will try again.'}</div>`:'')
  +(all.some(r=>r.mode==='month'&&!r.live)?`<div class="mt-banner">Shadow: the team's commentary is shown on each Reports tab but not used in the decks.</div>`:'');
 if(!period){const periods=[...new Set(all.map(x=>x.period_end))];const sel=document.querySelector('#period');sel.innerHTML=periods.map(p=>`<option>${esc(p)}</option>`).join('');if(periods.length)return load(periods[0])}
 render()}
function render(){const f=document.querySelector('#filter').value,rows=all.filter(r=>!f||(f==='need'?needs(r):clean(r)));const n=all.filter(needs).length,c=all.filter(clean).length;
 const button=document.querySelector('#batch');button.hidden=!c;button.textContent=`Release ${c} clean report${c===1?'':'s'}`;
 document.querySelector('#summary').textContent=all.length?`${all.length} account(s); ${n} need you.`:'The team has not run for this month yet.';
 document.querySelector('#rows').innerHTML=rows.map(r=>`<tr class="${needs(r)?'mt-need':''}"><td><a href="/accounts/${encodeURIComponent(r.subject_id)}/reports">${esc(r.subject_id)}</a></td><td>${esc(stage(r))}</td><td>${esc(VERDICT[r.data_verdict]||'—')}</td><td>${esc(COMMENTARY[r.commentary_status]||'—')}</td><td>${esc(files(r))}</td><td>${r.open_objections}</td><td>${esc(String(r.updated_at||'').slice(0,16).replace('T',' '))}</td></tr>`).join('')}
document.querySelector('#batch').addEventListener('click',batch);document.querySelector('#mode').addEventListener('change',()=>load());document.querySelector('#period').addEventListener('change',e=>load(e.target.value));document.querySelector('#filter').addEventListener('change',render);load();
</script></body></html>"""
