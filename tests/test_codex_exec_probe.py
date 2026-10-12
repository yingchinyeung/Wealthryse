"""Real asynchronous child-process fixtures, no Codex/model/Azure calls."""
import asyncio
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zlib
from unittest.mock import patch
from urllib.request import Request, urlopen

from ibkr_etl.codex_exec_probe import (
    CATALOG_PATH, DECLARATION_TOOLS_BYTES, DECLARATION_TOOLS_SHA256, ExecStream,
    ExecSyntheticRelay, _stop_owned_process, exec_command, exec_probe,
    normalize_exec_payload,
)
from ibkr_etl.codex_runtime_probe import (
    DEPLOYMENT, MAX_BYTES, PROMPT, REPLY, ProbeError, SyntheticRelay,
    child_environment, restricted_payload,
)

THREAD = '0199a213-81c0-7800-8aa1-bbab2a035a53'
# Windows venv python.exe is a delegating launcher, not the interpreter process
# whose cwd/stdio we need to verify has closed after cancellation. Fixtures use
# the actual interpreter; no packages beyond the standard library are needed.
FIXTURE_PYTHON = getattr(sys, '_base_executable', None) or sys.executable


def events():
    return [dict(type='thread.started', thread_id=THREAD), dict(type='turn.started'),
            dict(type='item.completed', item=dict(id='item_0', type='reasoning', text='unretained reasoning')),
            dict(type='item.completed', item=dict(id='item_1', type='agent_message', text=REPLY)),
            dict(type='turn.completed', usage=dict(input_tokens=12, cached_input_tokens=0, output_tokens=8))]


def child_script(values, *, status=0, sleep=0, raw=None, ignore_terminate=False):
    # This fixture is a real process with stdio and deliberately asynchronous
    # completion. It does not issue any model request or open a relay connection.
    emitted = ''.join(json.dumps(v) + '\n' for v in values) if raw is None else raw
    # Compressed fixture prevents OS command-line limits from masking the
    # adapter's output-bound tests; the child still emits the full raw bytes.
    encoded = base64.b64encode(zlib.compress(emitted.encode())).decode()
    return ('import sys,time,signal,base64,zlib\n'
            + ('signal.signal(signal.SIGTERM,signal.SIG_IGN)\n' if ignore_terminate else '')
            + 'prompt=sys.stdin.read()\n'
            + 'assert prompt==' + repr(PROMPT) + '\n'
            + 'sys.stdout.buffer.write(zlib.decompress(base64.b64decode(' + repr(encoded) + ')))\nsys.stdout.flush()\n'
            + 'time.sleep(' + repr(sleep) + ')\nsys.exit(' + repr(status) + ')\n')


