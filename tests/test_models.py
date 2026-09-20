from decimal import Decimal

import pytest
from pydantic import ValidationError

from techlens.models import (
    AssessmentRequest,
    ResearchConstraints,
    TeamProfile,
    TechnologyCandidate,
)


VALID_REPOSITORY_URL = "https://github.com/langchain-ai/langgraph"


def make_candidate(
    *,
    name: str = "LangGraph",
    repository_url: str = VALID_REPOSITORY_URL,
) -> TechnologyCandidate:
    return TechnologyCandidate(name=name, repository_url=repository_url)


def make_request(**overrides: object) -> AssessmentRequest:
    values: dict[str, object] = {
        "assessment_id": "assessment-001",
        "candidates": [make_candidate()],
        "team_profile": TeamProfile(),
    }
    values.update(overrides)
    return AssessmentRequest.model_validate(values)


def test_valid_request() -> None:
    request = make_request(
        team_profile=TeamProfile(
            team_size=6,
            primary_languages=["Python"],
            backend_stack=["Django"],
        ),
        priorities=["Production readiness"],
    )

    assert request.assessment_id == "assessment-001"
    assert request.candidates[0].name == "LangGraph"
    assert request.constraints.max_research_rounds == 2


@pytest.mark.parametrize(
    "repository_url",
    [
        "https://github.com/langchain-ai/langgraph",
        "https://github.com/langchain-ai/langgraph/",
        "https://github.com/langchain-ai/langgraph.git",
    ],
)
def test_github_repository_root_url_variants_are_accepted(
    repository_url: str,
) -> None:
    candidate = make_candidate(repository_url=repository_url)

    assert str(candidate.repository_url).startswith(
        "https://github.com/langchain-ai/langgraph"
    )


@pytest.mark.parametrize(
    "repository_url",
    [
        "https://example.com/foo/bar",
        "https://gitlab.com/foo/bar",
        "https://github.com/",
        "not-a-url",
        "https://github.com/langchain-ai/langgraph/issues/1",
        "https://github.com/langchain-ai/langgraph/pull/1",
        "https://github.com/langchain-ai/langgraph/tree/main",
        "http://github.com/langchain-ai/langgraph",
        "https://user@github.com/langchain-ai/langgraph",
        "https://github.com:443/langchain-ai/langgraph",
        "https://github.com/langchain-ai/langgraph?tab=readme",
        "https://github.com/langchain-ai/langgraph#readme",
        "https://github.com/langchain-ai/.git",
        "https://github.com//langgraph",
        "https://github.com/langchain-ai/langgraph//",
    ],
)
def test_invalid_repository_urls_are_rejected(repository_url: str) -> None:
    with pytest.raises(ValidationError):
        make_candidate(repository_url=repository_url)


def test_zero_candidates_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_request(candidates=[])


def test_four_candidates_are_rejected() -> None:
    candidates = [
        make_candidate(name=f"Candidate {index}") for index in range(4)
    ]

    with pytest.raises(ValidationError):
        make_request(candidates=candidates)


def test_configured_candidate_limit_is_enforced() -> None:
    candidates = [make_candidate(name="One"), make_candidate(name="Two")]

    with pytest.raises(ValidationError):
        make_request(
            candidates=candidates,
            constraints=ResearchConstraints(max_candidates=1),
        )


def test_negative_budget_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResearchConstraints(max_run_cost_usd=Decimal("-0.01"))


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_research_rounds", 0),
        ("max_research_rounds", -1),
        ("max_web_searches", 0),
        ("max_web_searches", -1),
        ("max_candidates", 0),
        ("max_candidates", 4),
    ],
)
def test_invalid_research_limits_are_rejected(
    field_name: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        ResearchConstraints.model_validate({field_name: value})


def test_optional_lists_default_to_independent_empty_lists() -> None:
    first_profile = TeamProfile()
    second_profile = TeamProfile()
    first_request = make_request()
    second_request = make_request()

    assert first_profile.primary_languages == []
    assert first_profile.backend_stack == []
    assert first_request.priorities == []

    first_profile.primary_languages.append("Python")
    first_profile.backend_stack.append("Django")
    first_request.priorities.append("Maturity")

    assert second_profile.primary_languages == []
    assert second_profile.backend_stack == []
    assert second_request.priorities == []


def test_explicit_empty_optional_lists_are_accepted() -> None:
    request = make_request(
        team_profile=TeamProfile(primary_languages=[], backend_stack=[]),
        priorities=[],
    )

    assert request.team_profile.primary_languages == []
    assert request.team_profile.backend_stack == []
    assert request.priorities == []


def test_optional_list_values_are_trimmed() -> None:
    request = make_request(
        team_profile=TeamProfile(
            primary_languages=[" Python "],
            backend_stack=[" Django "],
        ),
        priorities=[" Production readiness "],
    )

    assert request.team_profile.primary_languages == ["Python"]
    assert request.team_profile.backend_stack == ["Django"]
    assert request.priorities == ["Production readiness"]


@pytest.mark.parametrize(
    "values",
    [
        {"team_profile": {"primary_languages": [""]}},
        {"team_profile": {"backend_stack": ["   "]}},
        {"priorities": ["\t"]},
    ],
)
def test_blank_optional_list_values_are_rejected(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        make_request(**values)

