"""Inert exact secretless ACA runtime canary; not dispatch or permission proof.

Only supplied, independently hash-bound checker bytes and a copied expected
runtime descriptor are used. No files, environment values, SDKs, connectors,
SQL, model tools, credentials or cloud actions are read or executed. The fixed
payload is identical to the accepted ACR probe family. A trusted controller
must bind protected provenance, reserve each effect and compare actual output.
"""
import base64
import gzip
import hashlib
import ipaddress
import json
import math
import re

import owner_task_provider_runtime as runtime


SUBSCRIPTION = "3956e821-cee4-403f-8708-e84e2ed490eb"
RESOURCE_GROUP = "rg-ibkr-database-v11"
LOCATION = "eastasia"
API = "2025-07-01"
BASE = "/subscriptions/" + SUBSCRIPTION + "/resourceGroups/" + RESOURCE_GROUP + "/providers/"
JOB = "ibkrv2stg-ownertrial-runtime-canary"
JOB_ID = BASE + "Microsoft.App/jobs/" + JOB
IDENTITY = "ibkrv2stg-ownertrial-runtime-identity"
IDENTITY_ID = BASE + "Microsoft.ManagedIdentity/userAssignedIdentities/" + IDENTITY
ENVIRONMENT = BASE + "Microsoft.App/managedEnvironments/ibkrv2stg-env"
ENVIRONMENT_DEFAULT_DOMAIN = "purplerock-8cff24a2.eastasia.azurecontainerapps.io"
EVENT_STREAM_ENDPOINT = ("https://eastasia.azurecontainerapps.dev/subscriptions/" + SUBSCRIPTION
    + "/resourceGroups/" + RESOURCE_GROUP + "/containerApps/" + JOB + "/eventstream")
REGISTRY_SERVER = "ibkrv2stgauhecwc4itaoo.azurecr.io"
REGISTRY_ID = BASE + "Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo"
IMAGE = REGISTRY_SERVER + "/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c"
CONTAINER = "secretless-runtime-canary"
OPERATION_ID = "4a403e7f-1f03-4401-89fb-8413bc1d32bb"
PROBE_FAMILY_ID = "3d3a5fca-b9d6-4323-b81b-64eaa6c4f23d"
RUNTIME_SOURCE_PATH = "scripts/owner_task_provider_runtime.py"
RUNTIME_SOURCE_SHA256 = "161db0ff2d9145f3fe72a2d96b0c6e8a2a71eec3fb2a3b0cff70e06aa85ec63d"
PAYLOAD_SHA256 = "6607082862f1e780f5a62e8a2a3fcc54e99776200197ae93131e5140434def5f"
PREFIX = "OWNER_TASK_PROVIDER_RUNTIME "
MAX_SOURCE_BYTES = 32768
MAX_JSON_BYTES = 131072
HOLD_SECONDS = 120
HOLD_SUFFIX = ";import time;time.sleep(120)"
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CODES = frozenset(("aca_runtime_source_invalid", "aca_runtime_expected_descriptor_invalid",
    "aca_runtime_native_json_invalid", "aca_runtime_definition_invalid", "aca_runtime_readback_invalid",
    "aca_runtime_identity_invalid", "aca_runtime_contract_failed"))
FALSE_FLAGS = ("dispatch_authorized", "expected_runtime_provenance_verified", "image_runtime_qualified",
    "effective_identity_permissions_verified", "registry_authentication_verified", "actual_execution_verified",
    "provider_semantics_verified", "fresh_restricted_login_verified", "model_credential_containment_verified",
    "owner_runtime_active", "six_specialist_runtimes_active", "ibkr_source_writes",
    "production_migration_059_installed", "platform_injected_credentials_excluded",
    "model_or_database_credentials_supplied")


