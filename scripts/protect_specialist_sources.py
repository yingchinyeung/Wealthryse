"""Scoped source WORM rollout with synthetic probes and retained evidence.

Never overwrites/deletes a received source. No credentials, ingestion job or
application configuration are changed. Legal holds are retained indefinitely;
only a separately authorized administrator may clear them, never an agent.
"""
import argparse
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from azure.core.exceptions import HttpResponseError
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from audit_azure_runtime import az, RG
from deploy_metadata_monitor import request, BASE

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (("ibkrv115rcc62xxqblay", "raw-statements", "source-protection-probes"),
           ("ibkrv2stgauhecwc4itaoo", "ibkr-raw", "specialist-audit-checkpoints"))
API = "?api-version=2023-05-01"
TAG = "ibkrsourcepreservation"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def jobs_digest(jobs):
    return digest(sorted([{"name": j["name"], "identity": j.get("identity"),
        "configuration": j["properties"]["configuration"], "template": j["properties"]["template"]}
        for j in jobs], key=lambda j: j["name"]))


def path(account, container):
    if (account, container) not in {(a, c) for a, source, probe in TARGETS for c in (source, probe)}:
        raise ValueError("Unregistered protection target")
    return BASE + f"Microsoft.Storage/storageAccounts/{account}/blobServices/default/containers/{container}"


def validate_container(result):
    p = result["properties"]
    if p.get("publicAccess") != "None" or p.get("deleted") or p.get("immutableStorageWithVersioning", {}).get("enabled"):
        raise ValueError("Expected private, active container without version-level WORM")


def inventory(client):
    # Include retained previous versions, not just current names. No source body
    # is emitted into the evidence record. ETags detect any concurrent mutation.
    return sorted([{"name": b.name, "version": b.version_id, "etag": str(b.etag), "bytes": b.size}
                   for b in client.list_blobs(include=["versions"])], key=lambda r: (r["name"], r["version"] or ""))


def verify_preserved(before, after):
    current = {(b["name"], b["version"]): b for b in after}
    if any(current.get((b["name"], b["version"])) != b for b in before):
        raise ValueError("Source version disappeared or changed during protection rollout")


class Journal:
    def __init__(self):
        self.run_id = str(uuid.uuid4())
        self.folder = ROOT / ".work" / "source-protection-20261002" / self.run_id
        self.folder.mkdir(parents=True, exist_ok=False)
        self.events = []

    def record(self, action, **fields):
        event = {"sequence": len(self.events)+1, "at": datetime.now(timezone.utc).isoformat(),
                 "action": action, "previous": digest(self.events[-1]) if self.events else "0"*64, **fields}
        self.events.append(event)
        # Evidence is generated output, exclusive creation prevents local rewrite.
        with (self.folder / f"{len(self.events):03d}.json").open("x", encoding="utf-8") as output:
            json.dump(event, output, indent=2)
        summary = {key: value for key, value in event.items() if key not in {"versions", "metadata", "before", "after"}}
        if "versions" in event and isinstance(event["versions"], list):
            summary["version_count"] = len(event["versions"])
        print(json.dumps(summary), flush=True)


def ensure_container(account, container, journal):
    p = path(account, container)
    result = request("GET", p + API, missing=True)
    if result is None:
        if container == "raw-statements":
            raise ValueError("Existing original archive missing")
        journal.record("create_private_container_started", account=account, container=container)
        request("PUT", p + API, data={"properties": {"publicAccess": "None"}})
        result = request("GET", p + API)
    validate_container(result)
    return result


def hold(account, container, journal):
    p = path(account, container)
    prior = request("GET", p + API)
    validate_container(prior)
    journal.record("set_hold_started", account=account, container=container, before=prior)
    request("POST", p + "/setLegalHold" + API,
            data={"tags": [TAG], "allowProtectedAppendWritesAll": False})
    after = request("GET", p + API)
    props = after["properties"]
    if not props.get("hasLegalHold") or props.get("legalHold", {}).get("protectedAppendWritesHistory", {}).get("allowProtectedAppendWritesAll"):
        raise ValueError("Source protection not confirmed")
    journal.record("set_hold_verified", account=account, container=container, after=after)


class ProtectionNotEffective(RuntimeError):
    """Synthetic mutation succeeded while a control-plane hold was propagating."""


