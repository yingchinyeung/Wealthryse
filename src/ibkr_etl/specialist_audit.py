"""Append-only audit writer; trusted audit service only, not an agent tool.

Caller owns the transaction. Commit the intent before allowing an external
effect. No returned row is proof of commit, complete capture, or external anchoring.
"""
import hashlib
from datetime import datetime, timedelta, timezone

from .specialist_autonomy import build_daily_activity_report
from .util import canonical_json


def append_event(connection, *, tenant_id: str, event_id: str, role: str, action: str,
                 operation_id: str, phase: str, evidence_sha256: str, cost_microusd: int = 0) -> dict:
    # Same transaction-scoped lock as the insert trigger, serializing writers.
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('specialist-audit:' || %s, 0))", (tenant_id,))
    context = connection.execute("SELECT app_tenant_id()::text AS tenant_id").fetchone()
    if context["tenant_id"] != tenant_id:
        raise ValueError("Audit tenant mismatch")
    fields = dict(event_id=event_id, role=role, action=action, operation_id=operation_id,
                  phase=phase, evidence_sha256=evidence_sha256, cost_microusd=cost_microusd)
    existing = connection.execute("SELECT event_json FROM specialist_audit_events WHERE tenant_id=%s AND event_id=%s",
                                  (tenant_id,event_id)).fetchone()
    if existing:
        event = existing["event_json"]
        if canonical_json({key: event.get(key) for key in fields}) != canonical_json(fields):
            raise ValueError("Audit idempotency conflict")
        return dict(event)
    head = connection.execute("SELECT sequence_no,event_sha256 FROM specialist_audit_events WHERE tenant_id=%s ORDER BY sequence_no DESC LIMIT 1",
                              (tenant_id,)).fetchone()
    now = connection.execute("SELECT clock_timestamp() AS observed_at").fetchone()["observed_at"].astimezone(timezone.utc)
    sequence = head["sequence_no"] + 1 if head else 1
    previous = head["event_sha256"] if head else "0" * 64
    event = {**fields, "sequence": sequence, "previous_sha256": previous, "occurred_at": now.isoformat()}
    event["sha256"] = hashlib.sha256(canonical_json(event).encode()).hexdigest()
    # Reuse strict event/schema validation; do not allow unstructured log bodies.
    build_daily_activity_report([event], period_start=now, period_end=now+timedelta(microseconds=1),
        first_sequence=sequence, previous_sha256=previous, last_sequence=sequence, last_sha256=event["sha256"])
    connection.execute("""INSERT INTO specialist_audit_events
        (tenant_id,sequence_no,event_id,event_json,previous_sha256,event_sha256)
        VALUES (%s,%s,%s,%s::jsonb,%s,%s)""",
        (tenant_id,sequence,event_id,canonical_json(event),previous,event["sha256"]))
    return event
