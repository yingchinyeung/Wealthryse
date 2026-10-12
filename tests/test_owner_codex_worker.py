"""Async synthetic coordinator acceptance, NOT live CLI/Azure/DB acceptance."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import unittest

from ibkr_etl.owner_codex_tasks import (
    OwnerAuthority, OwnerRef, ServerValidatedTaskRequest, WorkerAuthority,
)
from ibkr_etl.owner_codex_worker import (
    AUDIT_NAMESPACE, EXECUTION_CAPABILITY, MAX_ATTEMPTS, MODEL_DEPLOYMENT,
    PROMPT_VERSION, RUNTIME_VERSION, ExecutionAuthority, InferenceHandle,
    OwnerReviewCoordinator, ProtectedWorkerReceipt, ReviewResult, ReviewWork,
    SeriesObservation, StoppedInference, TaskControl, WorkerCallbacks,
    WorkerDecision, WorkerError, WorkerEvidence, _digest,
)


def uid(number):
    return f'00000000-0000-0000-0000-{number:012d}'


H = 'a' * 64
OWNER = OwnerRef(uid(1), uid(2))
WORKER = uid(3)
SUBJECTS = ('U16088571',)
REQUEST = ServerValidatedTaskRequest(SUBJECTS, H)
WORK = ReviewWork(OWNER, uid(4), uid(5), WORKER, uid(6), 1, 'b' * 64, REQUEST)


class RecordingFixture:
    """Typed callbacks, exact synthetic receipts, no prompts/raw series/runtime."""
    def __init__(self):
        self.calls = []
        self.authorize_calls = self.control_calls = 0
        self.authorized = SUBJECTS
        self.owner = OWNER
        self.worker = WorkerAuthority(OWNER.tenant_id, WORKER, uid(7))
        self.policy_sha256 = 'c' * 64
        self.authority = None
        self.use_authority_override = False
        self.authorization_error = False
        self.authorization_hook = None
        self.control_hook = None
        self.control = TaskControl(WORK.task_id, WORK.attempt_id, WORK.lease_token, 1, True, False)
        self.intent_evidence = self.outcome_evidence = None
        self.intent_modifier = self.outcome_modifier = None
        self.intent_error = self.outcome_error = False
        self.outcome_hook = self.observe_hook = None
        self.observation = SeriesObservation(SUBJECTS, H, 'd' * 64, 2)
        self.result = ReviewResult(WORK.attempt_id, SUBJECTS, H, 'd' * 64, 'e' * 64, True, 0, 0)
        self.observation_error = self.start_error = self.wait_error = self.stop_error = False
        self.start_value = None
        self.wait_started = asyncio.Event()
        self.start_called = asyncio.Event()
        self.stop_called = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.stop_release = asyncio.Event()
        self.stop_release.set()
        self.stop_ack = StoppedInference(WORK.attempt_id, True)

    async def authorize(self, work):
        self.calls.append('authorize')
        self.authorize_calls += 1
        if self.authorization_hook:
            self.authorization_hook(self)
        if self.authorization_error:
            raise RuntimeError('private provider credential')
        if self.use_authority_override:
            return self.authority
        return ExecutionAuthority(OwnerAuthority(self.owner, ('U16088571', 'U16088606'),
            self.authorized, 'f' * 64), self.worker, self.policy_sha256)

    async def check_control(self, work):
        self.calls.append('control')
        self.control_calls += 1
        if self.control_hook:
            self.control_hook(self)
        return self.control

    def receipt(self, evidence):
        return ProtectedWorkerReceipt(_digest(['synthetic', evidence.payload()]),
            _digest(evidence.payload()), AUDIT_NAMESPACE, True, True, True, True)

    async def protect_intent(self, evidence):
        self.calls.append('intent')
        self.intent_evidence = evidence
        if self.intent_error:
            raise RuntimeError('private audit credential')
        receipt = self.receipt(evidence)
        return self.intent_modifier(receipt) if self.intent_modifier else receipt

    async def observe(self, work):
        self.calls.append('observe')
        if self.observation_error:
            raise RuntimeError('raw IBKR values must never appear')
        if self.observe_hook:
            self.observe_hook(self)
        return self.observation

    async def start_inference(self, work, observation):
        self.calls.append('start')
        self.start_called.set()
        if self.start_error:
            raise RuntimeError('child may have started; private model text')
        return self.start_value or InferenceHandle(work.attempt_id, self.wait, self.stop)

    async def wait(self):
        self.calls.append('wait')
        self.wait_started.set()
        await self.release.wait()
        if self.wait_error:
            raise RuntimeError('private model reasoning must not appear')
        return self.result

    async def stop(self):
        self.calls.append('stop')
        self.stop_called.set()
        await self.stop_release.wait()
        if self.stop_error:
            raise RuntimeError('private process details')
        return self.stop_ack

    async def protect_outcome(self, evidence):
        self.calls.append('outcome')
        self.outcome_evidence = evidence
        if self.outcome_hook:
            self.outcome_hook(self)
        if self.outcome_error:
            raise RuntimeError('private archive credential')
        receipt = self.receipt(evidence)
        return self.outcome_modifier(receipt) if self.outcome_modifier else receipt

    def callbacks(self, **overrides):
        fields = dict(authorize=self.authorize, check_control=self.check_control,
                      protect_intent=self.protect_intent, observe=self.observe,
                      start_inference=self.start_inference, protect_outcome=self.protect_outcome)
        fields.update(overrides)
        return WorkerCallbacks(**fields)


class WorkerContractTests(unittest.TestCase):
    def test_fixed_contract_has_no_prompt_command_resume_or_source_fields(self):
        self.assertEqual(RUNTIME_VERSION, '0.160.0')
        self.assertEqual(MODEL_DEPLOYMENT, 'gpt-5-6-sol-apac')
        self.assertEqual(PROMPT_VERSION, 'owner-performance-series-review-v1')
        for kind in (ReviewWork, SeriesObservation, ReviewResult, InferenceHandle, WorkerCallbacks):
            self.assertTrue({'command', 'prompt', 'raw_data', 'reasoning', 'resume', 'tool'}
                            .isdisjoint(kind.__dataclass_fields__))
        with self.assertRaises(TypeError):
            ReviewWork(**WORK.__dict__, prompt='arbitrary')
        with self.assertRaises(ValueError):
            ServerValidatedTaskRequest(SUBJECTS, H, task_kind='arbitrary_code')

    def test_execution_is_not_metadata_authority(self):
        owner = OwnerAuthority(OWNER, SUBJECTS, SUBJECTS, H)
        worker = WorkerAuthority(OWNER.tenant_id, WORKER, uid(7))
        for fields in ({'capability': 'owner_task_metadata'}, {'tools_authorized': True},
                       {'source_mutation_authority': True}, {'owner_authority': None}):
            params = dict(owner_authority=owner, worker_authority=worker, policy_sha256=H)
            params.update(fields)
            with self.subTest(fields=fields), self.assertRaises(WorkerError):
                ExecutionAuthority(**params)

    def test_result_pins_and_cessation_are_strict(self):
        result = ReviewResult(WORK.attempt_id, SUBJECTS, H, H, H, True, 0, 0)
        for fields in ({'runtime_version': 'latest'}, {'model_deployment': 'public-model'},
            {'prompt_version': 'arbitrary'}, {'process_stopped_verified': False},
            {'process_exit_code': 1}, {'process_exit_code': False},
            {'tool_call_count': 1}, {'tool_call_count': False}):
            with self.subTest(fields=fields), self.assertRaises(WorkerError):
                replace(result, **fields)

    def test_cessation_exit_and_tools_are_never_implicitly_acknowledged(self):
        params = dict(attempt_id=WORK.attempt_id, subject_ids=SUBJECTS, input_sha256=H,
            observation_sha256=H, result_sha256=H,
            process_stopped_verified=True, process_exit_code=0, tool_call_count=0)
        for field in ('process_stopped_verified', 'process_exit_code', 'tool_call_count'):
            with self.subTest(field=field), self.assertRaises(TypeError):
                ReviewResult(**{key: value for key, value in params.items() if key != field})

    def test_contract_bounds_and_boolean_ambiguity(self):
        for count in (True, 0, 100001):
            with self.subTest(count=count), self.assertRaises(WorkerError):
                SeriesObservation(SUBJECTS, H, H, count)
        for revision in (True, 0):
            with self.assertRaises(WorkerError):
                replace(WORK, revision=revision)
        for field in ('call_timeout', 'inference_timeout', 'poll_interval', 'stop_timeout'):
            for value in (True, 0, -1, float('nan'), float('inf')):
                with self.subTest(field=field, value=value), self.assertRaises(WorkerError):
                    OwnerReviewCoordinator(RecordingFixture().callbacks(), **{field: value})

    def test_evidence_and_decision_cannot_claim_unverified_success_or_delivery(self):
        evidence = WorkerEvidence(WORK, 'intent', None, None, 'not_started', False, H, H, uid(7))
        for fields in ({'phase': 'arbitrary'}, {'status': 'succeeded'}, {'inference_attempted': True},
                       {'result_sha256': H}, {'policy_sha256': None}):
            with self.subTest(fields=fields), self.assertRaises(WorkerError):
                replace(evidence, **fields)
        for fields in ({'result_delivery_authorized': True}, {'automatic_retry_authorized': True},
                       {'protected_outcome_verified': True}, {'status': 'succeeded', 'code': None}):
            with self.subTest(fields=fields), self.assertRaises(WorkerError):
                replace(WorkerDecision('failed', 'inference_failed', None, None, False), **fields)

    def test_malformed_metadata_types_have_fixed_errors(self):
        evidence = WorkerEvidence(WORK, 'intent', None, None, 'not_started', False, H, H, uid(7))
        for field in ('phase', 'status', 'code', 'process_state'):
            with self.subTest(field=field), self.assertRaises(WorkerError):
                replace(evidence, **{field: ['private payload']})
        decision = WorkerDecision('failed', 'inference_failed', None, None, False)
        for field in ('status', 'code'):
            with self.subTest(field=field), self.assertRaises(WorkerError):
                replace(decision, **{field: ['private payload']})
        self.assertEqual(str(WorkerError(['private payload'])), 'invalid_request')


class WorkerCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = RecordingFixture()

    def coordinator(self, **settings):
        return OwnerReviewCoordinator(self.fixture.callbacks(), call_timeout=0.2,
            inference_timeout=settings.pop('inference_timeout', 0.5),
            poll_interval=0.005, stop_timeout=0.2, **settings)

    def withheld(self, decision):
        self.assertIsNone(decision.result_sha256)
        self.assertFalse(decision.result_delivery_authorized)
        self.assertFalse(decision.automatic_retry_authorized)

    async def launch_blocked(self, coordinator=None):
        self.fixture.release.clear()
        task = asyncio.create_task((coordinator or self.coordinator()).run(WORK))
        await asyncio.wait_for(self.fixture.wait_started.wait(), 1)
        return task

    async def test_normal_order_exact_bound_receipts_and_metadata_only(self):
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.result_sha256, 'e' * 64)
        self.assertTrue(result.protected_outcome_verified)
        self.assertFalse(result.result_delivery_authorized)
        self.assertFalse(result.automatic_retry_authorized)
        calls = self.fixture.calls
        self.assertLess(calls.index('intent'), calls.index('observe'))
        self.assertLess(calls.index('observe'), calls.index('start'))
        self.assertLess(calls.index('start'), calls.index('outcome'))
        self.assertGreaterEqual(self.fixture.authorize_calls, 6)
        self.assertEqual(self.fixture.authorize_calls, self.fixture.control_calls)
        self.assertNotIn('stop', calls)
        payload = self.fixture.outcome_evidence.payload()
        self.assertEqual(payload['policy_sha256'], 'c' * 64)
        self.assertEqual(payload['owner_authority_sha256'], 'f' * 64)
        self.assertEqual(payload['worker_service_principal_id'], uid(7))
        self.assertEqual(payload['process_state'], 'stopped')
        self.assertEqual(payload['intent_receipt_sha256'],
                         self.fixture.receipt(self.fixture.intent_evidence).record_sha256)
        encoded = json.dumps(payload)
        self.assertNotIn(WORK.lease_token, encoded)
        self.assertNotIn(SUBJECTS[0], encoded)
        self.assertFalse({'prompt', 'reasoning', 'raw_data', 'command'} & payload.keys())

    async def test_metadata_worker_permission_is_not_an_execution_grant(self):
        self.fixture.use_authority_override = True
        self.fixture.authority = self.fixture.worker
        result = await self.coordinator().run(WORK)
        self.assertEqual((result.status, result.code), ('denied', 'access_denied'))
        self.assertNotIn('intent', self.fixture.calls)
        self.assertNotIn('observe', self.fixture.calls)
        self.assertNotIn('start', self.fixture.calls)
        self.withheld(result)

    async def test_authority_identity_and_account_scope_are_exact(self):
        cases = ({'authorized': ()}, {'owner': OwnerRef(uid(1), uid(99))},
            {'worker': WorkerAuthority(uid(99), WORKER, uid(7))},
            {'worker': WorkerAuthority(uid(1), uid(99), uid(7))},
            {'worker': WorkerAuthority(uid(1), WORKER, OWNER.object_id)})
        for fields in cases:
            with self.subTest(fields=fields):
                self.fixture = RecordingFixture()
                for field, value in fields.items():
                    setattr(self.fixture, field, value)
                result = await self.coordinator().run(WORK)
                self.assertEqual(result.code, 'access_denied')
                self.assertNotIn('observe', self.fixture.calls)
                self.withheld(result)

    async def test_intent_failure_prevents_all_observation_and_inference(self):
        self.fixture.intent_error = True
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'intent_unverified')
        self.assertNotIn('observe', self.fixture.calls)
        self.assertNotIn('start', self.fixture.calls)
        self.assertEqual(self.fixture.outcome_evidence.process_state, 'not_started')
        self.withheld(result)

    async def test_all_intent_receipt_guarantees_and_binding_required(self):
        for fields in ({'binding_sha256': '0' * 64}, {'database_committed': False},
                       {'protected_archive_verified': False}, {'independently_verified': False},
                       {'newly_created': False}):
            with self.subTest(fields=fields):
                self.fixture = RecordingFixture()
                self.fixture.intent_modifier = lambda receipt: replace(receipt, **fields)
                result = await self.coordinator().run(WORK)
                self.assertEqual(result.code, 'intent_unverified')
                self.assertNotIn('start', self.fixture.calls)
                self.assertNotIn('observe', self.fixture.calls)
                self.withheld(result)

    async def test_duplicate_attempt_is_not_retried_even_after_failure(self):
        self.fixture.start_error = True
        coordinator = self.coordinator()
        first = await coordinator.run(WORK)
        calls = self.fixture.calls.copy()
        second = await coordinator.run(WORK)
        self.assertEqual(first.code, 'inference_start_uncertain')
        self.assertEqual(second.code, 'attempt_already_consumed')
        self.assertEqual(self.fixture.calls, calls)
        self.withheld(second)

    async def test_new_attempt_uuid_or_lease_cannot_replay_consumed_task(self):
        self.fixture.start_error = True
        coordinator = self.coordinator()
        await coordinator.run(WORK)
        calls = self.fixture.calls.copy()
        for fields in ({'attempt_id': uid(99)}, {'attempt_id': uid(98), 'lease_token': uid(99)}):
            with self.subTest(fields=fields):
                result = await coordinator.run(replace(WORK, **fields))
                self.assertEqual(result.code, 'attempt_already_consumed')
                self.assertEqual(self.fixture.calls, calls)
                self.withheld(result)

    async def test_new_instance_still_requires_create_only_task_reservation(self):
        await self.coordinator().run(WORK)
        self.fixture.calls.clear()
        self.fixture.intent_modifier = lambda receipt: replace(receipt, newly_created=False)
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'intent_unverified')
        self.assertNotIn('start', self.fixture.calls)

    async def test_concurrency_and_memory_are_bounded(self):
        coordinator = self.coordinator()
        task = await self.launch_blocked(coordinator)
        second = await coordinator.run(replace(WORK, task_id=uid(88), attempt_id=uid(89)))
        self.assertEqual(second.code, 'worker_busy')
        self.fixture.release.set()
        await task
        coordinator._consumed.update((uid(1), uid(2), uid(1000 + n)) for n in range(MAX_ATTEMPTS))
        result = await coordinator.run(replace(WORK, task_id=uid(90), attempt_id=uid(91)))
        self.assertEqual(result.code, 'coordinator_capacity')

    async def test_cancel_before_start_never_attempts_runtime(self):
        self.fixture.control = replace(self.fixture.control, cancellation_requested=True)
        result = await self.coordinator().run(WORK)
        self.assertEqual((result.status, result.code), ('cancelled', 'cancel_requested'))
        self.assertNotIn('start', self.fixture.calls)
        self.assertEqual(self.fixture.outcome_evidence.process_state, 'not_started')
        self.withheld(result)

    async def test_cancel_during_wait_requires_exact_owned_stop_ack(self):
        task = await self.launch_blocked()
        self.fixture.control = replace(self.fixture.control, state='cancel_requested', revision=2)
        result = await asyncio.wait_for(task, 1)
        self.assertEqual((result.status, result.code), ('cancelled', 'cancel_requested'))
        self.assertEqual(self.fixture.calls.count('stop'), 1)
        self.assertEqual(self.fixture.outcome_evidence.process_state, 'stopped')
        self.withheld(result)

    async def test_revocation_during_wait_stops_and_withholds(self):
        task = await self.launch_blocked()
        self.fixture.authorized = ()
        result = await asyncio.wait_for(task, 1)
        self.assertEqual(result.code, 'access_denied')
        self.assertTrue(result.protected_outcome_verified)
        self.assertEqual(self.fixture.calls.count('stop'), 1)
        self.withheld(result)

    async def test_revocation_after_observation_prevents_start(self):
        self.fixture.observe_hook = lambda fixture: setattr(fixture, 'authorized', ())
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'access_denied')
        self.assertNotIn('start', self.fixture.calls)
        self.withheld(result)

    async def test_execution_policy_changes_stop_existing_attempt(self):
        task = await self.launch_blocked()
        self.fixture.policy_sha256 = '0' * 64
        result = await asyncio.wait_for(task, 1)
        self.assertEqual(result.code, 'access_denied')
        self.assertEqual(self.fixture.outcome_evidence.policy_sha256, 'c' * 64)
        self.assertIn('stop', self.fixture.calls)
        self.withheld(result)

    async def test_executing_service_principal_cannot_change_during_attempt(self):
        task = await self.launch_blocked()
        self.fixture.worker = replace(self.fixture.worker, service_principal_id=uid(99))
        result = await asyncio.wait_for(task, 1)
        self.assertEqual(result.code, 'access_denied')
        self.assertEqual(self.fixture.outcome_evidence.worker_service_principal_id, uid(7))
        self.assertIn('stop', self.fixture.calls)
        self.withheld(result)

    async def test_revocation_after_intent_prevents_observation(self):
        def revoke(fixture):
            if fixture.intent_evidence is not None:
                fixture.authorized = ()
        self.fixture.authorization_hook = revoke
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'access_denied')
        self.assertNotIn('observe', self.fixture.calls)
        self.assertNotIn('start', self.fixture.calls)
        self.withheld(result)

    async def test_completed_result_still_requires_fresh_permission_before_audit_success(self):
        def revoke(fixture):
            if fixture.authorize_calls == 5:
                fixture.authorized = ()
        self.fixture.authorization_hook = revoke
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'access_denied')
        self.assertNotEqual(self.fixture.outcome_evidence.status, 'succeeded')
        self.assertIsNone(self.fixture.outcome_evidence.result_sha256)
        self.withheld(result)

    async def test_revocation_during_archival_withholds_completed_private_reference(self):
        self.fixture.outcome_hook = lambda fixture: setattr(fixture, 'authorized', ())
        result = await self.coordinator().run(WORK)
        self.assertEqual(self.fixture.outcome_evidence.status, 'succeeded')
        self.assertEqual((result.status, result.code), ('denied', 'delivery_withheld'))
        self.assertTrue(result.protected_outcome_verified)
        self.withheld(result)

    async def test_lease_identity_expiry_and_revision_regression_quarantine(self):
        for fields in ({'lease_token': uid(99)}, {'task_id': uid(99)}, {'attempt_id': uid(99)},
                       {'lease_valid': False}, {'state': 'needs_reconciliation'}, {'revision': 1}):
            with self.subTest(fields=fields):
                self.fixture = RecordingFixture()
                self.fixture.control = replace(self.fixture.control, revision=2)
                task = await self.launch_blocked()
                self.fixture.control = replace(self.fixture.control, **fields)
                result = await asyncio.wait_for(task, 1)
                self.assertEqual((result.status, result.code), ('needs_reconciliation', 'lease_lost'))
                self.assertIn('stop', self.fixture.calls)
                self.withheld(result)

    async def test_authorization_failure_is_fixed_no_private_exception_text(self):
        self.fixture.authorization_error = True
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'authorization_unavailable')
        self.assertNotIn('private', repr(result))
        self.assertNotIn('private', json.dumps(self.fixture.outcome_evidence.payload()))

    async def test_wrong_observation_scope_or_input_is_not_sent_to_inference(self):
        for fields in ({'subject_ids': ('U16088606',)}, {'input_sha256': '0' * 64}):
            with self.subTest(fields=fields):
                self.fixture = RecordingFixture()
                self.fixture.observation = replace(self.fixture.observation, **fields)
                result = await self.coordinator().run(WORK)
                self.assertEqual(result.code, 'observation_scope_invalid')
                self.assertNotIn('start', self.fixture.calls)
                self.withheld(result)

    async def test_observation_exception_never_exposes_source_values(self):
        self.fixture.observation_error = True
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'observation_failed')
        self.assertNotIn('IBKR values', json.dumps(self.fixture.outcome_evidence.payload()))
        self.assertNotIn('start', self.fixture.calls)

    async def test_uncertain_start_is_never_retried_or_claimed_stopped(self):
        self.fixture.start_error = True
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'inference_start_uncertain')
        self.assertEqual(self.fixture.calls.count('start'), 1)
        self.assertNotIn('stop', self.fixture.calls)
        self.assertEqual(self.fixture.outcome_evidence.process_state, 'unknown')
        self.withheld(result)

    async def test_uncertain_runtime_quarantines_worker_even_for_different_task(self):
        self.fixture.start_error = True
        coordinator = self.coordinator()
        await coordinator.run(WORK)
        calls = self.fixture.calls.copy()
        result = await coordinator.run(replace(WORK, task_id=uid(97), attempt_id=uid(98)))
        self.assertEqual(result.code, 'worker_quarantined')
        self.assertEqual(self.fixture.calls, calls)
        self.assertFalse(hasattr(coordinator, 'reset'))
        self.withheld(result)

    async def test_unverified_stop_quarantines_worker_after_external_cancellation(self):
        self.fixture.stop_error = True
        coordinator = self.coordinator()
        task = await self.launch_blocked(coordinator)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        calls = self.fixture.calls.copy()
        result = await coordinator.run(replace(WORK, task_id=uid(97), attempt_id=uid(98)))
        self.assertEqual(result.code, 'worker_quarantined')
        self.assertEqual(self.fixture.calls, calls)
        self.withheld(result)

    async def test_wrong_runtime_handle_cannot_be_used_to_stop_another_attempt(self):
        self.fixture.start_value = InferenceHandle(uid(99), self.fixture.wait, self.fixture.stop)
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'inference_start_uncertain')
        self.assertNotIn('wait', self.fixture.calls)
        self.assertNotIn('stop', self.fixture.calls)
        self.withheld(result)

    async def test_inference_failure_stops_without_retaining_reasoning(self):
        self.fixture.wait_error = True
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'inference_failed')
        self.assertIn('stop', self.fixture.calls)
        self.assertNotIn('reasoning', json.dumps(self.fixture.outcome_evidence.payload()))
        self.withheld(result)

    async def test_self_cancelled_wait_is_a_failure_not_caller_cancellation(self):
        async def cancelled():
            raise asyncio.CancelledError()
        self.fixture.start_value = InferenceHandle(WORK.attempt_id, cancelled, self.fixture.stop)
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'inference_failed')
        self.assertIn('stop', self.fixture.calls)

    async def test_result_scope_and_lineage_mismatch_is_rejected(self):
        for fields in ({'attempt_id': uid(99)}, {'subject_ids': ('U16088606',)},
                       {'input_sha256': '0' * 64}, {'observation_sha256': '0' * 64}):
            with self.subTest(fields=fields):
                self.fixture = RecordingFixture()
                self.fixture.result = replace(self.fixture.result, **fields)
                result = await self.coordinator().run(WORK)
                self.assertEqual(result.code, 'inference_scope_invalid')
                self.assertIn('stop', self.fixture.calls)
                self.withheld(result)

    async def test_inference_deadline_requires_stop_and_no_release(self):
        self.fixture.release.clear()
        result = await self.coordinator(inference_timeout=0.03).run(WORK)
        self.assertEqual(result.code, 'inference_deadline')
        self.assertIn('stop', self.fixture.calls)
        self.withheld(result)

    async def test_slow_fresh_check_cannot_extend_running_inference_deadline(self):
        started = asyncio.Event()
        async def slow_authorize(work):
            if self.fixture.start_called.is_set():
                started.set()
                await asyncio.sleep(5)
            return await self.fixture.authorize(work)
        self.fixture.release.clear()
        coordinator = OwnerReviewCoordinator(self.fixture.callbacks(authorize=slow_authorize),
            call_timeout=0.2, inference_timeout=0.03, poll_interval=0.005, stop_timeout=0.2)
        result = await asyncio.wait_for(coordinator.run(WORK), 0.5)
        self.assertTrue(started.is_set())
        self.assertEqual(result.code, 'inference_deadline')
        self.assertIn('stop', self.fixture.calls)

    async def test_failed_wrong_and_unverified_stop_ack_quarantine(self):
        for fields in ({'attempt_id': uid(99)}, {'process_stopped_verified': False}, {'error': True}):
            with self.subTest(fields=fields):
                self.fixture = RecordingFixture()
                if fields.get('error'):
                    self.fixture.stop_error = True
                else:
                    self.fixture.stop_ack = replace(self.fixture.stop_ack, **fields)
                task = await self.launch_blocked()
                self.fixture.control = replace(self.fixture.control, cancellation_requested=True)
                result = await asyncio.wait_for(task, 1)
                self.assertEqual((result.status, result.code), ('needs_reconciliation', 'stop_unverified'))
                self.assertEqual(self.fixture.outcome_evidence.process_state, 'unknown')
                self.withheld(result)

    async def test_outcome_receipt_failure_withholds_valid_private_result(self):
        for fields in ({'binding_sha256': '0' * 64}, {'database_committed': False},
                       {'protected_archive_verified': False}, {'independently_verified': False},
                       {'newly_created': False}, {'error': True}):
            with self.subTest(fields=fields):
                self.fixture = RecordingFixture()
                if fields.get('error'):
                    self.fixture.outcome_error = True
                else:
                    self.fixture.outcome_modifier = lambda receipt: replace(receipt, **fields)
                result = await self.coordinator().run(WORK)
                self.assertEqual(result.code, 'outcome_unverified')
                self.assertFalse(result.protected_outcome_verified)
                self.withheld(result)

    async def test_external_and_repeated_cancellation_do_not_abandon_owned_stop_and_audit(self):
        self.fixture.stop_release.clear()
        task = await self.launch_blocked()
        task.cancel()
        await asyncio.wait_for(self.fixture.stop_called.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        self.fixture.stop_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        self.assertEqual(self.fixture.calls.count('stop'), 1)
        self.assertEqual(self.fixture.outcome_evidence.status, 'cancelled')
        self.assertEqual(self.fixture.outcome_evidence.process_state, 'stopped')
        self.assertIsNone(self.fixture.outcome_evidence.result_sha256)

    async def test_cancellation_resistant_intent_has_bounded_wait_and_no_late_model_start(self):
        release, completed = asyncio.Event(), asyncio.Event()
        async def stubborn_intent(evidence):
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            completed.set()
            return self.fixture.receipt(evidence)
        coordinator = OwnerReviewCoordinator(self.fixture.callbacks(protect_intent=stubborn_intent),
            call_timeout=0.02, inference_timeout=0.05, poll_interval=0.005, stop_timeout=0.02)
        try:
            result = await asyncio.wait_for(coordinator.run(WORK), 0.3)
            self.assertEqual(result.code, 'intent_unverified')
            self.assertNotIn('start', self.fixture.calls)
        finally:
            release.set()
            await asyncio.wait_for(completed.wait(), 1)
        self.assertNotIn('start', self.fixture.calls)
        self.withheld(result)

    async def test_cancellation_resistant_start_is_unknown_and_quarantined(self):
        release, completed = asyncio.Event(), asyncio.Event()
        async def stubborn_start(work, observation):
            self.fixture.calls.append('start')
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            completed.set()
            return InferenceHandle(work.attempt_id, self.fixture.wait, self.fixture.stop)
        coordinator = OwnerReviewCoordinator(self.fixture.callbacks(start_inference=stubborn_start),
            call_timeout=0.02, inference_timeout=0.05, poll_interval=0.005, stop_timeout=0.02)
        try:
            result = await asyncio.wait_for(coordinator.run(WORK), 0.3)
            self.assertEqual(result.code, 'inference_start_uncertain')
            self.assertEqual(self.fixture.outcome_evidence.process_state, 'unknown')
        finally:
            release.set()
            await asyncio.wait_for(completed.wait(), 1)
        self.assertNotIn('wait', self.fixture.calls)
        self.assertEqual(self.fixture.calls.count('start'), 1)
        self.withheld(result)

    async def test_cancellation_resistant_stop_is_bounded_and_not_false_cessation(self):
        release, completed = asyncio.Event(), asyncio.Event()
        async def stubborn_stop():
            self.fixture.stop_called.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            completed.set()
            return StoppedInference(WORK.attempt_id, True)
        self.fixture.release.clear()
        self.fixture.start_value = InferenceHandle(WORK.attempt_id, self.fixture.wait, stubborn_stop)
        coordinator = OwnerReviewCoordinator(self.fixture.callbacks(),
            call_timeout=0.02, inference_timeout=0.02, poll_interval=0.005, stop_timeout=0.02)
        try:
            result = await asyncio.wait_for(coordinator.run(WORK), 0.3)
            self.assertEqual(result.code, 'stop_unverified')
            self.assertEqual(self.fixture.outcome_evidence.process_state, 'unknown')
        finally:
            release.set()
            await asyncio.wait_for(completed.wait(), 1)
        self.withheld(result)

    async def test_unknown_result_type_cannot_be_released(self):
        self.fixture.result = {'text': 'private model output', 'process_stopped_verified': True}
        result = await self.coordinator().run(WORK)
        self.assertEqual(result.code, 'inference_scope_invalid')
        self.assertNotIn('private model output', json.dumps(self.fixture.outcome_evidence.payload()))
        self.withheld(result)

    async def test_control_exception_never_exposes_private_details(self):
        async def broken(work):
            raise RuntimeError('private database details')
        coordinator = OwnerReviewCoordinator(self.fixture.callbacks(check_control=broken))
        result = await coordinator.run(WORK)
        self.assertEqual(result.code, 'control_unavailable')
        self.assertNotIn('observe', self.fixture.calls)
        self.withheld(result)

    async def test_cancellation_resistant_outcome_is_bounded_and_never_releases(self):
        release, completed = asyncio.Event(), asyncio.Event()
        async def stubborn_outcome(evidence):
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            completed.set()
            return self.fixture.receipt(evidence)
        coordinator = OwnerReviewCoordinator(self.fixture.callbacks(protect_outcome=stubborn_outcome),
            call_timeout=0.02, inference_timeout=0.05, poll_interval=0.005, stop_timeout=0.02)
        try:
            result = await asyncio.wait_for(coordinator.run(WORK), 0.3)
            self.assertEqual(result.code, 'outcome_unverified')
            self.assertFalse(result.protected_outcome_verified)
        finally:
            release.set()
            await asyncio.wait_for(completed.wait(), 1)
        self.withheld(result)

    async def test_late_wait_result_after_verified_stop_never_releases(self):
        release, completed = asyncio.Event(), asyncio.Event()
        async def stubborn_wait():
            self.fixture.wait_started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            completed.set()
            return self.fixture.result
        self.fixture.start_value = InferenceHandle(WORK.attempt_id, stubborn_wait, self.fixture.stop)
        task = await self.launch_blocked()
        try:
            self.fixture.control = replace(self.fixture.control, cancellation_requested=True)
            result = await asyncio.wait_for(task, 0.3)
            self.assertEqual(result.status, 'cancelled')
            self.assertEqual(self.fixture.outcome_evidence.process_state, 'stopped')
            self.assertIsNone(self.fixture.outcome_evidence.result_sha256)
        finally:
            release.set()
            await asyncio.wait_for(completed.wait(), 1)
        self.withheld(result)


if __name__ == '__main__':
    unittest.main()
