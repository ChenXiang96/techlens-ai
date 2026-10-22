/* No HTML interpretation, external fetch, persistence of capability, or provider access. */
"use strict";
const $ = id => document.getElementById(id);
const state = {boot:null, run:null, runId:null, plan:null, review:null, report:null, full:false, last:null, polling:false,
  reading:0, bootReading:0, selection:0, submitting:false};
function visible(text) { return String(text).replace(/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/gu,c=>"\\u"+c.codePointAt(0).toString(16).padStart(4,"0")); }
function node(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=tag==="pre"?String(text).split("\n").map(visible).join("\n"):visible(text); if(cls)n.className=cls; return n; }
function data(value) { return node("pre",JSON.stringify(value,null,2),"data"); }
function message(text,error=false) { $("message").textContent=text; $("message").className=error?"error":""; }
async function api(path,options={}) {
  const headers={...(options.headers||{})}; if(state.boot)headers["X-TechLens-CSRF"]=state.boot.csrf;
  const r=await fetch(path,{...options,headers,credentials:"same-origin",cache:"no-store",redirect:"error"});
  if(!r.ok) { const v=await r.json(); throw new Error((v.error||"Local operation unavailable.")+(r.status===409?" Refresh retained state; no newer proposal was substituted.":"")); } return r;
}
async function read(path) { return (await api(path)).json(); }
async function bootstrap() { const generation=++state.bootReading;const boot=await read("/api/bootstrap");if(generation===state.bootReading){state.boot=boot;renderRuns();}return boot; }
function renderRuns(){ const pick=$("run-picker");pick.replaceChildren(node("option","Choose a retained run"));pick.firstChild.value="";
  for(const r of state.boot.runs){const o=node("option",`${r.scenario} · ${r.phase} · ${r.id.slice(0,8)}`);o.value=r.id;pick.append(o);}pick.value=state.runId||""; }
async function submit(path,token,revision,payload={},action) {
  const body={token,revision,data:payload};if(action)body.action=action;
  const submission={path,body:JSON.parse(JSON.stringify(body))};state.last=submission;return sendLast(submission);
}
async function sendLast(submission=state.last){if(!submission)throw new Error("No action has been submitted in this tab.");
  const selection=state.selection;const r=await readMutation(submission.path,submission.body);
  if(selection!==state.selection)return r; // A delayed mutation never changes a newer user selection.
  if(r.run_id){state.runId=r.run_id;sessionStorage.setItem("techlens-run",r.run_id);}
  if(r.request_id){sessionStorage.setItem("techlens-request",r.request_id);await prepared(r.request_id);}
  await bootstrap();if(selection!==state.selection)return r;if(state.runId)await refresh();if(selection===state.selection)message(`Action ${r.operation}: ${r.status}. No automatic retry or replay.`);return r;
}
async function readMutation(path,body){return(await api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})).json();}
function guarded(fn){return async event=>{if(event)event.preventDefault();const selection=state.selection;try{await fn();}catch(e){if(selection===state.selection)message(e.message,true);}};}
function actionGuard(fn){return guarded(async()=>{if(state.submitting)return;state.submitting=true;$("action-status").textContent="Submission in progress. Overlapping submissions are ignored; no automatic retry.";
  for(const id of["request-form","start-demo","retry-action","plan-editor","saved-form"])$(id).setAttribute("aria-busy","true");
  try{return await fn();}finally{state.submitting=false;$("action-status").textContent="No submission in progress.";for(const id of["request-form","start-demo","retry-action","plan-editor","saved-form"])$(id).setAttribute("aria-busy","false");}});}
function button(text,fn){const b=node("button",text);b.type="button";b.addEventListener("click",guarded(fn));return b;}
function input(label,value="",maximum=256,tag="input"){const l=node("label",label);const i=node(tag);i.value=value;i.maxLength=maximum;l.append(i);return {label:l,input:i};}
function listEditor(container,title,values,maximum=20,itemLength=256){container.replaceChildren(node("h4",title));const rows=node("div");container.append(rows);
  function add(value=""){if(rows.children.length>=maximum)throw new Error(`Maximum ${maximum} items.`);const row=node("div",undefined,"row");const field=node("input");field.value=value;field.maxLength=itemLength;field.setAttribute("aria-label",`${title} item`);row.append(field,button("Remove",()=>row.remove()));rows.append(row);}
  for(const value of values)add(value);container.append(button("Add item",()=>add()));container.values=()=>[...rows.querySelectorAll("input")].map(i=>i.value);}
