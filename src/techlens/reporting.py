"""Deterministic report assembly and independent structural validation."""
from techlens.analysis import unknown_analysis
from techlens.analysis_models import RUBRIC, SECTION_TITLES
from techlens.evidence import validate_claim_evidence_contract, validate_records
from techlens.models import AssessmentReport
from techlens.planning import candidate_catalog
from techlens.report_models import ReportSection, ReportClaim, SourceReference
from techlens.risk import apply_risk_rules


class ReportValidationError(Exception):
    pass


SUMMARIES = {
    "rejected": "Research plan rejected. No external research performed.",
    "failed": "Assessment failed integrity checks.",
    "partial": "Assessment pipeline completed with explicit limitations.",
    "completed": "Assessment pipeline completed. Citations do not establish factual truth.",
}


def limitations_for(state):
    return ([o.operation_id for o in state.get("github_outcomes", []) + state.get("web_outcomes", [])
             if o.status != "succeeded"] + list(state.get("errors", []))
            + [f"unknown:{r.candidate_id}:{r.rule_id}:{r.reason_code}"
               for r in state.get("risk_evaluations", []) if r.status == "not_evaluable"]
            + [f"billing_unknown:{a.attempt_id}" for a in state["budget_ledger"].attempts if a.state != "settled"]
            + (["budget:overrun"] if state["budget_ledger"].overrun else []))


def report_status(state):
    if state.get("fatal_failure"):
        return "failed"
    if state.get("plan_status") == "rejected":
        return "rejected"
    outcomes = state.get("github_outcomes", []) + state.get("web_outcomes", [])
    if (state.get("analysis_status") != "succeeded" or state.get("errors")
            or any(o.status != "succeeded" for o in outcomes)
            or state["budget_ledger"].overrun):
        return "partial"
    return "completed"


def assemble_report(state):
    request, status = state["request"], report_status(state)
    analysis = state.get("analysis_result") or unknown_analysis(request)
    claims, evidence, flags = state.get("claims", []), state.get("evidence", []), state.get("risk_flags", [])
    limitations = limitations_for(state)
    sections = []
    for number, title in enumerate(SECTION_TITLES, 1):
        refs = list(analysis.section_claims.get(str(number), []))
        if number == 11:
            refs = [c for a in analysis.candidates for d in a.dimensions for c in d.claim_ids]
        if number == 10:
            refs = [c for d in analysis.comparison.dimensions for c in d.claim_ids]
        if number == 1:
            refs = [c.claim_id for c in claims][:3]
        applicable = not (number == 10 and len(request.candidates) == 1)
        available = bool(refs) or number in {1, 2, 9, 12, 13}
        sections.append(ReportSection(number=number, title=title,
            status="not_applicable" if not applicable else "available" if available else "insufficient_information",
            claim_ids=refs, risk_flag_ids=[f.flag_id for f in flags] if number in {1, 9} else [],
            limitation_ids=limitations if number in {1, 13} else [],
            text="Comparison not applicable." if not applicable else "See referenced observations and claims." if available
                 else "Insufficient information."))
    summary = ("Research plan rejected. No external research performed." if status == "rejected" else
               "Assessment failed integrity checks." if status == "failed" else
               "Assessment pipeline completed with explicit limitations." if status == "partial" else
               "Assessment pipeline completed. Citations do not establish factual truth.")
    return AssessmentReport(assessment_id=request.assessment_id,
        candidate_names=[c.name for c in request.candidates], candidate_ids=[c.candidate_id for c in candidate_catalog(request)],
        summary=summary, research_notes=["Evidence integrity and citation integrity do not prove semantic support.", *limitations],
        team_profile=request.team_profile.model_copy(deep=True), priorities=list(request.priorities),
        status=status, sections=sections, claims=[ReportClaim(**c.model_dump()) for c in claims],
        sources=[SourceReference(evidence_id=e.evidence_id, candidate_id=e.candidate_id, source_url=e.source_url) for e in evidence],
        risk_flags=flags, analyses=analysis.candidates, comparison=analysis.comparison)


