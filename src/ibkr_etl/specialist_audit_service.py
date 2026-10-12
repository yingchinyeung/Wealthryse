"""Trusted audit service commit and protected-archive handoff.

No HTTP authentication boundary or agent execution authority is provided here.
The caller authenticates the producer and validates its exact operation scope.
An archive outage fails closed; retrying the same event repairs the anchor after
an already committed database insert without duplicating or rewriting history.
"""
import hashlib
import uuid

from azure.core.exceptions import ResourceExistsError

from .specialist_audit import append_event
from .util import canonical_json


ARCHIVE_CONTAINER = "specialist-audit-checkpoints"


def archive_key(tenant_id, event):
    if str(uuid.UUID(tenant_id)) != tenant_id or type(event["sequence"]) is not int or event["sequence"] < 1:
        raise ValueError("Exact audit tenant and positive sequence required")
    return f"{tenant_id}/events/{event['sequence']:020d}.json"


def validate_archive_properties(properties):
    # The trusted ARM reader must provide the actual container response, not an
    # agent-supplied assertion. Legal hold is checked on every archive operation.
    p = properties["properties"]
    if (p.get("publicAccess") != "None" or not p.get("hasLegalHold") or p.get("deleted")
            or p.get("legalHold", {}).get("protectedAppendWritesHistory", {}).get("allowProtectedAppendWritesAll")):
        raise ValueError("Private immutable audit archive is not confirmed")


class ProtectedAuditArchive:
    """Dependencies are created by the trusted service, never supplied by agents."""
    def __init__(self, container_client, read_arm_properties):
        if container_client.container_name != ARCHIVE_CONTAINER:
            raise ValueError("Unexpected audit archive")
        self.client = container_client
        self.read_arm_properties = read_arm_properties

    def put(self, tenant_id, event):
        validate_archive_properties(self.read_arm_properties())
        name = archive_key(tenant_id, event)
        payload = canonical_json({"tenant_id": tenant_id, "event": event}).encode()
        blob = self.client.get_blob_client(name)
        try:
            blob.upload_blob(payload, overwrite=False)
        except ResourceExistsError:
            # Retry is valid only when independently retained bytes are exact.
            pass
        return self.verify(tenant_id, event)

    def verify(self, tenant_id, event):
        """Read-only verification; report builders must not repair missing evidence."""
        validate_archive_properties(self.read_arm_properties())
        name = archive_key(tenant_id, event)
        payload = canonical_json({"tenant_id": tenant_id, "event": event}).encode()
        blob = self.client.get_blob_client(name)
        properties = blob.get_blob_properties()
        if properties.size != len(payload):
            raise ValueError("Audit archive collision")
        # Bounded exact read, so a compromised/unexpected blob cannot exhaust RAM.
        if blob.download_blob(offset=0, length=len(payload)+1).readall() != payload:
            raise ValueError("Audit archive content mismatch")
        validate_archive_properties(self.read_arm_properties())
        return {"container": ARCHIVE_CONTAINER, "blob_name": name,
                "sha256": hashlib.sha256(payload).hexdigest(), "etag": str(properties.etag)}


def commit_and_archive(connection, archive, **fields):
    """Return only after the DB commit and exact protected-archive readback.

    Dedicated autocommit connection required: nesting inside a caller's open
    transaction would acknowledge an intent that was not actually committed.
    This receipt is necessary evidence, not an execution permit.
    """
    if not connection.autocommit or connection.info.transaction_status != 0:
        raise ValueError("Idle dedicated autocommit audit connection required")
    with connection.transaction():
        event = append_event(connection, **fields)
    anchor = archive.put(fields["tenant_id"], event)
    return {"event": event, "archive": anchor, "database_committed": True,
            "protected_archive_verified": True, "execution_authorized": False}
