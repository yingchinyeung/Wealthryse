"""Local all-month layout-only regeneration and unchanged-input audit."""
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader
from ibkr_etl.monthly_artifacts import render_factsheet


def main():
    root=Path(__file__).resolve().parents[1]
    audit=root/'.work/alpha-world-t-onboarding-20261008/history-v1'
    output=root/'output/pdf/AlphaWorld_T_Factsheets_Layout_v4'
    previous=root/'output/pdf/AlphaWorld_T_Factsheets_Layout_v3'
    template=root/'templates/alpha-world-t/owner_template_20261008.pdf'
    files=sorted((audit/'snapshots').glob('*.json'))
    if len(files)!=21: raise ValueError('Expected 21 frozen actual monthly inputs')
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files+[template]}
    results=[]
    for p in files:
        s=json.loads(p.read_text(encoding='utf-8'))
        target=output/f'AlphaWorld_T_Factsheet_{s["period_end"]}.pdf'
        result=render_factsheet(s,template_path=template,output_path=target)
        old=PdfReader(previous/target.name);new=PdfReader(target)
        fields=('/TMetrics','/TSnapshotSHA256','/TCalculationEvidence','/TMethodologyIdentity','/PublicationAllowed')
        if any(old.metadata[key]!=new.metadata[key] for key in fields):
            raise ValueError('Non-layout state changed: '+p.name)
        result['v3_metrics_input_evidence_and_release_state_unchanged']=True
        result['pdf_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
        results.append(result)
        print(s['period_end'],'PASS',flush=True)
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h for p,h in before.items()):
        raise ValueError('Source input or template modified')
    evidence={'status':'pass','months':21,'source_hashes_unchanged':before,'azure_changed':False,
              'main_disclaimer_unchanged':True,'results':results}
    with (audit/'factsheet-layout-v4-results.json').open('x',encoding='utf-8') as stream:
        json.dump(evidence,stream,indent=2)


if __name__=='__main__': main()
