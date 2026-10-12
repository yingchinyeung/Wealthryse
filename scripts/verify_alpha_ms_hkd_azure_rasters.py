"""Compare every downloaded Azure page to its already visually reviewed page."""
from pathlib import Path
import json
from PIL import Image,ImageChops

ROOT=Path(__file__).resolve().parents[1]


def main():
    local=ROOT/'tmp/pdfs/alpha-ms-hkd-v14r8'
    azure=ROOT/'tmp/pdfs/alpha-ms-azure-hkd-v14r8'
    files=sorted(azure.glob('*-[12].png'))
    if len(files)!=42:raise ValueError('42 actual Azure raster pages required')
    different=[]
    for path in files:
        a=Image.open(path).convert('RGB');b=Image.open(local/path.name).convert('RGB')
        if a.size!=b.size or ImageChops.difference(a,b).getbbox() is not None:different.append(path.name)
    if different:raise ValueError('Actual Azure pages need additional visual review: '+str(different))
    record={'azure_pages_checked':42,'pixel_parity_with_visually_reviewed_pages':'pass',
        'production_cutover':False,'onboarding_activated':False,'reports_released':False}
    folder=ROOT/'.work/alpha-ms-hkd-azure-v14r8c-20261007'
    (folder/'actual-azure-visual-qa.json').open('x',encoding='utf-8').write(json.dumps(record,indent=2))
    print(json.dumps(record))


if __name__=='__main__':main()
