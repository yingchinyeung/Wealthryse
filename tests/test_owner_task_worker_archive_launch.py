"""Offline scope/launch/control regressions; never live acceptance evidence."""
import base64
from dataclasses import asdict,replace
import copy
import hashlib
import json
import lzma
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import owner_task_worker_archive_launch as l
import deploy_owner_task_worker_archive_trial as c


def guid(n):return f'00000000-0000-0000-0000-{n:012d}'


def identities():
    return {m:dict(id=rid,name=rid.rsplit('/',1)[1],type='Microsoft.ManagedIdentity/userAssignedIdentities',
        location='eastasia',tags=dict(l.IDENTITY_TAGS,mode=m),properties=dict(tenantId=l.worker.TENANT,
            clientId=guid(i+1),principalId=guid(i+11))) for i,(m,rid) in enumerate(l.worker.IDENTITIES.items())}


def sources():
    raw={p:(c.ROOT/p).read_bytes() for p,_ in l.MODULES}
    return raw,{p:hashlib.sha256(v).hexdigest() for p,v in raw.items()}


def plan(mode='writer'):
    raw,hashes=sources();ctx=l.context(mode,identities(),'a'*64);refs=None
    if mode=='verifier':
        refs={key:dict(name=l.worker.payload(ctx,kind) and 'owner-worker-v1/'+l.worker.sha(l.worker.payload(ctx,kind))+'.json',
            version_id='2026-10-05T22:31:28.4066636Z',etag='fixture',size=len(l.worker.payload(ctx,kind)),
            sha256=l.worker.sha(l.worker.payload(ctx,kind))) for kind,key in (('normal','normal'),('lost_ack','lost_ack_inspection'))}
    return l.build(mode,raw,hashes,ctx,refs)


def native(mode='writer'):
    p=plan(mode);v=copy.deepcopy(p['definition']);reg=l.registered(identities()[mode],mode)
    v.update(id=l.worker.JOBS[mode],name=l.worker.JOBS[mode].rsplit('/',1)[1],type='Microsoft.App/jobs')
    v['identity']['userAssignedIdentities'][l.worker.IDENTITIES[mode]]=dict(clientId=reg['clientId'],principalId=reg['principalId'])
    v['properties']['provisioningState']='Succeeded'
    return v,p,reg


def result(mode='writer'):
    p=plan(mode);ctx=l.worker.Context(**p['context'])
    out=dict(status='synthetic_archive_boundary_verified',operation_id=l.worker.OPERATION,mode=mode,
        job_resource_id=l.worker.JOBS[mode],identity_resource_id=l.worker.IDENTITIES[mode],
        principal_id=ctx.principal_id,source_manifest_sha256=ctx.source_manifest_sha256,image_sha256=ctx.image_sha256,
        lost_ack_receipt_accepted=False,lost_ack_newly_created=False)
    out.update({k:False for k in ('database_committed','protected_worker_receipts_verified','production_evidence_registry_enrolled',
        'runtime_process_cessation_verified','website_owner_authenticated','owner_runtime_active','six_specialist_runtimes_active',
        'step_3_complete','step_4_started','production_changed','automatic_retry')})
    out.update({k:0 for k in ('source_rows_read','source_rows_written','sql_connections','model_calls')})
    if mode=='writer':
        out.update(plan('verifier')['references'],lost_ack_automatic_retry=False,
            duplicate_denial=dict(status_code=409,error_code='BlobAlreadyExists'),
            lost_ack_duplicate_denial=dict(status_code=409,error_code='BlobAlreadyExists'),
            overwrite_denial=dict(status_code=409,error_code='BlobImmutableDueToLegalHold'))
    else:out.update(exact_versions_independently_read=True,
        reader_creation_denial=dict(status_code=403,error_code='AuthorizationPermissionMismatch'))
    return out,ctx,p


