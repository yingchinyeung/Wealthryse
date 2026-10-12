"""Create/verify isolated delegated observation registrations, without secrets.

No existing website registration, tenant consent policy or conditional-access
policy is changed. Unknown outcomes are recovered by exact named/tagged reads.
"""
import argparse
import json
import uuid
import requests
from azure.identity import AzureCliCredential
from audit_azure_runtime import az

TENANT='0578a50b-a7a1-40a4-905b-8b20dba84124'
TAG='wealthryse-specialist-observation-v1'
API='WealthRyse Specialist Observation API'
CLIENT='WealthRyse Specialist Owner Client'
SCOPE_ID=str(uuid.uuid5(uuid.NAMESPACE_URL,TAG+'/Portfolio.Observe'))
SCOPE=dict(id=SCOPE_ID,value='Portfolio.Observe',type='User',isEnabled=True,
    adminConsentDisplayName='Read authorized portfolio observations',
    adminConsentDescription='Read deterministic portfolio observations within current WealthRyse account entitlements.',
    userConsentDisplayName='Read your authorized portfolio observations',
    userConsentDescription='Read deterministic observations for accounts you can access in WealthRyse.')


def graph(method,path,data=None):
    token=AzureCliCredential(tenant_id=TENANT).get_token('https://graph.microsoft.com/.default').token
    response=requests.request(method,'https://graph.microsoft.com/v1.0'+path,
        headers={'Authorization':'Bearer '+token},json=data,timeout=30,allow_redirects=False)
    if not response.ok:
        raise RuntimeError(f'Gateway registration {method} failed HTTP {response.status_code}')
    return response.json() if response.content else {}


def find(name):
    result=graph('GET',"/applications?$filter=displayName eq '"+name+"'")
    if result.get('@odata.nextLink'): raise ValueError('Unbounded registration inventory')
    rows=result['value']
    if len(rows)>1: raise ValueError('Duplicate registration name; inspect before mutation')
    if rows and TAG not in rows[0].get('tags',[]):
        raise ValueError('Existing registration lacks reviewed purpose tag')
    return rows[0] if rows else None


def verify(api,client):
    if not api or not client: raise ValueError('Both dedicated registrations required')
    for app in (api,client):
        if (app['signInAudience']!='AzureADMyOrg' or app.get('passwordCredentials') or app.get('keyCredentials')
            or app.get('appRoles') or app.get('web',{}).get('redirectUris') or app.get('spa',{}).get('redirectUris')):
            raise ValueError('Registration boundary differs from reviewed definition')
    a=api.get('api',{})
    if (a.get('requestedAccessTokenVersion')!=2 or a.get('oauth2PermissionScopes')!=[SCOPE]
        or api.get('identifierUris')!=['api://'+api['appId']] or api.get('requiredResourceAccess')
        or a.get('preAuthorizedApplications') or a.get('knownClientApplications')
        or api.get('isFallbackPublicClient') not in (False,None)):
        raise ValueError('API audience or scope differs')
    expected=[{'resourceAppId':api['appId'],'resourceAccess':[{'id':SCOPE_ID,'type':'Scope'}]}]
    if (client.get('requiredResourceAccess')!=expected or client.get('isFallbackPublicClient') is not True
        or client.get('identifierUris') or client.get('publicClient',{}).get('redirectUris')
        or client.get('api',{}).get('oauth2PermissionScopes')):
        raise ValueError('Owner client permissions differ')
    return dict(entra_tenant_id=TENANT,api_object_id=api['id'],audience=api['appId'],
        client_object_id=client['id'],client_id=client['appId'],scope='api://'+api['appId']+'/Portfolio.Observe',
        delegated_only=True,credentials_created=0,tenant_policy_changed=False,consent_granted=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','verify']);args=parser.parse_args()
    if az('account','show','--query','tenantId')!=TENANT: raise ValueError('Unexpected Entra tenant')
    api=find(API);client=find(CLIENT)
    if args.stage=='prepare':
        if api is None:
            api=graph('POST','/applications',dict(displayName=API,signInAudience='AzureADMyOrg',tags=[TAG],
                api=dict(requestedAccessTokenVersion=2,oauth2PermissionScopes=[SCOPE])))
        # The GUID URI can only be configured after appId allocation. Do not
        # mutate a conflicting pre-existing URI or permission configuration.
        uri=['api://'+api['appId']]
        if api.get('identifierUris')==[]:
            if api.get('api',{}).get('oauth2PermissionScopes')!=[SCOPE]: raise ValueError('API definition differs')
            graph('PATCH','/applications/'+api['id'],{'identifierUris':uri})
        elif api.get('identifierUris')!=uri: raise ValueError('Unregistered API URI')
        if client is None:
            client=graph('POST','/applications',dict(displayName=CLIENT,signInAudience='AzureADMyOrg',tags=[TAG],
                isFallbackPublicClient=True,requiredResourceAccess=[{'resourceAppId':api['appId'],
                    'resourceAccess':[{'id':SCOPE_ID,'type':'Scope'}]}]))
    api=find(API);client=find(CLIENT);result=verify(api,client)
    principals=[]
    for app in (api,client):
        rows=graph('GET',"/servicePrincipals?$filter=appId eq '"+app['appId']+"'")
        if rows.get('@odata.nextLink') or len(rows['value'])>1: raise ValueError('Unexpected service principal inventory')
        if not rows['value'] and args.stage=='prepare':
            rows={'value':[graph('POST','/servicePrincipals',{'appId':app['appId']})]}
        if len(rows['value'])!=1 or rows['value'][0].get('appOwnerOrganizationId')!=TENANT:
            raise ValueError('Dedicated tenant service principal required')
        principal=rows['value'][0]
        if principal.get('accountEnabled') is not True: raise ValueError('Dedicated sign-in principal disabled')
        principals.append(principal['id'])
    print(json.dumps({**result,'service_principal_ids':principals},sort_keys=True))


if __name__=='__main__': main()
