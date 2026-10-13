"""Bounded, strict browser input. No execution authority or provider configuration."""
import json
import re
from decimal import Decimal
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from techlens.configuration import ApplicationError, parse_model
from techlens.models import AssessmentRequest, ResearchPlan
from techlens.planning import request_limits, validate_plan, PlannerError
from techlens.presentation import safe_url


class UIError(Exception):
    """Fixed public diagnostic, never retains input/source exceptions."""
    def __init__(self, message="Invalid local interface request.", status=400):
        super().__init__(message)
        self.status = status


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Text = Annotated[str, StringConstraints(max_length=256)]
URL = Annotated[str, StringConstraints(max_length=2048)]
Description = Annotated[str, StringConstraints(max_length=1024)]
ListText = Annotated[list[Text], Field(max_length=20)]


class CandidateForm(Strict):
    name: Text
    repository_url: URL
    homepage: URL = ""


class RequestForm(Strict):
    assessment_id: Annotated[str, StringConstraints(max_length=128)] = ""
    candidates: Annotated[list[CandidateForm], Field(min_length=1, max_length=3)]
    team_size: Annotated[str, StringConstraints(max_length=9)] = ""
    primary_languages: ListText = Field(default_factory=list)
    backend_stack: ListText = Field(default_factory=list)
    priorities: ListText = Field(default_factory=list)
    ai_experience: Description = ""
    deployment_environment: Description = ""
    production_criticality: Description = ""
    framework_churn_tolerance: Description = ""
    max_research_rounds: Literal["1", "2"] = "1"
    max_web_searches: Literal["1", "2", "3", "4", "5", "6", "7", "8"] = "2"
    max_run_cost_usd: Annotated[str, StringConstraints(max_length=32)] = "0"

    def request(self):
        failed = False
        try:
            budget = self.max_run_cost_usd.strip() or "0"
            size = self.team_size.strip()
            if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", budget):
                raise ValueError()
            if size and (not re.fullmatch(r"[0-9]{1,9}", size) or int(size) < 1):
                raise ValueError()
            value = self.model_dump()
            team = {key: value[key].strip() or None for key in (
                "ai_experience", "deployment_environment", "production_criticality", "framework_churn_tolerance")}
            team.update(team_size=int(size) if size else None,
                        primary_languages=self.primary_languages, backend_stack=self.backend_stack)
            candidates = []
            for item in self.candidates:
                repository, homepage = item.repository_url.strip(), item.homepage.strip()
                safe_url(repository)
                if homepage:
                    safe_url(homepage)
                candidates.append({**item.model_dump(), "repository_url": repository, "homepage": homepage or None})
            result = parse_model({"assessment_id": self.assessment_id.strip() or "assessment-" + uuid4().hex,
                "candidates": candidates, "team_profile": team, "priorities": self.priorities,
                "constraints": {"max_candidates": len(candidates),
                    "max_research_rounds": int(self.max_research_rounds),
                    "max_web_searches": int(self.max_web_searches),
                    "max_run_cost_usd": Decimal(budget)}}, AssessmentRequest)
        except (ValueError, TypeError, ApplicationError):
            failed = True
        if failed:
            raise UIError("Request fields are invalid. No request was prepared.")
        return result


class WebTaskEdit(Strict):
    candidate_id: Text | None
    query: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    purpose: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class PlanEdit(Strict):
    objective: Annotated[str, StringConstraints(min_length=1, max_length=8192)]
    research_questions: Annotated[list[URL], Field(max_length=20)]
    engineering_checks: Annotated[list[URL], Field(max_length=20)]
    web_tasks: Annotated[list[WebTaskEdit], Field(max_length=8)]

    def plan(self, request):
        failed = False
        try:
            from techlens.planning import candidate_catalog
            value = self.model_dump()
            value["web_tasks"] = [{"task_id": f"web-{i:03d}", **task} for i, task in enumerate(value["web_tasks"], 1)]
            value.update(comparison_targets=[c.candidate_id for c in candidate_catalog(request)], **request_limits(request))
            result = validate_plan(ResearchPlan.model_validate(value), request)
        except (ValueError, TypeError, PlannerError):
            failed = True
        if failed:
            raise UIError("Modified plan is invalid; research remains unapproved.")
        return result


class Action(Strict):
    token: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    revision: int = Field(ge=0)
    data: dict = Field(default_factory=dict)


def parse(model, value):
    failed = False
    try:
        result = model.model_validate(value)
    except (ValueError, TypeError):
        failed = True
    if failed:
        raise UIError()
    return result


def decode_json(data):
    """Complete body first; bound nesting before the JSON parser recurses."""
    failed = False
    try:
        text = data.decode("utf-8", "strict")
        depth, quoted, escape = 0, False, False
        for c in text:
            if quoted:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    quoted = False
            elif c == '"':
                quoted = True
            elif c in "[{":
                depth += 1
                if depth > 32:
                    raise ValueError()
            elif c in "]}":
                depth -= 1
        def pairs(items):
            result = {}
            for k, v in items:
                if k in result:
                    raise ValueError()
                result[k] = v
            return result
        def invalid(_):
            raise ValueError()
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
        if not isinstance(result, dict):
            raise ValueError()
    except (ValueError, UnicodeError, RecursionError):
        failed = True
    if failed:
        raise UIError("Invalid bounded JSON body.")
    return result