class ExecProbeTests(unittest.IsolatedAsyncioTestCase):
    async def run_child(self, values, *, status=0, sleep=0, raw=None, timeout=10,
                        cancel_event=None, capture=None):
        with tempfile.TemporaryDirectory(prefix='codex-exec-unit-') as temp:
            directory = Path(temp)
            for name in ['workspace', 'home', 'codex-home', 'tmp']:
                (directory / name).mkdir()
            command = [FIXTURE_PYTHON, '-u', '-c', child_script(values, status=status, sleep=sleep, raw=raw)]
            real_create = asyncio.create_subprocess_exec

            async def create(*args, **kwargs):
                proc = await real_create(*args, **kwargs)
                if capture is not None:
                    capture.append(proc)
                return proc

            relay = SimpleNamespace(capability='synthetic-local-capability')
            with patch('ibkr_etl.codex_exec_probe.exec_command', return_value=command), \
                    patch('ibkr_etl.codex_exec_probe.asyncio.create_subprocess_exec', side_effect=create):
                return await exec_probe(relay, directory, timeout_seconds=timeout, cancel_event=cancel_event)

    async def test_real_child_normal_lifecycle_and_only_hashes_retained(self):
        result = await self.run_child(events())
        for name in ['thread_started_verified', 'turn_started_verified', 'turn_completed',
                     'event_stream_observed', 'runtime_output_verified', 'process_exit_verified']:
            self.assertTrue(result[name])
        self.assertEqual(result['thread_sha256'], hashlib.sha256(THREAD.encode()).hexdigest())
        self.assertEqual(result['runtime_output_sha256'], hashlib.sha256(REPLY.encode()).hexdigest())
        self.assertFalse(result['resume_enabled'])
        self.assertFalse(result['tool_execution_authorized'])
        self.assertNotIn(THREAD, json.dumps(result))
        self.assertNotIn(REPLY, json.dumps(result))
        self.assertNotIn('unretained reasoning', json.dumps(result))

    async def test_fixture_process_is_the_owned_interpreter_and_stdio_closes(self):
        with tempfile.TemporaryDirectory(prefix='codex-exec-owned-pid-') as temp:
            proc = await asyncio.create_subprocess_exec(
                FIXTURE_PYTHON, '-u', '-c',
                'import os,time;print(os.getpid(),flush=True);time.sleep(10)',
                cwd=temp, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                reported_pid = int((await asyncio.wait_for(proc.stdout.readline(), 10)).strip())
                self.assertEqual(proc.pid, reported_pid)
            finally:
                await _stop_owned_process(proc)
            self.assertIsNotNone(proc.returncode)
            self.assertEqual(await asyncio.wait_for(proc.stdout.read(), 2), b'')

    async def test_failed_turn_and_error_are_sanitized_and_child_stopped(self):
        for kind, reason in [('turn.failed', 'exec_turn_failed'), ('error', 'exec_runtime_error')]:
            capture = []
            with self.subTest(kind=kind), self.assertRaisesRegex(ProbeError, reason) as error:
                await self.run_child(events()[:2] + [dict(type=kind, error=dict(message='sensitive error'))],
                                     sleep=5, capture=capture)
            self.assertNotIn('sensitive', str(error.exception))
            self.assertIsNotNone(capture[0].returncode)

    async def test_observed_preturn_error_shape_is_not_silently_accepted(self):
        values = events()
        values.insert(1, dict(type='item.completed', item=dict(id='item_startup', type='error',
                                                            message='unretained startup diagnostic')))
        with self.assertRaisesRegex(ProbeError, 'exec_runtime_item_error') as error:
            await self.run_child(values)
        self.assertNotIn('unretained', str(error.exception))

    async def test_nonzero_exit_rejects_apparent_completion(self):
        with self.assertRaisesRegex(ProbeError, 'exec_process_nonzero_exit'):
            await self.run_child(events(), status=19)

    async def test_unknown_and_execution_items_denied_before_completion(self):
        for kind in ['command_execution', 'file_change', 'mcp_tool_call', 'web_search', 'plan', 'unknown']:
            capture = []
            with self.subTest(kind=kind), self.assertRaisesRegex(ProbeError, 'exec_non_message_item_denied'):
                await self.run_child(events()[:2] + [dict(type='item.completed', item=dict(id='x', type=kind))],
                                     sleep=5, capture=capture)
            self.assertIsNotNone(capture[0].returncode)

    async def test_oversized_line_and_total_output_rejected(self):
        with self.assertRaisesRegex(ProbeError, 'exec_output_line_limit'):
            await self.run_child([], raw='x' * (MAX_BYTES + 1) + '\n')
        long_items = [dict(type='item.completed', item=dict(id='r' + str(i), type='reasoning',
                      text='x' * (MAX_BYTES // 2))) for i in range(5)]
        with self.assertRaisesRegex(ProbeError, 'exec_output_limit'):
            await self.run_child(events()[:2] + long_items)

    async def test_cancel_event_terminates_child_without_acceptance(self):
        stop = asyncio.Event()
        capture = []
        task = asyncio.create_task(self.run_child(events()[:2], sleep=10, capture=capture, cancel_event=stop))
        while not capture:
            await asyncio.sleep(0.01)
        stop.set()
        with self.assertRaisesRegex(ProbeError, 'exec_cancelled'):
            await task
        self.assertIsNotNone(capture[0].returncode)

    async def test_task_cancellation_terminates_child(self):
        capture = []
        task = asyncio.create_task(self.run_child(events()[:2], sleep=10, capture=capture))
        while not capture:
            await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(capture[0].returncode)

    async def test_cancellation_during_launch_cannot_lose_child(self):
        with tempfile.TemporaryDirectory(prefix='codex-exec-launch-') as temp:
            directory = Path(temp)
            for name in ['workspace', 'home', 'codex-home', 'tmp']:
                (directory / name).mkdir()
            real_create = asyncio.create_subprocess_exec
            command = [FIXTURE_PYTHON, '-u', '-c', 'import time;time.sleep(10)']
            capture = []
            ready = asyncio.Event()
            release = asyncio.Event()

            async def delayed_create(*args, **kwargs):
                proc = await real_create(*args, **kwargs)
                capture.append(proc)
                ready.set()
                await release.wait()
                return proc

            relay = SimpleNamespace(capability='synthetic-local-capability')
            with patch('ibkr_etl.codex_exec_probe.exec_command', return_value=command), \
                    patch('ibkr_etl.codex_exec_probe.asyncio.create_subprocess_exec', side_effect=delayed_create):
                task = asyncio.create_task(exec_probe(relay, directory))
                await ready.wait()
                task.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertIsNotNone(capture[0].returncode)

    async def test_owned_process_kill_fallback_after_terminate_timeout(self):
        class AsyncProcess:
            returncode = None
            terminated = False
            killed = False
            def terminate(self):
                self.terminated = True
            def kill(self):
                self.killed = True
                self.returncode = -9
            async def wait(self):
                return self.returncode

        process = AsyncProcess()
        calls = []
        async def bounded_wait(awaitable, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                awaitable.close()
                raise TimeoutError()
            return await awaitable

        with patch('ibkr_etl.codex_exec_probe.asyncio.wait_for', side_effect=bounded_wait):
            await _stop_owned_process(process)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertEqual(calls, [2, 2])

    async def test_timeout_terminates_child_and_never_accepts(self):
        capture = []
        with self.assertRaisesRegex(ProbeError, 'exec_deadline'):
            await self.run_child(events()[:2], sleep=10, timeout=0.1, capture=capture)
        self.assertIsNotNone(capture[0].returncode)

    async def test_events_after_completion_and_missing_completion_fail(self):
        with self.assertRaisesRegex(ProbeError, 'exec_event_after_completion'):
            await self.run_child(events() + [dict(type='turn.started')])
        with self.assertRaisesRegex(ProbeError, 'exec_completion_missing'):
            await self.run_child(events()[:-1])

    async def test_duplicate_and_out_of_order_thread_or_turn_denied(self):
        cases = [events()[1:], events()[:1] + events(), events()[:2] + [events()[1]],
                 [events()[0], events()[2]]]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ProbeError):
                await self.run_child(values)

    async def test_cross_thread_metadata_and_wrong_reply_denied(self):
        values = events()
        values[2]['thread_id'] = '0199a213-81c0-7800-8aa1-bbab2a035a54'
        with self.assertRaisesRegex(ProbeError, 'exec_event_scope_invalid'):
            await self.run_child(values)
        values = events()
        values[3]['item']['text'] = REPLY + ' other text'
        with self.assertRaisesRegex(ProbeError, 'exec_synthetic_output_differs'):
            await self.run_child(values)

    async def test_duplicate_message_and_unfinished_item_denied(self):
        values = events()
        values.insert(4, copy.deepcopy(values[3]))
        with self.assertRaisesRegex(ProbeError, 'exec_item_lifecycle_invalid'):
            await self.run_child(values)
        values = events()
        values.insert(2, dict(type='item.started', item=dict(id='unfinished', type='reasoning', text='discard')))
        with self.assertRaisesRegex(ProbeError, 'exec_completion_invalid'):
            await self.run_child(values)

    async def test_blank_malformed_truncated_and_unknown_event_rejected(self):
        for raw in ['\n', '{not json}\n', json.dumps(events()[0]), '{"type":"turn.resumed"}\n',
                    '{"type":"error","type":"turn.started"}\n']:
            with self.subTest(raw=raw), self.assertRaises(ProbeError):
                await self.run_child([], raw=raw)

    async def test_limits_and_pre_cancelled_denied_without_spawn(self):
        stop = asyncio.Event()
        stop.set()
        with patch('ibkr_etl.codex_exec_probe.asyncio.create_subprocess_exec') as create:
            with self.assertRaisesRegex(ProbeError, 'exec_cancelled'):
                await exec_probe(None, Path('/unused'), cancel_event=stop)
            for timeout in [0, 81, float('inf'), float('nan'), True, '80']:
                with self.assertRaisesRegex(ProbeError, 'exec_probe_limits_invalid'):
                    await exec_probe(None, Path('/unused'), timeout_seconds=timeout)
            create.assert_not_called()


class ExecContractTests(unittest.TestCase):
    def test_fixed_command_and_no_resume_or_real_credentials(self):
        with SyntheticRelay('actual-private-token') as relay:
            args = exec_command(relay)
            self.assertEqual(args[-8:], ['--ask-for-approval', 'never', 'exec', '--json',
                                        '--skip-git-repo-check', '--sandbox', 'read-only', '-'])
            self.assertNotIn(PROMPT, args)
            self.assertNotIn('actual-private-token', str(args))
            self.assertNotIn(relay.capability, str(args))
            for denied in ['resume', '--last', '--all', '--remote', '--full-auto', '--yolo',
                           '--dangerously-bypass-approvals-and-sandbox', '--output-last-message']:
                self.assertNotIn(denied, args)
            self.assertIn('features.shell_tool=false', args)
            self.assertIn('features.view_image=false', args)
            self.assertIn('features.image_generation=false', args)
            self.assertIn('model_catalog_json=' + json.dumps(CATALOG_PATH), args)
            self.assertNotIn('tools.view_image=false', args)
            self.assertIn('web_search="disabled"', args)
            env = child_environment(Path('/temporary'), relay.capability)
            self.assertEqual(set(env), {'PATH', 'LANG', 'HOME', 'CODEX_HOME', 'TMPDIR', 'CODEX_AZURE_BROKER_TOKEN'})
            self.assertNotIn('actual-private-token', json.dumps(env))

    def test_reasoning_lifecycle_does_not_save_text(self):
        stream = ExecStream()
        values = events()
        values[2:3] = [dict(type='item.started', item=dict(id='r', type='reasoning', text='secret draft')),
                       dict(type='item.updated', item=dict(id='r', type='reasoning', text='secret update')),
                       dict(type='item.completed', item=dict(id='r', type='reasoning', text='secret final'))]
        for value in values:
            stream.feed((json.dumps(value) + '\n').encode())
        self.assertNotIn('secret', str(stream.__dict__))
        self.assertTrue(stream.result(0)['runtime_output_verified'])

    def test_external_relay_binding_is_rejected_before_process_start(self):
        for url in ['https://external.invalid/openai/v1', 'http://localhost:1/openai/v1',
                    'http://127.0.0.1:1/openai/v1?redirect=1', 'http://user@127.0.0.1:1/openai/v1']:
            with self.subTest(url=url), self.assertRaisesRegex(ProbeError, 'exec_relay_binding_invalid'):
                exec_command(SimpleNamespace(base_url=url))

    def test_event_count_and_usage_limits(self):
        stream = ExecStream()
        for value in events()[:2]:
            stream.feed((json.dumps(value) + '\n').encode())
        with self.assertRaisesRegex(ProbeError, 'exec_output_limit'):
            for i in range(301):
                stream.feed((json.dumps(dict(type='item.completed', item=dict(id='r' + str(i),
                            type='reasoning', text='discard'))) + '\n').encode())
        for usage in [None, {'input_tokens': True, 'cached_input_tokens': 0, 'output_tokens': 1},
                      {'input_tokens': 1, 'cached_input_tokens': 2, 'output_tokens': 1}]:
            stream = ExecStream()
            values = events()
            values[-1]['usage'] = usage
            with self.assertRaisesRegex(ProbeError, 'exec_usage_invalid'):
                for value in values:
                    stream.feed((json.dumps(value) + '\n').encode())

    def test_observed_pinned_usage_counter_is_typed_and_unknown_fields_still_denied(self):
        values = events()
        values[-1]['usage'].update(cache_write_input_tokens=0, reasoning_output_tokens=0)
        stream = ExecStream()
        for value in values:
            stream.feed((json.dumps(value)+'\n').encode())
        self.assertEqual(stream.result(0)['usage']['cache_write_input_tokens'], 0)
        for extra in [dict(cache_write_input_tokens=None), dict(cache_write_input_tokens=True),
                      dict(cache_write_input_tokens=-1), dict(cache_write_input_tokens=13),
                      dict(unrecognized_counter=0)]:
            invalid = copy.deepcopy(values)
            invalid[-1]['usage'].update(extra)
            stream = ExecStream()
            with self.subTest(extra=extra), self.assertRaisesRegex(ProbeError, 'exec_usage_invalid'):
                for value in invalid:
                    stream.feed((json.dumps(value)+'\n').encode())


class ExecRelayTests(unittest.TestCase):
    def setUp(self):
        # Small public fixture; production constants remain an exact observed
        # native-runtime hash/size, not a wildcard or caller-supplied binding.
        self.tools = [dict(type='namespace', name='functions', description='fixture', tools=[]),
                      dict(type='namespace', name='collaboration', description='fixture', tools=[])]
        canonical = json.dumps(self.tools, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
        self.fixture_hash = hashlib.sha256(canonical).hexdigest()
        self.hash_patch = patch('ibkr_etl.codex_exec_probe.DECLARATION_TOOLS_SHA256', self.fixture_hash)
        self.bytes_patch = patch('ibkr_etl.codex_exec_probe.DECLARATION_TOOLS_BYTES', len(canonical))
        self.hash_patch.start()
        self.bytes_patch.start()
        self.addCleanup(self.hash_patch.stop)
        self.addCleanup(self.bytes_patch.stop)

    def payload(self):
        declaration = dict(id='declaration_0', type='additional_tools', role='developer',
                           tools=copy.deepcopy(self.tools))
        message = dict(type='message', role='user', content=[dict(type='input_text', text=PROMPT)])
        return dict(model=DEPLOYMENT, stream=True, store=False, tools=['not_forwarded'],
                    input=[declaration, message])

    def test_production_declaration_binding_is_exact_not_a_generic_allowance(self):
        self.assertEqual(DECLARATION_TOOLS_SHA256,
                         '449a364fe16bf52da8798ab2f76ab5e5618319de87447719d753d66140ff074d')
        self.assertEqual(DECLARATION_TOOLS_BYTES, 14561)
        self.assertNotEqual(self.fixture_hash, DECLARATION_TOOLS_SHA256)

    def test_exact_declaration_removed_without_mutating_or_forwarding_it(self):
        payload = self.payload()
        before = copy.deepcopy(payload)
        normalized = normalize_exec_payload(payload)
        self.assertEqual(payload, before)
        self.assertEqual(normalized['input'], payload['input'][1:])
        with ExecSyntheticRelay() as relay:
            restricted = relay._restrict_payload(payload)
            self.assertEqual(restricted['tools'], [])
            self.assertEqual(restricted['tool_choice'], 'none')
            self.assertEqual(restricted['input'], normalized['input'])
            evidence = relay.restriction_evidence()
            self.assertEqual(evidence['discarded_declaration_count'], 1)
            self.assertEqual(evidence['discarded_declaration_sha256'], self.fixture_hash)
            self.assertFalse(evidence['declaration_tools_forwarded'])
            self.assertTrue(evidence['upstream_tool_contract_verified'])
            self.assertNotIn('declaration_0', json.dumps(evidence))
            with self.assertRaisesRegex(ProbeError, 'exec_declaration_budget_exhausted'):
                relay._restrict_payload(payload)

    def test_original_shared_validator_still_rejects_declarations(self):
        with self.assertRaisesRegex(ProbeError, 'synthetic_input_item_denied'):
            restricted_payload(self.payload())

    def test_modified_declaration_type_scope_keys_and_id_denied(self):
        for mutate in [lambda d: d.update(type='function_call'),
                       lambda d: d.update(role='assistant'),
                       lambda d: d.update(role='user'),
                       lambda d: d.update(extra='not_allowed'),
                       lambda d: d.update(id='\nraw'),
                       lambda d: d['tools'][0].update(description='modified'),
                       lambda d: d['tools'][0].update(name='other_namespace'),
                       lambda d: d.update(tools=[]),
                       lambda d: d.pop('id')]:
            payload = self.payload()
            mutate(payload['input'][0])
            with self.subTest(payload=payload), self.assertRaises(ProbeError):
                normalize_exec_payload(payload)

    def test_duplicate_missing_and_later_declaration_denied(self):
        payloads = []
        duplicate = self.payload()
        duplicate['input'].append(copy.deepcopy(duplicate['input'][0]))
        payloads.append(duplicate)
        missing = self.payload()
        missing['input'] = missing['input'][1:]
        payloads.append(missing)
        later = self.payload()
        later['input'].reverse()
        payloads.append(later)
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ProbeError):
                normalize_exec_payload(payload)

    def test_non_message_input_or_changed_prompt_still_denied_by_shared_contract(self):
        for item in [dict(type='function_call', name='unknown'),
                     dict(type='message', role='assistant', content=[]),
                     dict(type='reasoning', text='not_forwarded'),
                     dict(type='message', role='user', content=[dict(type='input_text', text=PROMPT+'\n')])]:
            payload = self.payload()
            payload['input'].append(item)
            with ExecSyntheticRelay() as relay, self.subTest(item=item), self.assertRaises(ProbeError):
                relay._restrict_payload(payload)
            self.assertEqual(relay.discarded_declaration_count, 0)

    def test_failed_original_contract_cannot_record_discard_acceptance(self):
        payload = self.payload()
        payload['model'] = 'unapproved-model'
        with ExecSyntheticRelay() as relay:
            with self.assertRaisesRegex(ProbeError, 'synthetic_request_contract_invalid'):
                relay._restrict_payload(payload)
            with self.assertRaisesRegex(ProbeError, 'exec_declaration_evidence_missing'):
                relay.restriction_evidence()
            self.assertEqual(relay.discarded_declaration_count, 0)

    def test_loopback_handler_uses_subclass_and_offline_fixture_only(self):
        with ExecSyntheticRelay() as relay:
            request = Request(relay.base_url+'/responses', data=json.dumps(self.payload()).encode(),
                              headers={'Content-Type': 'application/json',
                                       'Authorization': 'Bearer '+relay.capability})
            with urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(REPLY.encode(), response.read(MAX_BYTES+1))
            self.assertEqual((relay.request_count, relay.accepted_count), (1, 1))
            self.assertIsNone(relay.failure)
            self.assertEqual(relay.restriction_evidence()['discarded_declaration_count'], 1)


if __name__ == '__main__':
    unittest.main()
