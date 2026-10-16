"""Owner-only controls. Browser input cannot upload or execute renderer code."""
from html import escape
from urllib.parse import quote


def designer_link(subject_id):
    """A link to the factsheet designer while FACTSHEET_DESIGN_AGENTS_ENABLED is on; nothing otherwise."""
    from .factsheet_design_web import enabled
    if not enabled():
        return ''
    href=escape('/accounts/'+quote(subject_id,safe='')+'/factsheet-designer',quote=True)
    return (f'<p><a class="button" href="{href}">Design a new factsheet from a template</a> '
            '<span class="muted">Upload the template; the design agents draft it and you review it.</span></p>')


def controls(subject_id):
    endpoint=escape('/api/accounts/'+quote(subject_id,safe='')+'/reporting/design-updates',quote=True)
    return f'''<section id="report-design-controls" data-endpoint="{endpoint}" style="margin-top:28px;border-top:1px solid #dce2eb;padding-top:20px">
<h3>Report design updates</h3>
<p class="muted">Request changes after onboarding. A qualified renderer/template can regenerate all historical months and apply to future reports. Replacements become current downloads only after you review and approve them. Prior files remain archived for audit and rollback. Original IBKR data stays unchanged.</p>
{designer_link(subject_id)}<form id="report-design-request-form"><label for="report-design-description">Describe your layout or wording changes</label>
<textarea id="report-design-description" minlength="10" maxlength="4000" rows="4" required style="display:block;width:100%;margin:10px 0" placeholder="For example: change the chart legend font and update the disclaimer spacing."></textarea>
<button class="button" type="submit">Request design changes</button></form>
<div id="report-design-message" class="muted" role="status" aria-live="polite" style="margin-top:12px"></div>
<div id="report-design-requests" style="margin-top:16px"></div>
<label for="report-design-version">Qualified installed design</label><select id="report-design-version" aria-label="Qualified installed report design" disabled></select>
<button id="report-design-preview" class="button" type="button" disabled>Preview historical regeneration</button>
<p id="report-design-plan" class="muted"></p>
<label><input id="report-design-consent" type="checkbox" disabled> Apply this qualified design to all past and future monthly reports. Keep current downloads until replacements pass validation and owner approval.</label>
<div class="actions"><button id="report-design-queue" class="button" type="button" disabled>Regenerate historical reports</button></div>
</section>'''


SCRIPT=r'''<script>
(()=>{
const root=document.getElementById('report-design-controls');if(!root)return;
const endpoint=root.dataset.endpoint,form=document.getElementById('report-design-request-form'),description=document.getElementById('report-design-description'),message=document.getElementById('report-design-message'),list=document.getElementById('report-design-requests'),version=document.getElementById('report-design-version'),preview=document.getElementById('report-design-preview'),planText=document.getElementById('report-design-plan'),consent=document.getElementById('report-design-consent'),queue=document.getElementById('report-design-queue');
let state=null,plan=null,requestId=null,submissionId=null,submissionDescription=null,previewSerial=0;
const call=async(url,options)=>{const r=await fetch(url,options),data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${r.status}`);return data};
const post=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const invalidate=()=>{previewSerial++;plan=null;planText.textContent='';consent.checked=false;consent.disabled=true;queue.disabled=true};
const load=async()=>{invalidate();state=await call(endpoint);list.replaceChildren();requestId=null;
for(const item of state.requests){const line=document.createElement('p');line.textContent=`${item.description} — ${item.status.replaceAll('_',' ')}`;list.append(line);if(item.status==='requested'&&!requestId)requestId=item.request_id}
version.replaceChildren();for(const item of state.designs){const option=document.createElement('option');option.value=item.design_version;option.textContent=item.design_version;version.append(option)}
version.disabled=!state.regeneration_enabled||!state.designs.length;preview.disabled=version.disabled||!requestId;
if(!state.regeneration_enabled)message.textContent='Design requests are available. Regeneration remains disabled until the Azure worker and release controls are qualified.';
else if(!state.designs.length)message.textContent='No qualified installed design is available yet. Your requested changes need validation and deployment first.';
};
form.addEventListener('submit',async event=>{event.preventDefault();const value=description.value.trim();if(value.length<10)return;if(value!==submissionDescription){submissionId=crypto.randomUUID();submissionDescription=value}const button=form.querySelector('button');button.disabled=true;invalidate();try{await call(endpoint,post({request_id:submissionId,description:value}));description.value='';submissionId=null;submissionDescription=null;await load();message.textContent='Design change requested. This has not changed any reports.'}catch(e){message.textContent='Could not confirm submission: '+e.message+'. Retry with the same description to recover the same request.'}finally{button.disabled=false}});
version.addEventListener('change',invalidate);
preview.addEventListener('click',async()=>{invalidate();const serial=previewSerial;preview.disabled=true;try{const candidate=await call(endpoint+'/preview?design_version='+encodeURIComponent(version.value));if(serial!==previewSerial)return;plan=candidate;planText.textContent=`${plan.months} historical months (${plan.first_period} to ${plan.last_period}). All frozen source observations stay unchanged. Existing downloads remain available during validation and review.`;consent.disabled=false}catch(e){message.textContent=e.message}finally{preview.disabled=!state?.regeneration_enabled||!requestId||version.disabled}});
consent.addEventListener('change',()=>{queue.disabled=!plan||!consent.checked});
queue.addEventListener('click',async()=>{if(!plan||!consent.checked||!requestId)return;queue.disabled=true;try{await call(endpoint+'/'+requestId+'/queue',post({design_version:plan.design_version,plan_sha256:plan.plan_sha256,replace_prior_releases:true,apply_to_future:true}));await load();message.textContent='Regeneration queued. Review each replacement pair before approving it in the Report centre.'}catch(e){invalidate();message.textContent='Could not confirm queuing: '+e.message+'. Refresh before retrying.'}});
load().catch(e=>{form.querySelector('button').disabled=true;preview.disabled=true;queue.disabled=true;message.textContent='Report design controls are unavailable: '+e.message});
})();
</script>'''
