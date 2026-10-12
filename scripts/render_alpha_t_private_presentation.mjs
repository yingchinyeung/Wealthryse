// Isolated private template adapter. Does not alter the production renderer.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {artifactToolUrl,bindTemplateObjectIds,bindTReportImports} from './alpha_t_presentation_runtime.mjs';
const src=await fs.readFile(new URL('./render_monthly_presentation.mjs',import.meta.url),'utf8');
if(createHash('sha256').update(src).digest('hex')!=='8b62a1dd498bfffccdb415dcc7999e92b79af402882082ccf9176bb9face6add')
  throw Error('Reference renderer changed; inspect before adapting');
// Desktop finalization remains enabled when its complete environment is set.
// Azure uses the installed, image-pinned package and the same financial audit.
const artifactRuntimeUrl=artifactToolUrl();
let code=src.replaceAll('alpha-world-ms','alpha-world-t').replaceAll('Alpha World MS','Alpha World T')
 .replaceAll('line: { fill:',"line: { style: 'solid', fill:")
 .replaceAll('chartLine: { fill:',"chartLine: { style: 'solid', fill:")
 .replace("['-m','ibkr_etl.alpha_world_ms_presentation',snapshotPath]","['-m','ibkr_etl.alpha_world_t_presentation',snapshotPath]")
 .replace("from \"@oai/artifact-tool\"",`from ${JSON.stringify(artifactRuntimeUrl)}`)
 .replace("scripts/audit_alpha_ms_presentation.py","scripts/audit_alpha_t_private_presentation.py")
 .replaceAll('Frongello-linked contribution to return','Fixed-unit contribution to return')
 .replaceAll('are Frongello-linked','use component value changes divided by starting NAV')
 .replaceAll('Equity contribution','Risky-sleeve contribution')
 .replaceAll('Top 10 components','Components')
 .replaceAll('Top 10 ${holdingNoun}s','${isMsHkd ? \'Components\' : \'Top 10 holdings\'}')
 .replace("'U16088571':'US-Tech'","'U16088606':'GC','U16088571':'US-Tech'")
 .replace("'U16088571':'US Tech & Biotech'","'U16088606':'Greater China incl. Taiwan','U16088571':'US Tech & Biotech'")
 .replace("'HIBOR + Spread (unavailable)'","'HIBOR + Spread'")
 .replace("'Secondary benchmark (unavailable)'","'Secondary benchmark'");
