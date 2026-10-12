from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ibkr_etl.credential_broker import store_flex_secret


class CredentialBrokerTests(unittest.TestCase):
    def test_broker_uses_managed_identity_put_and_returns_no_token(self) -> None:
        token = "secret-token-not-for-output"
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps(
            {
                "id": "https://vault.vault.azure.net/secrets/"
                "ibkr-flex-source-0123456789abcdef0123456789abcdef/version"
            }
        ).encode("utf-8")
        response.status = 200
        with patch(
            "ibkr_etl.credential_broker.ManagedIdentityCredential"
        ) as credential, patch(
            "ibkr_etl.credential_broker.urllib.request.urlopen", return_value=response
        ) as urlopen:
            credential.return_value.get_token.return_value.token = "entra-access-token"
            result = store_flex_secret(
                vault_uri="https://vault.vault.azure.net",
                client_id="client-id",
                secret_name="ibkr-flex-source-0123456789abcdef0123456789abcdef",
                token=token,
            )
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "PUT")
        self.assertIn("api-version=7.4", request.full_url)
        self.assertIn(token, request.data.decode("utf-8"))
        self.assertNotIn(token, json.dumps(result))
        self.assertEqual(len(result["token_fingerprint"]), 16)

    def test_broker_rejects_secret_names_outside_dedicated_prefix(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside"):
            store_flex_secret(
                vault_uri="https://vault.vault.azure.net",
                client_id="client-id",
                secret_name="database-admin-url",
                token="token",
            )

    def test_bicep_role_is_set_only_and_ingress_is_internal(self) -> None:
        source = Path("infra/staging-credential-broker.bicep").read_text(encoding="utf-8")
        self.assertIn("Microsoft.KeyVault/vaults/secrets/setSecret/action", source)
        self.assertNotIn("Microsoft.KeyVault/vaults/secrets/read", source)
        self.assertIn("external: false", source)

    def test_discovery_worker_has_single_attempt_and_least_privilege(self) -> None:
        source = Path("infra/staging-flex-discovery.bicep").read_text(encoding="utf-8")
        self.assertIn("replicaRetryLimit: 0", source)
        self.assertIn("parallelism: 1", source)
        self.assertIn("scripts/discover_flex_sources.py", source)
        self.assertIn("scope: databaseSecret", source)
        self.assertIn("scope: tokenVault", source)
        self.assertIn("AZURE_STORAGE_ACCOUNT_URL", source)
        self.assertIn("AZURE_RAW_CONTAINER", source)
        self.assertIn("flex-discovery-raw-writer", source)
        self.assertNotIn("database-admin-url", source)


if __name__ == "__main__":
    unittest.main()
