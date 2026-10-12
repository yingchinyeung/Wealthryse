"""Offline guard tests; no Azure calls or image execution."""
import copy
import json
from pathlib import Path
import unittest
import scan_upgrade_remaining_images_20261010 as batch
from scan_upgrade_special_references_20261010 import platform_manifest, redirect_allowed


class Guards(unittest.TestCase):
    def test_only_unmatched_digest_pinned_acr_targets(self):
        data = json.loads(batch.CHECKPOINT.read_bytes())
        result = batch.targets(data)
        self.assertEqual(10, len(result))
        self.assertTrue(all(v[2].startswith('sha256:') for v in result.values()))
        self.assertNotIn('ibkrv2stg-ownertrial-log-canary', result)
        self.assertNotIn('ibkrv2stg-import-u16088571', result)
        self.assertNotIn('ibkrv2stg-autonomous-admin', result)

    def test_changed_target_count_rejected(self):
        data = json.loads(batch.CHECKPOINT.read_bytes())
        data['images'] = []
        with self.assertRaises(ValueError): batch.targets(data)

    def test_unique_linux_amd64(self):
        wanted = {'digest':'sha256:'+'a'*64,'size':10,'platform':{'os':'linux','architecture':'amd64'}}
        data = {'schemaVersion':2,'manifests':[wanted,{'platform':{'os':'linux','architecture':'arm64'}}]}
        self.assertEqual(wanted, platform_manifest(data))
        data['manifests'].append(copy.deepcopy(wanted))
        with self.assertRaises(ValueError): platform_manifest(data)

    def test_missing_platform_rejected(self):
        with self.assertRaises(ValueError): platform_manifest({'schemaVersion':2,'manifests':[]})

    def test_malformed_manifest_rejected(self):
        with self.assertRaises(ValueError): platform_manifest({'schemaVersion':1})
        with self.assertRaises(ValueError): platform_manifest({'schemaVersion':2,'layers':[]})

    def test_redirect_credentials_and_lookalikes_rejected(self):
        for url in ['http://x.blob.core.windows.net/a','https://u:p@x.blob.core.windows.net/a',
                    'https://x.blob.core.windows.net.evil.example/a','https://x.blob.core.windows.net:444/a']:
            self.assertFalse(redirect_allowed(url, False))
        self.assertTrue(redirect_allowed('https://x.blob.core.windows.net/a', False))
        self.assertFalse(redirect_allowed('https://x.blob.core.windows.net/a', True))
        self.assertTrue(redirect_allowed('https://production.cloudflare.docker.com/a', True))
        self.assertFalse(redirect_allowed('https://production.cloudflare.docker.com.evil.example/a', True))


if __name__ == '__main__': unittest.main()
