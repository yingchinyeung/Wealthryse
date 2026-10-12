"""Source-only coordinator for one fixed private owner review.

No CLI, model, network, database, prompt or result-content adapter is installed
here. Trusted server composition must inject independently verified one-shot
intent reservation, current authority/lease checks, read-only observation,
pinned Codex/Azure inference and owned-runtime cessation proof. Metadata worker
authority alone is never an inference grant. This module releases no result.

Uncertain side effects are quarantined, never retried. The in-memory logical
task guard is not distributed idempotency; the create-only protected intent
adapter must durably reserve the approved task execution, not just a new attempt
UUID/lease. A deliberate re-execution requires a separately authorized NEW task.
Uncertain runtime cessation also quarantines this instance with no reset API.
The live adapter must persist worker/runtime quarantine across process restarts;
constructing a new coordinator is not independent proof of recovery.
Cancellation cannot prove process-tree cessation
without the actual restricted runtime adapter's independent stop verification.
Timed-out callbacks may still have effects if they ignore cancellation: their
late values are discarded and the attempt requires independent reconciliation.
Concrete image/catalog identity and live execution-context verification are not
provided here; pinned names alone are not production runtime acceptance.
A succeeded protected outcome describes private computation, not delivery. A
later revoked/cancelled lease withholds its reference without rewriting that
immutable evidence. Future delivery adapters must independently audit actual
delivery/withheld decisions; this coordinator never authorizes delivery.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol
import uuid

from .owner_codex_tasks import OwnerAuthority, OwnerRef, ServerValidatedTaskRequest, TASK_KIND, WorkerAuthority

RUNTIME_VERSION = '0.160.0'
MODEL_DEPLOYMENT = 'gpt-5-6-sol-apac'
PROMPT_VERSION = 'owner-performance-series-review-v1'
EXECUTION_CAPABILITY = 'owner_performance_series_review'
AUDIT_NAMESPACE = 'owner_codex_worker_v1'
MAX_ATTEMPTS = 1000
STATUSES = frozenset({'succeeded', 'failed', 'cancelled', 'denied', 'needs_reconciliation'})
REASONS = frozenset({'cancel_requested', 'access_denied', 'authorization_unavailable',
                    'control_unavailable', 'lease_lost', 'intent_unverified',
                    'observation_failed', 'observation_scope_invalid', 'inference_start_uncertain',
                    'inference_failed', 'inference_deadline', 'inference_scope_invalid',
                    'stop_unverified', 'outcome_unverified', 'attempt_already_consumed',
                    'invalid_request', 'delivery_withheld', 'worker_busy', 'coordinator_capacity',
                    'worker_quarantined'})


class WorkerError(ValueError):
    """Fixed codes only, never callback/provider exception text."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in REASONS else 'invalid_request'
        super().__init__(self.code)


def _uuid(value):
    if type(value) is not str:
        raise WorkerError('invalid_request')
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise WorkerError('invalid_request') from None


def _sha(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value):
        raise WorkerError('invalid_request')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


@dataclass(frozen=True)
class ReviewWork:
    owner: OwnerRef
    task_id: str
    attempt_id: str
    worker_id: str
    lease_token: str
    revision: int
    request_sha256: str
    request: ServerValidatedTaskRequest

    def __post_init__(self):
        if type(self.owner) is not OwnerRef or type(self.request) is not ServerValidatedTaskRequest:
            raise WorkerError('invalid_request')
        for value in (self.task_id, self.attempt_id, self.worker_id, self.lease_token):
            _uuid(value)
        _sha(self.request_sha256)
        if type(self.revision) is not int or self.revision < 1 or self.request.task_kind != TASK_KIND:
            raise WorkerError('invalid_request')


@dataclass(frozen=True)
class ExecutionAuthority:
    """Fresh server-resolved execution grant, separate from metadata access."""
    owner_authority: OwnerAuthority
    worker_authority: WorkerAuthority
    policy_sha256: str
    capability: str = EXECUTION_CAPABILITY
    source_mutation_authority: bool = False
    tools_authorized: bool = False

    def __post_init__(self):
        _sha(self.policy_sha256)
        if (type(self.owner_authority) is not OwnerAuthority
                or type(self.worker_authority) is not WorkerAuthority
                or self.capability != EXECUTION_CAPABILITY
                or self.source_mutation_authority is not False or self.tools_authorized is not False):
            raise WorkerError('access_denied')


