"""Private real-bundle trial transport guards, without live Azure access."""
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from ibkr_etl.fx_evidence_bundle import build_bundle
from ibkr_etl.util import canonical_json

SPEC = importlib.util.spec_from_file_location("fx_trial_verifier", Path(__file__).resolve().parents[1] / "scripts/verify_fx_evidence_schema.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)
TENANT = "00000000-0000-0000-0000-000000000001"


class FxTrialInputsTests(unittest.TestCase):
    def setUp(self):
        self.manifest = dict(tenant_id=TENANT, container="input-snapshots", execution_authorized=False,
                             source_snapshot_sha256="c" * 64, records=[])
        self.blobs = {}
        for account in ("U16088571", "U16088606"):
            xml = (f'<FlexStatement accountId="{account}" toDate="20250120"><ConversionRates>'
                   '<ConversionRate fromCurrency="HKD" toCurrency="USD" rate="0.128"/>'
                   '</ConversionRates></FlexStatement>').encode()
            run = dict(tenant_id=TENANT, run_id=10, status="success", completed_at="2025-01-21T00:00:00Z",
                       raw_sha256=hashlib.sha256(xml).hexdigest())
            bundle = build_bundle(tenant_id=TENANT, account_id=account, source_namespace="trial",
                                  source_snapshot_sha256="c" * 64, archives=[(run, xml)], legacy_rows=[])
            content = canonical_json(bundle).encode()
            digest = hashlib.sha256(content).hexdigest()
            name = f"{TENANT}/fx-evidence-trials/{account}/{digest}.json"
            self.manifest["records"].append(dict(account_id=account, artifact_sha256=digest,
                bundle_sha256=bundle["bundle_sha256"], blob_name=name, etag='"pinned"', byte_count=len(content)))
            blob = MagicMock()
            blob.get_blob_properties.return_value = SimpleNamespace(etag='"pinned"', size=len(content))
            blob.download_blob.return_value.readall.return_value = content
            self.blobs[name] = blob
        self.container = MagicMock()
        self.container.get_container_properties.return_value = {"public_access": None}
        self.container.get_blob_client.side_effect = self.blobs.__getitem__

    def load(self, manifest=None):
        with patch.object(VERIFIER, "REAL_INPUT_MANIFEST", manifest or self.manifest, create=True), \
             patch.dict(os.environ, {"AZURE_CLIENT_ID": "reviewed-worker"}), \
             patch("azure.identity.ManagedIdentityCredential") as identity, \
             patch("azure.storage.blob.BlobServiceClient") as service:
            service.return_value.get_container_client.return_value = self.container
            result = VERIFIER.load_real_inputs(TENANT)
            identity.assert_called_once_with(client_id="reviewed-worker")
            return result

    def test_valid_private_inputs_use_pinned_etags_and_exact_accounts(self):
        result = self.load()
        self.assertEqual([b["account_id"] for _, b in result], ["U16088571", "U16088606"])
        for blob in self.blobs.values():
            self.assertEqual(blob.download_blob.call_args.kwargs["etag"], '"pinned"')
            blob.upload_blob.assert_not_called()

    def test_unapproved_scope_is_rejected(self):
        for change in ({"tenant_id": "other"}, {"container": "public"}, {"execution_authorized": True}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "input scope"):
                self.load({**self.manifest, **change})
        self.container.get_blob_client.assert_not_called()

    def test_public_container_is_rejected_before_download(self):
        self.container.get_container_properties.return_value = {"public_access": "blob"}
        with self.assertRaisesRegex(ValueError, "Private input container"): self.load()
        self.container.get_blob_client.assert_not_called()

    def test_changed_etag_or_size_is_rejected(self):
        first = next(iter(self.blobs.values()))
        for props in (SimpleNamespace(etag='"changed"', size=self.manifest["records"][0]["byte_count"]),
                      SimpleNamespace(etag='"pinned"', size=1)):
            first.get_blob_properties.return_value = props
            with self.assertRaisesRegex(ValueError, "artifact changed"): self.load()
        first.download_blob.assert_not_called()

    def test_wrong_blob_path_is_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["records"][0]["blob_name"] = "another-account/file.json"
        with self.assertRaisesRegex(ValueError, "Unexpected private trial artifact"): self.load(manifest)
        self.container.get_blob_client.assert_not_called()

    def test_changed_contents_are_rejected_even_with_same_metadata(self):
        first = next(iter(self.blobs.values()))
        first.download_blob.return_value.readall.return_value = b"changed"
        with self.assertRaisesRegex(ValueError, "artifact digest mismatch"): self.load()


if __name__ == "__main__":
    unittest.main()
