"""Non-evaluative presentation. Only this module builds public text/link syntax."""
import html
import json
import re
import unicodedata
from urllib.parse import quote, urlsplit

from pydantic import HttpUrl, TypeAdapter

from techlens.configuration import ApplicationError
from techlens.planning import candidate_catalog, validate_plan
from techlens.reporting import validate_report

DEMO_NOTICE = "SYNTHETIC DEMO — no real research or model assessment"
DISCLAIMER = "Citations establish traceability, not factual truth or semantic entailment."


class PublicOutput:
    def __init__(self, env):
        self.secrets = tuple(v.strip() for k, v in env.items()
                             if k in {"OPENAI_API_KEY", "TAVILY_API_KEY", "GITHUB_TOKEN"} and v.strip())

    def check(self, value):
        if isinstance(value, dict):
            for key, item in value.items():
                self.check(key)
                self.check(item)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                self.check(item)
            return
        text = str(value)
        if any(secret in text for secret in self.secrets):
            raise ApplicationError("Public output blocked: configured credential detected.", 5)

    def terminal(self, value):
        self.check(str(value))
        return neutralize(str(value))

    def json(self, value):
        self.check(value)
        return json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n"


def neutralize(text):
    # Newlines are data too: callers alone create layout and approval prompts.
    return "".join(f"\\u{ord(c):04x}" if unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
                   else c for c in text)


def markdown(text):
    text = html.escape(neutralize(str(text)), quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)


def safe_url(value):
    failed = False
    try:
        if any(c.isspace() or unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value) or "\\" in value:
            raise ValueError()
        parsed = urlsplit(value)
        TypeAdapter(HttpUrl).validate_python(value)
        if parsed.scheme not in {"http", "https"} or parsed.username is not None or parsed.password is not None:
            raise ValueError()
    except (ValueError, TypeError):
        failed = True
    if failed:
        raise ApplicationError("Public output blocked: unsafe source or candidate URL.", 5)
    return quote(value, safe=":/?#@!$&'*+,;=%-._~")


def _data(public, value):
    """Quoted data cannot supply new terminal lines, prompts or markup structure."""
    public.check(value)
    return public.terminal(json.dumps(value, ensure_ascii=False, allow_nan=False))


def render_request_summary(request, public):
    lines = ["LOCAL REQUEST SUMMARY — preparing a request does not evaluate its repositories"]
    value = request.model_dump(mode="json")
    public.check(value)
    lines.append("Assessment ID: " + _data(public, request.assessment_id))
    for item in candidate_catalog(request):
        safe_url(str(item.repository_url))
        lines.append(f"{item.candidate_id}: " + _data(public, item.name))
        lines.append("  Original repository: " + _data(public, str(item.repository_url)))
    for index, candidate in enumerate(request.candidates, 1):
        if candidate.homepage is not None:
            safe_url(str(candidate.homepage))
        lines.append(f"Candidate {index} homepage: " + _data(public, str(candidate.homepage) if candidate.homepage else None))
    for key, supplied in value["team_profile"].items():
        lines.append(f"Team {key}: " + (_data(public, supplied) if supplied not in (None, []) else "Unknown / not supplied"))
    lines.append("Priorities: " + _data(public, value["priorities"]))
    for key, limit in value["constraints"].items():
        lines.append(f"Immutable request {key}: " + _data(public, limit))
    lines.extend(["Effective behavior: at most one research round; declared limits are preserved.",
                  "Saving this request issues no live permission and starts no assessment."])
    return "\n".join(lines) + "\n"


REVIEW_HELP = (
    "approve: approve this validated plan for bounded research.\n"
    "modify: load an edited plan JSON, inspect all changes, then type APPLY or CANCEL.\n"
    "reject: finish with a rejected report and no external research.\n"
    "save-plan: exclusively save the pending plan JSON for manual editing.\n"
    "view / json / help: local display only; review remains pending.\n"
)


