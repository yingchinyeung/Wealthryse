import base64
import copy
import gzip
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_specialist_report_preview as preview
import deploy_specialist_report_worker as deployment


class PreviewTests(unittest.TestCase):
    def test_override_preserves_installed_worker_and_restricted_credential(self):
        original=deployment.definition(); before=copy.deepcopy(original)
        template=preview.expected_template(original)
        self.assertEqual(original,before)
        self.assertEqual(template['containers'][0]['env'],[{'name':'SPECIALIST_REPORT_DATABASE_URL','secretRef':'report-url'}])
        self.assertEqual(template['containers'][0]['image'],before['properties']['template']['containers'][0]['image'])
        args,digest=preview.bundle()
        source=gzip.decompress(base64.b64decode(args[1].split("b64decode('")[1].split("')")[0])).decode()
        compile(source,'preview','exec')
        self.assertIn('persist=False',source)
        self.assertNotIn('dispatch_daily_email',source)
        self.assertNotIn('DATABASE_URL\']',source.replace("SPECIALIST_REPORT_DATABASE_URL']",''))
        self.assertEqual(len(digest),64)
        self.assertLess(len(args[1]),30000)
        self.assertEqual(template['containers'][0]['resources'],{'cpu':0.5,'memory':'1Gi'})

    def test_scope_and_principal_cannot_expand(self):
        role=preview.ROLES[0]
        p={'principalId':preview.PRINCIPAL,'scope':preview.SCOPE,
           'roleDefinitionId':f'/subscriptions/{preview.SUB}/providers/Microsoft.Authorization/roleDefinitions/{role}'}
        preview.verify_grant(p,role)
        for changes in ({'principalId':'other'},{'scope':preview.SCOPE.rsplit('/',1)[0]},
                        {'roleDefinitionId':'Contributor'},{'condition':'other'}):
            with self.assertRaises(ValueError): preview.verify_grant({**p,**changes},role)


if __name__=='__main__': unittest.main()
