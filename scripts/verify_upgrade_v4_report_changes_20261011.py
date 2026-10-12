"""Independently bind v4 to v3's numerical audit and permit only scoped text changes."""
import hashlib,json,re,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from pypdf import PdfReader
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage33/v4-comparison-v1'
BASE=ROOT/'.work/platform-upgrade-20261011-stage33'
A='{http://schemas.openxmlformats.org/drawingml/2006/main}'
def require(ok,msg):
    if not ok:raise ValueError(msg)
def main():
    require(not OUT.exists(),'Preserve previous comparison')
    old=protected(BASE/'artifact-review-v1/downloads.json');new=protected(BASE/'artifact-review-v2/downloads.json')
    numeric=protected(BASE/'corrected-numbers-v1/result.json')
    require(numeric['passed'] is True and numeric['proof']['passed'] is True,'Independent audit required')
    trials=[protected(BASE/(v+'/result.json')) for v in ('corrected-reports-v3','corrected-reports-v4')]
    require({r['subject']:r['candidate_payload_sha256'] for r in trials[0]['proof']['subjects']}=={r['subject']:r['candidate_payload_sha256'] for r in trials[1]['proof']['subjects']},'Candidate financial inputs changed')
    records=[]
    for before in old['artifacts']:
        after=next(r for r in new['artifacts'] if (r['subject'],r['report_type'])==(before['subject'],before['report_type']))
        left=Path(before['file']);right=Path(after['file'])
        for p,r in ((left,before),(right,after)):require(hashlib.sha256(p.read_bytes()).hexdigest()==r['sha256'],'Artifact changed after download')
        row={'subject':before['subject'],'type':before['report_type'],'sha256':after['sha256']}
        if before['report_type']=='presentation':
            with zipfile.ZipFile(left) as z1,zipfile.ZipFile(right) as z2:
                charts=sorted(n for n in z1.namelist() if re.fullmatch(r'ppt/(?:slides/)?charts/chart\d+\.xml',n))
                require(len(charts)==6 and charts==sorted(n for n in z2.namelist() if re.fullmatch(r'ppt/(?:slides/)?charts/chart\d+\.xml',n)),'Chart inventory changed')
                for n in charts:require(ET.tostring(ET.fromstring(z1.read(n)))==ET.tostring(ET.fromstring(z2.read(n))),'Chart structure/data differs: '+n)
                changed=[]
                for i in range(1,21):
                    n=f'ppt/slides/slide{i}.xml';trees=[ET.fromstring(z.read(n)) for z in (z1,z2)]
                    for tag in ('tbl',):require([ET.tostring(x) for x in trees[0].iter(A+tag)]==[ET.tostring(x) for x in trees[1].iter(A+tag)],'Table changed')
                    texts=[[x.text or '' for x in t.iter(A+'t')] for t in trees]
                    if texts[0]!=texts[1]:
                        require(i==17 and before['subject'] not in ('alpha-world-ms','alpha-world-t'),'Unexpected slide text changed')
                        require(len(texts[0])==len(texts[1]),'Unexpected text structure change')
                        delta=[(a,b) for a,b in zip(*texts) if a!=b]
                        require(len(delta)==1 and delta[0][0].startswith('Bottom line:') and len(delta[0][1])<=240,'Unexpected conclusion edit')
                        require(re.findall(r'\d+(?:\.\d+)?',delta[0][0])==re.findall(r'\d+(?:\.\d+)?',delta[0][1]),'Numerical text changed')
                        changed.append(i)
                row.update(charts_unchanged=6,tables_unchanged=True,changed_text_slides=changed)
        else:
            pdf=[PdfReader(p) for p in (left,right)];require(all(len(p.pages)==2 for p in pdf),'Expected two-page factsheets')
            texts=['\n'.join(p.extract_text() or '' for p in r.pages) for r in pdf]
            numbers=[re.findall(r'[-+]?\d+(?:[.,]\d+)*(?:\s*%)?',s) for s in texts]
            require(numbers[0]==numbers[1],'Factsheet numeric text changed')
            if before['subject'] in ('hong-kong-china','U16088606'):
                require('North America' in texts[0] and 'North America' not in texts[1],'Native regions not corrected')
                require('Hong Kong' in texts[1],'Hong Kong label missing')
                if before['subject']=='U16088606':require('Taiwan' in texts[1],'Taiwan label missing')
                allowed=lambda s:re.sub(r'\s+','',re.sub(r'North Ameri\S*|Hong Kong|Taiwan| / ','',s))
                require(allowed(texts[0])==allowed(texts[1]),'Unexpected non-region factsheet text change')
                row['native_region_labels_verified']=True
            else:require(texts[0]==texts[1],'Unrelated factsheet text changed')
            row['numeric_text_unchanged']=True
        records.append(row)
    save(OUT,'result',{'passed':True,'source_audit':numeric['protected_receipt'],'artifacts':records,
        'charts_unchanged':30,'financial_inputs_unchanged':True,'native_visual_acceptance':'UNVERIFIED',
        'production_changed':False,'full_upgrade_complete':False})
    print(json.dumps({'passed':True,'charts_unchanged':30,'artifacts':len(records),'visual_acceptance':'UNVERIFIED'}))
if __name__=='__main__':main()
