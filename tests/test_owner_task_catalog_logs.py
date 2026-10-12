"""Independent synthetic Logs API/assembly tests; no live log-delivery proof."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import read_owner_task_catalog_logs as reader
from ibkr_etl import owner_codex_catalog_transport as codec


BASE = "/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/"
JOB = BASE+"Microsoft.App/jobs/ibkrv2stg-ownertrial-catalog"
CANARY = BASE+"Microsoft.App/jobs/ibkrv2stg-ownertrial-log-canary"
OPERATION = "3c9910dc-0c9f-4db6-91e0-ea39f8b32168"
TIME = "2026-10-04T14:30:00.0000000Z"
HEADER_COLUMNS = [("Group","string"),("Container","string"),("LogTime","datetime"),
    ("LinePrefix","string"),("Header","string"),("RawSha256","string"),("RawBytes","long")]
DATA_COLUMNS = [("Group","string"),("Container","string"),("LogTime","datetime"),("Log","string")]


def binding(purpose="provider"):
    return codec.Binding(purpose=purpose,profile_key="owner-codex-isolated-059-20261004-v1",
        job_resource_id=JOB if purpose == "provider" else CANARY,operation_id=OPERATION,source_sha256="a"*64)


def scope(purpose="provider"):
    group = "ibkrv2stg-ownertrial-catalog-exec-a9_replica-b7" if purpose == "provider" else "ibkrv2stg-ownertrial-log-canary-exec-a9_replica-b7"
    return reader.CatalogLogScope(binding(purpose),group,"catalog","2026-10-04T14:00:00Z","2026-10-04T15:00:00Z")


def table(rows,kind="inventory"):
    columns = HEADER_COLUMNS if kind == "inventory" else DATA_COLUMNS
    return {"tables":[{"name":"PrimaryResult","columns":[{"name":name,"type":kind} for name,kind in columns],"rows":rows}]}


def header_rows(lines,target):
    rows = []
    for line in lines:
        frame = json.loads(line[len("OWNER_TASK_CATALOG_CHUNK_V1 "):])
        del_data = {key:value for key,value in frame.items() if key != "data_b64"}
        raw = line.encode("utf-8")
        rows.append([target.log_group,target.container_name,TIME,"OWNER_TASK_CATALOG_CHUNK_V1 ",
            json.dumps(del_data,separators=(",",":")),hashlib.sha256(raw).hexdigest(),len(raw)])
    return rows


def data_rows(lines,target):
    return [[target.log_group,target.container_name,TIME,line] for line in lines]


class QueryFixture:
    def __init__(self,record=None,target=None):
        self.scope = scope() if target is None else target
        self.record = {"synthetic":True,"sql_acceptance":False,"data":"no-credentials"} if record is None else record
        self.lines = codec.encode_record(self.record,self.scope.binding)
        self.inventory = header_rows(self.lines,self.scope)
        self.inventory_after = deepcopy(self.inventory)
        self.requests = []
        self.payload_mutation = None
        self.inventory_calls = 0

    def __call__(self,query):
        self.requests.append(query)
        if query.kind == "inventory":
            self.inventory_calls += 1
            return table(deepcopy(self.inventory if self.inventory_calls == 1 else self.inventory_after))
        selected = []
        for line in self.lines:
            frame = json.loads(line[len("OWNER_TASK_CATALOG_CHUNK_V1 "):])
            if frame["kind"] == "manifest" and query.lower == 0 or frame["kind"] == "chunk" and query.lower <= frame["index"] < query.lower+128:
                selected.append(line)
        rows = data_rows(selected,self.scope)
        if self.payload_mutation:
            rows = self.payload_mutation(query,rows)
        return table(rows,"payload")


class Response:
    def __init__(self,value=None,status=200,content=None):
        self.status_code = status
        self.content = json.dumps(value).encode() if content is None else content
        self.closed = False
        self.chunk_sizes = []

    def iter_content(self,chunk_size):
        self.chunk_sizes.append(chunk_size)
        for index in range(0,len(self.content),chunk_size):
            yield self.content[index:index+chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self,responses):
        self.responses = list(responses)
        self.calls = []

    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        response = self.responses.pop(0)
        if isinstance(response,Exception):
            raise response
        return response


class Credential:
    def __init__(self):
        self.calls = []

    def get_token(self,audience):
        self.calls.append(audience)
        return SimpleNamespace(token="SYNTHETIC_TOKEN_NEVER_RETURNED",expires_on=5000)


class ScopeAndResponseTests(unittest.TestCase):
    def error(self,code,function,*args,**kwargs):
        with self.assertRaises(reader.CatalogLogsError) as caught:
            function(*args,**kwargs)
        self.assertEqual(caught.exception.code,code)
        self.assertEqual(str(caught.exception),code)

    def test_fixed_workspace_and_query_oracles_no_sampling_or_binding_filters(self):
        self.assertEqual(reader.WORKSPACE,"5264a124-10cd-45f6-93a5-6d65c37e9bfb")
        self.assertEqual(reader.ENDPOINT,"https://api.loganalytics.io/v1/workspaces/5264a124-10cd-45f6-93a5-6d65c37e9bfb/query")
        self.assertEqual(reader.AUDIENCE,"https://api.loganalytics.io/.default")
        target = scope()
        inventory = reader.CatalogQuery(target,"inventory").text
        self.assertIn("ContainerGroupName_s == '"+target.log_group+"'",inventory)
        self.assertIn("TimeGenerated >= datetime(2026-10-04T14:00:00Z)",inventory)
        self.assertIn("TimeGenerated < datetime(2026-10-04T15:00:00Z)",inventory)
        self.assertIn("Log_s startswith 'OWNER_TASK_CATALOG_CHUNK'",inventory)
        self.assertIn("bag_remove_keys(Envelope,dynamic(['data_b64']))",inventory)
        self.assertIn("RawSha256=hash_sha256(Log_s),RawBytes=string_size(Log_s)",inventory)
        for forbidden in ("| take ","| top ","| distinct ","| summarize ","ago(","startswith 'ibkr",OPERATION,"purpose ==","kind =="):
            self.assertNotIn(forbidden,inventory)
        first = reader.CatalogQuery(target,"payload",0).text
        later = reader.CatalogQuery(target,"payload",128).text
        self.assertIn("between (0 .. 127)",first)
        self.assertIn("or tostring(Envelope.kind) == 'manifest'",first)
        self.assertIn("between (128 .. 255)",later)
        self.assertNotIn("or tostring(Envelope.kind) == 'manifest'",later)

    def test_exact_scope_bounded_window_no_foreign_job_or_injection(self):
        checked = scope()
        for field,value in (("log_group","ibkrv2stg-bootstrap-a"),("log_group","ibkrv2stg-ownertrial-catalog"),
            ("log_group",checked.log_group+"';take 1"),("container_name","catalog';take 1"),
            ("start_utc","2026-10-04T14:00:00+00:00"),("start_utc",False),
            ("end_utc","2026-10-05T14:00:01Z"),("end_utc","2026-10-04T14:00:00Z"),
            ("end_utc","2026-99-04T15:00:00Z"),("binding",asdict(checked.binding))):
            args = dict(binding=checked.binding,log_group=checked.log_group,container_name=checked.container_name,
                start_utc=checked.start_utc,end_utc=checked.end_utc)
            args[field] = value
            self.error("logs_scope_invalid",reader.CatalogLogScope,**args)
        canary = scope("canary")
        self.assertIn("log-canary",reader.CatalogQuery(canary,"inventory").text)

    def test_frozen_scope_and_binding_forgery_revalidated_before_queries(self):
        for field,value in (("log_group","foreign"),("container_name","foreign'")):
            forged = scope(); object.__setattr__(forged,field,value)
            self.error("logs_scope_invalid",reader.collect_record,lambda _query:self.fail("must not query"),forged)
        forged = scope(); object.__setattr__(forged.binding,"source_sha256","not-a-hash")
        self.error("logs_scope_invalid",reader.collect_record,lambda _query:self.fail("must not query"),forged)

    def test_forged_binding_scalar_hooks_never_run_at_any_reader_boundary(self):
        calls = []
        class Hostile:
            def __deepcopy__(self,_memo):
                calls.append("deepcopy"); raise AssertionError("hook ran")
            def __eq__(self,_other):
                calls.append("equality"); raise AssertionError("hook ran")
            def __hash__(self):
                calls.append("hash"); raise AssertionError("hook ran")
        for field in ("purpose","profile_key","job_resource_id","operation_id","source_sha256"):
            with self.subTest(field=field):
                forged = scope(); object.__setattr__(forged.binding,field,Hostile())
                self.error("logs_scope_invalid",forged.__post_init__)
                self.error("logs_scope_invalid",reader.collect_evidence,lambda _query:self.fail("must not query"),forged)
                query = reader.CatalogQuery(scope(),"inventory")
                object.__setattr__(query,"scope",forged)
                session,credential = Session([]),Credential()
                transport = reader.LogsTransport(credential,session)
                self.error("logs_scope_invalid",transport,query)
                self.assertEqual(session.calls,[])
                self.assertEqual(credential.calls,[])
        self.assertEqual(calls,[])

    def test_string_character_cap_precedes_utf8_allocation(self):
        query = reader.CatalogQuery(scope(),"inventory")
        value = table(header_rows(codec.encode_record({},binding()),scope()))
        value["tables"][0]["rows"][0][4] = "\ud800"*8193
        # If encode runs before the character cap this raises UnicodeEncodeError
        # instead of the fixed response-validation error.
        self.error("logs_response_invalid",reader.validate_response,value,query)
        value["tables"][0]["rows"][0][4] = "\ud800"
        self.error("logs_response_invalid",reader.validate_response,value,query)
        value["tables"][0]["rows"][0][4] = "\u4e00"*3000
        self.error("logs_response_invalid",reader.validate_response,value,query)

    def test_query_exact_kind_and_fixed_range_rejects_bool_or_arbitrary_steps(self):
        for kind,lower in (("other",None),("inventory",0),("payload",None),("payload",True),
                           ("payload",1),("payload",-128),("payload",1024),("payload",128.0)):
            self.error("logs_query_invalid",reader.CatalogQuery,scope(),kind,lower)
        query = reader.CatalogQuery(scope(),"payload",0)
        object.__setattr__(query,"lower",1)
        self.error("logs_query_invalid",lambda:query.text)

    def test_response_preserves_duplicates_and_typed_exact_primary_result(self):
        target = scope(); query = reader.CatalogQuery(target,"inventory")
        lines = codec.encode_record({},target.binding)
        rows = header_rows(lines,target)
        value = table([rows[0],rows[0]])
        result = reader.validate_response(value,query)
        self.assertEqual(len(result),2)
        self.assertEqual(result[0],result[1])
        self.assertEqual(set(result[0]),set(name for name,_kind in HEADER_COLUMNS))

    def test_http200_errors_partial_truncation_continuations_and_extra_tables_rejected(self):
        query = reader.CatalogQuery(scope(),"inventory")
        valid = table([])
        for key,value in (("error",{}),("partialError",{}),("nextLink","https://evil.invalid"),
                           ("truncated",False),("statistics",{}),("status","success")):
            changed = deepcopy(valid); changed[key] = value
            self.error("logs_response_invalid",reader.validate_response,changed,query)
        changed = deepcopy(valid); changed["tables"].append(deepcopy(changed["tables"][0]))
        self.error("logs_response_invalid",reader.validate_response,changed,query)
        for status in (201,202,206,True,"200"):
            self.error("logs_response_invalid",reader.validate_response,valid,query,http_status=status)

    def test_table_unknown_columns_wrong_types_rows_and_limits_rejected(self):
        target = scope(); query = reader.CatalogQuery(target,"inventory")
        valid = table(header_rows(codec.encode_record({},target.binding),target))
        mutations = [lambda value:value["tables"][0].update(name="Other"),
            lambda value:value["tables"][0].update(nextLink="ignored"),
            lambda value:value["tables"][0]["columns"][0].update(type="dynamic"),
            lambda value:value["tables"][0]["columns"].append({"name":"Extra","type":"string"}),
            lambda value:value["tables"][0]["rows"][0].pop(),
            lambda value:value["tables"][0]["rows"][0].__setitem__(-1,True),
            lambda value:value["tables"][0]["rows"][0].__setitem__(0,None),
            lambda value:value["tables"][0]["rows"][0].__setitem__(4,"x"*8193),
            lambda value:value["tables"][0].update(rows=[valid["tables"][0]["rows"][0]]*1026)]
        for mutate in mutations:
            changed = deepcopy(valid); mutate(changed)
            self.error("logs_response_invalid",reader.validate_response,changed,query)


class AssemblyTests(unittest.TestCase):
    def denies(self,fixture,code=None):
        with self.assertRaises(reader.CatalogLogsError) as caught:
            reader.collect_record(fixture,fixture.scope)
        if code:
            self.assertEqual(caught.exception.code,code)
        self.assertNotIn("no-credentials",str(caught.exception))
        return caught.exception.code

    def test_complete_record_assembled_without_converting_transport_to_acceptance(self):
        fixture = QueryFixture({"synthetic":True,"sql_acceptance":False,"untrusted_claim":"not-an-approval"})
        result = reader.collect_record(fixture,fixture.scope)
        self.assertEqual(result,fixture.record)
        self.assertIs(result["sql_acceptance"],False)
        self.assertEqual([query.kind for query in fixture.requests],["inventory","payload","inventory"])

    def test_large_record_uses_fixed128_ranges_and_complete_enclosing_inventories(self):
        fixture = QueryFixture({"synthetic":True,"data":"x"*(4096*128+100)})
        self.assertEqual(reader.collect_record(fixture,fixture.scope),fixture.record)
        self.assertEqual([(query.kind,query.lower) for query in fixture.requests],
            [("inventory",None),("payload",0),("payload",128),("inventory",None)])

    def test_row_order_can_change_but_multiplicity_is_never_removed(self):
        fixture = QueryFixture(); fixture.inventory_after.reverse()
        fixture.payload_mutation = lambda _query,rows:list(reversed(rows))
        self.assertEqual(reader.collect_record(fixture,fixture.scope),fixture.record)
        for item in (0,-1):
            fixture = QueryFixture(); fixture.inventory.append(deepcopy(fixture.inventory[item]))
            self.denies(fixture,"logs_inventory_invalid")

    def test_evidence_retains_exact_canonical_order_and_independent_inventory_hash(self):
        fixture = QueryFixture({"data":"x"*(4096*129),"synthetic":True})
        fixture.inventory.reverse(); fixture.inventory_after.reverse()
        fixture.payload_mutation = lambda _query,rows:list(reversed(rows))
        result = reader.collect_evidence(fixture,fixture.scope)
        self.assertEqual(set(result),{"record","lines","header_inventory_sha256"})
        self.assertEqual(result["record"],fixture.record)
        self.assertEqual(result["lines"],fixture.lines)
        header_dicts = [dict(zip((name for name,_kind in HEADER_COLUMNS),row)) for row in fixture.inventory_after]
        for row in header_dicts:
            row["Header"] = json.loads(row["Header"])
        canonical = lambda value:json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode("ascii")
        expected_sha = hashlib.sha256(canonical(sorted(header_dicts,key=canonical))).hexdigest()
        self.assertEqual(result["header_inventory_sha256"],expected_sha)
        self.assertEqual([query.kind for query in fixture.requests],["inventory","payload","payload","inventory"])

    def test_evidence_hash_is_order_invariant_but_bound_to_actual_inventory(self):
        first = QueryFixture(); second = QueryFixture()
        second.inventory.reverse(); second.inventory_after.reverse()
        result1 = reader.collect_evidence(first,first.scope)
        result2 = reader.collect_evidence(second,second.scope)
        self.assertEqual(result1["header_inventory_sha256"],result2["header_inventory_sha256"])
        changed = QueryFixture()
        for row in changed.inventory+changed.inventory_after:
            row[2] = "2026-10-04T14:31:00Z"
        def change_time(_query,rows):
            for row in rows:
                row[2] = "2026-10-04T14:31:00Z"
            return rows
        changed.payload_mutation = change_time
        result3 = reader.collect_evidence(changed,changed.scope)
        self.assertNotEqual(result1["header_inventory_sha256"],result3["header_inventory_sha256"])

    def test_evidence_is_withheld_on_missing_frames_or_changed_second_inventory(self):
        for missing in (True,False):
            fixture = QueryFixture()
            if missing:
                fixture.payload_mutation = lambda _query,rows:rows[:-1]
            else:
                fixture.inventory_after[0][5] = "b"*64
            with self.assertRaises(reader.CatalogLogsError):
                reader.collect_evidence(fixture,fixture.scope)

    def test_missing_manifest_chunk_or_index_and_extra_unknown_headers_deny_before_payload(self):
        for mutate in (lambda rows:rows.pop(0),lambda rows:rows.pop(-1),
            lambda rows:rows[0].__setitem__(4,rows[0][4].replace('"index":0','"index":1')),
            lambda rows:rows[0].__setitem__(4,rows[0][4].replace('"kind":"chunk"','"kind":"unknown"'))):
            fixture = QueryFixture(); mutate(fixture.inventory)
            self.denies(fixture,"logs_inventory_invalid")
            self.assertEqual([query.kind for query in fixture.requests],["inventory"])

    def test_header_unknown_purpose_wrong_binding_duplicate_json_and_extra_fields_deny(self):
        for change in (lambda header:header.update(purpose="unknown"),lambda header:header.update(purpose="canary"),
            lambda header:header.update(operation_id="11111111-1111-4111-8111-111111111111"),
            lambda header:header.update(source_sha256="b"*64),lambda header:header.update(extra=True),
            lambda header:header.update(data_b64="unexpected")):
            fixture = QueryFixture(); header = json.loads(fixture.inventory[0][4]); change(header)
            fixture.inventory[0][4] = json.dumps(header)
            self.denies(fixture,"logs_inventory_invalid")
        fixture = QueryFixture()
        fixture.inventory[0][4] = fixture.inventory[0][4].replace('"kind":"chunk"','"kind":"chunk","kind":"chunk"')
        self.denies(fixture,"logs_inventory_invalid")

    def test_header_prefix_hash_length_group_container_and_time_bounds_deny(self):
        for index,value in ((0,"foreign-group"),(1,"foreign-container"),(2,"2026-10-04T15:00:00Z"),
                           (2,"2026-10-04T13:59:59Z"),(2,"bad-date"),(3,"OWNER_TASK_CATALOG_CHUNK_V2 "),
                           (3,"OWNER_TASK_CATALOG_CHUNK_V1 extra"),(5,"A"*64),(6,8193)):
            fixture = QueryFixture(); fixture.inventory[0][index] = value
            self.denies(fixture,"logs_inventory_invalid")

    def test_payload_missing_duplicate_extra_and_wrongrange_deny(self):
        for change in (lambda _query,rows:rows[:-1],lambda _query,rows:rows+[deepcopy(rows[0])],
                       lambda _query,rows:rows+data_rows(codec.encode_record({"other":"record"},binding()),scope())):
            fixture = QueryFixture(); fixture.payload_mutation = change
            self.denies(fixture,"logs_payload_invalid")
        fixture = QueryFixture({"data":"x"*(4096*129)})
        fixture.payload_mutation = lambda query,rows:rows+data_rows([fixture.lines[128]],fixture.scope) if query.lower == 0 else rows
        self.denies(fixture,"logs_payload_invalid")

    def test_payload_header_rawbytes_hash_timestamp_group_and_container_deny(self):
        changes = [(0,"foreign"),(1,"foreign"),(2,"2026-10-04T14:31:00Z"),(3,"not-a-frame")]
        for index,value in changes:
            fixture = QueryFixture()
            def mutate(_query,rows,index=index,value=value):
                rows[0][index] = value
                return rows
            fixture.payload_mutation = mutate
            self.denies(fixture,"logs_payload_invalid")
        for index,value in ((5,"b"*64),(6,20)):
            fixture = QueryFixture(); fixture.inventory[0][index] = value
            self.denies(fixture,"logs_payload_invalid")
        fixture = QueryFixture(); changed = json.loads(fixture.inventory[0][4]); changed["sha256"] = "b"*64
        fixture.inventory[0][4] = json.dumps(changed)
        self.denies(fixture,"logs_payload_invalid")

    def test_second_complete_inventory_detects_drift_and_never_qualifies_partial_stream(self):
        fixture = QueryFixture(); fixture.inventory_after[0][5] = "b"*64
        self.denies(fixture,"logs_inventory_drift")
        fixture = QueryFixture(); fixture.inventory_after.append(deepcopy(fixture.inventory_after[0]))
        self.denies(fixture,"logs_inventory_invalid")
        fixture = QueryFixture()
        original = fixture.__call__
        def partial(query):
            result = original(query)
            if fixture.inventory_calls == 2:
                result["error"] = {"code":"PartialError"}
            return result
        with self.assertRaises(reader.CatalogLogsError):
            reader.collect_record(partial,fixture.scope)

    def test_complete_but_falsely_bound_manifest_hash_is_transport_failure(self):
        fixture = QueryFixture()
        manifest = json.loads(fixture.lines[-1][len(codec.PREFIX):]); manifest["record_sha256"] = "b"*64
        fixture.lines[-1] = codec.PREFIX+codec.canonical(manifest).decode("ascii")
        fixture.inventory = header_rows(fixture.lines,fixture.scope)
        fixture.inventory_after = deepcopy(fixture.inventory)
        self.denies(fixture,"logs_record_invalid")

    def test_injected_query_failure_is_sanitized_without_retry(self):
        calls = []
        def failed(query):
            calls.append(query)
            raise RuntimeError("SYNTHETIC_PROVIDER_CREDENTIAL")
        with self.assertRaises(reader.CatalogLogsError) as caught:
            reader.collect_record(failed,scope())
        self.assertEqual(str(caught.exception),"logs_record_invalid")
        self.assertEqual(len(calls),1)


class TransportTests(unittest.TestCase):
    def valid(self):
        return table(header_rows(codec.encode_record({},binding()),scope()))

    def test_only_fixed_post_audience_timewindow_timeout_redirect_stream_and_cache(self):
        responses = [Response(self.valid()),Response(self.valid())]
        session,credential = Session(responses),Credential()
        transport = reader.LogsTransport(credential,session)
        query = reader.CatalogQuery(scope(),"inventory")
        with patch.object(reader.time,"time",return_value=1000):
            self.assertEqual(transport(query),self.valid())
            transport(query)
        self.assertEqual(credential.calls,["https://api.loganalytics.io/.default"])
        for args,kwargs in session.calls:
            self.assertEqual(args,("POST","https://api.loganalytics.io/v1/workspaces/5264a124-10cd-45f6-93a5-6d65c37e9bfb/query"))
            self.assertEqual(kwargs["timeout"],(5,20))
            self.assertIs(kwargs["allow_redirects"],False)
            self.assertIs(kwargs["stream"],True)
            self.assertEqual(kwargs["json"],{"query":query.text,"timespan":"2026-10-04T14:00:00Z/2026-10-04T15:00:00Z"})
        self.assertTrue(all(response.closed for response in responses))
        self.assertTrue(all(response.chunk_sizes == [16384] for response in responses))

    def test_default_session_configures_zero_http_retries_without_opening_request(self):
        fake = SimpleNamespace(mount=unittest.mock.Mock(),request=unittest.mock.Mock())
        with patch.object(reader.requests,"Session",return_value=fake):
            reader.LogsTransport(Credential())
        fake.request.assert_not_called()
        args = fake.mount.call_args.args
        self.assertEqual(args[0],"https://")
        self.assertEqual(args[1].max_retries.total,0)

    def test_token_expiry_refreshes_but_never_returns_token(self):
        session,credential = Session([Response(self.valid()),Response(self.valid())]),Credential()
        transport = reader.LogsTransport(credential,session); query = reader.CatalogQuery(scope(),"inventory")
        with patch.object(reader.time,"time",return_value=1000):
            result = transport(query)
            transport.token.expires_on = 1050
            transport(query)
        self.assertEqual(len(credential.calls),2)
        self.assertNotIn("SYNTHETIC_TOKEN",json.dumps(result))

    def test_malformed_query_cannot_trigger_token_or_request(self):
        session,credential = Session([]),Credential(); transport = reader.LogsTransport(credential,session)
        for value in ("ContainerAppConsoleLogs_CL",{},None):
            with self.assertRaises(reader.CatalogLogsError):
                transport(value)
        forged = reader.CatalogQuery(scope(),"payload",0); object.__setattr__(forged,"lower",1)
        with self.assertRaises(reader.CatalogLogsError):
            transport(forged)
        self.assertEqual(session.calls,[]); self.assertEqual(credential.calls,[])

    def test_redirect_partial_timeout_truncation_duplicate_json_and_oversize_no_retry(self):
        query = reader.CatalogQuery(scope(),"inventory")
        malformed = deepcopy(self.valid()); malformed["error"] = {"code":"PartialError"}
        cases = (Response(self.valid(),status=302),Response(self.valid(),status=206),Response(malformed),
            Response(content=b'{"tables":[],"tables":[]}'),Response(content=b"{") ,
            Response(content=b"x"*(2*1024*1024+1)),RuntimeError("SYNTHETIC_PROVIDER_SECRET"))
        for response in cases:
            with self.subTest(response=type(response).__name__):
                session = Session([response]); transport = reader.LogsTransport(Credential(),session)
                with self.assertRaises(reader.CatalogLogsError) as caught:
                    transport(query)
                self.assertEqual(str(caught.exception),"logs_transport_failed")
                self.assertEqual(len(session.calls),1)
                if isinstance(response,Response):
                    self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
