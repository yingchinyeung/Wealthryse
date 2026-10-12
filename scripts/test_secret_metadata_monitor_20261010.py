"""Offline boundary/fault tests, never read any credential values."""
import copy,unittest
from datetime import datetime,timezone
from unittest.mock import Mock
import secret_metadata_monitor_20261010 as m


class SecretMetadataTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,10,tzinfo=timezone.utc);self.ts=int(self.now.timestamp());self.v=m.VAULTS[0]
        self.item={'id':'https://'+self.v+'.vault.azure.net/secrets/synthetic','attributes':{'enabled':True,'created':self.ts-86400,'updated':self.ts-86400,'exp':self.ts+40*86400},
            'tags':{'owner':'synthetic','rotation-policy':'synthetic'}}
    def reasons(self,item):return m.assess(self.v,[item],self.now)['findings'][0]['reasons']
    def test_healthy(self):self.assertEqual(m.assess(self.v,[self.item],self.now)['findings'],[])
    def test_no_expiry(self):del self.item['attributes']['exp'];self.assertIn('expiry_metadata_missing',self.reasons(self.item))
    def test_expired(self):self.item['attributes']['exp']=self.ts;self.assertIn('expired',self.reasons(self.item))
    def test_expiring(self):self.item['attributes']['exp']=self.ts+30*86400;self.assertIn('expires_within_30_days',self.reasons(self.item))
    def test_age_review(self):self.item['attributes']['created']=self.ts-180*86400;self.assertIn('age_review_180_days',self.reasons(self.item))
    def test_disabled_not_paged(self):self.item['attributes']['enabled']=False;self.assertEqual(m.assess(self.v,[self.item],self.now)['findings'],[])
    def test_missing_owner_and_rotation(self):self.item['tags']={};self.assertEqual(set(self.reasons(self.item)),{'owner_metadata_missing','rotation_metadata_missing'})
    def test_tag_values_not_logged(self):self.item['tags']={'unknown':'sensitive-example'};self.assertNotIn('sensitive-example',str(m.assess(self.v,[self.item],self.now)))
    def test_secret_values_rejected(self):
        self.item['value']='do-not-log'
        with self.assertRaises(ValueError):m.assess(self.v,[self.item],self.now)
    def test_cross_vault_rejected(self):
        self.item['id']='https://other.vault.azure.net/secrets/name'
        with self.assertRaises(ValueError):m.assess(self.v,[self.item],self.now)
    def test_unknown_enabled_rejected(self):
        del self.item['attributes']['enabled']
        with self.assertRaises(ValueError):m.assess(self.v,[self.item],self.now)
    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):m.assess(self.v,[self.item,self.item],self.now)
    def test_bad_timestamp_rejected(self):
        self.item['attributes']['exp']=True
        with self.assertRaises(ValueError):m.assess(self.v,[self.item],self.now)
    def test_missing_creation(self):del self.item['attributes']['created'];self.assertIn('creation_time_missing',self.reasons(self.item))
    def test_future_nbf(self):self.item['attributes']['nbf']=self.ts+1;self.assertIn('not_yet_valid',self.reasons(self.item))
    def test_future_created(self):self.item['attributes']['created']=self.ts+1;self.assertIn('creation_time_in_future',self.reasons(self.item))
    def test_empty_vault(self):self.assertEqual(m.assess(self.v,[],self.now)['listed'],0)
    def test_pagination(self):
        nexturl='https://'+self.v+'.vault.azure.net/secrets?api-version=7.4&maxresults=25&$skiptoken=abc'
        fetch=Mock(side_effect=[{'value':[self.item],'nextLink':nexturl},{'value':[]}])
        self.assertEqual(m.collect(self.v,fetch,self.now)['listed'],1);self.assertEqual(fetch.call_count,2)
    def test_cross_host_pagination_never_fetched(self):
        fetch=Mock(return_value={'value':[],'nextLink':'https://evil.invalid/secrets?api-version=7.4'})
        with self.assertRaises(ValueError):m.collect(self.v,fetch,self.now)
        self.assertEqual(fetch.call_count,1)
    def test_secret_value_endpoint_never_fetched(self):
        fetch=Mock(return_value={'value':[],'nextLink':'https://'+self.v+'.vault.azure.net/secrets/name?api-version=7.4'})
        with self.assertRaises(ValueError):m.collect(self.v,fetch,self.now)
        self.assertEqual(fetch.call_count,1)
    def test_query_duplicate_rejected(self):
        with self.assertRaises(ValueError):m.listing_url(self.v,'https://'+self.v+'.vault.azure.net/secrets?api-version=7.4&api-version=7.4')
    def test_pagination_cycle(self):
        url='https://'+self.v+'.vault.azure.net/secrets?api-version=7.4&maxresults=25'
        with self.assertRaises(ValueError):m.collect(self.v,lambda _: {'value':[],'nextLink':url},self.now)
    def test_truncated_pagination_fails(self):
        fetch=Mock(side_effect=[{'value':[],'nextLink':'https://'+self.v+'.vault.azure.net/secrets?api-version=7.4&$skiptoken='+str(i)} for i in range(20)])
        with self.assertRaises(ValueError):m.collect(self.v,fetch,self.now)
    def test_unknown_vault(self):
        with self.assertRaises(ValueError):m.collect('other',Mock(),self.now)


if __name__=='__main__':unittest.main()
