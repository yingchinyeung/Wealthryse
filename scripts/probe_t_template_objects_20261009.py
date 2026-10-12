"""Read only the approved template's native table/chart identities."""
import json,subprocess
code="""import {FileBlob,PresentationFile} from '@oai/artifact-tool';
const p=await PresentationFile.importPptx(await FileBlob.load('/app/templates/U16088571/approved-august-2026-20-slide-v24.pptx'));
const inspection=await p.inspect({kind:'table,chart',maxChars:1000000});
const rows=inspection.ndjson.split('\\n').filter(Boolean).map(JSON.parse);
const selected=rows.map(x=>({id:x.id,kind:x.kind,slideIndex:x.slideIndex,slide:x.slide,slideId:x.slideId,keys:Object.keys(x)}));
let direct;try {const x=p.resolve('tb/fql4fa5s');direct={ok:!!x,id:x.id};} catch(e){direct={ok:false,message:e.message};}
console.log(JSON.stringify({rows:selected,direct}));
"""
run=subprocess.run(['node','--input-type=module','-e',code],cwd='/app/scripts',capture_output=True,text=True,timeout=120)
if run.returncode:raise RuntimeError('Template inspection failed')
print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'passed','inspection':json.loads(run.stdout),'database_connected':False,'reports_created':False}),flush=True)
