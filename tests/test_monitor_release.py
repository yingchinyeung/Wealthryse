import base64
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import unittest
from ibkr_etl import monitor_release as release


def args(source):
    encoded=base64.b64encode(source.encode()).decode()
    return ['-c',"import base64;exec(compile(base64.b64decode('"+encoded+"'),'metadata-monitor','exec'))"]


class MonitorReleaseTests(unittest.TestCase):
    def baseline(self):
        payload=args('SOURCE_POLICY="old"\n')
        return {'id':release.BASE+'Microsoft.App/jobs/'+release.JOB,'location':'eastasia',
            'tags':{'application':'ibkr-portfolio-platform','purpose':'metadata-shadow-monitor',
                'owner':'portfolio-platform','mode':'shadow','payloadSha256':hashlib.sha256(release.decode_bundle(payload)).hexdigest()},
            'identity':{'type':'UserAssigned','userAssignedIdentities':{release.IDENTITY:{'principalId':'p','clientId':'c'}}},
            'properties':{'environmentId':release.ENVIRONMENT,'configuration':{
                'triggerType':'Schedule','replicaTimeout':180,'replicaRetryLimit':0,
                'scheduleTriggerConfig':{'parallelism':1,'replicaCompletionCount':1,'cronExpression':'7,37 * * * *'},
                'secrets':[{'name':'monitor-url','keyVaultUrl':'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/database-metadata-monitor-url','identity':release.IDENTITY}],
                'registries':[{'server':'ibkrv2stgauhecwc4itaoo.azurecr.io','identity':release.IDENTITY}]},
                'template':{'containers':[{'name':'metadata-monitor','image':release.IMAGE,'command':['python'],
                    'args':payload,'resources':{'cpu':0.25,'memory':'0.5Gi'},
                    'env':[{'name':'PORTFOLIO_TENANT_ID','value':'00000000-0000-0000-0000-000000000001'},
                           {'name':'DATABASE_URL','secretRef':'monitor-url'}]}]}}}

    def candidate(self):return args('SOURCE_POLICY="new"\n')

    def evidence(self):
        baseline=self.baseline(); payload=self.candidate()
        container=release.source_only_definition(baseline,payload)['properties']['template']['containers'][0]
        container['env'][1]['secretRef']='cappjob-'+release.JOB
        execution={'name':release.JOB+'-abcdefg','properties':{'status':'Succeeded','template':{'containers':[container]}}}
        now=datetime(2026,10,6,12,tzinfo=timezone.utc); captured=now.isoformat()
        rows=[{'kind':'benchmark','index':0,'captured_at':captured,
            'value':{'state':'behind_expected_session','quality':'warn'}}]
        marker={'kind':'complete','mode':'shadow','captured_at':captured,'records':1,
            'evidence_sha256':'a'*64,'status':'needs_review','benchmark_checked':1,'benchmark_attention':1,
            'benchmark_quality_warnings':1,'benchmark_scope':'checked','financial_reconciliation':'not_certified',
            'inventory_complete':True,'denied_column_checks':7,'access_check':'passed',
            'calendar_version':'test','limits':['not financial certification']}
        return baseline,payload,execution,rows+[marker],now

    def test_source_only_readback_and_exact_rollback(self):
        baseline=self.baseline();payload=self.candidate()
        value=release.source_only_definition(baseline,payload);current=deepcopy(baseline)
        current.update(value)
        release.verify_readback(baseline,current,payload)
        self.assertEqual(release.invariant(baseline),release.invariant(current))
        restored=deepcopy(current);restored.update(release.writable_definition(baseline))
        release.verify_readback(baseline,restored,baseline['properties']['template']['containers'][0]['args'])

    def test_wrapper_digest_and_compile_required(self):
        for bad in (['-c','print(1)'],args('syntax is !!!'),['-c',self.candidate()[1]+';print(1)']):
            with self.assertRaises(ValueError):release.decode_bundle(bad)
        baseline=self.baseline();baseline['tags']['payloadSha256']='b'*64
        with self.assertRaises(ValueError):release.validate_baseline(baseline)

    def test_identity_image_env_schedule_grants_and_resources_cannot_change(self):
        cases=[]
        for field,value in [('image','other'),('command',['sh']),('resources',{'cpu':1,'memory':'1Gi'}),
            ('env',[{'name':'DATABASE_URL','secretRef':'admin-url'}])]:
            changed=self.baseline();changed['properties']['template']['containers'][0][field]=value;cases.append(changed)
        for field,value in [('replicaRetryLimit',1),('triggerType','Manual'),('replicaTimeout',1000),('secrets',[])]:
            changed=self.baseline();changed['properties']['configuration'][field]=value;cases.append(changed)
        changed=self.baseline();changed['identity']['type']='SystemAssigned';cases.append(changed)
        for changed in cases:
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):release.validate_baseline(changed)

    def test_resolved_secret_never_retained(self):
        changed=self.baseline();changed['properties']['configuration']['secrets'][0]['value']='secret'
        with self.assertRaises(ValueError):release.writable_definition(changed)

    def test_exact_azure_location_display_form_not_unrelated_region(self):
        value=self.baseline();value['location']='East Asia'
        release.validate_baseline(value)
        value['location']='eastus'
        with self.assertRaises(ValueError):release.validate_baseline(value)

    def test_complete_native_attention_summary_is_accepted_not_hidden(self):
        baseline,payload,native,rows,now=self.evidence()
        result=release.qualify_execution(native,baseline,payload,rows,now=now)
        self.assertEqual(result['marker']['status'],'needs_review')
        self.assertFalse(result['financial_acceptance'])
        self.assertFalse(result['image_security_acceptance'])

    def test_false_healthy_summary_and_missing_rows_rejected(self):
        for edit in ('false_status','missing_row','bool_count','different_capture','conflict','denied_false','error'):
            baseline,payload,native,rows,now=self.evidence()
            if edit=='false_status':rows[-1]['status']='no_findings_in_checked_scope'
            elif edit=='missing_row':rows.pop(0)
            elif edit=='bool_count':rows[-1]['benchmark_attention']=True
            elif edit=='different_capture':rows[0]['captured_at']=(now-timedelta(seconds=1)).isoformat()
            elif edit=='conflict':rows.append({**rows[0],'value':{'state':'current_for_scheduled_cycle'}})
            elif edit=='denied_false':rows[-1]['access_check']='failed'
            else:rows.append({'kind':'error'})
            with self.subTest(edit=edit):
                with self.assertRaises(ValueError):release.qualify_execution(native,baseline,payload,rows,now=now)

    def test_exact_native_payload_identity_credentials_and_success_required(self):
        for edit in ('image','args','env','status'):
            baseline,payload,native,rows,now=self.evidence()
            if edit=='status':native['properties']['status']='Failed'
            else:native['properties']['template']['containers'][0][edit]=[] if edit!='image' else 'other'
            with self.subTest(edit=edit):
                with self.assertRaises(ValueError):release.qualify_execution(native,baseline,payload,rows,now=now)

    def test_stale_future_or_naive_capture_rejected(self):
        for stamp in ('2026-10-06T11:00:00+00:00','2026-10-06T12:01:00+00:00','2026-10-06T12:00:00'):
            baseline,payload,native,rows,now=self.evidence()
            for row in rows:row['captured_at']=stamp
            with self.subTest(stamp=stamp):
                with self.assertRaises(ValueError):release.qualify_execution(native,baseline,payload,rows,now=now)


if __name__=='__main__':unittest.main()
