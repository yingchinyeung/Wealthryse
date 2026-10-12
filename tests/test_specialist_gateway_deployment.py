import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from deploy_specialist_gateway import definition,validate,REGISTRY
from register_specialist_gateway import verify,SCOPE
import deploy_specialist_gateway as deployment
from ibkr_etl.specialist_evidence import OBSERVATION_MANIFESTS

IMAGE=REGISTRY+'/specialist-gateway@sha256:'+'a'*64


class GatewayDeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        folder=Path(temporary.name);(folder/'context').mkdir()
        # Unit construction checks use a reviewed manifest identifier, never
        # whichever private deployment artifact happens to exist locally.
        (folder/'context/gateway-build.json').write_text(json.dumps(
            dict(source_manifest_sha256=next(iter(OBSERVATION_MANIFESTS)))),encoding='utf-8')
        binding=patch.object(deployment,'FOLDER',folder);binding.start();self.addCleanup(binding.stop)

    def test_exact_definition_and_azure_normalization(self):
        desired=definition(IMAGE);actual=copy.deepcopy(desired)
        actual['properties']['configuration']['ingress']['transport']='Http'
        actual['properties']['template']['containers'][0]['resources']['ephemeralStorage']='2Gi'
        actual['properties']['template']['containers'][0]['probes'].reverse()
        validate(actual,desired)
        self.assertEqual(len(actual['identity']['userAssignedIdentities']),3)
        self.assertEqual(len(actual['properties']['configuration']['secrets']),3)

    def test_identity_credential_capacity_image_and_ingress_drift_rejected(self):
        original=definition(IMAGE)
        changes=[lambda x:x['properties']['configuration']['ingress'].update(allowInsecure=True),
            lambda x:x['properties']['configuration']['ingress'].update(transport='http2'),
            lambda x:x['properties']['configuration']['ingress'].update(corsPolicy={'allowedOrigins':['*']}),
            lambda x:x['properties']['configuration']['secrets'][0].update(keyVaultUrl='https://other/secret'),
            lambda x:x['identity'].update(type='SystemAssigned,UserAssigned'),
            lambda x:x['properties']['template']['scale'].update(maxReplicas=2),
            lambda x:x['properties']['template']['containers'][0].update(image='latest'),
            lambda x:x['properties']['template']['containers'][0]['resources'].update(cpu=2),
            lambda x:x['properties']['template']['containers'][0]['resources'].update(ephemeralStorage='4Gi'),
            lambda x:x['properties']['template']['containers'][0].update(probes=[])]
        for change in changes:
            actual=copy.deepcopy(original);change(actual)
            with self.assertRaises(ValueError):validate(actual,original)

    def test_mutable_image_rejected(self):
        for value in (REGISTRY+'/specialist-gateway:latest',REGISTRY+'/ibkr-platform@sha256:'+'a'*64,'other'):
            with self.assertRaises(ValueError):definition(value)

    def test_registration_cannot_acquire_graph_permissions_or_credentials(self):
        api=dict(id='api-object',appId='api-id',signInAudience='AzureADMyOrg',
            api=dict(requestedAccessTokenVersion=2,oauth2PermissionScopes=[SCOPE]),identifierUris=['api://api-id'])
        client=dict(id='client-object',appId='client-id',signInAudience='AzureADMyOrg',isFallbackPublicClient=True,
            requiredResourceAccess=[dict(resourceAppId='api-id',resourceAccess=[dict(id=SCOPE['id'],type='Scope')])])
        self.assertFalse(verify(api,client)['tenant_policy_changed'])
        for change in ({'passwordCredentials':[{'secretText':'synthetic'}]},
            {'requiredResourceAccess':[dict(resourceAppId='graph',resourceAccess=[])]},
            {'web':{'redirectUris':['https://other.test']}}):
            with self.assertRaises(ValueError):verify(api,{**client,**change})
