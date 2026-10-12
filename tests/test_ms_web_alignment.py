"""Fail-closed control checks; no Azure access and no artifact generation."""
import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import align_alpha_ms_web_v14r13 as control


def website():
    return {'identity':{'type':'SystemAssigned','principalId':'unchanged'},'properties':{
        'environmentId':'environment','provisioningState':'Succeeded',
        'latestReadyRevisionName':control.REVISION,
        'template':{'revisionSuffix':'old','containers':[{'image':control.IMAGE,
            'name':'web','env':[{'name':'DATABASE_URL','secretRef':'db'}],
            'resources':{'cpu':.5,'memory':'1Gi'}}],'scale':{'minReplicas':1}},
        'configuration':{'secrets':[{'name':'db','keyVaultUrl':'unchanged'}],
            'ingress':{'external':True,'traffic':[{'revisionName':control.REVISION,'weight':100}]}}}}


class AlignmentSafeguards(unittest.TestCase):
    def test_only_image_and_revision_names_are_excluded(self):
        before=website();after=copy.deepcopy(before)
        after['properties']['template']['revisionSuffix']='new'
        after['properties']['template']['containers'][0]['image']='new-image'
        after['properties']['configuration']['ingress']['traffic'][0]['revisionName']='new-revision'
        self.assertEqual(control.web_invariant(before),control.web_invariant(after))
        self.assertEqual(before,website(), 'Invariant must not mutate its input')

    def test_security_credentials_and_resource_drift_are_not_excluded(self):
        before=website()
        for field in ('identity','environment','secret','env','resources','traffic','ingress'):
            after=copy.deepcopy(before)
            if field=='identity':after['identity']['principalId']='different'
            elif field=='environment':after['properties']['environmentId']='different'
            elif field=='secret':after['properties']['configuration']['secrets'][0]['keyVaultUrl']='different'
            elif field=='env':after['properties']['template']['containers'][0]['env'][0]['secretRef']='different'
            elif field=='resources':after['properties']['template']['containers'][0]['resources']['cpu']=1
            elif field=='traffic':after['properties']['configuration']['ingress']['traffic'][0]['weight']=90
            else:after['properties']['configuration']['ingress']['external']=False
            with self.subTest(field=field):
                self.assertNotEqual(control.web_invariant(before),control.web_invariant(after))

    def run_verify(self,live,auth_changed=False):
        before=website();auth={'properties':{'platform':{'enabled':True}}}
        baseline={'web_invariant':control.web_invariant(before),'auth':auth}
        def request(method,url):
            self.assertEqual(method,'GET')
            if '/revisions/' in url:
                return {'properties':{'healthState':'Healthy','active':True,'runningState':'Running'}}
            if '/authConfigs/' in url:
                return {'properties':{'platform':{'enabled':False}}} if auth_changed else auth
            return live
        with tempfile.TemporaryDirectory() as folder, patch.object(control,'FOLDER',Path(folder)), \
                patch.object(control,'read',side_effect=lambda name: baseline if name=='baseline' else {}), \
                patch.object(control.p,'request',side_effect=request),patch.object(control,'save') as save:
            control.verify_web()
            return save

    def test_exact_ready_image_is_recorded_without_release_or_activation(self):
        save=self.run_verify(website())
        proof=save.call_args.args[1]
        self.assertEqual(proof['traffic_percent'],100)
        for key in ('ibkr_source_changed','reports_released','onboarding_activated','historical_batch_replaced'):
            self.assertFalse(proof[key])

    def test_auth_drift_rejects_completion(self):
        with self.assertRaisesRegex(ValueError,'Authentication changed'):
            self.run_verify(website(),auth_changed=True)

    def test_image_drift_rejects_completion(self):
        live=website();live['properties']['template']['containers'][0]['image']='unexpected'
        with self.assertRaisesRegex(ValueError,'image or non-image'):
            self.run_verify(live)

    def test_not_ready_never_records_success(self):
        live=website();live['properties']['latestReadyRevisionName']='old-revision'
        save=self.run_verify(live)
        save.assert_not_called()


if __name__=='__main__':unittest.main()
