import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {bindTReportImports} from './alpha_t_presentation_runtime.mjs';

const imports="import {cashActivityNote} from './report_content_labels.mjs';\nimport {genericSummaryLabel} from './report_summary_label.mjs';";
test('T data-module imports bind to reviewed sibling helpers',()=>{
  const result=bindTReportImports(imports,'file:///app/scripts/render_alpha_t_private_presentation.mjs');
  assert.match(result,/"file:\/\/\/app\/scripts\/report_content_labels.mjs"/);
  assert.match(result,/"file:\/\/\/app\/scripts\/report_summary_label.mjs"/);
});
test('T helper binding refuses missing, duplicate, extra relative and remote imports',()=>{
  assert.throws(()=>bindTReportImports('',import.meta.url));
  assert.throws(()=>bindTReportImports(imports+imports,import.meta.url));
  assert.throws(()=>bindTReportImports(imports+"\nimport x from '../unknown.mjs';",import.meta.url));
  assert.throws(()=>bindTReportImports(imports,'https://example.invalid/adapter.mjs'));
});
test('T exact source pin matches the reviewed shared renderer',async()=>{
  const source=await fs.readFile(new URL('./render_monthly_presentation.mjs',import.meta.url));
  const adapter=await fs.readFile(new URL('./render_alpha_t_private_presentation.mjs',import.meta.url),'utf8');
  assert.ok(adapter.includes("!=="+JSON.stringify(createHash('sha256').update(source).digest('hex')).replaceAll('"',"'")));
  assert.ok(adapter.includes("throw Error('Reference renderer changed; inspect before adapting')"));
  assert.doesNotMatch(bindTReportImports(source.toString(),import.meta.url),/from\s+['"]\.\.?\//);
});
