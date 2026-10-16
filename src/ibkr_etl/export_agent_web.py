"""Website routes and panel for the export agent, on each account's Exports tab.

1. The person describes the export in words. The agent service turns that into
   a plan; the website validates the plan against the account and shows it in
   plain sentences written by code.
2. "Build and check" builds the files from the Exports tab's own reads, runs
   the fixed checks and asks the checker agent for a cover note. The result is
   saved by the agent service as the export's check record.
3. "Download" rebuilds the files and serves them only if they match the
   checked fingerprint byte for byte. A failed export can't be downloaded; one
   that needs review is downloaded only after the person releases it.

Saved export packs re-run a plan every month, quarter or year: the
export_packs job leaves an inbox card, and the card opens this panel.
Shown wherever the agent UI is on (AGENT_UI_MODE); see agent_web.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .export_agent import (MAX_REQUEST_CHARS, PlanError, build, check_facts, dated_plan, describe, fallback_note,
                           fingerprint, overall, package, preview, run_checks, validate_plan)

PANEL_PATHS = re.compile(r"^/accounts/[^/]+/exports$")
CHECK_TIMEOUT_SECONDS = 150.0
PLAN_TIMEOUT_SECONDS = 90.0


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=64)
    request: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)


class BuildBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=64)
    plan: dict[str, Any] | None = None
    pack_item_id: int | None = Field(default=None, ge=1)


class PackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    schedule: str = Field(pattern="^(monthly|quarterly|yearly)$")
    plan: dict[str, Any]


def _filename(account_id: str, title: str, created: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:40] or "export"
    return f"{re.sub(r'[^A-Za-z0-9_-]', '', account_id)}_{slug}_{created[:10]}.zip"


def install(app: FastAPI, agent_user: Callable[..., dict[str, Any]],
            allowed_accounts: Callable[[dict[str, Any]], list[str]], api: Any,
            export_source: Callable[[dict[str, Any], str], Any]) -> None:
    """``export_source(user, account_id)`` checks the person's access and returns a PlatformExportSource."""

    def oid(user: dict[str, Any]) -> str:
        return str(user["object_id"]).lower()

    def source_for(user: dict[str, Any], account_id: str) -> Any:
        if account_id not in allowed_accounts(user):
            raise HTTPException(status_code=403, detail="You do not have access to that account.")
        return export_source(user, account_id)

    def today() -> str:
        return date.today().isoformat()

    @app.post("/api/agents/exports/plan")
    def export_plan(body: PlanBody, user: dict[str, Any] = Depends(agent_user)) -> dict[str, Any]:
        source = source_for(user, body.account_id)
        facts = source.facts(today())
        result = api.request("POST", "/v1/exports/plan", timeout=PLAN_TIMEOUT_SECONDS,
                             json={"user_object_id": oid(user), "account_id": body.account_id,
                                   "request": body.request.strip(), "facts": facts})
        try:
            plan, adjustments = validate_plan(result.get("plan") or {}, facts)
        except PlanError as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "plan": plan, "adjustments": adjustments,
                "lines": [describe(d, source.subject_type) for d in plan["datasets"]]}

    def pack_plan(user: dict[str, Any], account_id: str, item_id: int, facts: dict[str, Any]
                  ) -> tuple[dict[str, Any], list[str]]:
        items = api.request("GET", "/v1/inbox", params={"user_object_id": oid(user)})["items"]
        item = next((x for x in items if x.get("item_id") == item_id and x.get("kind") == "export_pack"
                     and x.get("subject_id") == account_id), None)
        card = next((c for c in (item or {}).get("citations") or [] if isinstance(c, dict) and c.get("export_pack")), None)
        if card is None:
            raise HTTPException(status_code=404, detail="That export pack card was not found.")
        pack = api.request("GET", f"/v1/exports/packs/{card['export_pack']}", params={"user_object_id": oid(user)})
        saved, _ = validate_plan(pack["plan"], facts)
        return validate_plan(dated_plan(saved, str(card["from_date"]), str(card["to_date"])), facts)

    @app.post("/api/agents/exports/build")
    def export_build(body: BuildBody, user: dict[str, Any] = Depends(agent_user)) -> dict[str, Any]:
        source = source_for(user, body.account_id)
        facts = source.facts(today())
        try:
            if body.pack_item_id is not None:
                plan, adjustments = pack_plan(user, body.account_id, body.pack_item_id, facts)
            else:
                # The browser only hands back what the website validated; validate it again all the same.
                plan, adjustments = validate_plan(body.plan or {}, facts)
        except PlanError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        built = build(plan, source.fetch, source.subject_type)
        checks = run_checks(built, source.books(), source.subject_type, adjustments)
        status = overall(checks)
        facts_for_checker = check_facts(account_id=body.account_id, plan=plan, built=built, checks=checks,
                                        subject_type=source.subject_type, reporting_currency=source.currency)
        record = api.request("POST", "/v1/exports/checks", timeout=CHECK_TIMEOUT_SECONDS, json={
            "user_object_id": oid(user), "account_id": body.account_id, "plan": plan, "fingerprint": fingerprint(built),
            "status": status, "facts": facts_for_checker, "pack_item_id": body.pack_item_id})
        return {"check_id": record["check_id"], "status": record["status"],
                "note": record.get("note") or fallback_note(facts_for_checker),
                "note_checked": bool(record.get("note_checked")), "concerns": record.get("concerns") or [],
                "checks": checks, "title": plan["title"], "plan": plan,
                "lines": [describe(d, source.subject_type) for d in plan["datasets"]], "files": preview(built)}

    @app.get("/api/agents/exports/{check_id}/download")
    def export_download(check_id: str, release: bool = False, user: dict[str, Any] = Depends(agent_user)) -> Response:
        record = api.request("GET", f"/v1/exports/checks/{check_id}", params={"user_object_id": oid(user)})
        account_id = str(record["subject_id"])
        source = source_for(user, account_id)
        if record["status"] == "fail":
            raise HTTPException(status_code=409, detail="This export failed its checks, so it can't be downloaded.")
        if record["status"] == "review" and not release and not record.get("released_at"):
            raise HTTPException(status_code=409, detail="This export needs your review. Read the checks, then "
                                                        "release it to download.")
        try:
            plan, _ = validate_plan(record["plan"], source.facts(today()))
        except PlanError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        built = build(plan, source.fetch, source.subject_type)
        if fingerprint(built) != record["fingerprint"]:
            raise HTTPException(status_code=409, detail="The account's data changed after this export was checked. "
                                                        "Build and check it again.")
        api.request("POST", f"/v1/exports/checks/{check_id}/release", json={"user_object_id": oid(user)})
        if record.get("pack_item_id"):
            try:
                api.request("POST", f"/v1/inbox/{int(record['pack_item_id'])}/decision",
                            json={"user_object_id": oid(user), "decision": "approved"})
            except HTTPException:
                pass  # the card's status is a nicety; the release is recorded on the check
        created = str(record.get("created_at") or datetime.now(timezone.utc).isoformat())
        facts_for_checker = check_facts(account_id=account_id, plan=plan, built=built, checks=record["checks"],
                                        subject_type=source.subject_type, reporting_currency=source.currency)
        content = package(built=built, plan=plan, checks=record["checks"], status=record["status"],
                          note=record.get("note") or fallback_note(facts_for_checker),
                          note_checked=bool(record.get("note_checked")), check_id=check_id, account_id=account_id,
                          account_name=source.display_name, created_at=created,
                          released_by_person=record["status"] == "review", subject_type=source.subject_type)
        return Response(content, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{_filename(account_id, plan["title"], created)}"',
            "Cache-Control": "no-store"})

    @app.get("/api/agents/exports/packs")
    def export_packs(account_id: str, user: dict[str, Any] = Depends(agent_user)) -> Any:
        if account_id not in allowed_accounts(user):
            raise HTTPException(status_code=403, detail="You do not have access to that account.")
        return api.request("GET", "/v1/exports/packs", params={"user_object_id": oid(user), "account_id": account_id})

    @app.post("/api/agents/exports/packs")
    def save_export_pack(body: PackBody, user: dict[str, Any] = Depends(agent_user)) -> Any:
        source = source_for(user, body.account_id)
        try:
            plan, _ = validate_plan(body.plan, source.facts(today()))
        except PlanError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return api.request("POST", "/v1/exports/packs", json={
            "user_object_id": oid(user), "account_id": body.account_id, "name": body.name.strip(),
            "schedule": body.schedule, "plan": plan})

    @app.delete("/api/agents/exports/packs/{pack_id}")
    def delete_export_pack(pack_id: str, user: dict[str, Any] = Depends(agent_user)) -> Any:
        return api.request("DELETE", f"/v1/exports/packs/{pack_id}", params={"user_object_id": oid(user)})

    @app.get("/agents/exports.js")
    def export_panel_script(user: dict[str, Any] = Depends(agent_user)) -> Response:
        return Response(PANEL_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})


