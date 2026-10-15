# Offline preview release checklist

Draft identity: `techlens==0.1.0`, future tag `v0.1.0`, title
**TechLens AI 0.1.0 — Initial Offline Preview**. These are reviewed preparation
identities, not actual tags or permission to publish.

## Preparation gates

- [x] T-012 plan reviewed; baseline `50859f0b66f06ad3f911d1137a53d14483c14dc3`.
- [x] MIT selected by the owner on 2026-10-14.
- [x] Owner-approved Copyright (c) 2026 Xiang Chen and project-authored code/UI/synthetic-fixture scope.
- [x] Final [LICENSE](../LICENSE) using the Master-reviewed MIT grant/disclaimer and exact owner header.
- [ ] Consistent actual wheel/sdist license metadata and payloads.
- [ ] Freeze all archive inputs: source, docs, metadata, license, examples and tests.
- [ ] Enumerate wheel/sdist members; verify payloads, entry point, license and exclusions.
- [ ] Hash exact final artifacts and record baseline plus dirty prepared-tree identity.
- [ ] Create a genuinely new environment with an existing interpreter and install the
      locked runtime closure offline. Missing cache entries remain BLOCKED.
- [ ] Run actual guarded installed console/module/browser journeys outside checkout.
- [ ] Safely unpack sdist outside checkout and build with cached tools from those
      inputs only; compare runtime payloads/assets/metadata/license with the release wheel.
- [ ] Complete focused checks and one final full frozen/offline suite on frozen inputs.
- [ ] Repeat unchanged evaluation and protected-scope comparisons without rebaseline.
- [ ] Validate links, examples, tracked/untracked whitespace and portable records.
- [ ] Master Release Readiness Review.

`qa/t012/release-readiness.json` records current gate states separately from these
source-distributed notes. Historical T-011 evidence is unchanged and does not replace
new T-012 artifact/first-install evidence. No package checksum or final artifact-specific
test output belongs inside an input whose build would then need repeating.

License provenance: owner selected MIT on 2026-10-14 and subsequently supplied the
holder/year and scope directly. Master reviewed the grant/disclaimer body from the
locally cached Hatchling 1.32.4 MIT text. Its author/year/email is not copied into
TechLens; no external text retrieval or third-party rights clearance is claimed.

## Exact intended local artifact set

```text
techlens-0.1.0-py3-none-any.whl
techlens-0.1.0.tar.gz
SHA256SUMS
release-manifest.json
release-notes.md
```

Use a new controlled output directory, not the populated accepted `dist/`. Keep the
accepted T-011 wheel unchanged. Checksums cover final bytes; the manifest does not
hash itself. Runtime/member equality is separate from archive-byte reproducibility.
The prepared source tree may be dirty; do not identify it as exactly baseline HEAD.

## Later publication gates — NOT authorized by local preparation

- [ ] User commits only the reviewed source/docs/tests/portable QA records.
- [ ] Reconcile the actual final commit, source member inventory and prepared artifacts;
      rebuild/reverify if included inputs changed. No future hash may be invented.
- [ ] Owner confirms actual target repository, authority and intended visibility.
      A locally configured remote does not prove any of those facts.
- [ ] Owner reviews final asset names/checksums, license and release notes.
- [ ] Obtain explicit instructions identifying which tag/push/release/upload/visibility
      actions are authorized. No remote draft, PR, upload or registry claim is implicit.

No CI activation, PyPI/TestPyPI publication, price discovery, provider call, T-006B
execution or prerequisite download follows from this checklist. Do not fabricate a
download URL, contact/security channel, author identity or support commitment.
