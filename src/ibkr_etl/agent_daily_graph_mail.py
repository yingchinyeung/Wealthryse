"""Fixed-mailbox Microsoft Graph adapter; disabled until live mail acceptance.

Only trusted reporting code calls this adapter. It performs a single submission,
never follows redirects or retries a send, and does not claim recipient delivery.
Credentials, response bodies and report contents are never logged here.
"""
import json
import re
import uuid
from datetime import date
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .agent_daily_schedule import REPORT_EMAIL, delivery_key as expected_delivery_key
from .specialist_report_runtime import TENANT

SENDER = 'anthony.ying@wealthryse.ai'
GRAPH_SCOPE = 'https://graph.microsoft.com/.default'
SEND_URL = 'https://graph.microsoft.com/v1.0/users/anthony.ying%40wealthryse.ai/sendMail'
MAX_MESSAGE_BYTES = 65536
MAIL_IDENTITY_CLIENT_ID = 'cc80dc26-9f62-4231-bc63-dbe8276a0eca'


def create_graph_sender(*, enabled=False):
    """Trusted deployment factory, never CLI/user/default credential fallback.

    This is not wired to a live worker. Enablement follows scoped Exchange and
    independent end-to-end delivery acceptance, not a browser or model request.
    """
    if type(enabled) is not bool:
        raise ValueError('Explicit mail enablement required')
    if not enabled:
        return GraphDailySender(None)
    from azure.identity import ManagedIdentityCredential
    return GraphDailySender(ManagedIdentityCredential(client_id=MAIL_IDENTITY_CLIENT_ID), enabled=True)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib turns this into HTTPError without resubmitting credentials/body.
        return None


class GraphDailySender:
    def __init__(self, credential, *, enabled=False):
        if type(enabled) is not bool:
            raise ValueError('Explicit mail enablement required')
        self._credential = credential
        self._enabled = enabled

    def assert_configured(self):
        """Local enablement check only, not proof of Exchange authorization."""
        if not self._enabled or self._credential is None:
            raise ValueError('Daily mail transport is not activated')

    def submit(self, message, *, delivery_key, report_sha256):
        self.assert_configured()
        if (not isinstance(message, dict) or set(message) != {'recipient', 'subject', 'text', 'html'}
                or message['recipient'] != REPORT_EMAIL or REPORT_EMAIL != SENDER
                or not isinstance(report_sha256, str) or not re.fullmatch('[0-9a-f]{64}', report_sha256)):
            raise ValueError('Registered daily message and recipient required')
        subject = message['subject']
        match = re.fullmatch(r'Agent operations daily report — (\d{4}-\d{2}-\d{2}) \(Hong Kong\)', subject) if isinstance(subject, str) else None
        if not match:
            raise ValueError('Registered daily-report subject required')
        day = date.fromisoformat(match[1])
        if delivery_key != expected_delivery_key(TENANT, day, 'email'):
            raise ValueError('Exact daily email delivery key required')
        text = message['text']
        if (not isinstance(text, str) or not text.startswith(subject + '\n\nReporting period: ')
                or len(text.encode('utf-8')) > MAX_MESSAGE_BYTES):
            raise ValueError('Bounded deterministic daily-report text required')
        correlation = str(uuid.uuid5(uuid.UUID(TENANT), delivery_key + ':' + report_sha256))
        # Correlation headers are evidence, NOT a Graph deduplication promise.
        payload = json.dumps({'message': {
            'subject': subject, 'body': {'contentType': 'Text', 'content': text},
            'toRecipients': [{'emailAddress': {'address': REPORT_EMAIL}}],
            'internetMessageHeaders': [
                {'name': 'X-WealthRyse-Delivery-Key', 'value': delivery_key},
                {'name': 'X-WealthRyse-Report-SHA256', 'value': report_sha256}],
        }, 'saveToSentItems': True}, ensure_ascii=False).encode('utf-8')
        try:
            token = self._credential.get_token(GRAPH_SCOPE).token
            if not isinstance(token, str) or not token or '\r' in token or '\n' in token:
                raise ValueError('Invalid credential')
            request = Request(SEND_URL, data=payload, method='POST', headers={
                'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                'client-request-id': correlation, 'return-client-request-id': 'true'})
            # No environment proxy, redirect, HTTP retry or alternative endpoint.
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
                if response.status != 202:
                    raise ValueError('Provider did not accept submission')
                reference = response.headers.get('request-id')
                if not isinstance(reference, str) or str(uuid.UUID(reference)) != reference:
                    raise ValueError('Provider correlation evidence missing')
        except HTTPError as error:
            error.close()
            raise ValueError('Microsoft Graph submission acceptance unverified') from None
        except Exception:
            # Do not expose token, request, mailbox response body or provider URL.
            raise ValueError('Microsoft Graph submission acceptance unverified') from None
        return {'state': 'accepted', 'provider_reference': 'graph:' + reference,
                'recipient_delivery_confirmed': False}
