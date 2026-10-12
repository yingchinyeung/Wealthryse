"""Normalize only the observed East Asia display name; preserve frozen v1 code."""
import copy,sys
import control_upgrade_private_canaries_20261011 as original

def same(actual,wanted):
    actual=copy.deepcopy(actual)
    if isinstance(actual,dict) and isinstance(wanted,dict) and wanted.get('location')=='eastasia':
        if actual.get('location') not in ('eastasia','East Asia'):raise ValueError('Canary left approved East Asia region')
        actual['location']='eastasia'
    if not original.dns.old.contains_requested(actual,wanted):raise ValueError('Temporary canary definition changed')

def main(*args):
    original.same=same
    original.main(*args)

if __name__=='__main__':main(*sys.argv[1:])
