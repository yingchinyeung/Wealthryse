// How a composite's deck adapts the reference renderer, kept apart from the
// entry script so the adaptation can be checked without the artifact runtime.
import {bindTemplateObjectIds} from './alpha_t_presentation_runtime.mjs';

// render_monthly_presentation.mjs as reviewed; T's adapter pins the same bytes.
export const REFERENCE_SHA256='c3d268fc87340f83ee7f4a568f295082474760f1568d25eb04825e7c1da8b9da';
const STYLES=['alpha-world-ms','alpha-world-t'];

export function compositeStrategyName(snapshot) {
  const profile=snapshot?.report_profile;
  if(!profile || profile.subject_id!==snapshot.subject_id || STYLES.includes(snapshot.subject_id)
     || !STYLES.includes(profile.style) || !String(profile.display_name||'').trim())
    throw Error('A composite deck needs its Alpha World report profile');
  return 'iStrategy: '+String(profile.display_name).trim();
}

// Tables and charts by their one owner slide (zero-based), as T binds them.
const OWNERS={
 'tb/fql4fa5s':2,'tb/gjutwb61':4,'tb/wfmpkre1':5,'tb/edoralwf':8,'tb/qpsbq1o7':9,
 'tb/i1onmh0n':10,'tb/m1gj2xgb':11,'tb/3ets36l8':12,'tb/3mtobupc':13,'tb/a9srud0n':14,'tb/5kb2xwby':15,
 'ch/w7yloryp':5,'ch/g36d8zy1':6,'ch/m9w3y1cn':7,'ch/ut8nmhsr':8,'ch/e94rut4f':9,'ch/2pk76d4z':10
};

const PAGE=`{
  // The composite's own slide 18 and its name sized to every header (ibkr_etl.composite_presentation).
  const compositePath = path.join(stagingDir, \`\${path.basename(finalPath, '.pptx')}.composite.pptx\`);
  const receipt = JSON.parse(execFileSync(process.env.RUNTIME_PYTHON || process.env.PYTHON_BINARY || 'python',
    ['-m', 'ibkr_etl.composite_presentation', 'compose', candidatePath, compositePath, snapshotPath], {encoding: 'utf8'}));
  if (receipt.status !== 'passed' || receipt.named_shapes !== 17) throw new Error('The composite page did not pass');
  candidatePath = compositePath;
}

`;

const WORDING=`
// Fixed-unit wording, the composite's own inception, and notes that describe it.
setText('sh/onmxcv69','Each contribution equals the fixed-unit component value change divided by starting NAV. These are percentage-point effects on return.');
setText('sh/cva9k7a9','Signed component-type effects sum to the monthly NAV return. Bars show magnitudes. Benchmark comparisons are unavailable.');
setText('sh/25sf2d4z',msModel.identity.inception_note);
for (const slide of presentation.slides.items) slide.speakerNotes.textFrame.setText(msModel.identity.speaker_note);

`;

export function adaptReferenceRenderer(source, artifactUrl) {
  let code=source;
  const swap=(from,to,count=1)=>{
    if(code.split(from).length-1!==count)throw Error('Reference renderer text changed: '+from.slice(0,60));
    code=code.replaceAll(from,to);
  };
  // MS's HKD path, for the composite the snapshot's profile names.
  swap("snapshot.subject_id === 'alpha-world-ms'","snapshot.report_profile?.subject_id === snapshot.subject_id");
  swap("['-m','ibkr_etl.alpha_world_ms_presentation',snapshotPath]","['-m','ibkr_etl.composite_presentation',snapshotPath]");
  swap("'alpha-world-ms-presentation-hkd-hibor-v2'","'alpha-world-design-composite-presentation-v1'");
  swap("name: isMsHkd ? 'Alpha World MS' : snapshot.subject_id","name: isMsHkd ? governedDisplayName : snapshot.subject_id");
  swap('["Metric", snapshot.subject_id, primaryBenchmarkName, secondaryBenchmarkName]','["Metric", governedDisplayName, primaryBenchmarkName, secondaryBenchmarkName]');
  // The composite's own sleeves, labelled by direction, never MS's.
  swap("({'hong-kong-china':'HK-China','U16088571':'US-Tech','hang-seng-price-index':'HSI','spy-settlement':'SPY','constant-cash':'Cash'}[row.conid] || row.symbol)",
       "(row.asset_category === 'cash' ? 'Cash' : Number(row.market_value_text) < 0 ? 'Short' : 'Long')");
  swap("({'hong-kong-china':'China & Hong Kong','U16088571':'US Tech & Biotech','hang-seng-price-index':'Hang Seng Index','spy-settlement':'S&P500 (SPY)','constant-cash':'Constant cash'}[row.conid] || row.description || row.symbol)",
       "(row.description || row.symbol)");
  // Composite contributions are fixed-unit value changes, not Frongello-linked.
  swap('Frongello-linked contribution to return','Fixed-unit contribution to return');
  swap('are Frongello-linked','use component value changes divided by starting NAV');
  swap('Equity contribution','Risky-sleeve contribution',2);
  // The composite's own scales: a sleeve type above MS's 70%, and borrowed cash below zero.
  swap('xAxis: { numberFormatCode: "0%", min: 0, max: 0.7,',
       'xAxis: { numberFormatCode: "0%", min: 0, max: Math.max(0.7, Math.ceil(Math.max(...sector.allocation.map(row => Number(row.weight))) * 10) / 10),');
  swap("numberFormatCode: isMsHkd ? '\"HKD \"0' : \"$0\",\n    min: 0,",
       "numberFormatCode: isMsHkd ? '\"HKD \"0' : \"$0\",\n    min: Math.min(0, Math.floor(msModel.constant_cash / 10) * 10),");
  swap('scripts/audit_alpha_ms_presentation.py','scripts/audit_composite_presentation.py');
  swap('if (isMsHkd) {\n  const workbookPath',PAGE+'if (isMsHkd) {\n  const workbookPath');
  swap('const outputDir = path.dirname(path.resolve(finalPath));',WORDING+'const outputDir = path.dirname(path.resolve(finalPath));');
  // As T's adapter: explicit line styles, and objects bound by owner slide.
  swap('line: { fill:',"line: { style: 'solid', fill:",code.split('line: { fill:').length-1);
  swap('chartLine: { fill:',"chartLine: { style: 'solid', fill:",code.split('chartLine: { fill:').length-1);
  swap('presentation.resolve(','resolveC(',11);
  swap('tableDimensions.get(id)','tableDimensions.get(cIds.get(id)||id)');
  swap("tableDimensions.get('tb/gjutwb61')","tableDimensions.get(cIds.get('tb/gjutwb61'))");
  const imported='const presentation = await PresentationFile.importPptx(await FileBlob.load(sourcePath));';
  swap(imported,imported+`
const cInspection=(await presentation.inspect({kind:'table,chart',maxChars:1000000})).ndjson.split('\\n').filter(Boolean).map(JSON.parse);
const cIds=new Map((${bindTemplateObjectIds.toString()})(cInspection,${JSON.stringify(OWNERS)}));
const resolveC=id=>presentation.resolve(cIds.get(id)||id);
`);
  swap('from "@oai/artifact-tool"','from '+JSON.stringify(artifactUrl));
  return code;
}
