import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
import private_upgrade_archive_transport_20261010 as m
import complete_upgrade_vault_dns_20261010 as dns


class PrivateArchiveTests(unittest.TestCase):
    def test_exact_host_routes(self):
        original=Mock(return_value=['result'])
        self.assertEqual(m.routed_lookup(original,1234)(m.HOST,443,type=1),['result'])
        original.assert_called_once_with('127.0.0.1',1234,type=1)

    def test_other_hosts_unchanged(self):
        original=Mock();m.routed_lookup(original,1234)('management.azure.com',443)
        original.assert_called_once_with('management.azure.com',443)

    def test_wrong_port_denied(self):
        original=Mock()
        with self.assertRaises(ValueError):m.routed_lookup(original,1234)(m.HOST,80)
        original.assert_not_called()

    def test_valid_private_response(self):
        response=NS(raw=NS(_connection=NS(sock=NS(getpeername=lambda:('127.0.0.1',1234)))))
        original=Mock(return_value=response);count={'private_https_responses':0}
        self.assertIs(m.response_guard(original,1234,count)(None,NS(url='https://'+m.HOST+'/test')),response)
        self.assertEqual(count['private_https_responses'],1)

    def test_public_response_rejected(self):
        response=NS(raw=NS(_connection=NS(sock=NS(getpeername=lambda:('20.1.1.1',443)))),close=Mock())
        with self.assertRaises(ValueError):m.response_guard(Mock(return_value=response),1234,{'private_https_responses':0})(None,NS(url='https://'+m.HOST+'/test'))
        response.close.assert_called_once()

    def test_insecure_or_proxy_rejected_before_request(self):
        for url,kwargs in [('http://'+m.HOST,{}),('https://'+m.HOST+':444',{}),('https://'+m.HOST,{'verify':False}),('https://'+m.HOST,{'proxies':{'https':'https://proxy'}})]:
            with self.subTest(url=url,kwargs=kwargs):
                original=Mock()
                with self.assertRaises(ValueError):m.response_guard(original,1234,{})(None,NS(url=url),**kwargs)
                original.assert_not_called()

    def test_missing_socket_denied(self):
        response=NS(raw=NS(_connection=None),close=Mock())
        with self.assertRaises(ValueError):m.response_guard(Mock(return_value=response),1234,{})(None,NS(url='https://'+m.HOST))

    def test_nonblob_untouched(self):
        response=object();original=Mock(return_value=response)
        self.assertIs(m.response_guard(original,1234,{})(None,NS(url='https://management.azure.com/test')),response)

    def test_dns_association_exact(self):
        self.assertEqual(dns.body(),{'properties':{'privateDnsZoneConfigs':[{'name':'service-zone','properties':{'privateDnsZoneId':dns.p.ZONE}}]}})

    def test_dns_cutover_not_part_of_body(self):
        self.assertNotIn('virtualNetworkLinks',str(dns.body()))
        self.assertEqual(dns.GROUP,dns.p.ENDPOINT+'/privateDnsZoneGroups/upgrade')


if __name__=='__main__':unittest.main()
