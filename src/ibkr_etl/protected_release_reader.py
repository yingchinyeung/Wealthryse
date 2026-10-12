"""Read exact protected evidence versions; never approve or deploy a release.

Clients, destination policy, ARM reader and receipt bindings must be created by
the trusted release controller, not taken from a website-uploaded declaration.
Storage protection/content integrity is not authentication of a scan producer.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from typing import Callable

from azure.core import MatchConditions

MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class EvidenceVersion:
    blob: str
    version_id: str
    sha256: str
    size: int

    def validate(self):
        if (not isinstance(self.blob, str)
                or not re.fullmatch(r'acceptance/[A-Za-z0-9_.-]+\.json', self.blob)
                or not isinstance(self.sha256, str)
                or not re.fullmatch(r'[0-9a-f]{64}', self.sha256)
                or not self.blob.endswith('-' + self.sha256 + '.json')
                or type(self.size) is not int or not 0 < self.size <= MAX_BYTES
                or not isinstance(self.version_id, str)
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', self.version_id)):
            raise ValueError('Exact bounded content-addressed evidence version required')
        try:
            datetime.fromisoformat(self.version_id.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('Invalid evidence version date') from None


class ProtectedReleaseReader:
    def __init__(self, container, arm_reader: Callable, *, account: str,
                 container_name: str, resource_id: str):
        # This validates the server-owned configuration, not caller authority.
        if (not isinstance(account, str) or not re.fullmatch(r'[a-z0-9]{3,24}', account)
                or not isinstance(container_name, str)
                or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', container_name)
                or not isinstance(resource_id, str)
                or not re.fullmatch(r'/subscriptions/[0-9a-f-]{36}/resourceGroups/[A-Za-z0-9_.()-]+'
                    r'/providers/Microsoft.Storage/storageAccounts/' + re.escape(account)
                    + '/blobServices/default/containers/' + re.escape(container_name), resource_id)
                or container.account_name != account or container.container_name != container_name
                or not callable(arm_reader)):
            raise ValueError('Exact server-owned protected evidence destination required')
        self._container, self._arm_reader, self._resource_id = container, arm_reader, resource_id

    def _protection(self):
        record = self._arm_reader()
        if not isinstance(record, dict) or str(record.get('id', '')).casefold() != self._resource_id.casefold():
            raise ValueError('Evidence protection response scope differs')
        properties = record.get('properties')
        if (not isinstance(properties, dict) or properties.get('publicAccess') != 'None'
                or properties.get('hasLegalHold') is not True or properties.get('deleted')
                or properties.get('legalHold', {}).get('protectedAppendWritesHistory', {}).get('allowProtectedAppendWritesAll')):
            raise ValueError('Private immutable evidence protection not confirmed')

    def read(self, receipt: EvidenceVersion) -> bytes:
        if not isinstance(receipt, EvidenceVersion):
            raise ValueError('Exact evidence receipt required')
        receipt.validate()
        self._protection()
        blob = self._container.get_blob_client(receipt.blob, version_id=receipt.version_id)
        properties = blob.get_blob_properties(timeout=30)
        if properties.size != receipt.size or properties.version_id != receipt.version_id or not properties.etag:
            raise ValueError('Evidence properties differ from frozen receipt')
        content = blob.download_blob(offset=0, length=receipt.size + 1,
            etag=properties.etag, match_condition=MatchConditions.IfNotModified, timeout=60).readall()
        if (not isinstance(content, bytes) or len(content) != receipt.size
                or hashlib.sha256(content).hexdigest() != receipt.sha256):
            raise ValueError('Protected release evidence bytes differ')
        self._protection()
        return content
