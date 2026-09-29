"""Minimal checkpointed state for human-supervised T-003 research."""

from typing import Literal, NotRequired, Required, TypedDict

from techlens.evidence_models import EvidenceRecord, EvidenceValidationSummary
from techlens.models import AssessmentReport, AssessmentRequest, ResearchPlan
from techlens.research_models import CandidateEngineeringSignals, WebResearchTaskResult
from techlens.budget import BudgetLedger
from techlens.collection_models import CollectionCoverage, CollectionOutcome
from techlens.risk_models import RiskEvaluation, RiskFlag
from techlens.analysis_models import AnalysisProjection, AnalysisDraft, AnalysisResult
from techlens.evidence_models import ClaimRecord
from techlens.report_models import RunMetrics


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
    budget_ledger: NotRequired[BudgetLedger]
    github_outcomes: NotRequired[list[CollectionOutcome]]
    web_outcomes: NotRequired[list[CollectionOutcome]]
    collection_coverage: NotRequired[CollectionCoverage]
    risk_evaluations: NotRequired[list[RiskEvaluation]]
    risk_flags: NotRequired[list[RiskFlag]]
    analysis_projection: NotRequired[AnalysisProjection]
    analysis_envelope: NotRequired[dict]
    analysis_draft: NotRequired[AnalysisDraft]
    analysis_status: NotRequired[str]
    analysis_result: NotRequired[AnalysisResult]
    claims: NotRequired[list[ClaimRecord]]
    run_metrics: NotRequired[RunMetrics]
    fatal_failure: NotRequired[str]
