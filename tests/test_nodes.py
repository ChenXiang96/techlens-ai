import pytest
from pydantic import ValidationError

from techlens.models import (
    AssessmentReport,
    AssessmentRequest,
    ResearchPlan,
    TeamProfile,
    TechnologyCandidate,
)
from techlens.nodes import (
    generate_report_stub,
    plan_research,
    research_stub,
    validate_request,
)
from techlens.state import TechLensState


def make_request() -> AssessmentRequest:
    return AssessmentRequest(
        assessment_id="assessment-001",
        candidates=[
            TechnologyCandidate(
                name="LangGraph",
                repository_url="https://github.com/langchain-ai/langgraph",
            )
        ],
        team_profile=TeamProfile(primary_languages=["Python"]),
        priorities=["Production readiness"],
    )


def test_validate_request_initializes_state() -> None:
    update = validate_request({"request": make_request()})

    assert isinstance(update["request"], AssessmentRequest)
    assert update["current_round"] == 0
    assert update["research_notes"] == []
    assert update["errors"] == []


def test_validate_request_rejects_invalid_raw_request() -> None:
    invalid_state = {
        "request": {
            "assessment_id": "assessment-001",
            "candidates": [],
            "team_profile": {},
        }
    }

    with pytest.raises(ValidationError):
        validate_request(invalid_state)  # type: ignore[arg-type]


def test_planner_is_typed_and_deterministic() -> None:
    state: TechLensState = {"request": make_request()}

    first_plan = plan_research(state)["research_plan"]
    second_plan = plan_research(state)["research_plan"]

    assert isinstance(first_plan, ResearchPlan)
    assert first_plan == second_plan
    assert first_plan.comparison_targets == ["LangGraph"]
    assert first_plan.max_rounds == 2
    assert first_plan.max_searches == 8


def test_research_stub_consumes_plan_without_modifying_round() -> None:
    state: TechLensState = {"request": make_request(), "current_round": 0}
    state.update(plan_research(state))

    update = research_stub(state)

    assert "Planned objective:" in update["research_notes"][1]
    assert "current_round" not in update
    assert state["current_round"] == 0


def test_research_stub_requires_a_plan() -> None:
    with pytest.raises(ValueError, match="requires a ResearchPlan"):
        research_stub({"request": make_request()})


def test_report_stub_returns_typed_output() -> None:
    state: TechLensState = {
        "request": make_request(),
        "research_notes": ["Deterministic stub note."],
    }

    report = generate_report_stub(state)["report"]

    assert isinstance(report, AssessmentReport)
    assert report.status == "stub_complete"
    assert report.research_notes == ["Deterministic stub note."]
    assert "no adoption recommendation" in report.summary