@dataclass(frozen=True)
class TaskControl:
    task_id: str
    attempt_id: str
    lease_token: str
    revision: int
    lease_valid: bool
    cancellation_requested: bool
    state: str = 'running'

    def __post_init__(self):
        for value in (self.task_id, self.attempt_id, self.lease_token):
            _uuid(value)
        if (type(self.revision) is not int or self.revision < 1
                or type(self.lease_valid) is not bool or type(self.cancellation_requested) is not bool
                or type(self.state) is not str
                or self.state not in {'running', 'cancel_requested', 'needs_reconciliation'}):
            raise WorkerError('invalid_request')


@dataclass(frozen=True)
class SeriesObservation:
    """Private server-held series descriptor, not raw IBKR data or a URI."""
    subject_ids: tuple[str, ...]
    input_sha256: str
    observation_sha256: str
    record_count: int

    def __post_init__(self):
        ServerValidatedTaskRequest(self.subject_ids, self.input_sha256)
        _sha(self.observation_sha256)
        if type(self.record_count) is not int or not 1 <= self.record_count <= 100000:
            raise WorkerError('invalid_request')


@dataclass(frozen=True)
class ReviewResult:
    """Independently validated private artifact descriptor; no model text."""
    attempt_id: str
    subject_ids: tuple[str, ...]
    input_sha256: str
    observation_sha256: str
    result_sha256: str
    process_stopped_verified: bool
    process_exit_code: int
    tool_call_count: int
    runtime_version: str = RUNTIME_VERSION
    model_deployment: str = MODEL_DEPLOYMENT
    prompt_version: str = PROMPT_VERSION

    def __post_init__(self):
        _uuid(self.attempt_id)
        ServerValidatedTaskRequest(self.subject_ids, self.input_sha256)
        for value in (self.observation_sha256, self.result_sha256):
            _sha(value)
        if (self.runtime_version != RUNTIME_VERSION or self.model_deployment != MODEL_DEPLOYMENT
                or self.prompt_version != PROMPT_VERSION or self.process_stopped_verified is not True
                or type(self.process_exit_code) is not int or self.process_exit_code != 0
                or type(self.tool_call_count) is not int or self.tool_call_count != 0):
            raise WorkerError('inference_scope_invalid')


@dataclass(frozen=True)
class StoppedInference:
    attempt_id: str
    process_stopped_verified: bool

    def __post_init__(self):
        _uuid(self.attempt_id)
        if type(self.process_stopped_verified) is not bool:
            raise WorkerError('invalid_request')


@dataclass(frozen=True)
class InferenceHandle:
    attempt_id: str
    wait: Callable[[], Awaitable[ReviewResult]]
    stop: Callable[[], Awaitable[StoppedInference]]

    def __post_init__(self):
        _uuid(self.attempt_id)
        if not callable(self.wait) or not callable(self.stop):
            raise WorkerError('invalid_request')


