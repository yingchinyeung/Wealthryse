import json
import hashlib
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock,patch
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from ibkr_etl.agent_gateway_auth import TokenPolicy,EntraAccessTokens,EntraSigningKeys,GatewayAuthenticationError,strict_json
from ibkr_etl.agent_gateway import GatewayBinding,ObservationGateway,GatewayDenied,DatabaseUserDirectory,create_gateway_app,MAX_BODY
from ibkr_etl.agent_observation import ObservationBroker,DisabledAuthority,AuthorityState
from ibkr_etl.agent_governance import ObservationGrant
from ibkr_etl.util import canonical_json
import test_agent_observation as broker_fixtures

ENTRA='00000000-0000-0000-0000-000000000010'
TENANT='00000000-0000-0000-0000-000000000001'
AUD='00000000-0000-0000-0000-000000000020'
CLIENT='00000000-0000-0000-0000-000000000030'
USER='00000000-0000-0000-0000-000000000040'
AGENT='00000000-0000-0000-0000-000000000050'
BODY=json.dumps(dict(tool_name='get_portfolio_summary',arguments={'subject_id':'account-a'})).encode()


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.other=rsa.generate_private_key(public_exponent=65537,key_size=2048)

    def setUp(self):
        self.policy=TokenPolicy(ENTRA,AUD,frozenset({CLIENT}))
        self.keys=Mock();self.keys.key.return_value=self.private.public_key()
        self.auth=EntraAccessTokens(self.policy,self.keys)
        now=int(time.time())
        self.claims=dict(iss=self.policy.issuer,aud=AUD,tid=ENTRA,oid=USER,azp=CLIENT,
            ver='2.0',sub='synthetic-subject',scp='Portfolio.Observe',iat=now-5,nbf=now-5,exp=now+300)
        self.directory=Mock();self.directory.resolve.return_value='current@example.test'
        self.broker=Mock();self.broker.observe.return_value={'subject_id':'account-a','tool_name':'get_portfolio_summary','data':{}}
        self.gateway=ObservationGateway(binding=GatewayBinding(TENANT,AGENT),authenticator=self.auth,
            directory=self.directory,broker=self.broker,enabled=True)

    def token(self,claims=None,headers=None,key=None):
        return 'Bearer '+jwt.encode(self.claims if claims is None else claims,key or self.private,
            algorithm='RS256',headers=headers or {'kid':'test-key','typ':'JWT'})

    def test_valid_signature_binds_server_agent_and_database_email(self):
        claims=dict(self.claims,email='owner@attacker.test',preferred_username='owner',roles=['owner'])
        self.gateway.invoke(self.token(claims),BODY)
        caller,tool,args=self.broker.observe.call_args.args
        self.assertEqual((caller.tenant_id,caller.agent_principal_id,caller.user_object_id,caller.user_email),
            (TENANT,AGENT,USER,'current@example.test'))
        self.directory.resolve.assert_called_once_with(TENANT,USER)

    def test_bad_signature_rejected_before_directory_or_broker(self):
        with self.assertRaises(GatewayDenied): self.gateway.invoke(self.token(key=self.other),BODY)
        self.directory.resolve.assert_not_called();self.broker.observe.assert_not_called()

    def test_exact_issuer_audience_tenant_and_client_required(self):
        for field,value in [('iss',self.policy.issuer+'/'),('aud',CLIENT),('aud',[AUD]),('tid',TENANT),('azp',AGENT),('oid','not-uuid'),('ver','1.0')]:
            with self.subTest(field=field,value=value),self.assertRaises(GatewayAuthenticationError):
                self.auth.authenticate(self.token(dict(self.claims,**{field:value})))

    def test_expiry_not_before_and_future_issuance(self):
        now=int(time.time())
        for field,value in [('exp',now-1),('nbf',now+100),('iat',now+100),('exp',True),('iat',str(now-5)),('nbf',float(now-5))]:
            with self.subTest(field=field),self.assertRaises(GatewayAuthenticationError):
                self.auth.authenticate(self.token(dict(self.claims,**{field:value})))

    def test_app_only_id_token_and_missing_scope_rejected(self):
        for changes in [dict(idtyp='app'),dict(scp='User.Read'),dict(scp=['Portfolio.Observe']),dict(scp='Portfolio.Observe.All')]:
            with self.assertRaises(GatewayAuthenticationError): self.auth.authenticate(self.token(dict(self.claims,**changes)))
        for field in ('scp','exp','iat','nbf','oid','azp','tid','sub','ver'):
            claims=dict(self.claims);claims.pop(field)
            with self.assertRaises(GatewayAuthenticationError): self.auth.authenticate(self.token(claims))

    def test_header_key_injection_and_unsupported_algorithm(self):
        for changes in [dict(jku='https://attacker.test/keys'),dict(x5u='https://attacker.test/cert'),dict(crit=['other']),dict(kid='../key'),dict(typ='unexpected')]:
            with self.assertRaises(GatewayAuthenticationError): self.auth.authenticate(self.token(headers={'kid':'test-key','typ':'JWT',**changes}))
        token=jwt.encode(self.claims,'a'*64,algorithm='HS256',headers={'kid':'test-key','typ':'JWT'})
        with self.assertRaises(GatewayAuthenticationError): self.auth.authenticate('Bearer '+token)

    def test_duplicate_token_claims_and_malformed_headers_rejected(self):
        token=self.token().split(' ')[1].split('.')
        token[1]=jwt.utils.base64url_encode(b'{"oid":"a","oid":"b"}').decode()
        for value in ['Bearer '+'.'.join(token),None,'Bearer bad','Basic credential','Bearer '+('a'*21000),'Bearer a.b.c\n']:
            with self.assertRaises(GatewayAuthenticationError): self.auth.authenticate(value)

    def test_disabled_gateway_never_authenticates_or_reads(self):
        self.gateway.enabled=False
        with patch.object(self.auth,'authenticate') as auth:
            with self.assertRaises(GatewayDenied): self.gateway.invoke('unused',BODY)
            auth.assert_not_called()
        self.directory.resolve.assert_not_called()

    def test_only_deployed_tool_and_busy_rate_status(self):
        from ibkr_etl.agent_gateway_limits import GatewayAdmission
        self.gateway.supported_tools=frozenset({'get_performance_series'})
        with self.assertRaisesRegex(GatewayDenied,'tool_not_deployed'): self.gateway.invoke(self.token(),BODY)
        self.directory.resolve.assert_not_called()
        self.gateway.supported_tools=None
        self.gateway.admission=GatewayAdmission(per_minute=1)
        self.gateway.invoke(self.token(),BODY)
        with self.assertRaises(GatewayDenied) as caught: self.gateway.invoke(self.token(),BODY)
        self.assertEqual(caught.exception.status,429)

    def test_identity_overrides_in_body_rejected(self):
        body=json.loads(BODY)
        for field in ('tenant_id','agent_principal_id','user_object_id','user_email','roles','enabled'):
            with self.assertRaises(GatewayDenied): self.gateway.invoke(self.token(),json.dumps(dict(body,**{field:'injected'})).encode())
        self.directory.resolve.assert_not_called();self.broker.observe.assert_not_called()

    def test_bounded_strict_body_and_tool_schema(self):
        for body in [b'x'*(MAX_BODY+1),b'{}',b'[]',b'{"tool_name":"a","tool_name":"b","arguments":{}}',
            b'{"tool_name":"get_portfolio_summary","arguments":{"subject_id":"a","limit":NaN}}',
            json.dumps(dict(tool_name='execute_sql',arguments={'subject_id':'a'})).encode(),
            json.dumps(dict(tool_name='get_portfolio_summary',arguments={'subject_id':'a','roles':['owner']})).encode()]:
            with self.assertRaises(GatewayDenied): self.gateway.invoke(self.token(),body)
        self.broker.observe.assert_not_called()

    def test_user_revoked_and_token_expired_during_call_suppresses_result(self):
        self.directory.resolve.return_value=None
        with self.assertRaises(GatewayDenied): self.gateway.invoke(self.token(),BODY)
        self.broker.observe.assert_not_called()
        self.directory.resolve.return_value='current@example.test'
        self.gateway.clock=Mock(side_effect=[time.time(),self.claims['exp']+1])
        with self.assertRaisesRegex(GatewayDenied,'expired_during'): self.gateway.invoke(self.token(),BODY)

    def test_unregistered_agent_rejected_by_actual_broker_without_account_read(self):
        connection=Mock();audit=Mock()
        self.gateway.broker=ObservationBroker(connection,authority=DisabledAuthority(),audit=audit,enabled=True)
        with self.assertRaisesRegex(GatewayDenied,'observation_denied'): self.gateway.invoke(self.token(),BODY)
        connection.assert_not_called();audit.record.assert_not_called()

    def test_error_body_and_logs_never_expose_credentials_or_exception(self):
        self.directory.resolve.side_effect=RuntimeError('private credential and query')
        token=self.token()
        with self.assertLogs('uvicorn.error.ibkr_gateway',level='INFO') as logs:
            with self.assertRaisesRegex(GatewayDenied,'gateway_unavailable'): self.gateway.invoke(token,BODY)
        self.assertNotIn(token,str(logs.output));self.assertNotIn('private credential',str(logs.output))

    def test_http_ignores_identity_headers_and_requires_one_bearer(self):
        client=TestClient(create_gateway_app(self.gateway))
        response=client.post('/v1/observations',headers={'x-ms-client-principal-id':USER},json=json.loads(BODY))
        self.assertEqual(response.status_code,401)
        response=client.post('/v1/observations',headers=[('authorization',self.token()),('authorization',self.token())],json=json.loads(BODY))
        self.assertEqual(response.status_code,401)
        response=client.post('/v1/observations',headers={'authorization':self.token(),'x-ms-client-principal-id':'forged'},json=json.loads(BODY))
        self.assertEqual(response.status_code,200);self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(self.broker.observe.call_args.args[0].user_object_id,USER)

    def test_http_bounds_and_no_documentation_or_get_route(self):
        client=TestClient(create_gateway_app(self.gateway))
        response=client.post('/v1/observations',headers={'authorization':self.token(),'content-type':'application/json'},content=b'x'*(MAX_BODY+1))
        self.assertEqual(response.status_code,413)
        self.assertEqual(client.get('/v1/observations').status_code,405)
        self.assertEqual(client.get('/docs').status_code,404)
        self.broker.observe.assert_not_called()

    def integrated_broker(self):
        class Reader(broker_fixtures.Reader):
            def execute(reader,sql,params=()):
                if 'FROM app_users' in sql:
                    reader.queries.append((sql,params))
                    row=dict(entra_object_id=USER,email='current@example.test',display_name='Synthetic',
                        status='active' if reader.user_active else 'disabled')
                    return broker_fixtures.Result(row,[row] if reader.user_active else [])
                return super().execute(sql,params)
        reader=Reader()
        @contextmanager
        def factory(tenant):
            self.assertEqual(tenant,TENANT)
            yield reader
        now=datetime.now(timezone.utc)
        grant=ObservationGrant(TENANT,AGENT,'data_analyst',frozenset({'account-a'}),frozenset(),
            frozenset({'get_performance_series'}),now+timedelta(minutes=5),enabled=True)
        authority=Mock();authority.load_current.return_value=AuthorityState(grant,False)
        audit=Mock()
        def receipt(detail):
            event={key:detail[key] for key in ('operation_id','role','action','phase')}
            event.update(sequence=audit.record.call_count,previous_sha256='0'*64,
                occurred_at=now.isoformat(),event_id=detail['operation_id']+'-'+detail['phase'],cost_microusd=0,
                evidence_sha256=hashlib.sha256(canonical_json(detail).encode()).hexdigest())
            event['sha256']=hashlib.sha256(canonical_json(event).encode()).hexdigest()
            return dict(database_committed=True,protected_archive_verified=True,event=event)
        audit.record.side_effect=receipt
        self.gateway.directory=DatabaseUserDirectory(factory)
        self.gateway.broker=ObservationBroker(factory,authority=authority,audit=audit,enabled=True)
        body=json.dumps(dict(tool_name='get_performance_series',arguments={'subject_id':'account-a'})).encode()
        return reader,authority,audit,body

    def test_signed_request_through_real_broker_and_deterministic_tool(self):
        reader,authority,audit,body=self.integrated_broker()
        result=self.gateway.invoke(self.token(),body)
        self.assertEqual(result['data']['series'],[dict(date='2026-09-30',currency='USD',nav=100.25,flow=0.0)])
        self.assertEqual([call.args[0]['phase'] for call in audit.record.call_args_list],['started','succeeded'])
        self.assertEqual(authority.load_current.call_count,5)
        self.assertTrue(all(sql.strip().startswith('SELECT') for sql,_ in reader.queries))

    def test_protected_intent_failure_prevents_portfolio_read(self):
        reader,authority,audit,body=self.integrated_broker()
        audit.record.side_effect=RuntimeError('archive unavailable')
        with self.assertRaisesRegex(GatewayDenied,'observation_denied'): self.gateway.invoke(self.token(),body)
        self.assertFalse(any('FROM subject_nav_series' in sql for sql,_ in reader.queries))

    def test_revoked_user_or_account_access_prevents_portfolio_read(self):
        for user_active,access in [(False,['read']),(True,[])]:
            reader,authority,audit,body=self.integrated_broker()
            reader.user_active=user_active;reader.access=access
            with self.assertRaises(GatewayDenied): self.gateway.invoke(self.token(),body)
            self.assertFalse(any('FROM subject_nav_series' in sql for sql,_ in reader.queries))
            audit.record.assert_not_called()

    def test_completion_archive_failure_suppresses_http_result(self):
        reader,authority,audit,body=self.integrated_broker()
        receipt=audit.record.side_effect
        def unavailable(detail):
            if detail['phase']=='succeeded': raise RuntimeError('archive unavailable')
            return receipt(detail)
        audit.record.side_effect=unavailable
        response=TestClient(create_gateway_app(self.gateway)).post('/v1/observations',
            headers={'authorization':self.token(),'content-type':'application/json'},content=body)
        self.assertEqual(response.status_code,403)
        self.assertNotIn('100.25',response.text)
        self.assertTrue(any('FROM subject_nav_series' in sql for sql,_ in reader.queries))

    def test_non_finite_json_exponents_rejected(self):
        for value in [b'{"value":1e999}',b'{"value":-1e999}',b'{"value":NaN}']:
            with self.assertRaises(ValueError): strict_json(value)


