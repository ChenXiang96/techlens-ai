from decimal import Decimal

import pytest
from pydantic import ValidationError

from fakes import make_plan, make_request
from techlens.planning import PlannerValidationError, candidate_catalog, validate_plan


def test_duplicate_names_and_ordered_candidate_identity():
    request = make_request(3)
    first = candidate_catalog(request)
    assert first == candidate_catalog(request)
    assert [c.candidate_id for c in first] == ["candidate-001", "candidate-002", "candidate-003"]
    assert [c.name for c in first] == ["Same name"] * 3
    assert [c.repository_url for c in first] == [c.repository_url for c in request.candidates]
    reordered = request.model_copy(deep=True)
    reordered.candidates.reverse()
    assert candidate_catalog(reordered)[0].repository_url == request.candidates[-1].repository_url


def test_plan_has_canonical_ids_and_original_limits():
    request = make_request(2)
    plan = make_plan(request)
    assert plan == make_plan(request)
    assert plan.comparison_targets == ["candidate-001", "candidate-002"]
    assert [t.task_id for t in plan.web_tasks] == ["web-001", "web-002"]
    assert plan.max_rounds == request.constraints.max_research_rounds
    assert plan.max_run_cost_usd == request.constraints.max_run_cost_usd
    with pytest.raises(ValidationError):
        plan.max_searches = 100


@pytest.mark.parametrize("field,value", [
    ("max_rounds", 3), ("max_searches", 9), ("max_candidates", 2),
    ("max_run_cost_usd", "0.16"), ("max_run_cost_usd", "0.01"),
    ("recommendation", "adopt"), ("repository_url", "https://github.com/other/repo"),
    ("comparison_targets", ["Same name"]), ("comparison_targets", ["candidate-999"]),
    ("comparison_targets", []),
])
def test_invalid_plan_fields_are_rejected(field, value):
    request = make_request()
    raw = make_plan(request).model_dump()
    raw[field] = value
    with pytest.raises(PlannerValidationError):
        validate_plan(raw, request)


@pytest.mark.parametrize("candidate_id", ["candidate-999", "Same name", "candidate-1"])
def test_invented_or_display_name_candidate_identity_is_rejected(candidate_id):
    request = make_request()
    raw = make_plan(request).model_dump()
    raw["web_tasks"][0]["candidate_id"] = candidate_id
    with pytest.raises(PlannerValidationError):
        validate_plan(raw, request)


@pytest.mark.parametrize("task_id", ["arbitrary", "web-002", "web-1", ""])
def test_human_task_ids_must_be_canonical(task_id):
    request = make_request()
    raw = make_plan(request).model_dump()
    raw["web_tasks"][0]["task_id"] = task_id
    with pytest.raises(PlannerValidationError):
        validate_plan(raw, request)


def test_comparison_order_and_duplicates_rejected():
    request = make_request(2)
    raw = make_plan(request).model_dump()
    for ids in (["candidate-002", "candidate-001"], ["candidate-001", "candidate-001"]):
        raw["comparison_targets"] = ids
        with pytest.raises(PlannerValidationError):
            validate_plan(raw, request)


def test_search_limit_and_mutated_model_revalidated():
    request = make_request()
    plan = make_plan(request)
    request.constraints.max_web_searches = 1
    raw = plan.model_dump()
    raw["max_searches"] = 1
    with pytest.raises(PlannerValidationError, match="search limit"):
        validate_plan(raw, request)
    plan.comparison_targets.append("candidate-999")
    with pytest.raises(PlannerValidationError):
        validate_plan(plan, make_request())


def test_cross_candidate_and_zero_dollar_contract_without_preflight():
    request = make_request()
    request.constraints.max_run_cost_usd = Decimal("0")
    raw = make_plan(request).model_dump()
    raw["web_tasks"][0]["candidate_id"] = None
    assert validate_plan(raw, request).max_run_cost_usd == Decimal("0")
    assert validate_plan(raw, request).web_tasks[0].candidate_id is None


def test_invented_repository_in_text_is_rejected():
    request = make_request()
    raw = make_plan(request).model_dump()
    raw["web_tasks"][0]["query"] = "Review https://github.com/invented/repository"
    with pytest.raises(PlannerValidationError, match="repository"):
        validate_plan(raw, request)


def test_original_repository_links_remain_valid_in_text():
    request = make_request()
    raw = make_plan(request).model_dump()
    raw["objective"] = "Collect https://github.com/example/project-1.git."
    raw["web_tasks"][0]["query"] = "https://github.com/example/project-1/issues"
    assert validate_plan(raw, request).objective == raw["objective"]