def verify_probe(client, name, content, journal):
    blob = client.get_blob_client(name)
    for operation in ("overwrite", "delete"):
        try:
            if operation == "overwrite":
                blob.upload_blob(b"Synthetic overwrite test only", overwrite=True)
            else:
                blob.delete_blob()
        except HttpResponseError as error:
            if error.status_code != 409 or str(error.error_code) not in {"BlobImmutableDueToLegalHold", "BlobImmutableDueToPolicy"}:
                raise RuntimeError("Probe failed for a reason other than immutability") from None
            journal.record("synthetic_mutation_denied", container=client.container_name,
                           operation=operation, error_code=str(error.error_code))
        else:
            journal.record("synthetic_mutation_unexpectedly_allowed", container=client.container_name,
                           operation=operation, name=name)
            raise ProtectionNotEffective("Synthetic mutation unexpectedly succeeded; do not protect live sources")
    if blob.download_blob().readall() != content:
        raise ValueError("Synthetic protected content changed")
    second = client.get_blob_client(name + ".new")
    second.upload_blob(content, overwrite=False, content_settings=ContentSettings(content_type="application/json"))
    if second.download_blob().readall() != content:
        raise ValueError("New-object creation/readback failed under protection")
    journal.record("synthetic_new_object_verified", container=client.container_name)


def verify_settled_probe(client, name, content, journal, *, attempts=4, delay=15):
    """Bound propagation checks to synthetic objects; never weaken assertions.

    ARM can report an active hold before blob writes enforce it. Every retry
    uses a fresh name; retain all earlier versions as evidence. Permission or
    unrelated errors stop immediately, and exhaustion never permits rollout.
    """
    if (client.account_name, client.container_name) not in {(a, p) for a, _, p in TARGETS}:
        raise ValueError("Propagation retries are restricted to probe containers")
    if attempts < 1 or attempts > 4 or delay < 0 or delay > 15:
        raise ValueError("Unbounded propagation probe")
    for attempt in range(attempts):
        candidate = name if attempt == 0 else name + f".settle-{attempt}"
        if attempt:
            time.sleep(delay)
            client.get_blob_client(candidate).upload_blob(content, overwrite=False,
                content_settings=ContentSettings(content_type="application/json"))
        try:
            verify_probe(client, candidate, content, journal)
            return
        except ProtectionNotEffective:
            journal.record("synthetic_protection_pending", attempt=attempt + 1,
                           container=client.container_name, name=candidate)
            if attempt == attempts - 1:
                raise


def prepare_probes():
    """Owner-approved contributor access, only to the separate new containers."""
    journal = Journal()
    account_context = az("account", "show")
    if account_context["id"] != "3956e821-cee4-403f-8708-e84e2ed490eb" or account_context["user"]["name"].lower() != "anthony.ying@wealthryse.ai":
        raise ValueError("Unexpected operator")
    principal = "8617fd1c-2280-40f2-92dd-45686e9d686c"
    role = "/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
    for account, _, probe in TARGETS:
        ensure_container(account, probe, journal)
        scope = path(account, probe)
        name = str(uuid.uuid5(uuid.NAMESPACE_URL, scope.lower() + principal + role))
        target = scope + "/providers/Microsoft.Authorization/roleAssignments/" + name + "?api-version=2022-04-01"
        journal.record("probe_contributor_grant_started", scope=scope, principal=principal)
        result = request("PUT", target, data={"properties": {"roleDefinitionId": role, "principalId": principal, "principalType": "User"}})
        if result["properties"]["principalId"] != principal or result["properties"]["scope"].lower() != scope.lower():
            raise ValueError("Probe grant readback mismatch")
        journal.record("probe_contributor_grant_verified", scope=scope, principal=principal)


