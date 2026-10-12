"""Provider defaults are not execution content or permission to change scope."""
import copy
import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import owner_task_catalog_execution_metadata as c


class ExecutionMetadataTests(unittest.TestCase):
    def test_exact_two_additions_preserve_all_836_predecessors(self):
        before={f'old-{i}.py':'a'*64 for i in range(836)}
        after=dict(before,**{p:'b'*64 for p in c.ADDITIONS})
        c.extension(before,after)
        for changed in (dict(after,**{'old-0.py':'c'*64}),dict(after,**{'extra.py':'c'*64})):
            with self.assertRaises(ValueError):c.extension(before,changed)

    def test_empty_execution_initializers_normalized_without_mutating_input(self):
        value={'containers':[{'name':'fixed'}],'initContainers':[]};original=copy.deepcopy(value)
        seen=[]
        result=c.execution_template(value,{'containers':[{'name':'fixed'}]},lambda a,b:seen.append((a,b)) or 'ok')
        self.assertEqual(result,'ok');self.assertEqual(value,original)
        self.assertEqual(seen[0][0],{'containers':[{'name':'fixed'}]})

    def test_nonempty_initializers_rejected_before_old_validator(self):
        for v in ([{'name':'unexpected'}],{},False,'',0):
            with self.subTest(value=v),self.assertRaises(ValueError):
                c.execution_template({'initContainers':v},{},lambda a,b:self.fail('validator reached'))

    def test_other_template_fields_are_not_removed(self):
        value={'initContainers':[],'volumes':[{'name':'unexpected'}],'containers':[]}
        seen=[];c.execution_template(value,{},lambda a,b:seen.append(a))
        self.assertEqual(seen[0]['volumes'],value['volumes'])

    def test_only_registered_job_secret_alias_normalized(self):
        alias='cappjob-'+c.prior.base.worker.JOB
        registered=dict(name='OWNERTRIAL_ADMIN_URL',secretRef=c.prior.base.worker.host.SECRET_NAME)
        expected={'containers':[{'env':[registered]}]}
        actual={'containers':[{'env':[dict(name=registered['name'],secretRef=alias)]}]}
        original=copy.deepcopy(actual);seen=[]
        c.execution_template(actual,expected,lambda a,b:seen.append(a))
        self.assertEqual(seen[0],expected);self.assertEqual(actual,original)

    def test_alias_cannot_hide_extra_values_or_retarget_secret(self):
        alias='cappjob-'+c.prior.base.worker.JOB
        registered=dict(name='OWNERTRIAL_ADMIN_URL',secretRef=c.prior.base.worker.host.SECRET_NAME)
        expected={'containers':[{'env':[registered]}]}
        for env in ([dict(name=registered['name'],secretRef=alias,value='unexpected')],
                    [dict(name='OTHER',secretRef=alias)]):
            with self.subTest(env=env),self.assertRaises(ValueError):
                c.execution_template({'containers':[{'env':env}]},expected,lambda a,b:self.fail('validator reached'))
        with self.assertRaises(ValueError):
            c.execution_template({'containers':[{'env':[dict(name=registered['name'],secretRef=alias)]}]},
                {'containers':[{'env':[dict(name=registered['name'],secretRef='other-secret')]}]},lambda a,b:self.fail('validator reached'))

    def test_other_jobs_alias_not_normalized(self):
        env=[dict(name='OWNERTRIAL_ADMIN_URL',secretRef='cappjob-other-job')]
        seen=[];c.execution_template({'containers':[{'env':env}]},{},lambda a,b:seen.append(a))
        self.assertEqual(seen[0]['containers'][0]['env'],env)

    def test_original_job_validator_stays_bound_to_original_namespace(self):
        n=c.control();self.assertIsNot(n['collect'].__globals__,n['checked_job'].__globals__)
        self.assertEqual(n['checked_job'].__globals__['checked_template'],n['checked_template'])

    def test_preserved_original_markers_and_no_new_dispatch_cli(self):
        text=(ROOT/c.PATH).read_text()
        self.assertIn("choices=('review','collect')",text)
        self.assertNotIn("client.call('PUT'",text);self.assertNotIn("client.call('POST'",text)
        self.assertEqual(c.INSPECTION_SHA,'f7d7a3b621f94198b0c7a172175fd06f3b7cfd57da307a00ea03e346ead073ff')


if __name__=='__main__':unittest.main()