class AcaRuntimeContractError(ValueError):
    """Fixed metadata code; never reflects input values or library failures."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in _CODES else "aca_runtime_contract_failed"
        super().__init__(self.code)


def require(condition, code):
    if not condition:
        raise AcaRuntimeContractError(code)


def _native(value, depth=0):
    require(depth <= 64, "aca_runtime_native_json_invalid")
    if type(value) in (type(None), bool, int, str):
        return
    if type(value) is float:
        require(math.isfinite(value), "aca_runtime_native_json_invalid")
        return
    if type(value) is list:
        for item in value: _native(item, depth + 1)
        return
    require(type(value) is dict and all(type(key) is str for key in value), "aca_runtime_native_json_invalid")
    for item in value.values(): _native(item, depth + 1)


def canonical(value):
    try:
        _native(value)
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
        require(0 < len(result) <= MAX_JSON_BYTES, "aca_runtime_native_json_invalid")
        return result
    except AcaRuntimeContractError: raise
    except Exception: raise AcaRuntimeContractError("aca_runtime_native_json_invalid") from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _expected_runtime(value):
    try:
        canonical(value)
        selected = runtime.validate_descriptor(value)
        require(canonical(selected) == canonical(value), "aca_runtime_expected_descriptor_invalid")
        return json.loads(canonical(selected))
    except Exception:
        raise AcaRuntimeContractError("aca_runtime_expected_descriptor_invalid") from None


def bundle(source_bytes):
    """Deterministically reproduce the frozen family's exact Python argv."""
    try:
        require(type(source_bytes) is bytes and 0 < len(source_bytes) <= MAX_SOURCE_BYTES
            and hashlib.sha256(source_bytes).hexdigest() == RUNTIME_SOURCE_SHA256, "aca_runtime_source_invalid")
        text = source_bytes.decode("utf-8", errors="strict")
        require("\x00" not in text and text.encode("utf-8") == source_bytes, "aca_runtime_source_invalid")
        compile(source_bytes, RUNTIME_SOURCE_PATH, "exec")  # Parsing only, never execute the caller source.
        encoded = base64.b64encode(source_bytes).decode("ascii")
        script = ("import base64,hashlib,json,types\n"
            + "b=base64.b64decode(" + repr(encoded) + ",validate=True)\n"
            + "assert hashlib.sha256(b).hexdigest()==" + repr(RUNTIME_SOURCE_SHA256) + "\n"
            + 'm=types.ModuleType("owner_task_provider_runtime");m.__file__=' + repr(RUNTIME_SOURCE_PATH) + "\n"
            + 'exec(compile(b,m.__file__,"exec"),m.__dict__)\n'
            + "try:\n r=m.check_runtime()\n"
            + 'except Exception:\n print("OWNER_TASK_PROVIDER_RUNTIME_FAILURE runtime_check_failed",flush=True);raise SystemExit(1)\n'
            + 'v={"operation_id":' + repr(PROBE_FAMILY_ID) + ',"image":' + repr(IMAGE)
            + ',"runtime_source_sha256":' + repr(RUNTIME_SOURCE_SHA256) + ',"runtime":r}\n'
            + "print(" + repr(PREFIX) + '+json.dumps(v,sort_keys=True,separators=(",",":")),flush=True)\n')
        require(hashlib.sha256(script.encode("utf-8")).hexdigest() == PAYLOAD_SHA256, "aca_runtime_source_invalid")
        packed = base64.b64encode(gzip.compress(script.encode("utf-8"), mtime=0)).decode("ascii")
        runner = "import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('" + packed + "')),'fixed-runtime-probe','exec'))"
        probe_args = ["-I", "-B", "-c", runner]
        args = ["-I", "-B", "-c", runner + HOLD_SUFFIX]
        require(len(canonical(args)) <= 65536, "aca_runtime_source_invalid")
        return dict(args=args, runtime_source_sha256=RUNTIME_SOURCE_SHA256, probe_payload_sha256=PAYLOAD_SHA256,
            args_sha256=digest(args), aca_args_sha256=digest(args), probe_args_sha256=digest(probe_args),
            aca_trampoline_sha256=hashlib.sha256(args[3].encode("utf-8")).hexdigest(), hold_seconds=HOLD_SECONDS,
            probe_family_operation_id=PROBE_FAMILY_ID)
    except AcaRuntimeContractError: raise
    except Exception: raise AcaRuntimeContractError("aca_runtime_source_invalid") from None