@dataclass(frozen=True)
class WorkerEvidence:
    work: ReviewWork
    phase: str
    status: str | None
    code: str | None
    process_state: str
    inference_attempted: bool
    policy_sha256: str | None = None
    owner_authority_sha256: str | None = None
    worker_service_principal_id: str | None = None
    intent_receipt_sha256: str | None = None
    observation_sha256: str | None = None
    result_sha256: str | None = None

    def __post_init__(self):
        if (type(self.work) is not ReviewWork or type(self.phase) is not str
                or self.phase not in {'intent', 'outcome'}
                or (self.status is not None and type(self.status) is not str)
                or (self.code is not None and type(self.code) is not str)
                or type(self.inference_attempted) is not bool
                or type(self.process_state) is not str
                or self.process_state not in {'not_started', 'stopped', 'unknown'}
                or (self.process_state == 'not_started') != (not self.inference_attempted)):
            raise WorkerError('invalid_request')
        for value in (self.policy_sha256, self.owner_authority_sha256,
                      self.intent_receipt_sha256, self.observation_sha256, self.result_sha256):
            if value is not None:
                _sha(value)
        if self.worker_service_principal_id is not None:
            _uuid(self.worker_service_principal_id)
            if self.worker_service_principal_id == self.work.owner.object_id:
                raise WorkerError('invalid_request')
        if self.phase == 'intent':
            if (self.status is not None or self.code is not None or self.inference_attempted
                    or self.policy_sha256 is None or self.owner_authority_sha256 is None
                    or self.worker_service_principal_id is None
                    or any(v is not None for v in (self.intent_receipt_sha256,
                                                  self.observation_sha256, self.result_sha256))):
                raise WorkerError('invalid_request')
        elif (self.status not in STATUSES
                or (self.status == 'succeeded') != (self.code is None)
                or (self.code is not None and self.code not in REASONS)
                or (self.status == 'succeeded') != (self.result_sha256 is not None)
                or (self.status == 'succeeded' and (self.process_state != 'stopped'
                    or any(v is None for v in (self.policy_sha256, self.owner_authority_sha256,
                                               self.worker_service_principal_id,
                                               self.intent_receipt_sha256, self.observation_sha256))))):
            raise WorkerError('invalid_request')

    def payload(self):
        return dict(schema_version=1, namespace=AUDIT_NAMESPACE, phase=self.phase,
                    tenant_id=self.work.owner.tenant_id, owner_object_id=self.work.owner.object_id,
                    task_id=self.work.task_id, attempt_id=self.work.attempt_id,
                    worker_id=self.work.worker_id, task_kind=TASK_KIND,
                    subject_scope_sha256=_digest(list(self.work.request.subject_ids)),
                    request_sha256=self.work.request_sha256, input_sha256=self.work.request.input_sha256,
                    runtime_version=RUNTIME_VERSION, model_deployment=MODEL_DEPLOYMENT,
                    prompt_version=PROMPT_VERSION, status=self.status, code=self.code,
                    process_state=self.process_state, inference_attempted=self.inference_attempted,
                    policy_sha256=self.policy_sha256, owner_authority_sha256=self.owner_authority_sha256,
                    worker_service_principal_id=self.worker_service_principal_id,
                    intent_receipt_sha256=self.intent_receipt_sha256,
                    observation_sha256=self.observation_sha256, result_sha256=self.result_sha256,
                    result_delivery_confirmed=False, source_mutation_authority=False,
                    tools_authorized=False)


@dataclass(frozen=True)
class ProtectedWorkerReceipt:
    record_sha256: str
    binding_sha256: str
    namespace: str
    database_committed: bool
    protected_archive_verified: bool
    independently_verified: bool
    newly_created: bool

    def __post_init__(self):
        _sha(self.record_sha256)
        _sha(self.binding_sha256)
        if (self.namespace != AUDIT_NAMESPACE
                or any(type(v) is not bool for v in (self.database_committed,
                    self.protected_archive_verified, self.independently_verified, self.newly_created))):
            raise WorkerError('invalid_request')


class Authorize(Protocol):
    async def __call__(self, work: ReviewWork) -> ExecutionAuthority | None: ...


class CheckControl(Protocol):
    async def __call__(self, work: ReviewWork) -> TaskControl: ...


class Protect(Protocol):
    async def __call__(self, evidence: WorkerEvidence) -> ProtectedWorkerReceipt: ...


class Observe(Protocol):
    async def __call__(self, work: ReviewWork) -> SeriesObservation: ...


class StartInference(Protocol):
    async def __call__(self, work: ReviewWork, observation: SeriesObservation) -> InferenceHandle: ...


@dataclass(frozen=True)
class WorkerCallbacks:
    authorize: Authorize
    check_control: CheckControl
    protect_intent: Protect
    observe: Observe
    start_inference: StartInference
    protect_outcome: Protect

    def __post_init__(self):
        if not all(callable(v) for v in (self.authorize, self.check_control, self.protect_intent,
                                        self.observe, self.start_inference, self.protect_outcome)):
            raise WorkerError('invalid_request')


