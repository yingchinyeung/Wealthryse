"""Native original/candidate render equality; no database or report publication."""
import difflib,hashlib,io,json,os,runpy,sys,zipfile
from pathlib import Path,PurePosixPath
from xml.etree import ElementTree as ET


def digest(data):return hashlib.sha256(data).hexdigest()


def parts(content):
    result={}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if archive.testzip():raise ValueError('Invalid OOXML archive')
        relations={};relationship_xml={}
        for name in archive.namelist():
            if not name.endswith('.rels'):continue
            root=ET.fromstring(archive.read(name));mapping={}
            for element in root:
                old=element.attrib['Id']
                if old in mapping:raise ValueError('Duplicate relationship ID')
                semantic={k:v for k,v in element.attrib.items() if k!='Id'}
                # IDs are package-local arbitrary names, not slide content.
                # Keep every relationship and its type/target/mode unchanged.
                mapping[old]='REL_'+digest(json.dumps(semantic,sort_keys=True).encode())
                element.set('Id',mapping[old])
            relations[name]=mapping
            root[:]=sorted(root,key=lambda e:json.dumps(e.attrib,sort_keys=True))
            relationship_xml[name]=ET.tostring(root)
        for name in sorted(archive.namelist()):
            data=archive.read(name)
            if name.endswith(('.xml','.rels')):
                if name.endswith('.rels'):data=relationship_xml[name]
                else:
                    root=ET.fromstring(data);part=PurePosixPath(name)
                    mapping=relations.get(str(part.parent/'_rels'/(part.name+'.rels')), {})
                    for element in root.iter():
                        for key,value in list(element.attrib.items()):
                            if key.startswith('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'):
                                if value not in mapping:raise ValueError('Dangling relationship reference')
                                element.set(key,mapping[value])
                    data=ET.tostring(root)
                if name=='docProps/core.xml':
                    root=ET.fromstring(data)
                    for child in list(root):
                        if child.tag in ('{http://purl.org/dc/terms/}created','{http://purl.org/dc/terms/}modified'):root.remove(child)
                    data=ET.tostring(root)
                data=ET.canonicalize(data).encode()
            elif name.endswith('.xlsx'):
                data=json.dumps(parts(data),sort_keys=True).encode()
            result[name]=digest(data)
    return result


def main():
    stage=sys.argv[1]
    if stage not in ('baseline','candidate') or os.getuid()!=10001:raise ValueError('Nonroot exact render stage required')
    if any(os.environ.get(k) for k in ('DATABASE_URL','AZURE_CLIENT_SECRET','AZURE_STORAGE_ACCOUNT_URL')):raise ValueError('No production credentials permitted')
    root=Path('/qualification');config=json.loads((root/'config.json').read_bytes());snapshot=root/'snapshot.json'
    template=Path('/app/templates/U16088571/approved-august-2026-20-slide-v23.pptx')
    if digest(snapshot.read_bytes())!=config['snapshot_sha256'] or digest(template.read_bytes())!=config['template_sha256']:raise ValueError('Approved render inputs changed')
    module=runpy.run_path('/app/scripts/upgrade_historical_presentations_v23.py',run_name='render_qualification')
    output=root/(stage+'.pptx');module['_render'](template,snapshot,output)
    checked=module['validate'](output,expected_period='2026-08-31')
    if int(checked['slides'])!=20:raise ValueError('Expected exactly twenty slides')
    value=parts(output.read_bytes());result={'stage':stage,'passed':True,'slides':20,'parts':len(value),
        'normalized_package_sha256':digest(json.dumps(value,sort_keys=True).encode()),'snapshot_sha256':config['snapshot_sha256'],
        'template_sha256':config['template_sha256'],'database_connections':0,'published_reports_changed':0,
        'normalization':'OOXML core created/modified, ZIP timestamps and arbitrary relationship IDs; all relationship targets, content and media retained'}
    if stage=='baseline':(root/'baseline-parts.json').write_text(json.dumps(value,sort_keys=True))
    else:
        previous=json.loads((root/'baseline-parts.json').read_bytes());changed=[n for n in set(value)|set(previous) if value.get(n)!=previous.get(n)]
        result['different_parts']=sorted(changed);result['passed']=not changed
        if changed:
            with zipfile.ZipFile(root/'baseline.pptx') as left,zipfile.ZipFile(output) as right:
                for name in ('_rels/.rels','ppt/_rels/presentation.xml.rels','ppt/slides/slide1.xml','ppt/slideMasters/slideMaster1.xml','ppt/slideLayouts/slideLayout12.xml'):
                    if name not in changed:continue
                    a=left.read(name).decode().replace('><','>\n<').splitlines();b=right.read(name).decode().replace('><','>\n<').splitlines()
                    delta=[line[:1200] for line in difflib.unified_diff(a,b,n=1) if line.startswith(('+','-'))][:12]
                    print('RENDER_DIFF '+json.dumps({'part':name,'diff':delta}),flush=True)
    print('LEGACY_RENDER_ACCEPTANCE '+json.dumps(result,sort_keys=True),flush=True)
    if not result['passed']:raise ValueError('Original/candidate deck contents differ; review before deployment')


if __name__=='__main__':main()
