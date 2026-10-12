"""Retain all migration gates; stabilize only the proven equal-value SQL tie."""
import sys
from pathlib import Path
import control_reporting_snapshot_migration_20261011 as c
from control_upgrade_legacy_recovery_probe_20261009 import protected
c.OUT=c.OUT.parent/'snapshot-migration-v2'
c.FILES.extend([Path(__file__).name,'legacy_reporting_query_order_20261011.py','test_legacy_reporting_query_order_20261011.py'])
original=c.b.corrected_template
def template(job,code):
    diagnosis=protected(c.OUT.parent/'snapshot-order-diagnostic-v1/result.json')['proof']
    if not diagnosis['equivalent_after_only_classification_order_and_reference_hash'] or [r['tie_order'] for r in diagnosis['variants'] if r['matches_plan']]!=['ASC']:raise ValueError('Exact ordering diagnosis required')
    addition=''
    for name in ('legacy_reporting_query_order_20261011','test_legacy_reporting_query_order_20261011'):
        addition+='m=types.ModuleType('+repr(name)+');sys.modules[m.__name__]=m\nexec('+repr((c.ROOT/'scripts'/(name+'.py')).read_text(encoding='utf-8'))+',m.__dict__)\n'
    addition+='from legacy_reporting_query_order_20261011 import stable_legacy_sector_query\nTEST_MODULES.append("test_legacy_reporting_query_order_20261011")\n'
    old=(c.ROOT/'scripts/rehearse_reporting_snapshot_migration_20261011.py').read_text(encoding='utf-8')
    new=old
    changes={"tests.testsRun!=46":"tests.testsRun!=52",
        "                return db.execute(query.replace('?','%s'),args)":"                return db.execute(stable_legacy_sector_query(query,args).replace('?','%s'),args)",
        "        for key,plan in expected.items():":"        for key,plan in expected.items():\n            STAGE='prepare:'+plan['subject']+':'+plan['period_end']"}
    for anchor,replacement in changes.items():
        if new.count(anchor)!=1:raise ValueError('Exact migration patch required')
        new=new.replace(anchor,replacement)
    if code.count(old)!=1:raise ValueError('Original private runner required')
    return original(job,code.replace(old,addition+new))
c.b.corrected_template=template
if __name__=='__main__':c.main(*sys.argv[1:])
