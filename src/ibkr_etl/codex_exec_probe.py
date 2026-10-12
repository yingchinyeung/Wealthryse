"""Bounded synthetic acceptance adapter for the documented Codex exec interface.

This accepts no user task, attachment, resume ID or tool. It is not an owner
workspace or a specialist worker. The existing loopback relay holds Azure's
credential in the parent and validates one fixed, tool-free model request.
The actual pinned binary must independently pass the image/help acceptance gate.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import uuid
from urllib.parse import urlsplit

from .codex_runtime_probe import (
    DEPLOYMENT, MAX_BYTES, PROMPT, REPLY, VERSION, ProbeError, SyntheticRelay,
    child_environment,
)

MAX_EVENTS = 300
MAX_OUTPUT_BYTES = 2 * MAX_BYTES
MAX_SECONDS = 80
CATALOG_PATH = '/probe/codex-exec-model-catalog.json'
# Pinned 0.160.0, native gpt-5.6-sol metadata, exact fixed feature settings.
# Canonical tools JSON: sorted keys, compact separators, ensure_ascii=True.
# Verified twice in clean offline Windows scopes. Linux independently gates it.
DECLARATION_TOOLS_SHA256 = '449a364fe16bf52da8798ab2f76ab5e5618319de87447719d753d66140ff074d'
DECLARATION_TOOLS_BYTES = 14561


def normalize_exec_payload(value):
    """Discard one exact runtime declaration, never a tool call or source item.

    Native code-mode metadata adds this declaration even with shell, web, apps
    and multi-agent features disabled. It is NOT forwarded or made executable.
    The shared app-server validator remains unchanged and validates the rest.
    """
    if not isinstance(value, dict):
        raise ProbeError('exec_request_shape_invalid')
    items = value.get('input')
    if not isinstance(items, list) or not 2 <= len(items) <= 12:
        raise ProbeError('exec_input_shape_invalid')
    declarations = [i for i, item in enumerate(items)
                    if isinstance(item, dict) and item.get('type') == 'additional_tools']
    if declarations != [0]:
        raise ProbeError('exec_tool_declaration_position_invalid')
    item = items[0]
    if set(item) != {'id', 'type', 'role', 'tools'} or item['role'] != 'developer':
        raise ProbeError('exec_tool_declaration_scope_invalid')
    _identifier(item['id'], 'exec_tool_declaration_id_invalid')
    try:
        canonical = json.dumps(item['tools'], sort_keys=True, separators=(',', ':'),
                               ensure_ascii=True).encode()
    except (TypeError, ValueError, RecursionError):
        raise ProbeError('exec_tool_declaration_invalid') from None
    if (len(canonical) != DECLARATION_TOOLS_BYTES
            or hashlib.sha256(canonical).hexdigest() != DECLARATION_TOOLS_SHA256):
        raise ProbeError('exec_tool_declaration_differs')
    return {**value, 'input': items[1:]}


class ExecSyntheticRelay(SyntheticRelay):
    """CLI-only exact declaration removal; no generic validation callback."""

    def __init__(self, bearer=None, transport=None):
        super().__init__(bearer=bearer, transport=transport)
        self.discarded_declaration_count = 0
        self.discarded_declaration_sha256 = None
        self.declaration_tools_forwarded = False

    def _restrict_payload(self, payload):
        if self.discarded_declaration_count:
            raise ProbeError('exec_declaration_budget_exhausted')
        restricted = super()._restrict_payload(normalize_exec_payload(payload))
        if restricted.get('tools') != [] or restricted.get('tool_choice') != 'none':
            raise ProbeError('exec_upstream_tool_contract_invalid')
        # Record only after the unchanged fixed-prompt validator also passes.
        self.discarded_declaration_count = 1
        self.discarded_declaration_sha256 = DECLARATION_TOOLS_SHA256
        return restricted

    def restriction_evidence(self):
        if (self.discarded_declaration_count != 1
                or self.discarded_declaration_sha256 != DECLARATION_TOOLS_SHA256
                or self.declaration_tools_forwarded is not False):
            raise ProbeError('exec_declaration_evidence_missing')
        return dict(discarded_declaration_count=1,
                    discarded_declaration_sha256=DECLARATION_TOOLS_SHA256,
                    declaration_tools_forwarded=False,
                    upstream_tool_contract_verified=True)


def exec_command(relay):
    """No shell, resume, writable scope, arbitrary argument or real bearer."""
    try:
        parsed = urlsplit(relay.base_url)
        port = parsed.port
    except (AttributeError, TypeError, ValueError):
        raise ProbeError('exec_relay_binding_invalid') from None
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not port
            or parsed.path != '/openai/v1' or parsed.query or parsed.fragment
            or parsed.username or parsed.password):
        raise ProbeError('exec_relay_binding_invalid')
    settings = {
        'model': DEPLOYMENT, 'model_provider': 'azure_synthetic_exec_probe',
        'model_catalog_json': CATALOG_PATH,
        'model_providers.azure_synthetic_exec_probe.name': 'Azure synthetic exec trial',
        'model_providers.azure_synthetic_exec_probe.base_url': relay.base_url,
        'model_providers.azure_synthetic_exec_probe.env_key': 'CODEX_AZURE_BROKER_TOKEN',
        'model_providers.azure_synthetic_exec_probe.wire_api': 'responses',
        'model_providers.azure_synthetic_exec_probe.request_max_retries': 0,
        'model_providers.azure_synthetic_exec_probe.stream_max_retries': 0,
        'model_providers.azure_synthetic_exec_probe.stream_idle_timeout_ms': 70000,
        'approval_policy': 'never', 'sandbox_mode': 'read-only',
        'web_search': 'disabled', 'project_root_markers': [],
        'features.shell_tool': False, 'features.unified_exec': False,
        'features.multi_agent': False, 'features.apps': False,
        'features.shell_snapshot': False, 'features.skill_mcp_dependency_install': False,
        # Pinned 0.160.0 lists these feature keys as stable. The older
        # tools.view_image alias emits startup error-items even when false.
        'features.view_image': False, 'features.image_generation': False,
        'analytics.enabled': False, 'feedback.enabled': False,
    }
    args = ['/usr/local/bin/codex']
    for key, value in settings.items():
        args += ['-c', key + '=' + json.dumps(value)]
    return args + ['--ask-for-approval', 'never', 'exec', '--json',
                   '--skip-git-repo-check', '--sandbox', 'read-only', '-']


def _identifier(value, reason):
    if (not isinstance(value, str) or not value or len(value) > 128
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ProbeError(reason)
    return value


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ProbeError('exec_event_duplicate_key')
        value[key] = item
    return value


class ExecStream:
    """One fresh thread/turn only; untrusted text never enters retained results."""

    def __init__(self):
        self.phase = 'new'
        self.thread_id = None
        self.items = {}
        self.message_completed = False
        self.event_count = 0
        self.byte_count = 0
        self.digest = hashlib.sha256()
        self.usage = None

    def feed(self, raw):
        self.event_count += 1
        self.byte_count += len(raw)
        if self.event_count > MAX_EVENTS or self.byte_count > MAX_OUTPUT_BYTES:
            raise ProbeError('exec_output_limit')
        if len(raw) > MAX_BYTES:
            raise ProbeError('exec_output_line_limit')
        if not raw.endswith(b'\n'):
            raise ProbeError('exec_output_truncated')
        self.digest.update(raw)
        try:
            value = json.loads(raw, object_pairs_hook=_unique_object)
        except ProbeError:
            raise
        except (ValueError, UnicodeError, RecursionError):
            raise ProbeError('exec_event_json_invalid') from None
        if not isinstance(value, dict) or not isinstance(value.get('type'), str):
            raise ProbeError('exec_event_shape_invalid')
        kind = value['type']
        if kind == 'turn.failed':
            raise ProbeError('exec_turn_failed')
        if kind == 'error':
            raise ProbeError('exec_runtime_error')
        if self.phase == 'completed':
            raise ProbeError('exec_event_after_completion')
        if kind == 'thread.started':
            if self.phase != 'new' or set(value) != {'type', 'thread_id'}:
                raise ProbeError('exec_thread_lifecycle_invalid')
            thread_id = _identifier(value['thread_id'], 'exec_thread_id_invalid')
            try:
                if str(uuid.UUID(thread_id)) != thread_id:
                    raise ValueError()
            except ValueError:
                raise ProbeError('exec_thread_id_invalid') from None
            self.thread_id = thread_id
            self.phase = 'thread'
            return
        if self.thread_id is None:
            raise ProbeError('exec_thread_missing')
        if 'thread_id' in value and value['thread_id'] != self.thread_id:
            raise ProbeError('exec_event_scope_invalid')
        if (kind in {'item.started', 'item.updated', 'item.completed'}
                and isinstance(value.get('item'), dict)
                and value['item'].get('type') == 'error'):
            # Pinned CLI startup/configuration warnings also use error items.
            # Resolve the cause; neither their text nor a generic bypass enters
            # the acceptance path, including before turn.started.
            raise ProbeError('exec_runtime_item_error')
        if kind == 'turn.started':
            if self.phase != 'thread' or set(value) - {'type', 'thread_id'}:
                raise ProbeError('exec_turn_lifecycle_invalid')
            self.phase = 'turn'
            return
        if self.phase != 'turn':
            raise ProbeError('exec_turn_missing')
        if kind in {'item.started', 'item.updated', 'item.completed'}:
            if set(value) - {'type', 'thread_id', 'item'}:
                raise ProbeError('exec_item_event_invalid')
            self._item(kind, value.get('item'))
            return
        if kind == 'turn.completed':
            if (not self.message_completed
                    or set(value) - {'type', 'thread_id', 'usage'}
                    or any(state != 'completed' for _, state in self.items.values())):
                raise ProbeError('exec_completion_invalid')
            usage = value.get('usage')
            required = {'input_tokens', 'cached_input_tokens', 'output_tokens'}
            # Actual pinned 0.160.0 emits this cache-write counter as well as
            # reasoning_output_tokens. Keep the whitelist numeric and exact.
            optional = {'reasoning_output_tokens', 'cache_write_input_tokens'}
            if (not isinstance(usage, dict) or not required <= set(usage)
                    or set(usage) - required - optional
                    or any(type(v) is not int or not 0 <= v <= 1000000000
                           for v in usage.values())
                    or usage['cached_input_tokens'] > usage['input_tokens']
                    or usage.get('cache_write_input_tokens', 0) > usage['input_tokens']):
                raise ProbeError('exec_usage_invalid')
            self.usage = dict(usage)
            self.phase = 'completed'
            return
        raise ProbeError('exec_event_type_denied')

    def _item(self, event_kind, item):
        if not isinstance(item, dict) or item.get('type') not in {'reasoning', 'agent_message'}:
            raise ProbeError('exec_non_message_item_denied')
        if set(item) - {'id', 'type', 'text', 'phase'}:
            raise ProbeError('exec_item_shape_invalid')
        item_id = _identifier(item.get('id'), 'exec_item_id_invalid')
        item_kind = item['type']
        previous = self.items.get(item_id)
        if previous and (previous[0] != item_kind or previous[1] == 'completed'):
            raise ProbeError('exec_item_lifecycle_invalid')
        if event_kind == 'item.started' and previous:
            raise ProbeError('exec_item_lifecycle_invalid')
        if event_kind == 'item.updated' and previous is None:
            raise ProbeError('exec_item_lifecycle_invalid')
        if 'text' in item and not isinstance(item['text'], str):
            raise ProbeError('exec_item_text_invalid')
        if item_kind == 'agent_message':
            if item.get('phase') not in {None, 'final_answer'}:
                raise ProbeError('exec_message_phase_denied')
            text = item.get('text')
            if not isinstance(text, str) or not REPLY.startswith(text):
                raise ProbeError('exec_synthetic_output_differs')
            if event_kind == 'item.completed':
                if self.message_completed or text != REPLY:
                    raise ProbeError('exec_synthetic_output_differs')
                self.message_completed = True
        elif 'phase' in item:
            raise ProbeError('exec_reasoning_shape_invalid')
        # Retain only item identity/type/state in memory, never reasoning text.
        self.items[item_id] = (item_kind, 'completed' if event_kind == 'item.completed' else 'started')

    def result(self, exit_code):
        if exit_code != 0:
            raise ProbeError('exec_process_nonzero_exit')
        if self.phase != 'completed' or not self.message_completed:
            raise ProbeError('exec_completion_missing')
        return dict(thread_started_verified=True, turn_started_verified=True,
                    turn_completed=True, event_stream_observed=True,
                    runtime_output_verified=True, process_exit_verified=True,
                    thread_sha256=hashlib.sha256(self.thread_id.encode()).hexdigest(),
                    runtime_output_sha256=hashlib.sha256(REPLY.encode()).hexdigest(),
                    event_stream_sha256=self.digest.hexdigest(),
                    event_count=self.event_count, output_bytes=self.byte_count,
                    usage=self.usage, runtime_version=VERSION,
                    resume_enabled=False, tool_execution_authorized=False)


async def _consume(proc):
    stream = ExecStream()
    try:
        # EOF frames CLI stdin; a JSONL-style newline would change the exact
        # model-visible synthetic prompt and must not relax the relay contract.
        proc.stdin.write(PROMPT.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        while True:
            try:
                raw = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                raise ProbeError('exec_output_line_limit') from None
            if not raw:
                break
            stream.feed(raw)
        return stream.result(await proc.wait())
    except ProbeError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise ProbeError('exec_process_io_failed') from None


async def _stop_owned_process(proc):
    if proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(proc.wait(), 2)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except TimeoutError:
            raise ProbeError('exec_process_cleanup_failed') from None


async def exec_probe(relay, directory: Path, *, cancel_event=None, timeout_seconds=MAX_SECONDS):
    """Run one fixed stdin task in an isolated caller-owned temporary directory.

    Cancellation prevents acceptance and terminates only this owned child. A
    successful JSONL completion is insufficient without EOF and process exit 0.
    Caller owns relay close and temporary-directory removal, including rollout
    files; session resume or persistent owner history is deliberately absent.
    """
    if (type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= MAX_SECONDS
            or (cancel_event is not None and not isinstance(cancel_event, asyncio.Event))):
        raise ProbeError('exec_probe_limits_invalid')
    if cancel_event is not None and cancel_event.is_set():
        raise ProbeError('exec_cancelled')
    launch = asyncio.create_task(asyncio.create_subprocess_exec(
            *exec_command(relay), cwd=directory / 'workspace',
            env=child_environment(directory, relay.capability),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=MAX_BYTES))
    try:
        # Cancellation during subprocess creation must not lose the owned child.
        proc = await asyncio.shield(launch)
    except asyncio.CancelledError:
        try:
            proc = await launch
        except (OSError, ValueError):
            raise asyncio.CancelledError() from None
        await _stop_owned_process(proc)
        raise
    except (OSError, ValueError):
        raise ProbeError('exec_process_start_failed') from None
    work = asyncio.create_task(_consume(proc))
    stop = asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
    tasks = {work} if stop is None else {work, stop}
    try:
        done, _ = await asyncio.wait(tasks, timeout=timeout_seconds, return_when=asyncio.FIRST_COMPLETED)
        if stop is not None and (stop in done or cancel_event.is_set()):
            raise ProbeError('exec_cancelled')
        if work not in done:
            raise ProbeError('exec_deadline')
        result = work.result()
        if isinstance(relay, ExecSyntheticRelay):
            result.update(relay.restriction_evidence())
        return result
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        cleanup = asyncio.create_task(_stop_owned_process(proc))
        await asyncio.shield(cleanup)
