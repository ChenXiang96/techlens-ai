# Reopen saved reports offline

Start the local interface with `python -B -m techlens ui`, keep that terminal
running, and open the printed loopback URL manually. Choose **Open saved report**.
Select either one previously downloaded TechLens ZIP or these three files together:
`report.json`, `report.md`, `manifest.json`. Click **Check and open saved report**.
This is not an assessment and does not use API keys, live permission, providers,
research, a graph, or a budget ledger. No directory scanning or path import exists.

## What is checked

The shared verifier checks the v1 envelope/manifest versions, `complete=true`,
exact filenames, SHA-256 hashes over the original JSON/Markdown bytes, strict UTF-8
JSON, required emitted fields (including nulls), supported report structure and
represented references. The 13 sections, candidate roster, claim/evidence/risk
references, six-dimension rubric, comparison shape and represented metric counts
must agree. Legitimate `metrics=null` is supported. Costs, unknown usage and
rejection/failure accounting are not converted to zero. Original checked values,
including URL spelling, whitespace and exact decimal strings, are displayed.

A supplied metrics object must be finalized: start and finish timestamps and a
finite, nonnegative elapsed duration must be present and agree. Boolean durations
are not numbers. `metrics=null` remains a legitimate unmeasured export; missing
measurements are never invented. Failed-integrity reports cannot retain analysis
claims or claim-supported judgments, but may retain source/risk observations,
failure notes and supported declared accounting.

**Imported saved report — read-only.** File hashes and supported report structure
checked. Imported snapshot; origin is not authenticated. Original workflow
validation and factual support were not repeated. File-declared provenance,
`validation_scope`, mode and costs remain declarations, not authenticated facts.
A matching manifest can be authored by anyone. These checks cannot reconstruct
the original request, evidence registry, ledger, collection outcomes or checkpoint.
They do not establish factual truth, source credibility, semantic support or
equivalence between Markdown and JSON. Bundled Markdown is UTF-8/hash checked,
never rendered. Source URLs remain inert text and are never fetched.

## Viewing and lifecycle

Choose an opened saved report from its separate list. Summary/full views preserve
the represented report, all 13 sections, claims, candidate analyses, comparison,
risk provenance, limitations, sources and metrics. **Close saved view** removes
only that view. It never cancels, resumes, approves or replays an assessment.
Saved identifiers cannot be substituted for execution identifiers. Saved actions
use separate owner-bound revisions and receipts; execution revisions are unchanged.

Changing the selected files creates a new local selection generation. An older
file read or upload completion cannot install a view, replace a message or clear
the newer selection. An already admitted upload may finish on the server; it is
not cancelled, replayed or automatically followed by submitting the newer files.
Wait for the pending action to finish, then explicitly submit the new selection.

Only immutable serialized view bytes are retained, not the uploaded files, parsed
trees or Markdown. Lists are process-only, not browser storage or a database.
Views expire after 30 idle minutes; owner expiry and shutdown purge their views
and action receipts. After restart the list is empty: explicitly select your files
again. Original runs, checkpoints, authority and outstanding liabilities are not
restored. A repeated accepted action returns its bounded receipt, even after its
view was closed; it cannot resurrect a view. A fresh intentional import is separate.

## Bounds and failures

| Resource | Limit |
|---|---:|
| ZIP bytes | 5 MiB |
| Report JSON / Markdown | 2 MiB each |
| Manifest | 8 KiB |
| Import JSON/base64 route only | 7 MiB |
| Other existing POST routes | 1 MiB, unchanged |
| Concurrent imports | 1 globally, no queue |
| Saved views | 2 per owner / 8 globally |
| Serialized view | 4 MiB maximum |
| Retained view bytes | 8 MiB per owner / 32 MiB globally |
| Saved action tickets | 16 per owner / 64 globally |
| Receipts | Less than 1 KiB |
| JSON depth / nodes / collection / string | 32 / 65,536 / 2,048 / 65,536 characters |

Existing four-owner, two-job, run and export-cache limits remain separate. The
single import lease is acquired after early ownership/CSRF/header checks, before
reading the large body. Count and maximum projection bytes are reserved together
before verification. The execution lock is not held during reads or verification.
Publication rechecks owner, shutdown, action identity and saved revision. Every
exit releases the lease/reservation. No active run is evicted to admit an import.
These are input/retention bounds, not an exact process-RSS guarantee. HTTP reads
have the existing 15-second total/5-second inactivity limits; verification has
cooperative deadline checks, not forced thread termination.

The intentionally small ZIP subset accepts stored/deflated regular top-level
members produced by TechLens. It rejects duplicate/path/aliased/extra names,
links/special files, encrypted or unsupported compression, ZIP64/multi-disk,
prefix/trailer/comment/extra-field forms, overlapping ranges and inconsistent
headers, lengths, CRC or decompression EOF. Decompression is bounded by actual
output bytes. Nothing is extracted to disk. Other ZIP tools' extensions may be
rejected; use the original exported files instead of repacking them.

Malformed/unsupported input yields a fixed sanitized diagnostic, never a partial
successful view. Known configured credentials cannot be displayed. Text, filenames
and URLs are untrusted inert data under the existing CSP; no remote assets or
navigation are added. If capacity is reached, close a saved view. A failed accepted
check has a fixed replay receipt; choosing files again is a new explicit action,
not an automatic retry. No file is automatically saved on import.

This feature does not add CLI history, persistent recovery, comparison merging,
new research, scoring, providers, dependencies or publication authority.
