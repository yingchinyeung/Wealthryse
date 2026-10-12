import importlib.util,sys,types,unittest
from pathlib import Path
from unittest.mock import patch

class IsolationTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('all_reports_tested',Path(__file__).with_name('probe_upgrade_all_reports_20261011.py'))
        self.m=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'qa_boundary':types.ModuleType('qa_boundary')}):spec.loader.exec_module(self.m)
        self.args=['https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net','upgrade-g04-reports-20261010','review/alpha-world-t/2026-09-30/a.pdf',{'overwrite':False},'alpha-world-t']
    def test_valid(self):self.assertTrue(self.m.upload_allowed(*self.args))
    def test_production_container(self):
        self.args[1]='internal-reports';self.assertFalse(self.m.upload_allowed(*self.args))
    def test_wrong_subject(self):
        self.args[4]='alpha-world-ms';self.assertFalse(self.m.upload_allowed(*self.args))
    def test_mutable(self):
        self.args[3]={'overwrite':True};self.assertFalse(self.m.upload_allowed(*self.args))
    def test_traversal(self):
        self.args[2]='review/alpha-world-t/2026-09-30/../a.pdf';self.assertFalse(self.m.upload_allowed(*self.args))
    def test_wrong_host(self):
        self.args[0]='https://elsewhere.invalid';self.assertFalse(self.m.upload_allowed(*self.args))
    def test_boundary_source(self):
        source=Path(self.m.__file__).read_text()
        for guard in ('Non-QA connection prohibited','Empty QA generation registry required','EXPECTED_SNAPSHOTS','Snapshots or approvals changed','verify-full','Expected ten QA generation records'):
            self.assertIn(guard,source)

if __name__=='__main__':unittest.main()
