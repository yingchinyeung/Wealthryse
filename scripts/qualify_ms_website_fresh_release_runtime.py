"""Reuse the qualified native runner on fresh inputs with temporary mapping rollback."""
import hashlib
import json
from pathlib import Path

import qualify_ms_website_native_runtime as runtime

p=runtime.probe
FOLDER=p.ROOT/'.work/alpha-ms-website-release-20261008-v1'
STAGE=FOLDER/'fresh-native-runtime-v1'
SOURCE=FOLDER/'fresh-native-source-v1'
PREFIX='review/alpha-world-ms/website-release-20261008-v1/fresh-native/'
ROLLBACK_CHECK='''        # Rehearse coordinated selection rollback without deleting v2 history.
        current_mapping=[dict(r) for r in raw.execute("SELECT * FROM pg_temp.portfolio_benchmark_mapping ORDER BY benchmark_id,effective_from")]
        current_comparisons=[dict(r) for r in raw.execute("SELECT * FROM pg_temp.performance_comparison_daily ORDER BY benchmark_id,observation_date,calculation_version")]
        raw.execute('SAVEPOINT ms_mapping_rollback')
        raw.execute("DELETE FROM pg_temp.portfolio_benchmark_mapping WHERE tenant_id=%s AND subject_id=%s",(TENANT,SUBJECT))
        raw.execute("INSERT INTO pg_temp.portfolio_benchmark_mapping SELECT * FROM public.portfolio_benchmark_mapping WHERE tenant_id=%s AND subject_id=%s",(TENANT,SUBJECT))
        restored_mapping=[dict(r) for r in raw.execute("SELECT * FROM pg_temp.portfolio_benchmark_mapping ORDER BY benchmark_id,effective_from")]
        original_mapping=[dict(r) for r in raw.execute("SELECT * FROM public.portfolio_benchmark_mapping WHERE tenant_id=%s AND subject_id=%s ORDER BY benchmark_id,effective_from",(TENANT,SUBJECT))]
        if restored_mapping!=original_mapping:raise ValueError('Mapping rollback differs from original selection')
        if any(r['reporting_currency']!='USD' for r in restored_mapping if r['effective_to'] is None):
            raise ValueError('Original USD selection not restored')
        if [dict(r) for r in raw.execute("SELECT * FROM pg_temp.performance_comparison_daily ORDER BY benchmark_id,observation_date,calculation_version")]!=current_comparisons:
            raise ValueError('Mapping rollback lost comparison history')
        raw.execute('ROLLBACK TO SAVEPOINT ms_mapping_rollback')
        raw.execute('RELEASE SAVEPOINT ms_mapping_rollback')
        if [dict(r) for r in raw.execute("SELECT * FROM pg_temp.portfolio_benchmark_mapping ORDER BY benchmark_id,effective_from")]!=current_mapping:
            raise ValueError('Candidate mapping selection not recoverable')
        if not web._onboarding_preview_payload(raw,SUBJECT)['validation']['ready_for_owner_preview_approval']:
            raise ValueError('Candidate preview failed after rollback rehearsal')
'''


def configure():
    fresh=p.release.read(FOLDER/'fresh-inputs-v1','qualified');p.verify_protected(fresh)
    if p.sha(FOLDER/'fresh-inputs-v1/inputs.json')!=fresh['inputs_sha256']:
        raise ValueError('Fresh input bytes differ')
    if not SOURCE.exists():
        original=runtime.candidate.rehearsal.STAGE
        binding=p.release.read(original,'binding')
        runner=(original/'frozen-runner.py').read_text(encoding='utf-8')
        if hashlib.sha256(runner.encode()).hexdigest()!=binding['runner_sha256']:
            raise ValueError('Original frozen runner changed')
        anchor='        raw.rollback()\n        after=fingerprint()'
        if runner.count(anchor)!=1:raise ValueError('Rollback insertion anchor differs')
        runner=runner.replace(anchor,ROLLBACK_CHECK+anchor)
        anchor="        'secondary_failure_rollback_verified':True,"
        if runner.count(anchor)!=1:raise ValueError('Proof insertion anchor differs')
        runner=runner.replace(anchor,anchor+"\n        'mapping_selection_rollback_verified':True,\n        'comparison_rows_preserved_on_mapping_rollback':True,")
        binding.update(inputs_sha256=fresh['inputs_sha256'],input_blob=fresh['input_blob'],
            runner_sha256=hashlib.sha256(runner.encode()).hexdigest(),
            fresh_controller_sha256=p.sha(Path(__file__)),
            native_controller_sha256=p.sha(Path(runtime.__file__)))
        SOURCE.mkdir()
        p.release.build.save(SOURCE,'binding',binding)
        with (SOURCE/'frozen-runner.py').open('x',encoding='utf-8',newline='\n') as f:f.write(runner)
    binding=p.release.read(SOURCE,'binding')
    if (binding['fresh_controller_sha256']!=p.sha(Path(__file__))
        or binding['native_controller_sha256']!=p.sha(Path(runtime.__file__))
        or binding['inputs_sha256']!=fresh['inputs_sha256']):
        raise ValueError('Frozen fresh runtime controller changed')
    runtime.STAGE=STAGE
    runtime.candidate.rehearsal.STAGE=SOURCE
    p.PREFIX=PREFIX


if __name__=='__main__':
    configure()
    runtime.main()
    if (STAGE/'qualified.json').exists():
        proof=p.release.read(STAGE,'proof')
        if not proof.get('mapping_selection_rollback_verified') or not proof.get('comparison_rows_preserved_on_mapping_rollback'):
            raise ValueError('Mapping rollback qualification missing')
