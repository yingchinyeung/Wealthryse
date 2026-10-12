"""Read-only permissions, alert delivery and network metadata; never retrieve secrets."""
import concurrent.futures,json
from datetime import datetime,timezone
from collections import Counter
from audit_azure_runtime import az,RG
from audit_viewer_resource_group_20261008 import FOLDER

def main():
    sub='3956e821-cee4-403f-8708-e84e2ed490eb'
    if az('account','show','--query','id')!=sub:raise ValueError('Subscription scope differs')
    scope='/subscriptions/'+sub+'/resourceGroups/'+RG
    queries={
        'assignments':('role','assignment','list','--scope',scope,'--include-inherited','--all','--query','[].{principalId:principalId,principalType:principalType,role:roleDefinitionName,scope:scope}'),
        'action_groups':('monitor','action-group','list','-g',RG,'--query','[].{name:name,enabled:enabled,emailCount:length(emailReceivers),emails:emailReceivers[].{status:status,commonSchema:useCommonAlertSchema}}'),
        'diagnostic_settings':('monitor','diagnostic-settings','subscription','list','--query','value[].{name:name,logs:logs[].{category:category,enabled:enabled},workspaceId:workspaceId}'),
        'query_alerts':('monitor','scheduled-query','list','-g',RG,'--query','[].{name:name,enabled:enabled,frequency:evaluationFrequency,window:windowSize,actionGroups:actions.actionGroups,criteria:criteria.allOf[].{query:query,operator:operator,threshold:threshold}}'),
        'network_rules':('network','nsg','list','-g',RG,'--query','[].{name:name,rules:securityRules[].{name:name,access:access,direction:direction,priority:priority,source:sourceAddressPrefix,destinationPort:destinationPortRange}}'),
        'private_endpoints':('network','private-endpoint','list','-g',RG,'--query','[].{name:name,state:provisioningState,connections:privateLinkServiceConnections[].{state:privateLinkServiceConnectionState.status,target:privateLinkServiceId}}'),
    }
    def read(item):
        name,args=item
        try:return name,az(*args)
        except Exception as error:return name,{'error_type':type(error).__name__}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:result=dict(pool.map(read,queries.items()))
    result.update(captured_at=datetime.now(timezone.utc).isoformat(),mode='read_only_no_secrets')
    path=FOLDER/'security-metadata.json'
    if path.exists():raise ValueError('Preserve previous security evidence')
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    assignments=result['assignments']
    print(json.dumps({'roles':dict(Counter(r['role'] for r in assignments)) if isinstance(assignments,list) else assignments,'elevated_service_assignments':[{'role':r['role'],'scope':r['scope']} for r in assignments if r.get('principalType')=='ServicePrincipal' and r['role'] in ('Owner','Contributor','User Access Administrator')] if isinstance(assignments,list) else [],'action_groups':result['action_groups'],'private_endpoints':result['private_endpoints'],'read_failures':[k for k,v in result.items() if isinstance(v,dict) and 'error_type' in v]},indent=2))

if __name__=='__main__':main()
