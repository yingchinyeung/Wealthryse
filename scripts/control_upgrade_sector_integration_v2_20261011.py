"""Preserve v1's preflight failure; new helper has no installed predecessor."""
import sys
from pathlib import Path
import control_upgrade_sector_integration_20261011 as base
from control_upgrade_legacy_recovery_probe_20261009 import protected
old=protected(base.OUT/'failure.json')
if old['proof']!={'type':'FileNotFoundError','message':"[Errno 2] No such file or directory: '/app/src/ibkr_etl/reporting_currency_holdings.py'",'reconcile_before_retry':True}:raise ValueError('Unexpected prior failure')
base.OUT=base.ROOT/'.work/platform-upgrade-20261011-stage34/sector-integration-v2'
base.SOURCE_FILES.remove('src/ibkr_etl/reporting_currency_holdings.py')
base.FILES.append('scripts/'+Path(__file__).name)
original_save=base.save
def save(folder,name,value):
    if name=='intent':value={**value,'previous_preflight_failure':old['protected_receipt'],'scope':value['scope'].replace('nine allowlisted','eight allowlisted')}
    return original_save(folder,name,value)
base.save=save
if __name__=='__main__':base.main(*sys.argv[1:])
