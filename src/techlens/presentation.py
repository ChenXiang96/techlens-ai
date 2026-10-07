"""Non-evaluative presentation. Only this module builds public text/link syntax."""
import html
import json
import re
import unicodedata
from urllib.parse import quote, urlsplit

from pydantic import HttpUrl, TypeAdapter

from techlens.configuration import ApplicationError
from techlens.planning import candidate_catalog
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
        if section.status != "available":
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
