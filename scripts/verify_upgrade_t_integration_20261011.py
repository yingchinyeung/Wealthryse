"""Download T's integration pair and compare every slide with reviewed QA."""
import hashlib,json,re,zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage34/t-review-v1'
A='{http://schemas.openxmlformats.org/drawingml/2006/main}'
def require(ok,msg):
    if not ok:raise ValueError(msg)
def main():
    require(not OUT.exists(),'Preserve earlier comparison')
    trial=protected(ROOT/'.work/platform-upgrade-20261011-stage34/t-integration-v1/result.json')
    old=protected(ROOT/'.work/platform-upgrade-20261011-stage33/artifact-review-v2/downloads.json')
    require(trial['passed'] is True and trial['proof']['artifacts']==2,'Qualified T pair required')
    before={r['report_type']:r for r in old['artifacts'] if r['subject']=='alpha-world-t'}
    rows=trial['proof']['subjects']
    require([r['subject'] for r in rows]==['alpha-world-t'],'T only')
    require(rows[0]['source_snapshot_sha256']==rows[0]['candidate_payload_sha256'],'Financial input changed')
    save(OUT,'intent',{'scope':'Read-only private T QA download and comparison','trial':trial['protected_receipt']})
    outputs={}
    with BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()) as client:
        for r in rows[0]['artifacts']:
            kind=r['report_type'];container,name=r['output_reference'].split('/',1)
            expected='factsheet.pdf' if kind=='factsheet' else 'presentation.pptx'
            require(kind in before and container=='upgrade-g04-reports-20261010' and name=='candidate-stage34-t-integration-v1/alpha-world-t/'+expected,'Outside approved QA pair')
            blob=client.get_blob_client(container,name)
            require(blob.get_blob_properties().size<64*1024*1024,'Oversized output')
            content=blob.download_blob().readall()
            require(hashlib.sha256(content).hexdigest()==r['sha256'],'Download hash mismatch')
            path=OUT/('AlphaWorld_T_Integrated_QA_2026-09-30'+Path(expected).suffix)
            with path.open('xb') as f:f.write(content)
            outputs[kind]={'file':str(path),'sha256':r['sha256']}
    require(set(outputs)=={'factsheet','presentation'},'Missing pair')
    for r in before.values():require(hashlib.sha256(Path(r['file']).read_bytes()).hexdigest()==r['sha256'],'Reviewed original changed')
    require(outputs['factsheet']['sha256']==before['factsheet']['sha256'],'T factsheet changed')
    with zipfile.ZipFile(before['presentation']['file']) as left,zipfile.ZipFile(outputs['presentation']['file']) as right:
        charts=lambda z:sorted(n for n in z.namelist() if re.fullmatch(r'ppt/(?:slides/)?charts/chart\d+\.xml',n))
        require(charts(left)==charts(right) and len(charts(left))==6,'Chart inventory differs')
        for n in charts(left):require(E.tostring(E.fromstring(left.read(n)))==E.tostring(E.fromstring(right.read(n))),'Chart differs')
        for i in range(1,21):
            trees=[E.fromstring(z.read(f'ppt/slides/slide{i}.xml')) for z in (left,right)]
            for tag in ('t','tbl','xfrm','pPr','rPr','endParaRPr'):
                require([E.tostring(x) for x in trees[0].iter(A+tag)]==[E.tostring(x) for x in trees[1].iter(A+tag)],f'Slide {i} {tag} differs')
    save(OUT,'result',{'passed':True,'trial':trial['protected_receipt'],'outputs':outputs,
        'factsheet_byte_identical':True,'financial_inputs_unchanged':True,'charts_unchanged':6,
        'slide_text_tables_geometry_typography_unchanged':20,'saved_job_changed':False,
        'candidate_deployed':False,'real_viewer_sign_in':'UNVERIFIED','observation_14_day':'UNVERIFIED'})
    print(json.dumps({'passed':True,'factsheet_identical':True,'slides_unchanged':20,'charts_unchanged':6}))
if __name__=='__main__':main()
