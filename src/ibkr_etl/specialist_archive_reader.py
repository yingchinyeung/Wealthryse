"""Fixed-destination, read-only archive adapter for the reporting identity.

No uploads, repair, credential fallback, arbitrary URLs or caller-selected
canaries. Even an empty ledger requires a live read of a retained receipt.
"""
import hashlib
import uuid

import requests
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import ContainerClient
from azure.core import MatchConditions

from .util import canonical_json

ACCOUNT = 'ibkrv2stgauhecwc4itaoo'
CLIENT_ID = '388f6dac-d405-4b2c-ba40-779d3761aeca'
CONTAINER = 'specialist-audit-checkpoints'
SCOPE = ('/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11'
         '/providers/Microsoft.Storage/storageAccounts/'+ACCOUNT+'/blobServices/default/containers/'+CONTAINER)
CANARY_SHA256 = '7e5260210147262d6e5971275ec447f0da0c80f50418b5eeb3927c57cbb9ae96'
CANARY = 'acceptance/ibkrv2stg-agent-report-h7gz63v-'+CANARY_SHA256+'.json'
CANARY_VERSION = '2026-10-02T16:45:49.5072187Z'
MAX_BYTES = 65536


class ReportingArchiveReader:
    def __init__(self, container, arm_reader):
        if container.container_name != CONTAINER or container.account_name != ACCOUNT:
            raise ValueError('Unregistered reporting archive destination')
        self._container = container
        self._arm_reader = arm_reader

    def _protection(self):
        response = self._arm_reader()
        if response.get('id','').casefold() != SCOPE.casefold():
            raise ValueError('Archive protection response has wrong scope')
        p = response['properties']
        if (p.get('publicAccess') != 'None' or p.get('hasLegalHold') is not True or p.get('deleted')
                or p.get('legalHold',{}).get('protectedAppendWritesHistory',{}).get('allowProtectedAppendWritesAll')):
            raise ValueError('Private protected archive is not confirmed')

    def _read(self, name, *, version=None, expected_size=None):
        blob = self._container.get_blob_client(name, version_id=version)
        props = blob.get_blob_properties()
        if not 0 < props.size <= MAX_BYTES or (expected_size is not None and props.size != expected_size):
            raise ValueError('Protected evidence size mismatch')
        if version is not None and props.version_id != version:
            raise ValueError('Protected evidence version mismatch')
        content = blob.download_blob(offset=0,length=props.size+1,etag=props.etag,
                                     match_condition=MatchConditions.IfNotModified).readall()
        if len(content) != props.size:
            raise ValueError('Protected evidence changed during read')
        return content

    def check_ready(self):
        self._protection()
        data = self._read(CANARY,version=CANARY_VERSION)
        if hashlib.sha256(data).hexdigest() != CANARY_SHA256:
            raise ValueError('Archive readiness receipt mismatch')
        self._protection()
        return {'protected_archive_verified':True,'canary_sha256':CANARY_SHA256,
                'container':CONTAINER,'data_plane_read_verified':True}

    def verify(self, tenant_id, event):
        if str(uuid.UUID(tenant_id)) != tenant_id or type(event['sequence']) is not int or event['sequence'] < 1:
            raise ValueError('Exact tenant and positive sequence required')
        self._protection()
        payload = canonical_json({'tenant_id':tenant_id,'event':event}).encode()
        name = f"{tenant_id}/events/{event['sequence']:020d}.json"
        if self._read(name,expected_size=len(payload)) != payload:
            raise ValueError('Protected audit event mismatch')
        self._protection()
        return {'protected_archive_verified':True,'sha256':hashlib.sha256(payload).hexdigest()}


def managed_reporting_archive():
    # Select the registered user-assigned identity explicitly. This credential
    # never falls back to developer, CLI, environment-secret or owner identities.
    credential = ManagedIdentityCredential(client_id=CLIENT_ID)
    container = ContainerClient('https://'+ACCOUNT+'.blob.core.windows.net',CONTAINER,credential=credential,
                                connection_timeout=10,read_timeout=15,retry_total=0)
    def arm_reader():
        token = credential.get_token('https://management.azure.com/.default').token
        response = requests.get('https://management.azure.com'+SCOPE+'?api-version=2023-05-01',
                                headers={'Authorization':'Bearer '+token},timeout=15,allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('Protected archive inspection unavailable')
        return response.json()
    return ReportingArchiveReader(container,arm_reader)
