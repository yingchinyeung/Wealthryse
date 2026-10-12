"""Independent OFFLINE controller tests; no cloud/auth/protection proof."""
from copy import deepcopy
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import qualify_owner_task_isolated_host as host


BASE = "/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/"
SERVER_ID = BASE + "Microsoft.DBforPostgreSQL/flexibleServers/ibkrv2stg-ownertrial-20261004"
VAULT = "https://ibkrv2stg-auhecwc4itaoo.vault.azure.net"
SECRET = "ownertrial-20261004-admin-url"
SECRET_ID = VAULT + "/secrets/" + SECRET + "/" + "a" * 32
SOURCE = {"independent_frozen_source.py": "a" * 64}
RUNTIME = {"python":"3.12.10", "versions":{"azure-core":"1.35.0", "azure-storage-blob":"12.26.0",
    "azure-identity":"1.24.0", "requests":"2.32.5"}, "requirements_lock_sha256":"b"*64}
BASELINE = {"job_inventory_complete": True, "other_jobs": {f"job_{i:02d}":{"image":"fixed"} for i in range(34)}}
SUBNET_ID = BASE + "Microsoft.Network/virtualNetworks/ibkrv2stg-vnet/subnets/postgres"
DNS_ID = BASE + "Microsoft.Network/privateDnsZones/ibkrv2stg.postgres.database.azure.com"
ENV_ID = BASE + "Microsoft.App/managedEnvironments/ibkrv2stg-env"
VNET_ID = BASE + "Microsoft.Network/virtualNetworks/ibkrv2stg-vnet"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def definition():
    return {"location":"eastasia", "sku":{"name":"Standard_B1ms", "tier":"Burstable"},
        "tags":{"application":"ibkr-portfolio-platform", "purpose":"owner-codex-isolated-trial",
            "marker":"wealthryse-owner-task-isolated-059-20261004-v1",
            "profile":"owner-codex-isolated-059-20261004-v1",
            "sourceScope":digest({"server_resource_id":SERVER_ID, "database":"owner_codex_isolated_20261004"})},
        "properties":{"createMode":"Default", "version":"16", "administratorLogin":"ibkr_owner_trial_admin_20261004",
            "storage":{"storageSizeGB":32,"autoGrow":"Disabled"},
            "backup":{"backupRetentionDays":7,"geoRedundantBackup":"Disabled"},
            "highAvailability":{"mode":"Disabled"},
            "authConfig":{"passwordAuth":"Enabled","activeDirectoryAuth":"Disabled"},
            "network":{"publicNetworkAccess":"Disabled","delegatedSubnetResourceId":SUBNET_ID,
                "privateDnsZoneArmResourceId":DNS_ID}}}


def server(state="Ready"):
    result = definition()
    result.update(id=SERVER_ID, name="ibkrv2stg-ownertrial-20261004", type="Microsoft.DBforPostgreSQL/flexibleServers")
    result["properties"].update(state=state, fullyQualifiedDomainName="ibkrv2stg-ownertrial-20261004.postgres.database.azure.com")
    return result


def dependency_responses():
    return {
        SUBNET_ID:{"id":SUBNET_ID,"properties":{"provisioningState":"Succeeded","addressPrefix":"10.50.2.0/24",
            "delegations":[{"properties":{"serviceName":"Microsoft.DBforPostgreSQL/flexibleServers"}}],
            "networkSecurityGroup":{"id":"fixed_nsg"},"routeTable":None}},
        DNS_ID:{"id":DNS_ID},
        ENV_ID:{"id":ENV_ID,"properties":{"provisioningState":"Succeeded",
            "vnetConfiguration":{"infrastructureSubnetId":VNET_ID+"/subnets/container-apps"}}},
        DNS_ID+"/virtualNetworkLinks":{"value":[{"properties":{"virtualNetwork":{"id":VNET_ID},
            "virtualNetworkLinkState":"Completed","provisioningState":"Succeeded","registrationEnabled":False}}]},
    }


class Evidence:
    """Independent synthetic receipt oracle; not real immutable storage."""
    def __init__(self):
        self.records, self.protected_checks = {}, []

    def save(self, value, name):
        if name in self.records:
            raise ValueError("synthetic_record_already_exists")
        raw = deepcopy(value)
        raw["protected_receipt"] = {"sha256":digest(raw),"kind":host.KINDS[name],"blob":"synthetic/"+name}
        self.records[name] = deepcopy(raw)
        return raw

    def protected(self, raw, kind):
        self.protected_checks.append(kind)
        receipt = raw.get("protected_receipt")
        body = {k:v for k,v in raw.items() if k != "protected_receipt"}
        if receipt != {"sha256":digest(body),"kind":kind,"blob":"synthetic/"+
                       next((name for name,k in host.KINDS.items() if k == kind), "unregistered")}:
            raise ValueError("synthetic_protected_binding_mismatch")
        return deepcopy(body)

    def load(self, name):
        raw = deepcopy(self.records[name])
        return raw, self.protected(raw, host.KINDS[name])

    def rebound(self, name):
        raw = self.records.pop(name)
        return self.save({k:v for k,v in raw.items() if k != "protected_receipt"}, name)


class MemoryPath:
    def __init__(self, evidence, name=None):
        self.evidence, self.name = evidence, name

    def __truediv__(self, name):
        return MemoryPath(self.evidence, name)

    def exists(self):
        return self.name in self.evidence.records

    def mkdir(self, **_kwargs):
        pass


