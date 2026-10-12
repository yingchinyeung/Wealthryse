import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import deploy_agent_inbox_access as deployment
import check_agent_inbox_access as probe


class InboxAccessDeploymentTests(unittest.TestCase):
    def test_manual_zero_retry_and_separate_credentials(self):
        for stage in deployment.JOBS:
            value=deployment.definition(stage)
            config=value['properties']['configuration']
            self.assertEqual(config['triggerType'],'Manual')
            self.assertEqual(config['replicaRetryLimit'],0)
            self.assertEqual(len(config['secrets']),1)
            secret='database-admin-url' if stage=='grant' else 'database-web-url'
            self.assertTrue(config['secrets'][0]['keyVaultUrl'].endswith('/'+secret))
            self.assertEqual(value['properties']['template']['containers'][0]['image'],deployment.IMAGE)
            deployment.validate_job(value,value)
            observed=json.loads(json.dumps(value))
            observed['properties']['template']['containers'][0]['resources']['ephemeralStorage']=''
            deployment.validate_job(observed,value)

    def test_modified_runtime_or_schedule_rejected(self):
        for key,new in [('triggerType','Schedule'),('replicaRetryLimit',1)]:
            value=deployment.definition('inspect')
            value['properties']['configuration'][key]=new
            with self.assertRaises(ValueError): deployment.validate_job(value,deployment.definition('inspect'))
        value=deployment.definition('inspect')
        value['properties']['template']['containers'][0]['env'].append(dict(name='ADMIN_URL',value='bad'))
        with self.assertRaises(ValueError): deployment.validate_job(value,deployment.definition('inspect'))

    def test_url_allows_only_registered_readonly_login(self):
        url='postgresql://ibkr_stg_web:fictional@'+probe.HOST+'/ibkr_staging?sslmode=require'
        self.assertEqual(probe.validate_url(url),url)
        for changed in (url.replace('ibkr_stg_web','ibkr_app'),url.replace('sslmode=require','sslmode=disable'),url.replace(probe.HOST,'localhost')):
            with self.assertRaises(ValueError): probe.validate_url(changed)

    def test_grant_is_only_two_table_select_not_role_or_source_mutation(self):
        import base64
        args,digest=deployment.payload('grant')
        encoded=args[1].split("b64decode('")[1].split("')")[0]
        source=base64.b64decode(encoded).decode()
        self.assertIn('GRANT SELECT ON public.specialist_daily_reports,public.specialist_report_deliveries TO ibkr_stg_web',source)
        for forbidden in ('ALTER ROLE','CREATE ROLE','WITH GRANT OPTION','UPDATE public.','DELETE FROM','GRANT ALL','SET ROLE'):
            self.assertNotIn(forbidden,source)
        self.assertEqual(len(digest),64)


if __name__=='__main__': unittest.main()