def build(source_bytes, expected_runtime):
    """Candidate configuration only; caller must independently authenticate provenance."""
    try:
        payload = bundle(source_bytes)
        expected = _expected_runtime(expected_runtime)
        value = dict(location=LOCATION, identity=dict(type="UserAssigned", userAssignedIdentities={IDENTITY_ID: {}}),
            tags=dict(purpose="owner-secretless-aca-runtime", operation=OPERATION_ID, probeFamily=PROBE_FAMILY_ID,
                runtimeSourceSha256=RUNTIME_SOURCE_SHA256, probePayloadSha256=PAYLOAD_SHA256,
                expectedRuntimeSha256=digest(expected), acaArgsSha256=payload["aca_args_sha256"],
                acaTrampolineSha256=payload["aca_trampoline_sha256"], holdSeconds=str(HOLD_SECONDS), apiVersion=API),
            properties=dict(environmentId=ENVIRONMENT,
                configuration=dict(triggerType="Manual", replicaTimeout=600, replicaRetryLimit=0,
                    manualTriggerConfig=dict(parallelism=1, replicaCompletionCount=1), secrets=[],
                    registries=[dict(server=REGISTRY_SERVER, identity=IDENTITY_ID)],
                    identitySettings=[dict(identity=IDENTITY_ID, lifecycle="None")]),
                template=dict(containers=[dict(name=CONTAINER, image=IMAGE, command=["python"], args=payload["args"],
                    env=[], resources=dict(cpu=0.5, memory="1Gi"))], volumes=[], initContainers=[])))
        return dict(status="candidate_secretless_aca_runtime_contract", definition=value, definition_sha256=digest(value),
            expected_runtime=expected, expected_runtime_sha256=digest(expected), image=IMAGE, operation_id=OPERATION_ID,
            api_version=API, **payload, sql_calls=0, model_calls=0, credential_values_read=0, **{key: False for key in FALSE_FLAGS})
    except AcaRuntimeContractError: raise
    except Exception: raise AcaRuntimeContractError("aca_runtime_contract_failed") from None


def definition(source_bytes, expected_runtime):
    return build(source_bytes, expected_runtime)["definition"]


def validate_definition(value, source_bytes, expected_runtime):
    wanted = definition(source_bytes, expected_runtime)
    require(canonical(value) == canonical(wanted), "aca_runtime_definition_invalid")
    return dict(definition_sha256=digest(wanted), **{key: False for key in FALSE_FLAGS})


def _empty(value):
    return value is None or type(value) is list and not value


def validate_template(value, source_bytes, expected_runtime, *, execution=False):
    require(type(execution) is bool, "aca_runtime_readback_invalid")
    wanted = definition(source_bytes, expected_runtime)["properties"]["template"]
    canonical(value)
    require(type(value) is dict and set(value) <= {"containers", "volumes", "initContainers"}
        and _empty(value.get("volumes")) and _empty(value.get("initContainers")), "aca_runtime_readback_invalid")
    containers = value.get("containers")
    require(type(containers) is list and len(containers) == 1 and type(containers[0]) is dict, "aca_runtime_readback_invalid")
    actual, expected = containers[0], wanted["containers"][0]
    require(set(actual) <= set(expected) | {"volumeMounts", "probes"}
        and _empty(actual.get("env")) and _empty(actual.get("volumeMounts")) and _empty(actual.get("probes"))
        and all(canonical(actual.get(key)) == canonical(expected[key]) for key in ("name", "image", "command", "args")),
        "aca_runtime_readback_invalid")
    resources = actual.get("resources")
    require(type(resources) is dict and set(resources) <= {"cpu", "memory", "ephemeralStorage"}
        and ("ephemeralStorage" not in resources or type(resources["ephemeralStorage"]) is str and resources["ephemeralStorage"] == "")
        and canonical({key: resources.get(key) for key in ("cpu", "memory")}) == canonical(expected["resources"]),
        "aca_runtime_readback_invalid")
    return dict(template_sha256=digest(wanted), execution_template_requested=execution,
        **{key: False for key in FALSE_FLAGS})


def _guid(value):
    require(type(value) is str and _GUID.fullmatch(value) is not None
        and value != "00000000-0000-0000-0000-000000000000", "aca_runtime_identity_invalid")


def validate_identity(value, *, principal_id, client_id):
    _guid(principal_id); _guid(client_id)
    canonical(value)
    require(type(value) is dict and set(value) <= {"type", "userAssignedIdentities", "principalId", "tenantId"}
        and value.get("type") == "UserAssigned" and value.get("principalId") is None and value.get("tenantId") is None,
        "aca_runtime_identity_invalid")
    assigned = value.get("userAssignedIdentities")
    require(type(assigned) is dict and set(assigned) == {IDENTITY_ID}
        and type(assigned[IDENTITY_ID]) is dict
        and assigned[IDENTITY_ID] == dict(principalId=principal_id, clientId=client_id), "aca_runtime_identity_invalid")
    return dict(identity_id=IDENTITY_ID, principal_id=principal_id, client_id=client_id,
        effective_identity_permissions_verified=False, identity_provenance_verified=False)


