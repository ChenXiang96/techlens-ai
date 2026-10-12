# Local CLI MVP

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

See `examples/request-single.json`, `request-comparison.json`, and
`request-unknown-team.json` in the repository. A minimal request is:

```json
{
  "assessment_id": "my-assessment",
  "candidates": [{"name": "LangGraph", "repository_url": "https://github.com/langchain-ai/langgraph"}],
  "team_profile": {},
  "priorities": []
}
```

Edit names, original GitHub root URLs, supplied team context, priorities and limits.
Unknown team fields remain unknown, not invented defaults. Candidate IDs come from
request order; duplicate display names are valid. Unknown nested fields and duplicate
JSON keys are rejected by the application, without changing the domain models.
The default USD ceiling remains 0.15; the current workflow never executes a second round.

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

The complete plan and original candidate catalog are displayed as escaped JSON.
`save-plan` asks for a new path and exclusively writes the validated plan. Edit it
yourself, choose `modify`, and supply its path. No editor or shell is launched.
Candidate associations, canonical web task sequence, comparison targets and limits
must satisfy the existing plan validator. Valid existing plan edits remain allowed.
Invalid input stays in the review loop and authorizes no research.

One graph/executor/saver/thread persists through review; planning is not repeated.
Reject follows the graph's rejected-report route. EOF or Ctrl-C never approves.
Planning may already have incurred cost. An interrupted operation gets an application
summary, not a fabricated terminal report. Last saved accounting can be incomplete;
missing accounting is not zero. Reservations are not released or uncertain calls
replayed. Process loss destroys the in-memory session; a later command is NOT recovery.

## Reports and public artifacts

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
130 interrupted. Export failure does not change the report's assessment status.

## Installation and offline verification

Python >=3.11 is declared. Use the existing lockfile and declared Hatchling backend:

```text
uv sync --frozen --offline
uv build --offline --wheel --out-dir <wheel-directory>
```

For isolated QA, set `UV_PROJECT_ENVIRONMENT` to a fresh environment outside the
checkout before sync; never replace the development environment. Explicitly install
the built wheel over the editable package (same version must still be replaced):

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
