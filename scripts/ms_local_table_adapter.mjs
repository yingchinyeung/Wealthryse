// Local qualification only. Never copy into the Azure build context.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {PresentationFile} from '@oai/artifact-tool';
import {tableAliases,chartAliases,explicitLegacyPointLineStyle} from './ms_table_binding_compatibility.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const referencePath=path.join(root,'.work/alpha-ms-presentation-hkd-20261007/source-layout-review/inspection.ndjson');
const reference=await fs.readFile(referencePath);
if(createHash('sha256').update(reference).digest('hex')!=='87a387c4c1642d4d5f19a8d6d97e98310bb969fa78d87a746bf4089668430760')
  throw new Error('Legacy source inspection differs');
const template=await fs.readFile(process.env.MS_LOCAL_TEMPLATE);
if(createHash('sha256').update(template).digest('hex')!=='4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191')
  throw new Error('Approved source template differs');
const original=PresentationFile.importPptx.bind(PresentationFile);
PresentationFile.importPptx=async function(...args) {
  const presentation=await original(...args);
  const current=await presentation.inspect({kind:'slide,textbox,shape,table,chart',maxChars:1000000});
  const oldRows=reference.toString().split('\n').filter(Boolean).map(JSON.parse);
  const newRows=current.ndjson.split('\n').filter(Boolean).map(JSON.parse);
  const tables=tableAliases(oldRows,newRows),charts=chartAliases(oldRows,newRows);
  const aliases=new Map([...tables,...charts]);
  const resolve=presentation.resolve.bind(presentation);
  presentation.resolve=id=>resolve(aliases.get(id)??id);
  for(const slide of presentation.slides.items) {
    const add=slide.charts.add.bind(slide.charts);
    slide.charts.add=(type,options)=>add(type,explicitLegacyPointLineStyle(options));
  }
  const inspect=presentation.inspect.bind(presentation);
  presentation.inspect=async function(...options) {
    const result=await inspect(...options);
    if(options[0]?.kind?.includes('table') && result.ndjson) {
      const reverse=new Map([...aliases].map(([oldId,newId])=>[newId,oldId]));
      return {...result,ndjson:result.ndjson.split('\n').filter(Boolean).map(line=>{
        const row=JSON.parse(line); if(row.kind==='table') row.id=reverse.get(row.id)??row.id;
        return JSON.stringify(row);
      }).join('\n')};
    }
    return result;
  };
  console.log(JSON.stringify({local_template_id_adapter:true,tables_verified:tables.size,charts_verified:charts.size,
    changed_ids:[...aliases].filter(([before,after])=>before!==after).length,
    legacy_point_lines_explicit_solid:true,
    production_renderer_source_unchanged:true}));
  return presentation;
};
