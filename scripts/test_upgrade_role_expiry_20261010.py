import unittest
from unittest.mock import patch
import control_upgrade_role_expiry_20261010 as c
import probe_upgrade_role_expiry_20261010 as p


class RoleReviewTests(unittest.TestCase):
    def proof(self):
        return {'passed':True,'host':'expected','connection_role':'role',
            'database_readonly':True,'verified_tls':True,'database_writes':0,
            'credentials_changed':False,'password_hashes_read':False,
            'actual_last_rotation_verified':False,
            'roles':[{'rolname':'role','expiry_state':'no_database_expiration'}]}

    def test_accept(self):
        c.accept(self.proof(),'expected')

    def test_negative_boundaries(self):
        for key,value in [('host','other'),('verified_tls',False),('database_readonly',False),
                          ('database_writes',1),('credentials_changed',True),
                          ('password_hashes_read',True),('actual_last_rotation_verified',True),
                          ('roles',[])]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                c.accept({**self.proof(),key:value},'expected')

    def test_wrong_host_before_connection(self):
        with patch.object(p,'EXPECTED_HOST','expected',create=True),patch.dict(p.os.environ,{'DATABASE_ADMIN_URL':'postgresql://fake@wrong/db'}),patch.object(p.psycopg,'connect') as connect:
            with self.assertRaises(ValueError):p.main()
            connect.assert_not_called()


if __name__=='__main__':unittest.main()
