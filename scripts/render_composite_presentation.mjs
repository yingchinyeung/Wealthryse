// A composite's monthly deck in the Alpha World MS or T design: MS's HKD deck
// path under the composite's own name, description and composition. The
// reference renderer is read, checked and adapted in memory, never changed.
// Usage: render_composite_presentation.mjs template.pptx snapshot.json output.pptx [commentary.json]
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {artifactToolUrl,bindTReportImports} from './alpha_t_presentation_runtime.mjs';
import {REFERENCE_SHA256,adaptReferenceRenderer,compositeStrategyName} from './composite_presentation_runtime.mjs';

const snapshot=JSON.parse(await fs.readFile(process.argv[3],'utf8'));
const strategyName=compositeStrategyName(snapshot);
if(process.env.PRESENTATION_STRATEGY_NAME && process.env.PRESENTATION_STRATEGY_NAME!==strategyName)
  throw Error('The governed strategy name differs from the composite report profile');
process.env.PRESENTATION_STRATEGY_NAME=strategyName;
const source=await fs.readFile(new URL('./render_monthly_presentation.mjs',import.meta.url),'utf8');
if(createHash('sha256').update(source).digest('hex')!==REFERENCE_SHA256)
  throw Error('Reference renderer changed; inspect before adapting');
const code=bindTReportImports(adaptReferenceRenderer(source,artifactToolUrl()),import.meta.url);
try {await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));}
catch(error){console.error(error.message); console.error(String(error.stack).split('\n').filter(x=>!x.includes('data:text')).slice(0,5).join('\n'));process.exitCode=1;}
