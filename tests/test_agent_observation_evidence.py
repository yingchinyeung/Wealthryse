import hashlib
import unittest
from unittest.mock import patch
from test_agent_observation_audit import detail
from ibkr_etl.agent_observation_evidence import ObservationEvidenceBinding,validate_envelope
from ibkr_etl.specialist_evidence import validate_registered_detail,TENANT
from ibkr_etl.util import canonical_json


class ObservationEvidenceTests(unittest.TestCase):
    def fixture(self,phase='started',change=None):
        observation=detail(phase)
        if phase in ('failed','denied'): observation.update(code='tool_unavailable')
        value=ObservationEvidenceBinding('a'*64).envelope(observation)
        value.update(change or {})
        payload=canonical_json(value).encode()
        event=dict(operation_id=observation['operation_id'],phase=phase,
            event_id=observation['operation_id']+'-'+phase,role='data_analyst',action='inspect_and_analyze',
            evidence_sha256=hashlib.sha256(payload).hexdigest(),cost_microusd=0)
        return event,payload

    def test_enrolled_build_and_every_safe_phase(self):
        with patch('ibkr_etl.specialist_evidence.OBSERVATION_MANIFESTS',frozenset({'a'*64})):
            for phase in ('started','succeeded','failed','denied'):
                event,payload=self.fixture(phase)
                receipt=validate_registered_detail(TENANT,event,payload)
                self.assertTrue(receipt['detail_verified']);self.assertFalse(receipt['execution_authorized'])

    def test_unknown_build_cannot_self_enroll(self):
        event,payload=self.fixture(change={'source_manifest_sha256':'b'*64})
        with self.assertRaises(ValueError): validate_registered_detail(TENANT,event,payload)

    def test_changed_principal_resource_raw_payload_or_agent_rejected(self):
        binding=ObservationEvidenceBinding('a'*64);value=binding.envelope(detail())
        for key,changed in [('producer_principal','other'),('producer_resource','other'),
            ('autonomous_specialist',True),('financial_or_source_data_included',True)]:
            with self.assertRaises(ValueError): validate_envelope({**value,'producer':{**value['producer'],key:changed}})
        for changed in ({'agent_sha256':'b'*64},{'role':'financial_analyst'},
            {'tool_name':'get_holdings'},{'prompt':'private'}):
            with self.assertRaises(ValueError): binding.envelope({**detail(),**changed})

    def test_event_identity_and_phase_binding_and_exact_cost(self):
        event,payload=self.fixture()
        with patch('ibkr_etl.specialist_evidence.OBSERVATION_MANIFESTS',frozenset({'a'*64})):
            for changed in ({'phase':'succeeded'},{'event_id':'other'},{'role':'financial_analyst'},
                {'cost_microusd':False},{'cost_microusd':1}):
                with self.assertRaises(ValueError): validate_registered_detail(TENANT,{**event,**changed},payload)
