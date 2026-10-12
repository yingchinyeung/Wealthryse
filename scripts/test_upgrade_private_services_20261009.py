import contextlib,io,json,unittest
from unittest.mock import MagicMock,patch
import probe_upgrade_private_services_20261009 as probe

class PrivateServicesTests(unittest.TestCase):
    def target(self):return {'kind':'secret','url':'https://sample.vault.azure.net/secrets/database-url?api-version=7.4','private_ip':'10.50.10.8','alias':'database'}
    def test_invalid_scope_rejected_before_network(self):
        for change in ({'url':'http://sample.vault.azure.net/secrets/db'},{'url':'https://sample.evil.test/secrets/db'},{'private_ip':'8.8.8.8'},{'kind':'write'}):
            with patch.object(probe,'TARGETS',[{**self.target(),**change}],create=True),patch.object(probe,'ManagedIdentityCredential') as credential:
                with self.assertRaises(ValueError):probe.main()
                credential.assert_not_called()
    def test_empty_scope_rejected(self):
        with patch.object(probe,'TARGETS',[],create=True):
            with self.assertRaises(ValueError):probe.main()
    def test_read_only_secret_not_logged_and_resolution_restored(self):
        original=probe.socket.getaddrinfo;credential=MagicMock();credential.get_token.return_value.token='TOKEN_SENTINEL'
        session=MagicMock();response=session.request.return_value;response.status_code=200;response.json.return_value={'value':'SECRET_SENTINEL'}
        tls=MagicMock();tls.wrap_socket.return_value.__enter__.return_value.getpeername.return_value=('10.50.10.8',443)
        output=io.StringIO()
        with patch.object(probe,'TARGETS',[self.target()],create=True),patch.dict(probe.os.environ,{'AZURE_CLIENT_ID':'existing-client'}),patch.object(probe,'ManagedIdentityCredential',return_value=credential),patch.object(probe.requests,'Session',return_value=session),patch.object(probe.socket,'create_connection'),patch.object(probe.ssl,'create_default_context',return_value=tls),contextlib.redirect_stdout(output):probe.main()
        self.assertIs(probe.socket.getaddrinfo,original)
        args,kwargs=session.request.call_args
        self.assertEqual(args[0],'GET');self.assertFalse(kwargs['allow_redirects'])
        self.assertNotIn('SECRET_SENTINEL',output.getvalue());self.assertNotIn('TOKEN_SENTINEL',output.getvalue())
        self.assertTrue(json.loads(output.getvalue().splitlines()[-1].split(' ',1)[1])['passed'])
        credential.close.assert_called_once();session.close.assert_called_once()

if __name__=='__main__':unittest.main()
