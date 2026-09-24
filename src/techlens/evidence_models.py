"""Typed evidence and claim-reference contracts; no factual assessment."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel, ConfigDict, Field, HttpUrl, StrictBool, StrictFloat, StrictInt,
    StrictStr, TypeAdapter, field_validator, model_validator,
)

from techlens.models import NonBlankString

GitHubMetricKey = Literal[
    "repository.archived", "repository.created_at", "repository.pushed_at",
    "repository.default_branch", "repository.license", "repository.repository_age_days",
    "releases.latest_release_tag", "releases.latest_release_published_at",
    "releases.releases_last_90d", "releases.releases_last_365d",
    "commits.commits_last_30d", "commits.commits_last_90d",
    "issues.open_issues_observed", "issues.recently_updated_open_issues_90d",
    "pull_requests.open_prs_observed", "pull_requests.merged_prs_last_90d",
    "contributors.contributors_observed", "contributors.total_contributions_observed",
    "contributors.top_contributor_share", "contributors.top_3_contributor_share",
    "repository_practices.ci", "repository_practices.tests",
    "repository_practices.security_policy", "repository_practices.dependency_automation",
]

EvidenceCompleteness = Literal["complete", "truncated", "unknown"]
_COUNT_KEYS = {
    "releases.releases_last_90d", "releases.releases_last_365d",
    "commits.commits_last_30d", "commits.commits_last_90d",
    "issues.open_issues_observed", "issues.recently_updated_open_issues_90d",
    "pull_requests.open_prs_observed", "pull_requests.merged_prs_last_90d",
    "contributors.contributors_observed",
}
_PRACTICE_KEYS = {
    "repository_practices.ci", "repository_practices.tests",
    "repository_practices.security_policy", "repository_practices.dependency_automation",
}
_CONTRIBUTOR_KEYS = {
    "contributors.total_contributions_observed", "contributors.top_contributor_share",
    "contributors.top_3_contributor_share",
}
_DATETIME_KEYS = {
    "repository.created_at", "repository.pushed_at",
    "releases.latest_release_published_at",
}
_NULLABLE_STRING_KEYS = {"repository.license", "releases.latest_release_tag"}


class BoundedObservedValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed: int = Field(ge=0, strict=True)


class GitHubEvidencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["github"] = "github"
    metric_key: GitHubMetricKey
    value: StrictBool | StrictInt | StrictFloat | StrictStr | BoundedObservedValue | None
    completeness: EvidenceCompleteness
    collected_at: str
    evidence_complete: bool | None = None
    matched_paths: list[str] = Field(default_factory=list)
    population_scope: Literal["observed_contributors"] | None = None

    @model_validator(mode="after")
    def check_metric_shape(self) -> "GitHubEvidencePayload":
        key = self.metric_key
        value = self.value
        if key in _COUNT_KEYS:
            valid = isinstance(value, BoundedObservedValue) and self.completeness != "unknown"
        elif key in _PRACTICE_KEYS:
            valid = (
                isinstance(value, bool)
                and self.evidence_complete is not None
                and value == bool(self.matched_paths)
                and self.completeness == (
                    "complete" if self.evidence_complete else "truncated" if value else "unknown"
                )
            )
        elif key in _CONTRIBUTOR_KEYS:
            valid = self.completeness != "unknown" and self.population_scope == "observed_contributors"
            if key == "contributors.total_contributions_observed":
                valid = valid and type(value) is int and value >= 0
            else:
                valid = valid and (value is None or type(value) is float and 0 <= value <= 1)
        elif key in _DATETIME_KEYS:
            valid = (key != "repository.created_at" or value is not None) and (
                value is None or type(value) is str and value.endswith("Z")
            ) and self.completeness == "complete"
        elif key in _NULLABLE_STRING_KEYS:
            valid = (value is None or type(value) is str) and self.completeness == "complete"
        elif key == "repository.archived":
            valid = type(value) is bool and self.completeness == "complete"
        elif key == "repository.repository_age_days":
            valid = type(value) is int and value >= 0 and self.completeness == "complete"
        else:
            valid = type(value) is str and self.completeness == "complete"
        if not valid:
            raise ValueError("Invalid GitHub evidence metric shape")
        if key not in _PRACTICE_KEYS and (self.evidence_complete is not None or self.matched_paths):
            raise ValueError("Practice details require a practice metric")
        if key not in _CONTRIBUTOR_KEYS and self.population_scope is not None:
            raise ValueError("Population scope requires a contributor metric")
        return self


class WebEvidencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["web"] = "web"
    task_id: NonBlankString
    query: NonBlankString
    provider: NonBlankString
    content: str
    score: float = Field(allow_inf_nan=False)
    published_date: str | None = None


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: NonBlankString
    candidate_id: NonBlankString | None
    source_kind: Literal["github", "web"]
    title: str
    source_url: str
    payload: Annotated[GitHubEvidencePayload | WebEvidencePayload, Field(discriminator="kind")]

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        parsed = TypeAdapter(HttpUrl).validate_python(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Source URL must not contain credentials")
        return value  # Keep the provider's exact URL spelling.

    @model_validator(mode="after")
    def check_source_kind(self) -> "EvidenceRecord":
        if self.source_kind != self.payload.kind:
            raise ValueError("Source kind and payload kind differ")
        if self.source_kind == "github" and self.candidate_id is None:
            raise ValueError("GitHub evidence requires a candidate ID")
        return self


class EvidenceValidationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["validated"] = "validated"
    total_records: int = Field(ge=0)
    github_records: int = Field(ge=0)
    web_records: int = Field(ge=0)
    incomplete_github_records: int = Field(ge=0)
    untrusted_web_records: int = Field(ge=0)

    @model_validator(mode="after")
    def check_counts(self) -> "EvidenceValidationSummary":
        if (self.total_records != self.github_records + self.web_records
                or self.incomplete_github_records > self.github_records
                or self.untrusted_web_records != self.web_records):
            raise ValueError("Evidence validation counts are inconsistent")
        return self


class ClaimRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: NonBlankString
    statement: NonBlankString
    candidate_ids: list[NonBlankString] = Field(default_factory=list)
    evidence_ids: list[NonBlankString] = Field(min_length=1)
