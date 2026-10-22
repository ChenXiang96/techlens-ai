# TechLens AI

## Initial offline preview · version 0.1.0

TechLens explores an evidence-grounded technology-assessment workflow. This preview
lets you prepare requests and explore supervised **synthetic demonstrations** in
an English local browser or terminal.

**No keys are needed. Demo candidates, evidence, usage and prices are fictional.**
The browser does not research repositories you enter. A completed demo is not a
real technology assessment, production-readiness conclusion or model-quality result.
Citations preserve traceability; they do not establish truth or semantic support.

### What you can try

- Prepare and download a one-to-three-candidate request with optional team context
  and an explicit USD 0 budget.
- Start a package-owned single-candidate, comparison or partial-retrieval demo.
- Review its plan, Approve/Reject, or preview edits and explicitly Apply/Cancel.
- Inspect summary/full reports and canonical JSON, Markdown and ZIP downloads.
- Explore bounded, process-local runs without provider requests or live consent.

## Prerequisites and compatibility

The recorded offline environment is Windows 11 build 26200 / Python 3.12.14,
Chrome 155.0.8059.79 and the tested Edge 152.0.4191.53 subset.
Python `>=3.11` is declared in package metadata; Python 3.11 and other platforms
have not been executed. These are version-specific observations, not universal
browser/OS support. See [limitations and evidence provenance](docs/preview-limitations.md).

Python and the declared runtime dependencies are required. uv is a development/
reproducibility tool, not an inherent CLI/browser requirement. Node, Playwright,
pytest and native terminal helpers are QA tooling, not product runtime prerequisites.

## Install into a chosen environment

No public registry package or release download is established here. Do not infer
that a same-named registry project belongs to TechLens. Publication URL is pending;
use a reviewed local wheel or source checkout.

The following PowerShell commands describe **user-initiated installation**; dependency
installation may access package indexes. It does not authorize provider execution.
For an offline install, use an already available compatible dependency cache or
reviewed wheelhouse; a cache miss is a blocker, not a reason to silently go online.

Choose an existing Python 3.12 interpreter, set its actual path, and choose a new
environment path. Do not replace an existing environment:

```powershell
$previewPython = Read-Host 'Full path to your existing Python 3.12 executable'
& $previewPython -m venv .preview-venv
$previewEnvPython = (Resolve-Path .preview-venv/Scripts/python.exe).Path
```

For a specifically supplied, reviewed local wheel:

```powershell
$previewWheel = Read-Host 'Full path to the reviewed techlens-0.1.0-py3-none-any.whl'
& $previewEnvPython -m pip install $previewWheel
```

Alternatively, from a reviewed source-checkout root:

```powershell
& $previewEnvPython -m pip install .
```

Check installation errors before continuing. These alternatives do not assert that
a public release is already available. Release preparation, license completion and
first-install checks are tracked in [the release checklist](docs/release-checklist.md).

## Launch the local interface

Use the same environment interpreter:

```powershell
& $previewEnvPython -m techlens --version
& $previewEnvPython -m techlens doctor
& $previewEnvPython -m techlens ui
```

Or run that environment's installed `techlens` console command. Open the exact
printed `http://127.0.0.1:<port>/` URL manually. Keep the terminal running.
Ctrl+C stops the server; restart loses retained runs and can select a new port.
Do not disable browser port protections, expose the server on a LAN, or treat
closing a tab as cancellation.

Follow [the offline walkthrough](docs/offline-walkthrough.md) for request preparation,
Approve, Modify/Cancel/Apply, partial/rejected results and downloads.

## Try the terminal workflow

Use new output destinations whose parents already exist:

```powershell
& $previewEnvPython -m techlens request create --output new-request.json
& $previewEnvPython -m techlens demo --scenario single --output-dir new-single-report
& $previewEnvPython -m techlens demo --scenario comparison --report-view full
& $previewEnvPython -m techlens demo --scenario partial --output-dir new-partial-report
```

Request creation defaults to USD 0. Inspect the full summary and explicitly type
`SAVE` or `CANCEL`. Saving only prepares input; a demo never assesses that request.
The [preparation examples](examples/request-single.json) likewise explicitly use
USD 0. The underlying domain default remains unchanged.

At a demo's review prompt, inspect the plan and type `approve`, `reject`, or
`modify`. No automatic approval occurs. A partial demo intentionally demonstrates
failed synthetic retrieval. All demo prices and usage remain fictional.
See [the CLI guide](docs/local-cli.md) for editing, export semantics and exit codes.

## Limits, costs and technical documentation

Runs are bounded and process-local: no persistent history or durable exactly-once
recovery. Closing a tab cannot recall work already started. CLI exports require
supported hard-link publication; use a new destination. Browser saves are controlled
by your browser.

Real services, independent token-count fee authority, invoices, actual-model quality,
T-006B and Python 3.11 execution remain **NOT VERIFIED**. No production rate card
is supplied. A positive request budget is not permission to spend.

- [Preview limitations and verification provenance](docs/preview-limitations.md)
- [Browser guide and lifecycle/security bounds](docs/local-ui.md)
- [Advanced architecture, evidence, accounting and replay contracts](docs/architecture.md)
- [Advanced live authorization and supported library usage](docs/live-authorization.md)
- [Separate fixture-only evaluator](docs/t006-evaluation.md)

The non-executable [rate-card template](examples/rate-card.template.json) is advanced,
unverified input, not provider pricing. It is outside the offline quickstart.
No live provider operation is part of this preview walkthrough.

## License, contributions and release status

Licensed under [MIT](LICENSE), Copyright (c) 2026 Xiang Chen. The owner confirmed
this scope for project-authored code, UI and synthetic fixtures. This does not certify
third-party rights. Public distribution still requires completed artifact/install
gates and a separate publication decision; dependency metadata is not legal clearance.

See [contribution guidance](CONTRIBUTING.md), [release-note draft](docs/release-notes.md)
and [publication gates](docs/release-checklist.md). No public support channel, security
response promise, public registry ownership or release publication is implied.

## Reopen a saved report

The local browser's **Open saved report** journey accepts an explicitly selected
TechLens ZIP or `report.json`, `report.md` and `manifest.json` together. It checks
file hashes and supported structure, then displays a separate read-only snapshot.
It does not authenticate origin, repeat authoritative validation, restore a run,
or start research. Files are handled in memory; restart requires explicit reopen.
See [saved report limits and trust scope](docs/saved-reports.md).
