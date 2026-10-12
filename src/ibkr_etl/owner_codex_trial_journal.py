"""Inert, bounded append journal for the ONE registered disposable trial.

The trusted backend must atomically publish complete, create-only records and
commit before acknowledging. These assumptions are not proof of cloud durability,
immutable retention, authentication, protected audit evidence or authorization.
No backend, credential reader, executor, model, SQL or CLI is installed here.

The fixed scope has ten slots, independent of operation UUID and mode. A consumed
reserve or claim never yields a new fence. Only its acknowledged original object
can be used once by this same store instance; persisted evidence cannot recreate
that local permit. Unknown writes never authorize effects. Quarantine is sticky
metadata, not proof that an already running executor has stopped. Concrete live
adapters must be separately qualified; local/synthetic adapters cannot enable live
execution. SOURCE_ONLY operations perform no backend access.
"""
from dataclasses import asdict, dataclass, fields
import json
import threading
from typing import Protocol

from .owner_codex_trial_lifecycle import (
    CleanupReview, LifecycleError, NAMESPACE, Phase, PhaseAcceptance, PhaseClaim,
    PhaseOutcome, PhaseReservation, ProtectedReceipt, REASONS,
    ReviewedRegistration, TrialMode, TrialPlan, TrialTarget, _record, digest,
)


JOURNAL_NAMESPACE = NAMESPACE
MAX_RECORD_BYTES = 16384
MAX_SLOTS = 10
_GENESIS = digest([])
_PHASES = (Phase.SETUP, Phase.RUN, Phase.CLEANUP)
_COMMON = frozenset({"version", "namespace", "scope_sha256", "slot",
    "parent_sha256", "plan_sha256", "event", "payload", "record_sha256"})


class TrialJournalBackend(Protocol):
    """Trusted bounded storage, not an attestation supplied by an agent."""
    @property
    def supports_live_execution(self) -> bool: ...
    def read(self, key: str) -> bytes | None: ...
    def create_if_absent(self, key: str, value: bytes) -> bool: ...


def _require(condition, code="invalid_contract"):
    if not condition:
        raise LifecycleError(code)


def key_for_slot(slot: int) -> str:
    _require(type(slot) is int and 1 <= slot <= MAX_SLOTS)
    return f"{JOURNAL_NAMESPACE}/{TrialTarget().scope_sha256}/{slot:02d}"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode("utf-8")


