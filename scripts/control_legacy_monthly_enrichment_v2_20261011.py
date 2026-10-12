"""Private source-only full-NAV repair qualification; nothing deployed."""
import sys
from pathlib import Path
import control_legacy_monthly_enrichment_20261011 as c
c.OUT=c.OUT.parent/'legacy-monthly-plan-v2'
c.FILES.extend(['scripts/'+Path(__file__).name,'src/ibkr_etl/monthly_reporting_analytics.py'])
c.MODULES={'prepare_reporting_migration_clone_20261011':c.MODULES['prepare_reporting_migration_clone_20261011'],
           'ibkr_etl.monthly_reporting_analytics':'src/ibkr_etl/monthly_reporting_analytics.py',
           **{k:v for k,v in c.MODULES.items() if k!='prepare_reporting_migration_clone_20261011'}}
original=c.b.corrected_template
def template(job,code):
    anchor="m=types.ModuleType('ibkr_etl.monthly_reporting_analytics');sys.modules[m.__name__]=m\n"
    before="""from pathlib import Path
import base64,zlib,hashlib
from ibkr_etl.monthly_reporting_analytics import build_monthly_reporting_analytics as BASELINE_MONTHLY
installed_monthly=Path('/app/src/ibkr_etl/monthly_reporting_analytics.py').read_bytes()
print('MONTHLY_INSTALLED_SOURCE '+__import__('json').dumps({'sha256':hashlib.sha256(installed_monthly).hexdigest(),'zlib_base64':base64.b64encode(zlib.compress(installed_monthly)).decode()}),flush=True)
"""
    if code.count(anchor)!=1:raise ValueError('Exact candidate module anchor required')
    code=code.replace(anchor,before+anchor+"m.__package__='ibkr_etl'\n")
    old=(c.ROOT/'scripts/probe_legacy_monthly_enrichment_20261011.py').read_text(encoding='utf-8')
    new=old
    replacements={
        'tested.testsRun!=24':'tested.testsRun!=32',
        "            fresh=build_monthly_reporting_analytics(Adapter(),subject_id='U16088571',period_end=plan['period_end'],reporting_currency='USD')":"            baseline=BASELINE_MONTHLY(Adapter(),subject_id='U16088571',period_end=plan['period_end'],reporting_currency='USD')\n            if sha(baseline)!=plan['rebuilt_sha256']:raise ValueError('Original installed calculation changed')\n            fresh=build_monthly_reporting_analytics(Adapter(),subject_id='U16088571',period_end=plan['period_end'],reporting_currency='USD')",
        "            if sha(fresh)!=plan['rebuilt_sha256']:raise ValueError('Reconstruction differs from readiness proof')":"            permitted={'method_version','position_coverage'}\n            if plan['period_end']=='2025-01-31':permitted|={'period_start','daily_observations','portfolio_monthly_return','residual_contribution'}\n            if any(fresh['data'][k]!=v for k,v in baseline['data'].items() if k not in permitted):raise ValueError('Unrelated monthly calculation changed')",
        "'enrichment_committed':False":"'enrichment_committed':False,'source_overlay_only':True,'installed_monthly_sha256':hashlib.sha256(installed_monthly).hexdigest()",
    }
    for old_text,new_text in replacements.items():
        if new.count(old_text)!=1:raise ValueError('Exact full-NAV probe patch required')
        new=new.replace(old_text,new_text)
    if code.count(old)!=1:raise ValueError('Exact original probe required')
    # Test modules are the two explicit test modules, never the helper module.
    tests="TEST_MODULES="+repr(list(c.MODULES)[2:])
    if code.count(tests)!=1:raise ValueError('Exact native test list required')
    code=code.replace(tests,"TEST_MODULES=['test_legacy_monthly_reporting_20261011','test_monthly_reporting_analytics']")
    return original(job,code.replace(old,new))
c.b.corrected_template=template
if __name__=='__main__':
    if sys.argv[1:]!=['start']:raise ValueError('Use v2 reader for results')
    c.main('start')
