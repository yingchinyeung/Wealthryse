"""Read the frozen execution with exact multi-variable environment validation."""
import control_upgrade_g04_worker_20261010 as original
from control_upgrade_readers_20261010 import execution_env


def exact_environment(job,actual,wanted):
    left={e['name']:e for e in actual};right={e['name']:e for e in wanted}
    if len(left)!=len(actual) or len(right)!=len(wanted) or set(left)!=set(right):
        raise ValueError('Execution environment names differ')
    for name,b in right.items():
        a=left[name]
        if b.get('secretRef'):execution_env(job,[a],[b])
        elif a!=b:raise ValueError('Non-secret execution environment differs: '+name)


if __name__=='__main__':
    original.execution_env=exact_environment
    original.main('read')
