import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_codex_identity_trial import IMAGE,CLIENT,PRINCIPAL
from deploy_codex_runtime_probe import (REGISTRY,REPO,diagnostic_template,
    protected_record,verified_build_image,fingerprint,build_command,build_run_id,
    validate_execution_template)


class CodexRuntimeDeploymentTests(unittest.TestCase):
    def job(self):
        return {'identity':{'type':'UserAssigned','userAssignedIdentities':{'id':{'clientId':CLIENT,'principalId':PRINCIPAL}}},
            'properties':{'configuration':{'triggerType':'Manual','replicaRetryLimit':0,
                'manualTriggerConfig':{'parallelism':1,'replicaCompletionCount':1}},
                'template':{'containers':[{'name':'diagnostic','image':IMAGE,
                    'resources':{'cpu':1,'memory':'2Gi'},'env':[{'name':'DATABASE_URL','secretRef':'sensitive'}],
                    'command':['production_command']} ]}}}

    def test_override_contains_no_original_files_or_credentials_and_no_settings_write(self):
        job=self.job();before=copy.deepcopy(job)
        image=REGISTRY+'.azurecr.io/'+REPO+'@sha256:'+'a'*64
        result=diagnostic_template(job,image)
        self.assertEqual(job,before)
        c=result['containers'][0]
        self.assertEqual(c['image'],image)
        self.assertEqual(c['env'],[{'name':'AZURE_CLIENT_ID','value':CLIENT}])
        self.assertNotIn('DATABASE_URL',json.dumps(result))
        self.assertNotIn('production_command',json.dumps(result))
        self.assertEqual(c['args'],['scripts/run_codex_runtime_probe.py','--mode','azure'])

    def test_mutable_other_repository_and_changed_diagnostic_scope_denied(self):
        for image in [REGISTRY+'.azurecr.io/'+REPO+':latest','other.azurecr.io/'+REPO+'@sha256:'+'a'*64,
                      REGISTRY+'.azurecr.io/ibkr-platform@sha256:'+'a'*64]:
            with self.assertRaises(ValueError):diagnostic_template(self.job(),image)
        job=self.job();job['properties']['configuration']['triggerType']='Schedule'
        with self.assertRaises(ValueError):diagnostic_template(job,REGISTRY+'.azurecr.io/'+REPO+'@sha256:'+'a'*64)

    def test_image_must_match_successful_build_output_and_exact_tag(self):
        digest='sha256:'+'a'*64
        run={'status':'Succeeded','outputImages':[{'repository':REPO,'tag':'pinned','digest':digest}]}
        self.assertTrue(verified_build_image(run,digest,'pinned').endswith('@'+digest))
        for field,value in [('digest','sha256:'+'b'*64),('repository','other'),('tag','other')]:
            changed=copy.deepcopy(run);changed['outputImages'][0][field]=value
            with self.assertRaises(ValueError):verified_build_image(changed,digest,'pinned')
        changed=copy.deepcopy(run);changed['status']='Failed'
        with self.assertRaises(ValueError):verified_build_image(changed,digest,'pinned')

    def test_protected_intent_cannot_be_altered_or_claimed_passed(self):
        payload=dict(status='intent',synthetic_only=True)
        digest=fingerprint(payload)
        payload['protected_receipt']=dict(sha256=digest,protected_readback_verified=True,blob='receipt-'+digest+'.json')
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'intent.json';path.write_text(json.dumps(payload))
            self.assertEqual(protected_record(path,'intent'),payload)
            with self.assertRaises(ValueError):protected_record(path,'passed')
            payload['synthetic_only']=False;path.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):protected_record(path,'intent')

    def test_cli_dockerfile_is_absolute_and_no_ambiguous_run_receipt(self):
        context=Path('/tmp/exact-frozen-context')
        args=build_command(context,'unique')
        self.assertEqual(args[args.index('--file')+1],str((context/'Dockerfile').resolve()))
        self.assertEqual(build_run_id('WARNING: Queued a build with ID: ck7u\n'), 'ck7u')
        for invalid in ['null','Queued a build with ID: ck7u\nQueued a build with ID: ck7v']:
            with self.assertRaises(ValueError):build_run_id(invalid)

    def test_execution_accepts_only_observed_empty_optional_storage(self):
        expected=diagnostic_template(self.job(),REGISTRY+'.azurecr.io/'+REPO+'@sha256:'+'a'*64)
        actual=copy.deepcopy(expected)
        actual['containers'][0]['resources'].update(cpu=1.0,ephemeralStorage='')
        validate_execution_template(actual,expected)
        self.assertEqual(actual['containers'][0]['resources']['ephemeralStorage'],'')

    def test_execution_storage_capacity_and_other_resource_changes_denied(self):
        expected=diagnostic_template(self.job(),REGISTRY+'.azurecr.io/'+REPO+'@sha256:'+'a'*64)
        for field,value in [('ephemeralStorage','1Gi'),('ephemeralStorage',None),
                            ('cpu',2),('memory','4Gi'),('gpu',1)]:
            actual=copy.deepcopy(expected);actual['containers'][0]['resources'][field]=value
            with self.assertRaises(ValueError):validate_execution_template(actual,expected)

    def test_execution_extensions_credentials_and_sidecars_denied(self):
        expected=diagnostic_template(self.job(),REGISTRY+'.azurecr.io/'+REPO+'@sha256:'+'a'*64)
        for change in [lambda t:t.update(volumes=[{'name':'extra'}]),
                       lambda t:t.update(initContainers=[{'name':'extra'}]),
                       lambda t:t['containers'].append(copy.deepcopy(t['containers'][0])),
                       lambda t:t['containers'][0].update(volumeMounts=[{'volumeName':'extra'}]),
                       lambda t:t['containers'][0].update(probes=[{'type':'Liveness'}]),
                       lambda t:t['containers'][0]['env'].append({'name':'DATABASE_URL','secretRef':'extra'})]:
            actual=copy.deepcopy(expected);change(actual)
            with self.assertRaises(ValueError):validate_execution_template(actual,expected)


if __name__=='__main__':unittest.main()
