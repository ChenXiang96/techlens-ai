/* External QA only. Existing Playwright + installed Chrome; no downloads. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const readline = require("node:readline");
const {spawn} = require("node:child_process");
const net = require("node:net");
const {chromium} = require(process.env.TECHLENS_PLAYWRIGHT);
const output = process.argv[2];
fs.mkdirSync(output,{recursive:true});
const events=[],errors=[],denied=[],consoleDenials=[],regressions=[];
// Playwright uses child pipes. This parent has no permitted outbound socket.
net.Socket.prototype.connect = function(){throw new Error("QA parent denies outbound sockets");};
// Independent deny-only proxy: never resolves/forwards any request.
const proxy=http.createServer((req,res)=>{denied.push("proxy-http");res.writeHead(403);res.end();});
proxy.on("connect",(req,socket)=>{socket.on("error",()=>{});denied.push("proxy-connect");socket.end("HTTP/1.1 403 Forbidden\r\n\r\n");});
proxy.on("clientError",(error,socket)=>{socket.on("error",()=>{});socket.destroy();});
function listen(server){return new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));}
function child(){const p=spawn(process.env.TECHLENS_UI_PYTHON,["-B",path.join(__dirname,"ui_browser_server.py")],{cwd:process.env.TECHLENS_UI_CWD||path.resolve(__dirname,".."),env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"},stdio:["pipe","pipe","pipe"]});
  p.stderr.on("data",()=>errors.push("Python child emitted stderr"));const lines=readline.createInterface({input:p.stdout});let first=true;return new Promise((resolve,reject)=>{p.on("error",reject);p.once("exit",c=>{if(first)reject(new Error("Server failed before startup"));});lines.on("line",line=>{if(first){first=false;resolve({p,origin:line,receipt:null,lines});}else{try{events.push({child:JSON.parse(line)});}catch{errors.push("Unexpected child output");}}});});}
async function shutdown(server){const exited=new Promise(resolve=>server.p.once("exit",resolve));server.p.stdin.end("stop\n");assert.equal(await exited,0);}
async function main(){await listen(proxy);const server=await child();assert.match(server.origin,/^http:\/\/127\.0\.0\.1:\d+$/);
  const browser=await chromium.launch({executablePath:process.env.TECHLENS_CHROME,headless:true,proxy:{server:`http://127.0.0.1:${proxy.address().port}`,bypass:"127.0.0.1"},args:["--disable-background-networking","--disable-component-update","--disable-default-apps","--disable-sync","--metrics-recording-only","--no-first-run","--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]});
  const contexts=[];
  async function pageFor(origin=server.origin){const context=await browser.newContext({serviceWorkers:"block",acceptDownloads:true});contexts.push(context);
    await context.route("**/*",route=>{const url=new URL(route.request().url());if(url.origin!==origin){denied.push("route-denied");return route.abort();}events.push({method:route.request().method(),path:url.pathname});return route.continue();});
    const page=await context.newPage();page.on("pageerror",()=>errors.push("Browser page error"));page.on("console",m=>{if(m.type()==="error"){if(/^Failed to load resource: the server responded with a status of 400/.test(m.text()))consoleDenials.push("HTTP 400 from deliberate invalid-input probe");else if(/^Failed to load resource: the server responded with a status of 409/.test(m.text()))consoleDenials.push("HTTP 409 from deliberate stale-proposal probe");else errors.push("Unexpected browser console error");}});await page.goto(origin);return page;}
  async function demo(page,scenario,decision="approve"){await page.locator("#demo-tab").click();await page.locator("#scenario").selectOption(scenario);await page.locator("#start-demo").click();await page.locator("#plan-panel").waitFor({state:"visible"});await page.locator(decision==="reject"?"#reject-plan":"#approve-plan").click();await page.locator("#report-panel").waitFor({state:"visible"});}
  async function idle(page){await page.waitForFunction(()=>document.querySelector("#start-demo").getAttribute("aria-busy")!=="true");}
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
  const initial=await p.context().cookies();assert.equal(initial.length,1);assert(initial[0].httpOnly&&initial[0].sameSite==="Strict");
  // Keyboard/form, duplicated names, exact Decimal, hostile markup stays text.
  await p.keyboard.press("Tab");assert(await p.evaluate(()=>document.activeElement.tagName!=="BODY"));
  await p.locator('[data-field="name"]').fill('<img src="https://bad.invalid/x" onerror="window.__bad=1">');
  await p.locator('[data-field="repository_url"]').fill("https://github.com/example/one");
  await overlap(p,"request-form","/api/requests/prepare",()=>p.locator("#prepared").waitFor({state:"visible"}),()=>p.locator('[data-field="name"]').fill("Changed after original gesture"));
  assert((await p.locator("#prepared-summary").textContent()).includes("<img"));assert(!(await p.locator("#prepared-summary").textContent()).includes("Changed after original gesture"));
  assert((await p.locator("#prepared-summary").textContent()).includes('"max_run_cost_usd": "0"'));
  assert(!(await p.locator("#prepared-summary").textContent()).includes("candidate-002"));
  await p.locator("#add-candidate").click();await p.locator('[data-field="name"]').nth(1).fill("Duplicate");await p.locator('[data-field="repository_url"]').nth(1).fill("https://github.com/example/two");
  await p.locator('#request-form button[type="submit"]').click();await p.waitForFunction(()=>document.querySelector("#prepared-summary").textContent.includes("candidate-002"));
  assert(!(await p.locator("#prepared-summary").textContent()).includes("candidate-003"));
  await p.locator("#add-candidate").click();await p.locator('[data-field="name"]').nth(2).fill("Duplicate");await p.locator('[data-field="repository_url"]').nth(2).fill("https://github.com/example/three");
  await p.locator("#budget").fill("1.0000000000000000000000000001");await p.locator('#request-form button[type="submit"]').click();await p.locator("#prepared").waitFor({state:"visible"});
  await p.waitForFunction(()=>document.querySelector("#prepared-summary").textContent.includes("candidate-003"));
  assert((await p.locator("#prepared-summary").textContent()).includes("1.0000000000000000000000000001"));assert.equal(await p.locator("#prepared-summary img").count(),0);assert.equal(await p.evaluate(()=>window.__bad),undefined);
  let download=p.waitForEvent("download");await p.locator("#download-request").click();await(await download).saveAs(path.join(output,"request.json"));
  await p.screenshot({path:path.join(output,"01-prepared.png"),fullPage:true});
  // Configured credential and unsafe URL fail without new preparation.
  await p.locator('[data-field="name"]').first().fill("BROWSER-FAKE-SECRET-SENTINEL");await p.locator('#request-form button[type="submit"]').click();await p.waitForFunction(()=>document.querySelector("#message").classList.contains("error"));assert(!(await p.locator("#message").textContent()).includes("BROWSER-FAKE"));
  await p.locator('[data-field="name"]').first().fill("Normal");await p.locator('[data-field="repository_url"]').first().fill("javascript:alert(1)");await p.locator('#request-form button[type="submit"]').click();await p.waitForFunction(()=>document.querySelector("#message").classList.contains("error"));
  // Real retained session preview/cancel/apply, authority and exact content.
  await p.locator("#demo-tab").click();await p.locator("#start-demo").click();await p.locator("#plan-panel").waitFor({state:"visible"});
  const original=await p.locator("#plan-display").textContent();await p.reload();await p.locator("#plan-panel").waitFor({state:"visible"});assert.equal((await p.context().cookies())[0].value,initial[0].value);
  await p.locator("#edit-plan").click();await p.locator("#edit-objective").fill('<script>window.__bad=1</script> local edited objective');await p.locator('#plan-editor button[type="submit"]').click();await p.locator("#proposal-panel").waitFor({state:"visible"});assert.equal(await p.locator("#proposal-diff script").count(),0);
  await p.locator("#cancel-proposal").click();await p.locator("#proposal-panel").waitFor({state:"hidden"});assert.equal(await p.locator("#plan-display").textContent(),original);
  await p.locator("#edit-plan").click();await p.locator("#edit-objective").fill("Explicit stored modification");await p.locator('#plan-editor button[type="submit"]').click();await p.locator("#proposal-panel").waitFor({state:"visible"});await p.locator("#apply-proposal").click();await p.locator("#report-panel").waitFor({state:"visible"});
  await p.locator("#retry-action").click();assert((await p.locator("#lifecycle").textContent()).includes("terminal"));
  await p.locator("#report-toggle").click();assert.equal(await p.locator("#report-display .record h4").filter({hasText:/^13\./}).count(),1);
  for(const name of["report.json","report.md","techlens-report.zip"]){download=p.waitForEvent("download");await p.getByRole("button",{name:`Download ${name}`,exact:true}).click();await(await download).saveAs(path.join(output,name));}
  const exported=JSON.parse(fs.readFileSync(path.join(output,"report.json"),"utf8"));const displayed=await p.locator("#report-display").textContent();
  for(const claim of exported.report.claims){assert(displayed.includes(claim.statement));assert(displayed.includes(claim.claim_id));for(const id of claim.evidence_ids)assert(displayed.includes(id));}
  for(const section of exported.report.sections){assert(displayed.includes(`${section.number}. ${section.title} · ${section.status}`));assert(displayed.includes(section.text));}
  await p.screenshot({path:path.join(output,"02-full-report.png"),fullPage:true});
  await p.locator("#report-heading").scrollIntoViewIfNeeded();await p.screenshot({path:path.join(output,"05-report-viewport.png")});
  // Narrow layout and refresh retain terminal state, no automatic invocation.
  await p.setViewportSize({width:390,height:844});await p.reload();await p.locator("#report-panel").waitFor({state:"visible"});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await p.screenshot({path:path.join(output,"03-narrow.png"),fullPage:true});await p.locator("#report-heading").scrollIntoViewIfNeeded();await p.screenshot({path:path.join(output,"06-narrow-viewport.png")});
  await demo(p,"comparison");assert((await p.locator("#report-display").textContent()).includes("candidate-002"));assert((await p.locator("#report-display").textContent()).includes("mixed"));
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
  const q=await pageFor();await demo(q,"partial");assert((await q.locator("#report-heading").textContent()).includes("partial"));await q.screenshot({path:path.join(output,"04-partial.png"),fullPage:true});await demo(q,"single","reject");assert((await q.locator("#report-heading").textContent()).includes("rejected"));
  const a=await pageFor();await a.locator("#demo-tab").click();await a.locator("#start-demo").click();await a.locator("#plan-panel").waitFor({state:"visible"});await a.locator("#abandon-run").click();await a.waitForFunction(()=>document.querySelector("#lifecycle").textContent.includes("abandoned"));assert(await a.locator("#plan-panel").isHidden());
  // Concurrent tab reuses owner. No security capability appears in public DOM/report.
  const tab=await p.context().newPage();await tab.goto(server.origin);await tab.locator("#demo-tab").click();assert.equal((await p.context().cookies()).length,1);
  assert.equal(await p.evaluate(()=>window.__bad),undefined);
  for(const context of contexts)await context.close();await browser.close();await shutdown(server);
  // Real process restart with stale sessionStorage/cookie; never recreate old run.
  const restarted=await child();const browser2=await chromium.launch({executablePath:process.env.TECHLENS_CHROME,headless:true,proxy:{server:`http://127.0.0.1:${proxy.address().port}`,bypass:"127.0.0.1"},args:["--disable-background-networking","--disable-component-update","--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]});
  const rc=await browser2.newContext({serviceWorkers:"block"});await rc.route("**/*",r=>new URL(r.request().url()).origin===restarted.origin?r.continue():r.abort());await rc.addCookies(initial.map(c=>({...c,domain:"127.0.0.1"})));await rc.addInitScript(()=>sessionStorage.setItem("techlens-run","a".repeat(32)));const rp=await rc.newPage();await rp.goto(restarted.origin);await rp.waitForFunction(()=>document.querySelector("#message").textContent.includes("unavailable"));assert(await rp.locator("#plan-panel").isHidden());await browser2.close();await shutdown(restarted);
  proxy.close();const receipts=events.filter(e=>e.child).map(e=>e.child);assert.equal(receipts.length,2);assert(receipts.every(r=>r.network_attempts===0&&!r.forbidden_imports.length));assert.equal(receipts[1].runs,0);
  // Console favicon failures are not ignored: shell handles its fixed icon route locally.
  const sessions=receipts[0].sessions;const corrected=sessions.find(s=>s.run_id===reviewId);assert.equal(corrected.starts,1);assert.equal(corrected.resumes,1);assert.equal(corrected.objective,"Review proposal C");assert(sessions.every(s=>s.starts===1&&s.resumes<=1));assert.equal(sessions.length,7);
  assert.deepEqual(errors,[]);assert.equal(consoleDenials.length,3);fs.writeFileSync(path.join(output,"browser-evidence.json"),JSON.stringify({status:"PASS",events,regressions,deny_proxy_observations:denied,console_errors:errors,expected_denial_console:consoleDenials,journeys:["prepare-three-duplicates-exact-decimal-hostile","modify-preview-cancel-apply","refresh-identical-retry","full-downloads","narrow-keyboard","comparison","partial","reject","abandon","multi-tab","restart","overlapping-start-prepare","same-run-coherent-proposal","between-status-plan-change","delayed-mutation-selection"]},null,2));console.log("Browser walkthrough PASS; child provider/network attempts 0; sanitized evidence recorded.");}
main().catch(error=>{console.error(error.stack);process.exitCode=1;setTimeout(()=>process.exit(1),1000);});
