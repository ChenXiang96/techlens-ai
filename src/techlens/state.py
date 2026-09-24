"""Minimal checkpointed state for human-supervised T-003 research."""

from typing import Literal, NotRequired, Required, TypedDict

from techlens.evidence_models import EvidenceRecord, EvidenceValidationSummary
from techlens.models import AssessmentReport, AssessmentRequest, ResearchPlan
from techlens.research_models import CandidateEngineeringSignals, WebResearchTaskResult


class TechLensState(TypedDict):
    """Contains domain data only, never provider clients or credentials."""

    request: Required[AssessmentRequest]
    research_plan: NotRequired[ResearchPlan]
    plan_status: NotRequired[Literal["pending", "approved", "modified", "rejected"]]
    current_round: NotRequired[int]
    github_signals: NotRequired[list[CandidateEngineeringSignals]]
    web_research_results: NotRequired[list[WebResearchTaskResult]]
    evidence: NotRequired[list[EvidenceRecord]]
    evidence_validation: NotRequired[EvidenceValidationSummary]
    research_notes: NotRequired[list[str]]
    report: NotRequired[AssessmentReport]
    errors: NotRequired[list[str]]
