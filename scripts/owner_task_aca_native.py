"""Inert, fixed-job native metadata adapters; never authorize an execution.

Input metadata is copied and returned without filling, coercing or flattening
native properties. Only the observed execution imageType is removed in a
detached validation shadow. Nested replicas are checked directly; their URLs
are unsigned descriptive metadata, never fetched. Valid not-ready metadata
has a distinct pending classification, not successful capture evidence.

No credential discovery, environment, cloud, SQL, model or dispatch operation
is provided. Frozen execution/job validation also performs its existing fixed
local source-byte check. Protected provenance, current authority, cancellation,
effect reservations and actual execution/log ownership remain caller duties.
"""
import re

import qualify_owner_task_aca_runtime_metadata as oldm


JOB, JOB_ID = oldm.JOB, oldm.JOB_ID
CONTAINER = oldm.contract.CONTAINER
REPLICA_TYPE = "Microsoft.App/containerapps/revisions/replicas"
MAX_DETAILS_CHARS = 4096
_BASE = ("eastasia.azurecontainerapps.dev/subscriptions/" + oldm.contract.SUBSCRIPTION
    + "/resourceGroups/" + oldm.contract.RESOURCE_GROUP)


class NativeResponseError(ValueError):
    def __init__(self, code):
        allowed = {"native_job_invalid", "native_execution_invalid", "native_replica_invalid", "native_log_binding_invalid"}
        super().__init__(code if type(code) is str and code in allowed else "native_replica_invalid")


class ReplicaPending(ValueError):
    """Structurally valid metadata is not currently ready; not authority to retry."""
    def __init__(self): super().__init__("native_replica_pending")


def _require(condition, code="native_replica_invalid"):
    if not condition: raise NativeResponseError(code)


def _text(value, limit, *, nonempty=False):
    return (type(value) is str and (not nonempty or bool(value)) and len(value) <= limit
        and all(char.isprintable() for char in value) and len(value.encode("utf-8")) <= limit * 4)


def _copy(value, code):
    try: return oldm.native_copy(value)
    except Exception: raise NativeResponseError(code) from None


def _name(value):
    try: return oldm.n.execution_name(value)
    except Exception: raise NativeResponseError("native_replica_invalid") from None


def validate_job_metadata(value, expected_runtime, principal_id, client_id):
    """Delegate the unchanged fixed native-job and credential-free contract."""
    try: return oldm.validate_job_metadata(value, expected_runtime, principal_id, client_id)
    except Exception: raise NativeResponseError("native_job_invalid") from None


def validate_execution(value, pf, name, *, terminal):
    """Return original properties; only exact ContainerImage is shadow-removed."""
    raw, context = _copy(value, "native_execution_invalid"), _copy(pf, "native_execution_invalid")
    _require(type(terminal) is bool, "native_execution_invalid")
    shadow = _copy(raw, "native_execution_invalid")
    try:
        containers = shadow["properties"]["template"]["containers"]
        _require(type(containers) is list and len(containers) == 1 and type(containers[0]) is dict,
            "native_execution_invalid")
        container = containers[0]
        if "imageType" in container:
            _require(type(container["imageType"]) is str and container["imageType"] == "ContainerImage",
                "native_execution_invalid")
            del container["imageType"]
        oldm.validate_execution(shadow, context, name, terminal=terminal)
        return raw["properties"]
    except Exception: raise NativeResponseError("native_execution_invalid") from None


def endpoint_metadata(name, replica):
    """Exact unsigned endpoint strings; construction conveys no authentication."""
    name = _name(name)
    _require(type(replica) is str and re.fullmatch(re.escape(name) + r"-[a-z0-9]{5,12}", replica) is not None)
    tail = "/replicas/" + replica + "/containers/" + CONTAINER
    return dict(execEndpoint="wss://" + _BASE + "/containerApps/" + JOB + "/revisions/" + name + tail + "/exec",
        logStreamEndpoint="https://" + _BASE + "/jobs/" + JOB + "/executions/" + name + tail + "/logstream")


