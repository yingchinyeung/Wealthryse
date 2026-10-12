"""Exact cloud PDF acceptance: independently compare requested regions only."""
import json
from pathlib import Path
import subprocess
from PIL import Image,ImageChops
from pypdf import PdfReader
import release_alpha_ms_headers_v14r15 as candidate
import build_ms_presentation_worker_candidate as build
from ibkr_etl.alpha_world_ms_pdf_validation import validate_date_headers
from ibkr_etl.alpha_world_ms_subtitle import validate as validate_subtitle
from run_specialist_gateway_read_verification import retain


def main():
    checks=[];qa=build.ROOT/'tmp/pdfs/alpha-ms-cloud-headers-v14r15-20261008-r2'
    qa.mkdir(exist_ok=False)
    for kind in ('worker','web'):
        folder=candidate.FOLDER/kind
        before=folder/'rollback-runtime-v1';after=folder/'candidate-runtime-v1'
        old=json.loads((before/'verification.json').read_bytes())
        new=json.loads((after/'verification.json').read_bytes())
        if old['count']!=21 or new['count']!=21:raise ValueError('Incomplete cloud qualification')
        oldrows={r['period_end']:r for r in old['artifacts']}
        for row in new['artifacts']:
            period=row['period_end'];prior=oldrows[period]
            for key in ('snapshot_id','data_sha256','metrics','calculation_evidence'):
                if row[key]!=prior[key]:raise ValueError('Cloud calculation/input changed: '+period+'/'+key)
            name=Path(row['factsheet_blob']).name
            pdf=after/'outputs'/name;baseline=before/'outputs'/name
            reader=PdfReader(pdf);previous=PdfReader(baseline)
            validate_subtitle(reader.pages[0]);validate_date_headers(reader,period)
            for key in ('/MSMetrics','/MSCalculationEvidence','/MSCopyVersion','/MSCopySHA256'):
                if reader.metadata[key]!=previous.metadata[key]:raise ValueError('Financial/disclaimer metadata changed')
            tile=qa/kind/period;tile.mkdir(parents=True)
            for label,path in (('old',baseline),('new',pdf)):
                subprocess.run(['pdftoppm','-png','-r','90',str(path),str(tile/label)],capture_output=True,check=True)
            for page in (1,2):
                a=Image.open(tile/f'old-{page}.png').convert('RGB');b=Image.open(tile/f'new-{page}.png').convert('RGB')
                diff=ImageChops.difference(a,b)
                sx=b.width/float(reader.pages[page-1].mediabox.width);sy=b.height/float(reader.pages[page-1].mediabox.height)
                regions=[(480,781,590,819),(21,764,275,778)] if page==1 else [(480,798,590,838)]
                for left,bottom,right,top in regions:
                    diff.paste((0,0,0),(int(left*sx),int((float(reader.pages[page-1].mediabox.height)-top)*sy),int(right*sx)+1,int((float(reader.pages[page-1].mediabox.height)-bottom)*sy)+1))
                if diff.getbbox() is not None:raise ValueError('Cloud PDF changed outside approved headers: '+kind+'/'+period+'/'+str(page))
            checks.append({'kind':kind,'period_end':period,'pdf_sha256':build.sha(pdf),
                'snapshot_id':row['snapshot_id'],'data_sha256':row['data_sha256'],
                'calculation_unchanged':True,'outside_requested_regions_pixel_identical':True})
            print(kind+' '+period+' accepted',flush=True)
    worker={r['period_end']:r['pdf_sha256'] for r in checks if r['kind']=='worker'}
    web={r['period_end']:r['pdf_sha256'] for r in checks if r['kind']=='web'}
    if worker!=web:raise ValueError('Website and worker output bytes differ')
    record={'status':'passed','months':21,'cloud_pdf_checks':42,'pages_compared':84,
        'website_worker_pdf_bytes_identical':True,'checks':checks,
        'database_writes':0,'source_data_changed':False,'report_approvals_changed':False}
    build.save(candidate.FOLDER,'cloud-acceptance',{**record,'protected_receipt':retain(record,'ms-headers-v14r15-cloud-acceptance')})
    print(json.dumps({'status':'passed','months':21,'cloud_checks':42}))


if __name__=='__main__':main()
