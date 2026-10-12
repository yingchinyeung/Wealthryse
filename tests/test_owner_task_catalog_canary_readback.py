"""Independent OFFLINE readback tests; every cloud/auth/evidence effect is mocked.

Synthetic receipt pins in workflow fixtures are not authentication of historical
Azure evidence. Literal registration/pin tests retain the measured contract.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
from datetime import timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import verify_owner_task_catalog_canary_readback as c
from ibkr_etl import owner_codex_catalog_transport as codec


BASE="/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/"
JOB_ID=BASE+"Microsoft.App/jobs/ibkrv2stg-ownertrial-log-canary"
ENV_ID=BASE+"Microsoft.App/managedEnvironments/ibkrv2stg-env"
EXEC="ibkrv2stg-ownertrial-log-canary-9hh541u"
EXEC_ID=JOB_ID+"/executions/"+EXEC
REPLICA=EXEC+"-87mk2"
IMAGE="python:3.12-slim-bookworm@sha256:9901e0a8d75037d8242ed43155cbcb2d1f61be1356383d8054afb59fd50e39c4"
CONTAINER="catalog-log-canary"
ADDITIONS=("scripts/verify_owner_task_catalog_canary_readback.py","tests/test_owner_task_catalog_canary_readback.py")
BINDING={"purpose":"canary","profile_key":"owner-codex-isolated-059-20261004-v1",
    "job_resource_id":JOB_ID,"operation_id":"b86898e4-09db-4e79-92e3-114b2ebc209e",
    "source_sha256":"cb97dfee55b23772e7a558ee7012e449cbda4cbe5a5cd602fb848d04cee53fb4"}
RECORD_SHA="5c7101833c90c9539fd9fa1a158053dfe04bee71a4746ceb42eecd10981a35b5"
FROZEN={"scripts/qualify_owner_task_catalog_canary.py":"bdbac18bfa59a3f01bef8d4ce6e5c824ccb42b433e0420d2453ea90cc2f82eb7",
    "tests/test_owner_task_catalog_canary_controller.py":"d114f8fe06fcb714d72e1cb501e89f7a270b974bbd30847eda6e07ad206c386e"}
PREDECESSORS={"scripts/qualify_owner_task_catalog_canary.py":"7197e146fad0f3dd70e001b0561c84e3c00562abe6f2a97d51586e5dfd5a080b",
    "tests/test_owner_task_catalog_canary_controller.py":"f819dabf6eddc5d6aeee238b3f02fb22785544d9aa357d9f2c95dac0714dceba"}
PINS={
    "preflight.json":("ae7e1836cc94f7e5995bf2ec0524e2bca7e5b0abcd59a8ed29eb9c33aa42a34c","2026-10-04T12:38:53.2891355Z"),
    "install-ack.json":("94f94a4e0a42cab5ddb381e38490024ce8e6490c093930fc7b4fab290b43e9c8","2026-10-04T12:44:28.0266417Z"),
    "readback-correction.json":("38f731712ac04556ab62655edff490f74695f86738303a38b1340b2c699e9d87","2026-10-04T13:22:16.8421209Z"),
    "install-acceptance.json":("9b3224795c395ea57b4bdca3c8fdbbf059364a12be9769fa827e5e52fa9a83e2","2026-10-04T13:28:01.1233365Z"),
    "start-ack.json":("6c66a35159f71646f88505a308eb1292b328517634e5747867d33d00741c2ae3","2026-10-04T13:31:42.5808438Z"),
    "running-metadata-observed.json":("0255cb4a2c9b05c6dfea127a44309fe45c95497ea171e0c39dec68b825345106","2026-10-04T13:35:47.7516538Z"),
    "log-diagnostic.json":("f55690b650554ea502829b6d579bb5b38c0eaf2b850b5b1fc51d3280ef8710a2","2026-10-04T13:50:32.1977619Z")}
FALSE_FLAGS=("platform_replacement_excluded","provider_sql_authority","provider_semantics_verified",
    "fresh_restricted_login_verified","model_credential_containment_verified","owner_runtime_active",
    "six_specialist_runtimes_active","production_migration_059_installed","ibkr_source_writes","credentials_supplied")
RUNTIME={"python":"3.12.10","requirements_lock_sha256":"a"*64,"versions":{"requests":"2.32.5"}}
BASELINE={"job_inventory_complete":True,"other_jobs":{f"existing-{i:02d}":{"image":"unchanged"} for i in range(34)}}
ANCHOR={"sha256":"e8e103df668996a00fe8d07a9e45f71f23078c358d6586f97609817d27f92589",
    "version_id":"2026-10-04T11:45:48.1413402Z","blob":"synthetic/historical-host"}


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def seal(value,kind):
    raw=deepcopy(value); sha=digest(value)
    raw["protected_receipt"]={"blob":f"acceptance/gateway-read-owner-task-pg-{kind}-{sha}.json",
        "sha256":sha,"version_id":"2026-10-04T14:00:00.1234567Z","protected_readback_verified":True}
    return raw


def body(raw):
    return {k:v for k,v in raw.items() if k!="protected_receipt"}


def source_pair():
    old={f"synthetic-source-{i:03d}.py":"a"*64 for i in range(703)}; old.update(FROZEN)
    return old,{**old,ADDITIONS[0]:"b"*64,ADDITIONS[1]:"c"*64}


def checks(count=1978):
    return {"test_count":count,"passed_test_count":count,"skipped_tests":[],"unittest_exit_code":0,
        "compilation_exit_code":0,"repository_policy":"PASS: synthetic independent oracle",
        "live_sql_tests_passed":False,"financial_or_visual_acceptance":False,"os_or_dependency_security_certification":False}


def desired():
    return {"location":"eastasia","identity":{"type":"None"},
        "tags":{"purpose":"owner-catalog-log-canary","operation":BINDING["operation_id"],
            "sourceSha256":BINDING["source_sha256"],"workerSha256":"d"*64,"profile":BINDING["profile_key"]},
        "properties":{"environmentId":ENV_ID,"configuration":{"triggerType":"Manual","replicaTimeout":600,
            "replicaRetryLimit":0,"manualTriggerConfig":{"parallelism":1,"replicaCompletionCount":1},"secrets":[],"registries":[]},
            "template":{"containers":[{"name":CONTAINER,"image":IMAGE,"command":["python"],
                "args":["-I","-S","-B","-c","SYNTHETIC_INERT_PAYLOAD"],"env":[],"resources":{"cpu":0.5,"memory":"1Gi"}}]}}}


def observed_template():
    value=deepcopy(desired()["properties"]["template"])
    value["containers"][0]["resources"]["ephemeralStorage"]=""
    value["containers"][0]["imageType"]="ContainerImage"
    value["initContainers"]=[]
    return value


def execution(status="Succeeded"):
    return {"id":EXEC_ID,"name":EXEC,"type":"Microsoft.App/jobs/executions","properties":{
        "status":status,"startTime":"2026-10-04T13:31:29+00:00",
        "endTime":"2026-10-04T13:36:54+00:00" if status!="Running" else None,"template":observed_template()}}


def replica():
    return {"id":EXEC_ID+"/replicas/"+REPLICA,"name":REPLICA,
        "type":"Microsoft.App/containerapps/revisions/replicas","createdTime":"2026-10-04T13:31:29Z",
        "runningState":"Running","containers":[{"name":CONTAINER,"ready":True,"started":True,
            "restartCount":0,"runningState":"Running"}]}


class MemoryPath:
    def __init__(self,records,name=None):self.records,self.name=records,name
    def __truediv__(self,name):return MemoryPath(self.records,name)
    def exists(self):return self.name in self.records


class Arm:
    def __init__(self):
        self.calls=[]; self.actual=execution(); self.job=desired()
        self.job.update(id=JOB_ID,name="ibkrv2stg-ownertrial-log-canary",location="East Asia")
        self.job["properties"].update(template=observed_template(),provisioningState="Succeeded")
        self.env={"id":ENV_ID,"properties":{"appLogsConfiguration":{"destination":"log-analytics",
            "logAnalyticsConfiguration":{"customerId":"5264a124-10cd-45f6-93a5-6d65c37e9bfb"}}}}
    def get(self,path):
        self.calls.append(path)
        values={JOB_ID+"?api-version=2024-03-01":self.job,
            EXEC_ID+"?api-version=2024-03-01":self.actual,ENV_ID+"?api-version=2024-03-01":self.env}
        if path not in values:raise AssertionError("synthetic_unregistered_get")
        return deepcopy(values[path])


class ContractTests(unittest.TestCase):
    def test_fixed_registration_pins_and_frozen_old_controller(self):
        self.assertEqual(c.PINNED,PINS); self.assertEqual(c.FROZEN_CONTROLLER,FROZEN)
        self.assertEqual(c.ADDITIONS,ADDITIONS); self.assertEqual(c.EXECUTION,EXEC)
        self.assertEqual(c.EXECUTION_ID,EXEC_ID); self.assertEqual(c.REPLICA,REPLICA)
        self.assertEqual(c.REPLICA_TYPE,"Microsoft.App/containerapps/revisions/replicas")
        self.assertEqual(c.RECORD_SHA,RECORD_SHA); self.assertEqual(c.FALSE_FLAGS,FALSE_FLAGS)
        self.assertEqual(c.FOLDER.name,"owner-task-catalog-readback-20261004")
        self.assertEqual(c.HISTORY_FOLDER.name,"owner-task-catalog-canary-20261004")

    def test_utc_exact_z_or_zero_offset_only(self):
        for value in ("2026-10-04T13:31:29Z","2026-10-04T13:31:29+00:00",
            "2026-10-04T13:31:29.1234567Z","2026-10-04T13:31:29.1234567+00:00"):
            self.assertEqual(c.utc(value).tzinfo,timezone.utc)
        for value in (None,False,1,"2026-10-04T13:31:29","2026-10-04T13:31:29-00:00",
            "2026-10-04T13:31:29+01:00","2026-10-04T13:31:29-08:00","2026-10-04T13:31:29z",
            "2026-10-04 13:31:29Z","2026-10-04T13:31:29.12345678Z","2026-10-04T13:31:29Z "):
            with self.assertRaises(ValueError):c.utc(value)

    def test_source_manifest_exact_old705_plus_two_not_retargeting_or_drift(self):
        old,current=source_pair(); self.assertEqual(len(old),705); self.assertEqual(len(current),707)
        c.validate_sources(old,current)
        mutations=[{**current,"unregistered.py":"d"*64},
            {key:value for key,value in current.items() if key!=ADDITIONS[1]},
            {**current,"synthetic-source-000.py":"d"*64},{**current,ADDITIONS[0]:True},
            {**current,ADDITIONS[0]:"B"*64},{**current,ADDITIONS[0]:"a"*63}]
        for changed in mutations:
            with self.assertRaises(ValueError):c.validate_sources(old,changed)
        for name in FROZEN:
            changed=deepcopy(old); changed[name]="f"*64
            changed_current={**current,name:"f"*64}
            with self.assertRaises(ValueError):c.validate_sources(changed,changed_current)
        changed=deepcopy(old); changed.pop("synthetic-source-000.py")
        with self.assertRaises(ValueError):c.validate_sources(changed,current)

    def test_receipt_exact_content_address_version_flag_and_pin(self):
        raw=seal({"synthetic":True},"catalog-readback-preflight"); receipt=raw["protected_receipt"]
        self.assertEqual(c.receipt_binding(raw,"catalog-readback-preflight"),{"synthetic":True})
        c.receipt_binding(raw,"catalog-readback-preflight",(receipt["sha256"],receipt["version_id"]))
        for key,value in (("blob","foreign"),("sha256","f"*64),("version_id","2026-10-04T14:00:00Z"),
            ("version_id","2026-10-04T14:00:00.1234567+00:00"),("protected_readback_verified",1),("extra",False)):
            changed=deepcopy(raw); changed["protected_receipt"][key]=value
            with self.assertRaises(ValueError):c.receipt_binding(changed,"catalog-readback-preflight")
        for pin in (("f"*64,receipt["version_id"]),(receipt["sha256"],"2026-10-04T14:00:00.1234568Z")):
            with self.assertRaises(ValueError):c.receipt_binding(raw,"catalog-readback-preflight",pin)
        with self.assertRaises(ValueError):c.receipt_binding(seal({"large":"x"*(2*1024*1024)},"catalog-readback-preflight"),"catalog-readback-preflight")

    def test_body_and_record_names_require_registered_complete_records(self):
        for raw in (None,[],False,{}, {"ordinary":"no-receipt"}):
            with self.assertRaises(ValueError):c.body(raw)
        for name in ("preflight.json","acceptance.json",*(f"part-{i:03d}.json" for i in range(9))):
            self.assertEqual(c.record_kind(name),"catalog-readback-"+name.removesuffix(".json"))
        for name in ("part-009.json","part-000.json/foreign","../preflight.json","install-ack.json",None,False):
            with self.assertRaises((ValueError,TypeError)):c.record_kind(name)

    def test_readonly_arm_only_four_exact_get_endpoints(self):
        calls=[]
        transport=SimpleNamespace(call=lambda *args,**kwargs:(calls.append((args,kwargs)) or ({"synthetic":True},200)))
        arm=c.ReadOnlyArm(transport)
        for path in (JOB_ID+"?api-version=2024-03-01",EXEC_ID+"?api-version=2024-03-01",
            EXEC_ID+"/replicas?api-version=2023-11-02-preview",ENV_ID+"?api-version=2024-03-01"):
            self.assertEqual(arm.get(path),{"synthetic":True})
        self.assertTrue(all(args[0]=="GET" and len(args)==2 and kwargs=={} for args,kwargs in calls))
        for path in (JOB_ID+"/start?api-version=2024-03-01",JOB_ID+"/executions?api-version=2024-03-01",
            EXEC_ID+"/replicas?api-version=2024-03-01",EXEC_ID+"?api-version=2024-03-01#x",
            "https://management.azure.com"+JOB_ID+"?api-version=2024-03-01",EXEC_ID+"-foreign?api-version=2024-03-01",
            "https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/ownertrial-20261004-admin-url?api-version=7.4"):
            with self.assertRaises(ValueError):arm.get(path)
        self.assertEqual(len(calls),4)


class ReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.full_record=c.prior.worker.synthetic_record(codec.Binding(**BINDING))
        cls.full_lines=codec.encode_record(cls.full_record,codec.Binding(**BINDING))
        if len(canonical(cls.full_record))!=4194304 or digest(cls.full_record)!=RECORD_SHA:
            raise AssertionError("independent_measured_record_fixture_differs")

    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.records={}; self.new={}; self.arm=Arm(); self.reads=[]; self.saved=[]; self.query_scopes=[]
        self.old_sources,self.current=source_pair()
        self.stack.enter_context(patch.object(c.prior,"definition",side_effect=desired))
        self.stack.enter_context(patch.object(c.prior,"binding",side_effect=lambda:codec.Binding(**BINDING)))
        self.source=self.stack.enter_context(patch.object(c,"sources",return_value=deepcopy(self.current)))
        self.runtime=self.stack.enter_context(patch.object(c.prior.host,"runtime_descriptor",return_value=deepcopy(RUNTIME)))
        self.baseline=self.stack.enter_context(patch.object(c.prior,"baseline",return_value=deepcopy(BASELINE)))
        self.stack.enter_context(patch.object(c.prior,"historical_host",return_value=deepcopy(ANCHOR)))
        self.stack.enter_context(patch.object(c,"FOLDER",MemoryPath(self.new)))
        self.stack.enter_context(patch.object(c.prior.deploy,"FOLDER",MemoryPath(self.new)))
        self.stack.enter_context(patch.object(c,"read_local",side_effect=self.read_local))
        self.protected=self.stack.enter_context(patch.object(c.prior.deploy,"protected",side_effect=self.protected_read))
        self.saver=self.stack.enter_context(patch.object(c.prior.deploy,"save",side_effect=self.save))
        self.reservation=self.stack.enter_context(patch.object(c.prior,"reservation_readback",side_effect=self.check_reservation))
        self.qa=self.stack.enter_context(patch.object(c.prior.qa,"run_local_checks",return_value=checks()))
        self.stack.enter_context(patch.object(c.prior.logs,"collect_evidence",side_effect=lambda query,scope:query(scope)))
        self.build_history()
        self.synthetic_pins={name:(self.records[name]["protected_receipt"]["sha256"],self.records[name]["protected_receipt"]["version_id"]) for name in PINS}
        self.stack.enter_context(patch.object(c,"PINNED",self.synthetic_pins))
        self.before=deepcopy(self.records)

    def add(self,name,value):
        self.records[name]=seal(value,"catalog-canary-"+name.removesuffix(".json"))
        return self.records[name]["protected_receipt"]

    def build_history(self):
        old=deepcopy(self.old_sources); old.update(PREDECESSORS)
        p=self.add("preflight.json",{"status":"prepared_no_secret_catalog_canary","sources":old,
            "definition":desired(),"binding":deepcopy(BINDING),"registration_sha256":c.prior.TARGET.registration_sha256,
            "runtime":deepcopy(RUNTIME),"baseline":deepcopy(BASELINE),"historical_host_receipt":deepcopy(ANCHOR)})
        common={"preflight_receipt":p,"definition_sha256":digest(desired()),"binding":deepcopy(BINDING),
            "automatic_retry":False,"credentials_supplied":False,"sql_calls":0}
        i=self.add("install-intent.json",dict(common,stage="install",unrelated_administrator_race_excluded=False))
        ia=self.add("install-ack.json",{"stage":"install","intent_receipt":i,"http_status":201,
            "readiness_verified":False,"reservation":self.reservation_for("install")})
        self.add("readback-correction.json",{"status":"reviewed_canary_readback_only_correction","preflight_receipt":p,
            "install_ack_receipt":ia,"sources":deepcopy(self.old_sources),"runtime":deepcopy(RUNTIME),
            "local_checks":checks(1963),"definition_sha256":digest(desired()),"binding":deepcopy(BINDING),
            "allowed_predecessors":deepcopy(PREDECESSORS),"requested_location":"eastasia","observed_location":"East Asia",
            "observed_resource_defaults":{"ephemeralStorage":""},"creation_redispatch_authorized":False,
            "start_was_absent":True,"credentials_supplied":False,"ibkr_source_writes":False,"sql_calls":0})
        accepted=self.add("install-acceptance.json",{"status":"installed_no_secret_manual_canary",
            "install_ack_receipt":ia,"preflight_receipt":p})
        si=self.add("start-intent.json",dict(common,stage="start",install_acceptance_receipt=accepted))
        sa=self.add("start-ack.json",{"stage":"start","intent_receipt":si,"http_status":202,
            "execution":EXEC,"execution_id":EXEC_ID,"reservation":self.reservation_for("start")})
        observation=self.add("running-metadata-observed.json",{"status":"running_metadata_observed_not_accepted",
            "start_ack_receipt":sa,"binding":deepcopy(BINDING),"source_manifest":deepcopy(self.old_sources),
            "execution_response":execution("Running"),"replica_identity":replica(),"response_root_keys":["value"],
            "raw_replica_response_sha256":"9"*64,"observed_at_utc":"2026-10-04T13:35:28.922527+00:00",
            "transport_acceptance":False,"provider_sql_authority":False,"ibkr_source_writes":False,
            "credentials_supplied":False,"sql_calls":0,"model_calls":0})
        self.add("log-diagnostic.json",{"status":"diagnostic_complete_live_transport_not_controller_acceptance",
            "observed_replica_receipt":observation,"sources":deepcopy(self.old_sources),"binding":deepcopy(BINDING),
            "mapping_provenance":"protected_owner_observed_replica","controller_acceptance":False,
            "complete_parts_retained":False,"diagnostic_record_sha256":RECORD_SHA,"execution_response":execution()})

    def reservation_for(self,stage):
        payload=canonical(body(self.records[stage+"-intent.json"]))
        return {"blob":"probes/owner-codex-catalog-canary-20261004/"+stage+"-reservation.json",
            "sha256":hashlib.sha256(payload).hexdigest(),"version_id":"2026-10-04T13:00:00.1234567Z",
            "etag":'"0xABC123"',"protected_readback_verified":True}

    def check_reservation(self,stage,receipt,payload):
        if canonical(receipt)!=canonical(self.reservation_for(stage)) or payload!=canonical(body(self.records[stage+"-intent.json"])):
            raise ValueError("synthetic_consumed_reservation_binding_invalid")

    def read_local(self,folder,name):
        self.reads.append(name)
        store=self.records if folder is c.HISTORY_FOLDER else self.new
        if name not in store or type(store[name]) is not dict:
            raise ValueError("readback_complete_local_record_required")
        return deepcopy(store[name])

    def protected_read(self,raw,kind):
        value=body(raw)
        expected=seal(value,kind)["protected_receipt"]
        if canonical(raw.get("protected_receipt"))!=canonical(expected):
            raise ValueError("synthetic_protected_readback_differs")
        return deepcopy(value)

    def save(self,value,name,kind):
        if name in self.new:raise ValueError("synthetic_exclusive_record_consumed")
        self.saved.append(name); self.new[name]=seal(value,kind)
        return deepcopy(self.new[name])

    def rebind(self,name,store=None):
        store=self.records if store is None else store
        kind=("catalog-canary-" if store is self.records else "catalog-readback-")+name.removesuffix(".json")
        store[name]=seal(body(store[name]),kind)
        if store is self.records and name in self.synthetic_pins:
            self.synthetic_pins[name]=(store[name]["protected_receipt"]["sha256"],store[name]["protected_receipt"]["version_id"])

    def query(self,scope):
        self.query_scopes.append(scope)
        return {"record":deepcopy(self.full_record),"lines":list(self.full_lines),"header_inventory_sha256":"e"*64}

    def prepare(self):return c.prepare(self.arm)
    def collect(self,verify_only=False):return c.collect(self.arm,self.query,verify_only=verify_only)

    def test_real_history_verifies_all_nine_protected_records_and_both_consumed_reservations(self):
        records=c.history()
        self.assertEqual(records,self.records); self.assertEqual(len(records),9)
        self.assertEqual(self.reservation.call_count,2)
        self.assertEqual([call.args[0] for call in self.reservation.call_args_list],["install","start"])
        self.assertEqual(len(self.protected.call_args_list),9)
        self.assertEqual(self.new,{}); self.assertEqual(self.saved,[])

    def test_history_missing_pin_pointer_or_remote_mismatch_never_prepares(self):
        old=deepcopy(self.records)
        self.records.pop("install-intent.json")
        with self.assertRaises(ValueError):self.prepare()
        self.records=deepcopy(old)
        self.records["start-ack.json"]["protected_receipt"]["version_id"]="2026-10-04T14:00:00.1234568Z"
        with self.assertRaises(ValueError):self.prepare()
        self.records=deepcopy(old)
        self.records["start-intent.json"]["install_acceptance_receipt"]={"foreign":True}; self.rebind("start-intent.json")
        with self.assertRaises(ValueError):self.prepare()
        self.records=deepcopy(old); self.protected.side_effect=RuntimeError("synthetic_remote_unavailable")
        with self.assertRaises(RuntimeError):self.prepare()
        self.assertEqual(self.new,{}); self.qa.assert_not_called()

    def test_reprotected_false_flags_bool_counts_and_foreign_observation_denied(self):
        original=deepcopy(self.records)
        changes=(("running-metadata-observed.json","transport_acceptance",0),
            ("running-metadata-observed.json","provider_sql_authority",True),
            ("running-metadata-observed.json","sql_calls",False),
            ("running-metadata-observed.json","source_manifest",self.current),
            ("running-metadata-observed.json","response_root_keys",["replicas"]),
            ("log-diagnostic.json","controller_acceptance",0),("log-diagnostic.json","complete_parts_retained",True),
            ("log-diagnostic.json","mapping_provenance","automated_capture"),
            ("start-ack.json","execution_id",EXEC_ID+"-foreign"))
        for name,field,value in changes:
            self.records=deepcopy(original); self.records[name][field]=value; self.rebind(name)
            with self.assertRaises(ValueError):c.validate_history(self.records)
        self.assertEqual(self.new,{})

    def test_measured_utc_execution_and_owner_mapping_are_observation_not_capture(self):
        observed=c.observed_execution(self.records,self.arm.actual)
        self.assertEqual(observed["scope_start"],"2026-10-04T13:29:29Z")
        self.assertEqual(observed["scope_end"],"2026-10-04T13:51:29Z")
        self.assertEqual(observed["mapping"],{"log_group":REPLICA,"container_name":CONTAINER,
            "provenance":"protected_owner_observed_replica","original_automated_capture_passed":False})
        self.assertNotEqual(observed["execution"]["actual_template_readback_sha256"],observed["execution"]["validated_requested_template_sha256"])

    def test_owner_replica_exact_ids_types_single_container_and_zero_restart(self):
        running=c.execution(execution("Running"),"Running"); terminal=c.execution(execution(),"Succeeded")
        for field,value in (("id",EXEC_ID+"/replicas/foreign"),("name",REPLICA+"-foreign"),
            ("type","Microsoft.App/replicas"),("runningState","Stopped"),("createdTime","2026-10-04T13:31:29+01:00"),
            ("createdTime","2026-10-04T13:37:00Z"),("extra",True)):
            changed=replica(); changed[field]=value
            with self.assertRaises(ValueError):c.owner_mapping(changed,running,terminal)
        for field,value in (("restartCount",False),("restartCount",1),("ready",1),("started",1),
            ("name","foreign"),("runningState","Stopped"),("image",IMAGE)):
            changed=replica(); changed["containers"][0][field]=value
            with self.assertRaises(ValueError):c.owner_mapping(changed,running,terminal)
        for containers in ([],replica()["containers"]*2):
            changed=replica(); changed["containers"]=containers
            with self.assertRaises(ValueError):c.owner_mapping(changed,running,terminal)

    def test_execution_requires_fixed_identity_supported_status_and_complete_utc_window(self):
        for field,value in (("id",EXEC_ID+"-foreign"),("name","foreign")):
            changed=execution(); changed[field]=value
            with self.assertRaises(ValueError):c.execution(changed,"Succeeded")
        for field,value in (("status","Running"),("startTime","2026-10-04T13:31:29+01:00"),
            ("endTime",None),("endTime","2026-10-04T13:31:29Z"),("endTime","2026-10-04T13:52:00Z")):
            changed=execution(); changed["properties"][field]=value
            with self.assertRaises(ValueError):c.execution(changed,"Succeeded")
        for status in ("Failed","Stopped","Unknown",None,False):
            changed=execution(); changed["properties"]["status"]=status
            with self.assertRaises(ValueError):c.execution(changed,status)

    def test_actual_terminal_or_observed_timestamp_and_template_drift_denied(self):
        original=deepcopy(self.records)
        for value in ("2026-10-04T13:30:00Z","2026-10-04T13:37:00Z","2026-10-04T13:35:28+01:00"):
            self.records=deepcopy(original); self.records["running-metadata-observed.json"]["observed_at_utc"]=value
            with self.assertRaises(ValueError):c.observed_execution(self.records,self.arm.actual)
        self.records=deepcopy(original)
        changed=execution(); changed["properties"]["template"]["containers"][0]["resources"].pop("ephemeralStorage")
        with self.assertRaises(ValueError):c.observed_execution(self.records,changed)
        changed=execution(); changed["properties"]["endTime"]="2026-10-04T13:36:55+00:00"
        with self.assertRaises(ValueError):c.observed_execution(self.records,changed)

    def test_prepare_fresh_qa_second_guards_preserves_historical_records(self):
        raw=self.prepare(); value=body(raw)
        self.assertEqual(self.records,self.before); self.assertEqual(self.saved,["preflight.json"])
        self.assertEqual(value["sources"],self.current); self.assertEqual(len(value["sources"]),707)
        self.assertEqual(value["history_receipts"],{name:raw["protected_receipt"] for name,raw in self.records.items()})
        self.assertEqual(value["local_checks"],checks()); self.qa.assert_called_once_with()
        self.assertEqual(len(self.arm.calls),6)
        for flag in FALSE_FLAGS:self.assertIs(value[flag],False)
        self.assertEqual(value["sql_calls"],0); self.assertIs(type(value["sql_calls"]),int)
        self.assertEqual(value["model_calls"],0)
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.saved,["preflight.json"])

    def test_prepare_wrong_qa_drift_or_live_drift_never_writes_preflight(self):
        for field,value in (("test_count",1977),("test_count",1978.0),("unittest_exit_code",False),
            ("live_sql_tests_passed",True),("financial_or_visual_acceptance",True)):
            changed=checks(); changed[field]=value
            if field=="test_count" and type(value)is int:changed["passed_test_count"]=value
            self.qa.return_value=changed
            with self.assertRaises(ValueError):self.prepare()
            self.assertEqual(self.new,{})
        def drift():
            self.source.return_value={**self.current,"synthetic-source-000.py":"f"*64}
            return checks()
        self.qa.side_effect=drift
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.new,{})
        self.source.return_value=deepcopy(self.current)
        def live_drift():
            self.arm.actual["properties"]["endTime"]="2026-10-04T13:36:55+00:00"
            return checks()
        self.qa.side_effect=live_drift
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.new,{})

    def test_guards_reject_source_runtime_baseline_environment_or_current_job_drift(self):
        for mock,value in ((self.source,{**self.current,"synthetic-source-000.py":"f"*64}),
            (self.runtime,{"changed":True}),(self.baseline,{"job_inventory_complete":False})):
            old=mock.return_value; mock.return_value=value
            with self.assertRaises(ValueError):c.guards(self.records,self.arm)
            mock.return_value=old
        for obj,field,value in ((self.arm.env,"id",ENV_ID+"-foreign"),(self.arm.job,"location","westus")):
            old=obj[field]; obj[field]=value
            with self.assertRaises(ValueError):c.guards(self.records,self.arm)
            obj[field]=old
        self.assertEqual(self.new,{})

    def test_frozen_requires_strict_false_flags_zero_counts_and_current1978_qa(self):
        raw=self.prepare(); preflight=body(raw)
        for field,value in (("provider_sql_authority",0),("owner_runtime_active",True),("sql_calls",False),
            ("history_receipts",{}),("allowed_additions",[]),("local_checks",checks(1963))):
            changed=deepcopy(preflight); changed[field]=value
            with self.assertRaises(ValueError):c.frozen(changed,self.records,self.arm)

    def test_full_real_1025_frames_retained_nine_parts_and_verified_fresh_without_saving(self):
        self.prepare(); result=self.collect(); accepted=body(self.new["acceptance.json"])
        self.assertEqual(result["status"],"complete_owner_observed_canary_transport_retained")
        self.assertEqual(len(self.full_lines),1025); self.assertEqual(len(accepted["parts"]),9)
        self.assertEqual([entry["count"] for entry in accepted["parts"]],[128]*8+[1])
        self.assertEqual(accepted["record_bytes"],4194304); self.assertEqual(accepted["record_sha256"],RECORD_SHA)
        self.assertEqual(accepted["transport_scope"],"protected_owner_observed_replica_only")
        self.assertIs(accepted["original_automated_capture_passed"],False)
        self.assertIs(accepted["protected_complete_record_readback"],True)
        for flag in FALSE_FLAGS:self.assertIs(accepted[flag],False)
        self.assertEqual(accepted["daily_schedule_hkt"],"18:00")
        lines=[]
        for i in range(9):lines.extend(self.new[f"part-{i:03d}.json"]["lines"])
        self.assertEqual(lines,self.full_lines); self.assertEqual(codec.decode_record(lines,codec.Binding(**BINDING)),self.full_record)
        before=deepcopy(self.new); saves=len(self.saved); self.saver.reset_mock()
        result=self.collect(verify_only=True)
        self.assertEqual(result["status"],"independent_owner_observed_transport_readback_verified")
        self.assertEqual(self.new,before); self.assertEqual(len(self.saved),saves); self.saver.assert_not_called()
        self.assertEqual(len(self.query_scopes),2); self.assertEqual(self.records,self.before)

    def test_verify_requires_boolean_mode_missing_parts_or_proof_never_overwrite(self):
        self.prepare()
        for mode in (1,0,None,"verify",[],{}):
            with self.assertRaises(ValueError):self.collect(verify_only=mode)
        with self.assertRaises(ValueError):self.collect(verify_only=True)
        self.assertEqual(self.saved,["preflight.json"])

    def test_partial_local_part_or_unknown_save_ack_is_consumed_not_absent(self):
        raw=self.prepare(); observed=c.observed_execution(self.records,self.arm.actual)
        self.new["part-000.json"]=None
        with self.assertRaises(ValueError):c.retain_parts(self.full_lines,raw,self.records,observed)
        self.assertEqual(self.saved,["preflight.json"])
        self.new.pop("part-000.json")
        def lost(value,name,kind):
            self.new[name]=None; raise RuntimeError("synthetic_lost_write_ack")
        self.saver.side_effect=lost
        with self.assertRaises(RuntimeError):c.retain_parts(self.full_lines,raw,self.records,observed)
        self.assertIn("part-000.json",self.new)
        self.saver.side_effect=self.save
        with self.assertRaises(ValueError):c.retain_parts(self.full_lines,raw,self.records,observed)
        self.assertEqual(self.saved,["preflight.json"])

    def test_all_part_caps_validated_before_first_write_and_indices_strict(self):
        raw=self.prepare(); observed=c.observed_execution(self.records,self.arm.actual)
        for index in (True,False,-1,9,0.0,None):
            with self.assertRaises(ValueError):c.part_value(self.full_lines,index,raw,self.records,observed)
        for lines in (self.full_lines[:-1],tuple(self.full_lines),None):
            with self.assertRaises(ValueError):c.part_value(lines,0,raw,self.records,observed)
        lines=list(self.full_lines); lines[-1]="x"*(2*1024*1024)
        with self.assertRaises(ValueError):c.retain_parts(lines,raw,self.records,observed)
        self.assertEqual(self.saved,["preflight.json"])

    def test_existing_exact_parts_reused_but_immutable_binding_drift_denied(self):
        raw=self.prepare(); observed=c.observed_execution(self.records,self.arm.actual)
        entries=c.retain_parts(self.full_lines,raw,self.records,observed); saves=len(self.saved)
        self.assertEqual(c.retain_parts(self.full_lines,raw,self.records,observed),entries)
        self.assertEqual(len(self.saved),saves)
        original=deepcopy(self.new["part-000.json"])
        self.new["part-000.json"]["mapping"]["provenance"]="automated_capture"; self.rebind("part-000.json",self.new)
        with self.assertRaises(ValueError):c.retain_parts(self.full_lines,raw,self.records,observed)
        self.assertEqual(len(self.saved),saves)
        self.new["part-000.json"]=original
        for field,value in (("index",False),("offset",False),("receipt",{}),("count",127),("name","part-009.json")):
            changed=deepcopy(entries); changed[0][field]=value
            with self.assertRaises(ValueError):c.verify_parts(changed,raw,self.records,observed)

    def test_read_error_partial_or_foreign_stream_withholds_acceptance(self):
        self.prepare()
        for evidence in ({"record":self.full_record,"lines":self.full_lines[:-1],"header_inventory_sha256":"e"*64},
            {"record":self.full_record,"lines":self.full_lines,"header_inventory_sha256":True},
            {"record":{},"lines":self.full_lines,"header_inventory_sha256":"e"*64}):
            with self.assertRaises(ValueError):c.collect(self.arm,lambda _scope,evidence=evidence:evidence)
            self.assertNotIn("acceptance.json",self.new)
        def unavailable(_scope):raise RuntimeError("synthetic_log_read_unavailable")
        with self.assertRaises(RuntimeError):c.collect(self.arm,unavailable)
        self.assertEqual(self.saved,["preflight.json"])

    def test_final_source_guard_drift_after_parts_withholds_acceptance(self):
        self.prepare()
        def changed(scope):
            value=self.query(scope); self.source.return_value={**self.current,"synthetic-source-000.py":"f"*64}
            return value
        with self.assertRaises(ValueError):c.collect(self.arm,changed)
        self.assertNotIn("acceptance.json",self.new)
        self.assertEqual(self.records,self.before)

    def test_reprotected_acceptance_false_flag_or_wire_header_hash_drift_not_trusted(self):
        self.prepare(); self.collect(); original=deepcopy(self.new["acceptance.json"])
        # Unit-isolate the ALREADY independently tested codec/part-reassembly
        # layer for these late scalar/hash comparison denials only. The full
        # real4MiB collect+fresh verify test above remains entirely unpatched.
        # Each attempt still obtains fresh queried evidence, rereads protected
        # acceptance, checks current guards, and may never save a new record.
        def fresh_evidence(query,observed):
            scope=c.prior.logs.CatalogLogScope(codec.Binding(**BINDING),observed["mapping"]["log_group"],
                CONTAINER,observed["scope_start"],observed["scope_end"])
            return query(scope)
        self.saver.reset_mock(); saves=len(self.saved)
        with patch.object(c,"complete_evidence",side_effect=fresh_evidence) as evidence_check, \
                patch.object(c,"verify_parts",return_value=list(self.full_lines)) as parts_check:
            for field,value in (("provider_sql_authority",0),("owner_runtime_active",True),("sql_calls",False),
                ("record_sha256","f"*64),("header_inventory_sha256","f"*64)):
                queries=len(self.query_scopes)
                self.new["acceptance.json"]=deepcopy(original); self.new["acceptance.json"][field]=value
                self.rebind("acceptance.json",self.new)
                with self.assertRaises(ValueError):self.collect(verify_only=True)
                self.assertEqual(len(self.query_scopes),queries+1)
                self.saver.assert_not_called(); self.assertEqual(len(self.saved),saves)
            self.assertEqual(evidence_check.call_count,5)
            self.assertEqual(parts_check.call_count,5)
            for call in parts_check.call_args_list:
                self.assertEqual(call.args[0],original["parts"])
        self.assertEqual(self.records,self.before)


if __name__=="__main__":
    unittest.main()