def render_plan(request, plan, public, *, mode):
    plan = validate_plan(plan, request)
    data = plan.model_dump(mode="json")
    public.check(data)
    lines = ["RESEARCH PLAN — review before external research",
             DEMO_NOTICE if mode == "demo" else "AUTHORIZED LIVE — research approval is still pending"]
    names = {}
    for item in candidate_catalog(request):
        names[item.candidate_id] = item.name
        safe_url(str(item.repository_url))
        lines.extend([f"Candidate {item.candidate_id}: " + _data(public, item.name),
                      "  Original repository: " + _data(public, str(item.repository_url))])
    lines.append("Objective: " + _data(public, plan.objective))
    for key, label in (("research_questions", "Research questions"), ("engineering_checks", "Engineering checks")):
        lines.append(label + ":")
        for index, text in enumerate(data[key], 1):
            lines.append(f"  {index}. " + _data(public, text))
        if not data[key]:
            lines.append("  None supplied.")
    lines.append("Web tasks:")
    for task in plan.web_tasks:
        association = (f"{task.candidate_id} / " + _data(public, names[task.candidate_id])
                       if task.candidate_id is not None else "Cross-candidate (candidate_id=null)")
        lines.extend([f"  {task.task_id}: {association}", "    Query: " + _data(public, task.query),
                      "    Purpose: " + _data(public, task.purpose)])
    if not plan.web_tasks:
        lines.append("  No web tasks.")
    lines.append("Canonical comparison targets: " + _data(public, plan.comparison_targets))
    for key in ("max_rounds", "max_searches", "max_candidates", "max_run_cost_usd"):
        lines.append(f"Immutable {key}: " + _data(public, data[key]))
    lines.extend(["Effective behavior: at most one research round; a higher declared round limit is unchanged.",
                  "Search allowance counts attempts, including any permitted retry.", REVIEW_HELP.rstrip()])
    return "\n".join(lines) + "\n"


def render_plan_changes(request, original, proposed, public):
    original, proposed = validate_plan(original, request), validate_plan(proposed, request)
    old, new = original.model_dump(mode="json"), proposed.model_dump(mode="json")
    public.check([old, new])
    lines = ["PROPOSED PLAN CHANGES — nothing has been applied"]
    changed = False
    for key in ("objective", "research_questions", "engineering_checks"):
        if old[key] != new[key]:
            changed = True
            lines.extend([f"Changed {key}:", "  Original: " + _data(public, old[key]),
                          "  Proposed: " + _data(public, new[key])])
    if old["web_tasks"] != new["web_tasks"]:
        changed = True
        lines.append(f"Web task count: {len(old['web_tasks'])} -> {len(new['web_tasks'])}")
        lines.append("Task order and all fields are shown by position; IDs remain canonical.")
        for index in range(max(len(old["web_tasks"]), len(new["web_tasks"]))):
            before = old["web_tasks"][index] if index < len(old["web_tasks"]) else None
            after = new["web_tasks"][index] if index < len(new["web_tasks"]) else None
            lines.extend([f"  Position {index + 1} original: " + _data(public, before),
                          f"  Position {index + 1} proposed: " + _data(public, after)])
    if not changed:
        lines.append("No plan changes.")
    lines.append("Original candidates, comparison targets and request limits remain unchanged.")
    return "\n".join(lines) + "\n"


def validated_report(state, public):
    failed = False
    try:
        report = state["report"]
        validate_report(report, state)
    except Exception:
        # Never expose validator payloads or retain their authenticated context.
        failed = True
    if failed:
        raise ApplicationError("Terminal report failed authoritative integrity validation.", 5)
    catalog = [c.model_dump(mode="json") for c in candidate_catalog(state["request"])]
    data = report.model_dump(mode="json")
    public.check({"report": data, "candidates": catalog})
    for candidate in catalog:
        safe_url(candidate["repository_url"])
    for source in report.sources:
        safe_url(source.source_url)
    return report, catalog


