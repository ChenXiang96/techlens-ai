# Local CLI MVP

Start with [installation in the README](../README.md#install-into-a-chosen-environment)
and the [offline walkthrough](offline-walkthrough.md). Run commands using the chosen
environment's interpreter or its installed console launcher, not an unrelated global
Python. The [preview limitations](preview-limitations.md) distinguish declared support
from actual version-specific verification.

The CLI wraps the existing supervised, budgeted, one-round graph. It does not
establish live API availability, price authority, semantic correctness, or production readiness.

## Start with a synthetic demonstration

```text
techlens --help
python -m techlens --help
techlens doctor
techlens demo --scenario single --output-dir new-single-report
techlens demo --scenario comparison --output-dir new-comparison-report
techlens demo --scenario partial --output-dir new-partial-report
```

Type `approve`, `modify`, or `reject` at the real graph review interrupt. There is
no automatic approval. The demo uses only packaged canned dependencies, even when
credentials are present. All data, usage and cost values are fictional. The partial
scenario deliberately fails its web retrieval while preserving GitHub observations.
No checkout, tests, evaluation dataset, credentials, or network are needed for demo.

## Request and configuration

Create a standalone request through an interactive English terminal:

```text
techlens request create --output new-request.json
```

The parent directory must already exist and the selected file must be new. The
builder asks for one to three candidates, preserves their order and duplicate
names, and validates repository URLs locally as GitHub repository roots. Optional
homepage URLs must use HTTP/HTTPS and contain no credentials. It never checks
repository existence, constructs a session/client, counts tokens, or searches.

Enter an assessment ID or leave it blank for a locally generated `assessment-`
UUID. Candidate IDs are derived later from request order; no candidate/task IDs
are added to the request JSON. Blank team size remains unknown. Primary languages,
backend stack and priorities use one item per line; Enter ends a list. Commas are
literal text, duplicate values are preserved, and lists have at most 20 items.

There are exactly four optional descriptive fields: `ai_experience`,
`deployment_environment`, `production_criticality`, and `framework_churn_tolerance`.
Blank answers remain `None`; skipped lists remain independent empty lists. The
builder infers no skills, requirements or preferences from other answers.

Guided defaults are two web-search attempts, one declared round, and a candidate
limit equal to the selected count. Guided ranges are 1–8 searches and 1–2 declared
rounds. The existing JSON model accepts its existing wider declared limits; current
execution performs at most one research round regardless of a higher declaration.
Search attempts include any permitted retries. Request limits become immutable
for the subsequent assessment and cannot be increased in plan review.

The guided USD budget defaults to **0**; the domain JSON default remains USD 0.15.
Zero blocks the existing live paid-work path. A positive amount must be explicitly
entered and is only request data, never permission. Use a finite non-negative
decimal without a currency symbol or exponent; no float conversion or rounding
is performed. The builder asks for neither credentials nor a rate card.

Input bounds: assessment ID 128 characters; candidate name 256; URLs 2048;
descriptive context 1024; list items 256; positive integers nine digits; budget 32.
The CLI bounds each physical input line to 4096 data characters, excluding at
most one LF or CRLF terminator. Repeated carriage returns count as data. An
oversized/truncated input ends the command with code 2; no unread suffix is used
as an action or confirmation, and the CLI does not drain the line. A late viewer
failure does not undo a terminal result or a published export. Invalid fields receive fixed
English guidance without echoing rejected data. Noninteractive creation fails
without publication. After inspecting the entire request summary, type exactly
`SAVE` or `CANCEL`. Saving stages the fully validated JSON, flushes it, and publishes
with a no-clobber hard link. Unsupported filesystems or collisions fail safely.
EOF/Ctrl+C before publication leaves no request file. Publication is the commit
point; cancellation afterward does not roll back an already saved valid request.

Saving does not invoke `assess`, choose live mode or issue a grant. Synthetic demos
cannot assess this new request; their repository observations are fictional.

See `examples/request-single.json`, `request-comparison.json`, and
`request-unknown-team.json` in the repository. A minimal request is:

```json
{
  "assessment_id": "my-assessment",
  "candidates": [{"name": "LangGraph", "repository_url": "https://github.com/langchain-ai/langgraph"}],
  "team_profile": {},
  "priorities": [],
  "constraints": {"max_research_rounds": 1, "max_web_searches": 2, "max_candidates": 1, "max_run_cost_usd": "0"}
}
```

Edit names, original GitHub root URLs, supplied team context, priorities and limits.
Unknown team fields remain unknown, not invented defaults. Candidate IDs come from
request order; duplicate display names are valid. Unknown nested fields and duplicate
JSON keys are rejected by the application, without changing the domain models.
This preparation example explicitly sets USD 0; the underlying domain default remains
USD 0.15. The current workflow never executes a second round.

## Advanced live configuration (not the offline quickstart)

The commands below describe existing guarded interfaces, not a verified live service
or a suggested preview exercise. No live pricing basis is bundled.

Credentials belong only in environment configuration: `OPENAI_API_KEY`,
`TAVILY_API_KEY`, and optional `GITHUB_TOKEN`. Never put them in JSON, command-line
arguments, plan text or reports. `OPENAI_MODEL` overrides the existing default.
Custom `OPENAI_BASE_URL` is rejected. HTTP clients disable inherited proxy/certificate
environment configuration; custom proxy/endpoint deployment is outside this MVP.
Inherited tracing and provider debug logging are disabled during application execution.

```text
techlens doctor --request request.json --rate-card rates.json
techlens assess --live --request request.json --rate-card rates.json --output-dir new-live-report
```

`--live` requests interactive consent, not automatic permission. Exact run-specific
acceptance is required before clients or counting/planning; research-plan approval
is a separate later gate. Missing consent/noninteractive use returns exit code 7.
See [Live authorization](live-authorization.md). Doctor never constructs clients or
checks keys online. Missing live configuration does not prevent the default demo.

The rate-card template is deliberately NON-EXECUTABLE. Supply caller-trusted USD
upper generation rates, model, provenance, effective date and input/output bounds
(the existing analyzer requires output capacity of 8192 tokens). Count calls need
their own independent upper-fee basis or authoritative zero-fee declaration; search
calls also need upper-fee provenance. Optional actual fixed fees must be justified,
not fabricated. Unknown billing retains reservations. Doctor checks presence and
structure, not the truth or applicability of an asserted source. No production count
price basis ships with this application. Without one, live execution stays blocked.
Do not use fictional demo prices for live operation.

## Human review and cancellation

The default review is an English layout showing every original candidate ID,
name and repository, objective, questions, engineering checks, web-task ID,
candidate association/query/purpose, comparison target and immutable limit.
Cross-candidate tasks are explicitly labeled. Values are complete, quoted and
escaped; display does not truncate approval information or open a pager.
`view` redraws the plan, `json` displays the complete escaped plan/catalog, and
`help` explains the actions. These local actions create no reservations, provider
calls or graph resume. They retain the same session, saver, executor and thread.

`save-plan` asks for a new path and exclusively writes the validated plan. Edit it
yourself, choose `modify`, and supply its path. No editor or shell is launched.
Candidate associations, canonical web task sequence, comparison targets and limits
must satisfy the existing plan validator. Valid existing plan edits remain allowed.
Invalid input stays in the review loop and authorizes no research.

A valid modification shows every changed objective/question/check and complete
old/new task blocks by position, including task order, IDs, association, query and
purpose. Canonical IDs are positional; the preview does not guess identity across
reordering. Type exactly `APPLY` to submit the validated modification, or `CANCEL`
to retain the original pending plan. Even an unchanged proposed plan requires
confirmation. EOF/Ctrl+C does not apply the proposal. No planning is repeated.

One graph/executor/saver/thread persists through review; planning is not repeated.
Reject follows the graph's rejected-report route. EOF or Ctrl-C never approves.
Planning may already have incurred cost. An interrupted operation gets an application
summary, not a fabricated terminal report. Last saved accounting can be incomplete;
missing accounting is not zero. Reservations are not released or uncertain calls
replayed. Process loss destroys the in-memory session; a later command is NOT recovery.

## Reports and public artifacts

`demo` and `assess` accept `--report-view summary` (default) or `--report-view full`.
The terminal summary resolves all candidate and comparison judgments, exact claim
statements/citations, section associations, risk observations, unknowns, limitations
and accounting. It reduces repetition rather than selecting favorable findings.
It can remain long when qualifications are numerous. Both views validate against
the same authoritative state; the full view is the complete Markdown report.

In an interactive terminal, summary mode offers `Report [full / done; Enter=done]:`
after rendering and any requested export publication. `full` reads the retained
result; it does not invoke the graph again. `done`, Enter and EOF return the
original report status code (0/3/4/5). Ctrl+C returns 130 with a report-viewing
diagnostic stating that the terminal result remains available and no assessment
was restarted. It neither changes the report status nor removes a published bundle.
Noninteractive demos have no optional viewer; `--report-view full` selects complete
output from the outset. Display or export failure follows the safe error path and
never restarts research or announces a completed export prematurely.

The renderer revalidates the terminal report against authoritative state. It resolves
the existing 13 sections, claims/citations, six dimensions, risks, context, comparison,
limitations and metrics without generating new prose with an LLM. Unknown judgments
stay unknown; a completed pipeline does not mean a technology is production-ready.
Citations establish traceability, not truth, credibility, or semantic entailment.

Attempt metrics count admissions, not confirmed HTTP sends. Ambiguous admissions
may or may not have reached providers. Known usage and completeness are separate.
Settled estimates are not invoices; outstanding reservations remain liability.
Elapsed time includes human review. Fixed demo clocks make canned timing fictional.

Exports use a `techlens-local-report-v1` envelope: mode, explicit provenance,
original candidate catalog, and the unchanged report JSON. Standalone schema parsing
cannot repeat full-state integrity validation. Raw snippets, responses, checkpoints,
clients and ledger internals are not exported. Terminal controls, Markdown and HTML
are escaped; unsafe URLs and known credential leakage block output. This is not a
universal detector for unknown/encoded secrets in arbitrary text.

The output directory must be new. It is reserved and hard-link no-clobber capability
tested before live clients are constructed. Files are fully staged, then exclusively
published as `report.json`, `report.md`, and finally `manifest.json` with SHA-256
hashes. A bundle is complete only when that manifest exists and hashes match.
This is not a multi-file atomic transaction. A failed export may leave incomplete
files; other owners' files are never overwritten or removed, and research is never
repeated to repair an export. Output directories on filesystems without hard links
are rejected. The local OS account/filesystem remains trusted against active tampering.

Exit codes: 0 success/completed; 2 invalid input/configuration; 3 rejected;
4 partial; 5 failed/integrity or unsafe output; 6 application/export failure;
7 live authorization denied; 130 interrupted or local request cancellation.
Export failure does not change the report's assessment status.

## Offline user walkthrough

1. Run `techlens request create --output new-request.json`. Enter a name and an
   original GitHub root URL; skip unknown context and retain budget 0. Inspect
   the summary and type `SAVE`. This prepares input; it does not research it.
2. Run `techlens demo --scenario single --output-dir new-single-report`. Use
   `view`, `json` and `help`, then `approve`. Inspect the summary; type `full`
   for all sections and `done` to close. Verify the complete export manifest.
3. Run `techlens demo --scenario comparison --output-dir new-comparison-report`.
   Approve and inspect the cited mixed maintenance observation, both candidate
   source associations, and the dimensions still marked insufficient information.
4. Run `techlens demo --scenario partial --output-dir new-partial-report`.
   Approve; inspect failed synthetic retrieval, limitations and partial status.
5. Run `techlens demo --scenario single --output-dir new-rejected-report` and
   choose `reject`. The report is rejected; its research round remains zero.
6. In another single demo, choose `save-plan` and a new JSON path. Manually edit
   its objective/query. Choose `modify`, supply that file, inspect old/new values,
   then `CANCEL`. Review remains pending. Repeat and type `APPLY` to proceed.
7. Try a modification containing an invented candidate or changed limit. It is
   rejected without research; use `view` to inspect the unchanged pending plan.
8. Cancel request creation or review using EOF/Ctrl+C. No save/apply/approval is
   implied. Before terminal completion, saved accounting can be incomplete and
   unresolved liability remains reserved. At the completed viewer, EOF preserves
   the result code and Ctrl+C leaves the terminal result/export intact.

Use new destinations for every publication. None of these steps requires keys,
Python programming or live authorization. Live configuration, prices, billing,
Python 3.11 execution and T-006B remain separate verification gates.

## Installation and offline verification

Python >=3.11 is declared, not fully compatibility-tested. uv and cached build tools
are developer prerequisites, not installed product requirements. The following source
workflow uses an existing lockfile and declared Hatchling backend:

```text
uv sync --frozen --offline
uv build --offline --wheel --out-dir <wheel-directory>
```

That source/development workflow is not a clean first-install test. Release QA creates
a genuinely empty, outside-checkout environment and installs the reviewed wheel's
locked runtime closure offline, without preinstalling pytest or source/test modules.
Never replace the development environment or reuse a populated QA environment as
proof of first installation. Reinstallation in an existing environment is only a
separate identity/regression check:

```text
uv pip install --offline --no-deps --reinstall --python <fresh-python> <wheel-file>
```

Run help, doctor and every demo from outside the checkout. Verify the actual package
path and absence of editable `.pth` references. Set `TECHLENS_INSTALLED_PYTHON` for
`tests/test_packaging.py`; it independently installs a socket-blocking sitecustomize
in each smoke subprocess, checks imported modules, and records environment receipts.
Normal parent pytest network patches do not protect subprocesses.

Offline cache misses must name the missing artifact before a separately approved
remedy. Do not silently enable downloads or change versions. Python 3.11 execution
remains NOT VERIFIED until a compatible interpreter actually runs these checks.
Wheel installation is not live-provider verification.

T-006A repository-only evaluation QA remains documented separately in
`docs/t006-evaluation.md`. Do not regenerate its frozen fixtures to hide regressions.
T-006B is not implemented or authorized by this CLI ticket.
