"""Website side of the WealthRyse agents: chat panel, "Explain" buttons and inbox.

Off unless AGENT_UI_MODE is "owner" (owners only) or "all". The website
never talks to a model itself: it forwards the signed-in person's request to
the internal agent service, signing in with its own managed identity and
naming the person, and streams the validated answer back.

Settings: AGENT_UI_MODE, AGENT_API_URL (internal https URL of the agent
service), AGENT_API_AUDIENCE (its Entra app ID URI), AZURE_CLIENT_ID (the
website identity, already set), and MONTH_TEAM_UI for the month-end team's
screens (see month_team_web).
"""
from __future__ import annotations

import functools
import json
import os
import re
import threading
from html import escape
from urllib.parse import quote
from typing import Any, Callable, Iterable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import month_team_web

# The chat panel opens on every account tab; the script also runs on every other page so the
# navigation's "Agent inbox" link can show how many items are new.
WIDGET_PATHS = re.compile(r"^/accounts/[^/]+/[^/]+$")
MODES = {"off", "owner", "all"}


class _Settings:
    def __init__(self) -> None:
        self.mode = os.environ.get("AGENT_UI_MODE", "off").strip().lower()
        if self.mode not in MODES:
            self.mode = "off"
        self.url = os.environ.get("AGENT_API_URL", "").strip().rstrip("/")
        self.audience = os.environ.get("AGENT_API_AUDIENCE", "").strip()
        self.client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=36)


class MemoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=200)


class FeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=1000)


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(read|approved|dismissed)$")


class _AgentApi:
    """Calls the internal agent service as the website's managed identity."""

    def __init__(self, settings: _Settings) -> None:
        self.settings = settings
        self._credential = None
        self._lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        with self._lock:
            if self._credential is None:
                from azure.identity import ManagedIdentityCredential

                self._credential = ManagedIdentityCredential(client_id=self.settings.client_id)
        token = self._credential.get_token(self.settings.audience.rstrip("/") + "/.default").token
        return {"Authorization": "Bearer " + token}

    def request(self, method: str, path: str, timeout: float = 30.0, **kwargs: Any) -> Any:
        import httpx

        if not self.settings.url or not self.settings.audience:
            raise HTTPException(status_code=503, detail="The analyst is not configured.")
        response = httpx.request(method, self.settings.url + path, headers=self._headers(), timeout=timeout, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            raise HTTPException(status_code=response.status_code if response.status_code < 500 else 502,
                                detail=detail if isinstance(detail, str) else "The analyst could not complete that.")
        return response.json()

    def stream(self, path: str, payload: dict[str, Any]) -> Iterable[bytes]:
        import httpx

        if not self.settings.url or not self.settings.audience:
            raise HTTPException(status_code=503, detail="The analyst is not configured.")
        client = httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))  # the agent sends a keepalive every 15s
        request = client.build_request("POST", self.settings.url + path, headers=self._headers(), json=payload)
        response = client.send(request, stream=True)
        if response.status_code >= 400:
            body = response.read()
            response.close()
            client.close()
            try:
                detail = json.loads(body).get("detail")
            except ValueError:
                detail = None
            raise HTTPException(status_code=response.status_code if response.status_code < 500 else 502,
                                detail=detail if isinstance(detail, str) else "The analyst could not start.")

        def chunks() -> Iterable[bytes]:
            try:
                yield from response.iter_bytes()
            finally:
                response.close()
                client.close()

        return chunks()


def ui_enabled_for(user: dict[str, Any]) -> bool:
    """Whether this person sees the chat panel, the Explain buttons and the Agent inbox."""
    mode = _Settings().mode
    return mode == "all" or (mode == "owner" and user.get("role") == "owner")


@functools.lru_cache(maxsize=1)
def case_question_stream() -> Callable[[dict[str, Any]], Iterable[bytes]] | None:
    """For the onboarding case page's "Ask about this case" box; None while the agent service is not configured.

    That box is switched by ONBOARDING_AGENTS_ENABLED, not AGENT_UI_MODE (see onboarding_v2_web).
    """
    settings = _Settings()
    if not settings.url or not settings.audience:
        return None
    api = _AgentApi(settings)
    return lambda payload: api.stream("/v1/case-question", payload)


