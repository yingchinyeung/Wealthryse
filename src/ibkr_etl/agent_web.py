"""Website side of the WealthRyse agents: chat panel, "Explain" buttons and inbox.

Off unless AGENT_UI_MODE is "owner" (owners only) or "all". The website
never talks to a model itself: it forwards the signed-in person's request to
the internal agent service, signing in with its own managed identity and
naming the person, and streams the validated answer back.

Settings: AGENT_UI_MODE, AGENT_API_URL (internal https URL of the agent
service), AGENT_API_AUDIENCE (its Entra app ID URI), AZURE_CLIENT_ID (the
website identity, already set).
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
        client = httpx.Client(timeout=httpx.Timeout(90.0, connect=10.0))
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

    @app.get("/api/agents/inbox")
    def agent_inbox(user: dict[str, Any] = Depends(agent_user)) -> Any:
        return api.request("GET", "/v1/inbox", params={"user_object_id": oid(user)})

    @app.post("/api/agents/inbox/{item_id}/decision")
    def agent_decision(item_id: int, body: DecisionBody, user: dict[str, Any] = Depends(agent_user)) -> Any:
        return api.request("POST", f"/v1/inbox/{item_id}/decision", json={"user_object_id": oid(user), **body.model_dump()})

    @app.get("/agents/widget.js")
    def agent_widget() -> Response:
        return Response(WIDGET_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.get("/agent-inbox", response_class=HTMLResponse)
    def agent_inbox_page(user: dict[str, Any] = Depends(agent_user)) -> str:
        return INBOX_PAGE.replace("{THEME}", theme_css).replace("{VIEWER}", escape(str(user.get("email") or "")))

    if settings.mode == "off":
        return

    @app.middleware("http")
    async def agent_widget_injector(request: Request, call_next):
        response = await call_next(request)
        if (request.method != "GET" or response.status_code != 200 or not WIDGET_PATHS.match(request.url.path)
                or not response.headers.get("content-type", "").startswith("text/html")):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        marker = body.rfind(b"</body>")
        if marker >= 0:
            body = body[:marker] + b'<script src="/agents/widget.js" defer></script>' + body[marker:]
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(body, status_code=response.status_code, headers=headers, media_type="text/html")


WIDGET_JS = r"""(()=>{
'use strict';
const m=location.pathname.match(/^\/accounts\/([^/]+)\/(overview|holdings|performance)$/);if(!m)return;
const account=decodeURIComponent(m[1]);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const md=s=>esc(s).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\[(T\d+)\]/g,'<sup class="wr-cite">$1</sup>')
 .split(/\n{2,}/).map(p=>/^\s*[-•] /m.test(p)?'<ul>'+p.split('\n').filter(l=>l.trim()).map(l=>'<li>'+l.replace(/^\s*[-•] /,'')+'</li>').join('')+'</ul>':'<p>'+p.replace(/\n/g,'<br>')+'</p>').join('');
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
@media (max-width:600px){.wr-panel{right:16px;left:16px;width:auto;bottom:76px}.wr-fab{right:16px;bottom:16px}}`;
fetch('/api/agents/status',{credentials:'same-origin'}).then(r=>r.ok?r.json():{enabled:false}).then(s=>{if(!s.enabled)return;init()}).catch(()=>{});
function init(){
 const style=document.createElement('style');style.textContent=css;document.head.appendChild(style);
 const fab=document.createElement('button');fab.className='wr-fab';fab.type='button';fab.textContent='Ask about this account';
 const panel=document.createElement('section');panel.className='wr-panel';panel.setAttribute('aria-label','Portfolio analyst');
 panel.innerHTML=`<div class="wr-head"><strong>Portfolio analyst</strong><span><a href="/agent-inbox">Inbox</a> · <button type="button" data-new>New chat</button> · <button type="button" data-close aria-label="Close">✕</button></span></div><div class="wr-log" aria-live="polite"></div><form class="wr-form"><textarea maxlength="2000" placeholder="Ask about holdings or performance…" aria-label="Your question"></textarea><button type="submit">Send</button></form>`;
 document.body.append(fab,panel);
 const log=panel.querySelector('.wr-log'),form=panel.querySelector('form'),input=form.querySelector('textarea'),send=form.querySelector('button');
 let conversation=store.get(key),busy=false;
 const add=(cls,html)=>{const d=document.createElement('div');d.className='wr-msg '+cls;d.innerHTML=html;log.appendChild(d);log.scrollTop=log.scrollHeight;return d};
 const empty=()=>{log.innerHTML='';add('wr-bot','<p>Ask what changed, how the account performed against its benchmark, or what drives its sector mix. Every figure comes from your validated data.</p>')};
 const sources=c=>c&&c.length?'<div class="wr-sources">Sources: '+c.map(x=>esc(x.id)+' '+esc((x.tool||'').replace(/_/g,' '))+(x.as_of?' ('+esc(x.as_of)+')':'')).join(' · ')+'</div>':'';
 const rate=(node,id)=>{if(!id)return;const r=document.createElement('div');r.className='wr-rate';r.innerHTML='<button type="button" aria-label="Helpful">👍</button><button type="button" aria-label="Not helpful">👎</button>';
  r.querySelectorAll('button').forEach((b,i)=>b.addEventListener('click',()=>{fetch(`/api/agents/messages/${id}/feedback`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({rating:i?-1:1})}).then(()=>{r.textContent='Thanks for the feedback.'})}));node.appendChild(r)};
 async function load(){empty();if(!conversation)return;try{const r=await fetch(`/api/agents/conversations/${encodeURIComponent(conversation)}`,{credentials:'same-origin'});if(!r.ok){conversation=null;store.set(key,null);return}const c=await r.json();log.innerHTML='';
  for(const x of c.messages){if(x.role==='user')add('wr-user',md(x.content));else if(x.status==='ok'){const n=add('wr-bot',md(x.content)+sources(x.citations));rate(n,x.message_id)}}}catch(e){}}
 async function ask(question){if(busy||!question.trim())return;busy=true;send.disabled=true;add('wr-user',md(question));const status=add('wr-bot wr-status','Thinking…');
  try{const r=await fetch('/api/agents/chat',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({account_id:account,message:question,conversation_id:conversation})});
   if(!r.ok){let d='The analyst could not answer.';try{d=(await r.json()).detail||d}catch(e){}status.className='wr-msg wr-bot';status.textContent=d;return}
   const reader=r.body.getReader(),dec=new TextDecoder();let buf='';
   for(;;){const {value,done}=await reader.read();if(done)break;buf+=dec.decode(value,{stream:true});let i;
    while((i=buf.indexOf('\n\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+2);if(!line.startsWith('data: '))continue;const e=JSON.parse(line.slice(6));
     if(e.type==='start'){conversation=e.conversation_id;store.set(key,conversation)}
     else if(e.type==='status'){status.textContent=e.text+'…'}
     else if(e.type==='answer'||e.type==='rejected'){status.className='wr-msg wr-bot';status.innerHTML=md(e.answer)+sources(e.citations);rate(status,e.message_id)}
     else if(e.type==='error'){status.className='wr-msg wr-bot';status.textContent=e.message}}}
  }catch(e){status.className='wr-msg wr-bot';status.textContent='The connection dropped. Try again.'}
  finally{busy=false;send.disabled=false;log.scrollTop=log.scrollHeight}}
 const open=()=>{if(!panel.classList.contains('open')){panel.classList.add('open');load()}input.focus()};
 fab.addEventListener('click',()=>panel.classList.contains('open')?panel.classList.remove('open'):open());
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
</style></head><body><header><strong class="brand-title">Agent inbox</strong><div class="session">{VIEWER} · <a href="/">Accounts</a></div></header>
<main><h1>Agent inbox</h1><p class="muted">Explanations, draft commentary and data checks written by the scheduled agents. Nothing here has been sent or published. Approving a draft marks it ready for the normal report release process.</p>
<div class="actions"><label>Show <select id="filter"><option value="">All</option><option value="new">New</option><option value="approved">Approved</option><option value="dismissed">Dismissed</option></select></label></div>
<section id="items" class="grid"><div class="card wide muted">Loading…</div></section></main>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const md=s=>esc(s).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').split(/\n{2,}/).map(p=>'<p>'+p.replace(/\n/g,'<br>')+'</p>').join('');
const kinds={holdings_explainer:'What changed',month_end_draft:'Draft commentary',data_quality:'Data check'};
async function load(){const r=await fetch('/api/agents/inbox',{credentials:'same-origin'});const box=document.querySelector('#items');
 if(!r.ok){box.innerHTML='<div class="card wide">The inbox could not be loaded.</div>';return}
 const f=document.querySelector('#filter').value;const items=(await r.json()).items.filter(x=>!f||x.status===f);
 box.innerHTML=items.length?items.map(x=>`<article class="card wide inbox-item" data-id="${x.item_id}"><div class="label">${esc(kinds[x.kind]||x.kind)} · ${esc(x.subject_id)} · ${esc(x.period_key)}</div><h3>${esc(x.title)}</h3><div class="inbox-body">${md(x.body)}</div><div class="inbox-meta">Status: ${esc(x.status)}</div><div class="inbox-actions">${x.status==='new'?'<button class="button" data-d="read">Mark read</button>':''}${x.kind==='month_end_draft'&&x.status!=='approved'?'<button class="button" data-d="approved">Approve draft</button>':''}${x.status!=='dismissed'?'<button class="button" data-d="dismissed">Dismiss</button>':''}</div></article>`).join(''):'<div class="card wide muted">Nothing here yet.</div>';
 box.querySelectorAll('[data-d]').forEach(b=>b.addEventListener('click',async()=>{const id=b.closest('[data-id]').dataset.id;b.disabled=true;
  await fetch(`/api/agents/inbox/${id}/decision`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:b.dataset.d})});load()}))}
document.querySelector('#filter').addEventListener('change',load);load();
</script></body></html>"""
