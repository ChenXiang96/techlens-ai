"""Typed domain models for the TechLens Day-1 workflow."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

NonBlankString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class TechnologyCandidate(BaseModel):
    """An open-source technology hosted at a GitHub repository root."""

    name: NonBlankString
    repository_url: HttpUrl
    homepage: HttpUrl | None = None

    @field_serializer("repository_url", "homepage")
    def serialize_url(self, value: HttpUrl | None) -> str | None:
        # JsonPlus checkpoints cannot encode Pydantic HttpUrl directly.
        return str(value) if value is not None else None

    @field_validator("repository_url", mode="before")
    @classmethod
    def validate_github_repository_url(cls, value: object) -> object:
        """Accept only an offline-validated GitHub repository-root URL."""
        raw_url = str(value).strip()
        parsed = urlsplit(raw_url)

        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("repository URL contains an invalid port") from error

        if parsed.scheme != "https":
            raise ValueError("repository URL must use https")
        if parsed.hostname != "github.com":
            raise ValueError("repository URL host must be exactly github.com")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("repository URL must not contain credentials")
        if port is not None:
            raise ValueError("repository URL must not contain a port")
        if parsed.query:
            raise ValueError("repository URL must not contain a query")
        if parsed.fragment:
            raise ValueError("repository URL must not contain a fragment")
        if "%" in parsed.path or "\\" in parsed.path:
            raise ValueError("repository URL path must use plain path segments")

        path = parsed.path
        if not path.startswith("/"):
            raise ValueError("repository URL must contain an owner and repository")

        repository_path = path[1:]
        if repository_path.endswith("/"):
            repository_path = repository_path[:-1]

        segments = repository_path.split("/")
        if len(segments) != 2:
            raise ValueError("repository URL must point to a repository root")

        owner, repository = segments
        if repository.endswith(".git"):
            repository = repository[:-4]

        if not owner or not repository:
            raise ValueError("repository URL must contain a non-empty owner and repository")

        return raw_url


class TeamProfile(BaseModel):
    """Available context about the team evaluating the technology."""

    team_size: int | None = Field(default=None, gt=0)
    primary_languages: list[NonBlankString] = Field(default_factory=list)
    backend_stack: list[NonBlankString] = Field(default_factory=list)
    ai_experience: NonBlankString | None = None
    deployment_environment: NonBlankString | None = None
    production_criticality: NonBlankString | None = None
    framework_churn_tolerance: NonBlankString | None = None


class ResearchConstraints(BaseModel):
    """Limits that bound a future research run."""

    max_research_rounds: int = Field(default=2, gt=0)
    max_web_searches: int = Field(default=8, gt=0)
    max_candidates: int = Field(default=3, ge=1, le=3)
    max_run_cost_usd: Decimal = Field(default=Decimal("0.15"), ge=0)


class AssessmentRequest(BaseModel):
    """Validated input for a TechLens assessment."""

    assessment_id: NonBlankString
    candidates: list[TechnologyCandidate] = Field(min_length=1, max_length=3)
    team_profile: TeamProfile
    priorities: list[NonBlankString] = Field(default_factory=list)
    constraints: ResearchConstraints = Field(default_factory=ResearchConstraints)

    @model_validator(mode="after")
    def enforce_configured_candidate_limit(self) -> AssessmentRequest:
        if len(self.candidates) > self.constraints.max_candidates:
            raise ValueError(
                "candidate count exceeds constraints.max_candidates"
            )
        return self


class WebResearchTask(BaseModel):
    """One bounded retrieval task with application-assigned identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: NonBlankString
    candidate_id: NonBlankString | None
    query: NonBlankString
    purpose: NonBlankString


class ResearchPlan(BaseModel):
    """Validated executable plan; limits come only from the original request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: NonBlankString
    research_questions: list[NonBlankString]
    engineering_checks: list[NonBlankString]
    web_tasks: list[WebResearchTask]
    comparison_targets: list[NonBlankString]
    max_rounds: int = Field(gt=0)
    max_searches: int = Field(gt=0)
    max_candidates: int = Field(ge=1, le=3)
    max_run_cost_usd: Decimal = Field(ge=0)


from techlens.report_models import ReportSection, ReportClaim, SourceReference, RunMetrics
from techlens.analysis_models import CandidateAnalysis, ComparisonAnalysis
from techlens.risk_models import RiskFlag


class AssessmentReport(BaseModel):
    """Typed deterministic presentation of cited analysis and limitations."""

    model_config = ConfigDict(extra="forbid")

    assessment_id: NonBlankString
    candidate_names: list[NonBlankString]
    summary: NonBlankString
    research_notes: list[NonBlankString]
    status: Literal["completed", "partial", "rejected", "failed"]
    candidate_ids: list[str] = Field(default_factory=list)
    sections: list[ReportSection] = Field(default_factory=list)
    claims: list[ReportClaim] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    analyses: list[CandidateAnalysis] = Field(default_factory=list)
    comparison: ComparisonAnalysis | None = None
    metrics: RunMetrics | None = None
    team_profile: TeamProfile | None = None
    priorities: list[NonBlankString] = Field(default_factory=list)
