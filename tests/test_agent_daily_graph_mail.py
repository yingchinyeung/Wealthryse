import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from ibkr_etl.agent_daily_graph_mail import GraphDailySender, NoRedirect, SEND_URL, GRAPH_SCOPE, create_graph_sender, MAIL_IDENTITY_CLIENT_ID
from ibkr_etl.agent_daily_schedule import delivery_key
from ibkr_etl.specialist_report_runtime import TENANT


class GraphDailyMailTests(unittest.TestCase):
    def setUp(self):
        self.credential = Mock()
        self.credential.get_token.return_value = SimpleNamespace(token='synthetic-token')
        self.sender = GraphDailySender(self.credential, enabled=True)
        subject = 'Agent operations daily report — 2026-10-02 (Hong Kong)'
        self.message = dict(recipient='anthony.ying@wealthryse.ai', subject=subject,
                            text=subject + '\n\nReporting period: synthetic test', html='<pre>synthetic</pre>')
        self.fields = dict(delivery_key=delivery_key(TENANT, date(2026, 10, 2), 'email'), report_sha256='a'*64)

    def response(self, *, status=202, reference='11111111-1111-4111-8111-111111111111'):
        response = Mock(status=status, headers={'request-id': reference})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_disabled_by_default_no_token_or_network(self):
        with patch('ibkr_etl.agent_daily_graph_mail.build_opener') as build:
            with self.assertRaises(ValueError):
                GraphDailySender(self.credential).submit(self.message, **self.fields)
            self.credential.get_token.assert_not_called()
            build.assert_not_called()

    def test_enablement_must_be_boolean(self):
        for value in ['true', 1, None]:
            with self.assertRaises(ValueError): GraphDailySender(self.credential, enabled=value)

    def test_factory_uses_only_dedicated_managed_identity_no_fallback(self):
        with patch('azure.identity.ManagedIdentityCredential') as credential:
            disabled = create_graph_sender()
            credential.assert_not_called()
            with self.assertRaises(ValueError): disabled.submit(self.message, **self.fields)
            enabled = create_graph_sender(enabled=True)
            self.assertTrue(enabled._enabled)
            credential.assert_called_once_with(client_id=MAIL_IDENTITY_CLIENT_ID)

    def test_accepted_not_delivered_exact_sender_recipient_and_no_retries(self):
        with patch('ibkr_etl.agent_daily_graph_mail.build_opener') as build:
            response = self.response()
            build.return_value.open.return_value = response
            result = self.sender.submit(self.message, **self.fields)
            self.assertEqual(result['state'], 'accepted')
            self.assertFalse(result['recipient_delivery_confirmed'])
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, SEND_URL)
            self.assertEqual(request.get_method(), 'POST')
            payload = json.loads(request.data)
            self.assertTrue(payload['saveToSentItems'])
            self.assertEqual(payload['message']['toRecipients'], [{'emailAddress': {'address': self.message['recipient']}}])
            self.assertEqual(payload['message']['body']['contentType'], 'Text')
            self.assertNotIn('ccRecipients', payload['message'])
            self.assertNotIn('attachments', payload['message'])
            self.credential.get_token.assert_called_once_with(GRAPH_SCOPE)
            build.return_value.open.assert_called_once()
            response.read.assert_not_called()

    def test_rejects_arbitrary_recipients_and_extra_fields_before_token(self):
        for altered in [dict(self.message, recipient='other@example.com'), dict(self.message, cc='other@example.com'),
                        dict(self.message, subject='Arbitrary report'), dict(self.message, text='raw data')]:
            with self.assertRaises(ValueError): self.sender.submit(altered, **self.fields)
        self.credential.get_token.assert_not_called()

    def test_date_tenant_channel_and_hash_must_match(self):
        for changed in [dict(self.fields, delivery_key='other'), dict(self.fields, report_sha256='A'*64),
                        dict(self.fields, delivery_key=delivery_key(TENANT, date(2026, 10, 1), 'email')),
                        dict(self.fields, delivery_key=delivery_key(TENANT, date(2026, 10, 2), 'website'))]:
            with self.assertRaises(ValueError): self.sender.submit(self.message, **changed)
        self.credential.get_token.assert_not_called()

    def test_oversized_body_rejected_before_token(self):
        with self.assertRaises(ValueError):
            self.sender.submit(dict(self.message, text=self.message['text'] + 'x'*65536), **self.fields)
        self.credential.get_token.assert_not_called()

    def test_wrong_status_or_missing_request_id_cannot_claim_acceptance(self):
        for status, reference in [(200, '11111111-1111-4111-8111-111111111111'), (202, None), (202, 'secret-body')]:
            with patch('ibkr_etl.agent_daily_graph_mail.build_opener') as build:
                build.return_value.open.return_value = self.response(status=status, reference=reference)
                with self.assertRaisesRegex(ValueError, '^Microsoft Graph submission acceptance unverified$'):
                    self.sender.submit(self.message, **self.fields)
                build.return_value.open.assert_called_once()

    def test_errors_are_sanitized_and_send_is_never_retried(self):
        for error in [TimeoutError('synthetic secret'), HTTPError(SEND_URL, 429, 'synthetic secret', {}, None)]:
            with patch('ibkr_etl.agent_daily_graph_mail.build_opener') as build:
                build.return_value.open.side_effect = error
                with self.assertRaisesRegex(ValueError, '^Microsoft Graph submission acceptance unverified$'):
                    self.sender.submit(self.message, **self.fields)
                build.return_value.open.assert_called_once()

    def test_redirects_cannot_forward_bearer_or_body(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 307, 'redirect', {}, 'https://other.example'))

    def test_invalid_token_never_reaches_network(self):
        for token in ['', None, 'header\r\ninjection']:
            self.credential.get_token.return_value = SimpleNamespace(token=token)
            with patch('ibkr_etl.agent_daily_graph_mail.build_opener') as build:
                with self.assertRaises(ValueError): self.sender.submit(self.message, **self.fields)
                build.assert_not_called()