function candidate(value={}){const root=$("candidate-cards");if(root.children.length>=3)throw new Error("Maximum 3 candidates.");const card=node("fieldset");card.append(node("legend",`Candidate ${root.children.length+1}`));
  for(const [k,label,max] of [["name","Display name",256],["repository_url","GitHub repository root URL",2048],["homepage","Homepage (optional HTTP/HTTPS)",2048]]){const f=input(label,value[k]||"",max);f.input.dataset.field=k;card.append(f.label);}
  card.append(button("Remove candidate",()=>{if(root.children.length===1)throw new Error("At least one candidate is required.");card.remove();[...root.children].forEach((c,i)=>c.querySelector("legend").textContent=`Candidate ${i+1}`);}));root.append(card);}
const contextLabels={primary_languages:"Primary languages",backend_stack:"Backend stack",priorities:"Priorities"};
const descriptions={ai_experience:"AI experience",deployment_environment:"Deployment environment",production_criticality:"Production criticality",framework_churn_tolerance:"Framework churn tolerance"};
candidate();for(const [key,label]of Object.entries(contextLabels)){const c=node("div");c.id=`list-${key}`;$("context-lists").append(c);listEditor(c,label,[]);}
for(const [key,label]of Object.entries(descriptions)){const f=input(label,"",1024);f.input.id=`context-${key}`;$("context-descriptions").append(f.label);}
async function prepare(){const selection=state.selection;const f={assessment_id:$("assessment-id").value,candidates:[...$("candidate-cards").children].map(c=>Object.fromEntries([...c.querySelectorAll("input")].map(i=>[i.dataset.field,i.value]))),team_size:$("team-size").value,
  max_research_rounds:$("rounds").value,max_web_searches:$("searches").value,max_run_cost_usd:$("budget").value};
  for(const k of Object.keys(contextLabels))f[k]=$(`list-${k}`).values();for(const k of Object.keys(descriptions))f[k]=$(`context-${k}`).value;
  const boot=await bootstrap();if(selection!==state.selection)return;await submit("/api/requests/prepare",boot.actions.prepare,boot.revision,f);}
