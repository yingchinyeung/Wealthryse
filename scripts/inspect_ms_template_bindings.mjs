// Read-only inspection of a template's element IDs. Never exports a PPTX.
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {FileBlob, PresentationFile} from '@oai/artifact-tool';
const [source, output] = process.argv.slice(2);
try {
  const before = await fs.readFile(source);
  const deck = await PresentationFile.importPptx(await FileBlob.load(source));
  const inspection = await deck.inspect({kind:'slide,textbox,shape,table,chart', maxChars:1000000});
  await fs.writeFile(output, inspection.ndjson, {flag:'wx'});
  const rows = inspection.ndjson.split('\n').filter(Boolean).map(JSON.parse);
  const table = rows.find(row => row.id === 'tb/fql4fa5s');
  let resolved = false;
  try { resolved = !!deck.resolve('tb/fql4fa5s'); } catch {}
  const after = await fs.readFile(source);
  if (!before.equals(after)) throw new Error('Read-only source changed');
  console.log(JSON.stringify({source_sha256:createHash('sha256').update(before).digest('hex'),
    records:rows.length, inspected_legacy_table:table ?? null, legacy_table_resolved:resolved,
    tables:rows.filter(row=>row.kind==='table').map(row=>({id:row.id,slideIndex:row.slideIndex,
      slide:row.slide,rows:row.rows,cols:row.cols,text:row.text,name:row.name}))}));
} catch (error) { console.error(error.message); process.exitCode=1; }