@dataclass(frozen=True)
class WorkerDecision:
    status: str
    code: str | None
    result_sha256: str | None
    outcome_receipt_sha256: str | None
    protected_outcome_verified: bool
    result_delivery_authorized: bool = False
    automatic_retry_authorized: bool = False

    def __post_init__(self):
        if (type(self.status) is not str or self.status not in STATUSES
                or (self.code is not None and type(self.code) is not str)
                or (self.status == 'succeeded') != (self.code is None)
                or (self.code is not None and self.code not in REASONS)
                or type(self.protected_outcome_verified) is not bool
                or self.result_delivery_authorized is not False or self.automatic_retry_authorized is not False
                or (self.status == 'succeeded') != (self.result_sha256 is not None)
                or self.protected_outcome_verified != (self.outcome_receipt_sha256 is not None)
                or (self.status == 'succeeded' and not self.protected_outcome_verified)):
            raise WorkerError('invalid_request')
        for value in (self.result_sha256, self.outcome_receipt_sha256):
            if value is not None:
                _sha(value)


def _discard(task):
    """Bound our wait, consume late failure, never accept a late side effect."""
    if not task.done():
        task.cancel()
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())


async def _bounded(callback, args, timeout):
    async def invoke():
        return await callback(*args)
    pending = asyncio.create_task(invoke())
    try:
        done, _ = await asyncio.wait({pending}, timeout=timeout)
        if pending not in done:
            raise TimeoutError()
        if pending.cancelled():
            raise WorkerError('invalid_request')
        return pending.result()
    finally:
        _discard(pending)