async function prepared(id){const selection=state.selection;const value=await read(`/api/requests/${id}`);if(selection!==state.selection)return;$("prepared-summary").replaceChildren(data(value));$("prepared").hidden=false;$("download-request").onclick=guarded(()=>download(`/api/requests/${id}/download`,"techlens-request.json"));}
async function download(path,name){const r=await api(path);const url=URL.createObjectURL(await r.blob());const a=node("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function journey(demo){const saved=demo==="saved";$("request-journey").hidden=demo!==false;$("demo-journey").hidden=demo!==true;$("saved-journey").hidden=!saved;$("demo-notice").hidden=saved;$("request-tab").setAttribute("aria-pressed",String(demo===false));$("demo-tab").setAttribute("aria-pressed",String(demo===true));$("saved-tab").setAttribute("aria-pressed",String(saved));}
async function refresh(){const rid=state.runId,generation=++state.reading,selection=state.selection;if(!rid)return;
  const current=()=>state.runId===rid&&state.reading===generation&&state.selection===selection;
  let r=await read(`/api/runs/${rid}`);if(!current())return;
  let plan=null,report=null;
  if(r.phase==="awaiting_review"){plan=await read(`/api/runs/${rid}/plan`);if(!current())return;
    const binding=plan.review;if(!binding||binding.id!==rid||binding.phase!=="awaiting_review"||binding.proposal_id!==(plan.proposal?plan.proposal.id:null)){
      state.review=null;$("plan-panel").hidden=true;throw new Error("Review changed while loading. Refresh retained state before deciding.");}r=binding;}
  if(r.phase==="terminal"){report=await read(`/api/runs/${rid}/report`);if(!current())return;}
  // No await below: displayed plan/proposal and its control binding install together.
  state.run=r;state.plan=plan;state.review=plan?{run:r,plan}:null;state.report=report;
  $("run-picker").value=rid;$("lifecycle").textContent=`${r.scenario} · ${r.phase}${r.abandoned?" · abandoned":""}. ${r.notice}`;
  $("abandon-run").hidden=!r.actions.abandon;if(r.phase!=="awaiting_review")$("plan-panel").hidden=true;if(r.phase!=="terminal")$("report-panel").hidden=true;
  if(plan){$("plan-display").replaceChildren(data(plan.candidates),data(plan.research_plan));
    $("proposal-panel").hidden=!state.plan.proposal;if(state.plan.proposal){$("proposal-diff").replaceChildren(...state.plan.proposal.changes.map(c=>{const div=node("div",undefined,c.changed?"changed":"record");div.append(node("h4",`${c.field} · ${c.changed?"changed":"unchanged"}`),data({before:c.before,after:c.after}));return div;}));}$("plan-panel").hidden=false;}
  if(report){renderReport();renderDownloads();$("report-panel").hidden=false;}}
function editPlan(){const p=state.plan.research_plan;$("plan-editor").hidden=false;$("edit-objective").value=p.objective;listEditor($("edit-questions"),"Research questions",p.research_questions,20,2048);listEditor($("edit-checks"),"Engineering checks",p.engineering_checks,20,2048);$("edit-tasks").replaceChildren();for(const t of p.web_tasks)addTask(t);}
function addTask(t={candidate_id:null,query:"",purpose:""}){const root=$("edit-tasks");if(root.children.length>=state.plan.research_plan.max_searches)throw new Error("Original web-search limit reached.");const card=node("fieldset");card.append(node("legend","Web task · ID assigned by position"));const label=node("label","Candidate association");const select=node("select");select.dataset.field="candidate_id";const cross=node("option","Cross-candidate (None)");cross.value="";select.append(cross);for(const c of state.plan.candidates){const o=node("option",`${c.candidate_id} — ${c.name}`);o.value=c.candidate_id;select.append(o);}select.value=t.candidate_id||"";label.append(select);card.append(label);
  for(const k of["query","purpose"]){const f=input(k==="query"?"Query":"Purpose",t[k],4096,"textarea");f.input.dataset.field=k;card.append(f.label);}card.append(button("Remove task",()=>card.remove()),button("Move earlier",()=>{if(card.previousElementSibling)root.insertBefore(card,card.previousElementSibling);}));root.append(card);}
async function preview(){const value={objective:$("edit-objective").value,research_questions:$("edit-questions").values(),engineering_checks:$("edit-checks").values(),web_tasks:[...$("edit-tasks").children].map(c=>Object.fromEntries([...c.querySelectorAll("select,textarea")].map(f=>[f.dataset.field,f.dataset.field==="candidate_id"?(f.value||null):f.value])))};
  const selection=state.selection;const r=reviewBinding();await submit(`/api/runs/${r.id}/preview`,r.actions.preview,r.revision,value);if(selection===state.selection)$("plan-editor").hidden=true;}
function reviewBinding(){const view=state.review;if(!view||view.run!==state.run||view.plan!==state.plan||view.run.id!==state.runId||view.run.proposal_id!==(view.plan.proposal?view.plan.proposal.id:null)||$("plan-panel").hidden)throw new Error("Review binding unavailable. Refresh retained state.");return view.run;}
async function decision(action){const r=reviewBinding();if(!r.actions[action])throw new Error("Action unavailable. Refresh retained state.");await submit(`/api/runs/${r.id}/decision`,r.actions[action],r.revision,action==="apply"?{proposal_id:state.review.plan.proposal.id}:{},action);}
function renderReport(){const v=state.report,r=v.report;$("report-heading").textContent=`Validated ${r.status} report · synthetic ${v.scenario}`;const root=$("report-display");root.replaceChildren(node("p",r.summary),data({assessment_id:r.assessment_id,candidate_ids:r.candidate_ids,candidate_names:r.candidate_names,candidates:v.candidates,team_profile:r.team_profile,priorities:r.priorities}),node("h4","Candidate analysis and comparison"),data({analyses:r.analyses,comparison:r.comparison}));
  root.append(node("h4","Claim → evidence references"));for(const claim of r.claims){const div=node("div",undefined,"record");div.append(node("h4",claim.claim_id),node("p",claim.statement),data({candidate_ids:claim.candidate_ids,evidence_ids:claim.evidence_ids}));root.append(div);}
  root.append(node("h4","13 report sections"));for(const s of r.sections){const div=node("div",undefined,"record");div.append(node("h4",`${s.number}. ${s.title} · ${s.status}`));div.append(data({claim_ids:s.claim_ids,risk_flag_ids:s.risk_flag_ids,limitation_ids:s.limitation_ids}));if(state.full)div.append(node("p",s.text));root.append(div);}
  root.append(node("h4","Risk observations and provenance"),data(r.risk_flags),node("h4","Source URLs (display only; no fetching)"),data(r.sources),node("h4","Research notes and limitations"),data(r.research_notes),node("h4","Accounting and completeness"),data(r.metrics));
  $("report-toggle").textContent=state.full?"Show summary report":"Show full report";$("report-toggle").setAttribute("aria-pressed",String(state.full));}
function renderDownloads(){const r=state.run;$("downloads").replaceChildren();for(const[k,name]of[["report.json","report.json"],["report.md","report.md"],["bundle","techlens-report.zip"]])if(r.downloads.includes(k))$("downloads").append(button(`Download ${name}`,()=>download(`/api/runs/${r.id}/downloads/${k}`,name)));$("retry-artifacts").hidden=!r.actions.artifacts;}
$("request-tab").onclick=()=>{++state.selection;journey(false);};$("demo-tab").onclick=()=>{++state.selection;journey(true);};
$("add-candidate").onclick=guarded(()=>candidate());$("request-form").onsubmit=actionGuard(prepare);
$("start-demo").onclick=actionGuard(async()=>{const selection=state.selection,scenario=$("scenario").value;const boot=await bootstrap();if(selection!==state.selection)return;await submit("/api/demos/start",boot.actions.start,boot.revision,{scenario});});
$("run-picker").onchange=guarded(async()=>{++state.selection;++state.reading;state.runId=$("run-picker").value;state.run=null;state.plan=null;state.review=null;$("plan-panel").hidden=true;$("report-panel").hidden=true;$("abandon-run").hidden=true;$("plan-editor").hidden=true;sessionStorage.setItem("techlens-run",state.runId);await refresh();});
$("refresh-run").onclick=guarded(refresh);$("retry-action").onclick=actionGuard(()=>sendLast());$("edit-plan").onclick=guarded(editPlan);$("add-task").onclick=guarded(()=>addTask());$("plan-editor").onsubmit=actionGuard(preview);
for(const[a,id]of[["approve","approve-plan"],["reject","reject-plan"],["apply","apply-proposal"],["cancel","cancel-proposal"]])$(id).onclick=actionGuard(()=>decision(a));
$("abandon-run").onclick=actionGuard(async()=>submit(`/api/runs/${state.runId}/abandon`,state.run.actions.abandon,state.run.revision));
$("retry-artifacts").onclick=actionGuard(async()=>submit(`/api/runs/${state.runId}/artifacts/retry`,state.run.actions.artifacts,state.run.revision));
$("download-plan").onclick=guarded(()=>download(`/api/runs/${state.runId}/downloads/plan`,"techlens-plan.json"));
$("report-toggle").onclick=()=>{state.full=!state.full;renderReport();};
guarded(async()=>{await bootstrap();const saved=sessionStorage.getItem("techlens-run");if(saved){state.runId=saved;journey(true);await refresh();}const id=sessionStorage.getItem("techlens-request");if(id)await prepared(id);})();
setInterval(async()=>{if(state.polling||!state.run||!state.run.active)return;state.polling=true;try{await refresh();}catch(e){message(e.message,true);}finally{state.polling=false;}},700);

// Saved views have separate identities, revisions and response generations. No
// upload, saved identifier or action capability is persisted in browser storage.
const savedState={listing:null,id:null,view:null,full:false,reading:0,listingReading:0,fileGeneration:0};
$("saved-files").onchange=()=>{++savedState.fileGeneration;++state.selection;++savedState.reading;};
async function savedList(){const generation=++savedState.listingReading,selection=state.selection;const listing=await read("/api/saved-reports");if(generation!==savedState.listingReading||selection!==state.selection)return;
  savedState.listing=listing;const picker=$("saved-picker");picker.replaceChildren(node("option","Choose an opened saved report"));picker.firstChild.value="";
  for(const v of listing.views){const option=node("option",`${v.assessment_id} · ${v.status} · ${v.id.slice(0,14)}`);option.value=v.id;picker.append(option);}picker.value=savedState.id||"";
  if(savedState.id&&!listing.views.some(v=>v.id===savedState.id)){savedState.id=null;savedState.view=null;++savedState.reading;$("saved-panel").hidden=true;}}
async function savedRead(){const id=savedState.id,generation=++savedState.reading,selection=state.selection;if(!id)return;
  const view=await read(`/api/saved-reports/${id}`);if(id!==savedState.id||generation!==savedState.reading||selection!==state.selection)return;
  savedState.view=view;renderSaved();$("saved-panel").hidden=false;}
function renderSaved(){const v=savedState.view,r=v.snapshot.report;$("saved-report-heading").textContent=`Imported ${r.status} saved report — read-only`;
  $("saved-checks").textContent=v.notice;$("saved-provenance").replaceChildren(node("h4","File-declared provenance (not authenticated)"),data({schema_version:v.snapshot.schema_version,mode:v.snapshot.mode,provenance:v.snapshot.provenance}),data(v.checks));
  const root=$("saved-display");root.replaceChildren(node("p",r.summary),data({assessment_id:r.assessment_id,status:r.status,candidate_ids:r.candidate_ids,candidate_names:r.candidate_names,candidates:v.snapshot.candidates,team_profile:r.team_profile,priorities:r.priorities}),node("h4","Candidate analysis and comparison"),data({analyses:r.analyses,comparison:r.comparison}));
  root.append(node("h4","Claim → evidence references"));for(const claim of r.claims){const div=node("div",undefined,"record");div.append(node("h4",claim.claim_id),node("p",claim.statement),data({candidate_ids:claim.candidate_ids,evidence_ids:claim.evidence_ids}));root.append(div);}
  root.append(node("h4","13 report sections"));for(const s of r.sections){const div=node("div",undefined,"record");div.append(node("h4",`${s.number}. ${s.title} · ${s.status}`),data({claim_ids:s.claim_ids,risk_flag_ids:s.risk_flag_ids,limitation_ids:s.limitation_ids}));if(savedState.full)div.append(node("p",s.text));root.append(div);}
  root.append(node("h4","Risk observations and provenance"),data(r.risk_flags),node("h4","Source URLs (display only; no fetching)"),data(r.sources),node("h4","Research notes and limitations"),data(r.research_notes),node("h4","Accounting and completeness (file-declared)"),data(r.metrics));
  $("saved-toggle").textContent=savedState.full?"Show saved summary":"Show full saved report";$("saved-toggle").setAttribute("aria-pressed",String(savedState.full));}
$("saved-tab").onclick=guarded(async()=>{++state.selection;journey("saved");await savedList();if(savedState.id)await savedRead();});
$("saved-picker").onchange=guarded(async()=>{++state.selection;++savedState.reading;savedState.id=$("saved-picker").value;savedState.view=null;$("saved-panel").hidden=true;await savedRead();});
$("saved-refresh").onclick=guarded(async()=>{await savedList();if(savedState.id)await savedRead();});
$("saved-toggle").onclick=()=>{savedState.full=!savedState.full;renderSaved();};
$("saved-close").onclick=actionGuard(async()=>{const view=savedState.view,selection=state.selection;if(!view)throw new Error("No saved view selected.");
  await readMutation(`/api/saved-reports/${view.id}/close`,{token:view.close_token,revision:view.revision,data:{}});
  if(selection!==state.selection||savedState.id!==view.id)return;++savedState.reading;savedState.id=null;savedState.view=null;$("saved-panel").hidden=true;await savedList();});
$("saved-form").onsubmit=actionGuard(async()=>{const selection=state.selection,fileGeneration=savedState.fileGeneration;
  const current=()=>selection===state.selection&&fileGeneration===savedState.fileGeneration;let payload=null;try{
  const chosen=[...$("saved-files").files],zip=chosen.length===1&&chosen[0].name.endsWith(".zip");
  if(!zip&&(chosen.length!==3||new Set(chosen.map(f=>f.name)).size!==3||!chosen.every(f=>["report.json","report.md","manifest.json"].includes(f.name))))throw new Error("Choose one ZIP or exactly report.json, report.md and manifest.json.");
  const files=[];for(const file of chosen){const limit=zip?5*1024**2:file.name==="manifest.json"?8192:2*1024**2;if(file.size>limit)throw new Error("Selected saved file exceeds its size limit.");
    const bytes=new Uint8Array(await file.arrayBuffer());if(!current())return;let encoded="";for(let i=0;i<bytes.length;i+=32768)encoded+=String.fromCharCode(...bytes.subarray(i,i+32768));files.push({name:file.name,base64:btoa(encoded)});}
  await savedList();if(!current())return;const listing=savedState.listing;payload={token:listing.open_token,revision:listing.revision,data:{format:zip?"zip":"files",files}};
  const receipt=await readMutation("/api/saved-reports/open",payload);if(!current())return;if(receipt.status!=="opened")throw new Error(receipt.error||"Saved report was not opened.");
  savedState.id=receipt.saved_id;savedState.full=false;await savedList();if(!current())return;await savedRead();if(current())message("Saved file checks completed. Read-only snapshot; no assessment was started.");
}finally{payload=null;if(fileGeneration===savedState.fileGeneration)$("saved-files").value="";}});
