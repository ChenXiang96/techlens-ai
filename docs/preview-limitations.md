# Preview limitations and verification provenance

## Product scope

This is an English, loopback-only, process-local offline preview. Custom requests
are prepared/downloaded, not researched. Demos use package-owned fictional candidates,
evidence, usage and prices; they do not assess the repositories typed into a form.
A completed demo means the synthetic workflow completed, not that a technology is
healthy, production-ready or suitable for adoption.

Citation/provenance integrity is not factual truth, credibility or semantic support.
The separate offline evaluator checks fixture expectations and application boundaries;
it does not establish real-model quality or prompt-injection resistance.

## Recorded compatibility (historical T-011 evidence)

| Environment | Recorded observation | Limit |
|---|---|---|
| Windows 11 build 26200 / Python 3.12.14 | Guarded installed CLI/module and UI journeys | Not other platforms or Python builds |
| Chrome 155.0.8059.79 | Scripted browser, practical keyboard and layout journeys | Not accessibility certification |
| Edge 152.0.4191.53 | Scripted browser subset | Not separate keyboard certification |
| Python 3.11 | Declared supported by package metadata | NOT VERIFIED by execution |
| Other browsers/platforms | No applicable new execution | NOT VERIFIED |

Versions must be re-observed for later tests. Historical [T-011 acceptance](t011-acceptance.md)
records Codex's 917-pass final full offline suite, Master's separate 73 focused tests,
33 acceptance cases (32 PASS / Python3.11 NOT VERIFIED), and corroborated installed
artifact/browser/native evidence. These evidence sets overlap and are not summed.
Master did not independently rerun the full suite or native/browser journeys.

T-012 fresh artifact/install results are separately recorded in excluded
`qa/t012/release-readiness.json` in the prepared checkout and external release evidence.
They are not prerequisites for installing or building the source distribution, and
historical results do not satisfy that new first-install gate. Until those gates
complete, no T-012 fresh-install success is asserted by this document.

## Operation and accounting limits

- Keep the terminal/server running; Ctrl+C or process exit ends the local service.
- Restart loses in-memory sessions; it is not resume/recovery. No persistent history
  or durable exactly-once billing exists.
- Closing a tab is not cancellation; abandonment cannot recall already-started work.
- Default UI ports use 49152-65535 with at most eight address-collision binds.
  Explicit ports are preserved and may be browser-restricted. Do not disable browser protections.
- Bounded owner/run/action/cache capacity may deny new work. There is no hosting,
  LAN/public binding or multi-user service guarantee.
- CLI no-clobber publication requires hard-link support. Export failure can leave an
  incomplete bundle; only a final matching manifest establishes bundle completeness.
- Admitted attempts are not confirmed transport; missing usage is not zero. Outstanding
  reservations remain estimated liability, not invoices. Elapsed time includes review.
- Checkpoint-type deserialization notices are a documented compatibility item; this
  release preparation does not upgrade dependencies or change serializers.

## Not verified or supplied

Real OpenAI/Tavily/GitHub integration, repository assessments, production count-fee
authority, invoice ceilings, actual-model semantic quality and T-006B remain
**NOT VERIFIED**. No production rate card is bundled. Unknown count pricing blocks
count dispatch itself; generation rates do not establish the count endpoint's fee.
Credentials, `--live`, and positive budgets are not permission by themselves.

The advanced [authorization](live-authorization.md) and [architecture](architecture.md)
contracts protect TechLens-owned requests under trusted application code; they do not
sandbox arbitrary Python, certify supplied prices or guarantee provider invoices.

## Distribution gate

[MIT](../LICENSE), Copyright (c) 2026 Xiang Chen, was owner-approved for project-authored
code, UI and synthetic fixtures on 2026-10-14. This does not certify third-party rights.
Final artifact metadata, fresh-install and release review gates must complete before
public distribution readiness can be asserted.
Repository visibility/ownership, registry availability, publication and support channels
are not established by a locally configured remote. See [the checklist](release-checklist.md).
