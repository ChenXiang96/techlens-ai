"""Approval-gated research nodes with separate branch state ownership."""

from collections.abc import Callable

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import ValidationError

from techlens.evidence import (
    EvidenceNormalizationError, EvidenceValidationError, normalize_records, validate_records,
)
from techlens.evidence_models import EvidenceRecord, EvidenceValidationSummary
from techlens.github_models import EngineeringSignals
from techlens.models import AssessmentReport, AssessmentRequest, ResearchPlan, TechnologyCandidate
from techlens.planning import (
    PlanReviewError, PlannerError, ResearchPlanner, candidate_catalog, validate_plan,
)
from techlens.research_models import (
    CandidateEngineeringSignals, PlanReviewDecision, WebResearchTaskResult,
)
from techlens.search import SearchProvider, SearchResponseError
from techlens.state import TechLensState

GitHubCollector = Callable[[TechnologyCandidate], EngineeringSignals]


def validate_request(state: TechLensState, config: RunnableConfig) -> dict[str, object]:
    thread_id = config.get("configurable", {}).get("thread_id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("A nonempty configurable.thread_id is required")
    if "request" not in state:
        raise ValueError("No assessment request is available for this thread")
    raw = state["request"]
    request = AssessmentRequest.model_validate(
        raw.model_dump() if isinstance(raw, AssessmentRequest) else raw
    )
    return {
        "request": request,
        "plan_status": "pending",
        "current_round": 0,
        "github_signals": [],
        "web_research_results": [],
        "research_notes": [],
        "errors": [],
    }


def plan_research(state: TechLensState, *, planner: ResearchPlanner) -> dict[str, object]:
    request = state["request"]
    # A dependency cannot mutate the authoritative checkpointed request.
    proposal = planner.plan(request.model_copy(deep=True))
    return {"research_plan": validate_plan(proposal, request)}


def human_review(state: TechLensState) -> dict[str, object]:
    request = state["request"]
    payload = {
        "type": "research_plan_review",
        "assessment_id": request.assessment_id,
        "candidates": [item.model_dump(mode="json") for item in candidate_catalog(request)],
        "research_plan": state["research_plan"].model_dump(mode="json"),
        "allowed_actions": ["approve", "modify", "reject"],
    }
    # This node is replayed on resume. No provider calls belong before interrupt.
    value = interrupt(payload)
    invalid = False
    update = None
    try:
        raw = value.model_dump() if isinstance(value, PlanReviewDecision) else value
        decision = PlanReviewDecision.model_validate(raw)
        if decision.action == "reject":
            update = {"plan_status": "rejected"}
        else:
            proposal = decision.modified_plan if decision.action == "modify" else state["research_plan"]
            plan = validate_plan(proposal, request)
            update = {
                "research_plan": plan,
                "plan_status": "modified" if decision.action == "modify" else "approved",
            }
    except (ValidationError, PlannerError, ValueError, TypeError):
        invalid = True
    if invalid or update is None:
        raise PlanReviewError("Invalid plan review; no research authorized")
    return update


def route_review(state: TechLensState) -> list[str]:
    if state["plan_status"] == "rejected":
        return ["generate_report_stub"]
    if state["plan_status"] in {"approved", "modified"}:
        return ["collect_github", "web_research"]
    raise PlanReviewError("Research requires human approval")


def _approved_plan(state: TechLensState) -> ResearchPlan:
    if state.get("plan_status") not in {"approved", "modified"}:
        raise PlanReviewError("Research requires human approval")
    if state.get("current_round") != 0:
        raise PlanReviewError("Only the first research round is supported")
    return validate_plan(state["research_plan"], state["request"])


def collect_github(state: TechLensState, *, collector: GitHubCollector) -> dict[str, object]:
    _approved_plan(state)
    request = state["request"]
    results = []
    for entry, candidate in zip(candidate_catalog(request), request.candidates, strict=True):
        results.append(CandidateEngineeringSignals(
            candidate_id=entry.candidate_id,
            candidate_name=candidate.name,
            repository_url=candidate.repository_url,
            engineering_signals=collector(candidate),
        ))
    return {"github_signals": results}


def web_research(state: TechLensState, *, provider: SearchProvider) -> dict[str, object]:
    plan = _approved_plan(state)
    results = []
    for task in plan.web_tasks:
        result = provider.search(task)
        invalid = False
        try:
            result = WebResearchTaskResult.model_validate(result.model_dump())
        except (ValidationError, AttributeError, TypeError, ValueError):
            invalid = True
        if invalid:
            raise SearchResponseError("Invalid normalized search result")
        if result.task != task:
            raise SearchResponseError("Search result does not match the original task")
        results.append(result)
    return {"web_research_results": results}


def research_complete(state: TechLensState) -> dict[str, object]:
    plan = _approved_plan(state)
    catalog = candidate_catalog(state["request"])
    github = state["github_signals"]
    web = state["web_research_results"]
    expected = [(c.candidate_id, c.name, c.repository_url) for c in catalog]
    actual = [(c.candidate_id, c.candidate_name, c.repository_url) for c in github]
    if actual != expected or [result.task for result in web] != plan.web_tasks:
        raise ValueError("Research collection is incomplete")
    return {
        "current_round": 1,
        "research_notes": [
            f"{len(github)} candidate GitHub signal sets collected.",
            f"{len(web)} web research tasks completed.",
            "Retrieved snippets are untrusted source candidates and have not been evaluated.",
        ],
    }


def normalize_evidence(state: TechLensState) -> dict[str, list[EvidenceRecord]]:
    if state.get("plan_status") not in {"approved", "modified"} or state.get("current_round") != 1:
        raise EvidenceNormalizationError("Research must complete before evidence normalization")
    return {"evidence": normalize_records(
        state["request"], state["research_plan"],
        state["github_signals"], state["web_research_results"],
    )}


def validate_evidence(state: TechLensState) -> dict[str, EvidenceValidationSummary]:
    if state.get("plan_status") not in {"approved", "modified"} or state.get("current_round") != 1:
        raise EvidenceValidationError("Research must complete before evidence validation")
    if "evidence" not in state:
        raise EvidenceValidationError("Evidence is missing")
    return {"evidence_validation": validate_records(
        state["request"], state["research_plan"], state["github_signals"],
        state["web_research_results"], state["evidence"],
    )}


def generate_report_stub(state: TechLensState) -> dict[str, AssessmentReport]:
    request = state["request"]
    if state.get("plan_status") == "rejected":
        summary = "Research plan rejected. No external research performed."
        notes = ["No GitHub or web research calls were made."]
    elif state.get("current_round") == 1:
        if "evidence" not in state or "evidence_validation" not in state:
            raise EvidenceValidationError("Validated evidence is required for a completed report")
        invalid_summary = False
        try:
            raw = state["evidence_validation"]
            validation_summary = EvidenceValidationSummary.model_validate(
                raw.model_dump() if isinstance(raw, EvidenceValidationSummary) else raw
            )
        except (ValidationError, TypeError, AttributeError):
            invalid_summary = True
        if invalid_summary:
            raise EvidenceValidationError("Invalid evidence validation summary")
        summary = "Research collection completed. This report records collection status only."
        notes = [
            *state["research_notes"],
            f"{validation_summary.total_records} evidence records normalized.",
            f"{validation_summary.github_records} GitHub evidence records.",
            f"{validation_summary.web_records} web evidence records.",
            f"{validation_summary.incomplete_github_records} incomplete GitHub observations.",
            "Evidence provenance and integrity validation completed; factual truth was not assessed.",
        ]
    else:
        raise ValueError("Cannot report successful research before completion")
    return {"report": AssessmentReport(
        assessment_id=request.assessment_id,
        candidate_names=[candidate.name for candidate in request.candidates],
        summary=summary,
        research_notes=notes,
        status="stub_complete",
    )}
