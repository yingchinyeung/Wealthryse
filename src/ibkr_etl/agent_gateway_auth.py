"""Strict Entra v2 delegated access-token validation for a dedicated API.

No production audience/client is enrolled here. The trusted composition supplies
reviewed registration IDs; token/body claims never configure keys or authority.
App-only/background tokens require a separate adapter and are rejected here.
"""
import json
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass
import jwt
import requests


class GatewayAuthenticationError(RuntimeError):
    pass


def canonical_uuid(value):
    if not isinstance(value,str) or str(uuid.UUID(value))!=value:
        raise ValueError('Canonical registered identifier required')
    return value


def strict_json(value):
    def pairs(items):
        result={}
        for key,item in items:
            if key in result: raise ValueError('Duplicate JSON member')
            result[key]=item
        return result
    def constant(_): raise ValueError('Non-finite JSON value')
    def finite(value):
        number=float(value)
        if not math.isfinite(number): raise ValueError('Non-finite JSON value')
        return number
    return json.loads(value,object_pairs_hook=pairs,parse_constant=constant,parse_float=finite)


@dataclass(frozen=True)
class TokenPolicy:
    entra_tenant_id: str
    audience: str
    client_ids: frozenset[str]
    scope: str = 'Portfolio.Observe'

    def __post_init__(self):
        canonical_uuid(self.entra_tenant_id);canonical_uuid(self.audience)
        if not isinstance(self.client_ids,frozenset) or not 1<=len(self.client_ids)<=20:
            raise ValueError('Reviewed client registry required')
        for client in self.client_ids: canonical_uuid(client)
        if self.audience in self.client_ids or not re.fullmatch(r'[A-Za-z][A-Za-z0-9.]{0,79}',self.scope):
            raise ValueError('Separate API audience and delegated scope required')

    @property
    def issuer(self): return f'https://login.microsoftonline.com/{self.entra_tenant_id}/v2.0'


@dataclass(frozen=True)
class VerifiedUser:
    entra_tenant_id: str
    object_id: str
    client_id: str
    expires_at: int


class EntraSigningKeys:
    """Fixed tenant endpoint, bounded reads, no redirects or stale-key fallback.

Unknown key IDs cannot trigger refresh storms. A key rotation is picked up on
the next five-minute refresh; failed refreshes are rate-limited to 30 seconds.
Neither endpoint selection nor refresh policy is controlled by the JWT.
"""
    def __init__(self,tenant_id):
        self.tenant_id=canonical_uuid(tenant_id)
        self.url=f'https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys'
        self._keys={};self._expires=0.;self._retry_after=0.;self._lock=threading.Lock()

    def _download(self):
        with requests.get(self.url,timeout=(3,10),stream=True,allow_redirects=False) as response:
            if response.status_code!=200: raise ValueError('Signing keys unavailable')
            data=bytearray()
            for chunk in response.iter_content(8192):
                data.extend(chunk)
                if len(data)>262144: raise ValueError('Signing key response too large')
            return strict_json(bytes(data))

    def key(self,kid):
        with self._lock:
            now=time.monotonic()
            if now>=self._expires:
                if now<self._retry_after: raise GatewayAuthenticationError('signing_keys_unavailable')
                self._keys={};self._retry_after=now+30
                try:
                    document=self._download()
                    rows=document.get('keys') if isinstance(document,dict) else None
                    if not isinstance(rows,list) or not 1<=len(rows)<=100: raise ValueError('Invalid key set')
                    keys={}
                    for row in rows:
                        if not isinstance(row,dict): raise ValueError('Invalid key record')
                        name=row.get('kid')
                        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',name) or name in keys:
                            raise ValueError('Ambiguous signing key')
                        if row.get('kty')!='RSA' or row.get('use')!='sig' or row.get('alg','RS256')!='RS256':
                            raise ValueError('Unsupported signing key')
                        issuer=row.get('issuer',f'https://login.microsoftonline.com/{self.tenant_id}/v2.0')
                        if issuer.replace('{tenantid}',self.tenant_id)!=f'https://login.microsoftonline.com/{self.tenant_id}/v2.0':
                            raise ValueError('Signing key issuer differs')
                        if any(k in row for k in ('d','p','q','dp','dq','qi')) or row.get('key_ops',['verify'])!=['verify']:
                            raise ValueError('Unexpected key material or use')
                        key=jwt.PyJWK.from_dict(row,algorithm='RS256').key
                        if not 2048<=key.key_size<=8192: raise ValueError('Signing key size rejected')
                        keys[name]=key
                    self._keys=keys;self._expires=time.monotonic()+300
                except Exception:
                    raise GatewayAuthenticationError('signing_keys_unavailable') from None
            if kid not in self._keys: raise GatewayAuthenticationError('invalid_access_token')
            return self._keys[kid]


class EntraAccessTokens:
    def __init__(self,policy:TokenPolicy,keys=None):
        self.policy=policy
        self.keys=keys if keys is not None else EntraSigningKeys(policy.entra_tenant_id)

    def authenticate(self,authorization):
        try:
            if not isinstance(authorization,str) or len(authorization)>20000:
                raise ValueError('Invalid bearer header')
            match=re.fullmatch(r'Bearer ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)',authorization)
            if not match: raise ValueError('Bearer access token required')
            token=match.group(1)
            # Reject duplicate JSON and non-finite numbers before library decode.
            header,payload,_=token.split('.')
            header=strict_json(jwt.utils.base64url_decode(header))
            raw=strict_json(jwt.utils.base64url_decode(payload))
            if not isinstance(header,dict) or not isinstance(raw,dict): raise ValueError('Invalid token JSON')
            if (header.get('alg')!='RS256' or header.get('typ') not in ('JWT','at+jwt')
                or set(header)-{'alg','typ','kid','x5t'}
                or not isinstance(header.get('kid'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',header['kid'])):
                raise ValueError('Unregistered token header')
            claims=jwt.decode(token,self.keys.key(header['kid']),algorithms=['RS256'],
                audience=self.policy.audience,issuer=self.policy.issuer,leeway=0,
                options={'strict_aud':True,'require':['exp','iat','nbf','iss','aud','tid','oid','azp','scp','sub','ver']})
            if (claims['ver']!='2.0' or claims['tid']!=self.policy.entra_tenant_id or claims.get('idtyp')=='app'
                or canonical_uuid(claims['azp']) not in self.policy.client_ids):
                raise ValueError('Unregistered delegated caller')
            for name in ('exp','iat','nbf'):
                if type(claims[name]) is not int: raise ValueError('Integer token time required')
            if not (claims['iat']<claims['exp'] and claims['nbf']<claims['exp']): raise ValueError('Invalid token lifetime')
            if not isinstance(claims['sub'],str) or not 1<=len(claims['sub'])<=256:
                raise ValueError('Invalid subject')
            scopes=claims['scp']
            if not isinstance(scopes,str) or len(scopes)>4096 or self.policy.scope not in scopes.split(' '):
                raise ValueError('Required delegated scope missing')
            return VerifiedUser(claims['tid'],canonical_uuid(claims['oid']),claims['azp'],claims['exp'])
        except GatewayAuthenticationError: raise
        except Exception:
            raise GatewayAuthenticationError('invalid_access_token') from None
