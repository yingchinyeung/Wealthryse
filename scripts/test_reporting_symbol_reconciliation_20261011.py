import copy,unittest
from reporting_symbol_reconciliation_20261011 import compare,validate_evidence,CONID,MASTER_SHA,BROKER_HASHES

class Tests(unittest.TestCase):
    def setUp(self):
        self.a=[dict(conid=CONID,symbol='SKYD',market_value=123,weight=.1,sector='Communication Services')]
        self.e=[{**self.a[0],'symbol':'PSKY'}]
        self.kw=dict(subject='U16088571',as_of='2026-09-30')
        self.master=dict(conid=CONID,symbol='SKYD',asset_category='STK',currency='USD',source_sha256=MASTER_SHA)
        self.broker=[dict(conid=CONID,symbol='PSKY',description='PARAMOUNT SKYDANCE CL B',section='OpenPositions',record_type='OpenPosition',report_date=d,row_sha256=h) for d,h in BROKER_HASHES.items()]
    def test_evidence(self):validate_evidence(self.master,self.broker)
    def test_master_drift(self):
        self.master['source_sha256']='bad'
        with self.assertRaises(ValueError):validate_evidence(self.master,self.broker)
    def test_missing_broker(self):
        with self.assertRaises(ValueError):validate_evidence(self.master,self.broker[:1])
    def test_bad_broker_hash(self):
        self.broker[0]['row_sha256']='bad'
        with self.assertRaises(ValueError):validate_evidence(self.master,self.broker)
    def test_alias_no_mutation(self):
        before=copy.deepcopy(self.a)
        self.assertEqual(len(compare(self.a,self.e,**self.kw)),1)
        self.assertEqual(self.a,before)
    def test_amount_diff(self):
        self.a[0]['market_value']=124
        with self.assertRaises(ValueError):compare(self.a,self.e,**self.kw)
    def test_sector_diff(self):
        self.a[0]['sector']='Technology'
        with self.assertRaises(ValueError):compare(self.a,self.e,**self.kw)
    def test_other_conid(self):
        self.a[0]['conid']=self.e[0]['conid']='OTHER'
        with self.assertRaises(ValueError):compare(self.a,self.e,**self.kw)
    def test_wrong_date(self):
        self.kw['as_of']='2025-01-20'
        with self.assertRaises(ValueError):compare(self.a,self.e,**self.kw)
    def test_other_subject(self):
        self.kw['subject']='alpha-world-t'
        with self.assertRaises(ValueError):compare(self.a,self.e,**self.kw)
    def test_duplicate(self):
        with self.assertRaises(ValueError):compare(self.a*2,self.e,**self.kw)
    def test_equal_other_row(self):
        self.assertEqual(compare(self.e,self.e,**self.kw),[])

if __name__=='__main__':unittest.main()