PANEL_JS = r"""(()=>{
'use strict';
const m=location.pathname.match(/^\/accounts\/([^/]+)\/exports$/);
const host=document.querySelector('.page-view[data-section="exports"]');
if(!m||!host)return;
const account=decodeURIComponent(m[1]);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const md=s=>esc(s).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\s*\[T\d+\]/g,'').split(/\n{2,}/).map(p=>'<p>'+p.replace(/\n/g,'<br>')+'</p>').join('');
const STATUS={pass:['Passed','xa-pass'],review:['Needs your review','xa-review'],fail:['Failed','xa-fail'],info:['Note','xa-info'],not_checked:['Not checked','xa-info']};
const pill=s=>{const [t,c]=STATUS[s]||[s,'xa-info'];return `<span class="xa-pill ${c}">${esc(t)}</span>`};
const style=document.createElement('style');
style.textContent=`.xa-card textarea{width:100%;min-height:64px;box-sizing:border-box;font:inherit;padding:10px;border-radius:8px;border:1px solid var(--line,#ccd);background:var(--surface,transparent);color:inherit}
.xa-chips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.xa-chip{border:1px solid var(--line,#ccd);background:transparent;color:inherit;border-radius:999px;padding:4px 12px;cursor:pointer;font:inherit;font-size:13px}
.xa-pill{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12px;font-weight:600;border:1px solid var(--line,#ccd)}
.xa-pass{color:var(--gain,#2e9e6b);border-color:currentColor}.xa-fail{color:var(--loss,#d64550);border-color:currentColor}.xa-review{color:var(--warn-ink,#b8860b);border-color:currentColor}.xa-info{color:var(--muted,#6c757d)}
.xa-section{margin-top:16px}.xa-section ul{margin:6px 0 0 18px;padding:0}.xa-checks li{margin:4px 0;list-style:none;margin-left:-18px}
.xa-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px}.xa-row input,.xa-row select{font:inherit;padding:6px 8px;border-radius:6px;border:1px solid var(--line,#ccd);background:var(--surface,transparent);color:inherit}
.xa-note{border-left:3px solid var(--accent,#1f6feb);padding:4px 12px;margin-top:8px}.xa-note p{margin:0 0 8px}
.xa-file{margin-top:12px}.xa-file table{font-size:12px}`;
document.head.appendChild(style);
const card=document.createElement('div');card.className='card wide xa-card';
card.innerHTML=`<div class="label">Export agent</div><div class="value">Describe the export you need</div>
<div class="muted">The agent plans the files, the website builds them from this account's records, and a checker ties them to the books before you can download them. Every figure comes from the records, not the AI.</div>
<div class="xa-chips">${['Dividends and withholding tax this year, with totals by currency and type','All trades last quarter, with commissions and totals by symbol','Holdings at the end of last month and the value history for the year','Deposits and withdrawals since the account opened, totalled by year'].map(t=>`<button type="button" class="xa-chip">${esc(t)}</button>`).join('')}</div>
<textarea id="xa-request" maxlength="1000" aria-label="Describe the export you need" placeholder="For example: every dividend in 2026 with withholding tax, totalled by currency"></textarea>
<div class="actions"><button id="xa-plan" class="button" type="button">Plan export</button><span id="xa-status" class="muted" role="status" aria-live="polite"></span></div>
<div id="xa-plan-box"></div><div id="xa-result"></div><div id="xa-packs" class="xa-section"></div>`;
host.insertBefore(card,host.firstChild);
const $=s=>card.querySelector(s),status=t=>{$('#xa-status').textContent=t||''};
let current=null;
const call=async(url,opts={})=>{const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opts});
 let body=null;try{body=await r.json()}catch(e){}if(!r.ok)throw new Error((body&&typeof body.detail==='string'&&body.detail)||'That did not work. Try again.');return body};
card.querySelectorAll('.xa-chip').forEach(c=>c.addEventListener('click',()=>{$('#xa-request').value=c.textContent;$('#xa-request').focus()}));
$('#xa-plan').addEventListener('click',async()=>{const request=$('#xa-request').value.trim();if(!request){status('Describe the export first.');return}
 $('#xa-plan').disabled=true;status('Planning…');$('#xa-result').innerHTML='';
 try{const r=await call('/api/agents/exports/plan',{method:'POST',body:JSON.stringify({account_id:account,request})});
  if(!r.ok){$('#xa-plan-box').innerHTML=`<div class="xa-section muted">${esc(r.message)}</div>`;status('');return}
  current=r.plan;showPlan(r);status('')}
 catch(e){status(e.message)}finally{$('#xa-plan').disabled=false}});
function showPlan(r){const p=r.plan;
 $('#xa-plan-box').innerHTML=`<div class="xa-section"><div class="label">Plan</div><strong>${esc(p.title)}</strong>${p.summary?`<div class="muted">${esc(p.summary)}</div>`:''}
 <ul>${r.lines.map(l=>`<li>${esc(l)}</li>`).join('')}</ul>${(r.adjustments||[]).map(a=>`<div class="muted">${esc(a)}</div>`).join('')}${p.unsupported?`<div class="muted">Not included: ${esc(p.unsupported)}</div>`:''}
 <div class="xa-row"><button id="xa-build" class="button" type="button">Build and check</button></div>
 <div class="xa-row"><input id="xa-pack-name" maxlength="80" value="${esc(p.title)}" aria-label="Pack name"><select id="xa-pack-schedule" aria-label="How often"><option value="monthly">Every month</option><option value="quarterly">Every quarter</option><option value="yearly">Every year</option></select><button id="xa-save" class="button" type="button">Save as a recurring pack</button></div>
 <div class="muted">A recurring pack runs this plan for each closed month, quarter or year and leaves a card in your Agent inbox to check and release. Nothing is sent anywhere.</div></div>`;
 $('#xa-build').addEventListener('click',()=>buildIt({account_id:account,plan:current}));
 $('#xa-save').addEventListener('click',async()=>{const b=$('#xa-save');b.disabled=true;
  try{await call('/api/agents/exports/packs',{method:'POST',body:JSON.stringify({account_id:account,name:$('#xa-pack-name').value.trim()||p.title,schedule:$('#xa-pack-schedule').value,plan:current})});status('Pack saved.');loadPacks()}
  catch(e){status(e.message)}finally{b.disabled=false}})}
async function buildIt(payload){status('Building the files and running the checks…');$('#xa-result').innerHTML='';
 card.querySelectorAll('#xa-build').forEach(b=>b.disabled=true);
 try{const r=await call('/api/agents/exports/build',{method:'POST',body:JSON.stringify(payload)});showResult(r);status('')}
 catch(e){status(e.message)}finally{card.querySelectorAll('#xa-build').forEach(b=>b.disabled=false)}}
function showResult(r){const url=`/api/agents/exports/${encodeURIComponent(r.check_id)}/download`;
 const action=r.status==='pass'?`<a class="button" href="${url}">Download the checked files</a>`
  :r.status==='review'?`<a class="button" href="${url}?release=true">Release and download</a><span class="muted">Read what needs review first. Releasing is recorded.</span>`
  :`<span class="muted">This export failed a check, so it can't be downloaded. Change the request and build it again.</span>`;
 $('#xa-result').innerHTML=`<div class="xa-section"><div class="label">Checked export ${pill(r.status)}</div><strong>${esc(r.title)}</strong>
 <ul>${(r.lines||[]).map(l=>`<li>${esc(l)}</li>`).join('')}</ul>
 <div class="xa-note">${md(r.note)}<div class="muted">${r.note_checked?'Cover note by the checker agent. Every figure in it was matched to the check results.':'Cover note written by code from the check results.'}</div></div>
 ${(r.concerns||[]).length?`<div class="xa-section"><div class="label">The checker's concerns</div><ul>${r.concerns.map(c=>`<li>${esc(c)}</li>`).join('')}</ul></div>`:''}
 <div class="xa-section"><div class="label">Checks</div><ul class="xa-checks">${r.checks.map(c=>`<li>${pill(c.status)} <strong>${esc(c.label)}</strong>: ${esc(c.detail)}</li>`).join('')}</ul></div>
 ${r.files.map(f=>`<div class="xa-file"><div class="label">${esc(f.file)} · ${f.rows} rows</div>${f.sample.length?`<div class="table-scroll"><table><thead><tr>${f.columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${f.sample.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:''}
 ${f.totals&&f.totals.length?`<div class="label" style="margin-top:8px">${esc(f.totals_file)}</div><div class="table-scroll"><table><thead><tr>${Object.keys(f.totals[0]).map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${f.totals.map(t=>`<tr>${Object.values(t).map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:''}</div>`).join('')}
 <div class="xa-row">${action}</div></div>`}
async function loadPacks(){try{const r=await call(`/api/agents/exports/packs?account_id=${encodeURIComponent(account)}`);const packs=r.packs||[];
 $('#xa-packs').innerHTML=packs.length?`<div class="label">Recurring packs for this account</div><ul>${packs.map(p=>`<li><strong>${esc(p.name)}</strong> · ${esc({monthly:'every month',quarterly:'every quarter',yearly:'every year'}[p.schedule]||p.schedule)} <button type="button" class="xa-chip" data-pack="${esc(p.pack_id)}">Delete</button></li>`).join('')}</ul>`:'';
 $('#xa-packs').querySelectorAll('[data-pack]').forEach(b=>b.addEventListener('click',async()=>{b.disabled=true;try{await call(`/api/agents/exports/packs/${encodeURIComponent(b.dataset.pack)}`,{method:'DELETE'});loadPacks()}catch(e){status(e.message);b.disabled=false}}))}
 catch(e){$('#xa-packs').innerHTML=''}}
loadPacks();
const item=new URLSearchParams(location.search).get('pack_item');
if(item&&/^\d+$/.test(item))buildIt({account_id:account,pack_item_id:Number(item)});
})();
"""
