import unittest
from pathlib import Path
from qualify_viewer_private_admin_login_20261008 import ssh_command,principals,owner_certificate_matches

class PinnedLoginTests(unittest.TestCase):
    def test_certificate_owner_case_not_additional_or_other_principals(self):
        self.assertTrue(owner_certificate_matches(['OWNER@example.test'],'owner@example.test'))
        for names in ([],['other@example.test'],['owner@example.test','other@example.test']):
            self.assertFalse(owner_certificate_matches(names,'owner@example.test'))
    def test_native_ssh_never_disables_host_verification(self):
        cmd=ssh_command(12345,'owner',Path('temp/private'),Path('temp/cert'),Path('temp/path with spaces/known-hosts'))
        self.assertEqual(cmd.count('StrictHostKeyChecking=yes'),1)
        self.assertNotIn('StrictHostKeyChecking=no',cmd)
        self.assertIn('BatchMode=yes',cmd);self.assertIn('IdentitiesOnly=yes',cmd)
        self.assertNotIn('network',cmd)
        self.assertIn('HostKeyAlias=ibkrv2stg-private-admin',cmd)
        quoted=ssh_command(12345,'owner',Path('private path/key'),Path('certificate path/cert'),Path('known host path/file'))
        self.assertIn('CertificateFile="certificate path/cert"',quoted)
    def test_only_certificate_principal_section_is_parsed(self):
        public='Type: user certificate\nPrincipals:\n        owner@example.test\nCritical Options:\n        (none)\nExtensions:\n        permit-pty\n'
        self.assertEqual(principals(public),['owner@example.test'])
        self.assertEqual(principals(public.replace('Critical Options:\n        (none)','Critical Options: (none)')),['owner@example.test'])
        self.assertEqual(principals('No principal section'),[])

if __name__=='__main__':unittest.main()
