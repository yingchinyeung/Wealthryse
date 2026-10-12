"""Prepare only an isolated website patch from the exact installed web baseline."""
import hashlib,json,shutil,sys
from pathlib import Path
import release_platform_access_correction_20261009 as helper
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant

ROOT=Path(__file__).resolve().parents[1];REVISION=3 if '--v3' in sys.argv else 2 if '--v2' in sys.argv else 1;OUT=ROOT/('.work/platform-upgrade-20261010-stage26/model-cash-v'+str(REVISION))
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:072e38f261fc062ad51aee46f05219d7e601caa1b48fd9d888c34817fde86688'
WEB_SHA='7b478e52d664486e097455956cde4a38e42775b9569b6ec519cb6c2551333f14'


def prepare():
    if OUT.exists():raise ValueError('Preserve existing UI candidate')
    live=request('GET',rid('Microsoft.App/containerApps','ibkrv2stg-web')+'?api-version=2026-07-01')
    if live['properties']['template']['containers'][0]['image']!=IMAGE:raise ValueError('Website drift; rebase required')
    baseline=ROOT/'.work/platform-upgrade-20261009-stage7/runtime/src/ibkr_etl/web.py'
    if helper.sha(baseline)!=WEB_SHA:raise ValueError('Frozen website source differs')
    text=baseline.read_text(encoding='utf-8')
    old='from .subject_read_contract import require_ms_hkd_contract, synthetic_exposure'
    if text.count(old)!=1:raise ValueError('Import boundary changed')
    text=text.replace(old,old+'\nfrom .model_cash_display import load_model_cash')
    old='''        balances_date = connection.execute(
            "SELECT MAX(snapshot_date) AS latest FROM subject_cash_balance_series'''
    new='''        if account_id in ('alpha-world-ms','alpha-world-t'):
            if access.get('subject_type')!='composite_portfolio':
                raise HTTPException(status_code=409,detail='Model cash requires approved composite metadata')
            try:
                model=load_model_cash(connection,tenant_id=TENANT_ID,subject_id=account_id,
                    reporting_currency=access['reporting_currency'],as_of=effective_as_of)
            except (ValueError,TypeError,KeyError) as error:
                raise HTTPException(status_code=409,detail='Model cash is withheld because its approved inputs do not reconcile') from error
            return {'account_id':account_id,'as_of':effective_as_of,'balances_date':model['as_of'] if model else None,
                'balances':[],'activity_from':activity_from,'activity_to':activity_to,'total_count':0,'transactions':[],
                'viewer':user['email'],'model_cash':model,'activity_applicable':False,
                'message':model['message'] if model else 'No validated model cash observation is available on or before this date.'}
        balances_date = connection.execute(
            "SELECT MAX(snapshot_date) AS latest FROM subject_cash_balance_series'''
    if text.count(old)!=1:raise ValueError('Cash endpoint boundary changed')
    text=text.replace(old,new)
    old='const loadCashActivity=async()=>{{\n'
    new=old+"document.querySelector('#cash-balances').innerHTML='';document.querySelector('#cash-asof').textContent='Loading…';document.querySelector('#cash-activity').innerHTML='';document.querySelector('#cash-export').hidden=true;\n"
    if text.count(old)!=1:raise ValueError('Cash view boundary changed')
    text=text.replace(old,new)
    old="document.querySelector('#cash-asof').textContent=result.balances_date||'No snapshot';"
    new=old+'''
document.querySelector('#cash-export').hidden=result.activity_applicable===false;
if(result.activity_applicable===false){{
const model=result.model_cash;
document.querySelector('#cash-balances').innerHTML=model?`<div class="metric"><div class="label">Model cash (normalized NAV units)</div><div class="value">${{fmt(model.normalized_value)}}</div><div class="muted">${{esc(model.currency_context)}} reporting context; not a currency cash balance</div></div><div class="metric"><div class="label">Share of current model NAV</div><div class="value">${{pct(model.nav_weight)}}</div></div>`:'<div class="empty">Validated model cash is unavailable.</div>';
document.querySelector('#cash-activity').innerHTML='<tr><td colspan="6">Not applicable: this model has no independently executed cash transactions.</td></tr>';
document.querySelector('#cash-note').textContent=result.message;return;
}}'''
    if text.count(old)!=1:raise ValueError('Cash rendering boundary changed')
    text=text.replace(old,new)
    text=text.replace('Unable to load cash activity for this date range. Check that the start date is not after the end date.','Cash data is unavailable or its inputs did not pass validation. Check the selected dates or contact the platform owner.')
    if REVISION>=2:
        substitutions=[
            ('Cash balances as of <span id="cash-asof">','<span id="cash-kind">Cash balances</span> as of <span id="cash-asof">'),
            ('<div class="label" style="margin-top:18px">Cash activity history</div>','<div id="cash-ledger-controls"><div class="label" style="margin-top:18px">Cash activity history</div>'),
            ('</a></div><div id="cash-note"','</a></div></div><div id="cash-note"'),
            ('<div class="table-scroll"><table><thead><tr><th>Date</th><th>Time</th><th>Type</th><th>Description</th><th>Currency</th><th>Amount</th>','<div id="cash-ledger-table" class="table-scroll"><table><thead><tr><th>Date</th><th>Time</th><th>Type</th><th>Description</th><th>Currency</th><th>Amount</th>'),
            ("document.querySelector('#cash-export').hidden=result.activity_applicable===false;", "document.querySelector('#cash-export').hidden=result.activity_applicable===false;document.querySelector('#cash-ledger-controls').hidden=result.activity_applicable===false;document.querySelector('#cash-ledger-table').hidden=result.activity_applicable===false;document.querySelector('#cash-kind').textContent=result.activity_applicable===false?'Model cash':'Cash balances';")]
        for old,new in substitutions:
            if text.count(old)!=1:raise ValueError('Model/broker UI boundary changed: '+old[:50])
            text=text.replace(old,new)
    compile(text,'web.py','exec')
    context=OUT/'context';(context/'src/ibkr_etl').mkdir(parents=True);(context/'tests').mkdir()
    (context/'src/ibkr_etl/web.py').write_text(text,encoding='utf-8')
    shutil.copyfile(ROOT/'src/ibkr_etl/model_cash_display.py',context/'src/ibkr_etl/model_cash_display.py')
    for name in ('test_model_cash_display.py','test_model_cash_web.py'):shutil.copyfile(ROOT/'tests'/name,context/'tests'/name)
    manifest=json.loads((ROOT/'.work/platform-upgrade-20261009-stage7/app-release-v1/context/ALPHA_T_RUNTIME_RELEASE.json').read_bytes())
    manifest['base_image']=IMAGE
    for path in ('src/ibkr_etl/web.py','src/ibkr_etl/model_cash_display.py'):manifest['installed_files_sha256'][path]=helper.sha(context/path)
    helper.save(context/'ALPHA_T_RUNTIME_RELEASE.json',manifest)
    local=OUT/'local-runtime';shutil.copytree(baseline.parents[1],local/'src',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for path in ('src/ibkr_etl/web.py','src/ibkr_etl/model_cash_display.py'):shutil.copyfile(context/path,local/path)
    helper.save(OUT/'prepared.json',{'before_image':IMAGE,'before_web_sha256':WEB_SHA,'settings_invariant_sha256':settings_invariant(live),
        'files_sha256':helper.manifest(context),'scope':'Read-only model cash display only. No NAV, contract, report, approval, authentication or entitlement changes.',
        'azure_deployed':False,'native_qualification_complete':False})
    print('Prepared isolated model-cash website patch; not deployed')


if __name__=='__main__':prepare()
