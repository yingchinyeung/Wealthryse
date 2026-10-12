"""Independent OFFLINE canary controller tests; auth/cloud are mocked."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
import hashlib
import base64
import gzip
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import qualify_owner_task_catalog_canary as control
from ibkr_etl import owner_codex_catalog_transport as codec


BASE = "/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/"
PATH = BASE+"Microsoft.App/jobs/ibkrv2stg-ownertrial-log-canary"
JOB = "ibkrv2stg-ownertrial-log-canary"
CONTAINER = "catalog-log-canary"
IMAGE = "python:3.12-slim-bookworm@sha256:9901e0a8d75037d8242ed43155cbcb2d1f61be1356383d8054afb59fd50e39c4"
OPERATION = "b86898e4-09db-4e79-92e3-114b2ebc209e"
ENVIRONMENT = BASE+"Microsoft.App/managedEnvironments/ibkrv2stg-env"
EXECUTION = JOB+"-abcdefg"
REPLICA = EXECUTION+"-abcde"
SOURCES = {"synthetic_frozen_source.py":"a"*64}
RUNTIME = {"python":"3.12.10","versions":{"requests":"2.32.5"},"requirements_lock_sha256":"b"*64}
BASELINE = {"job_inventory_complete":True,"other_jobs":{f"existing-{index:02d}":{"image":"untouched"} for index in range(34)}}
ANCHOR = {"sha256":"e8e103df668996a00fe8d07a9e45f71f23078c358d6586f97609817d27f92589",
    "version_id":"2026-10-04T11:45:48.1413402Z","blob":"synthetic/historical-host"}
ORIGINAL_BUNDLE = control.bundle
ORIGINAL_BASELINE = control.baseline


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def binding():
    return codec.Binding("canary","owner-codex-isolated-059-20261004-v1",PATH,OPERATION,"a"*64)


def definition():
    return {"location":"eastasia","identity":{"type":"None"},
        "tags":{"purpose":"owner-catalog-log-canary","operation":OPERATION,"sourceSha256":"a"*64,
            "workerSha256":"c"*64,"profile":"owner-codex-isolated-059-20261004-v1"},
        "properties":{"environmentId":ENVIRONMENT,
            "configuration":{"triggerType":"Manual","replicaTimeout":600,"replicaRetryLimit":0,
                "manualTriggerConfig":{"parallelism":1,"replicaCompletionCount":1},"secrets":[],"registries":[]},
            "template":{"containers":[{"name":CONTAINER,"image":IMAGE,"command":["python"],
                "args":["-I","-S","-B","-c","SYNTHETIC_INERT_PAYLOAD"],"env":[],"resources":{"cpu":0.5,"memory":"1Gi"}}]}}}


def job():
    result = definition(); result.update(id=PATH,name=JOB)
    result["properties"]["provisioningState"] = "Succeeded"
    return result


def execution(status="Running"):
    return {"id":PATH+"/executions/"+EXECUTION,"name":EXECUTION,
        "properties":{"status":status,"startTime":"2026-10-04T14:00:00Z",
            "endTime":"2026-10-04T14:03:05Z" if status == "Succeeded" else None,
            "template":deepcopy(definition()["properties"]["template"])}}


def replicas():
    return {"replicas":[{"name":REPLICA,"properties":{"containers":[{"name":CONTAINER,"image":IMAGE}]}}]}


class Evidence:
    """Independent synthetic receipts, never cloud protection acceptance."""
    def __init__(self):
        self.records = {}
        self.checks = []

    def save(self,value,name):
        if name in self.records:
            raise ValueError("synthetic_no_overwrite")
        raw = deepcopy(value)
        raw["protected_receipt"] = {"kind":"catalog-canary-"+name.removesuffix(".json"),
            "blob":"synthetic/"+name,"sha256":digest(value)}
        self.records[name] = deepcopy(raw)
        return raw

    def protected(self,raw,kind):
        self.checks.append(kind)
        value = {name:item for name,item in raw.items() if name != "protected_receipt"}
        receipt = raw.get("protected_receipt")
        if receipt != {"kind":kind,"blob":"synthetic/"+kind.removeprefix("catalog-canary-")+".json","sha256":digest(value)}:
            raise ValueError("synthetic_protected_digest_mismatch")
        return deepcopy(value)

    def load(self,name):
        raw = deepcopy(self.records[name])
        return raw,self.protected(raw,"catalog-canary-"+name.removesuffix(".json"))

    def rebind(self,name):
        old = self.records.pop(name)
        return self.save({key:value for key,value in old.items() if key != "protected_receipt"},name)


class MemoryPath:
    def __init__(self,evidence,name=None):
        self.evidence,self.name = evidence,name

    def __truediv__(self,name):
        return MemoryPath(self.evidence,name)

    def exists(self):
        return self.name in self.evidence.records


class Client:
    def __init__(self):
        self.calls = []
        self.job,self.actual,self.runs,self.replica = None,None,{"value":[]},replicas()
        self.put_fault,self.post_fault = None,None
        self.execution_reads = 0
        self.execution_after = None

    def call(self,method,path,*,body=None,missing=False):
        self.calls.append((method,path))
        if method == "PUT" and path == PATH+"?api-version=2024-03-01":
            self.job = job()
            if self.put_fault:
                return self.put_fault(body)
            return {},201
        if method == "POST" and path == PATH+"/start?api-version=2024-03-01":
            self.actual = execution(); self.runs = {"value":[{"name":EXECUTION}]}
            if self.post_fault:
                return self.post_fault(body)
            return {"id":PATH+"/executions/"+EXECUTION,"name":EXECUTION},202
        if method != "GET" or body is not None:
            raise AssertionError("unregistered fake write")
        if path == ENVIRONMENT+"?api-version=2024-03-01":
            return {"id":ENVIRONMENT,"properties":{"appLogsConfiguration":{"destination":"log-analytics",
                "logAnalyticsConfiguration":{"customerId":"5264a124-10cd-45f6-93a5-6d65c37e9bfb"}}}},200
        if path == PATH+"?api-version=2024-03-01":
            return deepcopy(self.job),200 if self.job is not None else 404
        if path == PATH+"/executions?api-version=2024-03-01":
            return deepcopy(self.runs),200
        if path == PATH+"/executions/"+EXECUTION+"?api-version=2024-03-01":
            self.execution_reads += 1
            return deepcopy(self.execution_after if self.execution_after and self.execution_reads >= 2 else self.actual),200
        if path == PATH+"/executions/"+EXECUTION+"/replicas?api-version=2023-11-02-preview":
            return deepcopy(self.replica),200
        raise AssertionError("unregistered fake read")

    def writes(self):
        return [entry for entry in self.calls if entry[0] != "GET"]


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Real codec at its exact 4 MiB boundary, generated once. All values are
        # synthetic; no provider record, secret or actual execution is reused.
        cls.full_record = control.worker.synthetic_record(binding())
        cls.full_lines = codec.encode_record(cls.full_record,binding())

    def setUp(self):
        self.evidence,self.client = Evidence(),Client()
        self.reservations = {}
        self.reserve_failure = None
        self.after_reserve = None
        self.scopes = []
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(control,"FOLDER",MemoryPath(self.evidence)))
        self.stack.enter_context(patch.object(control.deploy,"FOLDER",MemoryPath(self.evidence)))
        self.stack.enter_context(patch.object(control,"binding",side_effect=binding))
        self.stack.enter_context(patch.object(control,"bundle",return_value=(["-I","-S","-B","-c","SYNTHETIC_INERT_PAYLOAD"],"c"*64)))
        self.source = self.stack.enter_context(patch.object(control,"sources",return_value=deepcopy(SOURCES)))
        self.baseline = self.stack.enter_context(patch.object(control,"baseline",return_value=deepcopy(BASELINE)))
        self.runtime = self.stack.enter_context(patch.object(control.host,"runtime_descriptor",return_value=deepcopy(RUNTIME)))
        self.stack.enter_context(patch.object(control,"historical_host",return_value=deepcopy(ANCHOR)))
        self.stack.enter_context(patch.object(control,"save",side_effect=self.evidence.save))
        self.stack.enter_context(patch.object(control,"load",side_effect=self.evidence.load))
        self.stack.enter_context(patch.object(control.deploy,"protected",side_effect=self.evidence.protected))
        self.stack.enter_context(patch.object(control,"reserve",side_effect=self.reserve))
        self.stack.enter_context(patch.object(control,"reservation_readback",side_effect=self.readback))
        self.stack.enter_context(patch.object(control.worker,"synthetic_record",side_effect=lambda _binding:dict(self.full_record)))
        self.stack.enter_context(patch.object(control.logs,"collect_evidence",side_effect=self.collect))
        self.prepared = {"status":"prepared_no_secret_catalog_canary","sources":deepcopy(SOURCES),"runtime":deepcopy(RUNTIME),
            "definition":definition(),"binding":asdict(binding()),"registration_sha256":control.TARGET.registration_sha256,
            "baseline":deepcopy(BASELINE),"environment_logs":{"environment_id":ENVIRONMENT,"destination":"log-analytics",
                "workspace":"5264a124-10cd-45f6-93a5-6d65c37e9bfb"},"historical_host_receipt":deepcopy(ANCHOR)}
        self.prepared_raw = self.evidence.save(self.prepared,"preflight.json")

    def reserve(self,stage,value):
        if stage in self.reservations:
            raise ValueError("synthetic_conditional_create_lost")
        payload = canonical(value); self.reservations[stage] = payload
        if self.reserve_failure:
            raise RuntimeError("synthetic_lost_reservation_ack")
        if self.after_reserve:
            self.after_reserve(stage)
        return {"blob":"probes/owner-codex-catalog-canary-20261004/"+stage+"-reservation.json",
            "sha256":hashlib.sha256(payload).hexdigest(),"version_id":"2026-10-04T13:00:00.1234567Z",
            "etag":'"0xABC123"',"protected_readback_verified":True}

    def readback(self,stage,receipt,payload):
        expected = {"blob":"probes/owner-codex-catalog-canary-20261004/"+stage+"-reservation.json",
            "sha256":hashlib.sha256(payload).hexdigest(),"version_id":"2026-10-04T13:00:00.1234567Z",
            "etag":'"0xABC123"',"protected_readback_verified":True}
        if receipt != expected or type(receipt.get("protected_readback_verified")) is not bool or self.reservations.get(stage) != payload:
            raise ValueError("synthetic_reservation_binding_invalid")

    def collect(self,query,target):
        self.scopes.append(target)
        return query(target)

    def query(self,_scope):
        return {"record":dict(self.full_record),"lines":list(self.full_lines),"header_inventory_sha256":"d"*64}

    def install(self):
        return control.install(self.prepared_raw,self.prepared,self.client)

    def start(self):
        self.install()
        return control.start(self.prepared_raw,self.prepared,self.client)

    def capture(self):
        self.start()
        return control.capture(self.prepared_raw,self.prepared,self.client)

    def verify(self,query=None):
        return control.verify(self.prepared_raw,self.prepared,self.client,self.query if query is None else query)

    def denied(self,code,function,*args):
        with self.assertRaisesRegex(ValueError,"^"+code+"$"):
            function(*args)

    def test_exact_inert_definition_no_identity_secret_credential_or_schedule(self):
        self.assertEqual(control.definition(),definition())
        value = control.definition()
        self.assertEqual(value["identity"],{"type":"None"})
        self.assertEqual(value["properties"]["configuration"]["secrets"],[])
        self.assertEqual(value["properties"]["configuration"]["registries"],[])
        self.assertEqual(value["properties"]["configuration"]["replicaRetryLimit"],0)
        self.assertNotIn("scheduleTriggerConfig",value["properties"]["configuration"])
        self.assertEqual(value["properties"]["template"]["containers"][0]["env"],[])
        self.assertEqual(value["properties"]["template"]["containers"][0]["image"],IMAGE)

    def test_exact_get_regionalias_is_metadata_only_not_request_retargeting(self):
        expected=control.validate_job(job())
        value=job(); value["location"]="East Asia"
        self.assertEqual(control.validate_job(value),expected)
        self.assertEqual(control.definition()["location"],"eastasia")
        for changed in ("EastAsia","east asia","East Asia ","EASTASIA","westus",True,None):
            value=job(); value["location"]=changed
            with self.assertRaises(ValueError):control.validate_job(value)

    def test_get_regionalias_never_weakens_source_identity_or_prepared_request(self):
        value=job(); value["location"]="East Asia"; value["id"]=PATH+"-foreign"
        with self.assertRaises(ValueError):control.validate_job(value)
        bad=deepcopy(self.prepared); bad["definition"]["location"]="East Asia"
        with self.assertRaises(ValueError):control.install(self.prepared_raw,bad,self.client)
        self.assertEqual(self.client.writes(),[])
        self.assertEqual(set(self.evidence.records),{"preflight.json"})

    def test_exact_empty_ephemeral_storage_is_readback_only_for_job_and_execution(self):
        expected_job=control.validate_job(job())
        expected_execution=control.validate_execution(execution(),EXECUTION)
        desired=deepcopy(definition()["properties"]["template"])
        observed=deepcopy(desired)
        observed["containers"][0]["resources"]["ephemeralStorage"]=""
        original=deepcopy(observed)
        control.validate_template(observed,desired)
        self.assertEqual(observed,original)
        value=job(); value["properties"]["template"]=deepcopy(observed)
        self.assertEqual(control.validate_job(value),expected_job)
        value=execution(); value["properties"]["template"]=deepcopy(observed)
        expected_execution["actual_template_readback_sha256"]=digest(observed)
        result=control.validate_execution(value,EXECUTION)
        self.assertEqual(result,expected_execution)
        self.assertEqual(result["validated_requested_template_sha256"],digest(desired))
        self.assertNotEqual(result["actual_template_readback_sha256"],result["validated_requested_template_sha256"])
        self.assertNotIn("template_sha256",result)
        self.assertEqual(control.definition()["properties"]["template"]["containers"][0]["resources"],
            {"cpu":0.5,"memory":"1Gi"})

    def test_resource_readback_null_bool_nonempty_missing_or_unknown_fields_deny(self):
        desired=definition()["properties"]["template"]
        variants=[{"cpu":0.5,"memory":"1Gi","ephemeralStorage":value}
            for value in (None,False,True,0,0.0,"0","0Gi","1Gi"," ",[],{})]
        variants.extend(({"cpu":0.5,"memory":"1Gi","unregistered":""},
            {"cpu":0.5,"memory":"1Gi","ephemeralStorage":"","unregistered":""},
            {"cpu":0.5},{"memory":"1Gi"},{"cpu":True,"memory":"1Gi"},
            {"cpu":0.5,"memory":"1Gi "},{"cpu":1,"memory":"1Gi"},None,False,[]))
        for resources in variants:
            observed=deepcopy(desired); observed["containers"][0]["resources"]=resources
            with self.assertRaises(ValueError):control.validate_template(observed,desired)
            value=job(); value["properties"]["template"]=deepcopy(observed)
            with self.assertRaises(ValueError):control.validate_job(value)
            value=execution(); value["properties"]["template"]=deepcopy(observed)
            with self.assertRaises(ValueError):control.validate_execution(value,EXECUTION)

    def test_real_local_bundle_only_packages_pure_sources_and_never_executes_worker(self):
        args,hash_value=ORIGINAL_BUNDLE()
        self.assertEqual(args[:4],["-I","-S","-B","-c"])
        encoded=args[4].split("base64.b64decode('",1)[1].split("')",1)[0]
        source=gzip.decompress(base64.b64decode(encoded)).decode()
        self.assertEqual(hash_value,hashlib.sha256(source.encode()).hexdigest())
        self.assertIn("w.main(",source)
        for forbidden in ("AzureCliCredential","BlobServiceClient","psycopg","os.environ","requests.Session"):
            self.assertNotIn(forbidden,source)
        self.assertLess(len(canonical(args)),65536)

    def test_complete_baseline_excludes_only_new_canary_and_environment_is_exact(self):
        observed=deepcopy(BASELINE); observed["other_jobs"][JOB]={"new_canary":True}
        with patch.object(control.deploy,"baseline",return_value=observed):
            self.assertEqual(ORIGINAL_BASELINE(),BASELINE)
        for observed in ({"job_inventory_complete":1,"other_jobs":deepcopy(BASELINE["other_jobs"])},
                         {"job_inventory_complete":True,"other_jobs":{**BASELINE["other_jobs"],"foreign":{"x":1}}}):
            with patch.object(control.deploy,"baseline",return_value=observed):
                with self.assertRaises(ValueError):ORIGINAL_BASELINE()
        for environment,workspace in ((ENVIRONMENT+"-other","5264a124-10cd-45f6-93a5-6d65c37e9bfb"),(ENVIRONMENT,"foreign")):
            client=SimpleNamespace(call=lambda *_args,environment=environment,workspace=workspace,**_kwargs:(
                {"id":environment,"properties":{"appLogsConfiguration":{"destination":"log-analytics",
                    "logAnalyticsConfiguration":{"customerId":workspace}}}},200))
            self.denied("canary_registered_log_environment_required",control.environment_logs,client)

    def test_install_start_one_shot_exact_chain_and_two_fixed_writes(self):
        self.start()
        self.assertEqual(self.client.writes(),[("PUT",PATH+"?api-version=2024-03-01"),("POST",PATH+"/start?api-version=2024-03-01")])
        for stage in ("install","start"):
            intent_raw,intent = self.evidence.load(stage+"-intent.json")
            _ack_raw,ack = self.evidence.load(stage+"-ack.json")
            self.assertEqual(intent["preflight_receipt"],self.prepared_raw["protected_receipt"])
            self.assertEqual(ack["intent_receipt"],intent_raw["protected_receipt"])
            self.assertIs(intent["automatic_retry"],False)
            self.assertIs(intent["credentials_supplied"],False)
            self.assertEqual(intent["sql_calls"],0)
            self.readback(stage,ack["reservation"],canonical(intent))
        calls = len(self.client.calls)
        self.denied("canary_attempt_consumed_inspect_no_redispatch",control.start,self.prepared_raw,self.prepared,self.client)
        self.assertEqual(len(self.client.calls),calls)

    def test_install_existing_target_absent_and_frozen_source_baseline_required(self):
        self.client.job = job()
        self.denied("canary_new_job_must_be_absent",self.install)
        self.assertEqual(self.client.writes(),[])
        self.assertEqual(set(self.evidence.records),{"preflight.json"})
        self.client.job = None
        for mock,value in ((self.source,{"changed":"b"*64}),(self.baseline,{"job_inventory_complete":False}),
                           (self.runtime,{"python":"changed"})):
            old = mock.return_value; mock.return_value = value
            code="canary_correction_source_inventory_changed" if mock is self.source else "canary_protected_preflight_or_current_guard_differs"
            self.denied(code,self.install)
            mock.return_value = old
            self.assertEqual(self.client.writes(),[])
            self.assertEqual(set(self.evidence.records),{"preflight.json"})

    def test_source_drift_after_reservation_and_unknown_ack_consume_no_retry(self):
        self.after_reserve = lambda _stage:setattr(self.source,"return_value",{"changed":"b"*64})
        self.denied("canary_correction_source_inventory_changed",self.install)
        self.assertEqual(self.client.writes(),[])
        self.denied("canary_attempt_consumed_inspect_no_redispatch",self.install)

    def test_lost_reservation_ack_never_puts_or_reissues(self):
        self.reserve_failure = True
        with self.assertRaisesRegex(RuntimeError,"synthetic_lost_reservation_ack"):
            self.install()
        self.assertIn("install",self.reservations)
        self.assertEqual(self.client.writes(),[])
        self.denied("canary_attempt_consumed_inspect_no_redispatch",self.install)

    def test_lost_install_ack_is_never_retried(self):
        def failure(_body):
            raise RuntimeError("synthetic_lost_http_ack")
        self.client.put_fault = failure
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertEqual(len(self.client.writes()),1)
        self.denied("canary_attempt_consumed_inspect_no_redispatch",self.install)

    def test_lost_start_ack_is_never_retried(self):
        def failure(_body):
            raise RuntimeError("synthetic_lost_http_ack")
        self.install(); self.client.post_fault=failure
        with self.assertRaises(RuntimeError):
            control.start(self.prepared_raw,self.prepared,self.client)
        writes = len(self.client.writes())
        self.denied("canary_attempt_consumed_inspect_no_redispatch",control.start,self.prepared_raw,self.prepared,self.client)
        self.assertEqual(len(self.client.writes()),writes)

    def test_start_requires_complete_empty_inventory_and_exact_ack_identity(self):
        self.install()
        for runs in ({"value":[{"name":"other"}]},{"value":[],"nextLink":"more"},{"value":{}}):
            self.client.runs = runs
            self.denied("canary_complete_empty_execution_inventory_required",control.start,self.prepared_raw,self.prepared,self.client)
            self.assertNotIn("start-intent.json",self.evidence.records)
        self.client.runs = {"value":[]}
        self.client.post_fault = lambda _body:({"name":EXECUTION,"id":PATH+"/executions/foreign"},202)
        self.denied("canary_start_ack_identity_invalid_inspect_only",control.start,self.prepared_raw,self.prepared,self.client)
        self.denied("canary_attempt_consumed_inspect_no_redispatch",control.start,self.prepared_raw,self.prepared,self.client)

    def test_protected_ack_digest_or_reservation_payload_mutation_is_rejected(self):
        self.install()
        self.evidence.records["install-ack.json"]["http_status"] = 202
        self.denied("synthetic_protected_digest_mismatch",control.install_acceptance,self.prepared_raw,self.prepared,self.client)
        self.evidence.rebind("install-ack.json")
        self.reservations["install"] = b"changed"
        self.denied("synthetic_reservation_binding_invalid",control.install_acceptance,self.prepared_raw,self.prepared,self.client)

    def test_reprotected_unknown_intent_flags_and_bool_config_aliases_are_rejected(self):
        self.install()
        for field,value in (("automatic_retry",True),("credentials_supplied",True),("sql_calls",1),("extra",True)):
            original = deepcopy(self.evidence.records["install-intent.json"])
            self.evidence.records["install-intent.json"][field] = value
            raw = self.evidence.rebind("install-intent.json")
            self.evidence.records["install-ack.json"]["intent_receipt"] = raw["protected_receipt"]
            self.evidence.rebind("install-ack.json")
            self.reservations["install"] = canonical({key:item for key,item in raw.items() if key != "protected_receipt"})
            with self.assertRaises(ValueError):
                control.install_acceptance(self.prepared_raw,self.prepared,self.client)
            self.evidence.records["install-intent.json"] = original
        for field,value in (("replicaRetryLimit",False),("manualTriggerConfig",{"parallelism":True,"replicaCompletionCount":1})):
            changed = job(); changed["properties"]["configuration"][field] = value
            with self.assertRaises(ValueError):
                control.validate_job(changed)

    def test_job_template_identity_secret_volume_schedule_and_foreign_image_denied(self):
        changes = [lambda value:value.update(id=PATH+"-other"),
            lambda value:value.update(identity={"type":"UserAssigned","userAssignedIdentities":{"foreign":{}}}),
            lambda value:value["properties"]["configuration"].update(secrets=[{"name":"secret"}]),
            lambda value:value["properties"]["configuration"].update(registries=[{"server":"foreign"}]),
            lambda value:value["properties"]["configuration"].update(scheduleTriggerConfig={"cronExpression":"* * * * *"}),
            lambda value:value["properties"]["template"].update(volumes=[{"name":"mount"}]),
            lambda value:value["properties"]["template"]["containers"][0].update(image="foreign"),
            lambda value:value["properties"]["template"]["containers"][0].update(env=[{"name":"SECRET","secretRef":"foreign"}])]
        for change in changes:
            value = job(); change(value)
            with self.assertRaises(ValueError):
                control.validate_job(value)

    def test_actual_running_replica_capture_is_two_execution_reads_and_no_dispatch(self):
        raw = self.capture()
        value = self.evidence.protected(raw,"catalog-canary-replica-binding")
        self.assertEqual(value["mapping"],{"log_group":REPLICA,"container_name":CONTAINER})
        self.assertEqual(value["start_utc"],"2026-10-04T13:58:00Z")
        self.assertEqual(value["end_utc"],"2026-10-04T14:20:00Z")
        self.assertIs(value["platform_replacement_excluded"],False)
        self.assertIs(value["provider_sql_authority"],False)
        self.assertEqual(self.client.execution_reads,2)
        self.assertEqual(len(self.client.writes()),2)

    def test_capture_finished_failed_foreign_partial_replica_and_execution_drift_denied(self):
        self.start()
        for state in ("Succeeded","Failed","Stopped"):
            self.client.actual = execution(state)
            self.denied("canary_live_replica_capture_required_no_redispatch",control.capture,self.prepared_raw,self.prepared,self.client)
        self.client.actual = execution()
        for replica in ({"replicas":[]},{"replicas":replicas()["replicas"]*2},
            {"replicas":replicas()["replicas"],"nextLink":"more"},
            {"replicas":[{"name":"foreign","properties":{"containers":[{"name":CONTAINER}]}}]},
            {"replicas":[{"name":REPLICA,"properties":{"containers":[{"name":"foreign"}]}}]}):
            self.client.replica = replica
            with self.assertRaises(ValueError):
                control.capture(self.prepared_raw,self.prepared,self.client)
        self.client.replica=replicas(); self.client.execution_reads=0; self.client.execution_after=execution("Succeeded")
        self.denied("canary_execution_changed_during_replica_capture",control.capture,self.prepared_raw,self.prepared,self.client)
        self.assertNotIn("replica-binding.json",self.evidence.records)

    def test_actual_resource_readback_hash_is_captured_and_rechecked_without_normalizing_drift(self):
        self.start()
        self.client.actual["properties"]["template"]["containers"][0]["resources"]["ephemeralStorage"]=""
        self.client.execution_reads=0
        self.client.execution_after=execution()
        self.denied("canary_execution_changed_during_replica_capture",control.capture,self.prepared_raw,self.prepared,self.client)
        self.assertNotIn("replica-binding.json",self.evidence.records)
        self.client.execution_after=None; self.client.execution_reads=0
        raw=control.capture(self.prepared_raw,self.prepared,self.client)
        value=self.evidence.protected(raw,"catalog-canary-replica-binding")
        captured=value["execution"]
        self.assertEqual(captured["validated_requested_template_sha256"],digest(definition()["properties"]["template"]))
        self.assertEqual(captured["actual_template_readback_sha256"],digest(self.client.actual["properties"]["template"]))
        self.assertNotEqual(captured["validated_requested_template_sha256"],captured["actual_template_readback_sha256"])
        self.client.actual["properties"]["status"]="Succeeded"
        self.client.actual["properties"]["endTime"]="2026-10-04T14:03:05Z"
        original=deepcopy(self.evidence.records["replica-binding.json"])
        for field in ("validated_requested_template_sha256","actual_template_readback_sha256"):
            self.evidence.records["replica-binding.json"]=deepcopy(original)
            self.evidence.records["replica-binding.json"]["execution"][field]="f"*64
            self.evidence.rebind("replica-binding.json")
            self.denied("canary_protected_actual_replica_binding_required",self.verify)
            self.assertEqual(self.scopes,[])
            self.assertNotIn("acceptance.json",self.evidence.records)

    def test_execution_fixed_template_name_time_and_state_required(self):
        changes = [lambda value:value.update(id=PATH+"/executions/foreign"),lambda value:value.update(name="foreign"),
            lambda value:value["properties"].update(status="Unknown"),
            lambda value:value["properties"].update(endTime="2026-10-04T14:21:00Z"),
            lambda value:value["properties"].update(startTime="2026-10-04T14:00:00+00:00"),
            lambda value:value["properties"]["template"]["containers"][0].update(args=["changed"])]
        for change in changes:
            value=execution(); change(value)
            with self.assertRaises(ValueError):
                control.validate_execution(value,EXECUTION)

    def test_full9part_retention_actual_codec_reassembly_and_fresh_readback(self):
        self.capture(); self.client.actual=execution("Succeeded")
        first = self.verify()
        raw,accepted = self.evidence.load("acceptance.json")
        self.assertEqual(first["protected_receipt"],raw["protected_receipt"])
        self.assertEqual(accepted["record_bytes"],4194304)
        self.assertEqual(accepted["chunk_count"],1024)
        self.assertEqual(accepted["frame_count"],1025)
        self.assertEqual(len(accepted["parts"]),9)
        self.assertEqual([entry["count"] for entry in accepted["parts"]],[128]*8+[1])
        assembled=[]
        for index in range(9):
            _part_raw,part=self.evidence.load(f"part-{index:03d}.json")
            assembled.extend(part["lines"])
        self.assertEqual(assembled,self.full_lines)
        self.assertEqual(codec.decode_record(assembled,binding()),self.full_record)
        for name in ("provider_sql_authority","provider_semantics_verified","fresh_restricted_login_verified",
                     "model_credential_containment_verified","owner_runtime_active","six_specialist_runtimes_active",
                     "production_migration_059_installed","ibkr_source_writes","credentials_supplied"):
            self.assertIs(accepted[name],False)
        writes=len(self.client.writes()); records=set(self.evidence.records)
        second=self.verify()
        self.assertEqual(second["status"],"independent_complete_transport_readback_verified")
        self.assertEqual(set(self.evidence.records),records)
        self.assertEqual(len(self.client.writes()),writes)
        self.assertEqual(len(self.scopes),2)
        self.assertEqual(self.scopes[0].log_group,REPLICA)

    def test_verify_no_running_capture_or_success_never_queries_or_creates_parts(self):
        self.start()
        self.denied("canary_successful_execution_required_inspect_no_redispatch",self.verify)
        self.assertEqual(self.scopes,[])
        self.client.actual=execution("Succeeded")
        with self.assertRaises(KeyError):
            self.verify()
        self.assertEqual(self.scopes,[])
        self.assertNotIn("acceptance.json",self.evidence.records)

    def test_malicious_partial_evidence_or_foreign_scope_withholds_acceptance(self):
        self.capture(); self.client.actual=execution("Succeeded")
        for evidence in ({"record":self.full_record,"lines":self.full_lines[:-1],"header_inventory_sha256":"d"*64},
            {"record":self.full_record,"lines":self.full_lines,"header_inventory_sha256":True},
            {"record":{},"lines":self.full_lines,"header_inventory_sha256":"d"*64}):
            with self.assertRaises(ValueError):
                self.verify(lambda _scope,evidence=evidence:evidence)
            self.assertNotIn("acceptance.json",self.evidence.records)
        raw=self.evidence.records["replica-binding.json"]; raw["mapping"]["log_group"]="foreign"
        self.evidence.rebind("replica-binding.json")
        self.denied("canary_replica_mapping_invalid",self.verify)
        self.assertNotIn("acceptance.json",self.evidence.records)

    def test_bool_record_semantic_alias_does_not_equal_exact_canary_bytes(self):
        record=dict(self.full_record); record["credentials_supplied"]=0; record["sql_calls"]=False
        # Balanced mutations preserve the exact4MiB length and Python equality.
        # Only exact canonical bytes distinguish these changed scalar types.
        self.assertEqual(record,self.full_record)
        self.assertEqual(len(codec.canonical(record)),4194304)
        self.assertNotEqual(codec.canonical(record),codec.canonical(self.full_record))
        lines=codec.encode_record(record,binding())
        scope=control.logs.CatalogLogScope(binding(),REPLICA,CONTAINER,"2026-10-04T13:58:00Z","2026-10-04T14:20:00Z")
        with self.assertRaises(ValueError):
            control.complete_evidence({"record":record,"lines":lines,"header_inventory_sha256":"d"*64},scope)

    def test_reprotected_part_bool_index_or_foreign_receipt_must_be_rejected(self):
        self.capture(); self.client.actual=execution("Succeeded")
        actual,_ack=control.current_execution(self.prepared_raw,self.prepared,self.client)
        replica_raw,_replica=self.evidence.load("replica-binding.json")
        parts=control.retain_parts(self.full_lines,self.prepared,actual,replica_raw)
        manifest={"parts":parts,"record_sha256":hashlib.sha256(codec.canonical(self.full_record)).hexdigest()}
        for field,value in (("index",True),("replica_receipt",{"foreign":True})):
            name="part-001.json"; original=deepcopy(self.evidence.records[name])
            self.evidence.records[name][field]=value; raw=self.evidence.rebind(name)
            changed=deepcopy(manifest); changed["parts"][1]["receipt"]=raw["protected_receipt"]
            with self.assertRaises(ValueError):
                control.verify_parts(changed,self.prepared,actual,replica_raw)
            self.evidence.records[name]=original


class CorrectionTests(unittest.TestCase):
    """Synthetic protected-loader fixture, not live old-PF authentication proof."""
    full_record={"synthetic":True}
    full_lines=[]
    reserve=ControllerTests.reserve
    readback=ControllerTests.readback
    collect=ControllerTests.collect
    install=ControllerTests.install
    denied=ControllerTests.denied

    def setUp(self):
        ControllerTests.setUp(self)
        self.old_sources={
            "scripts/qualify_owner_task_catalog_canary.py":"7197e146fad0f3dd70e001b0561c84e3c00562abe6f2a97d51586e5dfd5a080b",
            "tests/test_owner_task_catalog_canary_controller.py":"f819dabf6eddc5d6aeee238b3f02fb22785544d9aa357d9f2c95dac0714dceba",
            "unchanged_source.py":"a"*64}
        self.prepared["sources"]=deepcopy(self.old_sources)
        self.evidence.records.pop("preflight.json")
        self.prepared_raw=self.evidence.save(self.prepared,"preflight.json")
        self.source.return_value=deepcopy(self.old_sources)
        self.install()
        self.before=deepcopy(self.evidence.records)
        self.current=deepcopy(self.old_sources)
        self.current["scripts/qualify_owner_task_catalog_canary.py"]="d"*64
        self.current["tests/test_owner_task_catalog_canary_controller.py"]="e"*64
        self.source.return_value=deepcopy(self.current)
        self.client.job["location"]="East Asia"
        self.client.job["properties"]["template"]["containers"][0]["resources"]["ephemeralStorage"]=""
        self.checks={"test_count":1963,"passed_test_count":1961,
            "skipped_tests":[
                {"test":"test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_actual_fifo_entry_is_rejected_without_blocking",
                    "reason":"POSIX FIFO fixture","result":"not_exercised"},
                {"test":"test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_symlink_entry_never_followed",
                    "reason":"OS does not permit unprivileged test symlink creation","result":"not_exercised"}],
            "unittest_exit_code":0,"compilation_exit_code":0,"repository_policy":"PASS: synthetic oracle",
            "live_sql_tests_passed":False,"financial_or_visual_acceptance":False,"os_or_dependency_security_certification":False}
        self.pin=self.stack.enter_context(patch.object(control,"correction_preflight_receipt",
            side_effect=lambda:(deepcopy(self.prepared_raw["protected_receipt"]),deepcopy(self.prepared))))
        self.unreserved=self.stack.enter_context(patch.object(control,"require_start_unreserved"))
        self.qa=self.stack.enter_context(patch.object(control.qa,"run_local_checks",return_value=deepcopy(self.checks)))

    def correct(self):
        return control.prepare_correction(self.prepared_raw,self.prepared,self.client)

    def test_exact_two_predecessors_and_pinned_original_receipt_constants(self):
        self.assertEqual(control.CORRECTION_PREDECESSORS,{name:value for name,value in self.old_sources.items() if name!="unchanged_source.py"})
        self.assertEqual(control.ORIGINAL_PREFLIGHT_SHA,"ae7e1836cc94f7e5995bf2ec0524e2bca7e5b0abcd59a8ed29eb9c33aa42a34c")
        self.assertEqual(control.ORIGINAL_PREFLIGHT_VERSION,"2026-10-04T12:38:53.2891355Z")
        control.correction_sources(self.prepared,self.current)
        for changed in ({**self.current,"extra.py":"f"*64},
            {key:value for key,value in self.current.items() if key!="unchanged_source.py"},
            {**self.current,"unchanged_source.py":"f"*64},
            {**self.current,"scripts/qualify_owner_task_catalog_canary.py":self.old_sources["scripts/qualify_owner_task_catalog_canary.py"]}):
            with self.assertRaises(ValueError):control.correction_sources(self.prepared,changed)
        bad=deepcopy(self.prepared); bad["sources"]["scripts/qualify_owner_task_catalog_canary.py"]="f"*64
        with self.assertRaises(ValueError):control.correction_sources(bad,self.current)

    def test_correction_is_readonly_preserves_all_consumed_install_records_and_unarmed_start(self):
        writes=len(self.client.writes())
        raw=self.correct()
        _raw,value=self.evidence.load("readback-correction.json")
        self.assertEqual(raw["protected_receipt"],_raw["protected_receipt"])
        for name,record in self.before.items():
            self.assertEqual(self.evidence.records[name],record)
        self.assertEqual(value["preflight_receipt"],self.prepared_raw["protected_receipt"])
        self.assertEqual(value["sources"],self.current)
        self.assertEqual(value["status"],"reviewed_canary_readback_only_correction")
        self.assertEqual(value["observed_resource_defaults"],{"ephemeralStorage":""})
        self.assertIs(value["creation_redispatch_authorized"],False)
        self.assertIs(value["start_was_absent"],True)
        self.assertEqual(len(self.client.writes()),writes)
        self.assertEqual(self.unreserved.call_count,2)
        self.qa.assert_called_once_with()
        self.assertNotIn("start-intent.json",self.evidence.records)
        self.assertNotIn("start",self.reservations)
        self.assertEqual(control.accepted_sources(self.prepared),self.current)
        self.denied("canary_attempt_consumed_inspect_no_redispatch",self.install)
        self.assertEqual(len(self.client.writes()),writes)
        self.denied("canary_readback_correction_evidence_already_exists",self.correct)

    def test_any_local_or_remote_consumed_start_blocks_correction_without_qa_or_writes(self):
        writes=len(self.client.writes())
        for name in ("start-intent.json","start-ack.json"):
            self.evidence.records[name]={"synthetic_unknown_prior_attempt":True}
            self.denied("canary_attempt_consumed_inspect_no_redispatch",self.correct)
            self.evidence.records.pop(name)
        self.qa.assert_not_called()
        self.unreserved.side_effect=ValueError("canary_start_reservation_already_consumed")
        self.denied("canary_start_reservation_already_consumed",self.correct)
        self.qa.assert_not_called()
        self.assertEqual(len(self.client.writes()),writes)
        self.assertNotIn("readback-correction.json",self.evidence.records)

    def test_foreign_changed_runtime_job_or_nonempty_inventory_never_runs_qa(self):
        original=self.runtime.return_value; self.runtime.return_value={"changed":True}
        self.denied("canary_correction_cannot_change_runtime_or_existing_resources",self.correct)
        self.runtime.return_value=original
        original=self.client.job["properties"]["template"]["containers"][0]["image"]
        self.client.job["properties"]["template"]["containers"][0]["image"]="foreign"
        with self.assertRaises(ValueError):self.correct()
        self.client.job["properties"]["template"]["containers"][0]["image"]=original
        for runs in ({"value":[{"name":"foreign"}]},{"value":[],"nextLink":"more"},
                     {"value":[],"nextLink":False},{"value":[],"nextLink":0},{"value":[],"nextLink":[]}):
            self.client.runs=runs
            with self.assertRaises(ValueError):self.correct()
        self.qa.assert_not_called()
        self.assertNotIn("readback-correction.json",self.evidence.records)

    def test_source_drift_or_start_consumption_during_qa_withholds_correction(self):
        def changed():
            self.source.return_value={**self.current,"unchanged_source.py":"f"*64}
            return deepcopy(self.checks)
        self.qa.side_effect=changed
        self.denied("canary_source_changed_during_correction_qa",self.correct)
        self.assertNotIn("readback-correction.json",self.evidence.records)
        self.source.return_value=deepcopy(self.current)
        def consumed():
            self.evidence.records["start-intent.json"]={"synthetic_unknown":True}
            return deepcopy(self.checks)
        self.qa.side_effect=consumed
        self.denied("canary_attempt_consumed_inspect_no_redispatch",self.correct)
        self.assertNotIn("readback-correction.json",self.evidence.records)
        self.assertEqual(len(self.client.writes()),1)

    def test_reprotected_correction_flags_sources_runtime_old_ack_and_qa_must_match(self):
        self.correct()
        original=deepcopy(self.evidence.records["readback-correction.json"])
        mutations=(("creation_redispatch_authorized",True),("start_was_absent",1),("credentials_supplied",0),
            ("ibkr_source_writes",0),("sql_calls",False),("install_ack_receipt",{"foreign":True}),
            ("sources",self.old_sources),("runtime",{}),("definition_sha256","f"*64),("allowed_predecessors",{}),
            ("status","reviewed_region_readback_only_correction"),("observed_resource_defaults",None),
            ("observed_resource_defaults",{"ephemeralStorage":False}),
            ("observed_resource_defaults",{"ephemeralStorage":None}),
            ("observed_resource_defaults",{"ephemeralStorage":"1Gi"}),
            ("observed_resource_defaults",{"ephemeralStorage":"","unregistered":""}))
        for field,value in mutations:
            self.evidence.records["readback-correction.json"]=deepcopy(original)
            self.evidence.records["readback-correction.json"][field]=value
            self.evidence.rebind("readback-correction.json")
            with self.assertRaises(ValueError):control.accepted_sources(self.prepared)
        self.evidence.records["readback-correction.json"]=deepcopy(original)
        self.evidence.records["readback-correction.json"]["local_checks"]["unittest_exit_code"]=False
        self.evidence.rebind("readback-correction.json")
        with self.assertRaises(ValueError):control.accepted_sources(self.prepared)

    def test_nonempty_or_malformed_observed_resource_defaults_stop_correction_before_qa(self):
        before=deepcopy(self.evidence.records)
        writes=len(self.client.writes())
        for resources in ({"cpu":0.5,"memory":"1Gi","ephemeralStorage":None},
            {"cpu":0.5,"memory":"1Gi","ephemeralStorage":False},
            {"cpu":0.5,"memory":"1Gi","ephemeralStorage":"1Gi"},
            {"cpu":0.5,"memory":"1Gi","ephemeralStorage":"","unregistered":""}):
            self.client.job["properties"]["template"]["containers"][0]["resources"]=resources
            with self.assertRaises(ValueError):self.correct()
            self.assertEqual(self.evidence.records,before)
            self.assertEqual(len(self.client.writes()),writes)
        self.qa.assert_not_called()

    def test_qa_exact_types_counts_skips_and_false_certifications(self):
        control.validate_correction_checks(self.checks)
        for field,value in (("test_count",1962),("test_count",1963.0),("passed_test_count",1963),
            ("unittest_exit_code",False),("compilation_exit_code",False),("live_sql_tests_passed",0),
            ("financial_or_visual_acceptance",0),("os_or_dependency_security_certification",0),
            ("repository_policy","failed"),("extra",True)):
            changed=deepcopy(self.checks); changed[field]=value
            with self.assertRaises(ValueError):control.validate_correction_checks(changed)
        for skipped in ([self.checks["skipped_tests"][0]]*2,[{"test":"unreviewed","reason":"arbitrary","result":"not_exercised"}]):
            changed=deepcopy(self.checks); changed["skipped_tests"]=skipped
            with self.assertRaises(ValueError):control.validate_correction_checks(changed)


class PinnedCorrectionAndRemoteAbsenceTests(unittest.TestCase):
    def test_real_pinned_preflight_helper_requires_both_exact_old_sha_and_version(self):
        receipt={"sha256":"ae7e1836cc94f7e5995bf2ec0524e2bca7e5b0abcd59a8ed29eb9c33aa42a34c",
            "version_id":"2026-10-04T12:38:53.2891355Z"}
        value={"synthetic":"protected-loader-not-cloud-proof"}
        with patch.object(control,"load",return_value=({"protected_receipt":receipt},value)):
            self.assertEqual(control.correction_preflight_receipt(),(receipt,value))
        for field,changed in (("sha256","f"*64),("version_id","2026-10-04T12:38:53.2891356Z")):
            wrong=dict(receipt); wrong[field]=changed
            with patch.object(control,"load",return_value=({"protected_receipt":wrong},value)):
                with self.assertRaisesRegex(ValueError,"^canary_exact_original_preflight_required_for_correction$"):
                    control.correction_preflight_receipt()

    def test_only_fixed_start_blob_notfound_is_verified_absence_and_other_results_deny(self):
        error=control.ResourceNotFoundError("synthetic"); error.error_code="BlobNotFound"; error.status_code=404
        getter=unittest.mock.Mock(side_effect=error)
        with patch.object(control,"reservation_blob",return_value=SimpleNamespace(get_blob_properties=getter)) as factory:
            control.require_start_unreserved()
            factory.assert_called_once_with("start")
            getter.assert_called_once_with(timeout=10,retry_total=0)
        for code in ("ContainerNotFound","ResourceGroupNotFound",None):
            error=control.ResourceNotFoundError("SYNTHETIC_NOT_AUTHORIZATION"); error.error_code=code; error.status_code=404
            with patch.object(control,"reservation_blob",return_value=SimpleNamespace(get_blob_properties=unittest.mock.Mock(side_effect=error))):
                with self.assertRaisesRegex(ValueError,"^canary_start_reservation_absence_unverified$"):
                    control.require_start_unreserved()
        for status in (None,403,404.0,True,"404"):
            error=control.ResourceNotFoundError("SYNTHETIC_NOT_AUTHORIZATION"); error.error_code="BlobNotFound"; error.status_code=status
            with patch.object(control,"reservation_blob",return_value=SimpleNamespace(get_blob_properties=unittest.mock.Mock(side_effect=error))):
                with self.assertRaisesRegex(ValueError,"^canary_start_reservation_absence_unverified$"):
                    control.require_start_unreserved()
        for properties in ({},None,False,SimpleNamespace(size=0)):
            with patch.object(control,"reservation_blob",return_value=SimpleNamespace(get_blob_properties=lambda **_kwargs:properties)):
                with self.assertRaisesRegex(ValueError,"^canary_start_reservation_already_consumed$"):
                    control.require_start_unreserved()
        getter=unittest.mock.Mock(side_effect=RuntimeError("SYNTHETIC_PROVIDER_CREDENTIAL"))
        with patch.object(control,"reservation_blob",return_value=SimpleNamespace(get_blob_properties=getter)):
            with self.assertRaisesRegex(ValueError,"^canary_start_reservation_absence_unverified$"):
                control.require_start_unreserved()
            self.assertEqual(getter.call_count,1)


class ArmTransportTests(unittest.TestCase):
    def setUp(self):
        self.patch=patch.object(control,"definition",side_effect=definition); self.patch.start(); self.addCleanup(self.patch.stop)

    def response(self,status=200,value=None,content=None):
        raw=json.dumps({} if value is None else value).encode() if content is None else content
        item=SimpleNamespace(status_code=status,closed=False)
        item.iter_content=lambda size:(raw[offset:offset+size] for offset in range(0,len(raw),size))
        item.close=lambda:setattr(item,"closed",True)
        return item

    def transport(self,responses):
        calls,tokens=[],[]
        def request(*args,**kwargs):
            calls.append((args,kwargs)); response=responses.pop(0)
            if isinstance(response,Exception): raise response
            return response
        credential=SimpleNamespace(get_token=lambda audience:(tokens.append(audience) or SimpleNamespace(token="SYNTHETIC_TOKEN",expires_on=5000)))
        session=SimpleNamespace(request=request)
        return control.ArmTransport(credential,session),calls,tokens

    def test_only_exact_job_put_start_template_and_bounded_reads(self):
        responses=[self.response(201),self.response(202),self.response(),self.response()]
        transport,calls,tokens=self.transport(list(responses))
        with patch.object(control.time,"time",return_value=1000):
            transport.call("PUT",PATH+"?api-version=2024-03-01",body=definition())
            transport.call("POST",PATH+"/start?api-version=2024-03-01",body=definition()["properties"]["template"])
            transport.call("GET",PATH+"/executions/"+EXECUTION+"?api-version=2024-03-01")
            transport.call("GET",PATH+"/executions/"+EXECUTION+"/replicas?api-version=2023-11-02-preview")
        self.assertEqual(tokens,["https://management.azure.com/.default"])
        for args,kwargs in calls:
            self.assertTrue(args[1].startswith("https://management.azure.com"+PATH))
            self.assertEqual(kwargs["timeout"],(5,20))
            self.assertIs(kwargs["stream"],True); self.assertIs(kwargs["allow_redirects"],False)
        self.assertTrue(all(response.closed for response in responses))

    def test_wrong_endpoint_methods_query_body_identity_or_secret_never_request(self):
        transport,calls,tokens=self.transport([])
        bad=definition(); bad["identity"]={"type":"SystemAssigned"}
        alias_body=definition(); alias_body["location"]="East Asia"
        resource_readback_body=definition()
        resource_readback_body["properties"]["template"]["containers"][0]["resources"]["ephemeralStorage"]=""
        resource_readback_template=deepcopy(resource_readback_body["properties"]["template"])
        cases=(("PUT",PATH+"-foreign?api-version=2024-03-01",definition()),
            ("PUT",PATH+"?api-version=2024-03-01",bad),
            ("PUT",PATH+"?api-version=2024-03-01",alias_body),
            ("PUT",PATH+"?api-version=2024-03-01",resource_readback_body),
            ("POST",PATH+"/start?api-version=2024-03-01",resource_readback_template),
            ("POST",PATH+"/start?api-version=2024-03-01",{}),
            ("DELETE",PATH+"?api-version=2024-03-01",None),
            ("GET","https://management.azure.com"+PATH+"?api-version=2024-03-01",None),
            ("GET",PATH+"/executions/foreign?api-version=2024-03-01",None),
            ("GET",PATH+"/executions/"+EXECUTION+"/replicas?api-version=2024-03-01",None),
            ("GET",PATH+"?api-version=2024-03-01#fragment",None))
        for method,path,body in cases:
            with self.assertRaises(ValueError):transport.call(method,path,body=body)
        self.assertEqual(calls,[]); self.assertEqual(tokens,[])

    def test_redirect_timeout_partial_badjson_oversize_are_sanitized_without_retry(self):
        for response in (self.response(302),self.response(206),self.response(content=b'{'),
            self.response(value=[]),self.response(content=b'{"id":"first","id":"second"}'),
            self.response(content=b'{"value":NaN}'),self.response(content=b'{"value":Infinity}'),
            self.response(content=b'{"nested":{"value":1e999}}'),self.response(content=b'{"nested":[-1e999]}'),
            self.response(content=b"x"*(2*1024*1024+1)),RuntimeError("SYNTHETIC_SECRET_EXCEPTION")):
            transport,calls,_tokens=self.transport([response])
            with self.assertRaisesRegex(RuntimeError,"^canary_http_outcome_requires_inspection$"):
                transport.call("GET",PATH+"?api-version=2024-03-01")
            self.assertEqual(len(calls),1)
            if not isinstance(response,Exception):self.assertTrue(response.closed)


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]; self.store={}; self.lost_ack=False
        self.version="2026-10-04T13:00:00.1234567Z"; self.etag='"0xABC123"'
        self.corrupt=None
        fixture=self
        class Blob:
            def __init__(self,stage):self.stage=stage
            def upload_blob(self,payload,**kwargs):
                fixture.calls.append(("upload",self.stage,kwargs))
                if self.stage in fixture.store:raise RuntimeError("synthetic_existing_reservation")
                fixture.store[self.stage]=payload
                if fixture.lost_ack:raise RuntimeError("synthetic_lost_ack")
                return {"version_id":fixture.version,"etag":fixture.etag}
            def get_blob_properties(self,**kwargs):
                fixture.calls.append(("properties",self.stage,kwargs))
                result=SimpleNamespace(size=len(fixture.store[self.stage]),etag=fixture.etag,
                    version_id=fixture.version,blob_type="BlockBlob")
                if fixture.corrupt:setattr(result,*fixture.corrupt)
                return result
            def download_blob(self,**kwargs):
                fixture.calls.append(("download",self.stage,kwargs))
                return SimpleNamespace(readall=lambda:fixture.store[self.stage])
        def sdk(url,**kwargs):
            fixture.calls.append(("sdk",url,kwargs))
            def get_blob(container,blob,version_id=None):
                fixture.calls.append(("blob",blob,version_id))
                fixture.assertEqual(container,"specialist-audit-checkpoints")
                stage=blob.removeprefix("probes/owner-codex-catalog-canary-20261004/").removesuffix("-reservation.json")
                fixture.assertIn(stage,("install","start"))
                return Blob(stage)
            return SimpleNamespace(get_blob_client=get_blob)
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(control,"BlobServiceClient",side_effect=sdk))
        self.stack.enter_context(patch.object(control,"AzureCliCredential",return_value=object()))
        self.stack.enter_context(patch.object(control.deploy,"archive_protection",side_effect=lambda:fixture.calls.append(("hold",))))

    def test_actual_conditional_create_pinned_version_hold_and_sdk_config(self):
        for stage in ("install","start"):
            self.calls.clear()
            receipt=control.reserve(stage,{"stage":stage,"synthetic":True})
            self.assertEqual(set(receipt),{"blob","sha256","version_id","etag","protected_readback_verified"})
            self.assertEqual(receipt["blob"],"probes/owner-codex-catalog-canary-20261004/"+stage+"-reservation.json")
            self.assertEqual([row[2] for row in self.calls if row[0]=="blob"],[None,self.version])
            self.assertEqual([row[0] for row in self.calls].count("hold"),3)
            self.assertEqual(self.calls[0],("hold",)); self.assertEqual(self.calls[-1],("hold",))
            for row in self.calls:
                if row[0]=="sdk":
                    self.assertEqual(row[1],"https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net")
                    self.assertEqual(row[2]["retry_total"],0); self.assertEqual(row[2]["redirect_max"],0)
                    self.assertIs(row[2]["permit_redirects"],False); self.assertIs(row[2]["retry_to_secondary"],False)
                if row[0]=="upload":
                    self.assertEqual(row[2]["match_condition"],control.MatchConditions.IfMissing)
                    self.assertEqual(row[2]["blob_type"],"BlockBlob"); self.assertIs(row[2]["overwrite"],False)
                    self.assertEqual(row[2]["max_concurrency"],1); self.assertEqual(row[2]["retry_total"],0)
                if row[0]=="download":
                    self.assertEqual(row[2]["match_condition"],control.MatchConditions.IfNotModified)
                    self.assertEqual(row[2]["etag"],self.etag); self.assertEqual(row[2]["length"],len(self.store[stage])+1)

    def test_existing_equal_bytes_or_lost_ack_never_create_authority(self):
        control.reserve("install",{"synthetic":True})
        with self.assertRaises(RuntimeError):control.reserve("install",{"synthetic":True})
        self.lost_ack=True; self.calls.clear()
        with self.assertRaises(RuntimeError):control.reserve("start",{"synthetic":True})
        self.assertIn("start",self.store)
        self.assertNotIn("properties",[row[0] for row in self.calls])
        self.assertNotIn("download",[row[0] for row in self.calls])

    def test_malformed_receipt_payload_or_pinned_properties_deny(self):
        receipt=control.reserve("install",{"synthetic":True}); payload=self.store["install"]
        for field,value in (("blob","foreign"),("version_id","wrong"),("etag","unquoted"),
                           ("sha256","b"*64),("protected_readback_verified",1),("extra",True)):
            changed=dict(receipt); changed[field]=value; count=len(self.calls)
            with self.assertRaises(ValueError):control.reservation_readback("install",changed,payload)
            self.assertEqual(len(self.calls),count)
        for mismatch in (("blob_type","AppendBlob"),("etag",'"0xBAD"'),("version_id","foreign"),("size",0)):
            self.corrupt=mismatch
            with self.assertRaisesRegex(ValueError,"^canary_reservation_ack_unverified$"):
                control.reservation_readback("install",receipt,payload)

    def test_unregistered_stage_or_oversized_value_deny_before_client(self):
        with self.assertRaises(ValueError):control.reserve("foreign",{"synthetic":True})
        with self.assertRaises(ValueError):control.reserve("install",{"data":"x"*(2*1024*1024)})
        self.assertEqual(self.calls,[])


if __name__ == "__main__":
    unittest.main()