class LaunchScopeTests(unittest.TestCase):
    def test_stack_has_exactly_two_identities_and_six_container_scoped_grants(self):
        body=l.identity_stack();self.assertEqual(body['properties']['mode'],'Incremental')
        rows=body['properties']['template']['resources'];self.assertEqual(len(rows),8)
        self.assertEqual(len([r for r in rows if r['type'].startswith('Microsoft.ManagedIdentity/')]),2)
        grants=[r for r in rows if r['type']=='Microsoft.Authorization/roleAssignments']
        self.assertEqual(len(grants),6)
        self.assertEqual({l.RG+'/providers/'+r['scope'] for r in grants},{l.REGISTRY,l.worker.ARM_SCOPE})
        self.assertTrue(all("'Full').properties.principalId" in r['properties']['principalId'] for r in grants))
        self.assertNotIn('Owner',json.dumps(body));self.assertNotIn('KeyVault',json.dumps(body))
        self.assertNotIn('raw-statements',json.dumps(body));self.assertNotIn('ibkr-raw',json.dumps(body))

    def test_assignment_names_are_distinct_and_deterministic(self):
        rows=[l.assignment(m,r) for m in l.worker.IDENTITIES for r in ('acr','protection',m)]
        self.assertEqual(len(set(rows)),6);self.assertEqual(rows,[l.assignment(m,r) for m in l.worker.IDENTITIES for r in ('acr','protection',m)])
        for m,r in (('writer','verifier'),('verifier','writer'),('other','acr'),('writer','Owner')):
            with self.assertRaises(ValueError):l.assignment(m,r)

    def test_registration_rejects_wrong_scope_mode_tenant_or_identity(self):
        for change in (dict(id=l.RG),dict(tags={}),dict(location='westus'),dict(properties=dict(tenantId=guid(999),clientId=guid(1),principalId=guid(2)))):
            v=identities()['writer'];v.update(change)
            with self.assertRaises(ValueError):l.registered(v,'writer')

    def test_context_requires_physically_separate_identities(self):
        for key in ('clientId','principalId'):
            ids=identities();ids['verifier']['properties'][key]=ids['writer']['properties'][key]
            with self.assertRaises(ValueError):l.context('writer',ids,'a'*64)

    def test_writer_job_has_one_identity_no_secrets_no_sql_and_zero_retries(self):
        p=plan();d=p['definition'];config=d['properties']['configuration']
        self.assertEqual(config['triggerType'],'Manual');self.assertEqual(config['replicaRetryLimit'],0)
        self.assertEqual(config['secrets'],[]);self.assertEqual(d['properties']['template']['containers'][0]['env'],[])
        self.assertEqual(set(d['identity']['userAssignedIdentities']),{l.worker.IDENTITIES['writer']})
        self.assertEqual(config['identitySettings'],[dict(identity=l.worker.IDENTITIES['writer'],lifecycle='All')])

    def test_bootstrap_binds_source_hashes_runtime_and_non_root_uid(self):
        p=plan();args=p['definition']['properties']['template']['containers'][0]['args']
        packed=args[3].split("base64.b64decode('",1)[1].split("')",1)[0]
        script=lzma.decompress(base64.b64decode(packed)).decode()
        self.assertIn('sys.version_info[:3]==(3,12,14)',script)
        self.assertIn('os.getuid()==os.getgid()==10001',script)
        self.assertEqual(hashlib.sha256(script.encode()).hexdigest(),p['full_script_sha256'])
        compile(script,'test-bootstrap','exec');self.assertNotIn('$',json.dumps(args))

    def test_build_rejects_changed_source_hash_missing_module_and_wrong_image(self):
        raw,hashes=sources();ctx=l.context('writer',identities(),'a'*64)
        changed=dict(raw);changed[next(iter(changed))]+=b'\n'
        with self.assertRaises(ValueError):l.build('writer',changed,hashes,ctx)
        missing=dict(raw);missing.pop(next(iter(missing)))
        with self.assertRaises(ValueError):l.build('writer',missing,hashes,ctx)
        with self.assertRaises(ValueError):l.build('writer',raw,hashes,replace(ctx,image_sha256='b'*64))

    def test_verifier_requires_exact_hash_bound_versions(self):
        raw,hashes=sources();ctx=l.context('verifier',identities(),'a'*64)
        with self.assertRaises(ValueError):l.build('verifier',raw,hashes,ctx)
        refs=plan('verifier')['references'];refs['normal']['sha256']='b'*64
        with self.assertRaises(ValueError):l.build('verifier',raw,hashes,ctx,refs)

    def test_native_jobs_accept_only_reviewed_azure_empty_defaults(self):
        v,p,reg=native();v['properties']['template']['initContainers']=[]
        v['properties']['template']['volumes']=None
        v['properties']['template']['containers'][0]['imageType']='ContainerImage'
        v['properties']['template']['containers'][0]['resources']['ephemeralStorage']=''
        l.checked_job(v,p,'writer',reg)

    def test_native_job_rejects_extra_identity_changed_image_secret_retry_and_initializer(self):
        for key in ('identity','image','secret','retry','initializer','env','volume'):
            v,p,reg=native()
            if key=='identity':v['identity']['userAssignedIdentities'][l.worker.IDENTITIES['verifier']]={}
            elif key=='image':v['properties']['template']['containers'][0]['image']='latest'
            elif key=='secret':v['properties']['configuration']['secrets']=[dict(name='secret')]
            elif key=='retry':v['properties']['configuration']['replicaRetryLimit']=1
            elif key=='initializer':v['properties']['template']['initContainers']=[dict(name='bad')]
            elif key=='env':v['properties']['template']['containers'][0]['env']=[dict(name='DATABASE_URL',value='bad')]
            else:v['properties']['template']['volumes']=[dict(name='bad')]
            with self.assertRaises(ValueError):l.checked_job(v,p,'writer',reg)

    def test_effects_reject_arbitrary_destinations_and_executable_plan_changes(self):
        for mode in ('writer','verifier'):
            p=plan(mode);self.assertEqual(c.effect(mode+'-job',p)[:2],('PUT',l.worker.JOBS[mode]))
            self.assertEqual(c.effect(mode+'-start',p)[0],'POST')
            p['definition']['properties']['template']['containers'][0]['args']=['-c','malicious']
            p['definition_sha256']=l.digest(p['definition'])
            with self.assertRaises(ValueError):c.effect(mode+'-job',p)
        for stage in ('delete','restart','sql','writer-role','production-job'):
            with self.assertRaises(ValueError):c.effect(stage)

    def test_transport_route_rejects_other_accounts_resources_and_execution_suffixes(self):
        for path in (l.RG,l.worker.ARM_SCOPE+'/setLegalHold',l.worker.JOBS['writer']+'/executions/wrong',
                l.worker.JOBS['writer']+'/executions/ibkrv2stg-ownaw-20261005-abcdefg/restart',
                l.worker.BASE+'Microsoft.KeyVault/vaults/ibkrv2stg-auhecwc4itaoo',
                l.worker.ARM_SCOPE.replace('specialist-audit-checkpoints','ibkr-raw')):
            with self.assertRaises(ValueError):c.api(path)

    def test_validation_is_not_a_provisioning_or_start_stage(self):
        method,path,body=c.effect('validate')
        self.assertEqual((method,path),('POST',l.VALIDATION+'/validate'))
        self.assertEqual(body,l.identity_stack());self.assertNotIn('validate',c.STAGES)
        self.assertNotEqual(path,l.DEPLOYMENT)

    def test_source_projection_preserves_every_844_predecessor_hash(self):
        before={f'old-{n}':'a'*64 for n in range(844)};after=dict(before,**{p:'b'*64 for p in l.ADDITIONS})
        c.extension(before,after)
        after['old-10']='c'*64
        with self.assertRaises(ValueError):c.extension(before,after)
        after['old-10']='a'*64;after['extra']='d'*64
        with self.assertRaises(ValueError):c.extension(before,after)

    def test_owned_namespace_does_not_mutate_frozen_predecessor_globals(self):
        old=dict(vars(c.previous));oldprior=dict(vars(c.previous.prior))
        n=c.read_context()
        self.assertEqual(dict(vars(c.previous)),old);self.assertEqual(dict(vars(c.previous.prior)),oldprior)
        self.assertTrue(callable(n['guards']));self.assertEqual(n['worker'].OPERATION,'89b88478-11e1-45d5-9ff1-048c4cf25ce9')

    def test_current_source_growth_does_not_requalify_cancelled_frozen_trial(self):
        current=c.previous.prior.candidate.previous.ROOT
        self.assertEqual(current,c.ROOT)
        n=c.read_context();after=n['p'].old.prior.host.sources()
        self.assertGreaterEqual(len(after),847)
        before={k:v for k,v in after.items() if k not in l.ADDITIONS}
        if len(after)==847:
            c.extension(before,after)
        else:
            # Normal application development must not expand the cancelled
            # trial's 847-file authority or retrospectively approve new sources.
            with self.assertRaises(ValueError):c.extension(before,after)

    def test_owned_guard_preserves_true_source_comparison_result(self):
        n=c.read_context()
        # The frozen comparator is a pure contract. Test its exact fixture,
        # not the current application's evolving file discovery.
        after={f'fixture-{i}':'a'*64 for i in range(841)}
        after.update({p:'b'*64 for p in c.previous.ADDITIONS|l.ADDITIONS})
        before=c.previous.project_sources(c.projection(after))
        comparator=n['guards'].__globals__['archive_source_extension']
        self.assertIs(comparator(before,after),True)
        changed=dict(after);changed[next(iter(before))]='f'*64
        with self.assertRaises(ValueError):comparator(before,changed)

    def test_qualified_writer_and_verifier_results_never_claim_sql_or_runtime(self):
        for mode in ('writer','verifier'):
            out,ctx,p=result(mode);c.qualified_result(out,ctx,p)

    def test_forged_permission_denial_and_success_claims_are_rejected(self):
        for field in ('database_committed','protected_worker_receipts_verified','owner_runtime_active',
                'step_3_complete','step_4_started','lost_ack_receipt_accepted'):
            out,ctx,p=result();out[field]=True
            with self.assertRaises(ValueError):c.qualified_result(out,ctx,p)
        out,ctx,p=result('verifier');out['reader_creation_denial']['error_code']='AuthenticationFailed'
        with self.assertRaises(ValueError):c.qualified_result(out,ctx,p)

    def test_boolean_zero_and_wrong_principal_are_not_evidence(self):
        for key,value in (('sql_connections',False),('principal_id',guid(99)),('source_manifest_sha256','b'*64)):
            out,ctx,p=result();out[key]=value
            with self.assertRaises(ValueError):c.qualified_result(out,ctx,p)

    def test_complete_direct_grant_inventory_rejects_extra_scope_and_condition(self):
        mode='verifier';principal=guid(12)
        rows=[dict(id=l.assignment(mode,r),properties=dict(principalId=principal,principalType='ServicePrincipal',
            scope=l.ROLES[r][0],roleDefinitionId=l.SUB+'/providers/Microsoft.Authorization/roleDefinitions/'+l.ROLES[r][1]))
            for r in ('acr','protection',mode)]
        class Client:
            def call(self,*args,**kw):return dict(value=rows)
        c.roles(Client(),mode,principal)
        rows[0]['properties']['scope']=l.RG
        with self.assertRaises(ValueError):c.roles(Client(),mode,principal)
        rows[0]['properties']['scope']=l.REGISTRY;rows[0]['properties']['condition']='unreviewed'
        with self.assertRaises(ValueError):c.roles(Client(),mode,principal)


if __name__=='__main__':unittest.main()
