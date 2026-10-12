"""One-time owner device sign-in and real delegated gateway denial acceptance.

Tokens stay in process memory: no token cache, files, KV, logs or model prompts.
The owner handles Microsoft authentication. No financial result is requested
until authority enrollment is independently accepted.
"""
import hashlib
import argparse
import json
import math
import re
import time
from pathlib import Path
import requests
import msal
from ibkr_etl.agent_gateway_auth import EntraAccessTokens,TokenPolicy
from ibkr_etl.specialist_gateway_runtime import ENTRA,AUDIENCE,CLIENT
from ibkr_etl.util import canonical_json

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/specialist-gateway-20261002'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--read-trial',action='store_true');args=parser.parse_args()
    acceptance=json.loads((FOLDER/'acceptance.json').read_text())
    url=acceptance['url']
    if (acceptance.get('status')!='passed' or not re.fullmatch(
        r'https://ibkrv2stg-agent-gateway\.[a-z0-9-]+\.eastasia\.azurecontainerapps\.io',url)):
        raise ValueError('Verified isolated gateway acceptance required')
    if args.read_trial:
        enrolled=json.loads((FOLDER/'authority-acceptance.json').read_text())
        if (enrolled.get('status')!='passed' or enrolled['record'].get('subjects')!=['U16088571']
            or enrolled['record'].get('background_allowed') is not False
            or enrolled['protected_receipt'].get('protected_readback_verified') is not True):
            raise ValueError('Protected exact account-read enrollment required')
    client=msal.PublicClientApplication(CLIENT,authority=f'https://login.microsoftonline.com/{ENTRA}',
        token_cache=msal.TokenCache())
    scope='api://'+AUDIENCE+'/Portfolio.Observe'
    flow=client.initiate_device_flow(scopes=[scope])
    if not all(k in flow for k in ('user_code','verification_uri','device_code')):
        raise ValueError('Owner sign-in initiation unavailable')
    print('SPECIALIST_OWNER_SIGNIN '+json.dumps(dict(verification_uri=flow['verification_uri'],
        user_code=flow['user_code'],expires_in=flow['expires_in'],application='WealthRyse Specialist Owner Client',
        scope='Portfolio.Observe')),flush=True)
    deadline=time.monotonic()+min(900,flow['expires_in'])
    result=client.acquire_token_by_device_flow(flow,exit_condition=lambda f:time.monotonic()>=deadline)
    if 'access_token' not in result:
        error=result.get('error','authentication_incomplete')
        if not isinstance(error,str) or not re.fullmatch('[A-Za-z0-9_]{1,80}',error): error='authentication_incomplete'
        print('SPECIALIST_OWNER_ACCEPTANCE '+json.dumps(dict(status='incomplete',code=error,token_retained=False)),flush=True)
        return
    token=result['access_token']
    user=EntraAccessTokens(TokenPolicy(ENTRA,AUDIENCE,frozenset({CLIENT}))).authenticate('Bearer '+token)
    response=requests.post(url+'/v1/observations',headers={'Authorization':'Bearer '+token},
        json=dict(tool_name='get_performance_series',arguments=dict(subject_id='U16088571')),timeout=60,allow_redirects=False)
    # Initial store is intentionally empty: authentication+current-user lookup
    # must pass but no account tool may run before audited authority enrollment.
    if args.read_trial:
        if response.status_code!=200:raise ValueError('Audited account read did not succeed')
        body=response.json();series=body.get('data',{}).get('series',[])
        if (body.get('subject_id')!='U16088571' or body.get('tool_name')!='get_performance_series'
            or body.get('quality_status')!='pass' or not series or body.get('data',{}).get('observations')!=len(series)
            or any(type(r.get('nav')) not in (int,float) or not math.isfinite(r['nav']) for r in series)):
            raise ValueError('Account-read result scope or quality differs')
        read_receipt=dict(status='passed',subject_id='U16088571',tool_name='get_performance_series',
            observations=len(series),response_sha256=hashlib.sha256(canonical_json(body).encode()).hexdigest(),
            financial_values_logged=False,independent_audit_readback_remaining=True,token_retained=False)
    elif response.status_code!=403 or response.json()!=dict(detail=dict(code='observation_denied')):
        raise ValueError('Expected unregistered observation denial was not established')
    parts=token.split('.')
    parts[2]=('a' if parts[2][0]!='a' else 'b')+parts[2][1:]
    tampered='.'.join(parts)
    invalid=requests.post(url+'/v1/observations',headers={'Authorization':'Bearer '+tampered},
        json=dict(tool_name='get_performance_series',arguments=dict(subject_id='U16088571')),timeout=30,allow_redirects=False)
    if invalid.status_code!=401: raise ValueError('Tampered signature was not rejected')
    receipt=dict(status='passed',real_entra_signature_verified=True,registered_client_verified=True,
        user_sha256=hashlib.sha256(user.object_id.encode()).hexdigest(),unregistered_observation_status=403,
        invalid_signature_status=401,financial_results_returned=0,token_retained=False,
        authority_enrollment_remaining=not args.read_trial)
    if args.read_trial:
        receipt={**receipt,**read_receipt,'unregistered_observation_status':None,'account_read_status':200,'financial_results_returned':1}
    name='owner-read-acceptance.json' if args.read_trial else 'owner-authentication-acceptance.json'
    (FOLDER/name).write_text(json.dumps(receipt,sort_keys=True,indent=2),encoding='utf-8')
    print('SPECIALIST_OWNER_ACCEPTANCE '+json.dumps(receipt),flush=True)
    result.clear();flow.clear();del token,tampered


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_OWNER_ACCEPTANCE '+json.dumps(dict(status='failed',error_type=type(error).__name__,token_retained=False)),flush=True)
        raise SystemExit(1)
