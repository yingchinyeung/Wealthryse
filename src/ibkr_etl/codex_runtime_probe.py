"""Synthetic staging-only Codex app-server probe with a bounded inference relay.

This does not expose arbitrary tasks, tools, files, an RPC endpoint or an owner
workspace. Azure credentials remain in the trusted parent, never the Codex
environment/arguments. The relay offers only one synthetic, tool-free turn.
The versioned app-server protocol is experimental, not production acceptance.
"""
from __future__ import annotations

import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERSION = '0.160.0'
DEPLOYMENT = 'gpt-5-6-sol-apac'
RESPONSES_URL = 'https://ibkrv2stg-ai-auhecwc4itaoo.openai.azure.com/openai/v1/responses'
CLIENT = '9475b7e9-94e3-4b91-abbf-3f0adc25a315'
REPLY = 'CODEX_RUNTIME_AZURE_OK'
PROMPT = 'Reply with exactly ' + REPLY + '. Do not use any tools or add any other text.'
MAX_BYTES = 262144


class ProbeError(ValueError):
    """Only fixed reason codes reach retained evidence."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def managed_token(environment):
    """ACA's registered local managed-identity endpoint, no CLI/key fallback."""
    endpoint = environment.get('IDENTITY_ENDPOINT', '')
    header = environment.get('IDENTITY_HEADER', '')
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise ProbeError('managed_identity_binding_invalid') from None
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
            or not port or parsed.path != '/msi/token'
            or parsed.query or parsed.fragment or parsed.username or parsed.password
            or not header or any(c.isspace() for c in header)
            or environment.get('AZURE_CLIENT_ID') != CLIENT):
        raise ProbeError('managed_identity_binding_invalid')
    query = urlencode({'api-version': '2019-08-01', 'resource': 'https://ai.azure.com',
                       'client_id': CLIENT})
    # ACA was verified to inject "localhost" here. Canonicalize the accepted
    # alias to the numeric loopback address instead of trusting DNS/hosts lookup.
    req = Request('http://127.0.0.1:' + str(port) + '/msi/token?' + query,
                  headers={'X-IDENTITY-HEADER': header})
    # No proxy or redirects for the local credential endpoint.
    from urllib.request import ProxyHandler
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=15) as response:
        raw = response.read(32769)
    if len(raw) > 32768:
        raise ProbeError('managed_identity_response_limit')
    value = json.loads(raw)
    token = value.get('access_token')
    if not isinstance(token, str) or not token or any(c.isspace() for c in token):
        raise ProbeError('managed_identity_token_invalid')
    return token


def restricted_payload(value):
    """One fixed prompt, pinned destination/model, no tools or persistent state.

    Built-in schemas advertised by Codex are deliberately NOT forwarded. This
    is a protocol/inference probe, not acceptance of tool execution/scoping.
    """
    if (not isinstance(value, dict) or value.get('model') != DEPLOYMENT
            or value.get('stream') is not True or value.get('store') is not False
            or value.get('previous_response_id') or value.get('conversation')):
        raise ProbeError('synthetic_request_contract_invalid')
    items = value.get('input')
    if not isinstance(items, list) or not items or len(items) > 12:
        raise ProbeError('synthetic_input_invalid')
    user_texts = []
    for item in items:
        if (not isinstance(item, dict) or item.get('type', 'message') != 'message'
                or item.get('role') not in {'user', 'developer', 'system'}):
            raise ProbeError('synthetic_input_item_denied')
        content = item.get('content')
        if not isinstance(content, list) or len(content) > 12:
            raise ProbeError('synthetic_input_content_denied')
        for part in content:
            if (not isinstance(part, dict) or part.get('type') != 'input_text'
                    or not isinstance(part.get('text'), str)):
                raise ProbeError('synthetic_attachment_denied')
            if item['role'] == 'user':
                user_texts.append(part['text'])
    if not user_texts or user_texts[-1] != PROMPT:
        raise ProbeError('synthetic_prompt_differs')
    allowed = {k: value[k] for k in ('instructions', 'input') if k in value}
    allowed.update(model=DEPLOYMENT, stream=True, store=False, tools=[],
                   tool_choice='none', max_output_tokens=512)
    if len(json.dumps(allowed).encode()) > MAX_BYTES:
        raise ProbeError('synthetic_request_limit')
    return allowed


