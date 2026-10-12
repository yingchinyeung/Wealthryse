"""Offline native boundary fixtures. No credentials, network, SQL or dispatch."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import owner_task_aca_runtime_transport as t

PRINCIPAL = "11111111-1111-4111-8111-111111111111"
EXECUTION = t.contract.JOB + "-abc1234"
START = "2026-10-04T19:00:00Z"
END = "2026-10-04T19:20:00+00:00"


class Credential:
    def __init__(self, fail=False): self.calls = []; self.fail = fail
    def get_token(self, audience):
        self.calls.append(audience)
        if self.fail: raise RuntimeError("sensitive fake failure")
        return SimpleNamespace(token="synthetic-token", expires_on=99999999999)


class Response:
    def __init__(self, value=None, *, raw=None, status=200, close_error=False):
        self.raw = raw if raw is not None else json.dumps(value).encode()
        self.status_code = status; self.close_error = close_error; self.closed = False
    def iter_content(self, chunk_size):
        for offset in range(0, len(self.raw), chunk_size): yield self.raw[offset:offset+chunk_size]
    def close(self):
        self.closed = True
        if self.close_error: raise RuntimeError("sensitive close failure")


class Session:
    def __init__(self, responses=None, fail=False): self.responses = list(responses or []); self.calls = []; self.fail = fail
    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if self.fail: raise RuntimeError("sensitive http failure")
        return self.responses.pop(0)


def identity():
    return dict(id=t.contract.IDENTITY_ID, name=t.contract.IDENTITY, type="Microsoft.ManagedIdentity/userAssignedIdentities",
        location="East Asia", tags=t.identity_body()["tags"], properties=dict(principalId=PRINCIPAL,
            clientId="22222222-2222-4222-8222-222222222222", tenantId="0578a50b-a7a1-40a4-905b-8b20dba84124"))


def assignment():
    return dict(id=t.ASSIGNMENT_ID, name=t.ASSIGNMENT, type="Microsoft.Authorization/roleAssignments",
        properties=dict(**t.assignment_body(PRINCIPAL)["properties"], scope=t.contract.REGISTRY_ID,
            condition=None, conditionVersion=None, delegatedManagedIdentityResourceId=None))


def role():
    return dict(id=t.ROLE_ID, name=t.ACR_ROLE, type="Microsoft.Authorization/roleDefinitions",
        properties=dict(roleName="AcrPull", type="BuiltInRole", description="acr pull", assignableScopes=["/"],
            permissions=[dict(actions=["Microsoft.ContainerRegistry/registries/pull/read"], notActions=[], dataActions=[], notDataActions=[])]))


def replica():
    name = EXECUTION + "-abc12"
    return {"value": [dict(id=t.contract.JOB_ID + "/executions/" + EXECUTION + "/replicas/" + name,
        name=name, type="Microsoft.App/containerapps/revisions/replicas", createdTime=START, runningState="Running",
        containers=[dict(name=t.contract.CONTAINER, ready=True, started=True, restartCount=0, runningState="Running")])], "nextLink": None}


def logs(lines=None):
    rows = [[EXECUTION + "-abc12", t.contract.CONTAINER, "2026-10-04T19:01:00Z", line]
        for line in (lines if lines is not None else ["runtime record", "unexpected warning"])]
    return {"tables": [dict(name="PrimaryResult", columns=[dict(name=n, type=k) for n,k in t.CONSOLE_COLUMNS], rows=rows)]}


class ArmBoundaryTests(unittest.TestCase):
    def test_inert_import_and_constructor_discover_no_credentials(self):
        source = Path(t.__file__).read_text()
        self.assertNotIn("AzureCliCredential", source)
        self.assertNotIn("DefaultAzureCredential", source)
        self.assertNotIn("os.environ", source)
        c = Credential(); t.ArmTransport(c, Session())
        self.assertEqual(c.calls, [])

    def test_default_session_no_retry_no_environment_proxy(self):
        arm = t.ArmTransport(Credential())
        self.assertFalse(arm.session.trust_env)
        self.assertEqual(arm.session.get_adapter("https://").max_retries.total, 0)
        arm.session.close()

    def test_unknown_routes_methods_body_and_api_denied_before_token(self):
        cases = [("DELETE", t.contract.JOB_ID+"?api-version="+t.JOB_API, None, False),
            ("GET", "https://management.azure.com"+t.contract.JOB_ID+"?api-version="+t.JOB_API, None, False),
            ("GET", t.contract.JOB_ID+"?api-version=2024-03-01", None, False),
            ("GET", t.ROLE_ID+"?api-version="+t.ROLE_API, None, True),
            ("GET", t.contract.JOB_ID+"?api-version="+t.JOB_API, {}, False),
            ("POST", t.contract.JOB_ID+"/start?api-version="+t.JOB_API, {}, False),
            ("PUT", t.ASSIGNMENT_ID+"?api-version="+t.ROLE_API, {"properties":{"principalId":PRINCIPAL,"roleDefinitionId":"Owner"}}, False),
            ("GET", t.contract.JOB_ID+"?api-version="+t.JOB_API+"&api-version="+t.JOB_API, None, False)]
        for method,path,body,missing in cases:
            with self.subTest(path=path,method=method):
                c=Credential(); s=Session(); arm=t.ArmTransport(c,s)
                with self.assertRaises(t.TransportError): arm.call(method,path,body=body,missing=missing)
                self.assertEqual(c.calls, []); self.assertEqual(s.calls, [])

    def test_exact_get_uses_single_bounded_native_request(self):
        c=Credential(); response=Response(identity()); s=Session([response]); arm=t.ArmTransport(c,s)
        value,status=arm.request(t.contract.IDENTITY_ID,t.MI_API)
        self.assertEqual((value,status),(identity(),200)); self.assertEqual(c.calls,[t.ARM_AUDIENCE])
        self.assertEqual(len(s.calls),1); self.assertTrue(response.closed)
        kw=s.calls[0][2]
        self.assertEqual(kw["timeout"],(5,20)); self.assertIs(kw["allow_redirects"],False); self.assertIs(kw["stream"],True)

    def test_exact_identity_and_assignment_puts_allowed_once(self):
        c=Credential(); s=Session([Response(identity(),status=201),Response(assignment(),status=201)])
        arm=t.ArmTransport(c,s)
        arm.request(t.contract.IDENTITY_ID,t.MI_API,method="PUT",body=t.identity_body())
        arm.request(t.ASSIGNMENT_ID,t.ROLE_API,method="PUT",body=t.assignment_body(PRINCIPAL))
        with self.assertRaises(t.TransportError): arm.request(t.contract.IDENTITY_ID,t.MI_API,method="PUT",body=t.identity_body())
        self.assertEqual(len(s.calls),2)

    def test_job_body_and_start_template_bound_before_authentication(self):
        expected={"properties":{"template":{"containers":[{"name":t.contract.CONTAINER}],"initContainers":[],"volumes":[]}}}
        with patch.object(t.contract,"definition",return_value=copy.deepcopy(expected)) as build:
            c=Credential(); s=Session([Response({"id":t.contract.JOB_ID},status=201),Response({"name":EXECUTION})])
            arm=t.ArmTransport(c,s,runtime_source_bytes=b"synthetic",expected_runtime={"synthetic":True})
        build.assert_called_once_with(b"synthetic",{"synthetic":True})
        bad=copy.deepcopy(expected); bad["identity"]={"type":"SystemAssigned"}
        with self.assertRaises(t.TransportError): arm.request(t.contract.JOB_ID,t.JOB_API,method="PUT",body=bad)
        self.assertEqual(c.calls,[])
        arm.request(t.contract.JOB_ID,t.JOB_API,method="PUT",body=expected)
        with self.assertRaises(t.TransportError):
            arm.request(t.contract.JOB_ID+"/start",t.JOB_API,method="POST",body=expected["properties"]["template"])
        arm.request(t.contract.JOB_ID+"/start",t.JOB_API,method="POST",body=t.start_template(expected))
        self.assertEqual(len(s.calls),2)

    def test_start_template_omits_only_exact_empty_volumes(self):
        value={"properties":{"template":{"containers":[],"initContainers":[],"volumes":[]}}}
        self.assertEqual(t.start_template(value),{"containers":[],"initContainers":[]})
        self.assertIn("volumes",value["properties"]["template"])
        for bad in (None,False,{},[{}]):
            value["properties"]["template"]["volumes"]=bad
            with self.assertRaises(t.TransportError): t.start_template(value)

    def test_actual_contract_bytes_descriptor_and_creation_start_interfaces(self):
        # Independently supplied synthetic descriptor; source is the actual fixed
        # reviewed checker, not .work evidence or a mocked contract builder.
        from test_owner_task_aca_runtime_contract import descriptor
        source=(Path(__file__).resolve().parents[1]/t.contract.RUNTIME_SOURCE_PATH).read_bytes()
        wanted=t.contract.definition(source,descriptor())
        c=Credential(); s=Session([Response({"id":t.contract.JOB_ID},status=201),Response({"name":EXECUTION})])
        arm=t.ArmTransport(c,s,runtime_source_bytes=source,expected_runtime=descriptor())
        altered=copy.deepcopy(wanted); altered["properties"]["configuration"]["secrets"]=[{"name":"not-approved"}]
        with self.assertRaises(t.TransportError): arm.request(t.contract.JOB_ID,t.JOB_API,method="PUT",body=altered)
        self.assertEqual(c.calls,[])
        arm.request(t.contract.JOB_ID,t.JOB_API,method="PUT",body=wanted)
        started=t.start_body(source,descriptor())
        self.assertNotIn("volumes",started); self.assertIn("initContainers",started)
        arm.request(t.contract.JOB_ID+"/start",t.JOB_API,method="POST",body=started)
        self.assertEqual(s.calls[0][2]["json"],wanted); self.assertEqual(s.calls[1][2]["json"],started)

    def test_unknown_effect_outcome_or_token_failure_consumes_attempt(self):
        for token_failure in (False,True):
            with self.subTest(token_failure=token_failure):
                c=Credential(fail=token_failure); s=Session(fail=True); arm=t.ArmTransport(c,s)
                with self.assertRaisesRegex(t.TransportError,"aca_transport_http_unknown"):
                    arm.request(t.contract.IDENTITY_ID,t.MI_API,method="PUT",body=t.identity_body())
                with self.assertRaisesRegex(t.TransportError,"aca_transport_effect_consumed"):
                    arm.request(t.contract.IDENTITY_ID,t.MI_API,method="PUT",body=t.identity_body())
                self.assertEqual(len(c.calls),1); self.assertEqual(len(s.calls),0 if token_failure else 1)

    def test_body_snapshot_survives_caller_mutation_during_authentication(self):
        wanted=t.identity_body(); original=copy.deepcopy(wanted)
        c=Credential(); previous=c.get_token
        def mutate(audience):
            wanted["tags"]["purpose"]="changed after validation"
            return previous(audience)
        c.get_token=mutate
        s=Session([Response(identity(),status=201)]); arm=t.ArmTransport(c,s)
        arm.request(t.contract.IDENTITY_ID,t.MI_API,method="PUT",body=wanted)
        self.assertEqual(s.calls[0][2]["json"],original)
        self.assertNotEqual(wanted,original)

    def test_404_only_registered_absence_and_strict_error_shape(self):
        for path,body,allowed in ((t.contract.IDENTITY_ID,{"error":{"code":"ResourceNotFound","message":"missing"}},True),
            (t.contract.JOB_ID,{"error":{"code":"ResourceNotFound","message":"missing"}},True),
            (t.contract.IDENTITY_ID,{"error":{"code":"AuthorizationFailed","message":"denied"}},False),
            (t.contract.IDENTITY_ID,{"error":{"code":"ResourceNotFound","message":"missing","extra":0}},False)):
            arm=t.ArmTransport(Credential(),Session([Response(body,status=404)]))
            if allowed: self.assertEqual(arm.request(path,t.MI_API if path==t.contract.IDENTITY_ID else t.JOB_API,missing=True),(None,404))
            else:
                with self.assertRaises(t.TransportError): arm.request(path,t.MI_API,missing=True)

    def test_registered_assignment_absence_uses_only_fixed_role_codes(self):
        for code in ("ResourceNotFound", "RoleAssignmentNotFound"):
            c=Credential(); response=Response({"error":{"code":code,"message":"missing"}},status=404)
            s=Session([response]); arm=t.ArmTransport(c,s)
            self.assertEqual(arm.request(t.ASSIGNMENT_ID,t.ROLE_API,missing=True),(None,404))
            self.assertEqual(len(c.calls),1); self.assertEqual(len(s.calls),1); self.assertTrue(response.closed)
        for path,api,body in (
            (t.contract.IDENTITY_ID,t.MI_API,{"error":{"code":"RoleAssignmentDoesNotExist","message":"missing"}}),
            (t.contract.JOB_ID,t.JOB_API,{"error":{"code":"RoleAssignmentDoesNotExist","message":"missing"}}),
            (t.contract.IDENTITY_ID,t.MI_API,{"error":{"code":"RoleAssignmentNotFound","message":"missing"}}),
            (t.contract.JOB_ID,t.JOB_API,{"error":{"code":"RoleAssignmentNotFound","message":"missing"}}),
            (t.ASSIGNMENT_ID,t.ROLE_API,{"error":{"code":"RoleAssignmentDoesNotExist","message":"missing"}}),
            (t.ASSIGNMENT_ID,t.ROLE_API,{"error":{"code":"RoleAssignmentDoesNotExist","message":"x"*4097}}),
            (t.ASSIGNMENT_ID,t.ROLE_API,{"error":{"code":"RoleAssignmentDoesNotExist","message":0}}),
            (t.ASSIGNMENT_ID,t.ROLE_API,{"error":{"code":"RoleAssignmentDoesNotExist","message":"missing","details":[]}}),
            (t.ASSIGNMENT_ID,t.ROLE_API,{"error":{"code":"AuthorizationFailed","message":"denied"}})):
            c=Credential(); response=Response(body,status=404); s=Session([response]); arm=t.ArmTransport(c,s)
            with self.assertRaisesRegex(t.TransportError,"aca_transport_response_invalid"):
                arm.request(path,api,missing=True)
            self.assertEqual(len(c.calls),1); self.assertEqual(len(s.calls),1); self.assertTrue(response.closed)

    def test_other_assignment_absence_denied_before_authentication(self):
        c=Credential(); s=Session([]); arm=t.ArmTransport(c,s)
        other=t.contract.REGISTRY_ID+"/providers/Microsoft.Authorization/roleAssignments/11111111-1111-4111-8111-111111111111"
        with self.assertRaisesRegex(t.TransportError,"aca_transport_route_denied"):
            arm.request(other,t.ROLE_API,missing=True)
        self.assertEqual(c.calls,[]); self.assertEqual(s.calls,[])

    def test_native_json_error_partial_oversize_and_close_denied(self):
        for response in (Response(raw=b'{"a":1,"a":2}'),Response(raw=b'{"a":1e999}'),
            Response(raw=b'{"a":NaN}'),Response(raw=b"x"*(t.MAX_BYTES+1)),Response({},status=403),Response({},close_error=True)):
            s=Session([response]); arm=t.ArmTransport(Credential(),s)
            with self.assertRaises(t.TransportError): arm.request(t.contract.IDENTITY_ID,t.MI_API)
            self.assertEqual(len(s.calls),1); self.assertTrue(response.closed)


class InventoryParserTests(unittest.TestCase):
    def test_exact_identity_assignment_and_role_metadata(self):
        self.assertEqual(t.validate_identity_metadata(identity()),identity())
        self.assertEqual(t.validate_assignment(assignment(),PRINCIPAL),assignment())
        self.assertEqual(t.validate_role_definition(role()),role())

    def test_identity_foreign_tenant_tags_and_extra_properties_denied(self):
        for mutate in (lambda v:v["properties"].update(tenantId=PRINCIPAL), lambda v:v["tags"].update(extra="x"),
            lambda v:v["properties"].update(password="synthetic"),lambda v:v.update(id=t.contract.IDENTITY_ID+"x")):
            v=identity(); mutate(v)
            with self.assertRaises(t.TransportError): t.validate_identity_metadata(v)

    def test_assignment_scope_conditions_groups_delegation_and_owners_denied(self):
        for change in ({"scope":t.SUB_SCOPE},{"principalType":"Group"},{"condition":"true"},
            {"delegatedManagedIdentityResourceId":t.contract.IDENTITY_ID},{"roleDefinitionId":t.ROLE_ID+"x"}):
            v=assignment(); v["properties"].update(change)
            with self.assertRaises(t.TransportError): t.validate_assignment(v,PRINCIPAL)

    def test_role_definition_extra_write_wildcard_data_and_alias_denied(self):
        for change in ({"actions":["*"]},{"actions":["Microsoft.ContainerRegistry/registries/pull/read","write"]},
            {"dataActions":["read"]},{"notActions":False}):
            v=role(); v["properties"]["permissions"][0].update(change)
            with self.assertRaises(t.TransportError): t.validate_role_definition(v)

    def test_complete_paginated_assigned_to_retains_group_grant(self):
        path=t.assignment_path(t.SUB_SCOPE,PRINCIPAL)
        group=copy.deepcopy(assignment()); group["id"]="/synthetic/group-assignment"; group["properties"]["principalId"]="33333333-3333-4333-8333-333333333333"; group["properties"]["principalType"]="Group"
        link=t.ARM+path+"&%24skipToken=opaque%2Btoken"
        s=Session([Response({"value":[assignment()],"nextLink":link}),Response({"value":[group],"nextLink":None})])
        arm=t.ArmTransport(Credential(),s)
        rows=t.role_assignments(arm,t.SUB_SCOPE,PRINCIPAL)
        self.assertEqual(len(rows),2); self.assertIn(group,rows); self.assertEqual(len(s.calls),2)
        self.assertIn("assignedTo",s.calls[0][1]); self.assertNotIn("principalId+eq",s.calls[0][1])

    def test_three_scope_snapshot_includes_inherited_registry_but_not_rg_descendant(self):
        s=Session([Response({"value":[assignment()]}),Response({"value":[]}),Response({"value":[assignment()]})])
        value=t.role_inventory(t.ArmTransport(Credential(),s),PRINCIPAL)
        self.assertEqual(value["subscription"],[assignment()]); self.assertEqual(value["resource_group_inherited"],[])
        self.assertEqual(value["registry_inherited"],[assignment()])
        self.assertIs(value["observed_arm_inventory_complete"],True)
        self.assertIs(value["tenant_permission_closure_verified"],False)
        self.assertEqual(len(s.calls),3)
        self.assertNotIn("atScope",s.calls[0][1]); self.assertIn("atScope",s.calls[1][1]); self.assertIn("atScope",s.calls[2][1])

    def test_pagination_drift_duplicate_loop_scope_host_and_alias_denied(self):
        path=t.assignment_path(t.SUB_SCOPE,PRINCIPAL)
        bad_links=[t.ARM+path+"&$skipToken=",t.ARM+path+"&$skipToken=x&$filter=assignedTo('"+PRINCIPAL+"')",
            "https://evil.invalid"+path+"&$skipToken=x",t.ARM+path.replace(t.SUB_SCOPE,t.RG_SCOPE)+"&$skipToken=x",
            t.ARM+path.replace(PRINCIPAL,"22222222-2222-4222-8222-222222222222")+"&$skipToken=x",False]
        for link in bad_links:
            arm=t.ArmTransport(Credential(),Session([Response({"value":[],"nextLink":link})]))
            with self.assertRaises(t.TransportError): t.role_assignments(arm,t.SUB_SCOPE,PRINCIPAL)
        arm=t.ArmTransport(Credential(),Session([Response({"value":[assignment()],"nextLink":t.ARM+path+"&$skipToken=x"}),Response({"value":[assignment()]})]))
        with self.assertRaises(t.TransportError): t.role_assignments(arm,t.SUB_SCOPE,PRINCIPAL)

    def test_inventory_partial_extra_keys_and_page_cap_denied(self):
        for page in ({"value":[],"error":{}},{"value":{},"nextLink":None},{"value":[{}]}, {"value":[],"nextLink":0}):
            arm=t.ArmTransport(Credential(),Session([Response(page)]))
            with self.assertRaises(t.TransportError): t.role_assignments(arm,t.SUB_SCOPE,PRINCIPAL)
        path=t.assignment_path(t.SUB_SCOPE,PRINCIPAL)
        with patch.object(t,"MAX_PAGES",1):
            arm=t.ArmTransport(Credential(),Session([Response({"value":[],"nextLink":t.ARM+path+"&$skipToken=x"})]))
            with self.assertRaises(t.TransportError): t.role_assignments(arm,t.SUB_SCOPE,PRINCIPAL)

    def test_unknown_scope_filter_and_injection_denied_before_tokens(self):
        c=Credential(); s=Session(); arm=t.ArmTransport(c,s)
        for scope,p,flag in (("/",PRINCIPAL,False),(t.SUB_SCOPE,"x') or true",False),(t.SUB_SCOPE,PRINCIPAL,0)):
            with self.assertRaises(t.TransportError): t.role_assignments(arm,scope,p,at_scope=flag)
        self.assertEqual(c.calls,[]); self.assertEqual(s.calls,[])

    def test_flat_replica_exact_single_container_and_native_types(self):
        self.assertEqual(t.validate_replica(replica(),EXECUTION),replica()["value"][0])
        for mutate in (lambda v:v.update(nextLink=False),lambda v:v["value"].append(copy.deepcopy(v["value"][0])),
            lambda v:v["value"][0]["containers"][0].update(restartCount=False),lambda v:v["value"][0].update(name="different"),
            lambda v:v["value"][0]["containers"].append({}),lambda v:v["value"][0]["containers"][0].update(image=t.contract.IMAGE)):
            v=replica(); mutate(v)
            with self.assertRaises(t.TransportError): t.validate_replica(v,EXECUTION)


class ConsoleBoundaryTests(unittest.TestCase):
    def setUp(self): self.scope=t.ConsoleScope(EXECUTION,START,END); self.query=t.ConsoleQuery(self.scope)

    def test_query_retains_every_container_and_line_with_no_sampling(self):
        text=self.query.text
        self.assertIn("ContainerGroupName_s startswith '"+EXECUTION+"-'",text)
        self.assertIn("TimeGenerated >=",text)
        for phrase in ("Log_s startswith","ContainerName_s ==","take ","limit ","top ","distinct","arg_max","summarize"):
            self.assertNotIn(phrase,text)
        rows=t.validate_console_response(logs(),self.query)
        self.assertEqual([r["Log"] for r in rows],["runtime record","unexpected warning"])

    def test_invalid_scope_and_query_never_request_tokens(self):
        for execution,start,end in ((EXECUTION+"'",START,END),(EXECUTION,"2026-10-04T19:00:00+01:00",END),
            (EXECUTION,START,START),(EXECUTION,START,"2026-10-06T19:00:00Z"),(EXECUTION,False,END)):
            with self.assertRaises(t.TransportError): t.ConsoleScope(execution,start,end)
        c=Credential(); s=Session(); transport=t.LogsTransport(c,s)
        with self.assertRaises(t.TransportError): transport.call({"query":"arbitrary"})
        self.assertEqual(c.calls,[]); self.assertEqual(s.calls,[])

    def test_strict_complete_result_error_partial_tables_columns_and_row_caps(self):
        variants=[]
        v=logs(); v["error"]={}; variants.append(v)
        v=logs(); v["tables"].append(copy.deepcopy(v["tables"][0])); variants.append(v)
        v=logs(); v["tables"][0]["columns"][0]["type"]="dynamic"; variants.append(v)
        v=logs(); v["tables"][0]["rows"]=[["foreign",t.contract.CONTAINER,START,"line"]]; variants.append(v)
        v=logs(); v["tables"][0]["rows"]*=t.MAX_CONSOLE_ROWS; variants.append(v)
        v=logs(); v["tables"][0]["rows"][0][3]="x"*(t.MAX_CONSOLE_LINE+1); variants.append(v)
        v=logs(); v["tables"][0]["rows"][0][2]=END; variants.append(v)
        v=logs(); v["tables"][0]["rows"][0][3]=False; variants.append(v)
        for value in variants:
            with self.assertRaises(t.TransportError): t.validate_console_response(value,self.query)
        with self.assertRaises(t.TransportError): t.validate_console_response(logs(),self.query,http_status=True)

    def test_native_logs_fixed_endpoint_no_redirect_or_retry_and_closed(self):
        c=Credential(); response=Response(logs()); s=Session([response]); transport=t.LogsTransport(c,s)
        self.assertEqual(transport.call(self.query),logs()); self.assertEqual(c.calls,[t.LOGS_AUDIENCE])
        method,url,kw=s.calls[0]; self.assertEqual((method,url),("POST",t.LOGS_ENDPOINT))
        self.assertEqual(kw["timeout"],(5,20)); self.assertFalse(kw["allow_redirects"]); self.assertTrue(response.closed)
        default=t.LogsTransport(Credential()); self.assertFalse(default.session.trust_env)
        self.assertEqual(default.session.get_adapter("https://").max_retries.total,0); default.session.close()

    def test_logs_http_error_oversize_duplicates_nonfinite_and_close_fail(self):
        for response in (Response({},status=403),Response(raw=b"x"*(t.MAX_BYTES+1)),
            Response(raw=b'{"tables":[],"tables":[]}'),Response(raw=b'{"x":1e999}'),Response(logs(),close_error=True)):
            s=Session([response]); transport=t.LogsTransport(Credential(),s)
            with self.assertRaises(t.TransportError): transport.call(self.query)
            self.assertEqual(len(s.calls),1); self.assertTrue(response.closed)

    def test_two_full_inventories_preserve_duplicate_multiset_and_order_independence(self):
        first=logs(["same","same","warning"]); second=copy.deepcopy(first); second["tables"][0]["rows"].reverse()
        responses=iter([first,second]); calls=[]
        def query(q): calls.append(q.text); return next(responses)
        value=t.collect_console(query,self.scope)
        self.assertEqual(value["row_count"],3); self.assertEqual(len(calls),2)
        self.assertIs(value["late_delivery_excluded"],False); self.assertIs(value["platform_replacement_excluded"],False)
        self.assertEqual(sum(r["Log"]=="same" for r in value["rows"]),2)

    def test_inventory_drift_missing_added_altered_row_denied(self):
        for second in (logs(["runtime record"]),logs(["runtime record","unexpected warning","late"]),logs(["changed","unexpected warning"])):
            responses=iter([logs(),second])
            with self.assertRaisesRegex(t.TransportError,"aca_transport_logs_drift"):
                t.collect_console(lambda _q:next(responses),self.scope)


if __name__ == "__main__": unittest.main()
