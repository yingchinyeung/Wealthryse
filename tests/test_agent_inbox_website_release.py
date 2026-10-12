import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import release_agent_inbox_website as release


def evidence():
    candidate=dict(status='passed',image=release.IMAGE,protected_receipt=dict(protected_readback_verified=True))
    checks=[dict(identity=label,path=path,status=200 if label=='owner' else 403)
        for label in ('owner','viewer','anonymous')
        for path in ('/ai-operations','/api/ai-operations/daily-reports','/api/ai-operations/daily-reports/2026-10-02')]
    probe=dict(candidate,record=dict(tenant_isolation_verified=True,login='ibkr_stg_web',http_checks=checks))
    return candidate,probe


class InboxWebsiteReleaseTests(unittest.TestCase):
    def test_build_alone_cannot_qualify_without_denial_and_isolation(self):
        for field,value in [('login','ibkr_app'),('tenant_isolation_verified',False),('http_checks',[])]:
            candidate,probe=evidence();probe['record'][field]=value
            with patch.object(Path,'read_text',side_effect=[json.dumps(candidate),json.dumps(probe)]),patch.object(release,'az') as az:
                with self.assertRaises(ValueError):release.prerequisites()
                az.assert_not_called()

    def test_exact_registered_digest_and_protected_prerequisites(self):
        candidate,probe=evidence()
        with patch.object(Path,'read_text',side_effect=[json.dumps(candidate),json.dumps(probe)]), \
                patch.object(release,'az',return_value=dict(digest=release.IMAGE.split('@')[1])):
            self.assertEqual(set(release.prerequisites()),{'candidate_sha256','private_acceptance_sha256'})
        candidate['protected_receipt']['protected_readback_verified']=False
        with patch.object(Path,'read_text',side_effect=[json.dumps(candidate),json.dumps(probe)]):
            with self.assertRaises(ValueError):release.prerequisites()

    def test_auth_must_remain_enabled(self):
        with patch.object(release,'request',return_value=dict(properties=dict(platform=dict(enabled=False)))):
            with self.assertRaises(ValueError):release.auth_hash()

    def test_health_uses_only_exact_public_host_with_tls_defaults(self):
        resource=dict(properties=dict(configuration=dict(ingress=dict(fqdn='other.example'))))
        with patch.object(release.requests,'get') as get:
            with self.assertRaises(ValueError):release.public_probe(resource)
            get.assert_not_called()
        resource['properties']['configuration']['ingress']['fqdn']='ibkrv2stg-web.purplerock-8cff24a2.eastasia.azurecontainerapps.io'
        with patch.object(release.requests,'get',return_value=Mock(status_code=200,json=lambda:dict(status='ok'))) as get:
            self.assertEqual(release.public_probe(resource)['status_code'],200)
            self.assertNotIn('verify',get.call_args.kwargs)
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
        with patch.object(release.requests,'get',return_value=Mock(status_code=401)):
            result=release.public_probe(resource)
            self.assertTrue(result['sign_in_required'])
            self.assertFalse(result['application_health_confirmed'])
        with patch.object(release.requests,'get',return_value=Mock(status_code=502)):
            with self.assertRaises(ValueError):release.public_probe(resource)


if __name__=='__main__':unittest.main()