def fixture_stream():
    message = dict(id='msg_synthetic', type='message', role='assistant', status='completed',
                   content=[dict(type='output_text', text=REPLY, annotations=[])])
    response = dict(id='resp_synthetic', object='response', status='completed',
                    model=DEPLOYMENT, output=[message],
                    usage=dict(input_tokens=1, output_tokens=1, total_tokens=2))
    events = [
        dict(type='response.created', response={**response, 'status':'in_progress', 'output':[]}),
        dict(type='response.output_item.added', output_index=0,
             item={**message, 'status':'in_progress', 'content':[]}),
        dict(type='response.content_part.added', item_id=message['id'], output_index=0,
             content_index=0, part=dict(type='output_text', text='', annotations=[])),
        dict(type='response.output_text.delta', item_id=message['id'], output_index=0,
             content_index=0, delta=REPLY),
        dict(type='response.output_text.done', item_id=message['id'], output_index=0,
             content_index=0, text=REPLY),
        dict(type='response.output_item.done', output_index=0, item=message),
        dict(type='response.completed', response=response),
    ]
    return b''.join(('event: '+v['type']+'\ndata: '+json.dumps(v)+'\n\n').encode() for v in events)


def validate_stream(raw):
    """Buffer and validate BEFORE passing any item to the Codex tool machinery."""
    if len(raw) > MAX_BYTES:
        raise ProbeError('upstream_response_limit')
    events = []
    for line in raw.decode().splitlines():
        if not line.startswith('data:'):
            continue
        data = line[5:].strip()
        if data == '[DONE]':
            continue
        event = json.loads(data)
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            raise ProbeError('upstream_event_invalid')
        item = event.get('item')
        if item is not None and (not isinstance(item, dict) or item.get('type') not in {'message', 'reasoning'}):
            raise ProbeError('upstream_tool_item_denied')
        if not event['type'].startswith('response.') or event['type'] in {'response.failed', 'response.incomplete', 'error'}:
            raise ProbeError('upstream_event_failed')
        # Unknown function/tool delta types must not reach the runtime either.
        if any(x in event['type'] for x in ('function', 'tool', 'code_interpreter', 'mcp')):
            raise ProbeError('upstream_tool_event_denied')
        events.append(event)
    terminal = [e for e in events if e['type'] == 'response.completed']
    if len(terminal) != 1 or events[-1] != terminal[0]:
        raise ProbeError('upstream_completion_invalid')
    response = terminal[0].get('response', {})
    if response.get('status') != 'completed' or not isinstance(response.get('output'), list):
        raise ProbeError('upstream_response_not_completed')
    texts = []
    for item in response['output']:
        if item.get('type') == 'reasoning':
            continue
        if item.get('type') != 'message' or item.get('role') != 'assistant':
            raise ProbeError('upstream_output_item_denied')
        for part in item.get('content', []):
            if part.get('type') != 'output_text' or not isinstance(part.get('text'), str):
                raise ProbeError('upstream_output_content_denied')
            texts.append(part['text'])
    if ''.join(texts).strip() != REPLY:
        raise ProbeError('upstream_synthetic_output_differs')
    return hashlib.sha256(raw).hexdigest()


