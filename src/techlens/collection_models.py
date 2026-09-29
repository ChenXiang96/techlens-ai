"""Application-owned terminal collection coverage, including explicit gaps."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CollectionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str
    source_kind: Literal["github", "web"]
    target_id: str
    status: Literal["succeeded", "failed", "skipped_budget", "skipped_limit"]
    error_category: str | None = None
    attempt_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self):
        if self.operation_id != f"{self.source_kind}:{self.target_id}":
            raise ValueError("Invalid collection operation identity")
        if (self.status == "succeeded") != (self.error_category is None):
            raise ValueError("Outcome category disagrees with status")
        if len(set(self.attempt_ids)) != len(self.attempt_ids):
            raise ValueError("Duplicate attempt identity")
        return self


class CollectionCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    github_outcomes: list[CollectionOutcome]
    web_outcomes: list[CollectionOutcome]


def check_coverage(coverage, request, plan, github, web):
    """Require one ordered terminal outcome per scheduled operation."""
    from techlens.planning import candidate_catalog
    coverage = CollectionCoverage.model_validate(coverage.model_dump())
    expected = [c.candidate_id for c in candidate_catalog(request)]
    for kind, outcomes, targets, collected in (
        ("github", coverage.github_outcomes, expected, [g.candidate_id for g in github]),
        ("web", coverage.web_outcomes, [t.task_id for t in plan.web_tasks],
         [w.task.task_id for w in web]),
    ):
        if [o.target_id for o in outcomes] != targets or any(o.source_kind != kind for o in outcomes):
            raise ValueError("Collection outcome coverage differs")
        if [o.target_id for o in outcomes if o.status == "succeeded"] != collected:
            raise ValueError("Collection outcomes differ from successful sources")
    return coverage