def render_report(state, public, *, mode, scenario=None):
    report, catalog = validated_report(state, public)
    lines = ["# TechLens assessment", "", DEMO_NOTICE if mode == "demo" else "LIVE assessment", "",
             DISCLAIMER, "", f"Status: {report.status}", "",
             "Costs and usage are fictional demo values." if mode == "demo" else
             "Costs are configured estimates, not invoices or verified charges.", ""]
    claims = {c.claim_id: c for c in report.claims}
    sources = {s.evidence_id: s for s in report.sources}
    flags = {f.flag_id: f for f in report.risk_flags}

    def claim_lines(ids):
        for identity in ids:
            claim = claims[identity]
            links = ", ".join(f"[{e}]({safe_url(sources[e].source_url)})" for e in claim.evidence_ids)
            lines.extend([f"- {claim.claim_id} ({', '.join(claim.candidate_ids)}): {markdown(claim.statement)} — {links}", ""])

    for section in report.sections:
        lines.extend([f"## {section.number}. {section.title}", "", f"Status: {section.status}", ""])
        if section.number == 1:
            lines.extend([markdown(report.summary), ""])
        if section.number == 2:
            for candidate in catalog:
                lines.extend([f"- {candidate['candidate_id']}: {markdown(candidate['name'])} — "
                              f"[repository]({safe_url(candidate['repository_url'])})", ""])
            for key, value in report.team_profile.model_dump(mode="json").items():
                lines.extend([f"- {key}: {markdown(value if value not in (None, []) else 'Unknown / not supplied')}", ""])
            lines.extend([f"Priorities: {markdown(report.priorities or 'Not supplied')}", ""])
        claim_lines(section.claim_ids)
        for identity in section.risk_flag_ids:
            flag = flags[identity]
            lines.extend([f"- {flag.flag_id} / {flag.candidate_id} / {flag.severity}: {markdown(flag.statement)}",
                          f"  Provenance: {markdown(flag.provenance.model_dump(mode='json'))}", ""])
        if section.number == 10:
            for dimension in report.comparison.dimensions:
                lines.extend([f"- {dimension.dimension}: {dimension.judgment}; claims: {markdown(dimension.claim_ids)}", ""])
        if section.number == 11:
            for candidate in report.analyses:
                for dimension in candidate.dimensions:
                    lines.extend([f"- {candidate.candidate_id} / {dimension.dimension}: {dimension.judgment}; "
                                  f"claims: {markdown(dimension.claim_ids)}", ""])
        if section.number == 12:
            for source in report.sources:
                lines.extend([f"- {source.evidence_id} ({source.candidate_id or 'cross-candidate'}): "
                              f"[source]({safe_url(source.source_url)})", ""])
        for limitation in section.limitation_ids:
            lines.extend([f"- Limitation: {markdown(limitation)}", ""])
        lines.extend([markdown(section.text), ""])
    lines.extend(["## Execution facts", "", "Attempts count admitted records, NOT confirmed requests. "
                  "Ambiguous attempts may or may not have reached a provider. Outstanding reservations remain liability. "
                  "Elapsed time includes human wait; incomplete usage is not zero usage.", ""])
    if report.metrics:
        for key, value in report.metrics.model_dump(mode="json").items():
            lines.extend([f"- {key}: {markdown(value)}", ""])
    else:
        lines.extend(["Metrics unavailable.", ""])
    result = "\n".join(lines)
    public.check(result)
    return result


