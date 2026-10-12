"""Render the native Azure exports read-only and compare all 840 slide images."""
import hashlib,json,os,subprocess
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from PIL import Image,ImageChops
import release_ms_crisis as candidate
ROOT=candidate.ROOT;FOLDER=candidate.FOLDER.parent/'candidate-v6'
NODE=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
MODULES=NODE.parent.parent/'node_modules'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    qa=FOLDER/'cloud-slide-previews-v1';qa.mkdir()
    env={**os.environ,'RUNTIME_NODE_MODULES':str(MODULES),'PYTHONUTF8':'1'}
    work=[]
    for kind in candidate.IMAGES:
        folder=FOLDER/kind/'candidate-runtime-v1';proof=json.loads((folder/'verification.json').read_bytes())
        for row in proof['artifacts']:work.append((kind,row,folder/'outputs'/Path(row['presentation_blob']).name))
    def one(item):
        kind,row,path=item;period=row['period_end'];folder=qa/kind/period;folder.parent.mkdir(exist_ok=True)
        result=subprocess.run([str(NODE),str(ROOT/'scripts/inspect_alpha_ms_presentation.mjs'),str(path),str(folder)],
            cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
        if result.returncode:raise ValueError(period+' native render failed: '+result.stderr[-1000:])
        same=[];different=[];hashes={}
        for i in range(1,21):
            image=folder/f'slide-{i:02d}.png';local=candidate.FOLDER.parent/'all-deck-previews-v1'/period/f'slide-{i:02d}.png'
            with Image.open(image) as a,Image.open(local) as b:
                identical=a.size==b.size and ImageChops.difference(a.convert('RGB'),b.convert('RGB')).getbbox() is None
            (same if identical else different).append(i);hashes[str(i)]=sha(image)
        return {'kind':kind,'period_end':period,'presentation_sha256':sha(path),'pixel_identical_slides':same,'different_slides':different,'preview_sha256':hashes}
    rows=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(one,item) for item in work]
        for future in as_completed(futures):
            row=future.result();rows.append(row);print(json.dumps({'native_slides_compared':row['kind'],'period_end':row['period_end'],'different_slides':row['different_slides']}),flush=True)
    record={'status':'passed' if all(not r['different_slides'] for r in rows) else 'manual_native_review_required','decks':42,'slides':840,'rows':sorted(rows,key=lambda r:(r['kind'],r['period_end']))}
    with (qa/'verification.json').open('x',encoding='utf-8') as f:json.dump(record,f,indent=2)
    print(json.dumps({'status':record['status'],'decks':42,'slides':840}))
if __name__=='__main__':main()