class SigningKeyTests(unittest.TestCase):
    def setUp(self):
        private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        self.jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        self.jwk.update(kid='test-key',use='sig',issuer='https://login.microsoftonline.com/{tenantid}/v2.0')
        self.keys=EntraSigningKeys(ENTRA)

    def test_fixed_tenant_endpoint_no_token_selected_url(self):
        self.assertEqual(self.keys.url,f'https://login.microsoftonline.com/{ENTRA}/discovery/v2.0/keys')
        with self.assertRaises(ValueError): EntraSigningKeys('https://attacker.test')

    def test_download_bounded_and_redirects_not_followed(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.status_code=200;response.iter_content.return_value=[json.dumps({'keys':[self.jwk]}).encode()]
        with patch('ibkr_etl.agent_gateway_auth.requests.get',return_value=response) as get:
            self.assertEqual(self.keys._download()['keys'][0]['kid'],'test-key')
            get.assert_called_once_with(self.keys.url,timeout=(3,10),stream=True,allow_redirects=False)
            response.iter_content.return_value=[b'x'*262145]
            with self.assertRaises(ValueError): self.keys._download()
            response.status_code=302
            with self.assertRaises(ValueError): self.keys._download()

    def test_cache_and_unknown_key_do_not_trigger_fetch_storm(self):
        with patch.object(self.keys,'_download',return_value={'keys':[self.jwk]}) as download:
            self.keys.key('test-key');self.keys.key('test-key')
            for _ in range(3):
                with self.assertRaises(GatewayAuthenticationError): self.keys.key('missing')
            self.assertEqual(download.call_count,1)

    def test_stale_keys_discarded_when_refresh_fails(self):
        with patch.object(self.keys,'_download',return_value={'keys':[self.jwk]}) as download:
            self.keys.key('test-key')
            self.keys._expires=0;self.keys._retry_after=0
            download.side_effect=RuntimeError('private network detail')
            for _ in range(2):
                with self.assertRaisesRegex(GatewayAuthenticationError,'signing_keys_unavailable'): self.keys.key('test-key')
            self.assertEqual(download.call_count,2);self.assertEqual(self.keys._keys,{})

    def test_duplicate_issuer_private_material_and_algorithm_rejected(self):
        for document in [{'keys':[self.jwk,self.jwk]}]+[{'keys':[dict(self.jwk,**change)]} for change in
            [dict(issuer='https://attacker.test'),dict(d='private'),dict(alg='HS256'),dict(use='enc'),dict(key_ops=['sign'])]]:
            keys=EntraSigningKeys(ENTRA)
            with patch.object(keys,'_download',return_value=document):
                with self.assertRaises(GatewayAuthenticationError): keys.key('test-key')


class UserDirectoryTests(unittest.TestCase):
    def test_user_email_only_from_single_active_database_record(self):
        c=Mock();c.execute.return_value.fetchall.return_value=[dict(entra_object_id=USER,email='current@example.test')]
        @contextmanager
        def factory(tenant):
            self.assertEqual(tenant,TENANT);yield c
        directory=DatabaseUserDirectory(factory)
        with patch('ibkr_etl.agent_gateway._guard_database'):
            self.assertEqual(directory.resolve(TENANT,USER),'current@example.test')
            for rows in [[],[dict(entra_object_id=AGENT,email='other@example.test')],
                [dict(entra_object_id=USER,email='a'),dict(entra_object_id=USER,email='b')]]:
                c.execute.return_value.fetchall.return_value=rows
                self.assertIsNone(directory.resolve(TENANT,USER))
