"""Offline guard regressions for populated-store preflight correction."""
import unittest
from control_upgrade_authority_check_20261010 import shape,OLD,NEW


class Guards(unittest.TestCase):
    def proof(self):return {'status':'passed','database_access':'verified','authority_source_checked':True,'unregistered_principal_denied':True,
       'source_table_access':False,'authority_rows_written':0,'agents_enabled':0,'messages_sent':0,'tenant_authority_rows':{'controls':1,'grants':6}}
    def test_existing_counts_allowed(self):
        for counts in ({'controls':0,'grants':0},{'controls':1,'grants':0},{'controls':1,'grants':6}):
            p=self.proof();p['tenant_authority_rows']=counts;shape(p)
    def test_invalid_counts_rejected(self):
        for counts in ({'controls':0,'grants':1},{'controls':2,'grants':0},{'controls':True,'grants':0},{'controls':1,'grants':-1}):
            p=self.proof();p['tenant_authority_rows']=counts
            with self.assertRaises(ValueError):shape(p)
    def test_side_effects_and_denial_required(self):
        for k,v in (('authority_rows_written',1),('agents_enabled',1),('messages_sent',1),('source_table_access',True),('unregistered_principal_denied',False)):
            p=self.proof();p[k]=v
            with self.subTest(k=k),self.assertRaises(ValueError):shape(p)
    def test_only_precondition_patch(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        before=(root/'.work/platform-upgrade-20261010-stage16/original_authority_worker.py').read_text(encoding='utf-8')
        after=(root/'scripts/run_specialist_authority_worker.py').read_text(encoding='utf-8')
        self.assertEqual(before.count(OLD),1);self.assertEqual(before.replace(OLD,NEW),after)


if __name__=='__main__':unittest.main()
