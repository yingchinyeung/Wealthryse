import contextlib
import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import deploy_ms_presentation_worker_release as r


class WorkerReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.stage=Path(self.temp.name)
        self.base='registry/base@sha256:'+'1'*64
        self.image='registry/candidate@sha256:'+'2'*64
        self.jobs={}
        identity=r.BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/existing'
        for name in r.build.JOBS:
            cfg={'triggerType':'Manual' if name==r.build.JOBS[0] else 'Schedule',
                'eventTriggerConfig':None,'secrets':[{'name':'db','keyVaultUrl':'https://vault/secrets/app',
                    'identity':identity,'value':None}]}
            if name==r.build.JOBS[1]:cfg['scheduleTriggerConfig']={'cronExpression':'0 9 * * *'}
            self.jobs[name]={'name':name,'id':r.path(name),
                'identity':{'type':'UserAssigned','userAssignedIdentities':{identity:{}}},
                'properties':{'provisioningState':'Succeeded','environmentId':'existing',
                    'configuration':cfg,'template':{'containers':[{'name':'report','image':self.base,
                        'command':['python'],'args':[r.ENTRYPOINTS[name]],
                        'env':[{'name':'DATABASE_URL','secretRef':'db','value':''},
                               {'name':'DEPLOYMENT_ENVIRONMENT','value':'staging'}],
                        'resources':{'cpu':1,'memory':'2Gi'}}],'initContainers':None,'volumes':None}}}
        self.plan={'base_image':self.base,'image':self.image,
            'settings_invariants':{n:r.settings_invariant(j) for n,j in self.jobs.items()}}

    def save(self,name,value):
        (self.stage/(name+'.json')).write_text(json.dumps(value),encoding='utf-8')

    def test_scope_exact_two_jobs_only(self):
        for name,job in self.jobs.items():r.scope(job,name)
        for name in ('ibkrv2stg-web','ibkrv2stg-historical-reports','../outside'):
            with self.subTest(name=name),self.assertRaises(ValueError):r.path(name)

    def test_wrong_arm_identity_blocks(self):
        name=r.build.JOBS[0];job=copy.deepcopy(self.jobs[name]);job['id']=r.path(r.build.JOBS[1])
        with self.assertRaisesRegex(ValueError,'ARM identity'):r.scope(job,name)

    def test_inline_secret_or_plaintext_db_blocks(self):
        name=r.build.JOBS[0]
        job=copy.deepcopy(self.jobs[name]);job['properties']['configuration']['secrets'][0]['value']='not-a-real-secret'
        with self.assertRaisesRegex(ValueError,'Inline secret'):r.scope(job,name)
        job=copy.deepcopy(self.jobs[name]);job['properties']['template']['containers'][0]['env'][0]={'name':'DATABASE_URL','value':'not-a-real-secret'}
        with self.assertRaisesRegex(ValueError,'Database secret'):r.scope(job,name)

    def test_unexpected_schedule_or_command_blocks(self):
        name=r.build.JOBS[1];job=copy.deepcopy(self.jobs[name]);job['properties']['configuration']['scheduleTriggerConfig']['cronExpression']='* * * * *'
        with self.assertRaisesRegex(ValueError,'daily schedule'):r.scope(job,name)
        job=copy.deepcopy(self.jobs[name]);job['properties']['template']['containers'][0]['args']=['other.py']
        with self.assertRaisesRegex(ValueError,'persisted reporting command'):r.scope(job,name)

    def test_image_only_patch_nonmutating(self):
        job=self.jobs[r.build.JOBS[0]];original=copy.deepcopy(job)
        body=r.patch_body(job,self.image)
        self.assertEqual(job,original)
        self.assertEqual(set(body),{'properties'})
        self.assertEqual(set(body['properties']),{'template'})
        restored=copy.deepcopy(body['properties']['template']);restored['containers'][0]['image']=self.base
        self.assertEqual(restored,job['properties']['template'])

    def test_only_known_empty_projections_normalize(self):
        job=self.jobs[r.build.JOBS[0]];original=copy.deepcopy(job);actual=copy.deepcopy(job)
        actual['properties']['template']['containers'][0]['env'][0].pop('value')
        actual['properties']['template']['containers'][0]['resources']['ephemeralStorage']=''
        actual['properties']['template']['containers'][0]['image']=self.image
        self.assertEqual(r.settings_invariant(actual),r.settings_invariant(job));self.assertEqual(job,original)

    def test_real_setting_and_credential_drift_not_ignored(self):
        job=self.jobs[r.build.JOBS[0]]
        for field in ('secret','memory','schedule','identity','inline','empty-other'):
            with self.subTest(field=field):
                changed=copy.deepcopy(job);c=changed['properties']['template']['containers'][0]
                if field=='secret':c['env'][0]['secretRef']='admin-db'
                elif field=='memory':c['resources']['memory']='4Gi'
                elif field=='schedule':changed['properties']['configuration']['triggerType']='Schedule'
                elif field=='identity':changed['identity']['type']='SystemAssigned'
                elif field=='inline':c['env'][0]['value']='not-a-real-secret'
                else:c['env'][1]['value']=''
                self.assertNotEqual(r.settings_invariant(job),r.settings_invariant(changed))

    def test_busy_unknown_and_pagination_blocks(self):
        for state in ('Running','Pending','Processing','Unknown'):
            with self.subTest(state=state),patch.object(r,'request',return_value={'value':[{'properties':{'status':state}}]}):
                with self.assertRaisesRegex(ValueError,'Active or unknown'):r.idle(r.build.JOBS[0])
        with patch.object(r,'request',return_value={'value':[],'nextLink':'https://example.com/jobs/executions'}):
            with self.assertRaisesRegex(ValueError,'pagination'):r.idle(r.build.JOBS[0])

    def test_all_execution_pages_are_inspected(self):
        n=r.build.JOBS[0];link='https://management.azure.com'+r.path(n)+'/executions?api-version=2024-03-01&skip=1'
        with patch.object(r,'request',side_effect=[{'value':[],'nextLink':link},{'value':[{'properties':{'status':'Running'}}]}]) as req:
            with self.assertRaisesRegex(ValueError,'Active or unknown'):r.idle(n)
            self.assertEqual(req.call_count,2)

    def test_schedule_window_blocks(self):
        for hour,minute in ((8,0),(8,59),(9,0),(9,29)):
            with self.subTest(hour=hour,minute=minute),self.assertRaisesRegex(ValueError,'daily schedule'):
                r.window(datetime(2026,10,8,hour,minute,tzinfo=timezone.utc))
        for hour,minute in ((7,59),(9,30),(23,45)):
            r.window(datetime(2026,10,8,hour,minute,tzinfo=timezone.utc))

    def test_unrecorded_candidate_switch_blocks(self):
        n=r.build.JOBS[0];self.jobs[n]['properties']['template']['containers'][0]['image']=self.image
        with patch.object(r,'request',side_effect=lambda m,p:self.jobs[p.split('/jobs/')[1].split('?')[0]]):
            with self.assertRaisesRegex(ValueError,'drifted'):r.all_jobs(self.stage,self.plan)

    def test_partial_release_only_with_recorded_intent(self):
        n=r.build.JOBS[0];self.jobs[n]['properties']['template']['containers'][0]['image']=self.image
        self.save(n+'-apply-intent',{})
        with patch.object(r,'request',side_effect=lambda m,p:self.jobs[p.split('/jobs/')[1].split('?')[0]]):
            observed=r.all_jobs(self.stage,self.plan)
            self.assertEqual(observed[n]['properties']['template']['containers'][0]['image'],self.image)

    def test_verified_candidate_cannot_silently_return_to_base(self):
        n=r.build.JOBS[0];self.save(n+'-apply-intent',{});self.save(n+'-apply-qualified',{})
        with patch.object(r,'request',side_effect=lambda m,p:self.jobs[p.split('/jobs/')[1].split('?')[0]]):
            with self.assertRaisesRegex(ValueError,'drifted'):r.all_jobs(self.stage,self.plan)

    def test_check_no_db_connect_no_processing(self):
        compile('BINDING={}\n'+r.CHECK,'installed-check','exec')
        for bad in ('psycopg.connect','DefaultAzureCredential','render_factsheet(','_render_presentation(','.main(','assert '):
            self.assertNotIn(bad,r.CHECK)
        self.assertIn('r[\'source_data_changed\'] is False',r.CHECK)

    def invoke(self,action,name,*,request_side_effect=None):
        stage=self.stage/'deployment-v1';stage.mkdir(exist_ok=True)
        plan={**self.plan,'targets':list(r.build.JOBS),'before_jobs':copy.deepcopy(self.jobs),
            'controller_sha256':r.build.sha(Path(r.__file__)),'evidence_sha256':{},
            'check_source':'pass','binding':{'installed_files_sha256':{}},'protected_receipt':{}}
        target=stage/'plan.json'
        if not target.exists():target.write_text(json.dumps(plan),encoding='utf-8')
        plan=json.loads(target.read_bytes())
        with patch.object(r,'image_evidence',return_value=({'base_image':self.base},self.image,{})), \
            patch.object(r,'restore_protected',return_value=({k:v for k,v in plan.items() if k!='protected_receipt'},0)), \
            patch.object(r,'all_jobs',return_value=copy.deepcopy(self.jobs)), \
            patch.object(r,'checked_job',return_value=copy.deepcopy(self.jobs[name])), \
            patch.object(r,'idle'),patch.object(r,'window'), \
            patch.object(r,'retain',return_value={'protected_readback_verified':True}), \
            patch.object(r,'request',side_effect=request_side_effect) as req, \
            patch('sys.argv',['controller',action,'--folder',str(self.stage),'--job',name]), \
            contextlib.redirect_stdout(io.StringIO()):
            r.main()
        return req

    def test_unknown_patch_outcome_cannot_redispatch(self):
        name=r.build.JOBS[0]
        with self.assertRaisesRegex(RuntimeError,'unknown outcome'):
            self.invoke('apply',name,request_side_effect=RuntimeError('unknown outcome'))
        path=self.stage/'deployment-v1'/f'{name}-apply-intent.json'
        self.assertTrue(path.exists())
        with self.assertRaisesRegex(ValueError,'Mutation attempted'):
            self.invoke('apply',name,request_side_effect=AssertionError('must not mutate'))

    def test_only_exact_job_image_patch_is_sent(self):
        name=r.build.JOBS[0];req=self.invoke('apply',name,request_side_effect=lambda *a,**kw:{})
        self.assertEqual(req.call_count,1)
        args,kw=req.call_args
        self.assertEqual(args,('PATCH',r.path(name)+r.API))
        self.assertEqual(set(kw['data']['properties']),{'template'})
        self.assertEqual(kw['data']['properties']['template']['containers'][0]['image'],self.image)

    def test_scheduled_target_cannot_precede_manual_check(self):
        with self.assertRaises(FileNotFoundError):
            self.invoke('apply',r.build.JOBS[1],request_side_effect=AssertionError('must not mutate'))
        self.assertFalse((self.stage/'deployment-v1'/f'{r.build.JOBS[1]}-apply-intent.json').exists())

    def test_unknown_check_dispatch_cannot_repeat(self):
        name=r.build.JOBS[0];stage=self.stage/'deployment-v1';stage.mkdir()
        (stage/f'{name}-apply-qualified.json').write_text(json.dumps({'status':'passed'}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Unknown check dispatch'):
            self.invoke('check',name,request_side_effect=lambda *a,**kw:{})
        with self.assertRaisesRegex(ValueError,'Check dispatched'):
            self.invoke('check',name,request_side_effect=AssertionError('must not start'))


if __name__=='__main__':unittest.main()
