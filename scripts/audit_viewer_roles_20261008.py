"""Resource-group descendant and inherited role metadata, without changing grants."""
import concurrent.futures,json
from urllib.parse import urlsplit
from collections import Counter
from audit_azure_runtime import az,RG
from audit_viewer_resource_group_20261008 import FOLDER
from deploy_metadata_monitor import request

def main():
    sub='3956e821-cee4-403f-8708-e84e2ed490eb';scope='/subscriptions/'+sub+'/resourceGroups/'+RG
    target=scope+'/providers/Microsoft.Authorization/roleAssignments'
    path=target+'?api-version=2022-04-01';rows=[]
    for _ in range(20):
        data=request('GET',path);rows.extend(data.get('value',[]));link=data.get('nextLink')
        if not link:break
        url=urlsplit(link)
        if url.scheme!='https' or url.netloc!='management.azure.com' or url.path.casefold()!=target.casefold():raise ValueError('Role pagination escaped scope')
        path=url.path+'?'+url.query
    else:raise ValueError('Role inventory exceeds bounded pagination')
    inherited=az('role','assignment','list','--scope',scope,'--include-inherited','--query','[].{principalId:principalId,principalType:principalType,role:roleDefinitionName,scope:scope}')
    definitions={r['properties']['roleDefinitionId'] for r in rows}
    def definition(path):
        if not path.casefold().startswith('/subscriptions/'+sub+'/providers/microsoft.authorization/roledefinitions/'):raise ValueError('Role definition scope differs')
        return path,request('GET',path+'?api-version=2022-04-01')['properties']['roleName']
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:names=dict(pool.map(definition,definitions))
    normalized=[{'principalId':r['properties']['principalId'],'principalType':r['properties'].get('principalType'),'scope':r['properties']['scope'],'role':names[r['properties']['roleDefinitionId']]} for r in rows]
    record={'scope':scope,'resource_and_descendant_assignments':normalized,'inherited_assignments':inherited,'secret_values_read':0,'grant_changes':0}
    path=FOLDER/'security-roles-v2.json'
    if path.exists():raise ValueError('Preserve prior role inventory')
    path.write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps({'assignments':len(normalized),'roles':dict(Counter(r['role'] for r in normalized)),'elevated_service_assignments':[{'scope':r['scope'],'role':r['role']} for r in normalized+inherited if r.get('principalType')=='ServicePrincipal' and r['role'] in ('Owner','Contributor','User Access Administrator')],'grant_changes':0},indent=2))

if __name__=='__main__':main()