const internalMode=process.env.T_PRESENTATION_MODE==='internal';
if(process.env.T_PRESENTATION_MODE && !internalMode)throw Error('Unknown T presentation mode');
let extra=`
const tHasCrisis=msModel.crisis_available;
// Native PowerPoint expands the retained empty allocation rows. Keep the
// approved table and font sizes, but leave room before its explanatory note.
presentation.resolve('sh/4ji5kbid').position.top=486;
setText('sh/14ryd8fq', 'Private T review. '+longDate(periodEnd)+'. Internal/shareholder use only; provisional market data.');
setText('sh/ydgnit07','Weights use absolute component values including cash. Gross risky exposure / NAV: '+percent((grossMarketValue-58)/nav)+'.');
setText('sh/onmxcv69','Each contribution equals the fixed-unit component value change divided by starting NAV. These are percentage-point effects on return.');
setText('sh/cva9k7a9','Signed component-type effects sum to the monthly NAV return. Bars show magnitudes. Benchmark comparisons are unavailable.');
setText('sh/at8j290z','HKD total return '+percent(risk.total_return)+'. HIBOR uses the inception-to-date period average. Benchmark comparisons are unavailable. '+(tHasCrisis?'Hypothetical crisis replay uses provisional prices and documented proxies.':'Crisis replay is not qualified.'));
setText('sh/e98nydwb','Underlying account NAV includes actual cash and interest.');
setText('sh/fah47idw','Initial gross risky exposure is 230%. Fixed units let weights drift.');
setText('sh/gbq50neh','Current gross risky exposure / NAV: '+percent((grossMarketValue-58)/nav)+'.');
setText('sh/1cj69sv2',tHasCrisis?'Hypothetical crisis replay: '+percent(risk.crisis_maximum_drawdown)+'. Provisional prices and proxies.':'Crisis replay is not qualified.');
setText('sh/2dsn2xwn','The constant cash sleeve remains 58 per initial NAV 100.');
setText('sh/2l4faloj','Fixed-unit contribution to return. '+shortDate(monthlyReporting.period_start)+' to '+shortDate(dataAsOf));
setTable('tb/5kb2xwby',[
 ['Control','Status','Evidence'],
 ['T NAV arithmetic','PASS',msModel.calculation.nav_observations+' observations, inception 20 Jan 2025'],
 ['Broker records','CHECKED','NAV and FX match archived broker originals'],
 ['Cash transaction details','MATCHED','1,718 broker IDs recovered privately; originals retained'],
 ['Risk-free rate','MATCHED','1M HIBOR, period average '+percent(risk.risk_free_annual_rate)],
 ['Crisis replay',tHasCrisis?'INTERNAL ONLY':'PENDING',tHasCrisis?'121 sessions; provisional prices and documented proxies':'Qualified T replay unavailable'],
 ['Model basis','FIXED UNITS','230% initial gross risky exposure; cash 58'],
 ['Azure registration','NOT LIVE','Private model only; no T production rows']
]);
setTableCellFontSize('tb/5kb2xwby',2,2,11);
const hasPaths=risk.drawdown_simulation_paths>0;
setText('sh/up4fal83','Observed drawdown and historical-return bootstrap estimates');
setText('sh/d0fydk7m',hasPaths?'5,000 paths, 252 sessions':'N/A: fewer than 20 returns');
setText('sh/xw7mts3y',risk.actual_maximum_drawdown_1y==null?'N/A: fewer than 252 returns':'Latest 252 observed returns');
setText('sh/6l0nqxkf',hasPaths?'Mean of worst 5% of paths':'N/A: fewer than 20 returns');
setText('sh/gjelc7up',tHasCrisis?'15 Sep 2008 to 9 Mar 2009 replay':'Qualified replay unavailable');
setText('sh/25w3excv',(hasPaths?'5-session block bootstrap; estimates are not forecasts. ':'Bootstrap needs 20 returns; observed 1-year drawdown needs 252. ')+(tHasCrisis?'Crisis replay uses current holdings, provisional prices and documented proxies. Internal use only.':'Crisis replay is not qualified.'));
setText('sh/kza18ji1',hasPaths?'Mean drawdown in worst 5% of paths: '+percent(risk.tail_maximum_drawdown_1y)+'.':'Insufficient history for bootstrap drawdown.');
setTable('tb/3mtobupc',[
 ['Input / control','Value'],['Return observations',String(risk.return_observations)],
 ['Block length',hasPaths?'5 sessions':'N/A'],['Simulation paths',hasPaths?'5,000':'0'],
 ['Tail confidence',hasPaths?'95%':'N/A'],['Horizon',hasPaths?'252 sessions':'N/A']
]);
presentation.resolve('tb/m1gj2xgb').cells.set(8,0,'Mean daily return');
presentation.resolve('tb/m1gj2xgb').cells.set(8,2,'SE '+percent(risk.blue_standard_error,4)+' (iid assumption)');
const templateText=await presentation.inspect({kind:'textbox',maxChars:1000000});
const safeText=new Map([
 ['Factsheet Summary','Alpha World T'],
 ['SPROUT ASSET MANAGEMENT','PRIVATE MODEL REVIEW'],
 ['Sector Allocation','Component Allocation'],
 ['Monthly Security Contribution','Monthly Component Contribution'],
 ['iStrategy Fees','Alpha World T Model'],
 ['Terms and Conditions','Fixed-unit calculation basis'],
 ['Rebalancing and Updates','Model assumptions'],
 ['Rebalancing: Every two weeks','Fixed units from 20 January 2025'],
 ['Market Updates: Monthly end','Reporting currency: HKD'],
 ['Rebalancing Explanations: Quarterly end','Constant cash: 58 per initial NAV 100'],
 ['Performance fees calculated as the delta versus the prior quarter and only deemed earned if higher than the prior quarter and is a positive return (no watermark).',
  'Underlying account NAV includes its cash and interest. No additional financing charge is added. T fee terms are not established by this model.'],
 ['inquiry@www.sprout-asset.com','inquiry@sprout-asset.com'],
 ['llustrative examples only','Illustrative examples only']
]);
for(const line of templateText.ndjson.split('\\n').filter(Boolean)){
 const x=JSON.parse(line),old=String(x.text||x.textPreview||'').replace(/\\s+/g,' ').trim();
 if(safeText.has(old))setText(x.id,safeText.get(old));
}
presentation.resolve('sh/4vm5sfy1').text.style={fontSize:16,fontFamily:'Arial',color:'#FFFFFF'};
for(const slide of presentation.slides.items){
 slide.speakerNotes.textFrame.setText('Alpha World T, '+periodEnd+'. HKD fixed-unit model from 20 January 2025. Source: original IBKR statements, frozen T monthly history, and published HKAB 1M HIBOR fixings (https://www.hkab.org.hk/api/hibor). Cash and actual interest are included in underlying NAV; no additional financing is charged. Bootstrap uses 5,000 paths, 5-session blocks and a 252-session horizon, with a minimum of 20 observed returns. Tail drawdown is the mean of the worst 5% of paths. Observed 1-year drawdown requires 252 returns. Standard error assumes iid returns. Benchmark comparisons are unavailable. '+(tHasCrisis?'Hypothetical crisis: 15 September 2008 to 9 March 2009, current holdings, archived provisional adjusted security prices and documented proxies, including the owner-approved T-only SPY identity proxy; HKMA historical FX and provisional unadjusted hedge prices. Arithmetic independently reconciled; provider prices not independently certified. Source fingerprint '+snapshot.risk.crisis_replay.input_sha256+'. ':'Crisis replay unavailable. ')+'Private review, not released; internal/shareholder use only.');
}
`;
if(internalMode)extra=extra
 .replace('Private T review. ','Alpha World T. ')
 .replace("['Cash transaction details','MATCHED','1,718 broker IDs recovered privately; originals retained']",
          "['Cash sleeve','FIXED','58 HKD per initial NAV 100; no additional interest']")
 .replace("['Azure registration','NOT LIVE','Private model only; no T production rows']",
          "['Release scope','INTERNAL ONLY','Access requires the website approval workflow']")
 .replace('PRIVATE MODEL REVIEW','INTERNAL SHAREHOLDER REPORT')
 .replace('Private review, not released; internal/shareholder use only.','Internal/shareholder use only; access is governed separately by the website release workflow.');
