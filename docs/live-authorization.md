# Explicit offline and live authorization

Help, version, doctor, and demos are local. Credentials, a rate card, a saved
report, and selecting `assess` do not authorize network requests.

```text
techlens assess --live --request request.json --rate-card rates.json --output-dir new-report
```

After local configuration/destination checks, the CLI displays the original
request, canonical roster, model, endpoints, caller-trusted USD pricing references,
dates, ceiling and execution bounds. Type exactly `AUTHORIZE LIVE <displayed run ID>`.
Anything else or EOF denies authorization with exit code 7. Noninteractive live
execution is unsupported. Ctrl+C remains 130; later research rejection remains 3.
Denied startup creates no assessment ledger/report. Destination preparation may
leave an empty directory. There is no `--yes`, environment permission, or live fallback.

Counting and planning can incur charges before the separate research-plan review.
Rejecting that plan does not undo settled estimates or unresolved liability.
Consent does not establish price authority or increase the budget. Production
counting requires its independent trusted fee basis; no production prices ship here.

## Supported library ownership

Use `authorize_live_run(request, rates, captured_env, read=..., write=...,
public=PublicOutput(captured_env), interactive=...)` with a trusted interactive
terminal, after local validation and any export destination checks. It owns the
exact run-specific confirmation and returns a nonserializable runtime authorization.
Do not replace interaction with automatic acceptance in a real application.

Within an `ExitStack`, register `authorization.close` immediately, then call
`live_dependencies(stack, rates, authorization.model, captured_env,
authorization=authorization)`. Construct `AssessmentSession(request, dependencies,
authorization=authorization)` and retain it across `start()` and validated
`decision()` / `resume()` calls. A session also supports context-manager disposal.

Advanced graph callers pass the same authorization to `build_graph` and use its
`run_id` as `configurable.thread_id`. One authorization binds one graph, saver and
executor. Direct graph callers must close authorization on abandonment. A normal
HITL pause retains it; completion and cancellation revoke it. Ordinary recoverable
graph errors retain runtime ownership for existing cache/ambiguity handling; owner
disposal still closes all owned resources. Reading final state needs no live grant.

Real provider constructors now require `authorization`. Direct paid methods additionally
require the owning executor's checkpointed, atomically claimed dispatch scope.
Possessing a grant or injecting MockTransport is insufficient. Legacy direct real
adapter usage is intentionally incompatible; synthetic dependencies remain usable.

## Independent boundaries

Before new admission, the runtime checks the original request, rates, run and
provider configuration. Final paid boundaries atomically consume one matching
scope; a second identical call is denied. Context-local scopes isolate graph workers.
GitHub has a separate bounded collection scope with original repository authority,
validated research approval, and a per-request/page guard. It creates no paid attempts.

Only the actual validated graph review transition attests the approved plan.
Live `update_state` is denied. Fresh checkpoints, report data and action-like prose
cannot create runtime consent. Valid plan modifications do not alter original limits.
The live runner accepts only an initial request-only mapping, a resume-only valid
review decision at the actual review interrupt, or `None` for same-process
reconciliation. Command updates, routing and parent-graph controls are rejected
before checkpoint mutation. Invalid review input leaves the pending review intact.
Admission and dispatch independently check ledger run, rates, dollar ceiling and
search allowance against original authorization; accounting history is not editable
through the public runner. These checks do not erase outstanding reservations.
Credentials are captured once, including an explicitly absent GitHub token.
Endpoints, redirects, SDK retries, tracing and logging remain constrained.

Budget arithmetic, verified count receipts, synchronous checkpoint barriers,
single-use attempt slots, cached outcomes, retry reservations and unknown liability
are unchanged. Permission denial after reservation never frees uncertain charges.
Cached reconciliation makes no new transport scope. Revocation cannot recall a
request already dispatched. New runtime ownership does not revive old attempts.

This protects supported TechLens-owned calls under trusted application code, not
arbitrary Python, independently used SDKs or provider billing. There is no durable
exactly-once recovery. Live service behavior, invoices, pricing authority, T-006B
and Python 3.11 execution remain NOT VERIFIED. T-008 verification is offline only.
