import copy,unittest
from apply_upgrade_report_repair_20261009 import validate_package
TENANT='00000000-0000-0000-0000-000000000001'
def sample():
    items=[];blobs={}
    for i in range(375):
        digest=f'{i:064x}';old={'generation_run_id':i,'tenant_id':TENANT,'status':'awaiting_approval','output_reference':f'internal-reports/review/U16088571/2026-07-31/report{i}.pdf','output_sha256':digest}
        target=f'internal-reports/review/U16088571/2026-07-31/report{i}__recovered_{digest}.pdf'
        items.append({'old_row':old,'after_row':{**old,'output_reference':target}});blobs[target.split('/',1)[1]]={'sha256':digest}
    return {'passed':True,'clone_update_and_rollback_passed':True,'qualified':items,'copied_blobs':blobs}
class Tests(unittest.TestCase):
    def test_exact_scope(self):self.assertEqual(len(validate_package(sample(),TENANT)),375)
    def test_hash_change_rejected(self):
        p=sample();p['qualified'][0]['after_row']['output_sha256']='a'*64
        with self.assertRaises(ValueError):validate_package(p,TENANT)
    def test_released_rejected(self):
        p=sample();p['qualified'][0]['old_row']['status']='released_internal'
        with self.assertRaises(ValueError):validate_package(p,TENANT)
    def test_foreign_subject_rejected(self):
        p=sample();p['qualified'][0]['old_row']['output_reference']='internal-reports/review/other/2026-07-31/a.pdf'
        with self.assertRaises(ValueError):validate_package(p,TENANT)
    def test_missing_row_rejected(self):
        p=sample();p['qualified'].pop()
        with self.assertRaises(ValueError):validate_package(p,TENANT)
    def test_status_change_rejected(self):
        p=sample();p['qualified'][0]['after_row']['status']='released_internal'
        with self.assertRaises(ValueError):validate_package(p,TENANT)
    def test_unverified_bytes_rejected(self):
        p=sample();next(iter(p['copied_blobs'].values()))['sha256']='a'*64
        with self.assertRaises(ValueError):validate_package(p,TENANT)
if __name__=='__main__':unittest.main()
