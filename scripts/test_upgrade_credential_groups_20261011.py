import unittest
import review_upgrade_credential_groups_20261011 as c

class GroupTests(unittest.TestCase):
    def uri(self,password='fictional%40test',query='sslmode=require'):
        return 'postgresql://test_role:'+password+'@ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com/ibkr_staging?'+query
    def test_decoding(self):self.assertEqual(c.parse_connection(self.uri())[1],'fictional@test')
    def test_host_redirect_denied(self):
        with self.assertRaises(ValueError):c.parse_connection(self.uri(query='host=evil.example'))
    def test_wrong_host_denied(self):
        with self.assertRaises(ValueError):c.parse_connection(self.uri().replace('ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com','evil.example'))
    def test_coupled_entries(self):
        groups,unmatched,_=c.summarize({('vault','db-url'):self.uri(),('vault','app-password'):'fictional@test'})
        self.assertEqual(unmatched,[]);self.assertEqual(groups[0]['matching_password_secrets'],['vault/app-password'])
        self.assertNotIn('fictional',str(groups))
    def test_inconsistent_coupled_urls(self):
        groups,_,_=c.summarize({('vault','db-url'):self.uri(),('vault','other-url'):self.uri('different')})
        self.assertFalse(groups[0]['connection_passwords_consistent'])
    def test_unmatched_password(self):
        _,unmatched,_=c.summarize({('vault','db-url'):self.uri(),('vault','app-password'):'different'})
        self.assertEqual(unmatched,['vault/app-password'])
    def test_broker_token_forbidden(self):
        with self.assertRaises(ValueError):c.summarize({('vault','ibkr-flex-token'):'fictional'})
    def test_unexpected_host_not_qualified(self):
        groups,_,unsupported=c.summarize({('vault','db-url'):self.uri().replace('ibkrv2stg-pg-auhecwc4itaoo','unexpected')})
        self.assertEqual(groups,[]);self.assertEqual(len(unsupported),1);self.assertNotIn('fictional',str(unsupported))

if __name__=='__main__':unittest.main()
