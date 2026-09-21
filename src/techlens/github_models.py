"""Typed GitHub engineering signals collected by TechLens."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

from techlens.models import NonBlankString, TechnologyCandidate

CountCompleteness = Literal["complete", "truncated"]


class GitHubRepositoryRef(BaseModel):
    """Owner and repository name derived from a validated candidate URL."""

    owner: NonBlankString
    repository: NonBlankString

    @classmethod
    def from_candidate(
        cls,
        candidate: TechnologyCandidate,
    ) -> GitHubRepositoryRef:
        path = urlsplit(str(candidate.repository_url)).path.rstrip("/")
        owner, repository = path.removeprefix("/").split("/", maxsplit=1)
        if repository.endswith(".git"):
            repository = repository[:-4]
        return cls(owner=owner, repository=repository)


class BoundedCount(BaseModel):
    """An exact count or a lower bound when collection was truncated."""

    observed: int = Field(ge=0)
    completeness: CountCompleteness


class RepositorySignals(BaseModel):
    """Normalized repository metadata returned by GitHub."""

    owner: NonBlankString
    name: NonBlankString
    full_name: NonBlankString
    archived: bool
    created_at: datetime
    pushed_at: datetime | None
    default_branch: NonBlankString
    license: str | None
    repository_age_days: int = Field(ge=0)


class ReleaseSignals(BaseModel):
    """Published release observations."""

    latest_release_tag: str | None
    latest_release_published_at: datetime | None
    releases_last_90d: BoundedCount
    releases_last_365d: BoundedCount
    pages_fetched: int = Field(ge=0)


class CommitSignals(BaseModel):
    """Commit activity observed in bounded time windows."""

    commits_last_30d: BoundedCount
    commits_last_90d: BoundedCount
    pages_fetched: int = Field(ge=0)


class IssueSignals(BaseModel):
    """Issue-only observations with pull requests excluded."""

    open_issues_observed: BoundedCount
    recently_updated_open_issues_90d: BoundedCount
    pages_fetched: int = Field(ge=0)


class PullRequestSignals(BaseModel):
    """Open and recently merged pull-request observations."""

    open_prs_observed: BoundedCount
    merged_prs_last_90d: BoundedCount
    open_pages_fetched: int = Field(ge=0)
    closed_pages_fetched: int = Field(ge=0)


class ContributorConcentrationProxy(BaseModel):
    """Contribution shares over the contributor population observed."""

    contributors_observed: BoundedCount
    total_contributions_observed: int = Field(ge=0)
    top_contributor_share: float | None = Field(default=None, ge=0, le=1)
    top_3_contributor_share: float | None = Field(default=None, ge=0, le=1)
    pages_fetched: int = Field(ge=0)


class PracticeIndicator(BaseModel):
    """A path-based practice observation and its evidence completeness."""

    detected: bool
    evidence_complete: bool
    matched_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_detection_evidence(self) -> PracticeIndicator:
        if self.detected != bool(self.matched_paths):
            raise ValueError("detected must agree with matched_paths")
        return self


class RepositoryPracticeSignals(BaseModel):
    """Deterministic repository-path practice indicators."""

    ci: PracticeIndicator
    tests: PracticeIndicator
    security_policy: PracticeIndicator
    dependency_automation: PracticeIndicator
    tree_truncated: bool
    tree_entries_observed: int = Field(ge=0)


class CollectionMetadata(BaseModel):
    """Operational metadata for a successful GitHub collection."""

    collected_at: datetime
    github_api_calls_made: int = Field(ge=0)
    authenticated: bool
    rate_limit_limit: int | None = Field(default=None, ge=0)
    rate_limit_remaining: int | None = Field(default=None, ge=0)
    rate_limit_reset_at: datetime | None = None
    truncated_endpoints: list[str] = Field(default_factory=list)
    api_version: Literal["2026-03-10"]


class EngineeringSignals(BaseModel):
    """Observable GitHub engineering facts without scores or judgments."""

    repository: RepositorySignals
    releases: ReleaseSignals
    commits: CommitSignals
    issues: IssueSignals
    pull_requests: PullRequestSignals
    contributors: ContributorConcentrationProxy
    repository_practices: RepositoryPracticeSignals
    collection_metadata: CollectionMetadata