class Client:
    def __init__(self):
        self.calls, self.dep = [], dependency_responses()
        self.secret_url, self.secret_id, self.arm = None, SECRET_ID, None
        self.secret_fault, self.server_fault = None, None
        self.server_body = None

    def call(self, method, url, *, body=None, missing=False):
        self.calls.append((method, url))
        if method == "PUT" and url == VAULT+"/secrets/"+SECRET+"?api-version=7.4":
            self.secret_url = body["value"]
            if self.secret_fault:
                return self.secret_fault(body)
            return {"id":self.secret_id,"value":self.secret_url}, 200
        if method == "PUT" and url == "https://management.azure.com"+SERVER_ID+"?api-version=2024-08-01":
            self.server_body = body
            self.arm = server("Creating")
            if self.server_fault:
                return self.server_fault(body)
            return {"status":"accepted"}, 202
        if method != "GET" or body is not None:
            raise AssertionError("unregistered synthetic client method")
        if url.startswith("https://management.azure.com"):
            path = url.removeprefix("https://management.azure.com").split("?",1)[0]
            if path == SERVER_ID:
                return deepcopy(self.arm), 200 if self.arm is not None else 404
            if path in self.dep:
                return deepcopy(self.dep[path]), 200
        if url.startswith(VAULT+"/secrets/"+SECRET+"/versions?"):
            return {"value":[] if self.secret_url is None else [{"id":self.secret_id}]}, 200
        if url.startswith(VAULT+"/secrets?"):
            return {"value":[] if self.secret_url is None else [{"id":VAULT+"/secrets/"+SECRET}]}, 200
        if url.startswith(VAULT+"/deletedsecrets?"):
            return {"value":[]}, 200
        raise AssertionError("unregistered synthetic read endpoint")

    def puts(self):
        return [url for method,url in self.calls if method == "PUT"]


