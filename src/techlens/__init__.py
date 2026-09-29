"""Public API for TechLens human-supervised research collection."""

from techlens.graph import build_graph
from techlens.evidence import (
    ClaimEvidenceContractError, EvidenceError, EvidenceNormalizationError,
    EvidenceValidationError, normalize_records, validate_claim_evidence_contract,
    validate_records,
)
from techlens.evidence_models import (
    BoundedObservedValue, ClaimRecord, EvidenceRecord, EvidenceValidationSummary,
    GitHubEvidencePayload, WebEvidencePayload,
)
from techlens.github_client import (
    GitHubAuthenticationError,
    GitHubCollectionError,
    GitHubRateLimitError,
    GitHubRepositoryUnavailableError,
    GitHubResponseError,
    GitHubTransportError,
    collect_engineering_signals,
)
from techlens.github_models import (
    BoundedCount,
    CollectionMetadata,
    CommitSignals,
    ContributorConcentrationProxy,
    EngineeringSignals,
    GitHubRepositoryRef,
    IssueSignals,
    PracticeIndicator,
    PullRequestSignals,
    ReleaseSignals,
    RepositoryPracticeSignals,
    RepositorySignals,
)
from techlens.models import (
    AssessmentReport,
    AssessmentRequest,
    ResearchConstraints,
    ResearchPlan,
    TeamProfile,
    TechnologyCandidate,
    WebResearchTask,
)
from techlens.openai_planner import OpenAIResearchPlanner
from techlens.planning import ResearchPlanner
from techlens.research_models import (
    CandidateEngineeringSignals, PlanReviewDecision, PlannerCandidate,
    WebResearchTaskResult, WebSearchResult,
)
from techlens.search import SearchProvider
from techlens.state import TechLensState
from techlens.tavily_search import TavilySearchProvider
from techlens.openai_analyzer import OpenAIAdoptionAnalyzer
from techlens.analysis import AdoptionAnalyzer
from techlens.budget import RateCard, BudgetLedger, VerifiedInputCount
from techlens.execution import AttemptExecutor
from techlens.collection_models import CollectionCoverage, CollectionOutcome
from techlens.risk_models import RiskRuleConfig, RiskEvaluation, RiskFlag
from techlens.report_models import RunMetrics

__all__ = [
    "OpenAIAdoptionAnalyzer", "AdoptionAnalyzer", "RateCard", "BudgetLedger", "VerifiedInputCount",
    "AttemptExecutor", "CollectionCoverage", "CollectionOutcome", "RiskRuleConfig", "RiskEvaluation",
    "RiskFlag", "RunMetrics",
    "BoundedObservedValue",
    "ClaimEvidenceContractError",
    "ClaimRecord",
    "EvidenceError",
    "EvidenceNormalizationError",
    "EvidenceRecord",
    "EvidenceValidationError",
    "EvidenceValidationSummary",
    "GitHubEvidencePayload",
    "WebEvidencePayload",
    "normalize_records",
    "validate_claim_evidence_contract",
    "validate_records",
    "CandidateEngineeringSignals",
    "OpenAIResearchPlanner",
    "PlanReviewDecision",
    "PlannerCandidate",
    "ResearchPlanner",
    "SearchProvider",
    "TavilySearchProvider",
    "WebResearchTask",
    "WebResearchTaskResult",
    "WebSearchResult",
    "AssessmentReport",
    "AssessmentRequest",
    "BoundedCount",
    "CollectionMetadata",
    "CommitSignals",
    "ContributorConcentrationProxy",
    "EngineeringSignals",
    "GitHubAuthenticationError",
    "GitHubCollectionError",
    "GitHubRateLimitError",
    "GitHubRepositoryRef",
    "GitHubRepositoryUnavailableError",
    "GitHubResponseError",
    "GitHubTransportError",
    "IssueSignals",
    "PracticeIndicator",
    "PullRequestSignals",
    "ReleaseSignals",
    "RepositoryPracticeSignals",
    "RepositorySignals",
    "ResearchConstraints",
    "ResearchPlan",
    "TeamProfile",
    "TechLensState",
    "TechnologyCandidate",
    "build_graph",
    "collect_engineering_signals",
]