def validate_report(report, state):
    request = state["request"]
    ids = [c.candidate_id for c in candidate_catalog(request)]
    claims, evidence = state.get("claims", []), state.get("evidence", [])
    if state.get("fatal_failure") and (claims or report.status != "failed"):
        raise ReportValidationError("Failed integrity cannot produce analysis")
    if state.get("current_round") == 1 and not state.get("fatal_failure"):
        summary = validate_records(request, state["research_plan"], state["github_signals"],
            state["web_research_results"], evidence, state["collection_coverage"])
        if summary != state.get("evidence_validation"):
            raise ReportValidationError("Evidence summary differs")
        evaluations, flags = apply_risk_rules(request, evidence, state["collection_coverage"])
        if flags != state.get("risk_flags") or evaluations != state.get("risk_evaluations"):
            raise ReportValidationError("Application risk results differ")
    validate_claim_evidence_contract(claims, evidence, request)
    if (report.assessment_id != request.assessment_id or report.candidate_ids != ids
            or report.candidate_names != [c.name for c in request.candidates]
            or report.status != report_status(state)
            or [s.title for s in report.sections] != list(SECTION_TITLES)
            or [s.number for s in report.sections] != list(range(1, 14))):
        raise ReportValidationError("Report identity, status or sections differ")
    if (report.summary != SUMMARIES[report.status] or report.team_profile != request.team_profile
            or report.priorities != request.priorities
            or report.research_notes != ["Evidence integrity and citation integrity do not prove semantic support.", *limitations_for(state)]):
        raise ReportValidationError("Report context or prose differs")
    if report.claims != [ReportClaim(**c.model_dump()) for c in claims] or report.risk_flags != state.get("risk_flags", []):
        raise ReportValidationError("Report assertions differ")
    if [(s.evidence_id, s.candidate_id, s.source_url) for s in report.sources] != [(e.evidence_id, e.candidate_id, e.source_url) for e in evidence]:
        raise ReportValidationError("Report sources differ")
    analysis = state.get("analysis_result") or unknown_analysis(request)
    if report.analyses != analysis.candidates or report.comparison != analysis.comparison:
        raise ReportValidationError("Analysis presentation differs")
    if [a.candidate_id for a in report.analyses] != ids or any([d.dimension for d in a.dimensions] != list(RUBRIC) for a in report.analyses):
        raise ReportValidationError("Report rubric differs")
    by_claim, by_evidence = {c.claim_id: c for c in claims}, {e.evidence_id: e for e in evidence}
    for candidate in report.analyses:
        for dimension in candidate.dimensions:
            if (dimension.judgment == "insufficient_information") != (not dimension.claim_ids):
                raise ReportValidationError("Uncited judgment")
            if any(c not in by_claim or by_claim[c].candidate_ids != [candidate.candidate_id] for c in dimension.claim_ids):
                raise ReportValidationError("Dimension candidate association differs")
    comparison = report.comparison
    if comparison.candidate_ids != ids or (len(ids) == 1 and (comparison.status != "not_applicable" or comparison.dimensions)):
        raise ReportValidationError("Comparison roster differs")
    if len(ids) > 1 and [d.dimension for d in comparison.dimensions] != list(RUBRIC):
        raise ReportValidationError("Comparison rubric differs")
    for dimension in comparison.dimensions:
        if (dimension.judgment == "insufficient_information") != (not dimension.claim_ids):
            raise ReportValidationError("Uncited comparison judgment")
        if any(c not in by_claim or by_claim[c].candidate_ids != ids for c in dimension.claim_ids):
            raise ReportValidationError("Comparison claim roster differs")
    for c in claims:
        if not set(c.evidence_ids) <= set(state["analysis_projection"].eligible_evidence_ids):
            raise ReportValidationError("Report cites out-of-view evidence")
        if len(c.candidate_ids) > 1 and not set(c.candidate_ids) <= {by_evidence[e].candidate_id for e in c.evidence_ids}:
            raise ReportValidationError("Comparison citation coverage differs")
    # Reconstruct each section from state, independently of assemble_report().
    # A known claim in the wrong section is still a report integrity failure.
    limitations = limitations_for(state)
    risk_ids = [flag.flag_id for flag in state.get("risk_flags", [])]
    for number, section in enumerate(report.sections, 1):
        if number == 1:
            claim_ids = [claim.claim_id for claim in claims[:3]]
        elif number == 10:
            claim_ids = [claim_id for dimension in analysis.comparison.dimensions
                         for claim_id in dimension.claim_ids]
        elif number == 11:
            claim_ids = [claim_id for candidate in analysis.candidates
                         for dimension in candidate.dimensions for claim_id in dimension.claim_ids]
        else:
            claim_ids = list(analysis.section_claims.get(str(number), []))
        not_applicable = number == 10 and len(ids) == 1
        available = bool(claim_ids) or number in {1, 2, 9, 12, 13}
        expected_status = ("not_applicable" if not_applicable else "available" if available
                           else "insufficient_information")
        expected_section = ReportSection(
            number=number, title=SECTION_TITLES[number - 1], status=expected_status,
            claim_ids=claim_ids, risk_flag_ids=risk_ids if number in {1, 9} else [],
            limitation_ids=limitations if number in {1, 13} else [],
            text="Comparison not applicable." if not_applicable else
                 "See referenced observations and claims." if available else "Insufficient information.",
        )
        if section != expected_section:
            raise ReportValidationError("Report section differs from authoritative state")
    if {c for s in report.sections for c in s.claim_ids} != set(by_claim):
        raise ReportValidationError("Report omitted material claims")
    if report.metrics is not None:
        validate_metrics(report.metrics, report, state)