class Response:
    def __init__(self, status=200, value=None, content=None):
        self.status_code = status
        self.value = {} if value is None else value
        self.content = json.dumps(self.value).encode() if content is None else content
        self.closed = False

    def json(self):
        return deepcopy(self.value)

    def iter_content(self, chunk_size):
        for offset in range(0,len(self.content),chunk_size):
            yield self.content[offset:offset+chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Credential:
    def __init__(self):
        self.calls = []

    def get_token(self, audience):
        self.calls.append(audience)
        return SimpleNamespace(token="SYNTHETIC_TOKEN", expires_on=5000)


class IsolatedHostTests(unittest.TestCase):
    def setUp(self):
        self.evidence, self.client = Evidence(), Client()
        self.reservations = {}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(host,"FOLDER",MemoryPath(self.evidence)))
        self.stack.enter_context(patch.object(host.deploy,"FOLDER",MemoryPath(self.evidence)))
        self.stack.enter_context(patch.object(host,"sources",return_value=deepcopy(SOURCE)))
        self.stack.enter_context(patch.object(host,"runtime_descriptor",return_value=deepcopy(RUNTIME)))
        self.stack.enter_context(patch.object(host.deploy,"baseline",return_value=deepcopy(BASELINE)))
        self.stack.enter_context(patch.object(host,"save",side_effect=self.evidence.save))
        self.stack.enter_context(patch.object(host,"load",side_effect=self.evidence.load))
        self.stack.enter_context(patch.object(host.deploy,"protected",side_effect=self.evidence.protected))
        self.reserve = self.stack.enter_context(patch.object(host,"reserve",side_effect=self.synthetic_reserve))
        self.stack.enter_context(patch.object(host,"reservation_readback",side_effect=self.synthetic_reservation_readback))
        self.stack.enter_context(patch.object(host.secrets,"token_urlsafe",return_value="SYNTHETIC_TEST_RANDOM_MATERIAL"))
        self.prepared = dict(status="prepared_new_host_no_sql", sources=deepcopy(SOURCE), runtime=deepcopy(RUNTIME), definition=definition(),
            baseline=deepcopy(BASELINE), dependencies=host.dependencies(self.client),
            registration_sha256=host.TARGET.registration_sha256,
            physical_scope_sha256=digest({"server_resource_id":SERVER_ID,"database":"owner_codex_isolated_20261004"}),
            local_checks={"status":"synthetic_passed"}, sql_calls=0, model_calls=0,
            catalog_worker_deployed=False,new_identity_or_rbac=False,automatic_retry=False)
        self.prepared_raw = self.evidence.save(self.prepared,"preflight.json")

    def synthetic_reserve(self,value):
        payload = json.dumps(value,sort_keys=True,separators=(",", ":"),allow_nan=False).encode()
        sha256 = hashlib.sha256(payload).hexdigest()
        self.reservations[sha256] = payload
        return {"blob":"probes/owner-codex-isolated-host-20261004/provision-reservation.json",
            "sha256":sha256,"version_id":"2026-10-04T12:34:56.1234567Z","etag":'"0xABC123"',
            "protected_readback_verified":True}

    def synthetic_reservation_readback(self,receipt,payload):
        expected = {"blob":"probes/owner-codex-isolated-host-20261004/provision-reservation.json",
            "sha256":hashlib.sha256(payload).hexdigest(),"version_id":"2026-10-04T12:34:56.1234567Z",
            "etag":'"0xABC123"',"protected_readback_verified":True}
        if receipt != expected or type(receipt.get("protected_readback_verified")) is not bool \
                or self.reservations.get(expected["sha256"]) != payload:
            raise ValueError("synthetic_reservation_binding_mismatch")

    def error(self, code, operation, *args, **kwargs):
        with self.assertRaisesRegex(ValueError,"^"+code+"$"):
            operation(*args, **kwargs)

    def provision(self):
        return host.provision(self.prepared_raw, deepcopy(self.prepared), self.client)

    def run_main(self, stage):
        output = io.StringIO()
        with patch.object(host,"checked_owner"), patch.object(host,"Transport",return_value=self.client), \
                patch.object(sys,"argv",["qualifier",stage]), redirect_stdout(output):
            host.main()
        return json.loads(output.getvalue())

    def test_public_definition_exact_private_blank_no_credentials_or_sources(self):
        self.assertEqual(host.public_definition(),definition())
        encoded = json.dumps(host.public_definition())
        for forbidden in ("administratorLoginPassword","sourceServerResourceId","pointInTimeUTC","Replica","postgresql://"):
            self.assertNotIn(forbidden,encoded)
        self.assertNotIn("identity",host.public_definition())
        self.assertEqual(host.public_definition()["properties"]["createMode"],"Default")

    def test_checked_owner_requires_exact_owner_user_subscription(self):
        valid = {"subscription":"3956e821-cee4-403f-8708-e84e2ed490eb","user":"anthony.ying@wealthryse.ai","type":"user"}
        with patch.object(host.deploy,"az",return_value=valid):
            host.checked_owner()
        for field,value in (("subscription","wrong"),("user","other@example.invalid"),("type","servicePrincipal")):
            bad = deepcopy(valid); bad[field] = value
            with patch.object(host.deploy,"az",return_value=bad):
                self.error("isolated_host_registered_owner_required",host.checked_owner)

    def test_two_exact_puts_only_and_password_not_in_evidence(self):
        result = self.provision()
        self.assertEqual(self.client.puts(),[VAULT+"/secrets/"+SECRET+"?api-version=7.4",
            "https://management.azure.com"+SERVER_ID+"?api-version=2024-08-01"])
        self.assertIs(result["sql_acceptance"],False)
        self.assertIs(result["owner_runtime_active"],False)
        self.assertIs(result["six_specialist_runtimes_active"],False)
        serialized = json.dumps(self.evidence.records)
        self.assertNotIn("SYNTHETIC_TEST_RANDOM_MATERIAL",serialized)
        self.assertNotIn("postgresql://",serialized)
        self.assertNotIn("administratorLoginPassword",serialized)
        self.assertNotIn("administratorLoginPassword",self.client.server_body["properties"])
        self.assertIn("sslmode=verify-full",self.client.secret_url)
        self.assertIn("sslrootcert=",self.client.secret_url)

    def test_each_consumed_local_stage_refuses_rerun_before_reads_or_writes(self):
        for name in ("provision-intent.json","secret-intent.json","secret-ack.json","server-intent.json","server-ack.json"):
            with self.subTest(stage=name):
                self.evidence.records[name] = {}
                reads = len(self.client.calls)
                self.error("isolated_host_attempt_consumed_inspect_no_redispatch",self.provision)
                self.assertEqual(len(self.client.calls),reads)
                del self.evidence.records[name]
        self.reserve.assert_not_called()

    def test_existing_server_refuses_without_side_effects(self):
        self.client.arm = server()
        self.error("isolated_host_server_already_exists_inspect_only",self.provision)
        self.assertEqual(self.client.puts(),[])
        self.assertEqual(set(self.evidence.records),{"preflight.json"})

    def test_existing_or_soft_deleted_secret_refuses_without_writes(self):
        self.client.secret_url = "synthetic existing value not read"
        self.error("isolated_host_new_secret_name_already_reserved",self.provision)
        self.assertEqual(self.client.puts(),[])
        self.assertEqual(set(self.evidence.records),{"preflight.json"})
        client = SimpleNamespace(call=lambda *_a,**_k:({"value":[{"id":VAULT+"/secrets/"+SECRET.upper()}]},200))
        self.error("isolated_host_new_secret_name_already_reserved",host.require_secret_absent,client)

    def test_source_baseline_and_network_drift_deny_before_intent(self):
        for context in (patch.object(host,"sources",return_value={"different":"c"*64}),
                        patch.object(host.deploy,"baseline",return_value={"other":"baseline"})):
            with context:
                with self.assertRaises(ValueError):
                    self.provision()
            self.assertEqual(self.client.puts(),[])
            self.assertEqual(set(self.evidence.records),{"preflight.json"})
        self.client.dep[SUBNET_ID]["properties"]["networkSecurityGroup"] = {"id":"changed"}
        self.error("isolated_host_source_or_existing_configuration_drift",self.provision)
        self.assertEqual(self.client.puts(),[])

    def test_runtime_and_lock_drift_denies_before_effects(self):
        for field,changed in (("python","3.13.0"),("versions",{"requests":"unexpected"}),
                              ("requirements_lock_sha256","c"*64)):
            with self.subTest(field=field):
                drift = deepcopy(RUNTIME); drift[field] = changed
                with patch.object(host,"runtime_descriptor",return_value=drift):
                    self.error("isolated_host_exact_protected_preflight_required",self.provision)
                self.assertEqual(self.client.puts(),[])
                self.assertEqual(set(self.evidence.records),{"preflight.json"})
        self.reserve.assert_not_called()

    def test_prepared_target_definition_complete_inventory_must_match(self):
        for field, changed in (("status","other"),("registration_sha256","0"*64),
            ("physical_scope_sha256","0"*64),("definition",{}),("sources",{})):
            bad = deepcopy(self.prepared); bad[field] = changed
            self.error("isolated_host_exact_protected_preflight_required",host.verify_prepared,bad)
        for changed in (False,1,None):
            bad = deepcopy(self.prepared); bad["baseline"]["job_inventory_complete"] = changed
            self.error("isolated_host_exact_protected_preflight_required",host.verify_prepared,bad)
        bad = deepcopy(self.prepared); bad["baseline"]["other_jobs"].pop("job_00")
        self.error("isolated_host_exact_protected_preflight_required",host.verify_prepared,bad)

    def test_reservation_unknown_ack_consumes_local_attempt_without_any_put(self):
        self.reserve.side_effect = RuntimeError("synthetic lost reservation response")
        with self.assertRaises(RuntimeError):
            self.provision()
        self.assertEqual(self.client.puts(),[])
        self.assertEqual(set(self.evidence.records),{"preflight.json","provision-intent.json"})
        self.error("isolated_host_attempt_consumed_inspect_no_redispatch",self.provision)
        self.assertEqual(self.reserve.call_count,1)

    def test_secret_unknown_ack_or_bad_version_blocks_server_and_no_retry(self):
        def lost(_body):
            raise RuntimeError("synthetic lost secret response")
        self.client.secret_fault = lost
        with self.assertRaises(RuntimeError):
            self.provision()
        self.assertEqual(len(self.client.puts()),1)
        self.assertNotIn("server-intent.json",self.evidence.records)
        self.error("isolated_host_attempt_consumed_inspect_no_redispatch",self.provision)
        self.assertEqual(len(self.client.puts()),1)
        self.assertNotIn("SYNTHETIC_TEST_RANDOM_MATERIAL",json.dumps(self.evidence.records))

    def test_secret_reply_wrong_id_or_value_is_not_acceptance(self):
        for bad in ({"id":VAULT+"/secrets/other/"+"a"*32,"value":"wrong"},
                    {"id":SECRET_ID,"value":"wrong"}):
            evidence = Evidence()
            with patch.object(host,"FOLDER",MemoryPath(evidence)), patch.object(host,"save",side_effect=evidence.save), \
                    patch.object(host.deploy,"protected",side_effect=evidence.protected):
                client = Client(); client.secret_fault = lambda _body: (deepcopy(bad),200)
                self.error("isolated_host_new_secret_ack_unverified",host.provision,self.prepared_raw,self.prepared,client)
                self.assertEqual(len(client.puts()),1)
                self.assertNotIn("secret-ack.json",evidence.records)

    def test_secret_version_drift_prevents_server_put(self):
        call = self.client.call
        def drift(method,url,**kwargs):
            if "/versions?" in url:
                return {"value":[{"id":SECRET_ID},{"id":VAULT+"/secrets/"+SECRET+"/"+"b"*32}]},200
            return call(method,url,**kwargs)
        self.client.call = drift
        self.error("isolated_host_new_secret_version_drift",self.provision)
        self.assertEqual(len(self.client.puts()),1)

    def test_server_unknown_ack_no_retry_and_password_removed(self):
        self.client.server_fault = lambda _body: (_ for _ in ()).throw(RuntimeError("synthetic lost server response"))
        with self.assertRaises(RuntimeError):
            self.provision()
        self.assertEqual(len(self.client.puts()),2)
        self.assertNotIn("server-ack.json",self.evidence.records)
        self.assertNotIn("administratorLoginPassword",self.client.server_body["properties"])
        self.assertNotIn("SYNTHETIC_TEST_RANDOM_MATERIAL",json.dumps(self.evidence.records))
        self.error("isolated_host_attempt_consumed_inspect_no_redispatch",self.provision)
        self.assertEqual(len(self.client.puts()),2)

    def test_drift_after_reservation_but_before_secret_blocks_write(self):
        def reservation(_value):
            self.client.dep[SUBNET_ID]["properties"]["routeTable"] = {"id":"changed"}
            return {"sha256":"b"*64,"protected_readback_verified":True}
        self.reserve.side_effect = reservation
        self.error("isolated_host_source_or_existing_configuration_drift",self.provision)
        self.assertEqual(self.client.puts(),[])
        self.assertEqual(set(self.evidence.records),{"preflight.json","provision-intent.json"})

    def test_drift_after_secret_ack_blocks_server_without_secret_regeneration(self):
        save = self.evidence.save
        def drift(value,name):
            result = save(value,name)
            if name == "secret-ack.json":
                self.client.dep[SUBNET_ID]["properties"]["routeTable"] = {"id":"changed"}
            return result
        with patch.object(host,"save",side_effect=drift):
            self.error("isolated_host_source_or_existing_configuration_drift",self.provision)
        self.assertEqual(len(self.client.puts()),1)
        self.assertNotIn("server-intent.json",self.evidence.records)

    def test_observe_is_read_only_and_does_not_upgrade_false_acceptance_claims(self):
        self.client.arm, self.client.secret_url = server(), "synthetic secret present metadata only"
        result = host.observe(self.client)
        self.assertIs(result["redispatch_authorized"],False)
        self.assertIs(result["sql_acceptance"],False)
        self.assertIs(result["server"]["fresh_restricted_login_verified"],False)
        self.assertIs(result["server"]["blank_sql_inventory_verified"],False)
        self.assertIs(result["server"]["original_default_creation_verified"],False)
        self.assertEqual(self.client.puts(),[])
        for method,url in self.client.calls:
            self.assertEqual(method,"GET")
            self.assertFalse(url.startswith(SECRET_ID))

    def test_observe_registration_tag_drift_is_rejected(self):
        self.client.arm = server()
        self.client.arm["tags"]["sourceScope"] = "0"*64
        self.error("isolated_host_registration_tags_mismatch",host.observe,self.client)
        self.assertEqual(self.client.puts(),[])

    def test_independent_verify_binds_receipts_and_keeps_sql_pending(self):
        self.provision()
        self.client.arm = server()
        result = self.run_main("verify")
        self.assertEqual(result["status"],"new_private_host_verified_sql_pending")
        record = self.evidence.records["host-acceptance.json"]
        for key in ("blank_sql_inventory_verified","fresh_restricted_login_verified","production_migration_059_installed",
                    "owner_runtime_active","six_specialist_runtimes_active","ibkr_source_writes"):
            self.assertIs(record[key],False)
        self.assertIs(record["original_default_request_verified"],True)
        puts = len(self.client.puts())
        again = self.run_main("verify")
        self.assertEqual(again["status"],"independent_host_readback_verified")
        self.assertEqual(len(self.client.puts()),puts)

    def test_verify_wrong_bound_receipt_rejected_before_acceptance(self):
        self.provision(); self.client.arm = server()
        self.evidence.records["server-ack.json"]["server_intent_receipt"] = {"sha256":"0"*64}
        self.evidence.rebound("server-ack.json")  # Valid synthetic archive, wrong logical binding.
        self.error("isolated_host_server_ack_contract_invalid",self.run_main,"verify")
        self.assertNotIn("host-acceptance.json",self.evidence.records)

    def test_verify_receipt_bytes_mismatch_rejected_by_independent_oracle(self):
        self.provision(); self.client.arm = server()
        self.evidence.records["server-ack.json"]["status"] = "tampered without rebind"
        self.error("synthetic_protected_binding_mismatch",self.run_main,"verify")
        self.assertNotIn("host-acceptance.json",self.evidence.records)

    def test_verify_not_ready_or_version_drift_is_inspect_only(self):
        self.provision()
        self.error("isolated_host_not_ready_or_secret_drift_inspect_only",self.run_main,"verify")
        self.client.arm = server(); self.client.secret_id = VAULT+"/secrets/"+SECRET+"/"+"b"*32
        self.error("isolated_host_not_ready_or_secret_drift_inspect_only",self.run_main,"verify")
        self.assertNotIn("host-acceptance.json",self.evidence.records)

    def test_private_dependencies_reject_wrong_ids_delegation_links_and_pagination(self):
        changes = ((SUBNET_ID,"id","wrong"), (DNS_ID,"id","wrong"), (ENV_ID,"id","wrong"),
            (DNS_ID+"/virtualNetworkLinks","nextLink","https://other.example/next"))
        for resource,key,changed in changes:
            client = Client(); client.dep[resource][key] = changed
            self.error("isolated_host_private_dependencies_unverified",host.dependencies,client)
        client = Client(); client.dep[SUBNET_ID]["properties"]["delegations"] = []
        self.error("isolated_host_private_dependencies_unverified",host.dependencies,client)
        client = Client(); client.dep[DNS_ID+"/virtualNetworkLinks"]["value"][0]["properties"]["registrationEnabled"] = True
        self.error("isolated_host_private_dependencies_unverified",host.dependencies,client)

    def test_dependency_malformed_termination_or_rows_fail_closed(self):
        for changed in (False, [], {}, 0):
            client = Client(); client.dep[DNS_ID+"/virtualNetworkLinks"]["nextLink"] = changed
            self.error("isolated_host_private_dependencies_unverified",host.dependencies,client)
        client = Client(); client.dep[DNS_ID+"/virtualNetworkLinks"]["value"] = [None]
        self.error("isolated_host_private_dependencies_unverified",host.dependencies,client)

    def test_validate_chain_rejects_wrong_target_unknown_fields_and_bool_aliases(self):
        self.provision()
        good = deepcopy(self.evidence.records)
        cases = (
            ("provision-intent.json","sql_calls",False,"provision_intent"),
            ("provision-intent.json","automatic_retry",0,"provision_intent"),
            ("provision-intent.json","physical_scope_sha256","0"*64,"provision_intent"),
            ("provision-intent.json","extra","untrusted","provision_intent"),
            ("secret-intent.json","secret_name","old-secret","secret_intent"),
            ("secret-intent.json","credential_value_retained",0,"secret_intent"),
            ("secret-ack.json","http_status",200.0,"secret_ack_scope"),
            ("secret-ack.json","credentials_retained",0,"secret_ack"),
            ("server-intent.json","source_or_restore_inputs",0,"server_intent"),
            ("server-intent.json","sources",{},"server_intent"),
            ("server-ack.json","http_status",202.0,"server_ack_status"),
            ("server-ack.json","sql_acceptance",0,"server_ack"),
            ("server-ack.json","server_resource_id",SERVER_ID+"-old","server_ack"),
        )
        for name,field,changed,code in cases:
            self.evidence.records = deepcopy(good)
            self.evidence.records[name][field] = changed
            self.evidence.rebound(name)
            self.error("isolated_host_"+code+"_contract_invalid" if code not in ("secret_ack_scope","server_ack_status")
                else "isolated_host_"+code+"_invalid",host.validate_chain,
                self.prepared_raw,self.prepared,self.evidence.records)

    def test_validate_chain_rechecks_actual_reservation_bytes_not_echo(self):
        self.provision()
        self.reservations.clear()
        self.error("synthetic_reservation_binding_mismatch",host.validate_chain,
            self.prepared_raw,self.prepared,self.evidence.records)

    def test_inspect_absent_server_does_not_grant_or_dispatch(self):
        result = host.observe(self.client)
        self.assertIs(result["server_exists"],False)
        self.assertIsNone(result["server"])
        self.assertEqual(result["new_secret_versions"],[])
        self.assertIs(result["redispatch_authorized"],False)
        self.assertEqual(self.client.puts(),[])

    def test_verify_missing_server_cannot_become_acceptance(self):
        self.provision(); self.client.arm = None
        self.error("isolated_host_not_ready_or_secret_drift_inspect_only",self.run_main,"verify")
        self.assertNotIn("host-acceptance.json",self.evidence.records)


