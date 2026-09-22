"""Planner contract and deterministic validation against the authoritative request."""

import re
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError

from techlens.github_models import GitHubRepositoryRef
from techlens.models import AssessmentRequest, ResearchPlan
from techlens.research_models import PlannerCandidate


class PlannerError(Exception):
    """Sanitized planner failure; never stores a provider exception or response."""


class PlannerConfigurationError(PlannerError):
    pass


class PlannerProviderError(PlannerError):
    def __init__(self, status_code: int | None = None):
        super().__init__("OpenAI planner provider request failed")
        self.status_code = status_code


class PlannerTransportError(PlannerError):
    def __init__(self, kind: str):
        super().__init__(f"OpenAI planner {kind} failure")
        self.kind = kind


class PlannerValidationError(PlannerError):
    pass


class PlannerResponseError(PlannerError):
    pass


class PlanReviewError(Exception):
    """Invalid human review; external research must not start."""


class ResearchPlanner(Protocol):
    def plan(self, request: AssessmentRequest) -> ResearchPlan: ...


def candidate_catalog(request: AssessmentRequest) -> list[PlannerCandidate]:
    return [
        PlannerCandidate(
            candidate_id=f"candidate-{index:03d}",
            name=candidate.name,
            repository_url=candidate.repository_url,
        )
        for index, candidate in enumerate(request.candidates, start=1)
    ]


def request_limits(request: AssessmentRequest) -> dict[str, object]:
    constraints = request.constraints
    return {
        "max_rounds": constraints.max_research_rounds,
        "max_searches": constraints.max_web_searches,
        "max_candidates": constraints.max_candidates,
        "max_run_cost_usd": constraints.max_run_cost_usd,
    }


def validate_plan(value: object, request: AssessmentRequest) -> ResearchPlan:
    # Round-trip models so mutated lists and model_construct cannot bypass checks.
    invalid = False
    plan = None
    try:
        raw = value.model_dump() if isinstance(value, BaseModel) else value
        plan = ResearchPlan.model_validate(raw)
    except (ValidationError, TypeError, ValueError):
        invalid = True
    if invalid or plan is None:
        raise PlannerValidationError("Invalid research plan structure")

    ids = [item.candidate_id for item in candidate_catalog(request)]
    if plan.comparison_targets != ids:
        raise PlannerValidationError("Comparison targets must match original candidate IDs in order")
    if any(getattr(plan, key) != value for key, value in request_limits(request).items()):
        raise PlannerValidationError("Plan limits must equal original request constraints")
    if len(plan.web_tasks) > request.constraints.max_web_searches:
        raise PlannerValidationError("Plan exceeds the web search limit")
    for index, task in enumerate(plan.web_tasks, start=1):
        if task.task_id != f"web-{index:03d}":
            raise PlannerValidationError("Task IDs must follow the canonical web-NNN sequence")
        if task.candidate_id is not None and task.candidate_id not in ids:
            raise PlannerValidationError("Task refers to an unknown candidate ID")

    # Text is never an executable repository selector. Also reject explicit
    # GitHub links to repositories outside the original request.
    allowed = {
        (ref.owner.casefold(), ref.repository.casefold())
        for ref in (GitHubRepositoryRef.from_candidate(c) for c in request.candidates)
    }
    texts = [plan.objective, *plan.research_questions, *plan.engineering_checks]
    texts.extend(text for task in plan.web_tasks for text in (task.query, task.purpose))
    for text in texts:
        for match in re.finditer(r"\b(?:https?://)?github\.com/([^\s<>\"\)\]]+)", text, re.I):
            path = urlsplit("https://github.com/" + match.group(1).rstrip(".,;:")).path.strip("/")
            parts = path.split("/")
            if len(parts) < 2:
                raise PlannerValidationError("Plan contains an invalid GitHub repository reference")
            repository = parts[1].removesuffix(".git")
            if (parts[0].casefold(), repository.casefold()) not in allowed:
                raise PlannerValidationError("Plan references a repository outside the request")
    return plan