class SyntheticRelay:
    def __init__(self, bearer=None, transport=None):
        self.capability = secrets.token_urlsafe(32)
        self._bearer = bearer
        self._transport = transport
        self.request_count = 0
        self.accepted_count = 0
        self.advertised_tool_count = None
        self.response_sha256 = None
        self.failure = None
        self._lock = threading.Lock()
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    self.connection.settimeout(5)
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= MAX_BYTES or self.headers.get('Transfer-Encoding'):
                        raise ProbeError('relay_request_limit')
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise ProbeError('relay_request_truncated')
                    if (self.path != '/openai/v1/responses'
                            or not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+relay.capability)
                            or self.headers.get('Transfer-Encoding')
                            or not self.headers.get('Content-Type', '').startswith('application/json')):
                        raise ProbeError('relay_access_denied')
                    # Consume the bounded body before closing a denied duplicate
                    # request, so clients receive the rejection rather than RST.
                    payload = json.loads(body)
                    with relay._lock:
                        relay.request_count += 1
                        if relay.request_count != 1:
                            raise ProbeError('relay_request_budget_exhausted')
                    restricted = relay._restrict_payload(payload)
                    relay.advertised_tool_count = len(payload.get('tools', []))
                    if relay._bearer:
                        req = Request(RESPONSES_URL, method='POST', data=json.dumps(restricted).encode(),
                                      headers={'Content-Type':'application/json', 'Authorization':'Bearer '+relay._bearer,
                                               'Accept':'text/event-stream'})
                        if relay._transport:
                            raw = relay._transport(req)
                        else:
                            with build_opener(NoRedirect()).open(req, timeout=60) as upstream:
                                if upstream.status != 200 or not upstream.headers.get('Content-Type', '').startswith('text/event-stream'):
                                    raise ProbeError('upstream_stream_unavailable')
                                raw = upstream.read(MAX_BYTES+1)
                    else:
                        raw = fixture_stream()
                    relay.response_sha256 = validate_stream(raw)
                    relay.accepted_count += 1
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except Exception as error:
                    # Do not log remote bodies, token values or user/SDK content.
                    relay.failure = str(error) if isinstance(error, ProbeError) else 'relay_transport_or_protocol_failed'
                    if isinstance(error, HTTPError):
                        relay.failure = 'upstream_http_' + str(error.code)
                        error.close()
                    body = b'{"error":{"message":"Synthetic probe relay rejected this request"}}'
                    self.send_response(403)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def do_GET(self):
                self.send_response(404)
                self.end_headers()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _restrict_payload(self, payload):
        # Default app-server path retains its original strict contract. The
        # separately pinned CLI probe may remove only its verified native tool
        # announcement, never forward it or permit actual tool execution.
        return restricted_payload(payload)

    @property
    def base_url(self):
        return 'http://127.0.0.1:' + str(self.server.server_port) + '/openai/v1'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self._bearer = None
        self.capability = ''


def child_environment(directory, capability):
    """No ambient credentials, proxies, user home config or identity endpoint."""
    return {'PATH':'/usr/local/bin:/usr/bin:/bin', 'LANG':'C.UTF-8',
            'HOME':str(directory/'home'), 'CODEX_HOME':str(directory/'codex-home'),
            'TMPDIR':str(directory/'tmp'), 'CODEX_AZURE_BROKER_TOKEN':capability}


def command(relay):
    settings = {
        'model':DEPLOYMENT, 'model_provider':'azure_synthetic_probe',
        'model_providers.azure_synthetic_probe.name':'Azure synthetic trial',
        'model_providers.azure_synthetic_probe.base_url':relay.base_url,
        'model_providers.azure_synthetic_probe.env_key':'CODEX_AZURE_BROKER_TOKEN',
        'model_providers.azure_synthetic_probe.wire_api':'responses',
        'model_providers.azure_synthetic_probe.request_max_retries':0,
        'model_providers.azure_synthetic_probe.stream_max_retries':0,
        'model_providers.azure_synthetic_probe.stream_idle_timeout_ms':70000,
        'sandbox_mode':'read-only', 'approval_policy':'never', 'web_search':'disabled',
        'project_root_markers':[], 'features.shell_tool':False, 'features.unified_exec':False,
        'features.multi_agent':False, 'features.apps':False, 'features.shell_snapshot':False,
        'features.skill_mcp_dependency_install':False, 'tools.view_image':False,
        'analytics.enabled':False, 'feedback.enabled':False,
    }
    args = ['/usr/local/bin/codex']
    for key, value in settings.items():
        args += ['-c', key+'='+json.dumps(value)]
    return args + ['app-server', '--listen', 'stdio://']


