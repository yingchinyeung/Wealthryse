"""One display-only change, rebased on the exact installed model-cash website."""
import json,shutil
from pathlib import Path
import release_platform_access_correction_20261009 as h
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.work/platform-upgrade-20261010-stage26/model-cash-v3'
OUT=ROOT/'.work/platform-upgrade-20261010-stage30/cash-summary-v1'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:48faeaa7fb848a5de62924d77f50c836b7b88fc59fd0f908483f748e27e3244d'
OLD='''document.querySelector('#cash-balances').innerHTML=result.balances.map(x=>`<div class="metric"><div class="label">${{esc(x.currency)}} ending cash</div><div class="value">${{fmt(x.ending_cash_text)}} ${{esc(x.currency)}}</div><div class="muted">Settled: ${{fmt(x.settled_cash_text)}}</div></div>`).join('')||'<div class="empty">No cash balance snapshot is available.</div>';'''
NEW='''document.querySelector('#cash-balances').innerHTML=result.balances.map(x=>`<div class="metric"><div class="label">${{x.currency==='BASE_SUMMARY'?'Base-currency cash summary':esc(x.currency)+' ending cash'}}</div><div class="value">${{fmt(x.ending_cash_text)}}${{x.currency==='BASE_SUMMARY'?'':' '+esc(x.currency)}}</div><div class="muted">Settled: ${{fmt(x.settled_cash_text)}}${{x.currency==='BASE_SUMMARY'?'<br>Broker base-currency aggregate; currency code not supplied.':''}}</div></div>`).join('')||'<div class="empty">No cash balance snapshot is available.</div>';'''


def transform(source):
    if source.count(OLD)!=1:raise ValueError('Exact display boundary differs')
    changed=source.replace(OLD,NEW)
    compile(changed,'web.py','exec')
    if changed.replace(NEW,OLD)!=source:raise ValueError('Non-display source changed')
    return changed


def main():
    if OUT.exists():raise ValueError('Preserve prior candidate')
    live=request('GET',rid('Microsoft.App/containerApps','ibkrv2stg-web')+'?api-version=2026-07-01')
    if live['properties']['template']['containers'][0]['image']!=IMAGE:raise ValueError('Website drift')
    accepted=h.read(BASE/'native-build-qualified.json')['proof']['web_sha256']
    baseline=BASE/'context/src/ibkr_etl/web.py'
    if h.sha(baseline)!=accepted:raise ValueError('Installed website source differs')
    context=OUT/'context';(context/'src/ibkr_etl').mkdir(parents=True)
    changed=transform(baseline.read_text(encoding='utf-8'))
    (context/'src/ibkr_etl/web.py').write_text(changed,encoding='utf-8')
    shutil.copytree(BASE/'local-runtime',OUT/'local-runtime',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(context/'src/ibkr_etl/web.py',OUT/'local-runtime/src/ibkr_etl/web.py')
    shutil.copytree(BASE/'context/tests',context/'tests')
    release=h.read(BASE/'context/ALPHA_T_RUNTIME_RELEASE.json')
    release['base_image']=IMAGE
    release['installed_files_sha256']['src/ibkr_etl/web.py']=h.sha(context/'src/ibkr_etl/web.py')
    release['scope']='Physical cash BASE_SUMMARY presentation only; preserve all raw records, amounts, currency metadata, models, access and reports'
    h.save(context/'ALPHA_T_RUNTIME_RELEASE.json',release)
    h.save(OUT/'prepared.json',{'before_image':IMAGE,'before_web_sha256':accepted,
        'settings_invariant_sha256':settings_invariant(live),'files_sha256':h.manifest(context),
        'scope':release['scope'],'azure_deployed':False})
    print('Prepared display-only cash summary candidate; not deployed')


if __name__=='__main__':main()
