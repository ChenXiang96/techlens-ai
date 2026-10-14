# Offline walkthrough

This guide covers the local, English offline MVP. Demos use fictional candidates,
evidence, usage and prices. They do not assess a repository you enter, establish
factual truth, or make provider requests. A positive budget is not permission.

## Prerequisites and launch

Use an existing TechLens environment. This guide does not download or install
Python, packages or browsers. If the environment is missing, stop and report it.
The demonstrated environment is Windows/Python 3.12; see the acceptance record for
the exact tested browser versions and unverified environments.

Open PowerShell in your TechLens checkout and use its existing environment:

```powershell
.\.venv\Scripts\python.exe -B -m techlens doctor
.\.venv\Scripts\python.exe -B -m techlens ui
```

For an already installed package, `techlens ui` or `python -m techlens ui` are
equivalent entry points when those commands belong to that environment. A missing
console launcher does not mean the module command is missing. Do not select an
unrelated Python interpreter to work around an environment problem.

Open the exact printed `http://127.0.0.1:<port>/` URL in your browser. The default
port can change on every launch. Optionally add `--port 8765`; if occupied, stop
the owned earlier server or omit the option. Do not kill an unrelated process.
Automatic selection uses the high dynamic/private port range to avoid restricted
service ports such as 6667. Do not disable browser port protections. If an explicit
port is rejected by your browser, restart with 8765 or omit `--port`.

Keep PowerShell running; minimizing it is fine. Pressing Ctrl+C or closing it stops
the service. A browser connection refusal after that is expected: start the server
again and use its new printed URL. Never disable a firewall to address this first.
Doctor only checks locally; it does not validate credentials, services or prices.

## Journey 1: prepare, inspect and download a request

Select **Prepare request**. Example fictional team input:

| Field | Example |
|---|---|
| Assessment ID | `manual-test-001` |
| Display name | `LangGraph` |
| GitHub repository root URL | `https://github.com/langchain-ai/langgraph` |
| Homepage | Leave blank |
| Team size | `5` |
| Primary languages | Add separate `Python` and `TypeScript` items |
| Backend stack | Add a `PostgreSQL` item |
| Priorities | Add separate `Maintainability` and `Observability` items |
| AI experience | `Prototype experience only.` |
| Deployment environment | `Linux servers.` |
| Production criticality | `Internal tools initially.` |
| Framework churn tolerance | `Prefer stable interfaces.` |
| Maximum research rounds | `1` |
| Maximum web searches | `2` |
| Maximum run cost USD | `0` |

Use **Add item** for each list row; commas are literal text, not separators.
Remove an unused added row rather than leaving a blank item. Optional descriptive
fields can stay blank; empty context stays unknown. Add up to three candidates in
order; duplicate names are valid and do not identify candidates.

Choose **Validate and prepare request**. Inspect the complete summary and choose
**Download request JSON**. The amount remains an exact decimal string. This does
not start assessment or give live permission. No canned report is attached to
this custom request. The ordinary JSON model's budget default is a different
contract; guided preparation explicitly defaults to USD 0.

Try `not-a-url` in the repository field or a blank required name: an error should
appear without starting research. Any prior summary remains the last successfully
prepared request, not a successful version of the rejected input. Correct the
field and explicitly validate again. Never enter actual credentials into forms.

## Journey 2: approve a synthetic demo

1. Select **Explore demo**, then **Single candidate**.
2. Click **Start new synthetic demo** once and wait for planning.
3. Inspect **Research plan · review pending** and scroll through the entire plan.
   Candidates, original repositories, tasks, associations and limits are included.
4. Click **Approve original plan**. Collection begins only after approval.
5. Wait for **Validated completed report**. Choose **Show full report**.

Review all thirteen sections, claims and evidence references, candidate/team
context, judgments, risks, sources, unknowns and accounting qualifications.
Citations provide traceability, not proof. Admitted attempts do not prove dispatch;
cost estimates are not invoices, and outstanding reservations are not zero cost.
Source URLs are displayed only; no source visit is needed for this walkthrough.

## Journey 3: modify, cancel and explicitly Apply

Start a second demo using **Two-candidate comparison**. Merely changing Scenario
does not change a completed report: click **Start new synthetic demo** again.
Before approving, scroll to the plan's end and click **Edit planning content**.
This button exists only while review is pending, not on a terminal report.

Change Objective to `My manual comparison test`. Keep the other fields at first.
Click **Validate and preview changes** and inspect every before/after field.
Preview is not approval. Click **CANCEL proposal**: the original pending plan stays
unchanged. Edit/preview again, then click **APPLY stored proposal**. Only the
displayed stored proposal is submitted. Candidate/repository identity and limits
are application-owned; invalid or stale changes cannot authorize research.

An overlapping submission is ignored while busy. If the response was lost, use
**Retry last submitted action** only when you intend to reconcile that tab's
original submission. It is not a new Start. For a stale-action error, use
**Refresh retained state**, inspect the current plan again and make a new explicit
decision. A newer proposal is never substituted for an obsolete displayed one.

## Downloads, other outcomes and lifecycle

Download report JSON, Markdown and the complete ZIP. The ZIP contains exactly
`report.json`, `report.md`, `manifest.json`; the manifest records SHA-256 of the
exact report files. Your browser controls the save location and overwrite/renaming
behavior. Repeated downloads do not repeat research. If downloads are unavailable,
the explicit **Retry report downloads only** uses the retained terminal state;
it cannot start or resume assessment. A valid report may exist without downloads.

At most two runs are retained per browser owner. Save needed files before exploring
more. Stop the server with Ctrl+C, restart it, open its printed URL, and deliberately
Start a new demo. Restart loses all retained runs. On a reused origin, an old stored
run can correctly show unavailable; it is not restored or replayed.

After restart, try **Partial retrieval → Approve** (expect `partial` with explicit
limitations), then a new **Single candidate → Reject plan** (expect `rejected` with
no research collection). Use the existing selector to revisit retained runs; refresh
and tab closure do not approve anything. **Abandon run** blocks later review but
cannot recall already-started work. Closing a tab is not cancellation.

## Recording an issue

Record the scenario, exact actions, expected and actual behavior, browser/version,
and a screenshot or sanitized terminal message. Do not include keys, cookies,
action/CSRF tokens, request headers or raw checkpoints. Distinguish a defect from
documented limits and an environment/permission blocker.

The UI is loopback-only and process-local, not hosted/LAN or multi-user software.
Live providers, invoices, real-model quality, T-006B, persistent crash recovery and
unexecuted compatibility environments remain NOT VERIFIED. Keyboard/narrow-layout
checks are practical usability evidence, not accessibility certification.
