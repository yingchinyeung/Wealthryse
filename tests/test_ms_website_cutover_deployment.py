import copy
import json
import unittest

import deploy_ms_website_cutover as deployment
import control_ms_website_exact_cutover as control


class DeploymentBoundaries(unittest.TestCase):
    def test_template_normalization_preserves_commands_credentials_and_resources(self):
        value={'containers':[{'name':'web','image':'old-digest','command':['python'],
            'args':['-m','uvicorn','ibkr_etl.web:app'],'resources':{'cpu':.5,'memory':'1Gi'},
            'env':[{'name':'DATABASE_URL','secretRef':'unchanged-secret','value':''},
                   {'name':'EMPTY_SETTING','value':''}]}],'initContainers':None,'volumes':None}
        result=deployment.normalize_template(value)
        self.assertEqual(result['containers'][0]['env'],[
            {'name':'DATABASE_URL','secretRef':'unchanged-secret'},{'name':'EMPTY_SETTING','value':''}])
        self.assertEqual(result['containers'][0]['command'],value['containers'][0]['command'])
        self.assertEqual(result['containers'][0]['resources'],value['containers'][0]['resources'])
        self.assertEqual(value['containers'][0]['env'][0]['value'],'')

    def test_image_revision_only_changes_do_not_mask_auth_or_resource_changes(self):
        baseline=json.loads((control.r.FOLDER/'baseline.json').read_bytes())['website']
        changed=copy.deepcopy(baseline)
        changed['properties']['template']['containers'][0]['image']='candidate-digest'
        changed['properties']['template']['revisionSuffix']='new-release'
        changed['properties']['configuration']['ingress']['traffic'][0]['revisionName']='new-ready-revision'
        self.assertEqual(deployment.web_settings(changed),deployment.web_settings(baseline))
        for fault in ('resource','command','credential','traffic'):
            wrong=copy.deepcopy(changed);container=wrong['properties']['template']['containers'][0]
            if fault=='resource':container['resources']['memory']='4Gi'
            elif fault=='command':container['args']=['different-entrypoint']
            elif fault=='credential':container['env'][0]['secretRef']='different-credential'
            else:wrong['properties']['configuration']['ingress']['traffic'][0]['weight']=50
            with self.subTest(fault=fault):self.assertNotEqual(deployment.web_settings(wrong),deployment.web_settings(baseline))

    def test_frozen_query_extraction_rejects_ambiguous_or_dynamic_source(self):
        self.assertEqual(control.literal_assignment("queries={'subject':'SELECT subject_id'}",'queries'),{'subject':'SELECT subject_id'})
        for source in ("queries={};queries={}","queries=run_untrusted_code()"):
            with self.assertRaises((ValueError,TypeError)):control.literal_assignment(source,'queries')
