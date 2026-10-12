"""Fixed observation producer envelope, independent of the model and request.

Build enrollment lives in the reviewed reporting registry. These scope checks
are evidence validation, never permissions to execute an observation.
"""
import hashlib
import re
from dataclasses import dataclass
from .util import canonical_json

SCHEMA='specialist-observation-envelope-v1'
PRODUCER_RESOURCE=('/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11'
    '/providers/Microsoft.App/containerApps/ibkrv2stg-agent-gateway')
PRODUCER_PRINCIPAL='60313d53-6387-46d4-9bb8-dc5f8ab925a5'
AGENT_PRINCIPAL='bbe76059-bdd6-4c52-a644-877460a24f7f'
PRODUCER=dict(producer_kind='delegated_interactive_observation_gateway',producer_resource=PRODUCER_RESOURCE,
    producer_principal=PRODUCER_PRINCIPAL,database_login='ibkr_specialist_audit_login',
    autonomous_specialist=False,financial_or_source_data_included=False,
    cost_measurement='no model calls; infrastructure cost not measured')


@dataclass(frozen=True)
class ObservationEvidenceBinding:
    source_manifest_sha256: str

    def __post_init__(self):
        if not isinstance(self.source_manifest_sha256,str) or not re.fullmatch('[0-9a-f]{64}',self.source_manifest_sha256):
            raise ValueError('Reviewed observation build fingerprint required')

    def envelope(self,detail):
        from .agent_observation_audit import validate_detail
        validate_detail(detail)
        if (detail['role']!='data_analyst' or detail['tool_name']!='get_performance_series'
            or detail['agent_sha256']!=hashlib.sha256(AGENT_PRINCIPAL.encode()).hexdigest()):
            raise ValueError('Observation producer exceeds installed capability')
        return dict(schema=SCHEMA,source_manifest_sha256=self.source_manifest_sha256,
            producer=dict(PRODUCER),observation=detail)


def validate_envelope(value):
    if (not isinstance(value,dict) or set(value)!={'schema','source_manifest_sha256','producer','observation'}
        or value.get('schema')!=SCHEMA or canonical_json(value.get('producer'))!=canonical_json(PRODUCER)):
        raise ValueError('Exact registered observation producer required')
    expected=ObservationEvidenceBinding(value['source_manifest_sha256']).envelope(value['observation'])
    if canonical_json(value)!=canonical_json(expected): raise ValueError('Observation envelope differs')
    return expected
