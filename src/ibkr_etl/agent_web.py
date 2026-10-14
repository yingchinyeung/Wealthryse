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

import json
import os
import re
import threading
from html import escape
from typing import Any, Callable, Iterable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import month_team_web

WIDGET_PATHS = re.compile(r"^/accounts/[^/]+/(overview|holdings|performance)$")
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

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        import httpx

        if not self.settings.url or not self.settings.audience:
            raise HTTPException(status_code=503, detail="The analyst is not configured.")
        response = httpx.request(method, self.settings.url + path, headers=self._headers(), timeout=30.0, **kwargs)
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


def install(app: FastAPI, authorized_user: Callable[..., dict[str, Any]],
            allowed_accounts: Callable[[dict[str, Any]], list[str]], theme_css: str = "") -> None:
    """Add the agent routes and the widget to the website. No-op routes when off."""
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
        month_link = ' · <a href="/month-end">Month-end</a>' if month_team_web.is_month_owner(user) else ""
        return (INBOX_PAGE.replace("{THEME}", theme_css).replace("{MONTH_END}", month_link)
                .replace("{VIEWER}", escape(str(user.get("email") or ""))))

    month_team_web.install(app, authorized_user, allowed_accounts, theme_css, api)
    scripts = []
    if settings.mode != "off":
        scripts.append((WIDGET_PATHS, b'<script src="/agents/widget.js" defer></script>'))
    if month_team_web.enabled():
        scripts.append((month_team_web.PANEL_PATHS, b'<script src="/agents/month-team.js" defer></script>'))
    if not scripts:
        return

    @app.middleware("http")
    async def agent_widget_injector(request: Request, call_next):
        response = await call_next(request)
        tags = [tag for paths, tag in scripts if paths.match(request.url.path)]
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
const m=location.pathname.match(/^\/accounts\/([^/]+)\/(overview|holdings|performance)$/);if(!m)return;
const account=decodeURIComponent(m[1]);
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
const css=`.wr-fab{position:fixed;right:20px;bottom:20px;z-index:50;border:0;border-radius:999px;padding:12px 18px;font:inherit;font-weight:600;cursor:pointer;background:var(--accent,#1f6feb);color:#fff;box-shadow:0 6px 20px rgba(0,0,0,.2)}
.wr-panel{position:fixed;right:20px;bottom:80px;z-index:50;width:min(420px,calc(100vw - 32px));height:min(620px,calc(100vh - 120px));display:none;flex-direction:column;background:var(--card,#fff);color:var(--text,#111);border:1px solid var(--border,#ddd);border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.25);overflow:hidden}
.wr-panel.open{display:flex}.wr-head{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid var(--border,#ddd)}
.wr-head a,.wr-head button{font:inherit;font-size:13px;background:none;border:0;color:var(--accent,#1f6feb);cursor:pointer;text-decoration:none}
.wr-log{flex:1;overflow:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px}
.wr-msg{padding:10px 12px;border-radius:10px;line-height:1.45;font-size:14px}.wr-msg p{margin:0 0 6px}.wr-msg ul{margin:4px 0 6px 18px;padding:0}
.wr-user{align-self:flex-end;background:var(--accent,#1f6feb);color:#fff;max-width:85%}.wr-bot{background:var(--surface,#f4f5f7);max-width:95%}
.wr-status{font-size:12px;opacity:.7}.wr-cite{font-size:10px;opacity:.75}.wr-sources{font-size:11px;opacity:.75;margin-top:6px}
.wr-rate{margin-top:6px;display:flex;gap:6px}.wr-rate button{border:1px solid var(--border,#ccc);background:none;border-radius:6px;cursor:pointer;padding:1px 6px;color:inherit}
.wr-form{display:flex;gap:8px;padding:10px;border-top:1px solid var(--border,#ddd)}.wr-form textarea{flex:1;resize:none;height:44px;font:inherit;font-size:14px;padding:8px;border-radius:8px;border:1px solid var(--border,#ccc);background:transparent;color:inherit}
.wr-form button{border:0;border-radius:8px;padding:0 14px;font:inherit;font-weight:600;cursor:pointer;background:var(--accent,#1f6feb);color:#fff}
.wr-explain{margin-left:8px;font-size:12px;border:1px solid var(--border,#ccc);background:none;border-radius:999px;padding:1px 8px;cursor:pointer;color:inherit;vertical-align:middle}
.wr-panel.wide{width:min(760px,calc(100vw - 32px));height:min(780px,calc(100vh - 120px))}
.wr-visual{margin:8px 0 4px;padding:0}.wr-visual figcaption{font-weight:600;font-size:13px;margin-bottom:4px}.wr-chart{width:100%;height:auto;display:block;overflow:visible}
.wr-chart .wr-tick{font-size:10px;fill:currentColor;opacity:.75}.wr-chart .wr-grid{stroke:currentColor;opacity:.12}.wr-chart .wr-axis{stroke:currentColor;opacity:.45}
.wr-legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:11px;margin-top:4px}.wr-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:-1px}.wr-legend.wr-col{flex-direction:column;gap:3px}
.wr-pie{display:flex;gap:12px;align-items:center}.wr-pie svg{width:140px;flex:none}
.wr-tablewrap{overflow:auto;max-height:340px}.wr-table{border-collapse:collapse;font-size:12px;width:100%}.wr-table th,.wr-table td{padding:3px 6px;border-bottom:1px solid var(--border,#ddd);text-align:right;white-space:nowrap}.wr-table th:first-child{text-align:left}.wr-table thead th{position:sticky;top:0;background:var(--surface,#f4f5f7)}
@media (max-width:600px){.wr-panel{right:16px;left:16px;width:auto;bottom:76px}.wr-fab{right:16px;bottom:16px}}`;
fetch('/api/agents/status',{credentials:'same-origin'}).then(r=>r.ok?r.json():{enabled:false}).then(s=>{if(!s.enabled)return;init()}).catch(()=>{});
function init(){
 const style=document.createElement('style');style.textContent=css;document.head.appendChild(style);
 const fab=document.createElement('button');fab.className='wr-fab';fab.type='button';fab.textContent='Ask about this account';
 const panel=document.createElement('section');panel.className='wr-panel';panel.setAttribute('aria-label','Portfolio analyst');
 panel.innerHTML=`<div class="wr-head"><strong>Portfolio analyst</strong><span><a href="/agent-inbox">Inbox</a> · <button type="button" data-new>New chat</button> · <button type="button" data-wide aria-label="Wider panel">⤢</button> · <button type="button" data-close aria-label="Close">✕</button></span></div><div class="wr-log" aria-live="polite"></div><form class="wr-form"><textarea maxlength="2000" placeholder="Ask about holdings or performance…" aria-label="Your question"></textarea><button type="submit">Send</button></form>`;
 document.body.append(fab,panel);
 const log=panel.querySelector('.wr-log'),form=panel.querySelector('form'),input=form.querySelector('textarea'),send=form.querySelector('button');
 let conversation=store.get(key),busy=false;
 const add=(cls,html)=>{const d=document.createElement('div');d.className='wr-msg '+cls;d.innerHTML=html;log.appendChild(d);log.scrollTop=log.scrollHeight;return d};
 const empty=()=>{log.innerHTML='';add('wr-bot','<p>Ask what changed, how the account performed against its benchmark, or what drives its sector mix. Every figure comes from your validated data.</p>')};
 const sources=c=>c&&c.length?'<div class="wr-sources">Sources: '+c.map(x=>esc(x.id)+' '+esc((x.tool||'').replace(/_/g,' '))+(x.as_of?' ('+esc(x.as_of)+')':'')).join(' · ')+'</div>':'';
 const rate=(node,id)=>{if(!id)return;const r=document.createElement('div');r.className='wr-rate';r.innerHTML='<button type="button" aria-label="Helpful">👍</button><button type="button" aria-label="Not helpful">👎</button>';
  r.querySelectorAll('button').forEach((b,i)=>b.addEventListener('click',()=>{fetch(`/api/agents/messages/${id}/feedback`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({rating:i?-1:1})}).then(()=>{r.textContent='Thanks for the feedback.'})}));node.appendChild(r)};
 async function load(){empty();if(!conversation)return;try{const r=await fetch(`/api/agents/conversations/${encodeURIComponent(conversation)}`,{credentials:'same-origin'});if(!r.ok){conversation=null;store.set(key,null);return}const c=await r.json();log.innerHTML='';
  for(const x of c.messages){if(x.role==='user')add('wr-user',md(x.content));else if(x.status==='ok'){const n=add('wr-bot',md(x.content)+visuals(x.visuals)+sources(x.citations));rate(n,x.message_id)}}}catch(e){}}
 async function ask(question){if(busy||!question.trim())return;busy=true;send.disabled=true;add('wr-user',md(question));const status=add('wr-bot wr-status','Thinking… deeper questions can take a few minutes.');
  try{const r=await fetch('/api/agents/chat',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({account_id:account,message:question,conversation_id:conversation})});
   if(!r.ok){let d='The analyst could not answer.';try{d=(await r.json()).detail||d}catch(e){}status.className='wr-msg wr-bot';status.textContent=d;return}
   const reader=r.body.getReader(),dec=new TextDecoder();let buf='';
   for(;;){const {value,done}=await reader.read();if(done)break;buf+=dec.decode(value,{stream:true});let i;
    while((i=buf.indexOf('\n\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+2);if(!line.startsWith('data: '))continue;const e=JSON.parse(line.slice(6));
     if(e.type==='start'){conversation=e.conversation_id;store.set(key,conversation)}
     else if(e.type==='status'){status.textContent=e.text+'…'}
     else if(e.type==='answer'||e.type==='rejected'){status.className='wr-msg wr-bot';status.innerHTML=md(e.answer)+visuals(e.visuals)+sources(e.citations);rate(status,e.message_id)}
     else if(e.type==='error'){status.className='wr-msg wr-bot';status.textContent=e.message}}}
  }catch(e){status.className='wr-msg wr-bot';status.textContent='The connection dropped. Try again.'}
  finally{busy=false;send.disabled=false;log.scrollTop=log.scrollHeight}}
 const open=()=>{if(!panel.classList.contains('open')){panel.classList.add('open');load()}input.focus()};
 fab.addEventListener('click',()=>panel.classList.contains('open')?panel.classList.remove('open'):open());
 panel.querySelector('[data-wide]').addEventListener('click',()=>panel.classList.toggle('wide'));
 panel.querySelector('[data-close]').addEventListener('click',()=>panel.classList.remove('open'));
 panel.querySelector('[data-new]').addEventListener('click',()=>{conversation=null;store.set(key,null);empty();input.focus()});
 form.addEventListener('submit',e=>{e.preventDefault();const q=input.value;input.value='';ask(q)});
 input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();form.requestSubmit()}});
 const explain=()=>{document.querySelectorAll('.page-view.active .card > .label').forEach((label,n)=>{if(n>=6||label.querySelector('.wr-explain'))return;
  const topic=label.textContent.replace(/[·—].*$/,'').trim();if(!topic)return;const b=document.createElement('button');b.type='button';b.className='wr-explain';b.textContent='Explain';
  b.setAttribute('aria-label','Explain '+topic);b.addEventListener('click',()=>{open();ask(`Explain the ${topic.toLowerCase()} for this account as of the latest data.`)});label.appendChild(b)})};
 explain();new MutationObserver(()=>explain()).observe(document.querySelector('main')||document.body,{childList:true,subtree:false});
}
})();"""


INBOX_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent inbox</title><style>{THEME}
.inbox-item{margin-bottom:14px}.inbox-item h3{margin:4px 0 8px}.inbox-body p{margin:0 0 8px}.inbox-meta{font-size:13px;opacity:.75}.inbox-actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
</style></head><body><header><strong class="brand-title">Agent inbox</strong><div class="session">{VIEWER}{MONTH_END} · <a href="/">Accounts</a></div></header>
<main><h1>Agent inbox</h1><p class="muted">Explanations, draft commentary and data checks written by the scheduled agents. Nothing here has been sent or published. Approving a draft marks it ready for the normal report release process.</p>
<div class="actions"><label>Show <select id="filter"><option value="">All</option><option value="new">New</option><option value="approved">Approved</option><option value="dismissed">Dismissed</option></select></label></div>
<section id="items" class="grid"><div class="card wide muted">Loading…</div></section></main>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const md=s=>esc(s).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').split(/\n{2,}/).map(p=>'<p>'+p.replace(/\n/g,'<br>')+'</p>').join('');
const kinds={holdings_explainer:'What changed',month_end_draft:'Draft commentary',data_quality:'Data check',month_end_report:'Month-end report',month_end_hold:'Account blocked',month_end_dispute:'Agents disagree'};
const choices={month_end_hold:[['approved','Proceed'],['dismissed','Hold']],month_end_dispute:[['approved',"Keep the writer's text"],['dismissed','Agree with the reviewer']]};
const decided={month_end_hold:{approved:'You chose to proceed.',dismissed:'You chose to hold this account.'},month_end_dispute:{approved:"You kept the writer's text.",dismissed:'You agreed with the reviewer.'}};
const link=x=>{const c=(x.citations||[]).find(c=>c&&typeof c.link==='string'&&/^\/accounts\/[^/]+\/reports$/.test(c.link));return c?`<a class="button" href="${esc(c.link)}">Open the Reports tab</a>`:''};
const buttons=x=>{if(choices[x.kind]){const done=(decided[x.kind]||{})[x.status];return (done?`<span>${esc(done)}</span>`:choices[x.kind].map(([d,l])=>`<button class="button" data-d="${d}">${esc(l)}</button>`).join(''))+link(x)}
 return (x.status==='new'?'<button class="button" data-d="read">Mark read</button>':'')+(x.kind==='month_end_draft'&&x.status!=='approved'?'<button class="button" data-d="approved">Approve draft</button>':'')+link(x)+(x.status!=='dismissed'?'<button class="button" data-d="dismissed">Dismiss</button>':'')};
async function load(){const r=await fetch('/api/agents/inbox',{credentials:'same-origin'});const box=document.querySelector('#items');
 if(!r.ok){box.innerHTML='<div class="card wide">The inbox could not be loaded.</div>';return}
 const f=document.querySelector('#filter').value;const items=(await r.json()).items.filter(x=>!f||x.status===f);
 box.innerHTML=items.length?items.map(x=>`<article class="card wide inbox-item" data-id="${x.item_id}"><div class="label">${esc(kinds[x.kind]||x.kind)} · ${esc(x.subject_id)} · ${esc(x.period_key)}</div><h3>${esc(x.title)}</h3><div class="inbox-body">${md(x.body)}</div><div class="inbox-meta">Status: ${esc(x.status)}</div><div class="inbox-actions">${buttons(x)}</div></article>`).join(''):'<div class="card wide muted">Nothing here yet.</div>';
 box.querySelectorAll('[data-d]').forEach(b=>b.addEventListener('click',async()=>{const id=b.closest('[data-id]').dataset.id;b.disabled=true;
  await fetch(`/api/agents/inbox/${id}/decision`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:b.dataset.d})});load()}))}
document.querySelector('#filter').addEventListener('change',load);load();
</script></body></html>"""