def prepare_archive_reader():
    """Explicit owner approval: read-only access to the single v2 raw archive."""
    journal = Journal()
    context = az("account", "show")
    if (context["id"] != "3956e821-cee4-403f-8708-e84e2ed490eb"
            or context["tenantId"] != "0578a50b-a7a1-40a4-905b-8b20dba84124"
            or context["user"]["name"].lower() != "anthony.ying@wealthryse.ai"):
        raise ValueError("Unexpected operator")
    principal = "8617fd1c-2280-40f2-92dd-45686e9d686c"
    scope = path("ibkrv2stgauhecwc4itaoo", "ibkr-raw")
    validate_container(request("GET", scope + API))
    role = "/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/providers/Microsoft.Authorization/roleDefinitions/2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
    definition = request("GET", role + "?api-version=2022-04-01")
    permissions = definition["properties"]["permissions"]
    if (definition["properties"]["roleName"] != "Storage Blob Data Reader"
            or any(not action.lower().endswith("/read")
                   for permission in permissions for action in permission.get("dataActions", []))):
        raise ValueError("Expected built-in read-only blob role")
    name = str(uuid.uuid5(uuid.NAMESPACE_URL, scope.lower() + principal + role))
    target = scope + "/providers/Microsoft.Authorization/roleAssignments/" + name + "?api-version=2022-04-01"
    journal.record("archive_reader_grant_started", scope=scope, principal=principal, role=role)
    request("PUT", target, data={"properties": {"roleDefinitionId": role,
        "principalId": principal, "principalType": "User"}})
    actual = request("GET", target)["properties"]
    if (actual["principalId"] != principal or actual["scope"].lower() != scope.lower()
            or actual["roleDefinitionId"].lower() != role.lower()):
        raise ValueError("Archive reader grant readback mismatch")
    journal.record("archive_reader_grant_verified", scope=scope, principal=principal, role=role)


def run(apply):
    journal = Journal()
    if az("account", "show")["id"] != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise ValueError("Unexpected Azure subscription")
    jobs_before = az("containerapp", "job", "list", "-g", RG)
    # No secrets resolved; persist only configuration hashes, not secret bindings.
    jobs_hash = jobs_digest(jobs_before)
    journal.record("preflight", apply=apply, jobs_sha256=jobs_hash)
    try:
        for account, source, probe in TARGETS:
            service = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=AzureCliCredential())
            source_meta = request("GET", path(account, source) + API, missing=True)
            if source_meta:
                validate_container(source_meta)
            before = inventory(service.get_container_client(source)) if source_meta else []
            journal.record("source_inventory", account=account, container=source, metadata=source_meta,
                           versions=before, inventory_sha256=digest(before))
            if not apply:
                continue
            ensure_container(account, probe, journal)
            client = service.get_container_client(probe)
            name = "protection-probes/" + journal.run_id + ".json"
            content = json.dumps({"synthetic": True, "purpose": "source-immutability-probe", "run_id": journal.run_id}).encode()
            journal.record("synthetic_create_started", account=account, container=probe, name=name)
            client.get_blob_client(name).upload_blob(content, overwrite=False,
                content_settings=ContentSettings(content_type="application/json"))
            hold(account, probe, journal)
            verify_settled_probe(client, name, content, journal)
            ensure_container(account, source, journal)
            hold(account, source, journal)
            after = inventory(service.get_container_client(source))
            verify_preserved(before, after)
            journal.record("source_inventory_preserved", account=account, container=source,
                           versions=len(after), inventory_sha256=digest(after))
        if jobs_digest(az("containerapp", "job", "list", "-g", RG)) != jobs_hash:
            raise ValueError("Job definitions changed concurrently; review required")
        journal.record("complete", applied=apply, job_definitions_unchanged=True,
                       source_data_mutations_attempted=False, flex_requests=0)
        if apply:
            # Independent protected evidence copy, create-only. No new role grant.
            account = TARGETS[1][0]
            payload = json.dumps({"run_id": journal.run_id, "events": journal.events}, sort_keys=True).encode()
            target = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=AzureCliCredential()).get_blob_client(
                "specialist-audit-checkpoints", "source-protection/" + journal.run_id + ".json")
            receipt = target.upload_blob(payload, overwrite=False, content_settings=ContentSettings(content_type="application/json"))
            pinned = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=AzureCliCredential()).get_blob_client(
                "specialist-audit-checkpoints", "source-protection/" + journal.run_id + ".json", version_id=receipt.get("version_id"))
            if pinned.download_blob().readall() != payload:
                raise ValueError("Protected evidence readback failed")
            journal.record("evidence_anchored", sha256=hashlib.sha256(payload).hexdigest(), version=receipt.get("version_id"), url=target.url)
    except Exception as error:
        journal.record("stopped", error_type=type(error).__name__,
                       http_status=getattr(error, "status_code", None),
                       error_code=str(getattr(error, "error_code", "")))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Protect only registered source/probe containers")
    parser.add_argument("--prepare-probes", action="store_true")
    parser.add_argument("--prepare-archive-reader", action="store_true")
    args = parser.parse_args()
    if sum((args.prepare_probes, args.prepare_archive_reader, args.apply)) > 1:
        parser.error("Prepare access separately before applying protection")
    if args.prepare_archive_reader:
        prepare_archive_reader()
    elif args.prepare_probes:
        prepare_probes()
    else:
        run(args.apply)
