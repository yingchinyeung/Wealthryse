import hashlib,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class Result:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value
class Db:
    def __init__(self,approval):self.approval=approval
    def execute(self,query):
        if not query.startswith('SELECT ') or 'ibkr_stg_approval' not in query:raise AssertionError('Read-only exact role query required')
        return Result(self.approval)
class Tests(unittest.TestCase):
    def state(self,web,approval):
        namespace={'state':lambda db:{'web_privileges':web},'__name__':'test'}
        exec((ROOT/'scripts/handover_role_boundary_20261011.py').read_text(),namespace)
        return namespace['state'](Db(approval))
    def test_separate_roles(self):
        value=self.state({'select_ok':True,'insert_ok':False,'update_ok':False},{'select_ok':True,'insert_ok':True,'update_ok':True})
        self.assertTrue(all(value['web_privileges'].values()))
    def test_web_insert_denied(self):
        with self.assertRaises(ValueError):self.state({'select_ok':True,'insert_ok':True,'update_ok':False},{})
    def test_web_update_denied(self):
        with self.assertRaises(ValueError):self.state({'select_ok':True,'insert_ok':False,'update_ok':True},{})
    def test_web_missing_read_denied(self):
        with self.assertRaises(ValueError):self.state({'select_ok':False,'insert_ok':False,'update_ok':False},{})
    def test_approval_missing_update_fails_gate(self):
        value=self.state({'select_ok':True,'insert_ok':False,'update_ok':False},{'select_ok':True,'insert_ok':True,'update_ok':False})
        self.assertFalse(all(value['web_privileges'].values()))
    def test_exact_qualified_migration(self):
        raw=(ROOT/'migrations/postgres/062_report_design_updates.sql').read_text()
        self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(),'63710f9e0d6d0e305dc8ec4fefd1389b781f864b35370c3005409fbdf5e00ae9')
        self.assertNotIn('GRANT ',raw.upper())
        self.assertNotIn('DELETE ',raw.upper())
        self.assertNotIn('UPDATE ',raw.upper())
if __name__=='__main__':unittest.main()
