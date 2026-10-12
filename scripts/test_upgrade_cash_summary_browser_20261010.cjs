// Reuse the qualified offline UI harness with an explicit mixed-currency fixture.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const old=path.join(__dirname,'test_upgrade_model_cash_browser_20261010.cjs');
let source=fs.readFileSync(old,'utf8');
function replaceOnce(before,after){if(source.split(before).length!==2)throw Error('Exact browser fixture boundary changed');source=source.replace(before,after);}
replaceOnce("const candidateRoot='.work/platform-upgrade-20261010-stage26/model-cash-v'+revision;","const candidateRoot='.work/platform-upgrade-20261010-stage30/cash-summary-v1';");
if(process.argv.includes('--retry-v2'))replaceOnce("const out=path.join(root,candidateRoot,'browser-v1');","const out=path.join(root,candidateRoot,'browser-v2');");
if(process.argv.includes('--retry-v3'))replaceOnce("const out=path.join(root,candidateRoot,'browser-v1');","const out=path.join(root,candidateRoot,'browser-v3');");
replaceOnce("balances:[{currency:'USD',ending_cash_text:'100',settled_cash_text:'95'}]", "balances:[{currency:'BASE_SUMMARY',ending_cash_text:'-5107.93',settled_cash_text:'-5107.93'},{currency:'USD',ending_cash_text:'100',settled_cash_text:'95'},{currency:'<img src=x onerror=alert(1)>',ending_cash_text:'1',settled_cash_text:'1'}]");
replaceOnce("if(revision===2&&(result.ledgerHidden!==model||result.tableHidden!==model))", "if(result.ledgerHidden!==model||result.tableHidden!==model)");
replaceOnce("await page.screenshot({path:","if(!model&&(!result.cash.includes('Base-currency cash summary')||!result.cash.includes('-5,107.93')||!result.cash.includes('100 USD')||result.cash.includes('BASE_SUMMARY')||!result.cash.includes('currency code not supplied.')||await page.locator('#cash-balances img').count()))throw Error('Summary/ISO-currency/escaping display regression: '+JSON.stringify(result));\n   await page.screenshot({path:");
const compiled=new Module(__filename,module);compiled.filename=__filename;compiled.paths=module.paths;compiled._compile(source,__filename);
