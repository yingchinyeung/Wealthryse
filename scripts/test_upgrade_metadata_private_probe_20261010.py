import copy,unittest
import control_upgrade_metadata_private_20261010 as m


class MetadataProofTests(unittest.TestCase):
    def setUp(self):
        self.proof={'passed':True,'vault':'ibkrv11-5rcc62xxqblay','private_peer':'10.50.10.12',
            'source_sha256':'test-source','verified_tls':True,'statuses':[200],'listed':7,
            'secret_values_read':0,'secret_values_changed':0,'permissions_changed':False,'production_dns_changed':False}

    def test_accept(self):m.accept(self.proof,'10.50.10.12','test-source')

    def test_reject_mismatches(self):
        for field,value in [('passed',False),('vault','different'),('private_peer','1.1.1.1'),
                ('source_sha256','other'),('verified_tls',False),('statuses',[]),('statuses',[200,403]),
                ('listed',True),('listed',501),('listed',-1),('secret_values_read',1),
                ('secret_values_read',False),('secret_values_changed',1),('permissions_changed',True),('production_dns_changed',True)]:
            with self.subTest(field=field,value=value):
                changed=copy.deepcopy(self.proof);changed[field]=value
                with self.assertRaises(ValueError):m.accept(changed,'10.50.10.12','test-source')


if __name__=='__main__':unittest.main()