class TransportAndInventoryTests(unittest.TestCase):
    def transport(self,responses):
        session,credential = Session(responses),Credential()
        return host.Transport(credential,session),session,credential

    def test_exact_write_endpoints_timeout_redirect_and_token_audience_cache(self):
        responses = [Response(),Response(),Response(),Response()]
        transport,session,credential = self.transport(responses)
        with patch.object(host.time,"time",return_value=1000):
            transport.call("PUT","https://management.azure.com"+SERVER_ID+"?api-version=2024-08-01",body={})
            transport.call("GET","https://management.azure.com"+SUBNET_ID+"?api-version=2024-05-01")
            transport.call("PUT",VAULT+"/secrets/"+SECRET+"?api-version=7.4",body={})
            transport.call("GET",VAULT+"/secrets?api-version=7.4&maxresults=25")
        self.assertEqual(credential.calls,["https://management.azure.com/.default","https://vault.azure.net/.default"])
        for _args,kwargs in session.calls:
            self.assertEqual(kwargs["timeout"],(5,20))
            self.assertIs(kwargs["allow_redirects"],False)
        self.assertTrue(all(response.closed for response in responses))

    def test_expired_audience_token_refreshed_without_cross_audience_reuse(self):
        transport,session,credential = self.transport([Response(),Response()])
        with patch.object(host.time,"time",return_value=1000):
            transport.call("GET",VAULT+"/secrets?api-version=7.4&maxresults=25")
        transport.tokens["https://vault.azure.net/.default"].expires_on = 1050
        with patch.object(host.time,"time",return_value=1000):
            transport.call("GET",VAULT+"/secrets?api-version=7.4&maxresults=25")
        self.assertEqual(credential.calls,["https://vault.azure.net/.default"]*2)
        self.assertEqual(len(session.calls),2)

    def test_nonfixed_put_method_url_or_body_denied_before_request(self):
        transport,session,credential = self.transport([])
        requests = (("PUT","https://management.azure.com"+SERVER_ID+"-old?api-version=2024-08-01",{}),
            ("PUT","https://management.azure.com"+SERVER_ID+"?api-version=2022-12-01",{}),
            ("PUT",VAULT+"/secrets/other?api-version=7.4",{}),
            ("GET",VAULT+"/secrets?api-version=7.4",{}),
            ("DELETE","https://management.azure.com"+SERVER_ID+"?api-version=2024-08-01",None),
            ("GET","https://management.azure.com@other.invalid/path",None),
            ("GET","http://management.azure.com/path",None),
            ("GET","https://management.azure.com/path#fragment",None),
            ("GET","https://management.azure.com:443/path",None))
        for method,url,body in requests:
            with self.assertRaises(ValueError):
                transport.call(method,url,body=body)
        self.assertEqual(session.calls,[])
        self.assertEqual(credential.calls,[])

    def test_redirect_failure_timeout_oversize_and_bad_json_never_retry(self):
        for response in (Response(302),Response(503),Response(200,[]),Response(200,content=b"x"*(host.MAX_BYTES+1)),
                         RuntimeError("SYNTHETIC_PROVIDER_SECRET_TEXT")):
            transport,session,_credential = self.transport([response])
            with self.assertRaisesRegex(RuntimeError,"^isolated_host_http_outcome_requires_inspection$"):
                transport.call("GET","https://management.azure.com"+SERVER_ID+"?api-version=2024-08-01")
            self.assertEqual(len(session.calls),1)
            if isinstance(response,Response):
                self.assertTrue(response.closed)

    def test_missing_is_only_exact_get_404(self):
        response = Response(404)
        transport,session,_credential = self.transport([response])
        self.assertEqual(transport.call("GET","https://management.azure.com"+SERVER_ID+"?api-version=2024-08-01",missing=True),(None,404))
        self.assertTrue(response.closed)
        transport,session,_credential = self.transport([Response(404)])
        with self.assertRaises(RuntimeError):
            transport.call("PUT","https://management.azure.com"+SERVER_ID+"?api-version=2024-08-01",body={},missing=True)

    def test_endpoint_specific_http_statuses_not_generic_success(self):
        cases = (("GET",VAULT+"/secrets?api-version=7.4&maxresults=25",202,None),
            ("GET","https://management.azure.com"+SERVER_ID+"?api-version=2024-08-01",201,None),
            ("PUT",VAULT+"/secrets/"+SECRET+"?api-version=7.4",202,{}),
            ("PUT","https://management.azure.com"+SERVER_ID+"?api-version=2024-08-01",204,{}))
        for method,url,status,body in cases:
            transport,session,_credential = self.transport([Response(status)])
            with self.assertRaisesRegex(RuntimeError,"^isolated_host_http_outcome_requires_inspection$"):
                transport.call(method,url,body=body)
            self.assertEqual(len(session.calls),1)

    def pages(self,values):
        calls = []
        def call(method,url,**kwargs):
            calls.append((method,url,kwargs))
            return values.pop(0),200
        return SimpleNamespace(call=call),calls

    def test_inventory_pages_metadata_only_exact_continuation(self):
        next_url = VAULT+"/secrets?api-version=7.4&maxresults=25&$skiptoken=next"
        client,calls = self.pages([{"value":[{"id":VAULT+"/secrets/first"}],"nextLink":next_url},
            {"value":[{"id":VAULT+"/secrets/second/"+"b"*32}]}])
        self.assertEqual(host.secret_inventory(client),[VAULT+"/secrets/first",VAULT+"/secrets/second/"+"b"*32])
        self.assertEqual([method for method,_url,_kwargs in calls],["GET","GET"])
        self.assertEqual(calls[1][1],next_url)
        self.assertTrue(all("/versions" not in url and not url.startswith(SECRET_ID) for _method,url,_kwargs in calls))

    def test_exact_deletedsecret_default_port_is_normalized_before_transport(self):
        opaque_token = "a"*296
        continuation = VAULT+":443/deletedsecrets?api-version=7.4&maxresults=25&$skiptoken="+opaque_token
        response1 = Response(value={"value":[{"id":VAULT+"/secrets/first"}],"nextLink":continuation})
        response2 = Response(value={"value":[{"id":VAULT+"/secrets/second"}]})
        transport,session,credential = self.transport([response1,response2])
        with patch.object(host.time,"time",return_value=1000):
            result = host.secret_inventory(transport,"deletedsecrets")
        self.assertEqual(result,[VAULT+"/secrets/first",VAULT+"/secrets/second"])
        self.assertEqual([args for args,_kwargs in session.calls],[("GET",VAULT+"/deletedsecrets?api-version=7.4&maxresults=25"),
            ("GET",VAULT+"/deletedsecrets?api-version=7.4&maxresults=25&$skiptoken="+opaque_token)])
        self.assertEqual(credential.calls,["https://vault.azure.net/.default"])
        self.assertTrue(response1.closed and response2.closed)

    def test_continuation_port_compatibility_never_allows_foreign_scope(self):
        links = (VAULT+":444/deletedsecrets?api-version=7.4&$skiptoken=x",
            VAULT+":0443/deletedsecrets?api-version=7.4&$skiptoken=x",
            VAULT+":443/secrets?api-version=7.4&$skiptoken=x",
            VAULT+":443/deletedsecrets/other?api-version=7.4&$skiptoken=x",
            VAULT.replace("https://","https://user@")+":443/deletedsecrets?api-version=7.4&$skiptoken=x",
            VAULT.replace("https://","https://user:password@")+":443/deletedsecrets?api-version=7.4&$skiptoken=x",
            "https://other.invalid:443/deletedsecrets?api-version=7.4&$skiptoken=x")
        for link in links:
            with self.subTest(link=link):
                client,calls = self.pages([{"value":[],"nextLink":link}])
                with self.assertRaisesRegex(ValueError,"^isolated_host_secret_continuation_invalid$"):
                    host.secret_inventory(client,"deletedsecrets")
                self.assertEqual(len(calls),1)

    def test_global_transport_still_refuses_default_port_value_reads_and_puts(self):
        transport,session,credential = self.transport([])
        for method,path in (("GET","/deletedsecrets?api-version=7.4&maxresults=25"),
                            ("GET","/secrets/"+SECRET+"?api-version=7.4"),
                            ("PUT","/secrets/"+SECRET+"?api-version=7.4")):
            with self.assertRaisesRegex(ValueError,"^isolated_host_unregistered_endpoint$"):
                transport.call(method,VAULT+":443"+path,body={} if method == "PUT" else None)
        self.assertEqual(session.calls,[])
        self.assertEqual(credential.calls,[])

    def test_inventory_duplicates_shapes_foreign_host_and_bad_ids_denied(self):
        bad_pages = ({"value":[{"id":VAULT+"/secrets/a"},{"id":VAULT+"/secrets/a"}]},
            {"value":[]*0,"nextLink":False}, {"value":{}}, {"value":[{}]},
            {"value":[{"id":"https://evil.invalid/secrets/a"}]},
            {"value":[{"id":VAULT+"/secrets/a?credential=secret"}]},
            {"value":[{"id":VAULT+"/secrets/a#fragment"}]},
            {"value":[{"id":VAULT+"/secrets/"}]}, {"value":[{"id":VAULT+"/secrets/a/b/c"}]},
            {"value":[{"id":VAULT+"/secrets/a"}]*26})
        for page in bad_pages:
            client,_calls = self.pages([deepcopy(page)])
            with self.assertRaises(ValueError):
                host.secret_inventory(client)

    def test_inventory_malicious_continuation_and_duplicate_query_denied(self):
        links = ("https://evil.invalid/secrets?api-version=7.4&$skiptoken=x",
            VAULT+"/deletedsecrets?api-version=7.4&$skiptoken=x",
            VAULT+"/secrets?api-version=7.3&$skiptoken=x",
            VAULT+"/secrets?api-version=7.4&api-version=7.4&$skiptoken=x",
            VAULT+"/secrets?api-version=7.4&maxresults=100&$skiptoken=x",
            VAULT+"/secrets?api-version=7.4&$skiptoken=",
            VAULT+"/secrets?api-version=7.4&$skiptoken=x&unknown=1",
            VAULT+"/secrets?api-version=7.4&$skiptoken=x#fragment", 1, [], {})
        for link in links:
            client,calls = self.pages([{"value":[],"nextLink":link}])
            with self.assertRaises(ValueError):
                host.secret_inventory(client)
            self.assertEqual(len(calls),1)

    def test_repeated_continuation_and_100page_bound(self):
        link = VAULT+"/secrets?api-version=7.4&$skiptoken=x"
        client,calls = self.pages([{"value":[],"nextLink":link}]*2)
        with self.assertRaisesRegex(ValueError,"^isolated_host_repeated_secret_metadata_page$"):
            host.secret_inventory(client)
        self.assertEqual(len(calls),2)
        pages = [{"value":[],"nextLink":VAULT+f"/secrets?api-version=7.4&$skiptoken={i}"} for i in range(100)]
        client,calls = self.pages(pages)
        with self.assertRaisesRegex(ValueError,"^isolated_host_secret_inventory_incomplete$"):
            host.secret_inventory(client)
        self.assertEqual(len(calls),100)

    def test_unregistered_inventory_category_does_not_read(self):
        client,calls = self.pages([])
        with self.assertRaisesRegex(ValueError,"^isolated_host_secret_metadata_scope_invalid$"):
            host.secret_inventory(client,"secrets/other/versions")
        self.assertEqual(calls,[])

    def test_secret_value_gets_and_foreign_arm_reads_are_rejected(self):
        transport,session,credential = self.transport([])
        urls = (SECRET_ID+"?api-version=7.4",VAULT+"/secrets/"+SECRET+"?api-version=7.4",
            VAULT+"/deletedsecrets/"+SECRET+"?api-version=7.4",
            "https://management.azure.com"+SERVER_ID+"-old?api-version=2024-08-01",
            "https://management.azure.com"+SUBNET_ID+"?api-version=2024-03-01")
        for url in urls:
            with self.assertRaisesRegex(ValueError,"^isolated_host_unregistered_read$"):
                transport.call("GET",url)
        self.assertEqual(session.calls,[])
        self.assertEqual(credential.calls,[])

    def test_encoded_secret_names_and_foreign_version_scope_are_rejected(self):
        for item in (VAULT+"/secrets/%6fwnertrial-20261004-admin-url",VAULT+"/secrets/a%2Fb",
                     VAULT+"/secrets/a/"+"Z"*32,VAULT+"/secrets/other/"+"a"*32):
            client,calls = self.pages([{"value":[{"id":item}]}])
            with self.assertRaises(ValueError):
                host.secret_inventory(client,"secrets/"+SECRET+"/versions")
            self.assertEqual(len(calls),1)

    def test_versions_404_only_is_empty_metadata_without_value_read(self):
        calls = []
        def absent(method,url,**kwargs):
            calls.append((method,url,kwargs))
            return None,404
        self.assertEqual(host.secret_inventory(SimpleNamespace(call=absent),"secrets/"+SECRET+"/versions"),[])
        self.assertEqual(calls,[('GET',VAULT+"/secrets/"+SECRET+"/versions?api-version=7.4&maxresults=25",{"missing":True})])


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.original_blob_factory = host.reservation_blob
        self.payload, self.calls = None, []
        self.version, self.etag = "2026-10-04T12:34:56.1234567Z", '"0xABC123"'
        self.upload_fault = None
        self.property_mutation = None
        self.download_value = None
        fixture = self
        class Blob:
            def upload_blob(self,payload,**kwargs):
                fixture.calls.append(("upload",kwargs))
                if fixture.payload is not None:
                    raise RuntimeError("synthetic existing reservation")
                fixture.payload = payload
                if fixture.upload_fault:
                    raise RuntimeError("synthetic lost acknowledgement")
                return {"version_id":fixture.version,"etag":fixture.etag}

            def get_blob_properties(self,**kwargs):
                fixture.calls.append(("properties",kwargs))
                props = SimpleNamespace(size=len(fixture.payload),version_id=fixture.version,
                    etag=fixture.etag,blob_type="BlockBlob")
                if fixture.property_mutation:
                    setattr(props,*fixture.property_mutation)
                return props

            def download_blob(self,**kwargs):
                fixture.calls.append(("download",kwargs))
                return SimpleNamespace(readall=lambda:fixture.payload if fixture.download_value is None else fixture.download_value)
        self.blob = Blob()
        def factory(version=None):
            self.calls.append(("client",version))
            return self.blob
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(host,"reservation_blob",side_effect=factory))
        self.protection = self.stack.enter_context(patch.object(host.deploy,"archive_protection"))

    def test_atomic_create_ack_is_pinned_bounded_and_readback_exact(self):
        result = host.reserve({"scope":"synthetic"})
        self.assertEqual(set(result),{"blob","sha256","version_id","etag","protected_readback_verified"})
        self.assertEqual(result["version_id"],self.version)
        self.assertIn(("client",self.version),self.calls)
        upload = next(value for kind,value in self.calls if kind == "upload")
        self.assertIs(upload["overwrite"],False)
        self.assertEqual(upload["blob_type"],"BlockBlob")
        self.assertEqual(upload["match_condition"],host.MatchConditions.IfMissing)
        self.assertEqual(upload["timeout"],10)
        self.assertEqual(upload["retry_total"],0)
        self.assertEqual(upload["max_concurrency"],1)
        download = next(value for kind,value in self.calls if kind == "download")
        self.assertEqual(download["etag"],self.etag)
        self.assertEqual(download["match_condition"],host.MatchConditions.IfNotModified)
        self.assertEqual(download["length"],len(self.payload)+1)

    def test_actual_sdk_factory_is_fixed_no_retry_and_checks_hold_before_after(self):
        fixture = self
        self.protection.side_effect = lambda:fixture.calls.append(("hold",None))
        def service_factory(url,**kwargs):
            fixture.calls.append(("sdk",(url,kwargs)))
            def get_blob(container,blob,version_id=None):
                fixture.calls.append(("sdk_blob",(container,blob,version_id)))
                return fixture.blob
            return SimpleNamespace(get_blob_client=get_blob)
        with patch.object(host,"reservation_blob",self.original_blob_factory), \
                patch.object(host,"BlobServiceClient",side_effect=service_factory), \
                patch.object(host,"AzureCliCredential",return_value=object()):
            receipt = host.reserve({"scope":"synthetic"})
        sdk = [value for kind,value in self.calls if kind == "sdk"]
        self.assertEqual(len(sdk),2)
        for url,kwargs in sdk:
            self.assertEqual(url,"https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net")
            self.assertEqual(kwargs["connection_timeout"],5)
            self.assertEqual(kwargs["read_timeout"],10)
            self.assertEqual(kwargs["retry_total"],0)
            self.assertIs(kwargs["retry_to_secondary"],False)
            self.assertIs(kwargs["permit_redirects"],False)
            self.assertEqual(kwargs["redirect_max"],0)
        self.assertEqual([value[2] for kind,value in self.calls if kind == "sdk_blob"],[None,self.version])
        self.assertEqual([value[1] for kind,value in self.calls if kind == "sdk_blob"],
            ["probes/owner-codex-isolated-host-20261004/provision-reservation.json"]*2)
        kinds = [kind for kind,_value in self.calls]
        self.assertEqual(kinds[0],"hold")
        self.assertEqual(kinds[-1],"hold")
        self.assertEqual(kinds.count("hold"),3)
        self.assertEqual(receipt["version_id"],self.version)

    def test_existing_same_payload_and_lost_ack_never_create_authority(self):
        host.reserve({"scope":"synthetic"})
        with self.assertRaises(RuntimeError):
            host.reserve({"scope":"synthetic"})
        self.payload = None; self.calls.clear(); self.upload_fault = True
        with self.assertRaises(RuntimeError):
            host.reserve({"scope":"synthetic"})
        self.assertIsNotNone(self.payload)
        self.assertEqual([kind for kind,_value in self.calls],["client","upload"])

    def test_receipt_shape_flags_hash_version_etag_are_strict(self):
        receipt = host.reserve({"scope":"synthetic"})
        mutations = (("protected_readback_verified",1),("sha256","0"*64),("blob","other"),
            ("version_id","2026-99-99T00:00:00.0000000Z"),("etag","unquoted"),("extra",True))
        for field,value in mutations:
            changed = deepcopy(receipt); changed[field] = value
            calls = len(self.calls)
            with self.assertRaises(ValueError):
                host.reservation_readback(changed,self.payload)
            self.assertEqual(len(self.calls),calls)

    def test_pinned_properties_or_bytes_mismatch_refuses_acceptance(self):
        receipt = host.reserve({"scope":"synthetic"})
        for mutation in (("version_id","other"),("etag",'"0xDIFFERENT"'),("size",1),("blob_type","AppendBlob")):
            self.property_mutation = mutation
            with self.assertRaisesRegex(ValueError,"^isolated_host_reservation_ack_unverified$"):
                host.reservation_readback(receipt,self.payload)
        self.property_mutation = None; self.download_value = b"tampered"
        with self.assertRaisesRegex(ValueError,"^isolated_host_reservation_ack_unverified$"):
            host.reservation_readback(receipt,self.payload)


if __name__ == "__main__":
    unittest.main()
