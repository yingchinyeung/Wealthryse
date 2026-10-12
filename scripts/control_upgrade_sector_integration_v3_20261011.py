"""Use explicit source ordering after reconciling v2's packaging-only failure."""
import json,sys
from pathlib import Path
import control_upgrade_sector_integration_20261011 as base
from control_upgrade_legacy_recovery_probe_20261009 import protected
previous=base.ROOT/'.work/platform-upgrade-20261011-stage34/sector-integration-v2'
base.OUT=base.ROOT/'.work/platform-upgrade-20261011-stage34/sector-integration-v3'
base.SOURCE_FILES.remove('src/ibkr_etl/reporting_currency_holdings.py')
# The base controller's final two entries are the probe and DB-boundary helper.
# Prepend the controller audit entry without changing those positions.
base.FILES.insert(0,'scripts/'+Path(__file__).name)
original_save=base.save
def save(folder,name,value):
    if name=='intent':value={**value,'previous_preflight_failure':protected(previous/'failure.json')['protected_receipt'],'scope':value['scope'].replace('nine allowlisted','eight allowlisted')}
    return original_save(folder,name,value)
base.save=save
def reconcile():
    if (previous/'failure.json').exists():return
    intent=protected(previous/'intent.json');name=protected(previous/'dispatch.json')['execution']
    run=base.b.request('GET',base.a.PATH+'/executions/'+name+base.b.API)
    if run['properties']['status']!='Failed' or run['properties']['template']['containers'][0]['args']!=intent['template']['containers'][0]['args']:raise ValueError('Expected exact failed packaging attempt')
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith 'ModuleNotFoundError: No module named' | project Log_s"
    rows=base.b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if [r['Log_s'] for r in rows]!=["ModuleNotFoundError: No module named 'control_upgrade_sector_integration_20261011'"]:raise ValueError('Unexpected failure')
    original_save(previous,'failure',{'passed':False,'execution':name,'proof':{'type':'ModuleNotFoundError','missing_module':'control_upgrade_sector_integration_20261011','stage':'source envelope before database connection','database_writes':0,'cause':'append changed positional file references'}})
if __name__=='__main__':
    if sys.argv[1]=='start':reconcile()
    base.main(*sys.argv[1:])
