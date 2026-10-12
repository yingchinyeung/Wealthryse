"""Verify that the MS follow-up changes only one shape's vertical placement."""
import hashlib,json,re,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.work/platform-upgrade-20261011-stage33'
OUT=BASE/'ms-note-comparison-v1'
A='{http://schemas.openxmlformats.org/drawingml/2006/main}'

def require(ok,message):
    if not ok:raise ValueError(message)

def main():
    require(not OUT.exists(),'Preserve prior comparison')
    old=protected(BASE/'artifact-review-v2/downloads.json')
    new=protected(BASE/'artifact-review-ms-note-v1/downloads.json')
    previous=protected(BASE/'v4-comparison-v1/result.json')
    require(previous['passed'] is True,'Previous numerical verification required')
    outputs={r['report_type']:r for r in new['artifacts']}
    before={r['report_type']:r for r in old['artifacts'] if r['subject']=='alpha-world-ms'}
    require(set(outputs)==set(before)=={'factsheet','presentation'},'Expected one MS pair')
    for r in [*outputs.values(),*before.values()]:
        require(hashlib.sha256(Path(r['file']).read_bytes()).hexdigest()==r['sha256'],'Changed artifact')
    require(outputs['factsheet']['sha256']==before['factsheet']['sha256'],'Factsheet changed')
    changes=[]
    with zipfile.ZipFile(before['presentation']['file']) as left,zipfile.ZipFile(outputs['presentation']['file']) as right:
        charts=lambda z:sorted(n for n in z.namelist() if re.fullmatch(r'ppt/(?:slides/)?charts/chart\d+\.xml',n))
        require(charts(left)==charts(right) and len(charts(left))==6,'Chart inventory changed')
        for n in charts(left):require(ET.tostring(ET.fromstring(left.read(n)))==ET.tostring(ET.fromstring(right.read(n))),'Chart data changed')
        for i in range(1,21):
            trees=[ET.fromstring(z.read(f'ppt/slides/slide{i}.xml')) for z in (left,right)]
            for tag in ('t','tbl','pPr','rPr','endParaRPr'):
                require([ET.tostring(x) for x in trees[0].iter(A+tag)]==[ET.tostring(x) for x in trees[1].iter(A+tag)],f'Text/table/typography changed: {i}, {tag}')
            transforms=[list(t.iter(A+'xfrm')) for t in trees]
            require(len(transforms[0])==len(transforms[1]),'Shape count changed')
            for a,b in zip(*transforms):
                if ET.tostring(a)==ET.tostring(b):continue
                require(i==5,'Unexpected slide geometry change')
                first=a.find(A+'off');second=b.find(A+'off')
                require(first is not None and second is not None,'Missing shape position')
                require(first.attrib=={'x':'500063','y':'4371975'} and second.attrib=={'x':'500063','y':'4600575'},'Unexpected displacement')
                second.set('y',first.get('y'))
                require(ET.tostring(a)==ET.tostring(b),'More than vertical placement changed')
                changes.append({'slide':i,'vertical_move_emu':228600,'artifact_pixels':24})
    require(len(changes)==1,'Exactly one footnote movement required')
    save(OUT,'result',{'passed':True,'previous_numeric_acceptance':previous['protected_receipt'],
        'factsheet_unchanged':True,'charts_unchanged':6,'all_slide_text_tables_and_typography_unchanged':True,
        'geometry_changes':changes,'presentation_sha256':outputs['presentation']['sha256'],
        'native_visual_acceptance':'UNVERIFIED','production_changed':False})
    print(json.dumps({'passed':True,'charts_unchanged':6,'only_change':'MS slide 5 footnote moved down 24 pixels'}))

if __name__=='__main__':main()
