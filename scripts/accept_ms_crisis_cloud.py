"""Independent local acceptance of the exact privately rendered Azure files."""
import hashlib,json,subprocess,zipfile
from pathlib import Path
from PIL import Image,ImageChops
from ibkr_etl.alpha_world_ms_analytics import analyze
from ibkr_etl.alpha_world_ms_crisis import validate
from ibkr_etl.reporting_snapshot import assemble_month_end_payload
from validate_subject_presentation import validate_subject_presentation
from ibkr_etl.alpha_world_ms_pdf_validation import validate as validate_pdf
import release_ms_crisis as candidate
import build_ms_presentation_worker_candidate as build
from run_specialist_gateway_read_verification import retain
ROOT=candidate.ROOT;FOLDER=candidate.FOLDER.parent/'candidate-v6'
POPPLER=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def visuals(path):
    # Every drawing, chart, font/theme/master, relationship and media part is
    # included. Only document-level metadata and editable Excel packages are
    # excluded; embedded workbooks pass their separate six-chart audit.
    with zipfile.ZipFile(path) as z:
        return {n:hashlib.sha256(z.read(n)).hexdigest() for n in sorted(z.namelist())
            if n.startswith('ppt/') and not n.startswith('ppt/embeddings/')}
def main():
    target=FOLDER/'cloud-acceptance.json'
    if target.exists():raise ValueError('Frozen acceptance exists')
    qa=FOLDER/'cloud-offline-qa-v1';qa.mkdir()
    original=json.loads((candidate.FOLDER.parent/'preflight-v2/inputs.json').read_bytes())
    old={r['period_end']:json.loads(r['snapshot_payload_json']) for r in original['snapshots']}
    manual=json.loads((candidate.FOLDER.parent/'local-visual-acceptance.json').read_bytes());assert manual['status']=='passed'
    rows=[];per_kind={}
    for kind in candidate.IMAGES:
        folder=FOLDER/kind/'candidate-runtime-v1';proof=json.loads((folder/'verification.json').read_bytes())
        assert proof['status']=='passed' and proof['count']==21 and proof['database_writes']==0
        results=[]
        for row in proof['artifacts']:
            period=row['period_end'];outputs=folder/'outputs';source=outputs/(period+'.json');p=json.loads(source.read_bytes())
            assert sha(source)==row['data_sha256'] and analyze(old[period])==analyze(p)
            validate(p['risk']['crisis_replay'],as_of=p['data_as_of'],holdings=p['holdings']['holdings'],nav=p['portfolio_summary']['latest_nav']['total_text'])
            expected=json.loads(json.dumps(old[period]));expected['risk']['crisis_replay']=p['risk']['crisis_replay'];expected['publication_blockers']=[b for b in expected['publication_blockers'] if b!='risk:crisis_replay_unavailable'];expected['quality_status']='warn';expected['snapshot_version']=p['snapshot_version']
            assert expected==p,'Non-crisis frozen input changed'
            mappings={'summary':'portfolio_summary','performance':'performance','holdings':'holdings','risk':'risk',
                'freshness':'freshness','sector_allocation':'sector_allocation','primary_benchmark':'performance_comparison',
                'secondary_benchmark':'secondary_performance_comparison','monthly_reporting':'monthly_reporting'}
            tools={name:{'as_of':p['data_as_of'],'quality_status':'warn','data':p[field],
                         'citations':p['source_citations'].get(name,[])} for name,field in mappings.items()}
            assembled,quality,public,blockers=assemble_month_end_payload(subject_id='alpha-world-ms',period_start=p['period_start'],
                period_end=period,reporting_currency='HKD',tool_results=tools)
            assert quality=='warn' and not public and 'risk:crisis_replay_unavailable' not in blockers
            assert analyze(assembled)==analyze(p) and assembled['risk']['crisis_replay']==p['risk']['crisis_replay']
            pdf=outputs/Path(row['factsheet_blob']).name;ppt=outputs/Path(row['presentation_blob']).name
            assert sha(pdf)==row['factsheet_sha256'] and sha(ppt)==row['presentation_sha256']
            validate_pdf(pdf,p);audit=validate_subject_presentation(ppt,source,subject_id='alpha-world-ms',period_end=period,expected_strategy='iStrategy: Alpha World MS')
            assert audit['ms_financial_audit']['qualified_crisis_verified'] and audit['ms_chart_workbook_audit']['embedded_workbooks_verified']==6
            preview=qa/kind/period;preview.mkdir(parents=True)
            subprocess.run([str(POPPLER),'-r','100','-png',str(pdf),str(preview/'cloud')],check=True,capture_output=True)
            for page in (1,2):
                local=candidate.FOLDER.parent/'pdf-previews-v1'/period/f'after-{page}.png'
                assert ImageChops.difference(Image.open(local).convert('RGB'),Image.open(preview/f'cloud-{page}.png').convert('RGB')).getbbox() is None,'Factsheet pixels changed'
            local=ROOT/'output/pptx/AlphaWorld_MS_Crisis_Review_v1'/('AlphaWorld_MS_Presentation_'+period+'.pptx')
            native_parts=visuals(ppt);local_parts=visuals(local)
            difference=[n for n in set(native_parts)|set(local_parts) if native_parts.get(n)!=local_parts.get(n)]
            result={'kind':kind,'period_end':period,'factsheet_sha256':row['factsheet_sha256'],'presentation_sha256':row['presentation_sha256'],
                'data_sha256':row['data_sha256'],'financial_metrics_unchanged':True,'pdf_pages_pixel_identical':2,
                'presentation_visual_parts_identical_to_inspected_deck':not difference,'differing_visual_parts':sorted(difference),'presentation_audit':audit}
            results.append(result);print(json.dumps({'accepted_cloud':kind,'period_end':period,'differing_visual_parts':difference}),flush=True)
        per_kind[kind]=results;rows+=results
    # A differing drawing part cannot silently inherit a prior visual acceptance.
    if any(r['differing_visual_parts'] for r in rows):
        with (qa/'pending-visual.json').open('x',encoding='utf-8') as f:json.dump({'status':'pending_native_visual_review','rows':rows},f,indent=2)
        raise ValueError('Native drawing differs from locally inspected presentation; render/inspect before sealing')
    assert all(a['data_sha256']==b['data_sha256'] for a,b in zip(per_kind['worker'],per_kind['web']))
    record={'status':'passed','months':21,'financial_metrics_unchanged':True,'source_data_changed':False,'approvals_changed':False,'reports_released':False,
        'native_pdf_pages_pixel_verified':84,'native_presentation_slides_bound_to_manual_review':840,
        'local_visual_acceptance_sha256':sha(candidate.FOLDER.parent/'local-visual-acceptance.json'),'rows':rows}
    build.save(FOLDER,'cloud-acceptance',{**record,'protected_receipt':retain(record,'ms-crisis-v6-all-month-cloud-acceptance')})
    print(json.dumps({'status':'passed','months':21,'native_report_pairs':42}))
if __name__=='__main__':main()
