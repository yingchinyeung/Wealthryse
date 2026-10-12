"""Inert lifecycle contract for ONE registered disposable PostgreSQL trial.

No network, SQL, Azure, credential, model or production adapter is installed.
Trusted server composition, never a model, supplies the three typed adapters.
Constructing a registration is not human approval, authentication, source-byte
verification or protected retention. Concrete adapters must independently prove
these things, bound to the reviewed operation and current executing identity.

The durable store must reserve the FIXED target once across operations/restarts,
compare prior committed acceptances, and CAS before effects. A new operation UUID
must not evade consumed scope or quarantine. Unknown effects are never retried;
partial/uncertain setup cannot use this complete-scope cleanup path. Cleanup needs
an explicit separate review of successful setup and a definitive run outcome.

Adapter calls must have independently enforced bounded deadlines. This synchronous
foundation cannot terminate a hung adapter, reconcile uncertainty, prove process
cessation or implement distributed durability. Synthetic adapters in tests prove
only contract behavior. SOURCE_ONLY calls no adapters at all. No mode activates
agents, touches production, or grants access to IBKR source data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
from enum import Enum
import hashlib
import json
import re
from typing import Protocol
import uuid


HOST = "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"
DATABASE = "owner_codex_restricted_20261004"
SCHEMA = "owner_codex_059_restricted"
SCHEMA_OWNER = "ibkr_owner_codex_schema_20261004"
CAPABILITY = "ibkr_owner_codex_dml_20261004"
LOGIN = "ibkr_owner_codex_login_20261004"
MARKER = "wealthryse-owner-task-restricted-059-20261004-v1"
NAMESPACE = "owner_codex_restricted_trial_v1"
PRODUCER_RESOURCE = ("/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/"
    "resourceGroups/rg-ibkr-database-v11/providers/Microsoft.App/jobs/ibkrv2stg-bootstrap")
MIGRATION_SHA256 = "b4a78f64969d328f6b3ce1ce71b73cd3c033606fc8580ed1a933dd47857e9861"
REQUIRED_SOURCE_FILES = tuple(sorted((
    "src/ibkr_etl/util.py", "src/ibkr_etl/db.py", "src/ibkr_etl/owner_codex_tasks.py",
    "src/ibkr_etl/owner_codex_permissions.py", "src/ibkr_etl/owner_codex_worker.py",
    "src/ibkr_etl/owner_codex_trial_lifecycle.py", "migrations/postgres/059_owner_codex_tasks.sql",
    "scripts/inspect_owner_task_restricted_boundary.py", "scripts/prepare_owner_task_restricted_trial.py",
    "scripts/run_owner_task_restricted_trial.py", "scripts/cleanup_owner_task_restricted_trial.py")))
REASONS = frozenset({"invalid_contract", "phase_order_invalid", "cleanup_review_required",
    "scope_already_consumed", "context_unverified", "store_uncertain", "intent_unverified",
    "claim_unverified", "execution_uncertain", "outcome_unverified", "coordinator_quarantined"})


class LifecycleError(ValueError):
    """Fixed metadata codes, never arbitrary adapter/provider text."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in REASONS else "invalid_contract"
        super().__init__(self.code)


def _require(condition, code="invalid_contract"):
    if not condition:
        raise LifecycleError(code)


def _sha(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)


def _uuid(value):
    _require(type(value) is str)
    try:
        _require(str(uuid.UUID(value)) == value)
    except ValueError:
        raise LifecycleError("invalid_contract") from None


def _checked(value, cls):
    _require(type(value) is cls)
    value.__post_init__()
    return value