def _shape(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _enum(value, cls):
    _require(type(value) is str)
    return cls(value)


def _decode(cls, raw):
    """Rebuild exact typed metadata; never trust a supplied frozen instance."""
    _shape(raw, (item.name for item in fields(cls)))
    data = dict(raw)
    if cls is ReviewedRegistration:
        _require(type(data["files"]) is list)
        _require(all(type(pair) is list and len(pair) == 2 for pair in data["files"]))
        data["files"] = tuple(tuple(pair) for pair in data["files"])
    elif cls is TrialPlan:
        data["registration"] = _decode(ReviewedRegistration, data["registration"])
        data["target"] = _decode(TrialTarget, data["target"])
        data["mode"] = _enum(data["mode"], TrialMode)
    elif cls in (PhaseReservation, PhaseOutcome):
        data["phase"] = _enum(data["phase"], Phase)
    elif cls is PhaseClaim:
        data["reservation"] = _decode(PhaseReservation, data["reservation"])
    elif cls is PhaseAcceptance:
        data["reservation"] = _decode(PhaseReservation, data["reservation"])
        data["intent"] = _decode(ProtectedReceipt, data["intent"])
        data["outcome"] = _decode(PhaseOutcome, data["outcome"])
        data["outcome_receipt"] = _decode(ProtectedReceipt, data["outcome_receipt"])
    result = cls(**data)
    result.__post_init__()
    return result


def _checked(value, cls):
    try:
        _require(type(value) is cls)
        value.__post_init__()
        return _decode(cls, json.loads(_canonical(asdict(value))))
    except Exception:
        raise LifecycleError("invalid_contract") from None


def _fingerprint(value):
    try:
        return digest(asdict(value))
    except Exception:
        raise LifecycleError("invalid_contract") from None


def _receipt_bindings(plan, acceptance):
    reservation = acceptance.reservation
    _require(acceptance.intent.record_sha256 == digest(_record(plan, reservation, "intent")),
        "intent_unverified")
    _require(acceptance.outcome_receipt.record_sha256 == digest(_record(plan, reservation,
        "outcome", intent=acceptance.intent, outcome=acceptance.outcome)), "outcome_unverified")


def _previous(plan, phase, previous, review):
    _require(type(phase) is Phase and type(previous) is tuple, "phase_order_invalid")
    index = _PHASES.index(phase)
    _require(len(previous) == index, "phase_order_invalid")
    checked = []
    chain = []
    for position, value in enumerate(previous):
        item = _checked(value, PhaseAcceptance)
        r, outcome = item.reservation, item.outcome
        _require(r.plan_sha256 == plan.plan_sha256 and r.scope_sha256 == plan.target.scope_sha256
            and r.phase is _PHASES[position] and r.fence == position + 1
            and r.previous_sha256 == digest(chain) and r.cleanup_review_sha256 is None
            and outcome.current_identity == plan.registration.installer_identity
            and outcome.effects_complete_verified is True
            and outcome.owned_connections_closed_verified is True
            and outcome.live_sql_executed is (plan.mode is TrialMode.LIVE_DISPOSABLE)
            and (position != 0 or outcome.status == "passed"), "phase_order_invalid")
        _receipt_bindings(plan, item)
        checked.append(item)
        chain.append(item.acceptance_sha256)
    if phase is Phase.CLEANUP:
        review = _checked(review, CleanupReview)
        _require(review.plan_sha256 == plan.plan_sha256
            and review.setup_acceptance_sha256 == checked[0].acceptance_sha256
            and review.run_acceptance_sha256 == checked[1].acceptance_sha256,
            "cleanup_review_required")
    else:
        _require(review is None, "cleanup_review_required")
    return tuple(checked), review, digest(chain)


@dataclass(frozen=True)
class JournalSnapshot:
    """Read-only evidence summary. Never contains or reconstructs a permit."""
    record_sha256s: tuple[str, ...]
    plan_sha256: str | None
    acceptance_sha256s: tuple[str, ...]
    phase: Phase | None
    status: str
    quarantined: bool


@dataclass(frozen=True)
class _State:
    encoded: tuple[bytes, ...] = ()
    hashes: tuple[str, ...] = ()
    plan: TrialPlan | None = None
    reservation: PhaseReservation | None = None
    claim: PhaseClaim | None = None
    intent: ProtectedReceipt | None = None
    acceptances: tuple[PhaseAcceptance, ...] = ()
    status: str = "empty"
    phase: Phase | None = None


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _invalid_constant(_value):
    raise LifecycleError("invalid_contract")


def _parse(encoded, slot, parent):
    raw = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_pairs,
        parse_constant=_invalid_constant)
    _shape(raw, _COMMON)
    _require(_canonical(raw) == encoded and type(raw["version"]) is int and raw["version"] == 1
        and raw["namespace"] == JOURNAL_NAMESPACE
        and raw["scope_sha256"] == TrialTarget().scope_sha256
        and type(raw["slot"]) is int and raw["slot"] == slot
        and raw["parent_sha256"] == parent and type(raw["event"]) is str)
    body = {key: value for key, value in raw.items() if key != "record_sha256"}
    _require(raw["record_sha256"] == digest(body))
    return raw


