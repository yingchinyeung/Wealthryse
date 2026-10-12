import copy
import unittest
from deploy_upgrade_broker_security_20261010 import accept, patch, CHECKS, traffic_verified


class BrokerDeployTests(unittest.TestCase):
    def setUp(self):
        self.app = {'identity': {'type': 'UserAssigned'}, 'properties': {
            'environmentId': 'existing-private-environment',
            'configuration': {'activeRevisionsMode': 'Single', 'secrets': [{'name': 'auth'}],
                              'ingress': {'external': False, 'traffic': [{'weight': 100, 'revisionName': 'old'}]}},
            'template': {'revisionSuffix': 'old', 'scale': {'minReplicas': 1}, 'containers': [{
                'name': 'broker', 'image': 'old', 'command': ['python'],
                'env': [{'name': 'BROKER_AUTH', 'secretRef': 'auth', 'value': ''}],
                'probes': [{'type': 'Readiness', 'httpGet': {'path': '/health', 'port': 8000}}]}]}}}
        self.proof = {'passed': True, 'image': 'candidate', 'saved_settings_preserved': True, 'proof': {
            'passed': True, 'checks': sorted(CHECKS), 'actual_server_process': True,
            'synthetic_credentials_only': True, 'server_nonloopback_connections_denied': True,
            'secret_writes': 0, 'broker_calls': 0, 'database_connections': 0}}
    def test_image_only_patch_does_not_mutate_input(self):
        before = copy.deepcopy(self.app)
        result = patch(self.app, 'candidate', 'new')
        self.assertEqual(self.app, before)
        self.assertEqual(set(result['properties']), {'template'})
        container = result['properties']['template']['containers'][0]
        self.assertEqual(container['image'], 'candidate')
        self.assertEqual(container['env'], [{'name': 'BROKER_AUTH', 'secretRef': 'auth'}])
        self.assertEqual(container['probes'], self.app['properties']['template']['containers'][0]['probes'])
    def test_external_ingress_rejected(self):
        self.app['properties']['configuration']['ingress']['external'] = True
        with self.assertRaises(ValueError): patch(self.app, 'candidate', 'new')
    def test_multiple_revision_mode_rejected(self):
        self.app['properties']['configuration']['activeRevisionsMode'] = 'Multiple'
        with self.assertRaises(ValueError): patch(self.app, 'candidate', 'new')
    def test_complete_http_evidence_passes(self):
        accept(self.proof, 'candidate')
    def test_missing_or_duplicate_checks_rejected(self):
        for checks in (sorted(CHECKS)[:-1], sorted(CHECKS) + [sorted(CHECKS)[0]]):
            proof = copy.deepcopy(self.proof); proof['proof']['checks'] = checks
            with self.assertRaises(ValueError): accept(proof, 'candidate')
    def test_wrong_image_or_side_effects_rejected(self):
        with self.assertRaises(ValueError): accept(self.proof, 'different')
        self.proof['proof']['secret_writes'] = 1
        with self.assertRaises(ValueError): accept(self.proof, 'candidate')
    def test_named_traffic_requires_actual_weight(self):
        props = {'configuration': {'ingress': {'traffic': [{'weight': 100, 'revisionName': 'accepted'}]}}}
        self.assertTrue(traffic_verified(props, {'trafficWeight': 100}, 'accepted'))
        self.assertFalse(traffic_verified(props, {'trafficWeight': 0}, 'accepted'))
    def test_latest_traffic_requires_both_exact_revision_pointers(self):
        props = {'latestRevisionName': 'accepted', 'latestReadyRevisionName': 'accepted',
                 'configuration': {'ingress': {'traffic': [{'weight': 100, 'latestRevision': True}]}}}
        self.assertTrue(traffic_verified(props, {'trafficWeight': 100}, 'accepted'))
        for key in ('latestRevisionName', 'latestReadyRevisionName'):
            other = copy.deepcopy(props); other[key] = 'different'
            self.assertFalse(traffic_verified(other, {'trafficWeight': 100}, 'accepted'))


if __name__ == '__main__': unittest.main()
