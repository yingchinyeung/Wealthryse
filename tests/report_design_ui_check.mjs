// DOM-level interaction test; this does not substitute for staging visual QA.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { execFileSync } from 'node:child_process';
import { webcrypto } from 'node:crypto';

const source=execFileSync(process.argv[2],['-c',
    'from ibkr_etl.report_design_ui import SCRIPT;print(SCRIPT.removeprefix("<script>").removesuffix("</script>"))'],
    {encoding:'utf8'});
class Element {
    constructor(){this.disabled=false;this.value='';this.textContent='';this.children=[];this.listeners={};this.checked=false}
    addEventListener(name,handler){this.listeners[name]=handler}
    replaceChildren(){this.children=[];this.value=''}
    append(child){this.children.push(child);if(!this.value)this.value=child.value??''}
    querySelector(){return this.button??=(new Element())}
}
const ids=['controls','request-form','description','message','requests','version','preview','plan','consent','queue'];
const elements=Object.fromEntries(ids.map(id=>['report-design-'+id,new Element()]));
elements['report-design-controls'].dataset={endpoint:'/synthetic/design-updates'};
let requests=[],calls=[],previewResolver=null;
const state=()=>({requests,designs:[{design_version:'v2'}],regeneration_enabled:true});
const fetch=async(url,options)=>{
    calls.push({url,options});let data;
    if(url.includes('/preview?')) {
        data={design_version:'v2',plan_sha256:'a'.repeat(64),months:21,first_period:'2025-01-31',last_period:'2026-09-30'};
        if(previewResolver)await previewResolver();
    } else if(url.endsWith('/queue')) {
        const consent=JSON.parse(options.body);assert.equal(consent.replace_prior_releases,true);
        assert.equal(consent.apply_to_future,true);requests[0].status='queued';data=requests[0];
    } else if(options){data=JSON.parse(options.body);data.status='requested';requests.unshift(data)}
    else data=state();
    return {ok:true,json:async()=>data};
};
vm.runInNewContext(source,{document:{getElementById:id=>elements[id],createElement:()=>new Element()},
    fetch,crypto:webcrypto,console});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const item=id=>elements['report-design-'+id];
await settle();assert.equal(item('preview').disabled,true);assert.equal(item('queue').disabled,true);
item('description').value='<img src=x onerror=alert(1)> Improve chart legends';
await item('request-form').listeners.submit({preventDefault(){}});
assert.equal(item('requests').children[0].textContent.includes('<img'),true);
assert.equal(item('preview').disabled,false);
await item('preview').listeners.click();assert.equal(item('consent').disabled,false);
assert.equal(item('queue').disabled,true);assert.match(item('plan').textContent,/21 historical months/);
item('consent').checked=true;item('consent').listeners.change();assert.equal(item('queue').disabled,false);
item('version').listeners.change();assert.equal(item('queue').disabled,true);assert.equal(item('consent').checked,false);
await item('preview').listeners.click();item('consent').checked=true;item('consent').listeners.change();
await item('queue').listeners.click();assert.equal(requests[0].status,'queued');assert.equal(item('queue').disabled,true);
assert.equal(calls.filter(c=>c.url.endsWith('/queue')).length,1);
// An in-flight preview cannot restore consent after the selected version changes.
item('description').value='Improve another chart legend';await item('request-form').listeners.submit({preventDefault(){}});
let resume;previewResolver=()=>new Promise(resolve=>{resume=resolve});
const pending=item('preview').listeners.click();await settle();item('version').listeners.change();resume();await pending;
assert.equal(item('consent').disabled,true);assert.equal(item('queue').disabled,true);
console.log('Report design UI: request, preview, consent, queue, escaping and stale-preview checks passed');
