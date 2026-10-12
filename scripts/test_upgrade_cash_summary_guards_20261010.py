import copy,unittest
from unittest.mock import patch
import deploy_upgrade_cash_summary_20261010 as d

class Guards(unittest.TestCase):
    def web(self):return {'identity':{'type':'UserAssigned'},'properties':{'environmentId':'env','configuration':{'ingress':{'external':True,'traffic':[{'weight':100,'latestRevision':True}]},'secrets':[{'name':'db','keyVaultUrl':'https://example.vault.azure.net/secrets/db'}]},'template':{'revisionSuffix':'old','containers':[{'image':'old','env':[{'name':'DATABASE_URL','secretRef':'db','value':''}],'probes':[{'type':'Readiness','httpGet':{'path':'/readyz'}}]}],'scale':{'minReplicas':1}}}}
    def test_image_and_suffix_only(self):
        live=self.web();before=copy.deepcopy(live);result=d.body(live,'new','revision')
        self.assertEqual(set(result['properties']),{'template'});self.assertEqual(result['properties']['template']['containers'][0]['image'],'new');self.assertEqual(live,before)
    def test_probes_and_scale(self):
        live=self.web();value=d.body(live,'new','revision')['properties']['template']
        self.assertEqual(value['scale'],live['properties']['template']['scale']);self.assertEqual(value['containers'][0]['probes'],live['properties']['template']['containers'][0]['probes'])
    def test_no_permission_or_credential_patch(self):
        value=d.body(self.web(),'new','revision');self.assertNotIn('identity',value);self.assertNotIn('configuration',value['properties']);self.assertEqual(value['properties']['template']['containers'][0]['env'],[{'name':'DATABASE_URL','secretRef':'db'}])
    def test_extra_container(self):
        value=self.web();value['properties']['template']['containers'].append({'image':'extra'})
        with self.assertRaises(ValueError):d.body(value,'new','revision')
    def test_other_workload_drift(self):
        with patch.object(d,'current_resources',return_value=[]),patch.object(d,'bindings',return_value={'ibkrv2stg-web':{},'job':{'images':['wrong']}}),self.assertRaises(ValueError):d.unrelated({'ibkrv2stg-web':{},'job':{'images':['old']}})
    def test_removed_workload(self):
        with patch.object(d,'current_resources',return_value=[]),patch.object(d,'bindings',return_value={'ibkrv2stg-web':{}}),self.assertRaises(ValueError):d.unrelated({'ibkrv2stg-web':{},'job':{}})
    def test_only_web_image_can_change(self):
        with patch.object(d,'current_resources',return_value=[]),patch.object(d,'bindings',return_value={'ibkrv2stg-web':{'images':['new']},'job':{}}):d.unrelated({'ibkrv2stg-web':{'images':['old']},'job':{}})

if __name__=='__main__':unittest.main()
