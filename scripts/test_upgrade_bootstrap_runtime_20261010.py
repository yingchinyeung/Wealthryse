import copy, json, unittest
from control_upgrade_bootstrap_runtime_20261010 import RELEASE, accept
from deploy_upgrade_bootstrap_runtime_20261010 import validate_trial


class BootstrapGuards(unittest.TestCase):
    def setUp(self):
        self.expected = json.loads((RELEASE / 'native-build-qualified.json').read_bytes())['proofs'][1]
        self.proof = {'passed': True, 'runtime': copy.deepcopy(self.expected['runtime']), 'source_files': 530,
            'source_manifest_sha256': self.expected['source_manifest_sha256'], 'entrypoint_imported': True,
            'missing_credentials_rejected_before_network': True, 'verified_tls_readonly_connection': True,
            'financial_rows_read': 0, 'database_writes': 0, 'migrations_run': 0, 'roles_or_passwords_changed': False}

    def test_valid(self): accept(self.proof, self.expected)
    def test_wrong_runtime(self):
        self.proof['runtime']['python_version'] = '3.12.0'
        with self.assertRaises(ValueError): accept(self.proof, self.expected)
    def test_wrong_source(self):
        self.proof['source_manifest_sha256'] = '0' * 64
        with self.assertRaises(ValueError): accept(self.proof, self.expected)
    def test_each_check_required(self):
        for k in ('entrypoint_imported', 'missing_credentials_rejected_before_network', 'verified_tls_readonly_connection'):
            p = dict(self.proof, **{k: False})
            with self.assertRaises(ValueError): accept(p, self.expected)
    def test_writes_and_boolean_counts_rejected(self):
        for k in ('financial_rows_read', 'database_writes', 'migrations_run'):
            for value in (1, False):
                with self.assertRaises(ValueError): accept(dict(self.proof, **{k: value}), self.expected)
    def test_wrong_trial_image_or_settings_rejected(self):
        r = {'passed': True, 'image': 'candidate', 'settings_preserved': True, 'proof': self.proof}
        validate_trial(r, 'candidate', self.expected)
        with self.assertRaises(ValueError): validate_trial(r, 'other', self.expected)
        r['settings_preserved'] = False
        with self.assertRaises(ValueError): validate_trial(r, 'candidate', self.expected)


if __name__ == '__main__': unittest.main()