def _replica(value, name):
    name = _name(name); raw = _copy(value, "native_replica_invalid")
    _require(type(raw) is dict and set(raw) <= {"value", "nextLink"} and type(raw.get("value")) is list
        and len(raw["value"]) <= 1 and (raw.get("nextLink") is None
            or type(raw["nextLink"]) is str and raw["nextLink"] == ""))
    if not raw["value"]: return None, "pending"
    row = raw["value"][0]
    _require(type(row) is dict and set(row) == {"id", "name", "type", "properties"})
    replica = row.get("name"); endpoints = endpoint_metadata(name, replica)
    _require(row["id"] == JOB_ID + "/executions/" + name + "/replicas/" + replica
        and row["type"] == REPLICA_TYPE)
    props = row["properties"]
    required = {"createdTime", "runningState", "containers"}
    _require(type(props) is dict and required <= set(props) <= required | {"runningStateDetails", "initContainers"}
        and ("initContainers" not in props or type(props["initContainers"]) is list and not props["initContainers"])
        and type(props["runningState"]) is str and props["runningState"] in {"Running", "NotRunning", "Unknown"})
    try: oldm.transport.utc(props["createdTime"])
    except Exception: raise NativeResponseError("native_replica_invalid") from None
    if "runningStateDetails" in props: _require(_text(props["runningStateDetails"], MAX_DETAILS_CHARS))
    containers = props["containers"]
    _require(type(containers) is list and len(containers) == 1 and type(containers[0]) is dict)
    container = containers[0]
    required = {"name", "ready", "started", "restartCount", "runningState"}
    _require(required <= set(container) <= required | {"runningStateDetails", "execEndpoint", "logStreamEndpoint"}
        and container["name"] == CONTAINER and type(container["ready"]) is bool and type(container["started"]) is bool
        and type(container["restartCount"]) is int and container["restartCount"] == 0
        and type(container["runningState"]) is str and container["runningState"] in {"Running", "Waiting", "Terminated"})
    if "runningStateDetails" in container: _require(_text(container["runningStateDetails"], MAX_DETAILS_CHARS))
    for key, wanted in endpoints.items():
        if key in container: _require(type(container[key]) is str and container[key] == wanted)
    state = container["runningState"]
    _require(not container["ready"] or (container["started"] and state == "Running" and props["runningState"] == "Running"))
    if state == "Terminated":
        _require(container["ready"] is False and container["started"] is False)
        return row, "terminated"
    if state == "Waiting":
        _require(container["ready"] is False and container["started"] is False)
        return row, "pending"
    ready = props["runningState"] == "Running" and container["ready"] is True and container["started"] is True
    return row, "ready" if ready else "pending"


def classify_replica(value, name):
    """Return ready/pending only after ALL nested structure/URL checks pass."""
    status = _replica(value, name)[1]
    _require(status != "terminated")
    return status


def validate_replica(value, name, *, running=True):
    _require(type(running) is bool)
    row, status = _replica(value, name)
    if status == "terminated" and running: raise NativeResponseError("native_replica_invalid")
    if row is None: raise ReplicaPending()
    if running and status != "ready": raise ReplicaPending()
    return row


def validate_log_binding(rows, replica, pf):
    """Require a ready nested mapping; delegate unchanged one-event parsing."""
    raw = _copy(replica, "native_log_binding_invalid")
    _require(type(raw) is dict and type(raw.get("name")) is str, "native_log_binding_invalid")
    name = raw["name"].rsplit("-", 1)[0]
    try:
        checked = validate_replica({"value": [raw]}, name)
        return oldm.parse_console(_copy(rows, "native_log_binding_invalid"), {"name": checked["name"]},
            _copy(pf, "native_log_binding_invalid")["prerequisite"]["observed"])
    except ReplicaPending: raise
    except Exception: raise NativeResponseError("native_log_binding_invalid") from None