def render_report_summary(state, public, *, mode, scenario=None):
    """Reduce repetition while retaining every judgment, claim and qualification."""
    report, catalog = validated_report(state, public)
    lines = ["TECHLENS ASSESSMENT — terminal summary",
             DEMO_NOTICE if mode == "demo" else "LIVE assessment",
             f"Status: {report.status}", "Assessment ID: " + _data(public, report.assessment_id),
             "Summary: " + _data(public, report.summary), DISCLAIMER,
             "Costs and usage are fictional demo values." if mode == "demo" else
             "Costs are configured estimates, not invoices or verified charges.", "Candidates:"]
    for item in catalog:
        lines.extend([f"  {item['candidate_id']}: " + _data(public, item["name"]),
                      "    Original repository: " + _data(public, item["repository_url"])])
    lines.append("Team context: " + _data(public, report.team_profile.model_dump(mode="json")))
    lines.append("Priorities: " + _data(public, report.priorities))
    lines.append("Candidate dimension judgments:")
    for candidate in report.analyses:
        for dimension in candidate.dimensions:
            lines.append(f"  {candidate.candidate_id} / {dimension.dimension}: {dimension.judgment}; "
                         "claims: " + _data(public, dimension.claim_ids))
    lines.append(f"Comparison: {report.comparison.status}; candidates: " + _data(public, report.comparison.candidate_ids))
    for dimension in report.comparison.dimensions:
        lines.append(f"  {dimension.dimension}: {dimension.judgment}; claims: " + _data(public, dimension.claim_ids))
    lines.append("Claims (exact statements; citations establish traceability):")
    for claim in report.claims:
        lines.extend([f"  {claim.claim_id}; candidates: " + _data(public, claim.candidate_ids)
                      + "; evidence: " + _data(public, claim.evidence_ids),
                      "    Statement: " + _data(public, claim.statement)])
    if not report.claims:
        lines.append("  No claim records available; no conclusion inferred.")
    lines.append("Section coverage and associations:")
    for section in report.sections:
        lines.extend([f"  {section.number}. {section.title}: {section.status}",
                      "    Claims: " + _data(public, section.claim_ids)
                      + "; risk flags: " + _data(public, section.risk_flag_ids)
                      + "; limitations: " + _data(public, section.limitation_ids)])
        if section.status != "available":
            lines.append("    " + _data(public, section.text))
    lines.append("Risk observations:")
    for flag in report.risk_flags:
        lines.extend([f"  {flag.flag_id} / {flag.candidate_id} / {flag.severity}: " + _data(public, flag.statement),
                      "    Provenance: " + _data(public, flag.provenance.model_dump(mode="json"))])
    if not report.risk_flags:
        lines.append("  No risk observations recorded; this is not assurance of absence.")
    lines.append("Notes, unknowns and limitations (all recorded values):")
    for note in report.research_notes:
        lines.append("  " + _data(public, note))
    if "analysis:context_omissions" in report.research_notes:
        lines.append("  Some evidence was omitted from analyzer input; the report does not record its count here.")
    if "analysis:excerpted_evidence" in report.research_notes:
        lines.append("  Some analyzer-visible evidence was excerpted; omitted text is not additional visible support.")
    cited = {identity for claim in report.claims for identity in claim.evidence_ids}
    lines.append("Cited sources:")
    for source in report.sources:
        if source.evidence_id in cited:
            lines.append(f"  {source.evidence_id} / {source.candidate_id or 'cross-candidate'}: " + _data(public, source.source_url))
    lines.extend(["Execution facts:",
                  "  Attempts count admitted records, NOT confirmed requests. Ambiguous attempts may or may not have reached a provider.",
                  "  Outstanding reservations remain liability. Elapsed time includes human wait; incomplete usage is not zero usage."])
    if report.metrics is None:
        lines.append("  Metrics unavailable; missing measurements are not zero.")
    else:
        for key, value in report.metrics.model_dump(mode="json").items():
            lines.append(f"  {key}: " + _data(public, value))
    lines.append("Full report: use the local full viewer when offered, or --report-view full at invocation.")
    lines.append("With --output-dir, the full report is in report.md and report.json; manifest.json marks completed publication.")
    result = "\n".join(lines) + "\n"
    public.check(result)
    return result