async def protocol_probe(relay, directory):
    env = child_environment(directory, relay.capability)
    proc = await asyncio.create_subprocess_exec(*command(relay), cwd=directory/'workspace', env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        limit=MAX_BYTES)
    result = dict(handshake_verified=False, turn_completed=False, streamed_delta_observed=False,
                  runtime_output_verified=False, tool_execution_authorized=False)
    thread_id = None
    turn_id = None
    messages = []
    total_bytes = 0

    async def send(value):
        proc.stdin.write((json.dumps(value)+'\n').encode())
        await proc.stdin.drain()

    try:
        await send(dict(id=0, method='initialize', params=dict(clientInfo=dict(
            name='wealthryse_synthetic_runtime_probe', title='WealthRyse isolated compatibility test', version='1.0'))))
        deadline = time.monotonic() + 80
        for _ in range(300):
            raw = await asyncio.wait_for(proc.stdout.readline(), max(0.1, deadline-time.monotonic()))
            total_bytes += len(raw)
            if not raw or total_bytes > 2*MAX_BYTES:
                raise ProbeError('rpc_output_limit_or_eof')
            value = json.loads(raw)
            if 'error' in value:
                stage = {0:'initialize', 1:'thread_start', 2:'turn_start'}.get(value.get('id'), 'unknown')
                raise ProbeError('rpc_request_rejected_'+stage)
            if 'method' in value and 'id' in value:
                # No server request can dispatch a tool, approval, shell or permission.
                await send(dict(id=value['id'], error=dict(code=-32601, message='No tool execution in synthetic probe')))
                raise ProbeError('rpc_server_request_denied')
            if value.get('id') == 0:
                if not isinstance(value.get('result'), dict):
                    raise ProbeError('rpc_initialize_invalid')
                result['handshake_verified'] = True
                await send(dict(method='initialized', params={}))
                await send(dict(id=1, method='thread/start', params=dict(model=DEPLOYMENT,
                    cwd=str(directory/'workspace'), approvalPolicy='never', sandbox='read-only')))
            elif value.get('id') == 1:
                thread_id = value.get('result', {}).get('thread', {}).get('id')
                if not isinstance(thread_id, str) or not thread_id:
                    raise ProbeError('rpc_thread_invalid')
                await send(dict(id=2, method='turn/start', params=dict(threadId=thread_id,
                           input=[dict(type='text', text=PROMPT)])))
            elif value.get('id') == 2:
                turn_id = value.get('result', {}).get('turn', {}).get('id')
                if not isinstance(turn_id, str) or not turn_id:
                    raise ProbeError('rpc_turn_invalid')
            elif value.get('method') == 'item/agentMessage/delta':
                p = value.get('params', {})
                if p.get('threadId') != thread_id or p.get('turnId') != turn_id:
                    raise ProbeError('rpc_delta_scope_invalid')
                result['streamed_delta_observed'] = True
            elif value.get('method') == 'item/completed':
                p = value.get('params', {})
                item = p.get('item', {})
                if p.get('threadId') != thread_id or p.get('turnId') != turn_id:
                    raise ProbeError('rpc_item_scope_invalid')
                if item.get('type') == 'agentMessage':
                    messages.append(item.get('text', ''))
                elif item.get('type') not in {'userMessage', 'reasoning'}:
                    raise ProbeError('rpc_non_message_item_denied')
            elif value.get('method') == 'turn/completed':
                p = value.get('params', {})
                if (p.get('threadId') != thread_id or p.get('turn', {}).get('id') != turn_id
                        or p['turn'].get('status') != 'completed' or p['turn'].get('error')):
                    raise ProbeError('rpc_turn_not_completed')
                result['turn_completed'] = True
                result['runtime_output_verified'] = ''.join(messages).strip() == REPLY
                return result
            if time.monotonic() >= deadline:
                raise ProbeError('rpc_deadline')
        raise ProbeError('rpc_event_count_limit')
    finally:
        # This runner owns the child only; nothing outside this isolated trial.
        if proc.returncode is None:
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except TimeoutError:
            proc.kill()
            await proc.wait()
