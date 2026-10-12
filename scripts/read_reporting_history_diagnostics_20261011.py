"""Inspect aggregate-only legacy metadata after the protected private trial."""
import json
import read_reporting_history_inventory_20261011 as c
c.OUT=c.OUT.parent/'historical-diagnostic-read-v1'
c.TRIAL=c.OUT.parent/'historical-diagnostics-v1/result.json'
c.BLOB='candidate-stage34-historical-diagnostics-v1/inventory.json'
if __name__=='__main__':
    c.main()
    value=json.loads((c.OUT/'inventory.json').read_bytes())
    blocked=[r for r in value['rows'] if r.get('blocker')]
    print(json.dumps([{'period':r['period_end'],'shape':r.get('legacy_shape')} for r in blocked]))
