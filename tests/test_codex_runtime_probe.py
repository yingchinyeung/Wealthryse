import copy
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ibkr_etl.codex_runtime_probe import (CLIENT, DEPLOYMENT, PROMPT, REPLY, MAX_BYTES,
    ProbeError, SyntheticRelay, child_environment, command, fixture_stream,
    managed_token, restricted_payload, validate_stream, protocol_probe)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_codex_runtime_probe import manifest


def payload():
    return dict(model=DEPLOYMENT, store=False, stream=True,
                input=[dict(type='message', role='user', content=[dict(type='input_text',text=PROMPT)])],
                instructions='Synthetic fixture only', tools=[dict(type='function', name='shell')],
                max_output_tokens=999999, metadata={'not':'forwarded'})


class FakeProcess:
    def __init__(self, messages):
        self.messages=list(messages);self.writes=[];self.returncode=None
        self.stdin=self;self.stdout=self
    def write(self,raw):self.writes.append(json.loads(raw))
    async def drain(self):pass
    async def readline(self):
        if not self.messages:return b''
        return (json.dumps(self.messages.pop(0))+'\n').encode()
    def terminate(self):self.returncode=0
    def kill(self):self.returncode=-9
    async def wait(self):return self.returncode


def rpc_fixture():
    return [dict(id=0,result={}),dict(id=1,result=dict(thread=dict(id='thread1'))),
            dict(id=2,result=dict(turn=dict(id='turn1'))),
            dict(method='item/agentMessage/delta',params=dict(threadId='thread1',turnId='turn1',delta=REPLY)),
            dict(method='item/completed',params=dict(threadId='thread1',turnId='turn1',
                item=dict(type='agentMessage',text=REPLY))),
            dict(method='turn/completed',params=dict(threadId='thread1',
                turn=dict(id='turn1',status='completed',error=None)))]


