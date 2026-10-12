"""Compare real Azure candidate/rollback outputs; only disclaimer may differ."""
import json
from pathlib import Path
import subprocess
from PIL import Image,ImageChops
from pypdf import PdfReader
import release_alpha_ms_disclaimer_v14r16 as c
import build_ms_presentation_worker_candidate as build
from ibkr_etl.alpha_world_ms_copy import validate_copy
from ibkr_etl.alpha_world_ms_pdf_validation import validate_date_headers
from ibkr_etl.alpha_world_ms_subtitle import validate as validate_subtitle
from run_specialist_gateway_read_verification import retain
from replay_ms_disclaimer_v14r16 import POPPLER


def main():
    checks=[];qa=c.FOLDER/'cloud-pixel-acceptance';qa.mkdir(exist_ok=False)
    for kind in ('worker','web'):
        folder=c.FOLDER/kind;before=folder/'rollback-runtime-v1';after=folder/'candidate-runtime-v1'
        old=c.read(before,'verification');new=c.read(after,'verification')
        assert old['count']==new['count']==21
        oldrows={r['period_end']:r for r in old['artifacts']}
        for row in new['artifacts']:
            period=row['period_end'];prior=oldrows[period]
            for key in ('snapshot_id','data_sha256','metrics','calculation_evidence'):
                assert row[key]==prior[key],(period,key)
            name=Path(row['factsheet_blob']).name;pdf=after/'outputs'/name;baseline=before/'outputs'/name
            reader=PdfReader(pdf);previous=PdfReader(baseline)
            validate_copy(pdf);validate_subtitle(reader.pages[0]);validate_date_headers(reader,period)
            for key in ('/MSMetrics','/MSCalculationEvidence','/LayoutAuthoritySHA256','/CapitalPathBasis'):
                assert reader.metadata[key]==previous.metadata[key],key
            assert reader.metadata['/ChartStyleVersion']==c.LAYOUT
            tile=qa/kind/period;tile.mkdir(parents=True)
            for label,path in (('old',baseline),('new',pdf)):
                subprocess.run([str(POPPLER),'-png','-r','100',str(path),str(tile/label)],capture_output=True,check=True)
            for page in (1,2):
                a=Image.open(tile/f'old-{page}.png').convert('RGB');b=Image.open(tile/f'new-{page}.png').convert('RGB')
                assert a.size==b.size
                diff=ImageChops.difference(a,b)
                if page==2:
                    assert diff.getbbox() is not None
                    scale=100/72.;height=float(reader.pages[1].mediabox.height)
                    diff.paste((0,0,0),(int(37*scale),int((height-528)*scale),int(566*scale)+1,int((height-95)*scale)+1))
                assert diff.getbbox() is None,(kind,period,page,'outside disclaimer changed')
            checks.append({'kind':kind,'period_end':period,'pdf_sha256':build.sha(pdf),
                'snapshot_id':row['snapshot_id'],'data_sha256':row['data_sha256'],
                'calculation_unchanged':True,'outside_requested_regions_pixel_identical':True})
            print(kind+' '+period+' accepted',flush=True)
    worker={r['period_end']:r['pdf_sha256'] for r in checks if r['kind']=='worker'}
    web={r['period_end']:r['pdf_sha256'] for r in checks if r['kind']=='web'}
    assert worker==web
    record={'status':'passed','months':21,'cloud_pdf_checks':42,'pages_compared':84,
        'website_worker_pdf_bytes_identical':True,'checks':checks,'database_writes':0,
        'source_data_changed':False,'report_approvals_changed':False}
    build.save(c.FOLDER,'cloud-acceptance',{**record,'protected_receipt':retain(record,'ms-disclaimer-v14r16-cloud-acceptance')})
    print(json.dumps({'status':'passed','months':21,'cloud_pdf_checks':42}),flush=True)


if __name__=='__main__':main()
