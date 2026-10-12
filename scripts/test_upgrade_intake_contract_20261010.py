"""Synthetic entrypoint checks; all storage/database operations are mocked."""
import contextlib, hashlib, io, json, os, runpy, unittest
from pathlib import Path
from unittest.mock import Mock, patch


class IntakeContract(unittest.TestCase):
    def setUp(self):
        self.main=runpy.run_path(str(Path(os.environ.get('QUALIFICATION_APP','/app'))/'scripts/register_account_template_intake.py'),run_name='qualification_import')['main']
        self.g=self.main.__globals__
        self.body=b'{"subject_id":"synthetic-subject","assets":[]}'
        self.env={'DEPLOYMENT_ENVIRONMENT':'internal-production','DATABASE_URL':'postgresql://synthetic.invalid/test',
            'PORTFOLIO_TENANT_ID':'synthetic-tenant','TEMPLATE_SUBJECT_ID':'synthetic-subject',
            'AZURE_STORAGE_ACCOUNT_URL':'https://synthetic.invalid','TEMPLATE_MANIFEST_BLOB':'synthetic-manifest',
            'EXPECTED_TEMPLATE_MANIFEST_SHA256':hashlib.sha256(self.body).hexdigest()}

    def run_case(self,change=None,fail=None):
        env=dict(self.env);env.update(change or {})
        conn=Mock();download=Mock(return_value=self.body);parse=Mock(return_value=('synthetic-asset',))
        verify=Mock();register=Mock(return_value={'asset_count':1});connect=Mock(return_value=conn)
        operations={'download':download,'parse':parse,'verify':verify,'register':register}
        if fail:operations[fail].side_effect=RuntimeError('synthetic failure')
        up=Mock(side_effect=AssertionError('no upload without bundle'))
        with patch.dict(os.environ,env,clear=True),patch.dict(self.g,download_blob_bytes=download,parse_template_manifest=parse,
            verify_template_assets=verify,register_template_assets=register,connect=connect,upload_file=up,upload_bytes=up),contextlib.redirect_stdout(io.StringIO()):
            error=bool(fail or change)
            if error:
                with self.assertRaises(RuntimeError):self.main()
            else:self.assertEqual(self.main(),0)
        up.assert_not_called()
        if fail=='register':conn.rollback.assert_called_once();conn.commit.assert_not_called();conn.close.assert_called_once()
        elif error:connect.assert_not_called();conn.commit.assert_not_called();conn.close.assert_not_called()
        else:
            conn.commit.assert_called_once();conn.close.assert_called_once();conn.rollback.assert_not_called()
            self.assertEqual(connect.call_args.args[2],'synthetic-tenant')
            self.assertEqual(parse.call_args.kwargs,{'subject_id':'synthetic-subject','container_name':'internal-reports'})
            self.assertEqual(register.call_args.kwargs['subject_id'],'synthetic-subject')
            self.assertEqual(register.call_args.kwargs['tenant_id'],'synthetic-tenant')
            verify.assert_called_once()

    def test_private_hash_verified_success(self):self.run_case()
    def test_unknown_environment(self):self.run_case({'DEPLOYMENT_ENVIRONMENT':'production'})
    def test_missing_database(self):self.run_case({'DATABASE_URL':''})
    def test_sqlite_rejected(self):self.run_case({'DATABASE_URL':'sqlite:///test'})
    def test_missing_tenant(self):self.run_case({'PORTFOLIO_TENANT_ID':''})
    def test_missing_subject(self):self.run_case({'TEMPLATE_SUBJECT_ID':''})
    def test_missing_storage(self):self.run_case({'AZURE_STORAGE_ACCOUNT_URL':''})
    def test_missing_manifest(self):self.run_case({'TEMPLATE_MANIFEST_BLOB':''})
    def test_missing_digest(self):self.run_case({'EXPECTED_TEMPLATE_MANIFEST_SHA256':''})
    def test_changed_bytes_rejected(self):self.run_case({'EXPECTED_TEMPLATE_MANIFEST_SHA256':'0'*64})
    def test_download_failure(self):self.run_case(fail='download')
    def test_invalid_manifest(self):self.run_case(fail='parse')
    def test_asset_verification_failure(self):self.run_case(fail='verify')
    def test_database_failure_rolls_back(self):self.run_case(fail='register')


if __name__=='__main__':unittest.main()
