import unittest
from deploy_reporting_sector_assurance_20261011 import full_accept,only_assurance,JOB
from deploy_reporting_sector_web_20261011 import SUBJECTS
class Tests(unittest.TestCase):
    def setUp(self):self.v={'passed':True,'image':'image','proof':{'passed':True,'image':'image','subjects':[{'subject':s,'passed':True,'checks_failed':0,'status':'warn'} for s in SUBJECTS],'database_writes':0,'model_calls':0,'production_connection_used':False}}
    def test_valid(self):full_accept(self.v,'image')
    def test_wrong_image(self):
        with self.assertRaises(ValueError):full_accept(self.v,'other')
    def test_failed_check(self):
        self.v['proof']['subjects'][0]['checks_failed']=1
        with self.assertRaises(ValueError):full_accept(self.v,'image')
    def test_missing_subject(self):
        self.v['proof']['subjects'].pop()
        with self.assertRaises(ValueError):full_accept(self.v,'image')
    def test_writes(self):
        self.v['proof']['database_writes']=1
        with self.assertRaises(ValueError):full_accept(self.v,'image')
    def test_model_calls(self):
        self.v['proof']['model_calls']=1
        with self.assertRaises(ValueError):full_accept(self.v,'image')
    def test_unrelated_change(self):
        with self.assertRaises(ValueError):only_assurance({JOB:1,'web':2},{JOB:2,'web':3})
    def test_own_only(self):only_assurance({JOB:1,'web':2},{JOB:2,'web':2})
if __name__=='__main__':unittest.main()
