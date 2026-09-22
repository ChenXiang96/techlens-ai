import pytest
from pydantic import ValidationError

from fakes import FakeGitHubCollector, FakePlanner, FakeSearchProvider, make_plan, make_request
from techlens.models import AssessmentReport
from techlens.nodes import (
    collect_github, generate_report_stub, plan_research, research_complete,
    validate_request, web_research,
)
from techlens.planning import PlanReviewError

CONFIG = {"configurable": {"thread_id": "node-test"}}


def test_validate_request_initializes_only_approved_state():
    state = validate_request({"request": make_request()}, CONFIG)
    assert state["current_round"] == 0 and state["plan_status"] == "pending"
    assert state["github_signals"] == state["web_research_results"] == []
    assert state["research_notes"] == state["errors"] == []


def test_validate_request_rejects_invalid_request():
    with pytest.raises(ValidationError):
        validate_request({"request": {"candidates": []}}, CONFIG)


def test_planning_dependency_cannot_mutate_authoritative_request():
    request = make_request()

    class MutatingPlanner(FakePlanner):
        def plan(self, input_request):
            plan = super().plan(input_request)
            input_request.candidates[0].name = "Mutated"
            return plan

    state = {"request": request}
    assert plan_research(state, planner=MutatingPlanner())["research_plan"] == make_plan(request)
    assert request.candidates[0].name == "Same name"


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_both_branches_guard_approval(status):
    state = {"request": make_request(), "plan_status": status, "current_round": 0}
    github, search = FakeGitHubCollector(), FakeSearchProvider()
    with pytest.raises(PlanReviewError):
        collect_github(state, collector=github)
    with pytest.raises(PlanReviewError):
        web_research(state, provider=search)
    assert github.calls == search.calls == []


def test_collect_github_receives_original_objects_and_writes_only_its_field():
    request = make_request(2)
    state = {"request": request, "research_plan": make_plan(request), "plan_status": "approved",
             "current_round": 0}
    collector = FakeGitHubCollector()
    update = collect_github(state, collector=collector)
    assert set(update) == {"github_signals"}
    assert collector.calls[0] is request.candidates[0]
    assert collector.calls[1] is request.candidates[1]
    assert state["current_round"] == 0
    for index, result in enumerate(update["github_signals"]):
        assert result.candidate_id == f"candidate-{index + 1:03d}"
        assert result.candidate_name == request.candidates[index].name
        assert result.repository_url == request.candidates[index].repository_url


def test_branches_leave_round_unchanged_and_join_requires_both():
    request = make_request()
    state = validate_request({"request": request}, CONFIG)
    state.update(research_plan=make_plan(request), plan_status="approved")
    with pytest.raises(ValueError, match="incomplete"):
        research_complete(state)
    state.update(collect_github(state, collector=FakeGitHubCollector()))
    with pytest.raises(ValueError, match="incomplete"):
        research_complete(state)
    update = web_research(state, provider=FakeSearchProvider())
    assert set(update) == {"web_research_results"}
    assert state["current_round"] == 0
    state.update(update)
    state.update(research_complete(state))
    assert state["current_round"] == 1
    report = generate_report_stub(state)["report"]
    assert isinstance(report, AssessmentReport)
    assert "UNTRUSTED-SNIPPET" not in report.model_dump_json()


def test_second_round_and_premature_report_are_rejected():
    request = make_request()
    state = {"request": request, "plan_status": "approved", "current_round": 0,
             "research_plan": make_plan(request)}
    with pytest.raises(ValueError):
        generate_report_stub(state)
    state["current_round"] = 1
    with pytest.raises(PlanReviewError):
        collect_github(state, collector=FakeGitHubCollector())
    with pytest.raises(PlanReviewError):
        web_research(state, provider=FakeSearchProvider())