def digest(value):
    """Canonical metadata only; adapters verify actual bytes separately."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode()).hexdigest()


class Phase(str, Enum):
    SETUP = "setup"
    RUN = "run"
    CLEANUP = "cleanup"


class TrialMode(str, Enum):
    SOURCE_ONLY = "source_only"
    SYNTHETIC = "synthetic"
    LIVE_DISPOSABLE = "live_disposable"


@dataclass(frozen=True)
class TrialTarget:
    host: str = HOST
    database: str = DATABASE
    schema: str = SCHEMA
    schema_owner: str = SCHEMA_OWNER
    capability: str = CAPABILITY
    login: str = LOGIN
    marker: str = MARKER

    def __post_init__(self):
        expected = (HOST, DATABASE, SCHEMA, SCHEMA_OWNER, CAPABILITY, LOGIN, MARKER)
        _require(tuple(asdict(self).values()) == expected
                 and all(type(v) is str for v in asdict(self).values()))

    @property
    def scope_sha256(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class ReviewedRegistration:
    """Trusted reviewed inputs, not an approval/identity/source proof by itself."""
    files: tuple[tuple[str, str], ...]
    bundle_sha256: str
    installer_identity: str
    producer_principal_id: str
    producer_resource_id: str = PRODUCER_RESOURCE

    def __post_init__(self):
        _require(type(self.files) is tuple and len(self.files) == len(REQUIRED_SOURCE_FILES))
        _require(all(type(pair) is tuple and len(pair) == 2 for pair in self.files))
        _require(tuple(pair[0] for pair in self.files) == REQUIRED_SOURCE_FILES)
        for path, sha in self.files:
            _require(type(path) is str)
            _sha(sha)
        _sha(self.bundle_sha256)
        _require(self.bundle_sha256 == digest(self.files))
        _require(dict(self.files)["migrations/postgres/059_owner_codex_tasks.sql"] == MIGRATION_SHA256)
        _uuid(self.producer_principal_id)
        _require(type(self.installer_identity) is str and
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.installer_identity) is not None
            and self.installer_identity not in (SCHEMA_OWNER, CAPABILITY, LOGIN))
        _require(type(self.producer_resource_id) is str and self.producer_resource_id == PRODUCER_RESOURCE)


@dataclass(frozen=True)
class TrialPlan:
    operation_id: str
    registration: ReviewedRegistration
    baseline_sha256: str
    boundary_receipt_sha256: str
    mode: TrialMode = TrialMode.SOURCE_ONLY
    target: TrialTarget = TrialTarget()

    def __post_init__(self):
        _uuid(self.operation_id)
        _sha(self.baseline_sha256)
        _sha(self.boundary_receipt_sha256)
        _require(type(self.registration) is ReviewedRegistration and type(self.target) is TrialTarget
                 and type(self.mode) is TrialMode)
        self.registration.__post_init__()
        self.target.__post_init__()

    @property
    def plan_sha256(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class PhaseReservation:
    plan_sha256: str
    scope_sha256: str
    phase: Phase
    fence: int
    previous_sha256: str
    cleanup_review_sha256: str | None

    def __post_init__(self):
        for sha in (self.plan_sha256, self.scope_sha256, self.previous_sha256):
            _sha(sha)
        _require(type(self.phase) is Phase and type(self.fence) is int and 1 <= self.fence <= 1000000)
        if self.cleanup_review_sha256 is not None:
            _sha(self.cleanup_review_sha256)
        _require((self.phase is Phase.CLEANUP) == (self.cleanup_review_sha256 is not None))


@dataclass(frozen=True)
class PhaseClaim:
    reservation: PhaseReservation
    intent_receipt_sha256: str
    durable_cas_verified: bool

    def __post_init__(self):
        _require(type(self.reservation) is PhaseReservation and self.durable_cas_verified is True)
        self.reservation.__post_init__()
        _sha(self.intent_receipt_sha256)


@dataclass(frozen=True)
class ExecutionContext:
    plan_sha256: str
    producer_principal_id: str
    producer_resource_id: str
    current_identity: str
    independently_verified: bool
    live_verified: bool

    def __post_init__(self):
        _sha(self.plan_sha256)
        _uuid(self.producer_principal_id)
        _require(type(self.producer_resource_id) is str and type(self.current_identity) is str
                 and type(self.independently_verified) is bool and type(self.live_verified) is bool)


@dataclass(frozen=True)
class PhaseOutcome:
    """Exact result descriptor; raw SQL/provider text and credentials stay out."""
    plan_sha256: str
    phase: Phase
    fence: int
    status: str
    result_sha256: str
    current_identity: str
    effects_complete_verified: bool
    owned_connections_closed_verified: bool
    live_sql_executed: bool

    def __post_init__(self):
        _sha(self.plan_sha256)
        _sha(self.result_sha256)
        _require(type(self.phase) is Phase and type(self.fence) is int and 1 <= self.fence <= 1000000
                 and type(self.status) is str and self.status in {"passed", "failed"}
                 and type(self.current_identity) is str
                 and all(type(v) is bool for v in (self.effects_complete_verified,
                    self.owned_connections_closed_verified, self.live_sql_executed)))


@dataclass(frozen=True)
class ProtectedReceipt:
    record_sha256: str
    reference_sha256: str

    def __post_init__(self):
        _sha(self.record_sha256)
        _sha(self.reference_sha256)

    @property
    def receipt_sha256(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class ProtectedReadback:
    receipt: ProtectedReceipt
    binding_sha256: str
    producer_principal_id: str
    producer_resource_id: str
    current_identity: str
    protected_archive_verified: bool
    authenticated_producer_verified: bool
    live_verified: bool

    def __post_init__(self):
        _require(type(self.receipt) is ProtectedReceipt)
        self.receipt.__post_init__()
        _sha(self.binding_sha256)
        _uuid(self.producer_principal_id)
        _require(type(self.producer_resource_id) is str and type(self.current_identity) is str
            and all(type(v) is bool for v in (self.protected_archive_verified,
                self.authenticated_producer_verified, self.live_verified)))


@dataclass(frozen=True)
class PhaseAcceptance:
    reservation: PhaseReservation
    intent: ProtectedReceipt
    outcome: PhaseOutcome
    outcome_receipt: ProtectedReceipt

    def __post_init__(self):
        _require(type(self.reservation) is PhaseReservation and type(self.intent) is ProtectedReceipt
                 and type(self.outcome) is PhaseOutcome and type(self.outcome_receipt) is ProtectedReceipt)
        for value in (self.reservation, self.intent, self.outcome, self.outcome_receipt):
            value.__post_init__()
        _require(self.outcome.plan_sha256 == self.reservation.plan_sha256
                 and self.outcome.phase is self.reservation.phase and self.outcome.fence == self.reservation.fence)

    @property
    def acceptance_sha256(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class CleanupReview:
    plan_sha256: str
    setup_acceptance_sha256: str
    run_acceptance_sha256: str
    review_receipt_sha256: str

    def __post_init__(self):
        for value in asdict(self).values():
            _sha(value)


@dataclass(frozen=True)
class TrialDecision:
    status: str
    code: str | None
    mode: TrialMode
    phase: Phase
    acceptance: PhaseAcceptance | None = None
    live_phase_execution_verified: bool = False
    automatic_retry_authorized: bool = False
    production_actions_authorized: bool = False
    agents_activated: int = 0

    def __post_init__(self):
        _require(type(self.mode) is TrialMode and type(self.phase) is Phase
                 and type(self.status) is str and self.status in {"not_executed", "passed", "failed", "needs_reconciliation"}
                 and (self.code is None or type(self.code) is str and self.code in REASONS)
                 and (self.acceptance is None or type(self.acceptance) is PhaseAcceptance)
                 and type(self.live_phase_execution_verified) is bool
                 and self.automatic_retry_authorized is False and self.production_actions_authorized is False
                 and type(self.agents_activated) is int and self.agents_activated == 0)
        _require((self.acceptance is not None) == (self.status in {"passed", "failed"}))
        _require(self.live_phase_execution_verified is (self.acceptance is not None and self.mode is TrialMode.LIVE_DISPOSABLE))
        if self.acceptance is not None:
            self.acceptance.__post_init__()
            _require(self.acceptance.reservation.phase is self.phase
                and self.acceptance.outcome.status == self.status
                and self.acceptance.outcome.live_sql_executed is (self.mode is TrialMode.LIVE_DISPOSABLE))


class DurableTrialStore(Protocol):
    """CAS/one-shot semantics are mandatory, not implemented by this module.

    reserve_phase atomically owns fixed scope+operation, validates prior committed
    chain/phase, and returns None for ANY consumed/quarantined reservation. Never
    return a fresh fence after uncertainty. claim/complete must commit before
    returning; compare all supplied fields. quarantine blocks the entire scope
    across restart/new operation. No reset, force, cancellation or retry method.
    """
    def reserve_phase(self, plan: TrialPlan, phase: Phase, previous: tuple[PhaseAcceptance, ...],
                      cleanup_review: CleanupReview | None) -> PhaseReservation | None: ...
    def claim_phase(self, reservation: PhaseReservation, intent: ProtectedReceipt) -> PhaseClaim: ...
    def complete_phase(self, claim: PhaseClaim, acceptance: PhaseAcceptance) -> bool: ...
    def quarantine(self, plan: TrialPlan, phase: Phase, code: str) -> bool: ...


class ProtectedTrialAudit(Protocol):
    """Create-only retain and independently authenticated exact readback.

    readback must verify immutable version/bytes/protection, not simply echo the
    submitted dictionary. review_cleanup independently checks the reviewed receipt
    bound to this exact plan and both prior protected acceptances. No model may
    supply this adapter or attest its own identity/result.
    """
    def retain(self, record: dict) -> ProtectedReceipt: ...
    def readback(self, record: dict, receipt: ProtectedReceipt) -> ProtectedReadback: ...
    def review_cleanup(self, plan: TrialPlan, review: CleanupReview) -> bool: ...


class TrustedTrialExecutor(Protocol):
    """Fixed phase only, no arbitrary commands/prompts/tools/callback payloads.

    verify_context independently checks current source BYTES, identity, authority,
    target, unchanged baseline and fresh phase-appropriate catalog prerequisites.
    execute_phase invokes only the exact reviewed setup/harness/complete cleanup,
    verifies complete result and closure of owned connections. No source access,
    production SQL, model calls, agents, FORCE or session termination is allowed.
    """
    def verify_context(self, plan: TrialPlan, phase: Phase) -> ExecutionContext: ...
    def execute_phase(self, plan: TrialPlan, phase: Phase, claim: PhaseClaim) -> PhaseOutcome: ...


def _record(plan, reservation, kind, *, intent=None, outcome=None):
    return dict(namespace=NAMESPACE, operation_id=plan.operation_id, mode=plan.mode.value,
        target=asdict(plan.target), plan_sha256=plan.plan_sha256, source=asdict(plan.registration),
        baseline_sha256=plan.baseline_sha256, boundary_receipt_sha256=plan.boundary_receipt_sha256,
        phase=reservation.phase.value, fence=reservation.fence, kind=kind,
        previous_sha256=reservation.previous_sha256, cleanup_review_sha256=reservation.cleanup_review_sha256,
        producer_principal_id=plan.registration.producer_principal_id,
        producer_resource_id=plan.registration.producer_resource_id,
        current_identity=plan.registration.installer_identity,
        intent_receipt_sha256=None if intent is None else intent.receipt_sha256,
        outcome=None if outcome is None else asdict(outcome), production_actions_authorized=False,
        source_rows_read=0, source_rows_written=0, model_calls=0, agents_activated=0)


class LifecycleCoordinator:
    def __init__(self, store: DurableTrialStore, audit: ProtectedTrialAudit, executor: TrustedTrialExecutor):
        self.store, self.audit, self.executor = store, audit, executor
        self._quarantined = False

    def _context(self, plan, phase):
        context = _checked(self.executor.verify_context(plan, phase), ExecutionContext)
        r = plan.registration
        _require(type(context) is ExecutionContext and context.plan_sha256 == plan.plan_sha256
            and context.producer_principal_id == r.producer_principal_id
            and context.producer_resource_id == r.producer_resource_id
            and context.current_identity == r.installer_identity and context.independently_verified is True
            and context.live_verified is (plan.mode is TrialMode.LIVE_DISPOSABLE), "context_unverified")

    def _verify(self, plan, record, receipt):
        _checked(receipt, ProtectedReceipt)
        _require(type(receipt) is ProtectedReceipt and receipt.record_sha256 == digest(record), "outcome_unverified")
        proof = _checked(self.audit.readback(copy.deepcopy(record), receipt), ProtectedReadback)
        r = plan.registration
        _require(type(proof) is ProtectedReadback and proof.receipt == receipt
            and proof.binding_sha256 == digest(record) and proof.producer_principal_id == r.producer_principal_id
            and proof.producer_resource_id == r.producer_resource_id
            and proof.current_identity == r.installer_identity and proof.protected_archive_verified is True
            and proof.authenticated_producer_verified is True
            and proof.live_verified is (plan.mode is TrialMode.LIVE_DISPOSABLE), "outcome_unverified")

    def _previous(self, plan, phase, previous, review):
        expected = ((), (Phase.SETUP,), (Phase.SETUP, Phase.RUN))[list(Phase).index(phase)]
        _require(type(previous) is tuple and len(previous) == len(expected), "phase_order_invalid")
        chain = []
        for item, prior_phase in zip(previous, expected):
            _checked(item, PhaseAcceptance)
            _require(type(item) is PhaseAcceptance and item.reservation.plan_sha256 == plan.plan_sha256
                and item.reservation.scope_sha256 == plan.target.scope_sha256
                and item.reservation.phase is prior_phase and item.reservation.previous_sha256 == digest(chain)
                and item.outcome.current_identity == plan.registration.installer_identity
                and item.outcome.effects_complete_verified is True
                and item.outcome.owned_connections_closed_verified is True
                and item.outcome.live_sql_executed is (plan.mode is TrialMode.LIVE_DISPOSABLE)
                and (prior_phase is not Phase.SETUP or item.outcome.status == "passed"), "phase_order_invalid")
            self._verify(plan, _record(plan, item.reservation, "intent"), item.intent)
            self._verify(plan, _record(plan, item.reservation, "outcome", intent=item.intent, outcome=item.outcome), item.outcome_receipt)
            chain.append(item.acceptance_sha256)
        if phase is Phase.CLEANUP:
            _checked(review, CleanupReview)
            _require(type(review) is CleanupReview and review.plan_sha256 == plan.plan_sha256
                and review.setup_acceptance_sha256 == previous[0].acceptance_sha256
                and review.run_acceptance_sha256 == previous[1].acceptance_sha256
                and self.audit.review_cleanup(plan, review) is True, "cleanup_review_required")
        else:
            _require(review is None, "cleanup_review_required")
        return digest(chain)

    def advance(self, plan: TrialPlan, phase: Phase, *, previous: tuple[PhaseAcceptance, ...] = (),
                cleanup_review: CleanupReview | None = None) -> TrialDecision:
        _require(type(plan) is TrialPlan and type(phase) is Phase)
        plan.__post_init__()
        if plan.mode is TrialMode.SOURCE_ONLY:
            return TrialDecision("not_executed", None, plan.mode, phase)
        if self._quarantined:
            return TrialDecision("not_executed", "coordinator_quarantined", plan.mode, phase)
        code = "outcome_unverified"
        try:
            previous_sha = self._previous(plan, phase, previous, cleanup_review)
            code = "context_unverified"
            self._context(plan, phase)
            code = "store_uncertain"
            reservation = self.store.reserve_phase(plan, phase, previous, cleanup_review)
            if reservation is None:
                return TrialDecision("not_executed", "scope_already_consumed", plan.mode, phase)
            _checked(reservation, PhaseReservation)
            _require(type(reservation) is PhaseReservation and reservation.plan_sha256 == plan.plan_sha256
                and reservation.scope_sha256 == plan.target.scope_sha256 and reservation.phase is phase
                and reservation.previous_sha256 == previous_sha and reservation.cleanup_review_sha256 ==
                    (None if cleanup_review is None else digest(asdict(cleanup_review))), "store_uncertain")
            code = "intent_unverified"
            intent_record = _record(plan, reservation, "intent")
            intent = self.audit.retain(copy.deepcopy(intent_record))
            self._verify(plan, intent_record, intent)
            code = "claim_unverified"
            claim = _checked(self.store.claim_phase(reservation, intent), PhaseClaim)
            _require(type(claim) is PhaseClaim and claim.reservation == reservation
                and claim.intent_receipt_sha256 == intent.receipt_sha256, "claim_unverified")
            code = "context_unverified"
            self._context(plan, phase)
            code = "execution_uncertain"
            outcome = _checked(self.executor.execute_phase(plan, phase, claim), PhaseOutcome)
            _require(type(outcome) is PhaseOutcome and outcome.plan_sha256 == plan.plan_sha256
                and outcome.phase is phase and outcome.fence == reservation.fence
                and outcome.current_identity == plan.registration.installer_identity
                and outcome.effects_complete_verified is True and outcome.owned_connections_closed_verified is True
                and outcome.live_sql_executed is (plan.mode is TrialMode.LIVE_DISPOSABLE), "execution_uncertain")
            self._context(plan, phase)
            code = "outcome_unverified"
            record = _record(plan, reservation, "outcome", intent=intent, outcome=outcome)
            receipt = self.audit.retain(copy.deepcopy(record))
            self._verify(plan, record, receipt)
            acceptance = PhaseAcceptance(reservation, intent, outcome, receipt)
            code = "store_uncertain"
            _require(self.store.complete_phase(claim, acceptance) is True, "store_uncertain")
            return TrialDecision(outcome.status, None, plan.mode, phase, acceptance,
                live_phase_execution_verified=plan.mode is TrialMode.LIVE_DISPOSABLE)
        except Exception:
            self._quarantined = True
            try:
                self.store.quarantine(plan, phase, code)
            except Exception:
                pass  # Unknown quarantine persistence itself requires independent reconciliation.
            return TrialDecision("needs_reconciliation", code, plan.mode, phase)