class CodexRuntimeProbeTests(unittest.TestCase):
    def test_rpc_lifecycle_uses_pinned_enum_and_only_three_requests(self):
        process=FakeProcess(rpc_fixture())
        with SyntheticRelay() as relay, patch('ibkr_etl.codex_runtime_probe.asyncio.create_subprocess_exec',return_value=process):
            result=asyncio.run(protocol_probe(relay,Path('/tmp/synthetic')))
        self.assertTrue(result['runtime_output_verified'])
        self.assertTrue(result['streamed_delta_observed'])
        self.assertEqual([v['method'] for v in process.writes],['initialize','initialized','thread/start','turn/start'])
        self.assertEqual(process.writes[2]['params']['sandbox'],'read-only')
        self.assertNotIn('ephemeral',process.writes[2]['params'])
        self.assertEqual(process.returncode,0)

    def test_rpc_unexpected_server_request_is_denied_and_child_stopped(self):
        messages=rpc_fixture()[:3]+[dict(id=99,method='item/tool/call',params={'tool':'shell'})]
        process=FakeProcess(messages)
        with SyntheticRelay() as relay, patch('ibkr_etl.codex_runtime_probe.asyncio.create_subprocess_exec',return_value=process):
            with self.assertRaisesRegex(ProbeError,'rpc_server_request_denied'):
                asyncio.run(protocol_probe(relay,Path('/tmp/synthetic')))
        self.assertEqual(process.writes[-1]['error']['code'],-32601)
        self.assertEqual(process.returncode,0)

    def test_rpc_cross_thread_event_and_failed_turn_denied(self):
        for change in [lambda m:m[3]['params'].update(threadId='other'),
                       lambda m:m[-1]['params']['turn'].update(status='failed')]:
            messages=rpc_fixture();change(messages);process=FakeProcess(messages)
            with SyntheticRelay() as relay, patch('ibkr_etl.codex_runtime_probe.asyncio.create_subprocess_exec',return_value=process):
                with self.assertRaises(ProbeError):asyncio.run(protocol_probe(relay,Path('/tmp/synthetic')))
            self.assertEqual(process.returncode,0)

    def test_rpc_error_message_is_not_retained(self):
        messages=rpc_fixture()[:1]+[dict(id=1,error=dict(code=-32602,message='sensitive content'))]
        process=FakeProcess(messages)
        with SyntheticRelay() as relay, patch('ibkr_etl.codex_runtime_probe.asyncio.create_subprocess_exec',return_value=process):
            with self.assertRaisesRegex(ProbeError,'rpc_request_rejected_thread_start') as error:
                asyncio.run(protocol_probe(relay,Path('/tmp/synthetic')))
        self.assertNotIn('sensitive',str(error.exception))

    def test_exact_tool_free_payload_and_fixed_budget(self):
        value = payload()
        before = copy.deepcopy(value)
        result = restricted_payload(value)
        self.assertEqual(value, before)
        self.assertEqual(result['tools'], [])
        self.assertEqual(result['tool_choice'], 'none')
        self.assertFalse(result['store'])
        self.assertEqual(result['max_output_tokens'], 512)
        self.assertNotIn('metadata', result)

    def test_changed_model_prompt_persistence_attachment_and_continuation_denied(self):
        changes = [lambda p:p.update(model='other'), lambda p:p.update(store=True),
                   lambda p:p.update(stream=1), lambda p:p.update(previous_response_id='resp'),
                   lambda p:p.update(conversation='someone-else'), lambda p:p.update(input=[]),
                   lambda p:p['input'][0].update(type='function_call_output'),
                   lambda p:p['input'][0]['content'][0].update(type='input_image'),
                   lambda p:p['input'][0]['content'][0].update(text='Run code'),
                   lambda p:p.update(instructions='x'*(MAX_BYTES+1))]
        for change in changes:
            with self.subTest(change=change):
                value=payload(); change(value)
                with self.assertRaises(ProbeError): restricted_payload(value)

    def test_no_ambient_credentials_in_child_or_arguments(self):
        env=child_environment(Path('/tmp/probe-unique'), 'opaque-local-capability')
        self.assertEqual(set(env), {'PATH','LANG','HOME','CODEX_HOME','TMPDIR','CODEX_AZURE_BROKER_TOKEN'})
        with SyntheticRelay('actual-secret-never-exposed') as relay:
            args=command(relay)
            self.assertNotIn('actual-secret-never-exposed', str(args))
            self.assertNotIn(relay.capability, str(args))
            self.assertIn('app-server', args)
            self.assertIn('stdio://', args)
            self.assertIn('features.shell_tool=false', args)
        self.assertNotIn('IDENTITY_ENDPOINT', env)
        self.assertNotIn('IDENTITY_HEADER', env)
        self.assertNotIn('DATABASE_URL', env)

    def test_external_or_unregistered_identity_endpoint_denied_before_network(self):
        value=dict(AZURE_CLIENT_ID=CLIENT, IDENTITY_HEADER='synthetic',
                   IDENTITY_ENDPOINT='http://127.0.0.1:5000/msi/token')
        for field, replacement in [('IDENTITY_ENDPOINT','https://external.invalid/msi/token'),
                ('IDENTITY_ENDPOINT','http://127.0.0.1:5000/msi/token?other=1'),
                ('IDENTITY_ENDPOINT','http://username@127.0.0.1:5000/msi/token'),
                ('IDENTITY_ENDPOINT','http://localhost.external.invalid:5000/msi/token'),
                ('IDENTITY_ENDPOINT','http://169.254.169.254:5000/msi/token'),
                ('IDENTITY_ENDPOINT','http://localhost:invalid/msi/token'),
                ('AZURE_CLIENT_ID','other'), ('IDENTITY_HEADER','')]:
            changed={**value, field:replacement}
            with self.assertRaises(ProbeError):managed_token(changed)

    def test_verified_localhost_alias_is_numeric_loopback_without_dns_or_proxy(self):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'{"access_token":"synthetic-only"}'
        opener=MagicMock();opener.open.return_value=response
        for host in ['localhost','127.0.0.1']:
            environment=dict(AZURE_CLIENT_ID=CLIENT,IDENTITY_HEADER='synthetic-header',
                             IDENTITY_ENDPOINT='http://'+host+':5000/msi/token')
            with patch('ibkr_etl.codex_runtime_probe.build_opener',return_value=opener) as build:
                self.assertEqual(managed_token(environment),'synthetic-only')
            request=opener.open.call_args.args[0]
            self.assertTrue(request.full_url.startswith('http://127.0.0.1:5000/msi/token?'))
            self.assertIn('resource=https%3A%2F%2Fai.azure.com',request.full_url)
            self.assertIn('client_id='+CLIENT,request.full_url)
            self.assertEqual(build.call_args.args[0].proxies,{})
            self.assertEqual(opener.open.call_args.kwargs['timeout'],15)

    def test_managed_token_size_and_token_shape_remain_bounded(self):
        environment=dict(AZURE_CLIENT_ID=CLIENT,IDENTITY_HEADER='synthetic-header',
                         IDENTITY_ENDPOINT='http://localhost:5000/msi/token')
        for raw in [b'x'*32769,b'{"access_token":"contains whitespace"}',b'{"access_token":null}']:
            response=MagicMock();response.__enter__.return_value.read.return_value=raw
            opener=MagicMock();opener.open.return_value=response
            with patch('ibkr_etl.codex_runtime_probe.build_opener',return_value=opener):
                with self.assertRaises(ProbeError):managed_token(environment)

    def test_fixture_stream_validated_without_retaining_text(self):
        raw=fixture_stream()
        self.assertEqual(validate_stream(raw), hashlib.sha256(raw).hexdigest())

    def test_function_items_and_argument_deltas_denied(self):
        for event in [dict(type='response.output_item.added', item=dict(type='function_call', name='shell')),
                      dict(type='response.function_call_arguments.delta', delta='malicious')]:
            raw=('data: '+json.dumps(event)+'\n\n').encode()+fixture_stream()
            with self.assertRaises(ProbeError):validate_stream(raw)

    def test_bad_terminal_completion_duplicate_and_wrong_reply_denied(self):
        raw=fixture_stream()
        for changed in [raw.replace(REPLY.encode(),b'WRONG'), raw+raw,
                        raw.replace(b'"completed"', b'"incomplete"'), b'data: {}\n\n',
                        b'x'*(MAX_BYTES+1)]:
            with self.assertRaises((ProbeError,ValueError)):validate_stream(changed)

    def test_actual_loopback_relay_auth_path_and_single_request_budget(self):
        with SyntheticRelay() as relay:
            req=Request(relay.base_url+'/responses', method='POST', data=json.dumps(payload()).encode(),
                        headers={'Content-Type':'application/json','Authorization':'Bearer '+relay.capability})
            with urlopen(req, timeout=2) as response:
                self.assertEqual(validate_stream(response.read()), relay.response_sha256)
            self.assertEqual(relay.request_count,1)
            self.assertEqual(relay.accepted_count,1)
            self.assertEqual(relay.advertised_tool_count,1)
            with self.assertRaises(HTTPError) as denied:urlopen(req,timeout=2)
            denied.exception.close()
            self.assertEqual(relay.failure,'relay_request_budget_exhausted')

    def test_access_denial_does_not_contact_upstream(self):
        for suffix, token in [('/responses','wrong'),('/responses?redirect=1','correct')]:
            calls=[]
            with SyntheticRelay('secret',transport=lambda req:calls.append(req)) as relay:
                req=Request(relay.base_url+suffix, data=json.dumps(payload()).encode(),
                            headers={'Content-Type':'application/json','Authorization':'Bearer '+
                                     (relay.capability if token=='correct' else token)})
                with self.assertRaises(HTTPError) as denied:urlopen(req,timeout=2)
                denied.exception.close()
                self.assertEqual(calls,[])
                self.assertEqual(relay.request_count,0)

    def test_tool_free_upstream_and_bearer_only_in_parent_request(self):
        calls=[]
        def transport(req):calls.append(req);return fixture_stream()
        with SyntheticRelay('private-azure-bearer',transport=transport) as relay:
            req=Request(relay.base_url+'/responses',data=json.dumps(payload()).encode(),
                        headers={'Content-Type':'application/json','Authorization':'Bearer '+relay.capability})
            with urlopen(req,timeout=2) as response:self.assertEqual(response.status,200)
            self.assertEqual(len(calls),1)
            upstream=json.loads(calls[0].data)
            self.assertEqual(upstream['tools'],[])
            self.assertEqual(calls[0].get_header('Authorization'),'Bearer private-azure-bearer')
            self.assertNotIn('private-azure-bearer',json.dumps(upstream))
            self.assertNotIn('private-azure-bearer',str(relay.__dict__.get('failure')))

    def test_five_file_build_context_without_private_application_files(self):
        files,record=manifest()
        self.assertEqual(len(files),5)
        self.assertEqual(set(files),set(record['files']))
        self.assertFalse(record['portfolio_data_included'])
        self.assertFalse(record['credentials_included'])
        docker=files['Dockerfile'].decode()
        self.assertNotIn('ibkr-platform',docker)
        self.assertNotIn('COPY .',docker)
        self.assertIn('USER 10001:10001',docker)
        self.assertIn('--mode offline',docker)
        self.assertEqual(json.loads(files['codex-runtime.lock.json'])['runtime_version'],'0.160.0')
        self.assertTrue(json.loads(files['codex-runtime.lock.json'])['binary_member'].endswith('/bin/codex'))
        self.assertTrue(json.loads(files['codex-runtime.lock.json'])['code_mode_host_member'].endswith('/bin/codex-code-mode-host'))


if __name__=='__main__':unittest.main()
