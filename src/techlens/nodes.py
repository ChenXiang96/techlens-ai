"""Deterministic nodes for the TechLens Day-1 graph."""

from techlens.models import AssessmentReport, AssessmentRequest, ResearchPlan
from techlens.state import TechLensState


def validate_request(state: TechLensState) -> dict[str, object]:
    """Validate the graph request and initialize the T-001 run state."""
    request = AssessmentRequest.model_validate(state["request"])
    return {
        "request": request,
        "current_round": 0,
        "research_notes": [],
        "errors": [],
    }


def plan_research(state: TechLensState) -> dict[str, ResearchPlan]:
    """Create a deterministic plan without invoking a model or service."""
    request = state["request"]
    candidate_names = [candidate.name for candidate in request.candidates]
    target_text = ", ".join(candidate_names)
    priority_text = ", ".join(request.priorities) or "no stated priorities"

    plan = ResearchPlan(
        objective=(
            f"Assess production adoption suitability for {target_text} against "
            f"the supplied team context and priorities ({priority_text})."
        ),
        research_questions=[
            f"What production capabilities and constraints apply to {target_text}?",
            "How well would each candidate align with the supplied team context?",
            "What repository evidence would be needed for an adoption decision?",
        ],
        engineering_checks=[
            "Review maintenance and release signals.",
            "Review documentation, testing, and operational maturity.",
            "Review integration fit and adoption risks.",
        ],
        web_tasks=[
            (
                f"Planned repository-root review for {candidate.name}: "
                f"{candidate.repository_url}"
            )
            for candidate in request.candidates
        ],
        comparison_targets=candidate_names,
        max_rounds=request.constraints.max_research_rounds,
        max_searches=request.constraints.max_web_searches,
    )
    return {"research_plan": plan}


def research_stub(state: TechLensState) -> dict[str, list[str]]:
    """Summarize the plan deterministically without performing research."""
    plan = state.get("research_plan")
    if not isinstance(plan, ResearchPlan):
        raise ValueError("research_stub requires a ResearchPlan")

    notes = [
        "T-001 research stub: no external research was performed.",
        f"Planned objective: {plan.objective}",
        (
            f"Plan contains {len(plan.research_questions)} research questions, "
            f"{len(plan.engineering_checks)} engineering checks, and "
            f"{len(plan.web_tasks)} unexecuted web tasks."
        ),
    ]
    return {"research_notes": notes}


def generate_report_stub(state: TechLensState) -> dict[str, AssessmentReport]:
    """Create a typed stub report without claiming an adoption finding."""
    request = state["request"]
    candidate_names = [candidate.name for candidate in request.candidates]
    target_text = ", ".join(candidate_names)
    report = AssessmentReport(
        assessment_id=request.assessment_id,
        candidate_names=candidate_names,
        summary=(
            f"T-001 completed a deterministic stub assessment for {target_text}. "
            "No external evidence was collected and no adoption recommendation "
            "was produced."
        ),
        research_notes=state.get("research_notes", []),
        status="stub_complete",
    )
    return {"report": report}