def install(app: FastAPI, authorized_user: Callable[..., dict[str, Any]],
            allowed_accounts: Callable[[dict[str, Any]], list[str]], theme_css: str = "",
            render_page: Callable[..., str] | None = None,
            account_names: Callable[[dict[str, Any]], dict[str, str]] | None = None,
            export_source: Callable[[dict[str, Any], str], Any] | None = None) -> None:
    """Add the agent routes and the widget to the website. No-op routes when off.

    render_page is the website's page shell (logo, navigation, sign-out), called as
    render_page(user, active=, title=, description=, content=, script=); without it the agent
    pages stand alone. account_names gives the display name of each account the person can see.
    export_source(user, account_id) gives the export agent the account's rows and books (see
    export_agent_web); without it the Exports tab has no export agent panel.
    """
    settings = _Settings()
    api = _AgentApi(settings)

    def enabled_for(user: dict[str, Any]) -> bool:
        return settings.mode == "all" or (settings.mode == "owner" and user.get("role") == "owner")

    def agent_user(user: dict[str, Any] = Depends(authorized_user)) -> dict[str, Any]:
        if not enabled_for(user):
            raise HTTPException(status_code=404, detail="Not found")
        if not str(user.get("object_id") or ""):
            raise HTTPException(status_code=403, detail="Your sign-in has no Entra object ID.")
        return user

    def oid(user: dict[str, Any]) -> str:
        return str(user["object_id"]).lower()

    @app.get("/api/agents/status")
    def agent_status(user: dict[str, Any] = Depends(authorized_user)) -> dict[str, Any]:
        return {"enabled": enabled_for(user)}

    @app.post("/api/agents/chat")
    def agent_chat(body: ChatBody, user: dict[str, Any] = Depends(agent_user)) -> StreamingResponse:
        if body.account_id not in allowed_accounts(user):
            raise HTTPException(status_code=403, detail="You do not have access to that account.")
        stream = api.stream("/v1/chat", {"user_object_id": oid(user), **body.model_dump()})
        return StreamingResponse(stream, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/agents/memory")
    def agent_memory(user: dict[str, Any] = Depends(agent_user)) -> Any:
        return api.request("GET", "/v1/memory", params={"user_object_id": oid(user)})

    @app.put("/api/agents/memory/{key}")
    def agent_set_memory(key: str, body: MemoryBody, user: dict[str, Any] = Depends(agent_user)) -> Any:
        return api.request("PUT", f"/v1/memory/{quote(key, safe='')}", json={"user_object_id": oid(user), "value": body.value})

    @app.delete("/api/agents/memory")
    def agent_forget_all(user: dict[str, Any] = Depends(agent_user)) -> Any:
        return api.request("DELETE", "/v1/memory", params={"user_object_id": oid(user)})

    @app.delete("/api/agents/memory/{key}")
    def agent_forget(key: str, user: dict[str, Any] = Depends(agent_user)) -> Any:
        return api.request("DELETE", f"/v1/memory/{quote(key, safe='')}", params={"user_object_id": oid(user)})

    @app.get("/api/agents/conversations")
    def agent_conversations(account_id: str, user: dict[str, Any] = Depends(agent_user)) -> Any:
        return api.request("GET", "/v1/conversations", params={"user_object_id": oid(user), "account_id": account_id})

    @app.get("/api/agents/conversations/{conversation_id}")
    def agent_conversation(conversation_id: str, user: dict[str, Any] = Depends(agent_user)) -> Any:
        return api.request("GET", f"/v1/conversations/{conversation_id}", params={"user_object_id": oid(user)})

    @app.post("/api/agents/conversations/{conversation_id}/archive")
    def agent_archive(conversation_id: str, user: dict[str, Any] = Depends(agent_user)) -> Any:
        return api.request("POST", f"/v1/conversations/{conversation_id}/archive", json={"user_object_id": oid(user)})

    @app.post("/api/agents/messages/{message_id}/feedback")
    def agent_feedback(message_id: int, body: FeedbackBody, user: dict[str, Any] = Depends(agent_user)) -> Any:
        return api.request("POST", f"/v1/messages/{message_id}/feedback",
                           json={"user_object_id": oid(user), **body.model_dump()})

    def inbox_items(user: dict[str, Any]) -> list[dict[str, Any]]:
        items = api.request("GET", "/v1/inbox", params={"user_object_id": oid(user)})["items"]
        # The month-end team's cards ask the owner to decide; nobody else sees them.
        if month_team_web.is_month_owner(user):
            return items
        return [x for x in items if x.get("kind") not in month_team_web.MONTH_TEAM_KINDS]

    @app.get("/api/agents/inbox")
    def agent_inbox(user: dict[str, Any] = Depends(agent_user)) -> Any:
        return {"items": inbox_items(user)}

    @app.post("/api/agents/inbox/{item_id}/decision")
    def agent_decision(item_id: int, body: DecisionBody, user: dict[str, Any] = Depends(agent_user)) -> Any:
        if not month_team_web.is_month_owner(user) and not any(x.get("item_id") == item_id for x in inbox_items(user)):
            raise HTTPException(status_code=404, detail="Item not found.")
        return api.request("POST", f"/v1/inbox/{item_id}/decision", json={"user_object_id": oid(user), **body.model_dump()})

    @app.get("/agents/widget.js")
    def agent_widget() -> Response:
        return Response(WIDGET_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.get("/agent-inbox", response_class=HTMLResponse)
    def agent_inbox_page(user: dict[str, Any] = Depends(agent_user)) -> str:
        names: dict[str, str] = {}
        if account_names is not None:
            try:
                names = {str(k): str(v) for k, v in account_names(user).items()}
            except Exception:  # names are a nicety; the inbox still works with account IDs
                names = {}
        month_link = ('<a class="button" href="/month-end">Month-end</a>'
                      if month_team_web.is_month_owner(user) else "")
        script = INBOX_SCRIPT.replace("{NAMES}", json.dumps(names).replace("</", "<\\/"))
        content = INBOX_STYLE + INBOX_BODY.replace("{MONTH_END}", month_link)
        if render_page is not None:
            return render_page(user, active="agent-inbox", title="Agent inbox", description=INBOX_DESCRIPTION,
                               content=content, script=script)
        return (INBOX_PAGE.replace("{THEME}", theme_css).replace("{CONTENT}", content).replace("{SCRIPT}", script)
                .replace("{VIEWER}", escape(str(user.get("email") or ""))))

    month_team_web.install(app, authorized_user, allowed_accounts, theme_css, api, render_page)
    widget = b'<script src="/agents/widget.js" defer></script>' if settings.mode != "off" else b""
    scripts = []
    if export_source is not None and settings.mode != "off":
        from . import export_agent_web

        export_agent_web.install(app, agent_user, allowed_accounts, api, export_source)
        scripts.append((export_agent_web.PANEL_PATHS, b'<script src="/agents/exports.js" defer></script>'))
    if month_team_web.enabled():
        scripts.append((month_team_web.PANEL_PATHS, b'<script src="/agents/month-team.js" defer></script>'))
    if not widget and not scripts:
        return

    @app.middleware("http")
    async def agent_widget_injector(request: Request, call_next):
        response = await call_next(request)
        # The widget goes on every page (it only adds the chat panel on account tabs).
        tags = ([widget] if widget else []) + [tag for paths, tag in scripts if paths.match(request.url.path)]
        if (request.method != "GET" or response.status_code != 200 or not tags
                or not response.headers.get("content-type", "").startswith("text/html")):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        marker = body.rfind(b"</body>")
        if marker >= 0:
            body = body[:marker] + b"".join(tags) + body[marker:]
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(body, status_code=response.status_code, headers=headers, media_type="text/html")


WIDGET_JS = r"""(()=>{
'use strict';
const m=location.pathname.match(/^\/accounts\/([^/]+)\/([^/]+)$/);
const account=m?decodeURIComponent(m[1]):null,section=m?m[2]:'';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const md=s=>esc(s).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\[(T\d+)\]/g,'<sup class="wr-cite">$1</sup>')
 .split(/\n{2,}/).map(p=>/^\s*[-•] /m.test(p)?'<ul>'+p.split('\n').filter(l=>l.trim()).map(l=>'<li>'+l.replace(/^\s*[-•] /,'')+'</li>').join('')+'</ul>':'<p>'+p.replace(/\n/g,'<br>')+'</p>').join('');
const PALETTE=['var(--accent,#1f6feb)','#e8833a','#2e9e6b','#9b59b6','#d64550','#1aa3b8','#b8860b','#6c757d'];
const fmt=(v,u,c)=>{if(v===null||v===undefined||!isFinite(v))return '–';if(u==='percent')return (v*100).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})+'%';
 const a=Math.abs(v);return (u==='currency'&&c?esc(c)+' ':'')+v.toLocaleString(undefined,{maximumFractionDigits:a>=1000?0:a>=1?2:4})};
const short=(v,u)=>{if(u==='percent')return (v*100).toFixed(Math.abs(v)<0.1?1:0)+'%';const a=Math.abs(v);return a>=1e9?(v/1e9).toFixed(1)+'bn':a>=1e6?(v/1e6).toFixed(1)+'m':a>=1e4?(v/1e3).toFixed(0)+'k':v.toLocaleString(undefined,{maximumFractionDigits:2})};
const legend=v=>v.series.length>1?'<div class="wr-legend">'+v.series.map((s,i)=>`<span><i style="background:${PALETTE[i%8]}"></i>${esc(s.name)}</span>`).join('')+'</div>':'';
function vTable(v){return '<div class="wr-tablewrap"><table class="wr-table"><thead><tr><th>'+esc(v.x_label)+'</th>'+v.series.map(s=>'<th>'+esc(s.name)+'</th>').join('')+'</tr></thead><tbody>'+
 v.labels.map((l,i)=>'<tr><th>'+esc(l)+'</th>'+v.series.map(s=>'<td>'+fmt(s.values[i],s.unit,v.currency)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>'}
function range(v){let lo=Infinity,hi=-Infinity;v.series.forEach(s=>s.values.forEach(x=>{if(x!==null&&isFinite(x)){lo=Math.min(lo,x);hi=Math.max(hi,x)}}));if(!isFinite(lo))lo=hi=0;return [lo,hi]}
function vBar(v){const n=v.labels.length,k=v.series.length,row=Math.max(14,k*10+6),W=400,L=110,R=56,H=n*row+8;let [lo,hi]=range(v);lo=Math.min(lo,0);hi=Math.max(hi,0);if(hi===lo)hi=lo+1;
 const x=t=>L+(t-lo)/(hi-lo)*(W-L-R),z=x(0);let g=`<line x1="${z}" y1="0" x2="${z}" y2="${H}" class="wr-axis"/>`;
 v.labels.forEach((l,i)=>{const y=4+i*row;g+=`<text x="${L-6}" y="${y+row/2+3}" text-anchor="end" class="wr-tick">${esc(l.length>18?l.slice(0,17)+'…':l)}</text>`;
  v.series.forEach((s,j)=>{const val=s.values[i];if(val===null||!isFinite(val))return;const a=x(val),bh=Math.max(4,(row-6)/k-1),by=y+2+j*(bh+1);
   g+=`<rect x="${Math.min(a,z)}" y="${by}" width="${Math.max(1,Math.abs(a-z))}" height="${bh}" fill="${PALETTE[j%8]}" rx="2"><title>${esc(l)} · ${esc(s.name)}: ${fmt(val,s.unit,v.currency)}</title></rect>`;
   if(k===1)g+=`<text x="${val<0?z+4:a+4}" y="${by+bh-1}" class="wr-tick">${short(val,s.unit)}</text>`})});
 return `<svg viewBox="0 0 ${W} ${H}" class="wr-chart" role="img" aria-label="${esc(v.title)}">${g}</svg>`+legend(v)}
function vLine(v){const n=v.labels.length,W=400,H=220,L=48,R=10,T=10,B=26;let [lo,hi]=range(v);if(hi===lo){hi+=Math.abs(hi)*0.01||1;lo-=Math.abs(lo)*0.01||1}const pad=(hi-lo)*0.06;lo-=pad;hi+=pad;
 const x=i=>L+(n>1?i/(n-1):0.5)*(W-L-R),y=t=>T+(hi-t)/(hi-lo)*(H-T-B),u=v.series[0].unit;let g='';
 for(let t=0;t<=4;t++){const val=lo+(hi-lo)*t/4;g+=`<line x1="${L}" x2="${W-R}" y1="${y(val)}" y2="${y(val)}" class="wr-grid"/><text x="${L-4}" y="${y(val)+3}" text-anchor="end" class="wr-tick">${short(val,u)}</text>`}
 if(lo<0&&hi>0)g+=`<line x1="${L}" x2="${W-R}" y1="${y(0)}" y2="${y(0)}" class="wr-axis"/>`;
 [0,Math.floor((n-1)/2),n-1].filter((i,j,a)=>a.indexOf(i)===j).forEach(i=>g+=`<text x="${x(i)}" y="${H-8}" text-anchor="${i===0?'start':i===n-1?'end':'middle'}" class="wr-tick">${esc(v.labels[i])}</text>`);
 v.series.forEach((s,j)=>{let d='',pen=false;s.values.forEach((val,i)=>{if(val===null||!isFinite(val)){pen=false;return}d+=(pen?'L':'M')+x(i).toFixed(1)+' '+y(val).toFixed(1);pen=true});
  g+=`<path d="${d}" fill="none" stroke="${PALETTE[j%8]}" stroke-width="2" stroke-linejoin="round"/>`;
  if(n<=60)s.values.forEach((val,i)=>{if(val!==null&&isFinite(val))g+=`<circle cx="${x(i)}" cy="${y(val)}" r="2.5" fill="${PALETTE[j%8]}"><title>${esc(v.labels[i])} · ${esc(s.name)}: ${fmt(val,s.unit,v.currency)}</title></circle>`})});
 return `<svg viewBox="0 0 ${W} ${H}" class="wr-chart" role="img" aria-label="${esc(v.title)}">${g}</svg>`+legend(v)}
function vPie(v){const s=v.series[0],vals=s.values.map(x=>x!==null&&x>0?x:0),tot=vals.reduce((a,b)=>a+b,0)||1;let a=-Math.PI/2,g='';const c=80,r=70,r2=40;
 vals.forEach((val,i)=>{if(!val)return;const b=a+val/tot*Math.PI*2,big=b-a>Math.PI?1:0,p=(t,rr)=>`${(c+rr*Math.cos(t)).toFixed(2)} ${(c+rr*Math.sin(t)).toFixed(2)}`;
  g+=val/tot>0.9999?`<circle cx="${c}" cy="${c}" r="${(r+r2)/2}" fill="none" stroke="${PALETTE[i%8]}" stroke-width="${r-r2}"><title>${esc(v.labels[i])}: ${fmt(s.values[i],s.unit,v.currency)}</title></circle>`
   :`<path d="M${p(a,r)} A${r} ${r} 0 ${big} 1 ${p(b,r)} L${p(b,r2)} A${r2} ${r2} 0 ${big} 0 ${p(a,r2)} Z" fill="${PALETTE[i%8]}"><title>${esc(v.labels[i])}: ${fmt(s.values[i],s.unit,v.currency)}</title></path>`;a=b});
 return `<div class="wr-pie"><svg viewBox="0 0 160 160" class="wr-chart" role="img" aria-label="${esc(v.title)}">${g}</svg><div class="wr-legend wr-col">`+
  v.labels.map((l,i)=>`<span><i style="background:${PALETTE[i%8]}"></i>${esc(l)}: ${fmt(s.values[i],s.unit,v.currency)}</span>`).join('')+'</div></div>'}
const visuals=list=>(list||[]).map(v=>{try{if(!v||!Array.isArray(v.labels)||!Array.isArray(v.series)||!v.series.length)return '';
 const body={table:vTable,bar:vBar,line:vLine,pie:vPie}[v.kind];return body?'<figure class="wr-visual"><figcaption>'+esc(v.title)+'</figcaption>'+body(v)+(v.sources&&v.sources.length?'<div class="wr-sources">From '+v.sources.map(esc).join(', ')+'</div>':'')+'</figure>':''}catch(e){return ''}}).join('');
const store={get(k){try{return sessionStorage.getItem(k)}catch(e){return null}},set(k,v){try{v?sessionStorage.setItem(k,v):sessionStorage.removeItem(k)}catch(e){}}};
const key='wr-agent-conversation-'+account;
// Questions to start from, by account tab. Each one is answered by the read-only portfolio tools.
const STARTERS={overview:['What changed in this account this week?','How has it done against its benchmark this year?','What drives its sector mix?'],
 performance:['Why did this account beat or lag its benchmark this year?','Which were its best and worst months, and why?','Which holdings contributed most to the return?'],
 holdings:['Which holdings moved the most this week?','How concentrated is this account?','Which sector is largest, and how has it changed?'],
 risk:['What if Hong Kong falls 10%?','What are the main risks in this account?','How volatile has it been against its benchmark?'],
 cash:['Where did cash come from and go this month?','How much cash does the account hold now?'],
 trades:['What did this account trade this month?','Which trades changed the holdings most?'],
 reports:['Summarise last month for this account.','What changed since the last month-end?']};
const starters=STARTERS[section]||STARTERS.overview;
const css=`.wr-fab{position:fixed;right:20px;bottom:20px;z-index:50;display:inline-flex;align-items:center;gap:8px;border:0;border-radius:999px;padding:12px 18px;font:inherit;font-weight:600;cursor:pointer;background:var(--accent,#1f6feb);color:#fff;box-shadow:0 6px 20px rgba(0,0,0,.2)}
.wr-fab:focus-visible,.wr-panel button:focus-visible,.wr-panel a:focus-visible{outline:2px solid var(--accent,#1f6feb);outline-offset:2px}
.wr-fab[aria-expanded="true"]{opacity:.9}
.wr-panel{position:fixed;right:20px;bottom:80px;z-index:50;width:min(420px,calc(100vw - 32px));height:min(620px,calc(100vh - 120px));display:none;flex-direction:column;background:var(--card,#fff);color:var(--text,#111);border:1px solid var(--border,#ddd);border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.25);overflow:hidden}
.wr-panel.open{display:flex}.wr-head{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;padding:12px 14px 6px}
.wr-title{min-width:0}.wr-title strong{display:block;white-space:nowrap}.wr-sub{display:block;font-size:12px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wr-icons{display:flex;gap:2px;flex:none}.wr-icons button{font:inherit;font-size:15px;line-height:1;width:30px;height:30px;border-radius:8px;background:none;border:0;color:inherit;opacity:.7;cursor:pointer}.wr-icons button:hover{opacity:1;background:var(--surface,#f4f5f7)}
.wr-bar{display:flex;flex-wrap:wrap;gap:6px;padding:0 14px 10px;border-bottom:1px solid var(--border,#ddd)}
.wr-bar a,.wr-bar button{font:inherit;font-size:12px;font-weight:600;background:none;border:1px solid var(--border,#ccc);border-radius:999px;padding:3px 10px;color:inherit;cursor:pointer;text-decoration:none;white-space:nowrap}
.wr-bar a:hover,.wr-bar button:hover{border-color:var(--accent,#1f6feb)}
.wr-log{flex:1;overflow:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px}
.wr-msg{padding:10px 12px;border-radius:10px;line-height:1.45;font-size:14px}.wr-msg p{margin:0 0 6px}.wr-msg ul{margin:4px 0 6px 18px;padding:0}
.wr-user{align-self:flex-end;background:var(--accent,#1f6feb);color:#fff;max-width:85%}.wr-bot{background:var(--surface,#f4f5f7);max-width:95%}
.wr-chips{display:flex;flex-direction:column;align-items:flex-start;gap:6px;margin-top:8px}.wr-chip{font:inherit;font-size:13px;text-align:left;border:1px solid var(--border,#ccc);background:var(--card,#fff);color:inherit;border-radius:12px;padding:6px 10px;cursor:pointer}.wr-chip:hover{border-color:var(--accent,#1f6feb)}
.wr-status{font-size:12px;opacity:.7}.wr-cite{font-size:10px;opacity:.75}.wr-sources{font-size:11px;opacity:.75;margin-top:6px}
.wr-rate{margin-top:6px;display:flex;gap:6px}.wr-rate button{border:1px solid var(--border,#ccc);background:none;border-radius:6px;cursor:pointer;padding:1px 6px;color:inherit}
.wr-form{display:flex;gap:8px;padding:10px;border-top:1px solid var(--border,#ddd)}.wr-form textarea{flex:1;resize:none;height:44px;font:inherit;font-size:14px;padding:8px;border-radius:8px;border:1px solid var(--border,#ccc);background:transparent;color:inherit}
.wr-form button{border:0;border-radius:8px;padding:0 14px;font:inherit;font-weight:600;cursor:pointer;background:var(--accent,#1f6feb);color:#fff}.wr-form button:disabled{opacity:.6;cursor:wait}
.wr-hint{font-size:11px;opacity:.6;padding:0 12px 8px}
.wr-alert{text-decoration:none;color:#8a5a00;border-color:rgba(194,137,0,.4)}.wr-explain{margin-left:8px;font-size:12px;border:1px solid var(--border,#ccc);background:none;border-radius:999px;padding:1px 8px;cursor:pointer;color:inherit;vertical-align:middle}
.wr-panel.wide{width:min(760px,calc(100vw - 32px));height:min(780px,calc(100vh - 120px))}
.wr-visual{margin:8px 0 4px;padding:0}.wr-visual figcaption{font-weight:600;font-size:13px;margin-bottom:4px}.wr-chart{width:100%;height:auto;display:block;overflow:visible}
.wr-chart .wr-tick{font-size:10px;fill:currentColor;opacity:.75}.wr-chart .wr-grid{stroke:currentColor;opacity:.12}.wr-chart .wr-axis{stroke:currentColor;opacity:.45}
.wr-legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:11px;margin-top:4px}.wr-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:-1px}.wr-legend.wr-col{flex-direction:column;gap:3px}
.wr-pie{display:flex;gap:12px;align-items:center}.wr-pie svg{width:140px;flex:none}
.wr-tablewrap{overflow:auto;max-height:340px}.wr-table{border-collapse:collapse;font-size:12px;width:100%}.wr-table th,.wr-table td{padding:3px 6px;border-bottom:1px solid var(--border,#ddd);text-align:right;white-space:nowrap}.wr-table th:first-child{text-align:left}.wr-table thead th{position:sticky;top:0;background:var(--surface,#f4f5f7)}
@media (max-width:600px){.wr-panel,.wr-panel.wide{right:8px;left:8px;top:8px;bottom:72px;width:auto;height:auto}.wr-fab{right:16px;bottom:16px;padding:10px 16px}.wr-hint{display:none}}`;
// One read of the inbox serves the navigation badge, the panel's Inbox link and the live card's alert count.
let inboxRead=null;const inbox=()=>inboxRead||(inboxRead=fetch('/api/agents/inbox',{credentials:'same-origin'}).then(r=>r.ok?r.json():{items:[]}).catch(()=>({items:[]})));
const badge=n=>n?` <span class="wr-badge" aria-hidden="true">${n>99?'99+':n}</span>`:'';
function navBadge(){const links=document.querySelectorAll('a[data-agent-inbox]');if(!links.length)return;
 inbox().then(d=>{const n=(d.items||[]).filter(x=>x.status==='new').length;if(!n)return;links.forEach(a=>{if(a.querySelector('.wr-badge'))return;a.insertAdjacentHTML('beforeend',badge(n));a.setAttribute('aria-label',`Agent inbox, ${n} new`)})})}
fetch('/api/agents/status',{credentials:'same-origin'}).then(r=>r.ok?r.json():{enabled:false}).then(s=>{if(!s.enabled)return;
 const style=document.createElement('style');style.textContent=css+'.wr-badge{display:inline-block;min-width:18px;padding:0 6px;margin-left:4px;border-radius:999px;background:#d64550;color:#fff;font-size:11px;font-weight:700;line-height:18px;text-align:center;vertical-align:1px}';document.head.appendChild(style);
 navBadge();if(account)init()}).catch(()=>{});
function init(){
 const name=((document.querySelector('main h1')||{}).textContent||'').trim();
 const fab=document.createElement('button');fab.className='wr-fab';fab.type='button';fab.setAttribute('aria-expanded','false');fab.setAttribute('aria-controls','wr-agent-panel');
 fab.innerHTML='<span aria-hidden="true">✦</span><span>Ask about this account</span>';
 const panel=document.createElement('section');panel.className='wr-panel';panel.id='wr-agent-panel';panel.setAttribute('role','dialog');panel.setAttribute('aria-label','Portfolio analyst');
 panel.innerHTML=`<div class="wr-head"><div class="wr-title"><strong>Portfolio analyst</strong>${name?`<span class="wr-sub">${esc(name)}</span>`:''}</div><div class="wr-icons"><button type="button" data-wide aria-label="Wider panel" title="Wider panel">⤢</button><button type="button" data-close aria-label="Close" title="Close (Esc)">✕</button></div></div>
<div class="wr-bar"><button type="button" data-new>New chat</button><button type="button" data-memory>What I remember</button><a href="/agent-inbox" data-inbox>Inbox</a></div>
<div class="wr-log" aria-live="polite"></div><form class="wr-form"><textarea maxlength="2000" placeholder="Ask about holdings or performance…" aria-label="Your question"></textarea><button type="submit">Send</button></form><div class="wr-hint">Enter to send · Shift+Enter for a new line · Esc to close</div>`;
 document.body.append(fab,panel);
 const log=panel.querySelector('.wr-log'),form=panel.querySelector('form'),input=form.querySelector('textarea'),send=form.querySelector('button');
 let conversation=store.get(key),busy=false;
 inbox().then(d=>{const n=(d.items||[]).filter(x=>x.status==='new').length;if(n){const a=panel.querySelector('[data-inbox]');a.innerHTML='Inbox'+badge(n);a.setAttribute('aria-label',`Inbox, ${n} new`)}});
 const add=(cls,html)=>{const d=document.createElement('div');d.className='wr-msg '+cls;d.innerHTML=html;log.appendChild(d);log.scrollTop=log.scrollHeight;return d};
 const empty=()=>{log.innerHTML='';const box=add('wr-bot','<p>Ask what changed, how the account performed against its benchmark, or what drives its sector mix. Every figure comes from your validated data.</p><div class="wr-chips">'+starters.map(q=>`<button type="button" class="wr-chip">${esc(q)}</button>`).join('')+'</div>');
  box.querySelectorAll('.wr-chip').forEach(b=>b.addEventListener('click',()=>ask(b.textContent)))};
 const sources=c=>c&&c.length?'<div class="wr-sources">Sources: '+c.map(x=>esc(x.id)+' '+esc((x.tool||'').replace(/_/g,' '))+(x.as_of?' ('+esc(x.as_of)+')':'')).join(' · ')+'</div>':'';
 const rate=(node,id)=>{if(!id)return;const r=document.createElement('div');r.className='wr-rate';r.innerHTML='<button type="button" aria-label="Helpful">👍</button><button type="button" aria-label="Not helpful">👎</button>';
  r.querySelectorAll('button').forEach((b,i)=>b.addEventListener('click',()=>{fetch(`/api/agents/messages/${id}/feedback`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({rating:i?-1:1})}).then(()=>{r.textContent='Thanks for the feedback.'})}));node.appendChild(r)};
 async function load(){empty();if(!conversation)return;try{const r=await fetch(`/api/agents/conversations/${encodeURIComponent(conversation)}`,{credentials:'same-origin'});if(!r.ok){conversation=null;store.set(key,null);return}const c=await r.json();log.innerHTML='';
  for(const x of c.messages){if(x.role==='user')add('wr-user',md(x.content));else if(x.status==='ok'){const n=add('wr-bot',md(x.content)+visuals(x.visuals)+sources(x.citations));rate(n,x.message_id)}}
  if(!log.children.length)empty()}catch(e){}}
 async function ask(question){if(busy||!question.trim())return;busy=true;send.disabled=true;add('wr-user',md(question));const status=add('wr-bot wr-status','Thinking… deeper questions can take a few minutes.');
  try{const r=await fetch('/api/agents/chat',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({account_id:account,message:question,conversation_id:conversation})});
   if(!r.ok){let d='The analyst could not answer.';try{d=(await r.json()).detail||d}catch(e){}status.className='wr-msg wr-bot';status.textContent=d;return}
   const reader=r.body.getReader(),dec=new TextDecoder();let buf='';
   for(;;){const {value,done}=await reader.read();if(done)break;buf+=dec.decode(value,{stream:true});let i;
    while((i=buf.indexOf('\n\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+2);if(!line.startsWith('data: '))continue;const e=JSON.parse(line.slice(6));
     if(e.type==='start'){conversation=e.conversation_id;store.set(key,conversation)}
     else if(e.type==='status'){status.textContent=e.text+'…'}
     else if(e.type==='answer'||e.type==='rejected'){status.className='wr-msg wr-bot';status.innerHTML=md(e.answer)+visuals(e.visuals)+sources(e.citations);rate(status,e.message_id)}
     else if(e.type==='remembered'){add('wr-bot wr-status','I will remember: '+e.memories.map(x=>esc(x.label)+': '+esc(x.value)).join('; ')+'. Change it under What I remember.')}
     else if(e.type==='error'){status.className='wr-msg wr-bot';status.textContent=e.message}}}
  }catch(e){status.className='wr-msg wr-bot';status.textContent='The connection dropped. Try again.'}
  finally{busy=false;send.disabled=false;log.scrollTop=log.scrollHeight}}
 const open=()=>{if(!panel.classList.contains('open')){panel.classList.add('open');fab.setAttribute('aria-expanded','true');load()}input.focus()};
 const close=()=>{panel.classList.remove('open');fab.setAttribute('aria-expanded','false');fab.focus()};
 fab.addEventListener('click',()=>panel.classList.contains('open')?close():open());
 panel.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();close()}});
 panel.querySelector('[data-wide]').addEventListener('click',()=>panel.classList.toggle('wide'));
 panel.querySelector('[data-close]').addEventListener('click',close);
 panel.querySelector('[data-new]').addEventListener('click',()=>{conversation=null;store.set(key,null);empty();input.focus()});
 const mem=(method,path,body)=>fetch('/api/agents/memory'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined})
  .then(async r=>{const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||'Not saved.');return j});
 async function memory(){log.innerHTML='';const box=add('wr-bot','<p>Loading…</p>');
  try{const data=await mem('GET','');const by=Object.fromEntries(data.memories.map(x=>[x.key,x]));
   box.innerHTML='<p><strong>What I remember</strong><br>These shape how I write answers, never the figures. Only you can see them.</p>'+data.keys.map(k=>{const cur=by[k.key];
    const field=k.choices?`<select data-key="${esc(k.key)}"><option value="">Not set</option>${k.choices.map(c=>`<option ${cur&&cur.value===c?'selected':''}>${esc(c)}</option>`).join('')}</select>`
     :`<input data-key="${esc(k.key)}" maxlength="${k.max_length}" value="${esc(cur?cur.value:'')}" placeholder="Not set">`;
    return `<p><label>${esc(k.label)}<br>${field}</label> ${cur?`<button type="button" class="wr-explain" data-forget="${esc(k.key)}">Delete</button>`:''}</p>`}).join('')+
    '<p><button type="button" class="wr-explain" data-save>Save</button> <button type="button" class="wr-explain" data-forget-all>Forget everything</button> <button type="button" class="wr-explain" data-back>Back to chat</button></p><p class="wr-status" data-note></p>';
   const note=box.querySelector('[data-note]');
   box.querySelector('[data-save]').addEventListener('click',async()=>{try{for(const f of box.querySelectorAll('[data-key]')){const v=f.value.trim(),k=f.dataset.key;
     if(v&&(!by[k]||by[k].value!==v))await mem('PUT','/'+encodeURIComponent(k),{value:v});else if(!v&&by[k])await mem('DELETE','/'+encodeURIComponent(k))}memory()}catch(e){note.textContent=e.message}});
   box.querySelectorAll('[data-forget]').forEach(b=>b.addEventListener('click',()=>mem('DELETE','/'+encodeURIComponent(b.dataset.forget)).then(memory).catch(e=>{note.textContent=e.message})));
   box.querySelector('[data-forget-all]').addEventListener('click',()=>mem('DELETE','').then(memory).catch(e=>{note.textContent=e.message}));
   box.querySelector('[data-back]').addEventListener('click',()=>load());
  }catch(e){box.textContent='What I remember could not be loaded.'}}
 panel.querySelector('[data-memory]').addEventListener('click',memory);
 form.addEventListener('submit',e=>{e.preventDefault();const q=input.value;input.value='';ask(q)});
 input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();form.requestSubmit()}});
 // Old links to the AI analyst tab land on Overview with the panel open.
 if(location.hash==='#ask')open();
 const explain=()=>{document.querySelectorAll('.page-view.active .card > .label').forEach((label,n)=>{if(n>=6||label.querySelector('.wr-explain'))return;
  const topic=label.textContent.replace(/[·—].*$/,'').trim();if(!topic)return;const b=document.createElement('button');b.type='button';b.className='wr-explain';b.textContent='Explain';
  const live=label.closest('.live-card');const question=live?'Explain what moved this account today, using the live data.':`Explain the ${topic.toLowerCase()} for this account as of the latest data.`;
  b.setAttribute('aria-label','Explain '+topic);b.addEventListener('click',()=>{open();ask(question)});label.appendChild(b)})};
 const alerts=()=>{const label=document.querySelector('.live-card > .label');if(!label||label.querySelector('.wr-alert'))return;
  inbox().then(d=>{const today=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Hong_Kong'});
   const n=(d.items||[]).filter(x=>x.kind==='live_alert'&&x.subject_id===account&&x.status==='new'&&String(x.period_key).startsWith(today)).length;
   if(!n||label.querySelector('.wr-alert'))return;const a=document.createElement('a');a.className='wr-explain wr-alert';a.href='/agent-inbox';a.textContent=n===1?'1 alert today':n+' alerts today';label.appendChild(a)})};
 explain();new MutationObserver(()=>explain()).observe(document.querySelector('main')||document.body,{childList:true,subtree:false});
 alerts();
}
})();"""



INBOX_DESCRIPTION = ("Weekly briefs, explanations, draft commentary and data checks written by the scheduled agents. "
                     "Nothing here has been sent or published. Approving a draft marks it ready for the normal report "
                     "release process.")

INBOX_STYLE = """<style>.inbox-item{margin-bottom:14px}.inbox-item h3{margin:4px 0 8px}.inbox-body p{margin:0 0 8px}
.inbox-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.inbox-pill{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12px;font-weight:600;border:1px solid var(--border,#dce2eb)}
.inbox-pill.new{background:rgba(214,69,80,.12);border-color:rgba(214,69,80,.35)}.inbox-pill.approved{background:rgba(46,158,107,.12);border-color:rgba(46,158,107,.35)}
.inbox-item.is-new{border-left:4px solid #d64550}.inbox-actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;align-items:center}
.inbox-filters{display:flex;flex-wrap:wrap;gap:12px;align-items:center}.inbox-filters label{display:flex;gap:6px;align-items:center}</style>"""

INBOX_BODY = """<div class="page-view active" data-section="agent-inbox"><div class="card wide"><div class="label">What the agents wrote</div><div id="inbox-summary" class="value" aria-live="polite">Loading…</div>
<div class="actions inbox-filters"><label>Show <select id="filter"><option value="">All</option><option value="new">New</option><option value="read">Read</option><option value="approved">Approved</option><option value="dismissed">Dismissed</option></select></label>
<label>Type <select id="kind"><option value="">All types</option></select></label>{MONTH_END}</div></div>
<section id="items" class="wide" style="min-width:0"><div class="card wide muted">Loading…</div></section></div>"""

INBOX_SCRIPT = r"""<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const md=s=>esc(s).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').split(/\n{2,}/).map(p=>'<p>'+p.replace(/\n/g,'<br>')+'</p>').join('');
const NAMES={NAMES};
const kinds={export_pack:'Export pack',weekly_brief:'Weekly brief',holdings_explainer:'What changed',live_alert:'Live alert',risk_alert:'Risk alert',model_check:'Model check',month_end_draft:'Draft commentary',data_quality:'Data check',month_end_report:'Month-end report',month_end_hold:'Account blocked',month_end_dispute:'Agents disagree'};
const kindLabel=k=>kinds[k]||String(k||'Update').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
const statuses={new:'New',read:'Read',approved:'Approved',dismissed:'Dismissed'};
const choices={month_end_hold:[['approved','Proceed'],['dismissed','Hold']],month_end_dispute:[['approved',"Keep the writer's text"],['dismissed','Agree with the reviewer']]};
const decided={month_end_hold:{approved:'You chose to proceed.',dismissed:'You chose to hold this account.'},month_end_dispute:{approved:"You kept the writer's text.",dismissed:'You agreed with the reviewer.'}};
const accountName=id=>NAMES[id]||id;
const link=x=>{if(x.kind==='export_pack')return `<a class="button" href="/accounts/${encodeURIComponent(x.subject_id)}/exports?pack_item=${encodeURIComponent(x.item_id)}">${x.status==='approved'?'Build again':'Build and check'}</a>`;
 const c=(x.citations||[]).find(c=>c&&typeof c.link==='string'&&/^\/accounts\/[^/]+\/reports$/.test(c.link));
 return c?`<a class="button" href="${esc(c.link)}">Open the Reports tab</a>`:(x.subject_id&&NAMES[x.subject_id]?`<a class="button" href="/accounts/${encodeURIComponent(x.subject_id)}/overview">Open account</a>`:'')};
const buttons=x=>{if(choices[x.kind]){const done=(decided[x.kind]||{})[x.status];return (done?`<span>${esc(done)}</span>`:choices[x.kind].map(([d,l])=>`<button class="button" data-d="${d}">${esc(l)}</button>`).join(''))+link(x)}
 return (x.status==='new'?'<button class="button" data-d="read">Mark read</button>':'')+(x.kind==='month_end_draft'&&x.status!=='approved'?'<button class="button" data-d="approved">Approve draft</button>':'')+link(x)+(x.status!=='dismissed'?'<button class="button" data-d="dismissed">Dismiss</button>':'')};
let all=[];
const render=()=>{const f=document.querySelector('#filter').value,k=document.querySelector('#kind').value,box=document.querySelector('#items');
 const items=all.filter(x=>(!f||x.status===f)&&(!k||x.kind===k)),fresh=all.filter(x=>x.status==='new').length;
 document.querySelector('#inbox-summary').textContent=fresh?`${fresh} new ${fresh===1?'item':'items'}`:'You are up to date';
 box.innerHTML=items.length?items.map(x=>`<article class="card wide inbox-item${x.status==='new'?' is-new':''}" data-id="${x.item_id}"><div class="inbox-head"><span class="label">${esc(kindLabel(x.kind))} · ${esc(accountName(x.subject_id))} · ${esc(x.period_key)}</span><span class="inbox-pill ${esc(x.status)}">${esc(statuses[x.status]||x.status)}</span></div><h3>${esc(x.title)}</h3><div class="inbox-body">${md(x.body)}</div><div class="inbox-actions">${buttons(x)}<span class="muted" role="status"></span></div></article>`).join('')
  :`<div class="card wide muted">${all.length?'Nothing matches this filter.':'Nothing here yet. Weekly briefs arrive on Saturdays, and the other agents post here when they have something for you.'}</div>`;
 box.querySelectorAll('[data-d]').forEach(b=>b.addEventListener('click',async()=>{const card=b.closest('[data-id]'),note=card.querySelector('[role=status]');card.querySelectorAll('button').forEach(x=>x.disabled=true);
  try{const r=await fetch(`/api/agents/inbox/${card.dataset.id}/decision`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:b.dataset.d})});
   if(!r.ok){let d='That did not go through. Try again.';try{d=(await r.json()).detail||d}catch(e){}throw new Error(d)}load()}
  catch(e){note.textContent=e.message;card.querySelectorAll('button').forEach(x=>x.disabled=false)}}))};
async function load(){const r=await fetch('/api/agents/inbox',{credentials:'same-origin'}).catch(()=>null);
 if(!r||!r.ok){document.querySelector('#items').innerHTML='<div class="card wide">The inbox could not be loaded. Refresh the page to try again.</div>';document.querySelector('#inbox-summary').textContent='Unavailable';return}
 all=((await r.json()).items||[]).map((x,i)=>[x,i]).sort((a,b)=>(a[0].status!=='new')-(b[0].status!=='new')||a[1]-b[1]).map(([x])=>x);const kind=document.querySelector('#kind'),current=kind.value,seen=[...new Set(all.map(x=>x.kind))];
 kind.innerHTML='<option value="">All types</option>'+seen.map(k=>`<option value="${esc(k)}">${esc(kindLabel(k))}</option>`).join('');kind.value=seen.includes(current)?current:'';
 render()}
document.querySelector('#filter').addEventListener('change',render);document.querySelector('#kind').addEventListener('change',render);load();
</script>"""

INBOX_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent inbox</title><style>{THEME}</style></head>
<body><header><strong class="brand-title">Agent inbox</strong><div class="session">{VIEWER} · <a href="/">Accounts</a></div></header>
<main><h1>Agent inbox</h1><section class="grid">{CONTENT}</section></main>{SCRIPT}</body></html>"""
