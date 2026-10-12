import contextlib,io,json,unittest
from unittest.mock import patch,MagicMock
import probe_upgrade_dns_route_20261010 as probe
import control_upgrade_legacy_dns_20261010 as c


class DNSBoundaryTests(unittest.TestCase):
    def test_body_has_no_registration_or_network_modification(self):
        self.assertEqual(c.body(),{'location':'global','properties':{'registrationEnabled':False,'virtualNetwork':{'id':c.VNET}}})

    def test_exact_four_legacy_links(self):
        self.assertEqual(len(c.ZONES),4)
        self.assertTrue(all('.privatelink.' in z and z.startswith('ibkrv11') for z in c.ZONES))
        self.assertTrue(all(c.link_id(z).startswith(c.zone_id(z)+'/virtualNetworkLinks/') for z in c.ZONES))

    def test_existing_modification_denied(self):
        with self.assertRaises(ValueError):c.validate_changes([{'resourceId':c.link_id(next(iter(c.ZONES))),'changeType':'Modify'}],[c.link_id(z) for z in c.ZONES])

    def check_route(self,mode,address,peer=None):
        probe.MODE=mode;probe.EXPECTED={'example.vault.azure.net':'10.40.10.6'}
        tls=MagicMock();tls.__enter__.return_value.getpeername.return_value=(peer or address,443)
        ssl=MagicMock();ssl.wrap_socket.return_value=tls
        output=io.StringIO()
        with patch.object(probe.socket,'getaddrinfo',return_value=[(2,1,6,'',(address,443))]),patch.object(probe.socket,'create_connection',return_value=MagicMock()),patch.object(probe.ssl,'create_default_context',return_value=ssl),patch.object(probe.time,'monotonic',side_effect=[0,70]),contextlib.redirect_stdout(output):probe.main()
        return json.loads(output.getvalue().split(' ',1)[1])

    def test_private_route(self):self.assertTrue(self.check_route('private','10.40.10.6')['passed'])
    def test_public_route(self):self.assertTrue(self.check_route('public','20.1.1.1')['passed'])
    def test_private_wrong_dns_denied(self):
        with self.assertRaises(ValueError):self.check_route('private','20.1.1.1')
    def test_private_wrong_tls_peer_denied(self):
        with self.assertRaises(ValueError):self.check_route('private','10.40.10.6','20.1.1.1')
    def test_public_private_range_denied(self):
        for value in ('10.40.10.6','172.16.1.1','192.168.0.1','127.0.0.1'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.check_route('public',value)
    def test_mode_typo_denied(self):
        with self.assertRaises(ValueError):self.check_route('pubic','20.1.1.1')


if __name__=='__main__':unittest.main()
