"""Read-only infrastructure options and public Azure retail prices. No secrets."""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import requests
from audit_azure_runtime import az, RG

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'.work/platform-upgrade-20261009-v1/evidence'
BASE = '/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/'+RG+'/providers/'
REGISTRIES = ('ibkrv2stgauhecwc4itaoo','ibkrv115rcc62xxqblay')

def registry(name):
    resource = BASE+'Microsoft.ContainerRegistry/registries/'+name
    return {'name':name,
        'configuration':az('acr','show','-g',RG,'-n',name,'--query','{sku:sku.name,location:location,publicNetworkAccess:publicNetworkAccess,privateEndpointCount:length(privateEndpointConnections)}'),
        'diagnostic_categories':az('monitor','diagnostic-settings','categories','list','--resource',resource),
        'diagnostic_settings':az('monitor','diagnostic-settings','list','--resource',resource)}

def prices(service):
    endpoint = 'https://prices.azure.com/api/retail/prices'
    parameters = {'$filter':"serviceName eq '"+service+"' and armRegionName eq 'eastasia' and priceType eq 'Consumption'"}
    result = []
    for page in range(10):
        response = requests.get(endpoint,params=parameters,timeout=30)
        response.raise_for_status()
        data=response.json()
        for row in data['Items']:
            result.append({key:row.get(key) for key in ('currencyCode','retailPrice','unitOfMeasure','skuName','meterName','productName','armRegionName','effectiveStartDate','isPrimaryMeterRegion')})
        endpoint=data.get('NextPageLink');parameters=None
        if not endpoint:return result
    raise ValueError('Retail price pagination unexpectedly large')

def main():
    if sys.argv[1:] == ['network-prices']:
        endpoint='https://prices.azure.com/api/retail/prices'
        response=requests.get(endpoint,params={'$filter':"serviceName eq 'Virtual Network' and armRegionName eq 'eastasia' and priceType eq 'Consumption'"},timeout=30)
        response.raise_for_status()
        data=response.json()
        if data.get('NextPageLink'):raise ValueError('Price listing needs pagination before quoting')
        target=OUT/'network-prices.json'
        with target.open('x',encoding='utf-8') as stream:json.dump({'source':endpoint,'captured_at':datetime.now(timezone.utc).isoformat(),'items':data['Items']},stream,indent=2)
        print(json.dumps([{k:row.get(k) for k in ('productName','skuName','meterName','retailPrice','unitOfMeasure','armRegionName')} for row in data['Items']],indent=2))
        return
    if sys.argv[1:] in (['private-link-prices'], ['private-link-global-prices']):
        endpoint='https://prices.azure.com/api/retail/prices'
        global_meter=sys.argv[1]=='private-link-global-prices'
        query="productName eq 'Azure Private Link'" + ('' if global_meter else " and armRegionName eq 'eastasia'")
        response=requests.get(endpoint,params={'$filter':query},timeout=30)
        response.raise_for_status()
        if response.json().get('NextPageLink'):raise ValueError('More price evidence required')
        items=response.json()['Items']
        record={'source':endpoint,'captured_at':datetime.now(timezone.utc).isoformat(),'items':items}
        target=OUT/('private-link-global-prices.json' if global_meter else 'private-link-prices.json')
        with target.open('x',encoding='utf-8') as stream:json.dump(record,stream,indent=2)
        print(json.dumps([{k:row.get(k) for k in ('serviceName','productName','meterName','retailPrice','unitOfMeasure','armRegionName')} for row in items],indent=2))
        return
    path=OUT/'infrastructure-options.json'
    if path.exists():raise ValueError('Preserve existing option evidence')
    with ThreadPoolExecutor(max_workers=4) as pool:
        registry_futures=list(pool.map(registry,REGISTRIES))
    record={'captured_at':datetime.now(timezone.utc).isoformat(),'read_only':True,
            'registries':registry_futures,'price_source':'https://prices.azure.com/api/retail/prices',
            'retail_prices':{service:prices(service) for service in ('Container Registry','Azure Private Link')},
            'currency':'USD','prices_are_estimates_not_subscription_invoice':True,
            'public_access_changed':False,'resources_created':False}
    path.write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record,indent=2))

if __name__=='__main__':main()
