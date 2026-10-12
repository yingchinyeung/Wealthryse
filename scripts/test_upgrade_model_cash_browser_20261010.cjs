// Isolated headless UI test. Every request is fulfilled locally; no real sign-in.
const {createRequire}=require('node:module');
const path=require('node:path');const fs=require('node:fs');const {spawnSync}=require('node:child_process');
const bundled=createRequire('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/package.json');
const {chromium}=bundled('playwright');
const root=path.resolve(__dirname,'..');const revision=process.argv.includes('--v3')?3:process.argv.includes('--v2')?2:1;const candidateRoot='.work/platform-upgrade-20261010-stage26/model-cash-v'+revision;const out=path.join(root,candidateRoot,'browser-v1');
if(fs.existsSync(out))throw Error('Preserve previous browser evidence');fs.mkdirSync(out);
const runtime=path.join(root,candidateRoot,'local-runtime');
function html(subject){
 const code="from ibkr_etl import web;print(web._render_account_page({'email':'synthetic-viewer@example.invalid','role':'viewer'},account_id='"+subject+"',section='cash',access={'display_name':'"+(subject==='U16088571'?'Synthetic broker account':subject==='alpha-world-t'?'Alpha World T':'Alpha World MS')+"','reporting_currency':'"+(subject==='U16088571'?'USD':'HKD')+"','history_start_date':'2025-01-20','preview_mode':False}))";
 const p=spawnSync(path.join(root,'.work/venv-test/Scripts/python.exe'),['-c',code],{cwd:root,env:{...process.env,PYTHONPATH:path.join(runtime,'src'),PYTHONUTF8:'1'},encoding:'utf8',maxBuffer:4*1024*1024});
 if(p.status!==0)throw Error(p.stderr);return p.stdout;
}
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});const results=[];
 try{
 for(const subject of ['alpha-world-t','alpha-world-ms','U16088571']){
  const source=html(subject);const model=subject!=='U16088571';
  for(const width of [375,768,1440]){
   const context=await browser.newContext({viewport:{width,height:1000},deviceScaleFactor:1});const page=await context.newPage();const errors=[];const unknown=[];
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url());const p=url.pathname;let body;
    if(url.hostname!=='ui-fixture.invalid'){unknown.push(url.origin);return route.abort();}
    if(p.startsWith('/accounts/'))return route.fulfill({contentType:'text/html',body:source});
    if(p.endsWith('/available-dates'))body={dates:['2026-09-30'],latest:'2026-09-30'};
    else if(p.endsWith('/benchmark-status'))body={benchmarks:[]};
    else if(p==='/api/agent/tools')body={tools:[],ai_model:{enabled:false,message:'Synthetic UI acceptance only'},admin_actions:{enabled:false,message:'',approval_ttl_minutes:10,actions:[]}};
    else if(p.endsWith('/reporting/monthly'))body={message:'Synthetic reports only',ready_months:0,review_ready_months:0,months:[]};
    else if(p==='/api/agent/conversations')body={conversations:[]};
    else if(p.endsWith('/summary'))body={latest_nav:{total_text:'142',currency:model?'HKD':'USD'},nav_coverage:{observations:2},current_positions:5};
    else if(p.endsWith('/cash'))body=model?{balances_date:'2026-09-30',balances:[],transactions:[],total_count:0,activity_applicable:false,message:'Fixed model cash in normalized NAV units, not an HKD broker balance.',model_cash:{normalized_value:58,nav_weight:58/142,currency_context:'HKD'}}:{balances_date:'2026-09-30',balances:[{currency:'USD',ending_cash_text:'100',settled_cash_text:'95'}],transactions:[],total_count:0,activity_from:'2025-01-20',activity_to:'2026-09-30'};
    else{unknown.push(p);return route.fulfill({status:404,contentType:'application/json',body:'{"detail":"Unregistered synthetic route"}'});}
    return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
   });
   await page.goto('https://ui-fixture.invalid/accounts/'+subject+'/cash');
   try{await page.waitForFunction(()=>document.querySelector('#cash-balances')?.textContent.trim(),{},{timeout:12000});}
   catch(e){throw Error(JSON.stringify({subject,width,errors,unknown,text:(await page.locator('body').innerText()).slice(-2200)}));}
   const result=await page.evaluate(()=>({bodyWidth:document.documentElement.scrollWidth,viewport:innerWidth,cash:document.querySelector('#cash-balances').textContent,note:document.querySelector('#cash-note').textContent,exportHidden:document.querySelector('#cash-export').hidden,ledgerHidden:document.querySelector('#cash-ledger-controls')?.hidden,tableHidden:document.querySelector('#cash-ledger-table')?.hidden}));
   if(errors.length||unknown.length||result.bodyWidth>width+1||result.exportHidden!==model||model&&(!result.cash.includes('40.85%')||result.cash.includes('58.00 HKD')))throw Error(JSON.stringify({subject,width,errors,unknown,result}));
   if(revision===2&&(result.ledgerHidden!==model||result.tableHidden!==model))throw Error('Model/broker controls are not separated');
   await page.screenshot({path:path.join(out,subject+'-'+width+'.png'),fullPage:true});results.push({subject,width,...result,errors,unknown});await context.close();
  }
 }
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({passed:true,synthetic_only:true,real_viewer_sign_in_verified:false,external_requests:0,results},null,2),{flag:'wx'});console.log(JSON.stringify({passed:true,viewports:results.length,synthetic_only:true,real_viewer_sign_in_verified:false}));
 }finally{await browser.close();}
})().catch(e=>{fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({passed:false,error:e.message}),{flag:'wx'});console.error(e.message);process.exitCode=1;});
