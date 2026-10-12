import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import deploy_specialist_daily_schedule as daily
import deploy_specialist_report_watchdog as watchdog


class DailyDeploymentTests(unittest.TestCase):
    def current(self, scheduled=False):
        value=daily.wanted(scheduled); value['properties']['provisioningState']='Succeeded'
        return value

    def test_manual_then_exact_six_pm_hong_kong_cron(self):
        daily.validate(self.current())
        scheduled=self.current(True); daily.validate(scheduled,True)
        config=scheduled['properties']['configuration']
        self.assertEqual(config['scheduleTriggerConfig']['cronExpression'],'0 10 * * *')
        self.assertEqual(config['replicaRetryLimit'],0)
        self.assertEqual(config['scheduleTriggerConfig']['parallelism'],1)
        self.assertNotIn('manualTriggerConfig',config)
        self.assertEqual(len(config['secrets']),1)
        self.assertIn('database-specialist-report-url',config['secrets'][0]['keyVaultUrl'])

    def test_runtime_bundles_rehearsal_only_when_requested(self):
        import base64, gzip, re
        args,_=daily.bundle(); rehearsal,_=daily.bundle(True)
        def decode(args):
            encoded=re.search("base64.b64decode\\('([^']+)'",args[1])[1]
            return gzip.decompress(base64.b64decode(encoded)).decode()
        self.assertNotIn("REPLAY_DATE='2026-10-02'",decode(args))
        self.assertIn("REPLAY_DATE='2026-10-02'",decode(rehearsal))
        compile(decode(args),'candidate','exec')
        compile(decode(rehearsal),'rehearsal','exec')

    def test_reject_retries_different_cron_or_extra_secret(self):
        for mutate in (lambda v:v['properties']['configuration'].update(replicaRetryLimit=1),
                       lambda v:v['properties']['configuration']['scheduleTriggerConfig'].update(cronExpression='0 18 * * *'),
                       lambda v:v['properties']['configuration']['secrets'].append({'name':'admin-url'}),
                       lambda v:v['properties']['template']['containers'][0]['env'].append({'name':'DATABASE_URL','secretRef':'admin-url'}),
                       lambda v:v['identity'].update(type='SystemAssigned,UserAssigned'),
                       lambda v:v['properties']['template'].update(initContainers=[{'name':'unreviewed'}]),
                       lambda v:v['properties'].update(provisioningState='Updating'),
                       lambda v:v['properties']['template']['containers'][0].update(image='unreviewed:latest')):
            value=self.current(True); mutate(value)
            with self.assertRaises(ValueError): daily.validate(value,True)

    def test_synthetic_watchdog_cases_include_no_job(self):
        query,cases=watchdog.synthetic_query()
        self.assertEqual(cases['never_started'],1)
        self.assertEqual(cases['job_success_without_publication'],1)
        self.assertEqual(cases['validated_publication'],0)
        self.assertIn('datatable(TimeGenerated:datetime,ContainerGroupName_s:string,Log_s:string)[]',query)

    def test_job_inventory_continuation_is_exact_scope(self):
        from deploy_metadata_monitor import BASE
        good='https://management.azure.com'+BASE+'Microsoft.App/jobs?api-version=2024-03-01&%24skiptoken=next'
        self.assertIn('$skiptoken',daily.jobs_page_path(good).replace('%24','$'))
        for bad in (good.replace('management.azure.com','foreign.example'), good.replace('/jobs?','/containerApps?'),
                    good.replace('2024-03-01','2021-01-01'), good+'&extra=value'):
            with self.assertRaises(ValueError): daily.jobs_page_path(bad)

    def test_job_inventory_follows_all_pages_before_freezing(self):
        from unittest.mock import patch
        from deploy_metadata_monitor import BASE
        link='https://management.azure.com'+BASE+'Microsoft.App/jobs?api-version=2024-03-01&%24skiptoken=next'
        jobs=[dict(name=name,properties=dict(configuration={},template={},environmentId='fixed')) for name in ('first','last')]
        pages=[dict(value=[jobs[0]],nextLink=link),dict(value=[jobs[1]]),dict(properties={}),daily.ready_definition(True),{}]
        with patch.object(daily,'request',side_effect=pages),patch.object(daily,'invariant',return_value='fixed'):
            result=daily.unaffected()
        self.assertTrue(result['job_inventory_complete'])
        self.assertEqual(set(result['other_jobs']),{'first','last'})

    def test_azure_empty_registry_defaults_not_new_credentials(self):
        value=self.current()
        row=value['properties']['configuration']['registries'][0]
        row.update(username='',passwordSecretRef='')
        daily.validate(value)
        for bad in ('admin',False):
            row['username']=bad
            with self.assertRaises(ValueError): daily.validate(value)

    def test_watchdog_activation_preserves_complete_tag_map(self):
        patch=watchdog.activation_patch()
        self.assertEqual(patch['tags'],watchdog.wanted(True)['tags'])
        self.assertEqual(set(patch['tags']),{'application','purpose','mode'})
        self.assertEqual(patch['properties'],{'enabled':True})


if __name__=='__main__': unittest.main()
