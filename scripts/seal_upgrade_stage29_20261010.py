"""Seal isolated reporting evidence; do not mark the broader gate complete."""
import hashlib,json,os,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage29'

def main():
    previous=protected(ROOT/'.work/platform-upgrade-20261010-stage28/closeout.json')
    evidence={name:protected(OUT/path) for name,path in {
        'database':'g04-database-v1/result.json',
        'concurrency':'worker-preflight-v2/result.json',
        'fresh_artifacts':'fresh-report-v1/result.json',
        'integrity':'post-trial-integrity-v1/result.json',
        'owner_qa_reader':'qa-reader-v1/verified.json',
        'downloads':'artifact-review-v1/downloads.json'}.items()}
    if any(v.get('passed') is not True for v in evidence.values()):raise ValueError('Incomplete evidence')
    for artifact in evidence['downloads']['artifacts']:
        if hashlib.sha256(Path(artifact['file']).read_bytes()).hexdigest()!=artifact['sha256']:raise ValueError('Reviewed artifact changed')
    integrity=evidence['integrity']['proof'];fresh=evidence['fresh_artifacts']['proof']
    if integrity['tables_checked']!=97 or not integrity['source_unchanged'] or not integrity['qa_financial_data_unchanged'] or not integrity['qa_snapshots_and_approvals_unchanged']:raise ValueError('Integrity boundary differs')
    if not fresh['injected_upload_failure_rolled_back'] or not fresh['idempotent_replay_passed'] or fresh['fresh_snapshot_creation']:raise ValueError('Incorrect reporting scope')
    run=subprocess.run([sys.executable,'-m','unittest','scripts/test_upgrade_g04_isolation_20261010.py','-q'],cwd=ROOT,env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(ROOT/'src',ROOT/'scripts')))},capture_output=True,text=True,timeout=60)
    if run.returncode or 'Ran 10 tests' not in run.stderr:raise ValueError('Isolation safeguards failed')
    value={'passed':True,'sealed_at':datetime.now(timezone.utc).isoformat(),
        'previous_checkpoint':previous['protected_receipt'],
        'evidence':{k:v['protected_receipt'] for k,v in evidence.items()},
        'isolation_tests':10,'source_tables_unchanged':97,
        'fresh_artifact_generation':True,'upload_failure_recovery':True,'concurrency_and_idempotency':True,
        'fresh_snapshot_creation':False,'governed_regeneration':False,'all_subject_coverage':False,
        'visual_review':{'factsheet_pages':[1,2],'presentation_slides':list(range(1,21)),
            'factsheet_renderer':'Poppler','presentation_renderer':'installed Microsoft PowerPoint slideshow',
            'visible_clipping_or_overlap_observed':False,'repair_prompt_observed':False,
            'qa_deck_closed_without_saving':True,'artifact_hashes_unchanged':True,
            'scope':'Visual QA only; not a new independent numerical audit, legal approval or report approval'},
        'owner_access_change':'Approved Storage Blob Data Reader at QA container scope only',
        'production_reports_changed':False,'production_database_connected':False,
        'viewer_permissions_changed':False,'g04_complete':False,
        'cash_summary_correction':'Local only; four patch tests and nine synthetic responsive cases passed; no deployment',
        'remaining_gates':[
            'Final private-network cutover with all-caller, platform-secret, cold-pull, rollback and external-negative checks',
            'Credential ownership, grouped rotation and provider expiry review',
            'Fresh snapshot creation, governed regeneration and all-subject reporting coverage',
            'Physical cash-summary candidate qualification and deployment; remaining website acceptance',
            'Genuine whole scheduled cycle and genuine 14-day observation'],
        'observation_14_day':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED',
        'whole_scheduled_cycle':'UNVERIFIED','human_alert_receipt':'UNVERIFIED','full_upgrade_complete':False}
    save(OUT,'closeout',value)
    print(json.dumps({'checkpoint_passed':True,'pdf_pages_reviewed':2,'native_ppt_slides_reviewed':20,'g04_complete':False,'full_upgrade_complete':False}))

if __name__=='__main__':main()