def _server_metadata(value):
    """Known descriptive defaults only; no access or provenance assertion."""
    system_data = value.get("systemData")
    if system_data is not None:
        fields = {"createdBy", "createdByType", "createdAt", "lastModifiedBy", "lastModifiedByType", "lastModifiedAt"}
        require(type(system_data) is dict and set(system_data) <= fields, "aca_runtime_readback_invalid")
        for name, item in system_data.items():
            require(item is None or type(item) is str and 0 < len(item) <= 512 and item.isascii()
                and all(char.isprintable() for char in item), "aca_runtime_readback_invalid")
            if item is not None and name.endswith("Type"):
                require(item in ("User", "Application", "ManagedIdentity", "Key"), "aca_runtime_readback_invalid")
            if item is not None and name.endswith("At"):
                require(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|\+00:00)", item) is not None,
                    "aca_runtime_readback_invalid")
    addresses = value["properties"].get("outboundIpAddresses")
    if addresses is not None:
        require(type(addresses) is list and len(addresses) <= 64, "aca_runtime_readback_invalid")
        for item in addresses:
            require(type(item) is str and 0 < len(item) <= 45, "aca_runtime_readback_invalid")
            try: ipaddress.ip_address(item)
            except Exception: raise AcaRuntimeContractError("aca_runtime_readback_invalid") from None
    if "eventStreamEndpoint" in value["properties"]:
        # Registered unsigned metadata only. Never follow or fetch this URL.
        require(type(value["properties"]["eventStreamEndpoint"]) is str
            and value["properties"]["eventStreamEndpoint"] == EVENT_STREAM_ENDPOINT, "aca_runtime_readback_invalid")


def validate_job(value, source_bytes, expected_runtime, *, principal_id, client_id):
    wanted = definition(source_bytes, expected_runtime)
    canonical(value)
    require(type(value) is dict and set(value) <= {"id", "name", "type", "location", "tags", "identity", "properties", "systemData"}
        and value.get("id") == JOB_ID and value.get("name") == JOB and value.get("type") == "Microsoft.App/jobs"
        and value.get("location") in (LOCATION, "East Asia") and canonical(value.get("tags")) == canonical(wanted["tags"]),
        "aca_runtime_readback_invalid")
    validate_identity(value.get("identity"), principal_id=principal_id, client_id=client_id)
    properties = value.get("properties")
    require(type(properties) is dict and set(properties) <= {"environmentId", "configuration", "template", "provisioningState",
        "outboundIpAddresses", "workloadProfileName", "eventStreamEndpoint"} and properties.get("environmentId") == ENVIRONMENT
        and properties.get("provisioningState") == "Succeeded" and properties.get("workloadProfileName") in (None, "Consumption"),
        "aca_runtime_readback_invalid")
    _server_metadata(value)
    config = properties.get("configuration"); expected = wanted["properties"]["configuration"]
    require(type(config) is dict and set(config) <= set(expected) | {"scheduleTriggerConfig", "eventTriggerConfig", "dapr"}
        and config.get("scheduleTriggerConfig") is None and config.get("eventTriggerConfig") is None
        and config.get("dapr") is None
        and _empty(config.get("secrets"))
        and all(canonical(config.get(key)) == canonical(expected[key]) for key in expected if key != "secrets"),
        "aca_runtime_readback_invalid")
    validate_template(properties.get("template"), source_bytes, expected_runtime)
    return dict(job_id=JOB_ID, definition_sha256=digest(wanted), principal_id=principal_id, client_id=client_id,
        **{key: False for key in FALSE_FLAGS})


def validate_runtime_record(value, source_bytes, expected_runtime):
    """Pure relation check; caller must independently establish log/replica provenance."""
    bundle(source_bytes)
    expected = _expected_runtime(expected_runtime)
    canonical(value)
    require(type(value) is dict and set(value) == {"operation_id", "image", "runtime_source_sha256", "runtime"}
        and value.get("operation_id") == PROBE_FAMILY_ID and value.get("image") == IMAGE
        and value.get("runtime_source_sha256") == RUNTIME_SOURCE_SHA256
        and canonical(_expected_runtime(value.get("runtime"))) == canonical(expected), "aca_runtime_readback_invalid")
    return dict(reported_runtime_matches_expected=True, expected_runtime_sha256=digest(expected),
        **{key: False for key in FALSE_FLAGS})
