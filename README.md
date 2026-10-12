# TechLens AI

T-008 adds default-deny live authorization. Local commands and demos require no
permission. `assess --live` requests exact run-specific interactive consent before
client construction or paid planning; research-plan approval remains a separate gate.
See [live authorization and library compatibility](docs/live-authorization.md).

TechLens assesses one to three original GitHub-hosted technologies for one shared team context. T-005 adds deterministic risk observations, structured adoption analysis, citation checks, a typed 13-section report, and per-run budget admission.

Evidence integrity, citation integrity, and semantic support are different. This pipeline validates the first two. A cited statement is not automatically true, credible, or semantically supported.

## Local verification

Python 3.11+ and uv are required. Existing dependencies remain unchanged.

```console
uv run --frozen --offline --no-sync pytest
git diff --check
```

Tests remove provider credentials, block real socket connections, and use fake adapters or httpx MockTransport. Fictional rates occur only in test fixtures. No live provider verification was performed for T-005.

## Workflow

```text
validate_request / initialize ledger
  -> planner [reserve count -> count -> reserve generation -> generation]
  -> human_review (interrupt)
       reject -> report -> validate / finalize metrics -> END
       approve or valid modify
         -> GitHub collection + bounded web collection
         -> terminal-outcome join (current_round = 1)
         -> normalize_evidence -> validate_evidence
         -> deterministic risk rules -> bounded analysis projection
         -> analyzer [reserve count -> count -> reserve generation -> generation]
         -> validate claims / assemble comparison
         -> report -> independently validate / finalize metrics -> END
```

Use `build_graph(planner=..., search_provider=..., analyzer=..., rates=...)`. The production adapters are `OpenAIResearchPlanner`, `OpenAIAdoptionAnalyzer`, and `TavilySearchProvider`. They read `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-5.6-luna`), and `TAVILY_API_KEY`; GitHub uses optional `GITHUB_TOKEN`. Clients are caller-owned and never checkpointed.

Start with `graph.invoke({"request": request}, config)`, where config contains a stable, nonblank `configurable.thread_id`. Inspect the returned `__interrupt__` plan. Resume the same runner with `Command(resume={"action": "approve"})`, `reject`, or `modify` plus a valid `modified_plan`. No GitHub or Tavily call precedes approval. Invalid review input never authorizes collection.

Candidate IDs, web task IDs, comparison targets, repository mappings, and limits remain application-controlled. Duplicate display names remain valid. All GitHub selection uses the original candidate repository. Each branch checkpoints individual operation outcomes and the parent explicitly joins both branches. Only the join advances the research round. There is no second research round.

## Paid-call admission

No production rate card is bundled. With `rates=None`, the graph returns a truthful partial result before provider transport. Direct `OpenAIResearchPlanner.plan()` is blocked; dispatch through the budgeted graph.

A caller-supplied trusted `RateCard` must identify the model, USD units, source reference, effective date, and applicable upper token rates, including relevant billing premiums. It also requires an independently justified token-count per-call upper fee, source and date, or an authoritative zero-fee basis. Token-count pricing is never inferred from generation token rates. Unknown preflight pricing blocks the count request itself. Search pricing likewise requires an explicit per-attempt upper fee and its source/date; no free credits are presumed.

Each count call has its own operation, attempt ID and committed reservation. Its normalized `VerifiedInputCount` binds the model and canonical digest of the complete shared model/instructions/input/strict schema/settings. Counting and generation use identical token-relevant request JSON. Generation additionally sets its output cap and `store=False`. Input size is bounded before counting; provider counts include structured input overhead. Unknown/unprovable bounds block dispatch.

The Decimal ledger enforces settled estimated cost + unresolved reservations + new reservation <= the immutable request ceiling (default USD 0.15). USD 0.40 is not an automatic fallback. Failed or interrupted calls without trustworthy billing retain their reservations. A successful count may remain billing-unknown while its verified count is usable. Reconciliation is idempotent. Reported token or cost overruns remain visible and block later paid calls. Pricing protection depends on the configured upper rates being correct; it is not invoice reconciliation.

Planner and analyzer each allow one logical generation request, at most two separately reserved attempts, and one count preflight with no automatic preflight retry. An unchanged generation retry reuses the verified count. Web tasks allow at most two attempts each; every attempt consumes the original run-wide max_web_searches allowance. SDK retries remain disabled. Only classified transient transport, clear rate-limit, and server failures can retry. Authentication, quota, refusal, invalid output, and citation/integrity failures cannot. Timeouts remain explicit; backoff is bounded.

The CAD 10 project budget and remaining historical Day 03-05 cash allowance are manual concerns, not tracked by this per-run ledger.

## Replay and checkpoint limits

Retain the returned runner, its process-local `AttemptExecutor`, and its `InMemorySaver`. One invocation per run is allowed; different runs have separate ledgers. Historical or externally modified checkpoint execution is rejected by the retained runner.

A new admission alone creates a READY dispatch slot. Idempotent admission cannot recreate it. Every dispatch verifies an actual committed matching reservation after a synchronous checkpoint barrier. READY is claimed atomically once; completed normalized outcomes are cached before graph reconciliation. Resume reapplies cached outcomes without transport. An in-flight operation suspends competing execution. Missing or ambiguous slots retain liability and cannot authorize replay.

