import json,unittest
from qualify_viewer_private_admin_20261008 import parse_evidence,PREFIX,TOOLS

class PrivateAdminAcceptanceTests(unittest.TestCase):
    def test_login_requires_nonroot_actual_admin_and_no_unknown_fields(self):
        good={'kind':'login','uid':1001,'passwordless_admin':True,'hostname_sha256':'a'*64}
        self.assertEqual(parse_evidence(PREFIX+json.dumps(good),'login'),good)
        for bad in ({**good,'uid':0},{**good,'passwordless_admin':False},{**good,'token':'never-retain'}):
            with self.assertRaises(ValueError):parse_evidence(PREFIX+json.dumps(bad),'login')
    def test_rejects_raw_duplicate_and_wrong_kind_evidence(self):
        with self.assertRaises(ValueError):parse_evidence('raw authentication output','login')
        line=PREFIX+json.dumps({'kind':'login','uid':1001,'passwordless_admin':True,'hostname_sha256':'a'*64})
        with self.assertRaises(ValueError):parse_evidence(line+'\n'+line,'login')
        with self.assertRaises(ValueError):parse_evidence(line,'tools')
    def test_local_build_cannot_claim_registry_push(self):
        good={'kind':'tools','azure_cli_version':'fixture','docker_version':'fixture','local_build_image_id':'sha256:'+'a'*64,'registry_push_tested':False,'source_data_in_build':False}
        self.assertEqual(parse_evidence(PREFIX+json.dumps(good),'tools'),good)
        with self.assertRaises(ValueError):parse_evidence(PREFIX+json.dumps({**good,'registry_push_tested':True}),'tools')
        self.assertIn('--network=none',TOOLS)
        self.assertNotIn('az login',TOOLS)
        self.assertNotIn('docker push',TOOLS)

if __name__=='__main__':unittest.main()
