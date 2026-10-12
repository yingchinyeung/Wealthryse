"""Repeat read-only qualification with the native receipt test count corrected."""
import sys
import control_legacy_monthly_enrichment_v2_20261011 as v2
c=v2.c
c.OUT=c.OUT.parent/'legacy-monthly-plan-v3'
c.FILES.append('scripts/control_legacy_monthly_enrichment_v3_20261011.py')
previous=v2.original

def template(job,code):
    if code.count("'native_tests':24")!=1:raise ValueError('Exact final receipt count anchor required')
    return previous(job,code.replace("'native_tests':24","'native_tests':32"))
v2.original=template
if __name__=='__main__':
    if sys.argv[1:]!=['start']:raise ValueError('Start only')
    c.main('start')
