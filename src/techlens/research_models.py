"""Review decisions and retrieved source candidates, not evaluated evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_serializer, field_validator, model_validator

from techlens.github_models import EngineeringSignals
from techlens.models import NonBlankString, ResearchPlan, WebResearchTask


class PlannerCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: NonBlankString
    name: NonBlankString
    repository_url: HttpUrl

    @field_serializer("repository_url")
    def serialize_url(self, value: HttpUrl) -> str:
        return str(value)


class PlanReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "modify", "reject"]
    modified_plan: ResearchPlan | None = None

    @model_validator(mode="after")
    def check_modified_plan(self) -> "PlanReviewDecision":
        if (self.action == "modify") != (self.modified_plan is not None):
            raise ValueError("only modify requires a modified_plan")
        return self


class CandidateEngineeringSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: NonBlankString
    candidate_name: NonBlankString
    repository_url: HttpUrl
    engineering_signals: EngineeringSignals

    @field_serializer("repository_url")
    def serialize_url(self, value: HttpUrl) -> str:
        return str(value)


class WebSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    url: str
    content: str
    score: float = Field(allow_inf_nan=False)
    published_date: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        # Validate without canonicalizing the provider's source URL spelling.
        parsed = TypeAdapter(HttpUrl).validate_python(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("source URL must not contain credentials")
        return value


class WebResearchTaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: WebResearchTask
    provider: NonBlankString
    results: list[WebSearchResult] = Field(max_length=3)
    credits_used: int | None = Field(default=None, ge=0, strict=True)