class _Halt(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


class OwnerReviewCoordinator:
    """One bounded attempt; callbacks are trusted server wiring, never forms.

    The protected intent callback must atomically reserve a NEW approved logical
    task execution (tenant/owner/task), not merely a fresh attempt UUID or lease,
    prove protected readback/commit with a separate audit identity, and reject
    replay. Audit outcomes remain possible after revocation without granting
    access to source data or results. No owner task DB status is changed here.
    """
    def __init__(self, callbacks: WorkerCallbacks, *, call_timeout=10.0,
                 inference_timeout=80.0, poll_interval=0.25, stop_timeout=5.0):
        if type(callbacks) is not WorkerCallbacks:
            raise WorkerError('invalid_request')
        for value, maximum in ((call_timeout, 30), (inference_timeout, 80),
                               (poll_interval, 1), (stop_timeout, 10)):
            if type(value) not in {int, float} or not 0 < value <= maximum:
                raise WorkerError('invalid_request')
        self.callbacks = callbacks
        self.call_timeout = call_timeout
        self.inference_timeout = inference_timeout
        self.poll_interval = poll_interval
        self.stop_timeout = stop_timeout
        self._consumed = set()
        self._active = False
        self._quarantined = False

    async def _call(self, callback, *args, code):
        try:
            return await _bounded(callback, args, self.call_timeout)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise _Halt('needs_reconciliation', code) from None

    async def _fresh(self, work, last_revision, policy_sha256=None, worker_service_principal_id=None):
        authority = await self._call(self.callbacks.authorize, work, code='authorization_unavailable')
        if type(authority) is not ExecutionAuthority:
            raise _Halt('denied', 'access_denied')
        owner, worker = authority.owner_authority, authority.worker_authority
        if (authority.capability != EXECUTION_CAPABILITY or authority.source_mutation_authority is not False
                or authority.tools_authorized is not False or owner.role != 'owner'
                or owner.authorization_source != 'database' or worker.enabled is not True
                or worker.capability != 'owner_task_metadata' or worker.authorization_source != 'database_service'
                or (policy_sha256 is not None and authority.policy_sha256 != policy_sha256)
                or (worker_service_principal_id is not None
                    and worker.service_principal_id != worker_service_principal_id)
                or owner.owner != work.owner or worker.tenant_id != work.owner.tenant_id
                or worker.worker_id != work.worker_id or worker.service_principal_id == work.owner.object_id
                or not set(work.request.subject_ids).issubset(owner.authorized_subject_ids)):
            raise _Halt('denied', 'access_denied')
        control = await self._call(self.callbacks.check_control, work, code='control_unavailable')
        if (type(control) is not TaskControl or control.task_id != work.task_id
                or control.attempt_id != work.attempt_id or control.lease_token != work.lease_token
                or control.revision < last_revision or not control.lease_valid
                or control.state == 'needs_reconciliation'):
            raise _Halt('needs_reconciliation', 'lease_lost')
        if control.cancellation_requested or control.state == 'cancel_requested':
            raise _Halt('cancelled', 'cancel_requested')
        return control.revision, authority.policy_sha256, owner.authority_sha256, worker.service_principal_id

    @staticmethod
    def _verified(receipt, evidence):
        return (type(receipt) is ProtectedWorkerReceipt
                and receipt.binding_sha256 == _digest(evidence.payload())
                and receipt.database_committed is True and receipt.protected_archive_verified is True
                and receipt.independently_verified is True and receipt.newly_created is True)

    async def _stop(self, handle, work, waiting):
        verified = False
        try:
            ack = await _bounded(handle.stop, (), self.stop_timeout)
            verified = (type(ack) is StoppedInference and ack.attempt_id == work.attempt_id
                        and ack.process_stopped_verified is True)
        except Exception:
            pass
        finally:
            if waiting is not None and not waiting.done():
                _discard(waiting)
            if waiting is not None:
                # Even an uncooperative wait cannot release a result. Its late
                # exception is consumed; runtime stop is separately verified.
                waiting.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return verified

    async def run(self, work: ReviewWork):
        if type(work) is not ReviewWork:
            raise WorkerError('invalid_request')
        key = (work.owner.tenant_id, work.owner.object_id, work.task_id)
        if key in self._consumed:
            return WorkerDecision('needs_reconciliation', 'attempt_already_consumed', None, None, False)
        if self._quarantined:
            return WorkerDecision('needs_reconciliation', 'worker_quarantined', None, None, False)
        if self._active:
            return WorkerDecision('needs_reconciliation', 'worker_busy', None, None, False)
        if len(self._consumed) >= MAX_ATTEMPTS:
            return WorkerDecision('needs_reconciliation', 'coordinator_capacity', None, None, False)
        self._consumed.add(key)
        self._active = True
        try:
            return await self._run(work)
        finally:
            self._active = False

    async def _run(self, work):
        handle = waiting = observation = intent = result = None
        attempted = False
        stopped = False
        revision = work.revision
        policy_sha256 = owner_authority_sha256 = worker_service_principal_id = None
        status, code = 'needs_reconciliation', 'invalid_request'
        externally_cancelled = False
        try:
            revision, policy_sha256, owner_authority_sha256, worker_service_principal_id = await self._fresh(work, revision)
            evidence = WorkerEvidence(work, 'intent', None, None, 'not_started', False,
                                      policy_sha256, owner_authority_sha256, worker_service_principal_id)
            intent = await self._call(self.callbacks.protect_intent, evidence, code='intent_unverified')
            if not self._verified(intent, evidence):
                intent = None
                raise _Halt('needs_reconciliation', 'intent_unverified')
            revision, _, _, _ = await self._fresh(work, revision, policy_sha256, worker_service_principal_id)
            observation = await self._call(self.callbacks.observe, work, code='observation_failed')
            if (type(observation) is not SeriesObservation or observation.subject_ids != work.request.subject_ids
                    or observation.input_sha256 != work.request.input_sha256):
                observation = None
                raise _Halt('failed', 'observation_scope_invalid')
            revision, _, _, _ = await self._fresh(work, revision, policy_sha256, worker_service_principal_id)
            # An exception/timeout here may occur after a child actually starts.
            # No handle means we cannot prove it stopped, so never auto-retry.
            attempted = True
            handle = await self._call(self.callbacks.start_inference, work, observation,
                                      code='inference_start_uncertain')
            if type(handle) is not InferenceHandle or handle.attempt_id != work.attempt_id:
                handle = None
                raise _Halt('needs_reconciliation', 'inference_start_uncertain')
            waiting = asyncio.create_task(handle.wait())
            deadline = asyncio.get_running_loop().time() + self.inference_timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise _Halt('failed', 'inference_deadline')
                try:
                    revision, _, _, _ = await _bounded(self._fresh,
                        (work, revision, policy_sha256, worker_service_principal_id), remaining)
                except TimeoutError:
                    raise _Halt('failed', 'inference_deadline') from None
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise _Halt('failed', 'inference_deadline')
                done, _ = await asyncio.wait({waiting}, timeout=min(self.poll_interval, remaining))
                if waiting in done:
                    try:
                        result = waiting.result()
                    except asyncio.CancelledError:
                        raise _Halt('failed', 'inference_failed') from None
                    except Exception:
                        raise _Halt('failed', 'inference_failed') from None
                    if (type(result) is not ReviewResult or result.attempt_id != work.attempt_id
                            or result.subject_ids != work.request.subject_ids
                            or result.input_sha256 != work.request.input_sha256
                            or result.observation_sha256 != observation.observation_sha256):
                        result = None
                        raise _Halt('failed', 'inference_scope_invalid')
                    stopped = True
                    revision, _, _, _ = await self._fresh(work, revision, policy_sha256, worker_service_principal_id)
                    status, code = 'succeeded', None
                    break
        except _Halt as halt:
            status, code = halt.status, halt.code
        except asyncio.CancelledError:
            externally_cancelled = True
            status, code = 'cancelled', 'cancel_requested'
        except Exception:
            status, code = 'needs_reconciliation', 'inference_start_uncertain' if attempted else 'invalid_request'

        async def finalize():
            nonlocal stopped, status, code
            if handle is not None and not stopped:
                stopped = await self._stop(handle, work, waiting)
                if not stopped:
                    status, code = 'needs_reconciliation', 'stop_unverified'
            elif attempted and handle is None:
                status, code = 'needs_reconciliation', 'inference_start_uncertain'
            if attempted and not stopped:
                self._quarantined = True
            return await self._outcome(work, status, code, attempted, stopped, intent,
                                       observation, result, revision, policy_sha256,
                                       owner_authority_sha256, worker_service_principal_id)

        # Keep bounded owned cleanup/audit running even if the caller cancels
        # repeatedly. This does not prove cessation when the adapter cannot.
        cleanup = asyncio.create_task(finalize())
        while True:
            try:
                decision = await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                externally_cancelled = True
                if cleanup.cancelled():
                    decision = WorkerDecision('needs_reconciliation', 'outcome_unverified', None, None, False)
                    break
        if externally_cancelled:
            raise asyncio.CancelledError()
        return decision

    async def _outcome(self, work, status, code, attempted, stopped, intent,
                       observation, result, revision, policy_sha256,
                       owner_authority_sha256, worker_service_principal_id):
        evidence = WorkerEvidence(work, 'outcome', status, code,
            'stopped' if stopped else 'unknown' if attempted else 'not_started', attempted,
            policy_sha256, owner_authority_sha256, worker_service_principal_id,
            intent.record_sha256 if type(intent) is ProtectedWorkerReceipt else None,
            observation.observation_sha256 if type(observation) is SeriesObservation else None,
            result.result_sha256 if status == 'succeeded' else None)
        try:
            receipt = await self._call(self.callbacks.protect_outcome, evidence, code='outcome_unverified')
            if not self._verified(receipt, evidence):
                raise _Halt('needs_reconciliation', 'outcome_unverified')
            if status == 'succeeded':
                # Completion recording is not delivery authorization. Revocation
                # during protected archival still withholds the result reference.
                try:
                    await self._fresh(work, revision, policy_sha256, worker_service_principal_id)
                except _Halt:
                    status, code = 'denied', 'delivery_withheld'
            decision = WorkerDecision(status, code, result.result_sha256 if status == 'succeeded' else None,
                                      receipt.record_sha256, True)
        except (_Halt, asyncio.CancelledError):
            decision = WorkerDecision('needs_reconciliation', 'outcome_unverified', None, None, False)
        return decision
