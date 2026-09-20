"""LangGraph state for the TechLens Day-1 workflow."""

from typing import NotRequired, Required, TypedDict

from techlens.models import AssessmentReport, AssessmentRequest, ResearchPlan


class TechLensState(TypedDict):
    """Minimal state used by the four deterministic T-001 nodes."""

    request: Required[AssessmentRequest]
    research_plan: NotRequired[ResearchPlan]
    current_round: NotRequired[int]
    research_notes: NotRequired[list[str]]
    report: NotRequired[AssessmentReport]
    errors: NotRequired[list[str]]

