# Local visual interface

For installation, see [the README](../README.md#install-into-a-chosen-environment).
For exact user actions and expected results, see [the offline walkthrough](offline-walkthrough.md).
Use the selected environment's interpreter or console launcher; no Node/Playwright
installation is needed to operate the product. See [compatibility limits](preview-limitations.md).

Run `techlens ui` or `python -m techlens ui`. The default selects an available
port. `techlens ui --port 8765` requests that specific port (1–65535).
Automatic selection uses 49152–65535 with at most eight local bind attempts on
address collisions. This avoids service ports rejected by the tested browsers
(an OS-selected 6667 was reproduced). Explicit ports are never silently changed;
browser restrictions still apply. Permission failures are not retried.
The server binds numeric **127.0.0.1 only**. Open the printed URL manually in a
local browser. It does not open a browser or make any provider request on launch.

## Two separate journeys

**Prepare request:** enter one to three candidates in order, optional team
context, priorities and execution ceilings. Duplicate display names are valid.
List rows are separate items; commas are literal. Empty lists and unknown context
are preserved; supplied blank list items are rejected. The guided budget defaults
to USD 0 and stays an exact Decimal string. The existing domain default is not
changed. A positive budget grants no permission. The requested round ceiling may
be two, but the existing MVP performs at most one round. Review the complete
summary, then explicitly download request JSON. No repository is researched and
no canned result is attached to this request. Your browser controls saving and
overwrite prompts; the server writes no request file.

**Explore demo:** select the package-owned single, comparison or partial scenario
and explicitly start it. The retained real `AssessmentSession` runs with only
synthetic dependencies and stops at human review. Review the entire plan and
original candidate roster. Approve, reject, or edit useful planning content.
Candidate IDs/repository mappings, positional task IDs, comparison targets and
limits are application-owned. A preview lists every plan field, including
unchanged fields. Preview does not approve. APPLY submits the exact stored,
revalidated proposal; CANCEL removes only the UI proposal and leaves the graph
checkpoint unchanged. Invalid input never resumes research.

Reports retain all thirteen sections, claim-to-evidence references, source URLs,
candidate/team/priority context, six rubric dimensions, comparison judgments,
risk provenance, limitations and accounting completeness flags. Summary/full
views are read-only. Citations establish traceability, not truth or semantic
support. Demo usage and prices are fictional. Admitted attempts do not prove
transport; missing values are not zero; outstanding reservations are not invoices;
elapsed time includes review wait. Source URLs are displayed only, not fetched.

## Ownership and action reconciliation

The initial application-shell GET issues a bounded random HttpOnly,
SameSite=Strict cookie in a unique process namespace. Bootstrap requires that
cookie and returns a separate CSRF token/control channel. Refresh and tabs share
valid ownership without creating new sessions. Every API read except bootstrap
requires the CSRF header; mutations additionally require the exact local Origin.
Capabilities are never included in reports, download URLs or logs.

Server-issued actions bind the owner, operation, resource, revision and (for
Apply/Cancel) proposal. Under a lock, a consumed action is reconciled against the
stable original submitted content **before** current phase/revision checks.
Identical retries return the original receipt, even after completion; conflicting
reuse fails. Missing/retired actions fail closed. A scheduling failure leaves a
failed consumed receipt, never a deferred invocation. The explicit retry button
resubmits this tab's last action, not a fresh run. Refresh never invokes the graph.

The client acquires a synchronous submission guard before any bootstrap await.
Overlapping Start/Prepare/review gestures are ignored while that submission is
busy (announced through a live status and `aria-busy`); a later deliberate Start
remains possible. Request content is captured before the first await, and an
explicit retry preserves the original token, revision and body.

Plan projection, stored diff and review controls are returned in one lock-held
snapshot. The page installs them together, discards superseded responses even
for the same run, and binds Apply to exactly the displayed proposal/revision.
Changes between status and plan reads use the coherent plan snapshot, never
mixed controls. Stale server bindings require a refresh rather than substituting
a newer proposal. Delayed action responses cannot override a newer selection.
Unused controls retire by operation-specific binding; unchanged reads in other
tabs do not invalidate sibling actions. Consumed receipts are never reset.

One invocation is active per owner/run; independent owners are isolated.
Abandonment blocks future review, not already-started synthetic work. Closing a
tab does not approve/cancel. Ctrl+C stops admission and waits for admitted work
and HTTP workers; it does not forcibly preempt Python or reconcile/release paid
liability. Reports already completed remain read-only until process exit.
Restart/idle expiry loses sessions and rejects stale run IDs; a new explicit demo
is required. There is no persistent history, recovery or automatic replay.

## Cached downloads

Request/plan downloads use fixed filenames. Report JSON and Markdown are produced
by unchanged `export_bytes(..., mode="demo", scenario=...)`. A complete in-memory
ZIP contains exactly `report.json`, `report.md`, `manifest.json`; the manifest uses
`techlens-local-bundle-v1`, `complete=true`, and SHA-256 of the exact report files.
Only a completely built, checked, bounded set is cached atomically. GETs only read
cached bytes and never recover failed exports. The explicit authenticated
`POST /api/runs/{id}/artifacts/retry` rebuilds only from the retained terminal
state; it never calls session start, decision or resume. An export failure does
not change the validated assessment status or accounting.

## Security and bounds

This trusted-code local MVP is not a Python sandbox or a publicly hosted service.
No live execution, key/rate form, provider selector, arbitrary custom-request
assessment, shell bridge, filesystem publication, remote asset or telemetry exists.
No dependencies were added. HTTP uses an asset/route allowlist, exact Host/Origin,
Fetch Metadata, owner/CSRF/action checks, CSP and text-only rendering. No Markdown
or HTML from data is executed. Known configured credentials block public output;
workers suppress inherited tracing/logging with `private_execution()`.

Limits: request line 4 KiB; header line 8 KiB; headers 32 KiB / 64 fields; JSON body
1 MiB / nesting 32; socket inactivity 5 seconds; total read deadline 15 seconds;
8 HTTP workers, 2 jobs, 4 owners, 8 retained runs / 2 per owner, 64 retained action
controls/receipts, one prepared request per owner, one proposal per run, 32 MiB
report artifact cache. Idle owners expire after 30 minutes only when no job is
active. Capacity exhaustion is explicit; no active run is evicted/recreated.
Report JSON/Markdown are each capped at 2 MiB, manifest 8 KiB, ZIP 5 MiB.
Connections close after one response; malformed/unread suffixes cannot become
another action. Duplicate framing/JSON keys, encodings, credential URLs and
unapproved fields are rejected before effects.

## Offline verification

UI unit/HTTP tests use only real guarded application-loopback sockets and canned
providers. The existing global network fixture is unchanged. The separately run
browser harness requires already installed Node/Playwright/Chromium, routes only
the concrete application origin, and uses a deny-only proxy for other traffic;
no browser/package download is allowed. Installed-wheel QA uses configured disposable
environments, cached build prerequisites and locally built wheels. Historical replacement
in an existing QA environment is not proof of a genuinely fresh release installation;
release verification records that gate separately in its own results.

Live providers, billing/pricing authority, real-model quality, T-006B, durable
recovery and Python 3.11 execution remain **NOT VERIFIED**.

## Saved report views

Use **Open saved report** for a previously exported ZIP or the exact three-file
bundle. This independent journey shows file-declared provenance without labeling
an imported live report as a synthetic run. It grants no execution or live
authority. Read the [integrity checks and lifecycle](saved-reports.md) before
interpreting a saved snapshot. Markdown and source URLs remain inert.
