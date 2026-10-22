/* External QA only. Existing Playwright + installed Chrome; no downloads. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const readline = require("node:readline");
const {spawn} = require("node:child_process");
const net = require("node:net");
const crypto = require("node:crypto");
const {chromium} = require(process.env.TECHLENS_PLAYWRIGHT);
const output = process.argv[2];
fs.mkdirSync(output,{recursive:true});
const events=[],errors=[],denied=[],consoleDenials=[],regressions=[];
const fidelity=[],savedFidelity=[],savedUploadRaces=[],savedInvalid=[];
// Playwright uses child pipes. This parent has no permitted outbound socket.
net.Socket.prototype.connect = function(){throw new Error("QA parent denies outbound sockets");};
// Independent deny-only proxy: never resolves/forwards any request.
const proxy=http.createServer((req,res)=>{denied.push("proxy-http");res.writeHead(403);res.end();});
proxy.on("connect",(req,socket)=>{socket.on("error",()=>{});denied.push("proxy-connect");socket.end("HTTP/1.1 403 Forbidden\r\n\r\n");});
proxy.on("clientError",(error,socket)=>{socket.on("error",()=>{});socket.destroy();});
function listen(server){return new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));}
function child(importOnly=false){const p=spawn(process.env.TECHLENS_UI_PYTHON,["-B",path.join(__dirname,"ui_browser_server.py")],{cwd:process.env.TECHLENS_UI_CWD||path.resolve(__dirname,".."),env:{...process.env,PYTHONDONTWRITEBYTECODE:"1",TECHLENS_IMPORT_ONLY:importOnly?"1":"0"},stdio:["pipe","pipe","pipe"]});
  p.stderr.on("data",()=>errors.push("Python child emitted stderr"));const lines=readline.createInterface({input:p.stdout});let first=true;return new Promise((resolve,reject)=>{p.on("error",reject);p.once("exit",c=>{if(first)reject(new Error("Server failed before startup"));});lines.on("line",line=>{if(first){first=false;resolve({p,origin:line,receipt:null,lines});}else{try{events.push({child:JSON.parse(line)});}catch{errors.push("Unexpected child output");}}});});}
async function shutdown(server){const exited=new Promise(resolve=>server.p.once("exit",resolve));server.p.stdin.end("stop\n");assert.equal(await exited,0);}
async function main(){await listen(proxy);const server=await child();assert.match(server.origin,/^http:\/\/127\.0\.0\.1:\d+$/);
  assert(Number(new URL(server.origin).port)>=49152);assert(Number(new URL(server.origin).port)<=65535);
  const browser=await chromium.launch({executablePath:process.env.TECHLENS_CHROME,headless:true,proxy:{server:`http://127.0.0.1:${proxy.address().port}`,bypass:"127.0.0.1"},args:["--disable-background-networking","--disable-component-update","--disable-default-apps","--disable-sync","--metrics-recording-only","--no-first-run","--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]});
  const contexts=[];
  async function pageFor(origin=server.origin){const context=await browser.newContext({serviceWorkers:"block",acceptDownloads:true});contexts.push(context);
    await context.route("**/*",route=>{const url=new URL(route.request().url());if(url.origin!==origin){denied.push("route-denied");return route.abort();}events.push({method:route.request().method(),path:url.pathname});return route.continue();});
    const page=await context.newPage();page.on("pageerror",()=>errors.push("Browser page error"));page.on("console",m=>{if(m.type()==="error"){if(/^Failed to load resource: the server responded with a status of 400/.test(m.text()))consoleDenials.push("HTTP 400 from deliberate invalid-input probe");else if(/^Failed to load resource: the server responded with a status of 409/.test(m.text()))consoleDenials.push("HTTP 409 from deliberate stale-proposal probe");else errors.push("Unexpected browser console error");}});await page.goto(origin);return page;}
  async function demo(page,scenario,decision="approve"){await page.locator("#demo-tab").click();await page.locator("#scenario").selectOption(scenario);await page.locator("#start-demo").click();await page.locator("#plan-panel").waitFor({state:"visible"});await page.locator(decision==="reject"?"#reject-plan":"#approve-plan").click();await page.locator("#report-panel").waitFor({state:"visible"});}
  async function idle(page){await page.waitForFunction(()=>document.querySelector("#start-demo").getAttribute("aria-busy")!=="true");}
  async function keyboardTo(page,selector){for(let i=0;i<100;i++){if(await page.evaluate(s=>document.activeElement.matches(s),selector))return;await page.keyboard.press("Tab");}throw new Error("Required action not keyboard reachable");}
  async function fullReport(page,prefix){await idle(page);if(await page.locator("#report-toggle").getAttribute("aria-pressed")!=="true")await page.locator("#report-toggle").click();
    const dl=page.waitForEvent("download");await page.getByRole("button",{name:"Download report.json",exact:true}).click();const file=path.join(output,`${prefix}-report.json`);await(await dl).saveAs(file);
    const exported=JSON.parse(fs.readFileSync(file,"utf8")),r=exported.report;
    assert((await page.locator("#report-heading").textContent()).includes(`Validated ${r.status} report`));
    const blocks=(await page.locator("#report-display > pre").allTextContents()).map(v=>JSON.parse(v));
    assert.deepEqual(blocks,[{assessment_id:r.assessment_id,candidate_ids:r.candidate_ids,candidate_names:r.candidate_names,candidates:exported.candidates,team_profile:r.team_profile,priorities:r.priorities},
      {analyses:r.analyses,comparison:r.comparison},r.risk_flags,r.sources,r.research_notes,r.metrics]);
    const records=page.locator("#report-display > .record");assert.equal(await records.count(),r.claims.length+13);
    assert.equal(await page.locator("#report-display > p").textContent(),r.summary);
    for(let i=0;i<r.claims.length;i++){const claim=r.claims[i],row=records.nth(i);assert.equal(await row.locator("h4").textContent(),claim.claim_id);assert.equal(await row.locator("p").textContent(),claim.statement);assert.deepEqual(JSON.parse(await row.locator("pre").textContent()),{candidate_ids:claim.candidate_ids,evidence_ids:claim.evidence_ids});}
    for(let i=0;i<13;i++){const s=r.sections[i],row=records.nth(r.claims.length+i);assert.equal(await row.locator("h4").textContent(),`${s.number}. ${s.title} · ${s.status}`);assert.equal(await row.locator("p").textContent(),s.text);assert.deepEqual(JSON.parse(await row.locator("pre").textContent()),{claim_ids:s.claim_ids,risk_flag_ids:s.risk_flag_ids,limitation_ids:s.limitation_ids});}
    fidelity.push({run_id:await page.locator("#run-picker").inputValue(),report:r,json_hash:crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex"),all_fields_and_sections:true});return exported;}
  async function overlap(page,id,postPath,ready,whileHeld=async()=>{}){
    let releaseFirst,releaseSecond,entered,boots=0,posts=0;
    const first=new Promise(resolve=>releaseFirst=resolve),second=new Promise(resolve=>releaseSecond=resolve),waiting=new Promise(resolve=>entered=resolve);
    const count=request=>{if(new URL(request.url()).pathname===postPath&&request.method()==="POST")posts++;};page.on("request",count);
    await page.route("**/api/bootstrap",async route=>{boots++;if(boots===1){entered();await first;}else if(posts===0)await second;await route.continue();});
    await page.evaluate(id=>{const el=document.getElementById(id),fn=id==="request-form"?el.onsubmit:el.onclick;window.__overlap=Promise.all([fn(new Event("click")),fn(new Event("click"))]);},id);
    await waiting;assert.equal(await page.locator(id==="request-form"?"#request-form":"#start-demo").getAttribute("aria-busy"),"true");
    assert((await page.locator("#action-status").textContent()).includes("Submission in progress"));await whileHeld();releaseFirst();await ready();
    // An incorrect second handler gets a fresh bootstrap only AFTER the first reaches review.
    releaseSecond();await page.evaluate(()=>window.__overlap);await page.unroute("**/api/bootstrap");page.off("request",count);
    // sendLast's read-only inventory bootstrap is expected; only ONE pre-admission bootstrap is held.
    assert.equal(posts,1);assert.equal(boots,2);regressions.push({check:`overlap-${id}`,post_count:posts,bootstrap_count:boots});
  }
  const p=await pageFor();
  if(process.env.TECHLENS_T011_SMOKE==="1"){
    await keyboardTo(p,'[data-field="name"]');await p.keyboard.type("Smoke request");await keyboardTo(p,'[data-field="repository_url"]');await p.keyboard.type("https://github.com/example/smoke");await keyboardTo(p,'#request-form button[type="submit"]');await p.keyboard.press("Enter");await p.locator("#prepared").waitFor({state:"visible"});await idle(p);
    let dl=p.waitForEvent("download");await p.locator("#download-request").click();await(await dl).saveAs(path.join(output,"request.json"));assert.equal(JSON.parse(fs.readFileSync(path.join(output,"request.json"),"utf8")).constraints.max_run_cost_usd,"0");
    await keyboardTo(p,"#demo-tab");await p.keyboard.press("Enter");await keyboardTo(p,"#start-demo");await p.keyboard.press("Enter");await p.locator("#plan-panel").waitFor({state:"visible"});await idle(p);await keyboardTo(p,"#approve-plan");await p.keyboard.press("Enter");await p.locator("#report-panel").waitFor({state:"visible"});await fullReport(p,"smoke");await p.screenshot({path:path.join(output,"smoke.png"),fullPage:true});await p.context().close();const version=browser.version();await browser.close();await shutdown(server);proxy.close();const receipt=events.find(e=>e.child).child;
    assert.equal(receipt.network_attempts,0);assert.equal(receipt.sessions[0].starts,1);assert.equal(receipt.sessions[0].resumes,1);assert.deepEqual(fidelity[0].report,receipt.sessions[0].authoritative_report);assert.equal(fidelity[0].json_hash,receipt.sessions[0].canonical_hashes["report.json"]);assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,"browser-evidence.json"),JSON.stringify({status:"PASS",mode:"smoke",keyboard_request_and_approval:true,browser_version:version,events,fidelity,deny_proxy_observations:denied,console_errors:errors},null,2));console.log("Guarded installed browser smoke PASS");return;
  }
  const initial=await p.context().cookies();assert.equal(initial.length,1);assert(initial[0].httpOnly&&initial[0].sameSite==="Strict");
  // Keyboard/form, duplicated names, exact Decimal, hostile markup stays text.
  await p.keyboard.press("Tab");assert(await p.evaluate(()=>document.activeElement.tagName!=="BODY"));
  await p.locator('[data-field="name"]').fill('<img src="https://bad.invalid/x" onerror="window.__bad=1">');
  await p.locator('[data-field="repository_url"]').fill("https://github.com/example/one");
  await overlap(p,"request-form","/api/requests/prepare",()=>p.locator("#prepared").waitFor({state:"visible"}),()=>p.locator('[data-field="name"]').fill("Changed after original gesture"));
  assert((await p.locator("#prepared-summary").textContent()).includes("<img"));assert(!(await p.locator("#prepared-summary").textContent()).includes("Changed after original gesture"));
  assert((await p.locator("#prepared-summary").textContent()).includes('"max_run_cost_usd": "0"'));
  const emptyContext=JSON.parse(await p.locator("#prepared-summary pre").textContent());assert.deepEqual(emptyContext.request.team_profile,{team_size:null,primary_languages:[],backend_stack:[],ai_experience:null,deployment_environment:null,production_criticality:null,framework_churn_tolerance:null});assert.deepEqual(emptyContext.request.priorities,[]);
  assert(!(await p.locator("#prepared-summary").textContent()).includes("candidate-002"));
  await p.locator("#add-candidate").click();await p.locator('[data-field="name"]').nth(1).fill("Duplicate");await p.locator('[data-field="repository_url"]').nth(1).fill("https://github.com/example/two");
  await p.locator('#request-form button[type="submit"]').click();await p.waitForFunction(()=>document.querySelector("#prepared-summary").textContent.includes("candidate-002"));
  assert(!(await p.locator("#prepared-summary").textContent()).includes("candidate-003"));
  await p.locator("#add-candidate").click();await p.locator('[data-field="name"]').nth(2).fill("Duplicate");await p.locator('[data-field="repository_url"]').nth(2).fill("https://github.com/example/three");
  await p.locator("#budget").fill("1.0000000000000000000000000001");await p.locator('#request-form button[type="submit"]').click();await p.locator("#prepared").waitFor({state:"visible"});
  await p.waitForFunction(()=>document.querySelector("#prepared-summary").textContent.includes("candidate-003"));
  assert((await p.locator("#prepared-summary").textContent()).includes("1.0000000000000000000000000001"));assert.equal(await p.locator("#prepared-summary img").count(),0);assert.equal(await p.evaluate(()=>window.__bad),undefined);
  await idle(p);await p.locator("#team-size").fill("5");
  const descriptions={ai_experience:"Prototype experience only",deployment_environment:"Linux and PostgreSQL",production_criticality:"Internal tooling",framework_churn_tolerance:"Prefer stable interfaces"};
  for(const[key,value]of Object.entries(descriptions))await p.locator(`#context-${key}`).fill(value);
  for(const[key,value]of Object.entries({primary_languages:"Python, literal comma",backend_stack:"PostgreSQL",priorities:"Maintainability"})){await p.locator(`#list-${key}`).getByRole("button",{name:"Add item"}).click();await p.locator(`#list-${key} input`).fill(value);}
  await p.locator('#request-form button[type="submit"]').click();await idle(p);await p.waitForFunction(()=>document.querySelector("#prepared-summary").textContent.includes("Prototype experience only"));
  let download=p.waitForEvent("download");await p.locator("#download-request").click();await(await download).saveAs(path.join(output,"request.json"));
  const preparedRequest=JSON.parse(fs.readFileSync(path.join(output,"request.json"),"utf8"));
  const preparedProjection=JSON.parse(await p.locator("#prepared-summary pre").textContent());assert.deepEqual(preparedProjection.request,preparedRequest);
  assert.deepEqual(preparedRequest.team_profile,{team_size:5,primary_languages:["Python, literal comma"],backend_stack:["PostgreSQL"],...descriptions});assert.deepEqual(preparedRequest.priorities,["Maintainability"]);
  await p.screenshot({path:path.join(output,"01-prepared.png"),fullPage:true});
  // Configured credential and unsafe URL fail without new preparation.
  await p.locator('[data-field="name"]').first().fill("BROWSER-FAKE-SECRET-SENTINEL");await p.locator('#request-form button[type="submit"]').click();await p.waitForFunction(()=>document.querySelector("#message").classList.contains("error"));assert(!(await p.locator("#message").textContent()).includes("BROWSER-FAKE"));
  await p.locator('[data-field="name"]').first().fill("Normal");await p.locator('[data-field="repository_url"]').first().fill("javascript:alert(1)");await p.locator('#request-form button[type="submit"]').click();await p.waitForFunction(()=>document.querySelector("#message").classList.contains("error"));
  await idle(p);await p.locator('[data-field="repository_url"]').first().fill("https://github.com/example/one");
  async function invalidPreparation(){const response=p.waitForResponse(r=>r.request().method()==="POST"&&r.url().endsWith("/api/requests/prepare"));await p.locator('#request-form button[type="submit"]').click();assert.equal((await response).status(),400);await idle(p);assert.deepEqual(JSON.parse(await p.locator("#prepared-summary pre").textContent()).request,preparedRequest);}
  await p.locator('[data-field="name"]').first().fill("");await invalidPreparation();await p.locator('[data-field="name"]').first().fill("Normal");
  await p.locator("#list-priorities input").fill("   ");await invalidPreparation();await p.locator("#list-priorities input").fill("Maintainability");
  await p.locator("#budget").fill("-1");await invalidPreparation();await p.locator("#budget").fill("0");
  // Real retained session preview/cancel/apply, authority and exact content.
  await p.locator("#demo-tab").click();await p.locator("#start-demo").click();await p.locator("#plan-panel").waitFor({state:"visible"});
  const original=await p.locator("#plan-display").textContent();await p.reload();await p.locator("#plan-panel").waitFor({state:"visible"});assert.equal((await p.context().cookies())[0].value,initial[0].value);
  const planDownload={run_id:await p.locator("#run-picker").inputValue()};download=p.waitForEvent("download");await p.locator("#download-plan").click();await(await download).saveAs(path.join(output,"plan.json"));
  planDownload.sha256=crypto.createHash("sha256").update(fs.readFileSync(path.join(output,"plan.json"))).digest("hex");assert.deepEqual(JSON.parse(fs.readFileSync(path.join(output,"plan.json"),"utf8")),JSON.parse((await p.locator("#plan-display pre").allTextContents())[1]));
  await p.locator("#edit-plan").click();await p.locator("#edit-objective").fill('<script>window.__bad=1</script> local edited objective');await p.locator('#plan-editor button[type="submit"]').click();await p.locator("#proposal-panel").waitFor({state:"visible"});assert.equal(await p.locator("#proposal-diff script").count(),0);
  await p.locator("#cancel-proposal").click();await p.locator("#proposal-panel").waitFor({state:"hidden"});assert.equal(await p.locator("#plan-display").textContent(),original);
  await p.locator("#edit-plan").click();await p.locator("#edit-objective").fill("Explicit stored modification");await p.locator('#plan-editor button[type="submit"]').click();await p.locator("#proposal-panel").waitFor({state:"visible"});await p.locator("#apply-proposal").click();await p.locator("#report-panel").waitFor({state:"visible"});
  await p.locator("#retry-action").click();assert((await p.locator("#lifecycle").textContent()).includes("terminal"));
  await p.locator("#report-toggle").click();assert.equal(await p.locator("#report-display .record h4").filter({hasText:/^13\./}).count(),1);
  for(const name of["report.json","report.md","techlens-report.zip"]){download=p.waitForEvent("download");await p.getByRole("button",{name:`Download ${name}`,exact:true}).click();await(await download).saveAs(path.join(output,name));}
  const exported=JSON.parse(fs.readFileSync(path.join(output,"report.json"),"utf8"));const displayed=await p.locator("#report-display").textContent();
  await fullReport(p,"single");
  for(const claim of exported.report.claims){assert(displayed.includes(claim.statement));assert(displayed.includes(claim.claim_id));for(const id of claim.evidence_ids)assert(displayed.includes(id));}
  for(const section of exported.report.sections){assert(displayed.includes(`${section.number}. ${section.title} · ${section.status}`));assert(displayed.includes(section.text));}
  await p.screenshot({path:path.join(output,"02-full-report.png"),fullPage:true});
  await p.locator("#report-heading").scrollIntoViewIfNeeded();await p.screenshot({path:path.join(output,"05-report-viewport.png")});
  // Narrow layout and refresh retain terminal state, no automatic invocation.
  await p.setViewportSize({width:390,height:844});await p.reload();await p.locator("#report-panel").waitFor({state:"visible"});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await p.screenshot({path:path.join(output,"03-narrow.png"),fullPage:true});await p.locator("#report-heading").scrollIntoViewIfNeeded();await p.screenshot({path:path.join(output,"06-narrow-viewport.png")});
  await demo(p,"comparison");await fullReport(p,"comparison");assert((await p.locator("#report-display").textContent()).includes("candidate-002"));assert((await p.locator("#report-display").textContent()).includes("mixed"));
  // Controlled response ordering: an old run's held report cannot replace a newly selected run.
  const ids=await p.locator("#run-picker option").evaluateAll(options=>options.map(o=>o.value).filter(Boolean));
  const oldReport=await p.evaluate(async rid=>(await api(`/api/runs/${rid}/report`)).json(),ids[0]);
  let releaseOld, enterOld;const held=new Promise(resolve=>releaseOld=resolve);const entered=new Promise(resolve=>enterOld=resolve);
  await p.route(`**/api/runs/${ids[0]}/report`,async route=>{events.push({method:"GET",path:new URL(route.request().url()).pathname,controlled_cached_response:true});enterOld();await held;await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(oldReport)});});
  await p.locator("#run-picker").selectOption(ids[0]);await entered;await p.locator("#run-picker").selectOption(ids[1]);await p.locator("#report-panel").waitFor({state:"visible"});
  const oldResponse=p.waitForResponse(r=>r.url().endsWith(`/api/runs/${ids[0]}/report`));releaseOld();await oldResponse;
  await p.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await p.locator("#run-picker").inputValue(),ids[1]);assert((await p.locator("#report-display").textContent()).includes("candidate-002"));
  // F-01/F-02: fourth bounded owner, two real sessions, controlled same-run read ordering.
  const review=await pageFor();await review.locator("#demo-tab").click();
  await overlap(review,"start-demo","/api/demos/start",()=>review.locator("#plan-panel").waitFor({state:"visible"}));
  const reviewId=await review.locator("#run-picker").inputValue();
  await review.locator("#retry-action").click();await idle(review); // original Start receipt, not another session
  async function previewObjective(page,text){await idle(page);await page.locator("#edit-plan").click();await page.locator("#edit-objective").fill(text);await page.locator('#plan-editor button[type="submit"]').click();await page.waitForFunction(text=>document.querySelector("#proposal-diff").textContent.includes(text),text);await idle(page);}
  await previewObjective(review,"Review proposal A");
  const other=await review.context().newPage();await other.goto(server.origin);await other.locator("#demo-tab").click();await other.locator("#run-picker").selectOption(reviewId);await other.locator("#proposal-panel").waitFor({state:"visible"});
  const aSnapshot=await review.evaluate(rid=>read(`/api/runs/${rid}/plan`),reviewId);assert.equal(aSnapshot.proposal.plan.objective,"Review proposal A");
  let releaseA,enteredA;const aHeld=new Promise(resolve=>releaseA=resolve),aEntered=new Promise(resolve=>enteredA=resolve);let firstPlan=true;
  await review.route(`**/api/runs/${reviewId}/plan`,async route=>{if(!firstPlan)return route.continue();firstPlan=false;events.push({method:"GET",path:new URL(route.request().url()).pathname,controlled_cached_response:true});enteredA();await aHeld;await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(aSnapshot)});});
  await review.evaluate(()=>{window.__oldRefresh=refresh();});await aEntered;
  await other.locator("#cancel-proposal").click();await other.locator("#proposal-panel").waitFor({state:"hidden"});await idle(other);await previewObjective(other,"Review proposal B");
  // The still-visible A binding must fail closed, not transparently substitute B.
  assert((await review.locator("#proposal-diff").textContent()).includes("Review proposal A"));
  await review.locator("#apply-proposal").click();await review.waitForFunction(()=>document.querySelector("#message").textContent.includes("Refresh retained state"));await idle(review);
  assert((await review.locator("#proposal-diff").textContent()).includes("Review proposal A"));
  await review.locator("#refresh-run").click();await review.waitForFunction(()=>document.querySelector("#proposal-diff").textContent.includes("Review proposal B"));
  releaseA();await review.evaluate(()=>window.__oldRefresh);assert(!(await review.locator("#proposal-diff").textContent()).includes("Review proposal A"));await review.unroute(`**/api/runs/${reviewId}/plan`);
  // Change BETWEEN status and plan GETs: the plan endpoint's coherent binding wins.
  const bStatus=await review.evaluate(rid=>read(`/api/runs/${rid}`),reviewId);
  let releaseStatus,enteredStatus;const statusHeld=new Promise(resolve=>releaseStatus=resolve),statusEntered=new Promise(resolve=>enteredStatus=resolve);
  await review.route(`**/api/runs/${reviewId}`,async route=>{events.push({method:"GET",path:new URL(route.request().url()).pathname,controlled_cached_response:true});enteredStatus();await statusHeld;await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(bStatus)});});
  await review.evaluate(()=>{window.__mixedRefresh=refresh();});await statusEntered;
  await other.locator("#cancel-proposal").click();await other.locator("#proposal-panel").waitFor({state:"hidden"});await idle(other);await previewObjective(other,"Review proposal C");
  releaseStatus();await review.evaluate(()=>window.__mixedRefresh);await review.unroute(`**/api/runs/${reviewId}`);
  assert((await review.locator("#proposal-diff").textContent()).includes("Review proposal C"));
  const displayedBinding=await review.evaluate(()=>({proposal_id:state.review.plan.proposal.id,revision:state.review.run.revision}));
  await review.screenshot({path:path.join(output,"07-coherent-review.png"),fullPage:true});
  const applyRequest=review.waitForRequest(r=>r.method()==="POST"&&r.url().endsWith(`/api/runs/${reviewId}/decision`));
  await review.locator("#apply-proposal").click();const applied=(await applyRequest).postDataJSON();assert.equal(applied.data.proposal_id,displayedBinding.proposal_id);assert.equal(applied.revision,displayedBinding.revision);
  await review.locator("#report-panel").waitFor({state:"visible"});await idle(review);
  const actualPlan=await review.evaluate(async rid=>(await api(`/api/runs/${rid}/plan`)).json(),reviewId);assert.equal(actualPlan.research_plan.objective,"Review proposal C");
  regressions.push({check:"same-run-old-A-new-B-and-status-B-plan-C",displayed_objective:"Review proposal C",applied_objective:actualPlan.research_plan.objective,matching_proposal:true,matching_revision:true,run_id:reviewId});
  await review.screenshot({path:path.join(output,"08-applied-review.png"),fullPage:true});
  // A deliberate subsequent Start is allowed. A held mutation cannot steal selection.
  await review.locator("#start-demo").click();await review.locator("#plan-panel").waitFor({state:"visible"});await idle(review);
  const secondId=await review.locator("#run-picker").inputValue();assert.notEqual(secondId,reviewId);
  // Hold an ACTUAL browser-network response before the application consumes it;
  // do not use route.fetch (which would require a QA-parent outbound connection).
  await review.evaluate(rid=>{window.__originalFetch=window.fetch;window.__mutationEntered=false;window.fetch=async(...args)=>{const response=await window.__originalFetch(...args);if(args[0]===`/api/runs/${rid}/decision`&&args[1]?.method==="POST"){window.__mutationEntered=true;await new Promise(resolve=>window.__releaseMutation=resolve);}return response;};},secondId);
  await review.locator("#reject-plan").click();await review.waitForFunction(()=>window.__mutationEntered);await review.locator("#run-picker").selectOption(reviewId);await review.locator("#report-panel").waitFor({state:"visible"});await review.evaluate(()=>window.__releaseMutation());await idle(review);assert.equal(await review.locator("#run-picker").inputValue(),reviewId);
  await review.evaluate(()=>{window.fetch=window.__originalFetch;});await other.close();
  regressions.push({check:"delayed-mutation-selection",selected_run:reviewId,mutated_run:secondId,preserved:true});
  const q=await pageFor();await demo(q,"partial");await fullReport(q,"partial");assert((await q.locator("#report-heading").textContent()).includes("partial"));await q.screenshot({path:path.join(output,"04-partial.png"),fullPage:true});await demo(q,"single","reject");await fullReport(q,"rejected");assert((await q.locator("#report-heading").textContent()).includes("rejected"));
  const a=await pageFor();await a.locator("#demo-tab").click();await a.locator("#start-demo").click();await a.locator("#plan-panel").waitFor({state:"visible"});await a.locator("#abandon-run").click();await a.waitForFunction(()=>document.querySelector("#lifecycle").textContent.includes("abandoned"));assert(await a.locator("#plan-panel").isHidden());
  // Concurrent tab reuses owner. No security capability appears in public DOM/report.
  const tab=await p.context().newPage();await tab.goto(server.origin);await tab.locator("#demo-tab").click();assert.equal((await p.context().cookies()).length,1);
  assert.equal(await p.evaluate(()=>window.__bad),undefined);
  // T-013: explicit file inputs, not filesystem paths sent to the application.
  // Export fixtures were made in a separate parent setup phase, not imports.
  const fixtureRoot=process.env.TECHLENS_SAVED_FIXTURES;
  async function openSaved(page,status,format){await page.locator("#saved-tab").click();await page.waitForFunction(()=>savedState.listing!==null);
    const dir=path.join(fixtureRoot,status),names=format==="zip"?["saved.zip"]:["report.json","report.md","manifest.json"];
    await page.locator("#saved-files").setInputFiles(names.map(n=>path.join(dir,n)));await page.locator('#saved-form button[type="submit"]').click();await page.locator("#saved-panel").waitFor({state:"visible"});await idle(page);
    const expected=JSON.parse(fs.readFileSync(path.join(dir,"report.json"),"utf8")),r=expected.report;
    assert(await page.locator("#demo-notice").isHidden());assert((await page.locator("#saved-checks").textContent()).includes("origin is not authenticated"));
    assert.deepEqual(await page.evaluate(()=>savedState.view.snapshot),expected);
    if(await page.locator("#saved-toggle").getAttribute("aria-pressed")!=="true")await page.locator("#saved-toggle").click();
    const blocks=(await page.locator("#saved-display > pre").allTextContents()).map(JSON.parse);
    assert.deepEqual(blocks,[{assessment_id:r.assessment_id,status:r.status,candidate_ids:r.candidate_ids,candidate_names:r.candidate_names,candidates:expected.candidates,team_profile:r.team_profile,priorities:r.priorities},{analyses:r.analyses,comparison:r.comparison},r.risk_flags,r.sources,r.research_notes,r.metrics]);
    const records=page.locator("#saved-display > .record");assert.equal(await records.count(),r.claims.length+13);assert.equal(await page.locator("#saved-display > p").textContent(),r.summary);
    for(let i=0;i<r.claims.length;i++){const c=r.claims[i],row=records.nth(i);assert.equal(await row.locator("p").textContent(),c.statement);assert.equal(await row.locator("h4").textContent(),c.claim_id);assert.deepEqual(JSON.parse(await row.locator("pre").textContent()),{candidate_ids:c.candidate_ids,evidence_ids:c.evidence_ids});}
    for(let i=0;i<13;i++){const s=r.sections[i],row=records.nth(r.claims.length+i);assert.equal(await row.locator("h4").textContent(),`${s.number}. ${s.title} · ${s.status}`);assert.equal(await row.locator("p").textContent(),s.text);assert.deepEqual(JSON.parse(await row.locator("pre").textContent()),{claim_ids:s.claim_ids,risk_flag_ids:s.risk_flag_ids,limitation_ids:s.limitation_ids});}
    assert.equal(await page.locator("#saved-display a, #saved-display script, #saved-display img").count(),0);
    return {status:r.status,format,id:await page.locator("#saved-picker").inputValue(),all_fields_and_sections:true};}
  for(const status of["completed","comparison","partial","rejected","failed","failed-observations"]){for(const format of["zip","files"]){const page=format==="zip"?p:q;const item=await openSaved(page,status,format);savedFidelity.push(item);if(status==="failed"&&format==="zip")await page.screenshot({path:path.join(output,"09-saved-report.png"),fullPage:true});await page.locator("#saved-close").click();await page.locator("#saved-panel").waitFor({state:"hidden"});await idle(page);}}
  // Malformed upload displays fixed error, then a fresh deliberate valid upload works.
  await p.locator("#saved-files").setInputFiles({name:"bad.zip",mimeType:"application/zip",buffer:Buffer.from("not a zip")});await p.locator('#saved-form button[type="submit"]').click();await idle(p);assert((await p.locator("#message").textContent()).includes("check failed"));
  // A fresh owner isolates the bounded saved receipt budget from prior samples.
  const browserVersion=browser.version();for(const context of contexts)await context.close();await browser.close();await shutdown(server);
  // Real process restart with stale sessionStorage/cookie; never recreate old run.
  const restarted=await child(true);const browser2=await chromium.launch({executablePath:process.env.TECHLENS_CHROME,headless:true,proxy:{server:`http://127.0.0.1:${proxy.address().port}`,bypass:"127.0.0.1"},args:["--disable-background-networking","--disable-component-update","--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]});
  const rc=await browser2.newContext({serviceWorkers:"block"});await rc.route("**/*",r=>new URL(r.request().url()).origin===restarted.origin?r.continue():r.abort());await rc.addCookies(initial.map(c=>({...c,domain:"127.0.0.1"})));await rc.addInitScript(()=>sessionStorage.setItem("techlens-run","a".repeat(32)));const rp=await rc.newPage();await rp.goto(restarted.origin);await rp.waitForFunction(()=>document.querySelector("#message").textContent.includes("unavailable"));assert(await rp.locator("#plan-panel").isHidden());
  await rp.locator("#saved-tab").click();await rp.waitForFunction(()=>savedState.listing!==null);assert.deepEqual(await rp.evaluate(()=>savedState.listing.views),[]);
  for(const format of["zip","files"]){await openSaved(rp,format==="zip"?"completed":"partial",format);}
  // Controlled delayed read cannot replace a newer selected imported report.
  const savedIds=await rp.locator("#saved-picker option").evaluateAll(o=>o.map(v=>v.value).filter(Boolean));assert.equal(savedIds.length,2);
  const firstView=await rp.evaluate(id=>read(`/api/saved-reports/${id}`),savedIds[0]);let releaseSaved,enteredSaved;const savedHeld=new Promise(r=>releaseSaved=r),savedEntered=new Promise(r=>enteredSaved=r);
  await rp.route(`**/api/saved-reports/${savedIds[0]}`,async route=>{enteredSaved();await savedHeld;await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(firstView)});});
  await rp.locator("#saved-picker").selectOption(savedIds[0]);await savedEntered;await rp.locator("#saved-picker").selectOption(savedIds[1]);await rp.locator("#saved-panel").waitFor({state:"visible"});releaseSaved();await rp.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal(await rp.evaluate(()=>savedState.view.id),savedIds[1]);
  await rp.locator("#saved-close").click();await rp.locator("#saved-panel").waitFor({state:"hidden"});await idle(rp);assert.equal(await rp.locator("#saved-picker option").count(),2);
  assert.equal(await rp.evaluate(()=>sessionStorage.getItem("techlens-run")),"a".repeat(32));assert.equal(await rp.evaluate(()=>Object.keys(sessionStorage).some(k=>k.includes("saved"))),false);
  await rp.unroute(`**/api/saved-reports/${savedIds[0]}`);
  await openSaved(rp,"hostile","files");assert.equal(await rp.evaluate(()=>window.__savedBad),undefined);assert.equal(await rp.locator("#saved-display img, #saved-display script, #saved-display a").count(),0);
  const savedRestart={zip_reopened:true,files_reopened:true,old_run_unavailable:true,stale_read_ignored:true,close_removed_view:true,hostile_text_inert:true};
  await rp.screenshot({path:path.join(output,"10-reopened-after-restart.png"),fullPage:true});
  async function closeSavedViews(page){await page.evaluate(async()=>{const list=await read("/api/saved-reports");for(const item of list.views){const view=await read(`/api/saved-reports/${item.id}`);await readMutation(`/api/saved-reports/${item.id}/close`,{token:view.close_token,revision:view.revision,data:{}});}++state.selection;++savedState.reading;savedState.id=null;savedState.view=null;$("saved-panel").hidden=true;await savedList();});}
  await closeSavedViews(rp);
  // F-01: actual selected File bytes, with deterministic barriers, not sleeps.
  // During file reading A must never reach transport; during the held actual
  // response A may finish server-side but must not install/clear over B.
  for(const stage of["file-read","open-response"]){
    await rp.evaluate(stage=>{window.__uploadEntered=false;window.__uploadPosts=0;window.__uploadReceipt=null;
      window.__savedFetch=window.fetch;window.fetch=async(...args)=>{if(args[0]==="/api/saved-reports/open"&&args[1]?.method==="POST"){window.__uploadPosts++;const response=await window.__savedFetch(...args);if(stage==="open-response"&&window.__uploadPosts===1){window.__uploadReceipt=await response.clone().json();window.__uploadEntered=true;await new Promise(r=>window.__releaseUpload=r);}return response;}return window.__savedFetch(...args);};
      window.__savedArrayBuffer=File.prototype.arrayBuffer;if(stage==="file-read")File.prototype.arrayBuffer=async function(){const bytes=await window.__savedArrayBuffer.call(this);if(this.name==="A.zip"){window.__uploadEntered=true;await new Promise(r=>window.__releaseUpload=r);}return bytes;};
      window.__savedSubmit=$("saved-form").onsubmit;$("saved-form").onsubmit=event=>{window.__uploadCompletion=window.__savedSubmit(event);return window.__uploadCompletion;};},stage);
    const A={name:"A.zip",mimeType:"application/zip",buffer:fs.readFileSync(path.join(fixtureRoot,"completed/saved.zip"))},B={name:"B.zip",mimeType:"application/zip",buffer:fs.readFileSync(path.join(fixtureRoot,"partial/saved.zip"))};
    await rp.locator("#saved-files").setInputFiles(A);await rp.locator('#saved-form button[type="submit"]').click();await rp.waitForFunction(()=>window.__uploadEntered);
    const beforeGeneration=await rp.evaluate(()=>savedState.fileGeneration);await rp.locator("#saved-files").setInputFiles(B);assert.equal(await rp.evaluate(()=>savedState.fileGeneration),beforeGeneration+1);
    await rp.evaluate(()=>message("New B selection awaits an explicit submission."));
    // A second click while A is active is ignored, never queued as a B retry.
    await rp.locator('#saved-form button[type="submit"]').click();await rp.evaluate(()=>window.__releaseUpload());
    await rp.waitForFunction(()=>!state.submitting);await rp.evaluate(()=>window.__uploadCompletion);await idle(rp);
    assert.deepEqual(await rp.locator("#saved-files").evaluate(e=>[...e.files].map(f=>f.name)),["B.zip"]);
    assert(await rp.locator("#saved-panel").isHidden());assert.equal(await rp.evaluate(()=>savedState.view),null);
    assert.equal(await rp.locator("#message").textContent(),"New B selection awaits an explicit submission.");
    const beforePosts=await rp.evaluate(()=>window.__uploadPosts);assert.equal(beforePosts,stage==="file-read"?0:1);
    await rp.evaluate(()=>{File.prototype.arrayBuffer=window.__savedArrayBuffer;});
    // B starts only from this NEW explicit user action, exactly once.
    await rp.locator('#saved-form button[type="submit"]').click();await rp.locator("#saved-panel").waitFor({state:"visible"});await idle(rp);
    assert.equal(await rp.evaluate(()=>window.__uploadPosts),beforePosts+1);assert.equal(await rp.evaluate(()=>savedState.view.snapshot.report.status),"partial");
    if(stage==="open-response"){const receipt=await rp.evaluate(()=>window.__uploadReceipt);const listing=await rp.evaluate(()=>read("/api/saved-reports"));assert(listing.views.some(v=>v.id===receipt.saved_id));assert.notEqual(receipt.saved_id,await rp.evaluate(()=>savedState.id));}
    savedUploadRaces.push({stage,new_selection_preserved:true,no_stale_view_or_message:true,before_explicit_B_posts:beforePosts,after_explicit_B_posts:beforePosts+1});
    await rp.evaluate(()=>{window.fetch=window.__savedFetch;$("saved-form").onsubmit=window.__savedSubmit;});await closeSavedViews(rp);
  }
  // A distinct import-only owner gets its own bounded receipt budget for four
  // verifier rejections. No fixture setup/session/graph runs in this child.
  const invalidContext=await browser2.newContext({serviceWorkers:"block"});await invalidContext.route("**/*",r=>new URL(r.request().url()).origin===restarted.origin?r.continue():r.abort());const invalidPage=await invalidContext.newPage();await invalidPage.goto(restarted.origin);await invalidPage.locator("#saved-tab").click();await invalidPage.waitForFunction(()=>savedState.listing!==null);
  for(const status of["invalid-elapsed","invalid-failed"]){for(const format of["zip","files"]){const dir=path.join(fixtureRoot,status),names=format==="zip"?["saved.zip"]:["report.json","report.md","manifest.json"];
    await invalidPage.locator("#saved-files").setInputFiles(names.map(n=>path.join(dir,n)));await invalidPage.locator('#saved-form button[type="submit"]').click();await idle(invalidPage);
    assert.equal(await invalidPage.locator("#message").textContent(),"Saved report file or supported structure check failed.");assert(await invalidPage.locator("#saved-panel").isHidden());assert.deepEqual(await invalidPage.evaluate(async()=>(await read("/api/saved-reports")).views),[]);savedInvalid.push({status,format,rejected:true});}}
  await browser2.close();await shutdown(restarted);
  proxy.close();const receipts=events.filter(e=>e.child).map(e=>e.child);assert.equal(receipts.length,2);assert(receipts.every(r=>r.network_attempts===0&&!r.forbidden_imports.length));assert.equal(receipts[1].runs,0);
  // Console favicon failures are not ignored: shell handles its fixed icon route locally.
  const sessions=receipts[0].sessions;const corrected=sessions.find(s=>s.run_id===reviewId);assert.equal(corrected.starts,1);assert.equal(corrected.resumes,1);assert.equal(corrected.objective,"Review proposal C");assert(sessions.every(s=>s.starts===1&&s.resumes<=1));assert.equal(sessions.length,7);
  assert.equal(receipts[0].sessions_constructed,7);assert(sessions.every(s=>s.before_approval.github===0&&s.before_approval.search===0&&s.before_approval.analyzer_generation===0));
  assert.equal(sessions.find(s=>s.run_id===planDownload.run_id).initial_plan_sha256,planDownload.sha256);
  const rejected=sessions.find(s=>s.authoritative_report?.status==="rejected");assert.equal(rejected.github,0);assert.equal(rejected.search,0);assert.equal(rejected.current_round,0);
  for(const item of fidelity){const authoritative=sessions.find(s=>s.run_id===item.run_id);assert.deepEqual(item.report,authoritative.authoritative_report);assert.equal(item.json_hash,authoritative.canonical_hashes["report.json"]);}
  assert.deepEqual(errors,[]);assert.equal(consoleDenials.length,6);fs.writeFileSync(path.join(output,"browser-evidence.json"),JSON.stringify({status:"PASS",browser_version:browserVersion,planDownload,fidelity,saved_fidelity:savedFidelity,saved_restart:savedRestart,saved_upload_races:savedUploadRaces,saved_invalid_metrics_and_failed:savedInvalid,events,regressions,deny_proxy_observations:denied,console_errors:errors,expected_denial_console:consoleDenials,journeys:["prepare-three-duplicates-exact-decimal-hostile","modify-preview-cancel-apply","refresh-identical-retry","full-downloads","narrow-keyboard","comparison","partial","reject","abandon","multi-tab","restart","overlapping-start-prepare","same-run-coherent-proposal","between-status-plan-change","delayed-mutation-selection","saved-zip-and-files","saved-field-fidelity","saved-explicit-reopen-after-restart","replace-selected-files-during-read-and-response","invalid-duration-and-failed-claim-rejection"]},null,2));console.log("Browser walkthrough PASS; child provider/network attempts 0; sanitized evidence recorded.");}
main().catch(error=>{console.error(error.stack);process.exitCode=1;setTimeout(()=>process.exit(1),1000);});
