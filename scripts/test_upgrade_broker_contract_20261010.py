import json
import os
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from ibkr_etl import credential_broker as broker

ENV = {'KEY_VAULT_URI': 'https://qualification.vault.azure.net', 'BROKER_AUTH': 'synthetic-only-qualification-auth-0000', 'AZURE_CLIENT_ID': 'synthetic-client'}
NAME = 'ibkr-flex-source-' + 'a' * 32
PAYLOAD = {'secret_name': NAME, 'token': 'synthetic-only-token'}


class BrokerContractTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=False); self.env.start()
        self.client = TestClient(broker.app)
    def tearDown(self):
        self.client.close(); self.env.stop()
    def test_health_ready(self):
        self.assertEqual(self.client.get('/health').json(), {'status': 'ready', 'capability': 'set-only-flex-secrets'})
    def test_health_missing_auth(self):
        with patch.dict(os.environ, {'BROKER_AUTH': ''}):
            with self.assertRaises(RuntimeError): broker.health()
    def test_wrong_auth_never_writes(self):
        with patch.object(broker, 'store_flex_secret') as store:
            response = self.client.post('/v1/credentials', json=PAYLOAD, headers={'Authorization': 'Bearer wrong'})
        self.assertEqual(response.status_code, 403); store.assert_not_called()
        self.assertNotIn(PAYLOAD['token'], response.text)
    def test_missing_auth_never_writes(self):
        with patch.object(broker, 'store_flex_secret') as store:
            response = self.client.post('/v1/credentials', json=PAYLOAD)
        self.assertEqual(response.status_code, 403); store.assert_not_called()
    def test_valid_synthetic_authorization_uses_exact_arguments(self):
        expected = {'secret_name': NAME, 'status': 'stored', 'token_fingerprint': 'synthetic'}
        with patch.object(broker, 'store_flex_secret', return_value=expected) as store:
            response = self.client.post('/v1/credentials', json=PAYLOAD, headers={'Authorization': 'Bearer ' + ENV['BROKER_AUTH']})
        self.assertEqual(response.status_code, 200); self.assertEqual(response.json(), expected)
        store.assert_called_once_with(vault_uri=ENV['KEY_VAULT_URI'], client_id=ENV['AZURE_CLIENT_ID'], **PAYLOAD)
    def test_storage_failure_is_redacted(self):
        with patch.object(broker, 'store_flex_secret', side_effect=RuntimeError('private exception marker')):
            response = self.client.post('/v1/credentials', json=PAYLOAD, headers={'Authorization': 'Bearer ' + ENV['BROKER_AUTH']})
        self.assertEqual(response.status_code, 503); self.assertNotIn('private exception marker', response.text)
    def test_prefix_guard(self):
        with patch.object(broker, 'ManagedIdentityCredential') as credential:
            with self.assertRaises(ValueError):
                broker.store_flex_secret(vault_uri=ENV['KEY_VAULT_URI'], client_id='synthetic', secret_name='database-admin-url', token='x')
        credential.assert_not_called()
    def test_empty_and_oversized_tokens(self):
        for token in (' ', 'x' * 1025):
            with patch.object(broker, 'ManagedIdentityCredential') as credential:
                with self.assertRaises(ValueError):
                    broker.store_flex_secret(vault_uri=ENV['KEY_VAULT_URI'], client_id='synthetic', secret_name=NAME, token=token)
            credential.assert_not_called()
    def test_vault_validation(self):
        for value in ('http://qualification.vault.azure.net', 'https://example.com', 'https://qualification.vault.azure.net/path'):
            with patch.dict(os.environ, {'KEY_VAULT_URI': value}):
                with self.assertRaises(RuntimeError): broker._required_environment()
    def test_documentation_routes_disabled(self):
        for path in ('/docs', '/redoc', '/openapi.json'):
            self.assertEqual(self.client.get(path).status_code, 404)
    def test_no_get_credential_route(self):
        self.assertEqual(self.client.get('/v1/credentials').status_code, 405)
    def test_secret_model_repr(self):
        self.assertNotIn(PAYLOAD['token'], repr(broker.CredentialSubmission(**PAYLOAD)))
    def test_malformed_name_and_extra_fields_rejected(self):
        for body in ({**PAYLOAD, 'secret_name': 'database-admin-url'}, {**PAYLOAD, 'unexpected': True}):
            with patch.object(broker, 'store_flex_secret') as store:
                self.assertEqual(self.client.post('/v1/credentials', json=body).status_code, 422)
            store.assert_not_called()
    def test_store_returns_fingerprint_not_token(self):
        response = MagicMock(); response.__enter__.return_value = response
        response.read.return_value = json.dumps({'id': ENV['KEY_VAULT_URI'] + '/secrets/' + NAME + '/version'}).encode()
        with patch.object(broker, 'ManagedIdentityCredential') as credential, patch.object(broker.urllib.request, 'urlopen', return_value=response) as urlopen:
            credential.return_value.get_token.return_value.token = 'synthetic-entra-token'
            result = broker.store_flex_secret(vault_uri=ENV['KEY_VAULT_URI'], client_id='synthetic', **PAYLOAD)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, 'PUT'); self.assertNotIn(PAYLOAD['token'], json.dumps(result))
        self.assertEqual(len(result['token_fingerprint']), 16)


if __name__ == '__main__': unittest.main()
