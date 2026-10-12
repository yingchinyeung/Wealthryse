"""Fixed native ARM/Logs boundaries for the secretless ACA runtime canary.

Import is inert. Explicit caller credentials are never discovered here. These
parsers establish observed metadata only, not tenant-wide permission closure,
absence of future log delivery, SQL access or model credential containment.
Effect attempts are one-shot within this instance; durable protected reservation
and independent authorization belong to the separate trusted controller.
Injected credentials/sessions are trusted test or controller dependencies, not
untrusted agent callbacks and not a substitute for runtime identity acceptance.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests
from requests.adapters import HTTPAdapter

import owner_task_aca_runtime_contract as contract

ARM = "https://management.azure.com"
ARM_AUDIENCE = ARM + "/.default"
WORKSPACE = "5264a124-10cd-45f6-93a5-6d65c37e9bfb"
LOGS_ENDPOINT = "https://api.loganalytics.io/v1/workspaces/" + WORKSPACE + "/query"
LOGS_AUDIENCE = "https://api.loganalytics.io/.default"
MI_API = "2023-01-31"
JOB_API = "2025-07-01"
ENV_API = "2024-03-01"
REGISTRY_API = "2023-07-01"
ROLE_API = "2022-04-01"
REPLICA_API = "2023-11-02-preview"
ACR_ROLE = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
ASSIGNMENT = "d9d58eae-df49-4d74-9536-876e7b61141a"
SUB_SCOPE = "/subscriptions/" + contract.SUBSCRIPTION
RG_SCOPE = SUB_SCOPE + "/resourceGroups/" + contract.RESOURCE_GROUP
ASSIGNMENT_ID = contract.REGISTRY_ID + "/providers/Microsoft.Authorization/roleAssignments/" + ASSIGNMENT
ROLE_ID = SUB_SCOPE + "/providers/Microsoft.Authorization/roleDefinitions/" + ACR_ROLE
SCOPES = (SUB_SCOPE, RG_SCOPE, contract.REGISTRY_ID)
MAX_BYTES = 2 * 1024 * 1024
MAX_PAGES = 100
MAX_ASSIGNMENTS = 16384
MAX_CONSOLE_ROWS = 512
MAX_CONSOLE_LINE = 65536
CONSOLE_COLUMNS = (("Group", "string"), ("Container", "string"), ("LogTime", "datetime"), ("Log", "string"))
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|\+00:00)")


class TransportError(ValueError):
    def __init__(self, code):
        allowed = {"aca_transport_native_invalid", "aca_transport_route_denied", "aca_transport_effect_consumed",
            "aca_transport_http_unknown", "aca_transport_response_invalid", "aca_transport_response_oversized",
            "aca_transport_close_unknown", "aca_transport_identity_invalid", "aca_transport_assignment_invalid",
            "aca_transport_role_invalid", "aca_transport_inventory_invalid", "aca_transport_replica_invalid",
            "aca_transport_logs_invalid", "aca_transport_logs_drift", "aca_transport_scope_invalid"}
        self.code = code if type(code) is str and code in allowed else "aca_transport_response_invalid"
        super().__init__(self.code)


def require(condition, code):
    if not condition:
        raise TransportError(code)


def _native(value, depth=0):
    require(depth <= 64, "aca_transport_native_invalid")
    if type(value) is str:
        require(len(value) <= MAX_BYTES, "aca_transport_native_invalid")
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError:
            raise TransportError("aca_transport_native_invalid") from None
    elif type(value) in (type(None), bool, int):
        return
    elif type(value) is float:
        require(math.isfinite(value), "aca_transport_native_invalid")
    elif type(value) is list:
        for item in value:
            _native(item, depth + 1)
    else:
        require(type(value) is dict and all(type(key) is str for key in value), "aca_transport_native_invalid")
        for key, item in value.items():
            _native(key, depth + 1)
            _native(item, depth + 1)


def canonical(value):
    try:
        _native(value)
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
        require(0 < len(result) <= MAX_BYTES, "aca_transport_response_oversized")
        return result
    except TransportError:
        raise
    except Exception:
        raise TransportError("aca_transport_native_invalid") from None


def strict_json(payload):
    require(type(payload) is bytes and 0 < len(payload) <= MAX_BYTES, "aca_transport_response_invalid")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "aca_transport_response_invalid")
            result[key] = value
        return result
    def number(raw):
        result = float(raw)
        require(math.isfinite(result), "aca_transport_response_invalid")
        return result
    try:
        result = json.loads(payload.decode("utf-8", errors="strict"), object_pairs_hook=pairs,
            parse_float=number, parse_constant=lambda _raw: require(False, "aca_transport_response_invalid"))
        canonical(result)
        return result
    except Exception:
        raise TransportError("aca_transport_response_invalid") from None


def guid(value, code="aca_transport_identity_invalid"):
    require(type(value) is str and _GUID.fullmatch(value) is not None
        and value != "00000000-0000-0000-0000-000000000000", code)
    return value


def utc(value):
    require(type(value) is str and _UTC.fullmatch(value) is not None, "aca_transport_scope_invalid")
    try:
        result = datetime.fromisoformat(value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else ""))
    except Exception:
        raise TransportError("aca_transport_scope_invalid") from None
    require(result.tzinfo == timezone.utc, "aca_transport_scope_invalid")
    return result


def execution_name(value):
    require(type(value) is str and re.fullmatch(re.escape(contract.JOB) + r"-[a-z0-9]{7,12}", value) is not None,
        "aca_transport_scope_invalid")
    return value


def identity_body():
    return {"location": contract.LOCATION, "tags": {"purpose": "owner-secretless-aca-runtime", "operation": contract.OPERATION_ID}}


def assignment_body(principal_id):
    return {"properties": {"principalId": guid(principal_id), "principalType": "ServicePrincipal", "roleDefinitionId": ROLE_ID}}


def start_template(definition):
    """StartJobExecutionTemplate omits ONLY the known empty volumes field."""
    value = strict_json(canonical(definition))["properties"]["template"]
    require(type(value) is dict and value.get("volumes") == [], "aca_transport_route_denied")
    del value["volumes"]
    return value


def start_body(runtime_source_bytes, expected_runtime):
    return start_template(contract.definition(runtime_source_bytes, expected_runtime))


def validate_identity_metadata(value):
    canonical(value)
    require(type(value) is dict and set(value) <= {"id", "name", "type", "location", "tags", "properties", "systemData"}
        and value.get("id") == contract.IDENTITY_ID and value.get("name") == contract.IDENTITY
        and value.get("type") == "Microsoft.ManagedIdentity/userAssignedIdentities"
        and value.get("location") in (contract.LOCATION, "East Asia")
        and canonical(value.get("tags")) == canonical(identity_body()["tags"]), "aca_transport_identity_invalid")
    properties = value.get("properties")
    require(type(properties) is dict and set(properties) == {"principalId", "clientId", "tenantId"}, "aca_transport_identity_invalid")
    for key in properties:
        guid(properties[key])
    require(properties["tenantId"] == "0578a50b-a7a1-40a4-905b-8b20dba84124", "aca_transport_identity_invalid")
    return strict_json(canonical(value))


def validate_assignment(value, principal_id):
    guid(principal_id)
    canonical(value)
    require(type(value) is dict and set(value) <= {"id", "name", "type", "properties", "systemData"}
        and value.get("id") == ASSIGNMENT_ID and value.get("name") == ASSIGNMENT
        and value.get("type") == "Microsoft.Authorization/roleAssignments", "aca_transport_assignment_invalid")
    p = value.get("properties")
    allowed = {"principalId", "principalType", "roleDefinitionId", "scope", "condition", "conditionVersion",
        "delegatedManagedIdentityResourceId", "description", "createdBy", "updatedBy", "createdOn", "updatedOn"}
    require(type(p) is dict and set(p) <= allowed and p.get("principalId") == principal_id
        and p.get("principalType") == "ServicePrincipal" and p.get("scope") == contract.REGISTRY_ID
        and p.get("roleDefinitionId") == ROLE_ID
        and all(p.get(key) is None for key in ("condition", "conditionVersion", "delegatedManagedIdentityResourceId")),
        "aca_transport_assignment_invalid")
    return strict_json(canonical(value))


def validate_role_definition(value):
    canonical(value)
    require(type(value) is dict and set(value) <= {"id", "name", "type", "properties"}
        and value.get("id") in (ROLE_ID, "/providers/Microsoft.Authorization/roleDefinitions/" + ACR_ROLE)
        and value.get("name") == ACR_ROLE and value.get("type") == "Microsoft.Authorization/roleDefinitions",
        "aca_transport_role_invalid")
    p = value.get("properties")
    require(type(p) is dict and set(p) <= {"roleName", "type", "description", "assignableScopes", "permissions",
        "createdOn", "updatedOn", "createdBy", "updatedBy"}
        and p.get("roleName") == "AcrPull" and p.get("type") == "BuiltInRole"
        and p.get("assignableScopes") == ["/"]
        and canonical(p.get("permissions")) == canonical([{"actions": ["Microsoft.ContainerRegistry/registries/pull/read"],
            "notActions": [], "dataActions": [], "notDataActions": []}]), "aca_transport_role_invalid")
    return strict_json(canonical(value))


def _filter(principal_id, at_scope):
    guid(principal_id)
    require(type(at_scope) is bool, "aca_transport_route_denied")
    return ("atScope() and " if at_scope else "") + "assignedTo('" + principal_id + "')"


def assignment_path(scope, principal_id, at_scope=False):
    require(type(scope) is str and scope in SCOPES, "aca_transport_route_denied")
    return scope + "/providers/Microsoft.Authorization/roleAssignments?" + urlencode({"api-version": ROLE_API, "$filter": _filter(principal_id, at_scope)})


def _route(method, path, body, missing, expected):
    require(type(method) is str and type(path) is str and len(path) <= 16384 and type(missing) is bool,
        "aca_transport_route_denied")
    parsed = urlsplit(path)
    require(not parsed.scheme and not parsed.netloc and not parsed.fragment and path.startswith("/"), "aca_transport_route_denied")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    require(len(pairs) == len({key for key, _ in pairs}), "aca_transport_route_denied")
    query = dict(pairs)
    api = query.get("api-version")
    target = parsed.path
    if method == "GET":
        require(body is None and (not missing or target in (contract.IDENTITY_ID, contract.JOB_ID, ASSIGNMENT_ID)), "aca_transport_route_denied")
        fixed = {contract.IDENTITY_ID: MI_API, contract.JOB_ID: JOB_API, contract.REGISTRY_ID: REGISTRY_API,
            contract.ENVIRONMENT: ENV_API, ROLE_ID: ROLE_API, ASSIGNMENT_ID: ROLE_API,
            contract.JOB_ID + "/executions": JOB_API}
        execution = re.escape(contract.JOB_ID) + r"/executions/" + re.escape(contract.JOB) + r"-[a-z0-9]{7,12}"
        if target in fixed:
            require(query == {"api-version": fixed[target]}, "aca_transport_route_denied")
        elif re.fullmatch(execution, target):
            require(query == {"api-version": JOB_API}, "aca_transport_route_denied")
        elif re.fullmatch(execution + r"/replicas", target):
            require(query == {"api-version": REPLICA_API}, "aca_transport_route_denied")
        elif target in tuple(scope + "/providers/Microsoft.Authorization/roleAssignments" for scope in SCOPES):
            require(not missing and api == ROLE_API and set(query) <= {"api-version", "$filter", "$skipToken", "$skiptoken"}
                and "api-version" in query and "$filter" in query
                and not {"$skipToken", "$skiptoken"} <= set(query)
                and re.fullmatch(r"(?:atScope\(\) and )?assignedTo\('[0-9a-f-]{36}'\)", query["$filter"]) is not None,
                "aca_transport_route_denied")
            principal = query["$filter"].split("'")[1]
            guid(principal, "aca_transport_route_denied")
            for key in ("$skipToken", "$skiptoken"):
                if key in query:
                    require(0 < len(query[key]) <= 8192 and query[key].isascii() and all(c.isprintable() for c in query[key]),
                        "aca_transport_route_denied")
        else:
            require(False, "aca_transport_route_denied")
    elif method in ("PUT", "POST"):
        require(not missing and type(body) is dict, "aca_transport_route_denied")
        if method == "PUT" and target == contract.IDENTITY_ID:
            wanted, version = identity_body(), MI_API
        elif method == "PUT" and target == ASSIGNMENT_ID:
            p = body.get("properties", {})
            wanted, version = assignment_body(p.get("principalId") if type(p) is dict else None), ROLE_API
        elif method == "PUT" and target == contract.JOB_ID:
            require(expected is not None, "aca_transport_route_denied")
            wanted, version = expected, JOB_API
        elif method == "POST" and target == contract.JOB_ID + "/start":
            require(expected is not None, "aca_transport_route_denied")
            wanted, version = start_template(expected), JOB_API
        else:
            require(False, "aca_transport_route_denied")
        require(query == {"api-version": version} and canonical(body) == canonical(wanted), "aca_transport_route_denied")
    else:
        require(False, "aca_transport_route_denied")
    return target


def _response(response):
    pieces, size = [], 0
    for piece in response.iter_content(chunk_size=16384):
        require(type(piece) is bytes, "aca_transport_response_invalid")
        size += len(piece)
        require(size <= MAX_BYTES, "aca_transport_response_oversized")
        pieces.append(piece)
    return strict_json(b"".join(pieces))


class ArmTransport:
    def __init__(self, credential, session=None, *, runtime_source_bytes=None, expected_runtime=None):
        self.credential = credential
        self.session = requests.Session() if session is None else session
        if session is None:
            self.session.mount("https://", HTTPAdapter(max_retries=0))
            self.session.trust_env = False
        require((runtime_source_bytes is None) == (expected_runtime is None), "aca_transport_route_denied")
        self.__expected_bytes = None if runtime_source_bytes is None else canonical(contract.definition(runtime_source_bytes, expected_runtime))
        self.token = None
        self.__effects = set()

    def call(self, method, path, *, body=None, missing=False):
        # Snapshot caller-native bytes before any authentication callback; the
        # actual request must never follow later mutation of the reviewed body.
        snapshot = None if body is None else strict_json(canonical(body))
        expected = None if self.__expected_bytes is None else strict_json(self.__expected_bytes)
        target = _route(method, path, snapshot, missing, expected)
        if method != "GET":
            effect = (method, target)
            require(effect not in self.__effects, "aca_transport_effect_consumed")
            self.__effects.add(effect)  # Consume BEFORE token/request; unknown outcome never retries.
        response = None
        try:
            if self.token is None or self.token.expires_on <= time.time() + 120:
                self.token = self.credential.get_token(ARM_AUDIENCE)
            response = self.session.request(method, ARM + path, json=snapshot,
                headers={"Authorization": "Bearer " + self.token.token}, allow_redirects=False,
                stream=True, timeout=(5, 20))
            require(type(response.status_code) is int, "aca_transport_response_invalid")
            value = _response(response)
            if response.status_code == 404 and method == "GET" and missing:
                require(type(value) is dict and set(value) == {"error"} and type(value["error"]) is dict
                    and set(value["error"]) == {"code", "message"}
                    and value["error"]["code"] in (("ResourceNotFound", "RoleAssignmentNotFound")
                        if target == ASSIGNMENT_ID else ("ResourceNotFound",))
                    and type(value["error"]["message"]) is str and len(value["error"]["message"]) <= 4096,
                    "aca_transport_response_invalid")
                return None, 404
            require(response.status_code in ((200,) if method == "GET" else (200, 201, 202))
                and type(value) is dict, "aca_transport_response_invalid")
            return value, response.status_code
        except TransportError:
            raise
        except Exception:
            raise TransportError("aca_transport_http_unknown") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    raise TransportError("aca_transport_close_unknown") from None

    def request(self, path, api, *, method="GET", body=None, missing=False):
        require(type(path) is str and type(api) is str and "?" not in path, "aca_transport_route_denied")
        return self.call(method, path + "?api-version=" + api, body=body, missing=missing)

    def assignments(self, scope, principal_id, *, at_scope=False):
        return role_assignments(self, scope, principal_id, at_scope=at_scope)


def get(client, path, api=None, *, missing=False):
    return (client.request(path, api, missing=missing) if api is not None else client.call("GET", path, missing=missing))[0]


def role_assignments(arm, scope, principal_id, *, at_scope=False):
    path = assignment_path(scope, principal_id, at_scope)
    initial = urlsplit(path)
    initial_query = dict(parse_qsl(initial.query))
    visited, rows, identifiers = set(), [], set()
    for _ in range(MAX_PAGES):
        require(path not in visited, "aca_transport_inventory_invalid")
        visited.add(path)
        value, status = arm.call("GET", path)
        require(type(status) is int and status == 200 and type(value) is dict and set(value) <= {"value", "nextLink"}
            and type(value.get("value")) is list, "aca_transport_inventory_invalid")
        for row in value["value"]:
            canonical(row)
            require(type(row) is dict and type(row.get("id")) is str and row["id"] not in identifiers,
                "aca_transport_inventory_invalid")
            identifiers.add(row["id"])
            rows.append(row)  # ALL returned grants survive; no principal/group filtering.
        require(len(rows) <= MAX_ASSIGNMENTS, "aca_transport_inventory_invalid")
        link = value.get("nextLink")
        if link is None or type(link) is str and link == "":
            return sorted(rows, key=lambda row: row["id"])
        require(type(link) is str and len(link) <= 16384, "aca_transport_inventory_invalid")
        parsed = urlsplit(link)
        require(parsed.scheme == "https" and parsed.netloc == "management.azure.com" and not parsed.fragment
            and parsed.path == initial.path, "aca_transport_inventory_invalid")
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        q = dict(pairs)
        skip_keys = set(q) - set(initial_query)
        require(len(pairs) == len(q) and all(q.get(key) == val for key, val in initial_query.items())
            and skip_keys in ({"$skipToken"}, {"$skiptoken"}), "aca_transport_inventory_invalid")
        path = parsed.path + "?" + parsed.query
        _route("GET", path, None, False, None)
    raise TransportError("aca_transport_inventory_invalid")


def role_inventory(arm, principal_id):
    return {"subscription": role_assignments(arm, SUB_SCOPE, principal_id),
        "resource_group_inherited": role_assignments(arm, RG_SCOPE, principal_id, at_scope=True),
        "registry_inherited": role_assignments(arm, contract.REGISTRY_ID, principal_id, at_scope=True),
        "observed_arm_inventory_complete": True, "tenant_permission_closure_verified": False}


def validate_replica(value, execution):
    execution_name(execution)
    canonical(value)
    require(type(value) is dict and set(value) <= {"value", "nextLink"}
        and (value.get("nextLink") is None or type(value.get("nextLink")) is str and value["nextLink"] == "")
        and type(value.get("value")) is list and len(value["value"]) == 1, "aca_transport_replica_invalid")
    row = value["value"][0]
    require(type(row) is dict and set(row) == {"id", "name", "type", "createdTime", "runningState", "containers"},
        "aca_transport_replica_invalid")
    name = row.get("name")
    require(type(name) is str and re.fullmatch(re.escape(execution) + r"-[a-z0-9]{5,12}", name) is not None
        and row.get("id") == contract.JOB_ID + "/executions/" + execution + "/replicas/" + name
        and row.get("type") == "Microsoft.App/containerapps/revisions/replicas" and row.get("runningState") == "Running",
        "aca_transport_replica_invalid")
    utc(row.get("createdTime"))
    require(canonical(row.get("containers")) == canonical([{"name": contract.CONTAINER, "ready": True,
        "started": True, "restartCount": 0, "runningState": "Running"}]), "aca_transport_replica_invalid")
    return strict_json(canonical(row))


@dataclass(frozen=True)
class ConsoleScope:
    execution: str
    start_utc: str
    end_utc: str

    def __post_init__(self):
        require(type(self) is ConsoleScope, "aca_transport_scope_invalid")
        execution_name(self.execution)
        start, end = utc(self.start_utc), utc(self.end_utc)
        require(start < end and end - start <= timedelta(hours=24), "aca_transport_scope_invalid")


def checked_scope(value):
    require(type(value) is ConsoleScope, "aca_transport_scope_invalid")
    value.__post_init__()
    return ConsoleScope(value.execution, value.start_utc, value.end_utc)


@dataclass(frozen=True)
class ConsoleQuery:
    scope: ConsoleScope

    def __post_init__(self):
        require(type(self) is ConsoleQuery, "aca_transport_scope_invalid")
        checked_scope(self.scope)

    @property
    def text(self):
        self.__post_init__()
        s = checked_scope(self.scope)
        return ("ContainerAppConsoleLogs_CL\n| where ContainerGroupName_s startswith '" + s.execution + "-'\n"
            "| where TimeGenerated >= datetime(" + s.start_utc + ") and TimeGenerated < datetime(" + s.end_utc + ")\n"
            "| project Group=ContainerGroupName_s,Container=ContainerName_s,LogTime=TimeGenerated,Log=Log_s")


def validate_console_response(value, query, *, http_status=200):
    require(type(query) is ConsoleQuery, "aca_transport_scope_invalid")
    query.__post_init__()
    canonical(value)
    require(type(http_status) is int and http_status == 200 and type(value) is dict and set(value) == {"tables"}
        and type(value["tables"]) is list and len(value["tables"]) == 1, "aca_transport_logs_invalid")
    table = value["tables"][0]
    require(type(table) is dict and set(table) == {"name", "columns", "rows"} and table.get("name") == "PrimaryResult"
        and canonical(table.get("columns")) == canonical([{"name": n, "type": t} for n, t in CONSOLE_COLUMNS])
        and type(table.get("rows")) is list and len(table["rows"]) <= MAX_CONSOLE_ROWS, "aca_transport_logs_invalid")
    rows = []
    for row in table["rows"]:
        require(type(row) is list and len(row) == 4 and all(type(v) is str for v in row), "aca_transport_logs_invalid")
        require(re.fullmatch(re.escape(query.scope.execution) + r"-[A-Za-z0-9_-]{1,128}", row[0]) is not None
            and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", row[1]) is not None
            and utc(query.scope.start_utc) <= utc(row[2]) < utc(query.scope.end_utc)
            and len(row[3]) <= MAX_CONSOLE_LINE and len(row[3].encode("utf-8")) <= MAX_CONSOLE_LINE,
            "aca_transport_logs_invalid")
        rows.append(dict(zip((name for name, _kind in CONSOLE_COLUMNS), row)))
    return rows


class LogsTransport:
    def __init__(self, credential, session=None):
        self.credential = credential
        self.session = requests.Session() if session is None else session
        if session is None:
            self.session.mount("https://", HTTPAdapter(max_retries=0))
            self.session.trust_env = False
        self.token = None

    def call(self, query):
        require(type(query) is ConsoleQuery, "aca_transport_scope_invalid")
        query.__post_init__()
        response = None
        try:
            if self.token is None or self.token.expires_on <= time.time() + 120:
                self.token = self.credential.get_token(LOGS_AUDIENCE)
            response = self.session.request("POST", LOGS_ENDPOINT,
                json={"query": query.text, "timespan": query.scope.start_utc + "/" + query.scope.end_utc},
                headers={"Authorization": "Bearer " + self.token.token}, allow_redirects=False, stream=True, timeout=(5, 20))
            require(type(response.status_code) is int and response.status_code == 200, "aca_transport_logs_invalid")
            value = _response(response)
            validate_console_response(value, query, http_status=response.status_code)
            return value
        except TransportError:
            raise
        except Exception:
            raise TransportError("aca_transport_http_unknown") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    raise TransportError("aca_transport_close_unknown") from None

    __call__ = call


def collect_console(query, scope):
    scope = checked_scope(scope)
    request = ConsoleQuery(scope)
    first = validate_console_response(query(request), request)
    second = validate_console_response(query(request), request)
    fingerprint = lambda row: canonical(row)
    require(Counter(map(fingerprint, first)) == Counter(map(fingerprint, second)), "aca_transport_logs_drift")
    rows = sorted(first, key=fingerprint)
    return {"rows": rows, "row_count": len(rows), "inventory_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
        "observed_console_inventory_stable": True, "late_delivery_excluded": False,
        "zero_log_replica_absence_verified": False, "platform_replacement_excluded": False}
