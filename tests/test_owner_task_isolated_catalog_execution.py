"""Additive execution scope and fail-closed native metadata regression tests."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import execute_owner_task_isolated_catalog as c
from test_owner_task_provider_launch_contract import inputs


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        source,hashes,runtime=inputs();self.plan=c.launch.build(source,hashes,runtime)
        self.execution=c.JOB+'-abcdefg'
        self.replica=self.execution+'-abcde'
        self.row=dict(id=c.JOB_ID+'/executions/'+self.execution+'/replicas/'+self.replica,name=self.replica,
            type='Microsoft.App/containerapps/revisions/replicas',properties=dict(createdTime='2026-10-05T01:20:00Z',
            runningState='Running',containers=[dict(name=c.launch.prior.CONTAINER,ready=True,started=True,
            restartCount=0,runningState='Running',containerId='containerd://'+'a'*32)]))

    def test_native_ready_preserved(self):
        raw=dict(value=[self.row]);before=deepcopy(raw)
        self.assertEqual(c.validate_replica(raw,self.execution),self.row);self.assertEqual(raw,before)

    def test_no_rows_or_no_container_is_pending(self):
        self.assertIsNone(c.validate_replica(dict(value=[]),self.execution))
        self.row['properties']['containers']=[]
        self.assertIsNone(c.validate_replica(dict(value=[self.row]),self.execution))

    def test_foreign_replica_and_extra_rows_rejected(self):
        for edit in (lambda r:r.update(id='foreign'),lambda r:r.update(type='Microsoft.App/jobs/replicas'),
            lambda r:r['properties']['containers'][0].update(name='secretless-runtime-canary'),
            lambda r:r['properties']['containers'][0].update(execEndpoint='wss://foreign')):
            row=deepcopy(self.row);edit(row)
            with self.assertRaises(ValueError):c.validate_replica(dict(value=[row]),self.execution)
        with self.assertRaises(ValueError):c.validate_replica(dict(value=[self.row,self.row]),self.execution)

    def test_restart_bool_and_identity_validation(self):
        for key,value in (('restartCount',True),('restartCount',1),('ready','true'),('containerId','sha256:'+'a'*64),
            ('containerId','containerd://'+'A'*32),('secret','hidden'),('runningState','Terminated')):
            row=deepcopy(self.row);row['properties']['containers'][0][key]=value
            with self.assertRaises(ValueError):c.validate_replica(dict(value=[row]),self.execution)

    def test_waiting_is_pending_not_ready(self):
        self.row['properties']['containers'][0].update(ready=False,started=False,runningState='Waiting')
        self.assertIsNone(c.validate_replica(dict(value=[self.row]),self.execution))
        self.row['properties']['containers'][0]['ready']=True
        with self.assertRaises(ValueError):c.validate_replica(dict(value=[self.row]),self.execution)

    def test_complete_inventory_cannot_hide_executions(self):
        c.validate_inventory(dict(value=[]))
        row=dict(id=c.JOB_ID+'/executions/'+self.execution,name=self.execution)
        c.validate_inventory(dict(value=[row]),self.execution)
        for raw in (dict(value=[row]),dict(value=[],nextLink='https://more'),dict(value=[row,row])):
            with self.assertRaises(ValueError):c.validate_inventory(raw)

    def test_fixed_routes_reject_other_methods_scopes_and_templates(self):
        client=c.Transport(credential=object(),session=object())
        for method,path,api,kwargs in (('POST',c.JOB_ID+'/start','2025-07-01',dict(body={},plan=self.plan)),
            ('GET',c.JOB_ID+'/executions/foreign','2025-07-01',{}),
            ('GET',c.JOB_ID+'/executions/'+self.execution+'/exec','2023-11-02-preview',{}),
            ('GET',c.JOB_ID+'/executions','2024-03-01',{}),('DELETE',c.JOB_ID,'2025-07-01',{}),
            ('PUT',c.preparation.MI,'2023-01-31',dict(body=c.preparation.identity_body(),stage='identity',plan=self.plan))):
            with self.assertRaises(ValueError):client.call(method,path,api,**kwargs)

    def test_additive_sources_are_exact(self):
        before={'source'+str(i):'a'*64 for i in range(748)}
        after={**before,**{k:'b'*64 for k in c.ADDITIONS}};c.validate_sources(before,after)
        for raw in ({**after,'extra':'c'*64},{**after,'source0':'c'*64},before):
            with self.assertRaises(ValueError):c.validate_sources(before,raw)

    def test_execution_template_and_native_times(self):
        # Test uses actual frozen source bytes; the fixture hashes describe these.
        template=deepcopy(self.plan['definition']['properties']['template'])
        template['containers'][0]['imageType']='ContainerImage'
        template['containers'][0]['env'][0]['secretRef']='cappjob-'+c.JOB
        value=dict(id=c.JOB_ID+'/executions/'+self.execution,name=self.execution,type='Microsoft.App/jobs/executions',
            properties=dict(startTime='2026-10-05T01:20:00.1234567Z',status='Running',template=template))
        c.validate_execution(value,self.plan,self.execution)
        value['properties'].update(status='Succeeded',endTime='2026-10-05T01:25:00+00:00')
        c.validate_execution(value,self.plan,self.execution,terminal=True)
        for key,item in (('startTime','2026-10-05T01:20:00'),('endTime','2026-10-05T01:19:00Z'),('status','Running')):
            raw=deepcopy(value);raw['properties'][key]=item
            with self.assertRaises(ValueError):c.validate_execution(raw,self.plan,self.execution,terminal=True)


if __name__=='__main__':unittest.main()
