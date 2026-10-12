"""Reuse frozen runtime safeguards with this display-only source boundary."""
import sys
import build_upgrade_cash_summary_20261010 as build
import control_upgrade_model_cash_runtime_20261010 as runtime
from prepare_upgrade_cash_summary_20261010 import BASE

runtime.b=build
runtime.OUT=build.OUT/'runtime-v1'
ORIGINAL_FILES=runtime.files

def files(mode):
    if mode=='fallback':raise ValueError('Use the separately protected exact installed-image fallback proof')
    return {'src/ibkr_etl/web.py':build.h.sha(build.OUT/'context/src/ibkr_etl/web.py'),
        'ALPHA_T_RUNTIME_RELEASE.json':build.h.sha(build.OUT/'context/ALPHA_T_RUNTIME_RELEASE.json'),
        'src/ibkr_etl/model_cash_display.py':build.h.sha(BASE/'context/src/ibkr_etl/model_cash_display.py')}

runtime.files=files

def main(stage,mode='candidate'):
    if mode not in ('candidate','installed'):raise ValueError('Only candidate or installed read-only probes')
    return runtime.main(stage,mode)

if __name__=='__main__':main(*sys.argv[1:])
