"""INERT fixed-workspace reader and bounded catalog-log assembly.

No import-time IO, credential discovery, CLI, SQL or cloud dispatch. A caller
must separately authenticate and protect the exact ARM execution/replica and
single-container mapping before supplying this scope. Log claims cannot establish
that mapping. Complete decoded bytes are transport evidence only, never provider
semantics, database isolation, source immutability or permission acceptance.

Two full header inventories enclose fixed 128-index payload reads. This detects
observed drift and preserves duplicate rows; it cannot prove the logging system
has delivered every line or that nothing arrives after the second inventory.
The trusted execution controller must handle that independent completeness gate.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time

import requests
from requests.adapters import HTTPAdapter

from ibkr_etl import owner_codex_catalog_transport as codec
from ibkr_etl import owner_codex_isolated_target as registry


WORKSPACE = "5264a124-10cd-45f6-93a5-6d65c37e9bfb"
ENDPOINT = "https://api.loganalytics.io/v1/workspaces/" + WORKSPACE + "/query"
AUDIENCE = "https://api.loganalytics.io/.default"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1025
RANGE_SIZE = 128
FAMILY_PREFIX = "OWNER_TASK_CATALOG_CHUNK"
INVENTORY_COLUMNS = (("Group","string"),("Container","string"),("LogTime","datetime"),
    ("LinePrefix","string"),("Header","string"),("RawSha256","string"),("RawBytes","long"))
PAYLOAD_COLUMNS = (("Group","string"),("Container","string"),("LogTime","datetime"),("Log","string"))
_SHA = re.compile(r"[0-9a-f]{64}")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z")


class CatalogLogsError(ValueError):
    def __init__(self, code):
        allowed = {"logs_scope_invalid", "logs_query_invalid", "logs_response_invalid", "logs_response_oversized",
            "logs_transport_failed", "logs_inventory_invalid", "logs_payload_invalid", "logs_inventory_drift",
            "logs_record_invalid"}
        self.code = code if type(code) is str and code in allowed else "logs_record_invalid"
        super().__init__(self.code)


def _require(condition, code):
    if not condition:
        raise CatalogLogsError(code)


def _pairs(pairs):
    result = {}
    for key,value in pairs:
        _require(key not in result, "logs_response_invalid")
        result[key] = value
    return result


def _json(value, code):
    try:
        _require(type(value) in (str, bytes), code)
        return json.loads(value, object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(CatalogLogsError(code)))
    except Exception:
        raise CatalogLogsError(code) from None


def _utc(value, code="logs_scope_invalid"):
    _require(type(value) is str and _UTC.fullmatch(value) is not None, code)
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except Exception:
        raise CatalogLogsError(code) from None
    _require(parsed.tzinfo == timezone.utc, code)
    return parsed


def _binding(value):
    # Frozen dataclasses can be forged. Do not deepcopy, compare or hash a slot
    # before its exact scalar type has been validated.
    _require(type(value) is codec.Binding, "logs_scope_invalid")
    fields = {}
    for name in ("purpose","profile_key","job_resource_id","operation_id","source_sha256"):
        field = getattr(value,name,None)
        _require(type(field) is str, "logs_scope_invalid")
        fields[name] = field
    try:
        return codec.Binding(**fields)
    except Exception:
        raise CatalogLogsError("logs_scope_invalid") from None


@dataclass(frozen=True)
class CatalogLogScope:
    binding: codec.Binding
    log_group: str
    container_name: str
    start_utc: str
    end_utc: str

    def __post_init__(self):
        _require(type(self) is CatalogLogScope, "logs_scope_invalid")
        checked = _binding(self.binding)
        job = "ibkrv2stg-ownertrial-catalog" if checked.purpose == "provider" else "ibkrv2stg-ownertrial-log-canary"
        _require(type(self.log_group) is str and re.fullmatch(re.escape(job)+r"-[A-Za-z0-9_-]{1,128}",self.log_group) is not None,
            "logs_scope_invalid")
        _require(type(self.container_name) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,62}",self.container_name) is not None,
            "logs_scope_invalid")
        start,end = _utc(self.start_utc),_utc(self.end_utc)
        _require(start < end and end-start <= timedelta(hours=24), "logs_scope_invalid")


def _scope(value):
    _require(type(value) is CatalogLogScope, "logs_scope_invalid")
    value.__post_init__()
    return CatalogLogScope(_binding(value.binding),value.log_group,value.container_name,value.start_utc,value.end_utc)


@dataclass(frozen=True)
class CatalogQuery:
    scope: CatalogLogScope
    kind: str
    lower: int | None = None

    def __post_init__(self):
        _require(type(self) is CatalogQuery, "logs_query_invalid")
        _scope(self.scope)
        _require(type(self.kind) is str and self.kind in ("inventory","payload"), "logs_query_invalid")
        _require(self.lower is None if self.kind == "inventory" else type(self.lower) is int
            and 0 <= self.lower < codec.MAX_CHUNKS and self.lower % RANGE_SIZE == 0, "logs_query_invalid")

    @property
    def text(self):
        self.__post_init__()
        scope = _scope(self.scope)
        base = ("ContainerAppConsoleLogs_CL\n"
            "| where ContainerGroupName_s == '"+scope.log_group+"'\n"
            "| where TimeGenerated >= datetime("+scope.start_utc+") and TimeGenerated < datetime("+scope.end_utc+")\n"
            "| where Log_s startswith '"+FAMILY_PREFIX+"'\n"
            "| extend Envelope=parse_json(substring(Log_s,"+str(len(codec.PREFIX))+"))\n")
        if self.kind == "inventory":
            return base + ("| project Group=ContainerGroupName_s,Container=ContainerName_s,LogTime=TimeGenerated,"
                "LinePrefix=substring(Log_s,0,"+str(len(codec.PREFIX))+"),"
                "Header=tostring(bag_remove_keys(Envelope,dynamic(['data_b64']))),"
                "RawSha256=hash_sha256(Log_s),RawBytes=string_size(Log_s)")
        return base + ("| where (tostring(Envelope.kind) == 'chunk' and tolong(Envelope.index) between ("+str(self.lower)
            +" .. "+str(self.lower+RANGE_SIZE-1)+"))"
            + (" or tostring(Envelope.kind) == 'manifest'" if self.lower == 0 else "")+"\n"
            "| project Group=ContainerGroupName_s,Container=ContainerName_s,LogTime=TimeGenerated,Log=Log_s")


def validate_response(value, query, *, http_status=200):
    """Strict full PrimaryResult contract; error/partial/extra-table signals deny."""
    _require(type(query) is CatalogQuery, "logs_query_invalid")
    query.__post_init__()
    _require(type(http_status) is int and http_status == 200, "logs_response_invalid")
    _require(type(value) is dict and set(value) == {"tables"} and type(value["tables"]) is list
        and len(value["tables"]) == 1, "logs_response_invalid")
    table = value["tables"][0]
    columns = INVENTORY_COLUMNS if query.kind == "inventory" else PAYLOAD_COLUMNS
    expected = [{"name":name,"type":kind} for name,kind in columns]
    _require(type(table) is dict and set(table) == {"name","columns","rows"}
        and type(table["name"]) is str and table["name"] == "PrimaryResult"
        and type(table["columns"]) is list
        and all(type(column) is dict and set(column) == {"name","type"}
            and type(column["name"]) is str and type(column["type"]) is str for column in table["columns"])
        and table["columns"] == expected
        and type(table["rows"]) is list and len(table["rows"]) <= MAX_ROWS, "logs_response_invalid")
    result = []
    for row in table["rows"]:
        _require(type(row) is list and len(row) == len(columns), "logs_response_invalid")
        for element,(_name,kind) in zip(row,columns):
            _require(type(element) is int if kind == "long" else type(element) is str, "logs_response_invalid")
            if kind != "long":
                _require(len(element) <= codec.MAX_LINE_BYTES, "logs_response_invalid")
                try:
                    byte_count = len(element.encode("utf-8"))
                except UnicodeError:
                    raise CatalogLogsError("logs_response_invalid") from None
                _require(byte_count <= codec.MAX_LINE_BYTES, "logs_response_invalid")
        result.append(dict(zip((name for name,_kind in columns),row)))
    return result  # No set/distinct/dedup: every provider row survives validation.


class LogsTransport:
    """Read-only POST to one observed workspace, never discovers credentials."""
    def __init__(self, credential, session=None):
        self.credential = credential
        self.session = requests.Session() if session is None else session
        if session is None:
            self.session.mount("https://",HTTPAdapter(max_retries=0))
        self.token = None

    def __call__(self, query):
        _require(type(query) is CatalogQuery, "logs_query_invalid")
        query.__post_init__()
        scope = _scope(query.scope)
        try:
            if self.token is None or self.token.expires_on <= time.time()+120:
                self.token = self.credential.get_token(AUDIENCE)
            response = self.session.request("POST",ENDPOINT,
                json={"query":query.text,"timespan":scope.start_utc+"/"+scope.end_utc},
                headers={"Authorization":"Bearer "+self.token.token,"Content-Type":"application/json"},
                allow_redirects=False,stream=True,timeout=(5,20))
            try:
                _require(type(response.status_code) is int and response.status_code == 200, "logs_response_invalid")
                chunks,count = [],0
                for chunk in response.iter_content(chunk_size=16384):
                    _require(type(chunk) is bytes, "logs_response_invalid")
                    count += len(chunk)
                    _require(count <= MAX_RESPONSE_BYTES, "logs_response_oversized")
                    chunks.append(chunk)
                value = _json(b"".join(chunks),"logs_response_invalid")
                validate_response(value,query,http_status=response.status_code)
                return value
            finally:
                response.close()
        except Exception:
            raise CatalogLogsError("logs_transport_failed") from None


def _row_scope(row, scope, code):
    _require(row["Group"] == scope.log_group and row["Container"] == scope.container_name,code)
    instant = _utc(row["LogTime"],code)
    _require(_utc(scope.start_utc) <= instant < _utc(scope.end_utc),code)


def _inventory(query, scope):
    request = CatalogQuery(scope,"inventory")
    rows = validate_response(query(request),request)
    result = []
    for row in rows:
        _row_scope(row,scope,"logs_inventory_invalid")
        _require(row["LinePrefix"] == codec.PREFIX and _SHA.fullmatch(row["RawSha256"]) is not None
            and 0 < row["RawBytes"] <= codec.MAX_LINE_BYTES, "logs_inventory_invalid")
        try:
            header = codec.parse_header(_json(row["Header"],"logs_inventory_invalid"),scope.binding)
        except Exception:
            raise CatalogLogsError("logs_inventory_invalid") from None
        result.append({**row,"Header":header})
    manifests = [row for row in result if row["Header"]["kind"] == "manifest"]
    _require(len(manifests) == 1, "logs_inventory_invalid")
    manifest = manifests[0]["Header"]
    count = manifest["chunk_count"]
    chunks = [row for row in result if row["Header"]["kind"] == "chunk"]
    _require(len(chunks) == count and sorted(row["Header"]["index"] for row in chunks) == list(range(count)),
        "logs_inventory_invalid")
    return result


def _row_fingerprint(row):
    return codec.canonical(row)


def _collect_evidence(query, scope):
    scope = _scope(scope)
    first = _inventory(query,scope)
    manifest = next(row["Header"] for row in first if row["Header"]["kind"] == "manifest")
    expected = {(row["Header"]["kind"],row["Header"].get("index")):row for row in first}
    observed,lines = set(),{}
    for lower in range(0,manifest["chunk_count"],RANGE_SIZE):
        request = CatalogQuery(scope,"payload",lower)
        for row in validate_response(query(request),request):
            _row_scope(row,scope,"logs_payload_invalid")
            try:
                envelope = codec.parse_line(row["Log"],scope.binding)
            except Exception:
                raise CatalogLogsError("logs_payload_invalid") from None
            index = envelope.get("index")
            key = (envelope["kind"],index)
            _require(key in expected and key not in observed and (lower == 0 if key[0] == "manifest"
                else lower <= index < lower+RANGE_SIZE), "logs_payload_invalid")
            previous = expected[key]
            raw = row["Log"].encode("utf-8")
            header = {name:value for name,value in envelope.items() if name != "data_b64"}
            _require(len(raw) == previous["RawBytes"] and hashlib.sha256(raw).hexdigest() == previous["RawSha256"]
                and header == previous["Header"] and row["LogTime"] == previous["LogTime"], "logs_payload_invalid")
            observed.add(key); lines[key] = row["Log"]
    _require(observed == set(expected), "logs_payload_invalid")
    second = _inventory(query,scope)
    _require(Counter(map(_row_fingerprint,first)) == Counter(map(_row_fingerprint,second)), "logs_inventory_drift")
    ordered = [lines[("chunk",index)] for index in range(manifest["chunk_count"])] + [lines[("manifest",None)]]
    try:
        record = codec.decode_record(ordered,scope.binding)
        inventory_sha256 = hashlib.sha256(codec.canonical(sorted(second,key=_row_fingerprint))).hexdigest()
        return {"record":record,"lines":ordered,"header_inventory_sha256":inventory_sha256}
    except Exception:
        raise CatalogLogsError("logs_record_invalid") from None


def collect_evidence(query, scope):
    """Return bounded validated frames and a stable inventory hash, not approval.

    Chunks are ordered by exact index with the one manifest last. The inventory
    digest covers the sorted exact multiset of derived headers, raw-line hashes,
    lengths, execution/container and timestamps, after both full reads agree.
    No protected retention, authentication or semantic integrity claim is made.
    """
    try:
        return _collect_evidence(query,scope)
    except CatalogLogsError:
        raise
    except Exception:
        # Query failures or exotic supplied objects must not expose provider
        # diagnostics or cause another inventory/payload attempt.
        raise CatalogLogsError("logs_record_invalid") from None


def collect_record(query, scope):
    """Compatibility projection of one collection, without repeated queries."""
    return collect_evidence(query,scope)["record"]