def _apply(state, raw, encoded):
    payload, event = raw["payload"], raw["event"]
    _require(state.status != "quarantined")
    plan, reservation, claim, intent = state.plan, state.reservation, state.claim, state.intent
    acceptances, phase = state.acceptances, state.phase
    if event == "reserve":
        _shape(payload, {"plan", "reservation", "previous", "cleanup_review"})
        proposed = _decode(TrialPlan, payload["plan"])
        reservation = _decode(PhaseReservation, payload["reservation"])
        _require(type(payload["previous"]) is list and len(payload["previous"]) <= 2)
        previous = tuple(_decode(PhaseAcceptance, value) for value in payload["previous"])
        review = None if payload["cleanup_review"] is None else _decode(CleanupReview, payload["cleanup_review"])
        previous, review, prior_hash = _previous(proposed, reservation.phase, previous, review)
        _require(state.status in {"empty", "completed"} and len(acceptances) < 3
            and proposed.mode is not TrialMode.SOURCE_ONLY
            and (plan is None or proposed == plan) and previous == acceptances
            and reservation.phase is _PHASES[len(acceptances)]
            and reservation.fence == len(acceptances) + 1
            and reservation.plan_sha256 == proposed.plan_sha256
            and reservation.scope_sha256 == proposed.target.scope_sha256
            and reservation.previous_sha256 == prior_hash
            and reservation.cleanup_review_sha256 == (None if review is None else digest(asdict(review))))
        plan, phase, claim, intent, status = proposed, reservation.phase, None, None, "reserved"
    elif event == "claim":
        _shape(payload, {"claim", "intent"})
        claim = _decode(PhaseClaim, payload["claim"])
        intent = _decode(ProtectedReceipt, payload["intent"])
        _require(state.status == "reserved" and claim.reservation == reservation
            and claim.intent_receipt_sha256 == intent.receipt_sha256
            and intent.record_sha256 == digest(_record(plan, reservation, "intent")))
        status = "claimed"
    elif event == "complete":
        _shape(payload, {"claim", "acceptance"})
        supplied_claim = _decode(PhaseClaim, payload["claim"])
        item = _decode(PhaseAcceptance, payload["acceptance"])
        _require(state.status == "claimed" and supplied_claim == claim
            and item.reservation == reservation and item.intent == intent
            and item.outcome.current_identity == plan.registration.installer_identity
            and item.outcome.effects_complete_verified is True
            and item.outcome.owned_connections_closed_verified is True
            and item.outcome.live_sql_executed is (plan.mode is TrialMode.LIVE_DISPOSABLE))
        _receipt_bindings(plan, item)
        acceptances, status = (*acceptances, item), "completed"
    elif event == "quarantine":
        _shape(payload, {"plan", "phase", "code", "previous_sha256", "cleanup_review_sha256"})
        proposed = _decode(TrialPlan, payload["plan"])
        phase = _enum(payload["phase"], Phase)
        _require(proposed.mode is not TrialMode.SOURCE_ONLY and (plan is None or proposed == plan)
            and type(payload["code"]) is str and payload["code"] in REASONS
            and payload["previous_sha256"] == digest([item.acceptance_sha256 for item in acceptances])
            and payload["cleanup_review_sha256"] ==
                (None if reservation is None else reservation.cleanup_review_sha256))
        plan, status = proposed, "quarantined"
    else:
        raise LifecycleError("invalid_contract")
    _require(raw["plan_sha256"] == plan.plan_sha256)
    return _State((*state.encoded, encoded), (*state.hashes, raw["record_sha256"]),
        plan, reservation, claim, intent, acceptances, status, phase)


