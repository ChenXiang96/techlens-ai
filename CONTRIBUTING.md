# Contributing to the offline preview

Start with the [README](README.md) and [preview limitations](docs/preview-limitations.md).
This project is preparing an initial local offline preview, not certifying live
providers, real-model quality, production prices or general platform support.

## Local development and tests

Use a reviewed checkout and an existing compatible Python interpreter. Python 3.12.14
is the recorded executed version; Python >=3.11 is a declaration, not proof of
3.11 execution. uv and pytest are development tools, not installed product needs.

An already provisioned development environment can run:

```powershell
uv run --offline --frozen --no-sync python -B -m pytest -q -p no:cacheprovider
uv lock --check --offline
git diff --check
```

These commands do not provision missing prerequisites. Offline cache/interpreter
misses should be reported, not hidden by downloading or changing locked versions.
Native/browser tests require explicitly configured existing QA tools and installed
artifacts; a missing prerequisite is not a passing compatibility observation.
Use fresh test-owned temporary output paths; do not reuse populated pytest basetemp
directories or change the normal user's environment.

Normal tests remove provider credentials, disable tracing and deny sockets. Keep
child guards active too: parent pytest patches alone do not protect subprocesses.
Installed CLI/UI tests must operate outside the checkout with real terminal/browser
identity, bounded output/deadlines and cleanup of only owned resources. QA tools
must not become undeclared runtime dependencies or production bypasses.

## Change boundaries

Keep candidate/repository authority, plan review, canonical identities, immutable
limits, budget admission, checkpoint barriers, replay protection and report/evidence
validation intact. Synthetic demo prices are fictional; do not relabel them trusted
provider fees. A positive budget or a configured key is not execution permission.
Do not regenerate evaluation fixtures or baselines merely to make a regression pass.
The [architecture](docs/architecture.md) and [live authorization guide](docs/live-authorization.md)
describe the advanced contracts; no live test follows from contributing offline.

Release checks inspect both wheel and sdist, install into a genuinely new environment
using the locked cached runtime closure, and rebuild from the safely unpacked sdist.
Tests needing configured release artifacts must skip explicitly when absent, not
depend unconditionally on excluded generated QA records. See [release gates](docs/release-checklist.md).

## Reproduction and privacy

Record exact local version/environment, scenario, steps, expected/actual result,
exit status and sanitized relevant output. Distinguish a product defect from an
environment blocker or documented limit. Do not submit keys, authenticated headers,
cookies, action/CSRF tokens, raw checkpoints, private paths or provider responses.
Use only synthetic data for screenshots. Do not visit report source URLs for an
offline reproduction.

No public Issues channel, private security-reporting service, contact address or
response-time commitment is established here. Use the owner-agreed channel; do not
publish sensitive details assuming a private channel exists.

## Licensing and publication

The owner approved [MIT](LICENSE), Copyright (c) 2026 Xiang Chen, for project-authored
code, UI and synthetic fixtures. Contributions must identify any third-party material
and rights you can grant. A dependency license inventory is not legal clearance.
Do not tag, push, upload a release, change visibility or publish a registry package
as a side effect of local preparation. Those actions need separate owner authorization.
