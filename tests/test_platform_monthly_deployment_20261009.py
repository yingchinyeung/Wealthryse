"""Release acceptance must reject incomplete/incorrect/side-effectful evidence."""
import ast
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/deploy_platform_monthly_worker_v2_20261009.py'

class MonthlyReleaseGate(unittest.TestCase):
    def setUp(self):
        tree=ast.parse(SCRIPT.read_text(encoding='utf-8'))
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='runtime')
        self.file=MagicMock();self.file.__truediv__.return_value=self.file
        self.ns={'json':json,'release':SimpleNamespace(BUNDLE=self.file)}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(SCRIPT),'exec'),self.ns)
        self.result={'passed':True,'database_writes':0,'blob_writes':0,'model_calls':0,'report_approvals_changed':False,
            'normal_subject_paths':[{'subject':s,'status':'already_generated'} for s in ('alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606')]}
        self.envelope={'status':'Succeeded','image':'image@sha256:approved','job_settings_preserved':True}

    def check(self):
        value={**self.envelope,'records':[{'Result':json.dumps(self.result)}]}
        self.file.read_bytes.return_value=json.dumps(value).encode()
        return self.ns['runtime']('proof','image@sha256:approved')

    def test_exact_readonly_acceptance(self):self.assertEqual(self.check(),self.result)
    def test_wrong_image(self):
        self.envelope['image']='different'
        with self.assertRaises(ValueError):self.check()
    def test_missing_account(self):
        self.result['normal_subject_paths'].pop()
        with self.assertRaises(ValueError):self.check()
    def test_generation_attempt(self):
        self.result['normal_subject_paths'][0]['status']='generation_required'
        with self.assertRaises(ValueError):self.check()
    def test_writes_or_model_calls(self):
        for key in ('database_writes','blob_writes','model_calls'):
            with self.subTest(key=key):
                self.result[key]=1
                with self.assertRaises(ValueError):self.check()
                self.result[key]=0
    def test_approval_changes(self):
        self.result['report_approvals_changed']=True
        with self.assertRaises(ValueError):self.check()
    def test_settings_drift(self):
        self.envelope['job_settings_preserved']=False
        with self.assertRaises(ValueError):self.check()
    def test_unsuccessful_run(self):
        self.envelope['status']='Failed'
        with self.assertRaises(ValueError):self.check()
