// Read-only PPTX inspection/rendering. Never exports or modifies the source deck.
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const [source,folder]=process.argv.slice(2);
if (!source || !folder || !process.env.RUNTIME_NODE_MODULES) throw new Error('Source, new private folder and runtime modules required');
const {FileBlob,PresentationFile}=await import(pathToFileURL(path.join(process.env.RUNTIME_NODE_MODULES,'@oai/artifact-tool/dist/artifact_tool.mjs')).href);
await fs.mkdir(folder,{recursive:false});
const deck=await PresentationFile.importPptx(await FileBlob.load(source));
const inspection=await deck.inspect({kind:'slide,textbox,shape,chart,table,layout',maxChars:1000000});
await fs.writeFile(path.join(folder,'inspection.ndjson'),inspection.ndjson);
const slides=JSON.parse('['+inspection.ndjson.split('\n').filter(x=>x.trim()).join(',')+']').filter(x=>x.kind==='slide');
const numbers=slides.map(item=>Number.isInteger(item.slideIndex)?item.slideIndex+1:item.slide);
if(numbers.some(number=>!Number.isInteger(number)||number<1)||new Set(numbers).size!==slides.length)
  throw new Error('Missing or duplicate inspected slide numbering');
for (const item of slides) {
  const number=Number.isInteger(item.slideIndex)?item.slideIndex+1:item.slide;
  const slide=deck.resolve(item.id);
  const preview=await slide.export({format:'png',scale:1});
  await fs.writeFile(path.join(folder,`slide-${String(number).padStart(2,'0')}.png`),new Uint8Array(await preview.arrayBuffer()),{flag:'wx'});
  const layout=await slide.export({format:'layout'});
  await fs.writeFile(path.join(folder,`slide-${String(number).padStart(2,'0')}.layout.json`),await layout.text(),{flag:'wx'});
}
console.log(JSON.stringify({slides:slides.length,source_unchanged:true,folder}));
