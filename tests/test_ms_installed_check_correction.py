import base64
import copy
import gzip
import unittest
from unittest.mock import patch

import verify_installed_ms_worker_release as c


class InstalledCheckCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.job={'properties':{'template':{'containers':[{'name':'report','image':'exact-candidate',
            'command':['python'],'args':['normal.py'],'resources':{'cpu':1,'memory':'2Gi'},
            'env':[{'name':'DATABASE_URL','secretRef':'existing-db','value':''},
                   {'name':'OTHER','value':'unchanged'}]}],'initContainers':None,'volumes':None}}}

    def test_only_null_fields_empty_secret_value_and_source_encoding_change(self):
        old=copy.deepcopy(self.job);source='print("bounded check")\n'
        actual=c.corrected_template(self.job,source)
        self.assertEqual(self.job,old)
        self.assertNotIn('initContainers',actual);self.assertNotIn('volumes',actual)
        container=actual['containers'][0]
        encoded=container['args'][1].split("base64.b64decode('",1)[1].split("')",1)[0]
        self.assertEqual(gzip.decompress(base64.b64decode(encoded)).decode(),source)
        self.assertEqual(container['image'],'exact-candidate')
        self.assertEqual(container['resources'],old['properties']['template']['containers'][0]['resources'])
        self.assertEqual(container['env'][0],{'name':'DATABASE_URL','secretRef':'existing-db'})
        self.assertEqual(container['env'][1],{'name':'OTHER','value':'unchanged'})

    def test_nonempty_value_is_not_erased(self):
        self.job['properties']['template']['containers'][0]['env'][0]['value']='not-a-real-secret'
        self.assertEqual(c.corrected_template(self.job,'pass')['containers'][0]['env'][0]['value'],'not-a-real-secret')

    def test_existing_execution_prevents_new_dispatch(self):
        args=['-c','same-check']
        with patch.object(c,'request',return_value={'value':[{'properties':{'template':{'containers':[{'args':args}]}}}]}):
            with self.assertRaisesRegex(ValueError,'Matching execution'):
                c.no_matching_execution(c.release.build.JOBS[0],[args])

    def test_multi_container_request_blocks(self):
        self.job['properties']['template']['containers'].append(copy.deepcopy(self.job['properties']['template']['containers'][0]))
        with self.assertRaisesRegex(ValueError,'Single installed'):
            c.corrected_template(self.job,'pass')
