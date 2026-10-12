import contextlib,copy,io,json,os,socket,ssl,unittest
from unittest.mock import patch
import control_upgrade_private_canaries_20261011 as c
import prepare_upgrade_private_canaries_20261011 as p

class CanaryGuards(unittest.TestCase):
    def group(self):
        return {'group':17,'environment':'/environments/ibkrv2stg-env','profile':'Consumption',
                'identity':'/identities/test','registries':[{'server':'ibkrv2stgauhecwc4itaoo.azurecr.io','identity':'/identities/test'}],
                'references':[{'identity':'/identities/test','keyVaultUrl':'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/test-reference'}]}
    def test_exact_scope(self):
        for number in (0,1,2,18,'17',True):
            with self.assertRaises(ValueError):c.name(number)
    def test_no_normal_application(self):
        b=c.body(self.group());config=b['properties']['configuration'];container=b['properties']['template']['containers'][0]
        self.assertEqual(config['triggerType'],'Manual');self.assertEqual(config['replicaRetryLimit'],0)
        self.assertEqual(config['manualTriggerConfig']['parallelism'],1)
        self.assertEqual(container['command'],['python']);self.assertEqual(container['image'],c.IMAGE)
        self.assertNotIn('DATABASE_URL',str(container));self.assertNotIn('value',container['env'][0])
    def test_legacy_environment_denied(self):
        g=self.group();g['environment']='/environments/ibkrv11-env'
        with self.assertRaises(ValueError):c.body(g)
    def test_other_identity_denied(self):
        g=self.group();g['references'][0]['identity']='/other'
        with self.assertRaises(ValueError):c.body(g)
    def test_password_registry_denied(self):
        g=self.group();g['registries'][0]['passwordSecretRef']='private-password'
        with self.assertRaises(ValueError):c.body(g)
    def test_bounded_references(self):
        g=self.group();g['references']*=6
        with self.assertRaises(ValueError):c.body(g)
    def test_source_compiles(self):compile(c.probe(17,2),'canary-probe','exec')
    def test_no_body_mutation(self):
        g=self.group();original=copy.deepcopy(g);c.body(g);self.assertEqual(g,original)
    def test_changed_command_denied(self):
        wanted=c.body(self.group());actual=copy.deepcopy(wanted)
        actual['properties']['template']['containers'][0]['command']=['start-normal-app']
        with self.assertRaises(ValueError):c.same(actual,wanted)
    def test_changed_secret_denied(self):
        wanted=c.body(self.group());actual=copy.deepcopy(wanted)
        actual['properties']['configuration']['secrets'][0]['keyVaultUrl']='https://other.example/secret'
        with self.assertRaises(ValueError):c.same(actual,wanted)
    def test_inline_values_not_in_inventory(self):
        item={'name':'test','identity':{'type':'UserAssigned','userAssignedIdentities':{'/identities/test':{}}},
              'properties':{'environmentId':'/env','configuration':{'secrets':[{'name':'inline','value':'fictional-sensitive'}]},
                            'template':{'containers':[{'image':'sample'}]}}}
        groups,unsupported=p.summarize([item]);self.assertEqual(len(unsupported),1)
        self.assertNotIn('fictional-sensitive',str((groups,unsupported)))
    def test_missing_reference_does_not_start_network_or_leak(self):
        output=io.StringIO()
        with patch.dict(os.environ,{},clear=True),patch.object(socket,'getaddrinfo') as lookup,contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit):exec(compile(c.probe(17,1),'private-probe','exec'),{})
            lookup.assert_not_called()
        self.assertEqual(output.getvalue().strip(),'UPGRADE_SECRET_RESOLUTION_ERROR ValueError')
    def test_exception_message_never_logged(self):
        output=io.StringIO()
        with patch.dict(os.environ,{'CHECK_SECRET_000':'fictional-secret-value'}),patch.object(socket,'getaddrinfo',side_effect=RuntimeError('fictional-secret-value')),contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit):exec(compile(c.probe(17,1),'private-probe','exec'),{})
        self.assertNotIn('fictional-secret-value',output.getvalue())
    def test_success_prints_counts_not_values(self):
        output=io.StringIO()
        class TLS:
            def __init__(self,host):self.host=host
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def getpeername(self):return (c.dns.EXPECTED[self.host],443)
        class Context:
            def wrap_socket(self,sock,server_hostname):return TLS(server_hostname)
        lookup=lambda host,*args,**kwargs:[(None,None,None,None,(c.dns.EXPECTED[host],443))]
        with patch.dict(os.environ,{'CHECK_SECRET_000':'fictional-secret-value'}),patch.object(socket,'getaddrinfo',side_effect=lookup),patch.object(socket,'create_connection',side_effect=lambda address,**kwargs:TLS(address[0])),patch.object(ssl,'create_default_context',return_value=Context()),contextlib.redirect_stdout(output):
            exec(compile(c.probe(17,1),'private-probe','exec'),{})
        self.assertNotIn('fictional-secret-value',output.getvalue())
        result=json.loads(output.getvalue().removeprefix(c.PREFIX))
        self.assertTrue(result['passed']);self.assertEqual(result['resolved_reference_count'],1)
        self.assertEqual(len(result['peers']),9)

if __name__=='__main__':unittest.main()
