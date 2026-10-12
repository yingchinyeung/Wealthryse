import importlib.util,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
from test_upgrade_all_reports_20261011 import IsolationTests

class CorrectedIsolationTests(IsolationTests):
    def setUp(self):
        super().setUp()
        spec=importlib.util.spec_from_file_location('reports_v2_tested',Path(__file__).with_name('probe_upgrade_all_reports_v2_20261011.py'))
        self.m=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'qa_boundary':types.ModuleType('qa_boundary')}):spec.loader.exec_module(self.m)
    def test_pilot_exception_is_read_only_and_hash_bound(self):
        source=Path(self.m.__file__).read_text()
        self.assertIn("content=download(url,'internal-reports',name,**kwargs)",source)
        self.assertIn("q.SUBJECT!='U16088571'",source)
        self.assertIn("hashlib.sha256(content).hexdigest()!=source_template['FACTSHEET_TEMPLATE_SHA256']",source)
        self.assertNotIn("upload(url,'internal-reports'",source)
    def test_other_subjects_clear_pilot_template(self):
        source=Path(self.m.__file__).read_text()
        self.assertIn("if subject=='U16088571':os.environ.update(source_template)",source)
        self.assertIn('for key in source_template:os.environ.pop(key,None)',source)

if __name__=='__main__':unittest.main()
