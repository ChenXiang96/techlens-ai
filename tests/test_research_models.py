import pytest
from pydantic import ValidationError

from fakes import make_plan, make_request
from techlens.models import WebResearchTask
from techlens.research_models import PlanReviewDecision, WebResearchTaskResult, WebSearchResult


@pytest.mark.parametrize("field", ["query", "purpose"])
def test_task_text_trims_and_rejects_blank(field):
    raw = {"task_id": "web-001", "candidate_id": None, "query": " query ", "purpose": " purpose "}
    task = WebResearchTask(**raw)
    assert task.query == "query" and task.purpose == "purpose"
    raw[field] = " \n "
    with pytest.raises(ValidationError):
        WebResearchTask(**raw)


@pytest.mark.parametrize("decision", [
    {"action": "modify"}, {"action": "invalid"}, {"action": "approve", "request": {}},
])
def test_invalid_review_structure(decision):
    with pytest.raises(ValidationError):
        PlanReviewDecision.model_validate(decision)


def test_review_plan_only_allowed_for_modify():
    plan = make_plan(make_request())
    assert PlanReviewDecision(action="modify", modified_plan=plan).modified_plan == plan
    for action in ("approve", "reject"):
        with pytest.raises(ValidationError):
            PlanReviewDecision(action=action, modified_plan=plan)


def test_source_url_spelling_and_optional_date():
    url = "https://example.com/Doc?q=x%2fy"
    result = WebSearchResult(title="", url=url, content="", score=0.5)
    assert result.url == url
    assert result.published_date is None


@pytest.mark.parametrize("url", ["not-url", "file:///etc/passwd", "https://user:pass@example.com"])
def test_invalid_source_url(url):
    with pytest.raises(ValidationError):
        WebSearchResult(title="title", url=url, content="content", score=0.5)


def test_web_result_count_is_bounded():
    task = make_plan(make_request()).web_tasks[0]
    result = WebSearchResult(title="title", url="https://example.com", content="content", score=0.5)
    with pytest.raises(ValidationError):
        WebResearchTaskResult(task=task, provider="fake", results=[result] * 4)
