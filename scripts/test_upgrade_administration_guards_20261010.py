import copy, unittest
from control_upgrade_administration_runtime_20261010 import accept
from deploy_upgrade_administration_security_20261010 import validate_trial


class AdministrationGuards(unittest.TestCase):
    def setUp(self):
        self.expected = {'source_files': 430, 'source_manifest_sha256': 'a' * 64,
            'python': '3.12.15', 'openssl': 'expected', 'tests': 21, 'entry_points_imported': ['fixture.py']}
        self.proof = dict(self.expected, passed=True, missing_credentials_rejected=True,
            verified_tls_readonly_connection=True, normal_administration_executed=False,
            financial_rows_read=0, database_writes=0, messages_sent=0, arm_mutations=0)

    def test_accept(self): accept(self.proof, self.expected)

    def test_identity_fields(self):
        for key in self.expected:
            with self.subTest(key=key), self.assertRaises(ValueError):
                accept(dict(self.proof, **{key: 'wrong'}), self.expected)

    def test_required_checks(self):
        for key in ('passed', 'missing_credentials_rejected', 'verified_tls_readonly_connection'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                accept(dict(self.proof, **{key: False}), self.expected)

    def test_zero_mutations_strict(self):
        for key in ('financial_rows_read', 'database_writes', 'messages_sent', 'arm_mutations'):
            for value in (1, False, None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    accept(dict(self.proof, **{key: value}), self.expected)

    def test_normal_job_forbidden(self):
        with self.assertRaises(ValueError): accept(dict(self.proof, normal_administration_executed=True), self.expected)

    def test_trial_binding(self):
        result = {'passed': True, 'image': 'exact', 'settings_preserved': True, 'proof': self.proof}
        validate_trial(result, 'exact', self.expected)
        for key, value in (('passed', False), ('settings_preserved', False), ('image', 'other')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_trial(dict(result, **{key: value}), 'exact', self.expected)


if __name__ == '__main__': unittest.main()