A fresh executor restoring any existing run fails closed for new dispatch slots, including snapshots that precede a reservation: it cannot establish whether later calls are missing from that snapshot. This is same-process protection, not durable recovery or exactly-once billing across process crashes. Process loss also loses the in-memory saver. A restored ledger retains known outstanding reservations, but lost history cannot be reconstructed or safely reset automatically.

## Evidence, risks, and analysis

The T-004 strict source contract remains when coverage is omitted. Explicit partial coverage requires one ordered terminal outcome per original GitHub candidate and approved web task: succeeded, failed, skipped_budget, or skipped_limit. Successful zero-hit search is distinct from failure. Failed collection creates no synthetic signals or evidence.

Normalization keeps the unchanged 24-item GitHub registry per successful candidate plus one evidence record per successful web result. Evidence IDs remain canonical in original candidate/metric/task/result order. Bounded counts retain lower-bound semantics. All four practice indicators must agree with tree completeness. Null license/release observations and duplicate web URLs are preserved.

Versioned pure rules flag recorded archive state, undetected license, stale release, low complete commit activity, complete-population concentration, and recognized-path gaps. Defaults are 365 days, fewer than 5 commits in 90 days, and top-contributor share >= 0.80. These are product heuristics. Unknown and truncated data are not reassuring negatives. Only a known repository-unavailable collection outcome triggers the access observation; authentication, rate-limit, timeout, and other collection failures keep their original categories and make access not evaluable. Issue-resolution health remains not evaluable from collected counts.

One bounded structured analyzer request covers the original candidate roster and six shared dimensions: maintenance, technical stack, operations, maturity, risk tolerance, and ecosystem fit. Missing team fields remain unknown. The deterministic projection preserves stored evidence, limits web snippets to 1,200 characters and at most 24 web records, omits oversized records, and records omissions/excerpts. Only eligible evidence IDs are exposed as citation metadata. Retrieved text is untrusted data; the analyzer has no tools or provider authority.

Application code assigns claim IDs after parsing and reuses the T-004 reference validator. Comparison claims require candidate-assigned evidence for every named candidate. Invalid drafts are discarded entirely without an LLM repair call. There is no total score or forced winner. A single candidate has comparison marked not applicable.

## Report and metrics

The typed report preserves all 13 sections, with explicit insufficient-information content when needed. It copies supplied team context, resolves URLs from evidence, and references validated claims and deterministic flags. Assembly makes no additional LLM call. Independent validation rechecks evidence provenance, citations, candidate/rubric coverage, flags, limitations, report text ownership and measured accounting.

Statuses distinguish completed, partial, rejected, and failed. Expected source/budget/analysis failures produce visible partial results; evidence integrity failures block analysis and produce a failed path. Completed means the pipeline completed, not that every unknown was resolved.

Metrics include start/end times and elapsed wall time including human-review wait, admitted provider attempts and ambiguous attempts, supplied token/credit usage, measured GitHub requests, evidence/claim/round counts, settled estimates and unresolved reservations. Planner, analyzer, LLM, count, and search attempt totals count every admitted ledger record. Admission alone does not prove that transport began. `ambiguous_attempts` counts records without a settled provider outcome, including reservations interrupted before dispatch; `outstanding_reservations_usd` retains their liability. Failed GitHub request totals are unknown because the unchanged T-002 wrapper exposes its measured count only on success.

Automatic research sufficiency loops, source credibility scores, aggregate adoption scoring, RAG, UI, databases and persistent recovery remain outside this implementation.

## T-006A offline evaluation

The separate offline harness contains 26 synthetic cases and 52 canned samples. It
exercises existing structural validators and fake-provider boundaries, and scores
explicit fixture expectations. It does not modify the production workflow or make
live calls. Actual-model semantic quality and prompt-injection resistance remain
**NOT VERIFIED**; fixture labels are not human adjudication.

```text
uv run --frozen --offline --no-sync python -m techlens.evaluation --dataset evals/t006/v1 --format markdown
```

The default command is read-only. Optional `--output-dir new-directory` exports JSON
and Markdown without overwriting existing output. See [evaluation documentation](docs/t006-evaluation.md)
and the [fixture-only baseline](evals/t006/v1/baseline.md). T-006B live execution and its
batch runner are not implemented or authorized.
# Local CLI MVP

Run `python -m techlens --help` or the installed `techlens --help` command.
Start with `techlens doctor`, then `techlens demo --scenario single`; comparison
and partial demo scenarios are also available. Demos use packaged synthetic data,
the real approval interrupt, no credentials, and no network. Type `approve` or
`reject`, or use `save-plan` / `modify` to review an edited plan.

See [the local CLI guide](docs/local-cli.md) for live configuration gates, request
examples, installation verification, safe exports, exit codes and limitations.
No production count-price basis is supplied; live billing and provider behavior
remain NOT VERIFIED. T-006A is a separate offline evaluation harness, not the demo.