class DurableTrialJournal:
    """Single-scope journal; original object permits are local and one-shot."""
    def __init__(self, backend: TrialJournalBackend):
        self._backend = backend
        self._lock = threading.RLock()
        self._reservations = {}
        self._claims = {}

    def _gate(self, plan):
        if plan.mode is TrialMode.LIVE_DISPOSABLE:
            try:
                _require(self._backend.supports_live_execution is True, "context_unverified")
            except Exception:
                raise LifecycleError("context_unverified") from None

    def _load(self):
        try:
            # Always inspect all slots before interpreting a valid prefix.
            values = tuple(self._backend.read(key_for_slot(slot)) for slot in range(1, MAX_SLOTS + 1))
            _require(all(value is None or type(value) is bytes and 0 < len(value) <= MAX_RECORD_BYTES
                for value in values))
            state, gap = _State(), False
            for slot, encoded in enumerate(values, 1):
                if encoded is None:
                    gap = True
                    continue
                _require(not gap)
                raw = _parse(encoded, slot, _GENESIS if not state.hashes else state.hashes[-1])
                state = _apply(state, raw, encoded)
            return state
        except Exception:
            raise LifecycleError("store_uncertain") from None

    def snapshot(self) -> JournalSnapshot:
        """Fresh complete read, never restores a stranded reservation or claim."""
        with self._lock:
            state = self._load()
            return JournalSnapshot(state.hashes, None if state.plan is None else state.plan.plan_sha256,
                tuple(item.acceptance_sha256 for item in state.acceptances), state.phase,
                state.status, state.status == "quarantined")

    def _append(self, state, plan, event, payload):
        slot = len(state.encoded) + 1
        _require(slot <= MAX_SLOTS, "store_uncertain")
        try:
            body = dict(version=1, namespace=JOURNAL_NAMESPACE,
                scope_sha256=plan.target.scope_sha256, slot=slot,
                parent_sha256=_GENESIS if not state.hashes else state.hashes[-1],
                plan_sha256=plan.plan_sha256, event=event, payload=payload)
            encoded = _canonical({**body, "record_sha256": digest(body)})
            _require(len(encoded) <= MAX_RECORD_BYTES)
            acknowledged = self._backend.create_if_absent(key_for_slot(slot), encoded)
            _require(type(acknowledged) is bool)
            if not acknowledged:
                return None  # Even identical prior bytes are NOT our create acknowledgement.
            fresh = self._load()
            _require(fresh.encoded == (*state.encoded, encoded))
            return fresh
        except Exception:
            raise LifecycleError("store_uncertain") from None

    def reserve_phase(self, plan, phase, previous=(), cleanup_review=None):
        plan = _checked(plan, TrialPlan)
        _require(type(phase) is Phase)
        previous, review, prior_hash = _previous(plan, phase, previous, cleanup_review)
        if plan.mode is TrialMode.SOURCE_ONLY:
            return None
        with self._lock:
            self._gate(plan)
            state = self._load()
            if state.plan is not None and state.plan != plan:
                return None
            if state.status not in {"empty", "completed"} or _PHASES.index(phase) < len(state.acceptances):
                return None
            _require(previous == state.acceptances and len(state.acceptances) == _PHASES.index(phase),
                "phase_order_invalid")
            reservation = PhaseReservation(plan.plan_sha256, plan.target.scope_sha256,
                phase, _PHASES.index(phase) + 1, prior_hash,
                None if review is None else digest(asdict(review)))
            fresh = self._append(state, plan, "reserve", dict(plan=asdict(plan),
                reservation=asdict(reservation), previous=[asdict(item) for item in previous],
                cleanup_review=None if review is None else asdict(review)))
            if fresh is None:
                return None
            self._reservations[id(reservation)] = (reservation, _fingerprint(reservation))
            return reservation

    def _take(self, permits, value, cls, code):
        # Consume before validation, read, or publication; uncertainty cannot retry.
        original = permits.pop(id(value), None)
        _require(original is not None and original[0] is value, code)
        checked = _checked(value, cls)
        _require(_fingerprint(checked) == original[1], code)
        return checked

    def claim_phase(self, reservation, intent):
        with self._lock:
            checked = self._take(self._reservations, reservation, PhaseReservation, "claim_unverified")
            intent = _checked(intent, ProtectedReceipt)
            state = self._load()
            _require(state.status == "reserved" and state.reservation == checked, "claim_unverified")
            self._gate(state.plan)
            _require(intent.record_sha256 == digest(_record(state.plan, checked, "intent")), "intent_unverified")
            claim = PhaseClaim(reservation, intent.receipt_sha256, True)
            fresh = self._append(state, state.plan, "claim", dict(claim=asdict(claim), intent=asdict(intent)))
            _require(fresh is not None, "claim_unverified")
            self._claims[id(claim)] = (claim, _fingerprint(claim))
            return claim

    def complete_phase(self, claim, acceptance):
        with self._lock:
            checked = self._take(self._claims, claim, PhaseClaim, "claim_unverified")
            acceptance = _checked(acceptance, PhaseAcceptance)
            state = self._load()
            _require(state.status == "claimed" and state.claim == checked
                and acceptance.reservation == checked.reservation and acceptance.intent == state.intent,
                "outcome_unverified")
            self._gate(state.plan)
            _require(acceptance.outcome.current_identity == state.plan.registration.installer_identity
                and acceptance.outcome.effects_complete_verified is True
                and acceptance.outcome.owned_connections_closed_verified is True
                and acceptance.outcome.live_sql_executed is (state.plan.mode is TrialMode.LIVE_DISPOSABLE),
                "outcome_unverified")
            _receipt_bindings(state.plan, acceptance)
            fresh = self._append(state, state.plan, "complete", dict(claim=asdict(checked),
                acceptance=asdict(acceptance)))
            return fresh is not None

    def quarantine(self, plan, phase, code):
        plan = _checked(plan, TrialPlan)
        _require(type(phase) is Phase and type(code) is str and code in REASONS)
        if plan.mode is TrialMode.SOURCE_ONLY:
            return False
        with self._lock:
            self._gate(plan)
            # Only metadata quarantine may reconcile a bounded CAS race. It never
            # returns an execution permit or guesses a slot after corrupt reads.
            for _attempt in range(MAX_SLOTS):
                state = self._load()
                if state.plan is not None and state.plan != plan:
                    return False
                if state.status == "quarantined":
                    return True
                payload = dict(plan=asdict(plan), phase=phase.value, code=code,
                    previous_sha256=digest([item.acceptance_sha256 for item in state.acceptances]),
                    cleanup_review_sha256=None if state.reservation is None else state.reservation.cleanup_review_sha256)
                try:
                    fresh = self._append(state, plan, "quarantine", payload)
                except LifecycleError:
                    # A lost acknowledgement can confirm sticky quarantine only.
                    # No second create follows an unknown write response.
                    fresh = self._load()
                    if fresh.status == "quarantined" and fresh.plan == plan:
                        return True
                    raise LifecycleError("store_uncertain") from None
                if fresh is not None:
                    return True
            raise LifecycleError("store_uncertain")