def validate_metrics(metrics, report, state):
    """Recount source and accounting records independently of graph finalization."""
    ledger = state["budget_ledger"]
    # These totals count admitted ledger records. Admission alone does not
    # establish whether transport began; ambiguity and liability are separate.
    generations = [a for a in ledger.attempts if a.kind == "generation"]
    searches = [a for a in ledger.attempts if a.kind == "search"]
    ambiguous = [a for a in ledger.attempts if a.outcome is None or a.outcome.category == "ambiguous"]
    outcomes = state.get("github_outcomes", []) + state.get("web_outcomes", [])
    expected = {
        "run_id": ledger.run_id, "assessment_id": state["request"].assessment_id, "status": report.status,
        "planner_attempts": sum(a.operation_id == "planner:generate" for a in generations),
        "analyzer_attempts": sum(a.operation_id == "analyzer:generate" for a in generations),
        "llm_attempts": len(generations), "count_attempts": sum(a.kind == "count" for a in ledger.attempts),
        "ambiguous_attempts": len(ambiguous), "search_attempts": len(searches),
        "github_calls_measured": sum(g.engineering_signals.collection_metadata.github_api_calls_made for g in state.get("github_signals", [])),
        "github_calls_complete": all(o.status == "succeeded" for o in state.get("github_outcomes", [])),
        "research_rounds": state["current_round"], "evidence_count": len(report.sources), "claim_count": len(report.claims),
        "settled_estimate_usd": ledger.settled, "outstanding_reservations_usd": ledger.outstanding,
        "cost_status": "overrun" if ledger.overrun else "unknown_liability" if any(a.state != "settled" for a in ledger.attempts) else "estimated",
        "input_tokens_known": sum(a.outcome.usage.input_tokens or 0 for a in generations if a.outcome),
        "output_tokens_known": sum(a.outcome.usage.output_tokens or 0 for a in generations if a.outcome),
        "token_usage_complete": all(a.outcome and a.outcome.usage.input_tokens is not None and a.outcome.usage.output_tokens is not None for a in ledger.attempts if a.kind == "generation"),
        "credits_known": sum(a.outcome.usage.credits or 0 for a in searches if a.outcome),
        "credits_complete": all(a.outcome and a.outcome.usage.credits is not None for a in ledger.attempts if a.kind == "search"),
        "collection_failures": sum(o.status == "failed" for o in outcomes),
        "collection_skips": sum(o.status.startswith("skipped") for o in outcomes),
        "error_categories": sorted(set([o.error_category for o in outcomes if o.error_category] + state["errors"])),
    }
    if any(getattr(metrics, key) != value for key, value in expected.items()):
        raise ReportValidationError("Report metrics differ from source accounting")
    if (metrics.finished_at is None or metrics.started_at != state["run_metrics"].started_at
            or metrics.elapsed_seconds != max(0, (metrics.finished_at - metrics.started_at).total_seconds())
            or not metrics.includes_human_wait):
        raise ReportValidationError("Report timing semantics differ")
