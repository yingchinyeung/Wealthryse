"""Focused regression and native-review evidence; not a deployment certificate."""
import hashlib,json,os,re,subprocess,sys
from pathlib import Path
from pypdf import PdfReader
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage34/qualification-v1'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    if OUT.exists():raise ValueError('Preserve earlier qualification')
    OUT.mkdir()
    env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts','TEMP':str(ROOT/'.work/platform-upgrade-20261011-stage33/test-temp'),'TMP':str(ROOT/'.work/platform-upgrade-20261011-stage33/test-temp')}
    modules=['tests.test_reporting_currency_holdings','tests.test_reporting_snapshot','tests.test_presentation_design','tests.test_ms_presentation_model','tests.test_alpha_world_t_presentation_model','scripts.test_upgrade_all_reports_v2_20261011','scripts.test_upgrade_input_lineage_20261011','scripts.test_upgrade_g04_isolation_20261010','tests.test_agent_tools','tests.test_agent_sector_currency','tests.test_monthly_artifacts','tests.test_reporting_currency_regions']
    result=subprocess.run([sys.executable,'-m','unittest',*modules,'-v'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    (OUT/'python-tests.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode or re.findall(r'Ran (\d+) tests',result.stderr)!=['107']:raise ValueError('Python regression failure: '+result.stderr[-1500:])
    node='C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    result=subprocess.run([node,'--test','--test-reporter=tap','scripts/test_report_content_labels.mjs','scripts/test_report_summary_label.mjs','scripts/test_t_report_imports.mjs'],cwd=ROOT,capture_output=True,text=True,timeout=60)
    (OUT/'node-tests.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode or not re.search(r'# pass 13\b',result.stdout) or not re.search(r'# fail 0\b',result.stdout):raise ValueError('Node regression failure')
    reviewed=protected(ROOT/'.work/platform-upgrade-20261011-stage33/artifact-review-v2/downloads.json')
    native={}
    for subject,prefix in [('U16088571','U571'),('U16088606','U606')]:
        row=next(r for r in reviewed['artifacts'] if r['subject']==subject and r['report_type']=='presentation')
        if digest(Path(row['file']))!=row['sha256']:raise ValueError('Original deck changed')
        pdf=OUT.parent/(subject+'_QA_native.pdf')
        if len(PdfReader(pdf).pages)!=20:raise ValueError('Incomplete native export')
        pages=[OUT.parent/(prefix+'-native-'+str(i).zfill(2)+'.png') for i in range(1,21)]
        native[subject]={'deck_sha256':row['sha256'],'native_pdf_sha256':digest(pdf),'native_pdf':str(pdf),'slides_visually_inspected':20,'page_image_sha256':{p.name:digest(p) for p in pages},'result':'No clipping or layout defects observed; corrected benchmark, currency, cash and conclusion panels readable. This is not independent historical-provider certification.'}
    sources=['src/ibkr_etl/'+n+'.py' for n in ('agent_tools','reporting_currency_holdings','reporting_snapshot','monthly_artifacts','presentation_design')]+['scripts/'+n for n in ('render_monthly_presentation.mjs','render_alpha_t_private_presentation.mjs','alpha_t_presentation_runtime.mjs','report_content_labels.mjs','report_summary_label.mjs')]
    save(OUT,'result',{'passed':True,'python_tests':107,'node_tests':13,'logs':{n:digest(OUT/n) for n in ('python-tests.log','node-tests.log')},'native_review':native,'sources':{n:digest(ROOT/n) for n in sources},'candidate_deployed':False,'full_upgrade_complete':False,'real_viewer_sign_in':'UNVERIFIED','observation_14_day':'UNVERIFIED','broad_agent_discovery':'Interrupted after no further progress; not counted as passed'})
    print(json.dumps({'passed':True,'tests':120,'native_slides_inspected':40,'deployed':False}))
if __name__=='__main__':main()
