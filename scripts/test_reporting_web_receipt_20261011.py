import copy,unittest
from read_reporting_web_installed_receipt_20261011 import validate_env

class Tests(unittest.TestCase):
    def setUp(self):
        self.wanted=[{'name':'DATABASE_URL','secretRef':'website-database-url'},
          {'name':'PORTFOLIO_TENANT_ID','value':'00000000-0000-0000-0000-000000000001'}]
        self.actual=copy.deepcopy(self.wanted)
        self.actual[0]['secretRef']='cappjob-ibkrv2stg-inbox-acceptance-r2'
    def check(self):return validate_env('ibkrv2stg-inbox-acceptance-r2',self.actual,self.wanted)
    def test_exact(self):self.check()
    def test_order(self):self.actual.reverse();self.check()
    def test_wrong_tenant(self):
        self.actual[1]['value']='other'
        with self.assertRaises(ValueError):self.check()
    def test_inline_secret(self):
        self.actual[0]['value']='not-a-real-secret'
        with self.assertRaises(ValueError):self.check()
    def test_extra(self):
        self.actual.append({'name':'OTHER','value':'1'})
        with self.assertRaises(ValueError):self.check()
    def test_wrong_alias(self):
        self.actual[0]['secretRef']='other'
        with self.assertRaises(ValueError):self.check()
    def test_missing(self):
        self.actual.pop()
        with self.assertRaises(ValueError):self.check()

if __name__=='__main__':unittest.main()