code=code.replace('const outputDir = path.dirname(path.resolve(finalPath));',extra+'\nconst outputDir = path.dirname(path.resolve(finalPath));');
// This runtime regenerates table/chart IDs on import. Bind by unique owner slide.
const ids={
 'tb/fql4fa5s':2,'tb/gjutwb61':4,'tb/wfmpkre1':5,'tb/edoralwf':8,'tb/qpsbq1o7':9,
 'tb/i1onmh0n':10,'tb/m1gj2xgb':11,'tb/3ets36l8':12,'tb/3mtobupc':13,'tb/a9srud0n':14,'tb/5kb2xwby':15,
 'ch/w7yloryp':5,'ch/g36d8zy1':6,'ch/m9w3y1cn':7,'ch/ut8nmhsr':8,'ch/e94rut4f':9,'ch/2pk76d4z':10
};
// Resolve from this exact import, not a previous private runtime inspection.
code=code.replaceAll('presentation.resolve(', 'resolveT(')
 .replace('tableDimensions.get(id)','tableDimensions.get(tIds.get(id)||id)')
 .replace("tableDimensions.get('tb/gjutwb61')","tableDimensions.get(tIds.get('tb/gjutwb61'))");
const imported='const presentation = await PresentationFile.importPptx(await FileBlob.load(sourcePath));';
if(!code.includes(imported))throw Error('Template import boundary changed');
code=code.replace(imported,imported+`
const tInspection=(await presentation.inspect({kind:'table,chart',maxChars:1000000})).ndjson.split('\\n').filter(Boolean).map(JSON.parse);
const tIds=new Map((${bindTemplateObjectIds.toString()})(tInspection,${JSON.stringify(ids)}));
const resolveT=id=>presentation.resolve(tIds.get(id)||id);
`);
code=bindTReportImports(code,import.meta.url);
try {await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));}
catch(error){console.error(error.message); console.error(String(error.stack).split('\n').filter(x=>!x.includes('data:text')).slice(0,5).join('\n'));process.exitCode=1;}
