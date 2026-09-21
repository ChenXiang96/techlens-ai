import pytest
from pydantic import ValidationError

from techlens.github_models import (
    BoundedCount,
    GitHubRepositoryRef,
    PracticeIndicator,
)
from techlens.models import TechnologyCandidate


@pytest.mark.parametrize(
    "repository_url",
    [
        "https://github.com/langchain-ai/langgraph",
        "https://github.com/langchain-ai/langgraph/",
        "https://github.com/langchain-ai/langgraph.git",
    ],
)
def test_repository_ref_is_derived_from_candidate(repository_url: str) -> None:
    candidate = TechnologyCandidate(
        name="LangGraph",
        repository_url=repository_url,
    )

    reference = GitHubRepositoryRef.from_candidate(candidate)

    assert reference.owner == "langchain-ai"
    assert reference.repository == "langgraph"


def test_bounded_count_distinguishes_exact_from_lower_bound() -> None:
    exact = BoundedCount(observed=100, completeness="complete")
    lower_bound = BoundedCount(observed=100, completeness="truncated")

    assert exact.observed == lower_bound.observed
    assert exact.completeness == "complete"
    assert lower_bound.completeness == "truncated"


def test_practice_indicator_requires_matched_path_for_detection() -> None:
    with pytest.raises(ValidationError):
        PracticeIndicator(
            detected=True,
            evidence_complete=True,
            matched_paths=[],
        )

