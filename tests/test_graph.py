import json
from decimal import Decimal

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import techlens
import techlens.graph as graph_module
from fakes import FakeGitHubCollector, FakePlanner, FakeSearchProvider, FakeAnalyzer, fictional_rates, make_request
from techlens.execution import ExecutionBlocked
from techlens.evidence import EvidenceValidationError
from techlens.evidence_models import GitHubEvidencePayload, WebEvidencePayload
from techlens.github_client import GitHubCollectionError, collect_engineering_signals
from techlens.models import AssessmentReport, AssessmentRequest, ResearchPlan
from techlens.planning import PlanReviewError
from techlens.search import SearchResponseError

CONFIG = {"configurable": {"thread_id": "assessment-thread-003"}}


def build_test_graph(**kwargs):
    return techlens.build_graph(rates=fictional_rates(), analyzer=FakeAnalyzer(), sleep=lambda _: None, **kwargs)


def setup_graph(task_count=2, saver=None):
    planner, search, github = FakePlanner(task_count), FakeSearchProvider(), FakeGitHubCollector()
    graph = build_test_graph(planner=planner, search_provider=search,
                                github_collector=github, checkpointer=saver)
    return graph, planner, search, github


def test_package_imports_and_graph_compiles():
    assert techlens.AssessmentRequest is AssessmentRequest
    graph, *_ = setup_graph()
    assert isinstance(graph.checkpointer, InMemorySaver)
    assert set(graph.get_graph().nodes) == {
        "__start__", "validate_request", "plan_research", "human_review", "collect_github",
        "web_research", "research_complete", "normalize_evidence", "validate_evidence",
        "apply_risk_rules", "project_analysis", "analyze_adoption", "validate_analysis",
        "generate_report", "finalize_metrics", "__end__",
    }


def test_interrupt_payload_and_approve_with_duplicate_names():
    saver = InMemorySaver()
    graph, planner, search, github = setup_graph(saver=saver)
    request = make_request(2)
    # Untrusted start fields cannot supply approval or bypass the planner.
    paused = graph.invoke({"request": request, "plan_status": "approved"}, CONFIG)
    assert len(planner.calls) == 1
    assert search.calls == github.calls == []
    assert paused["current_round"] == 0 and paused["plan_status"] == "pending"
    payload = paused["__interrupt__"][0].value
    assert payload["type"] == "research_plan_review"
    assert payload["assessment_id"] == request.assessment_id
    assert payload["allowed_actions"] == ["approve", "modify", "reject"]
    assert json.loads(json.dumps(payload)) == payload
    assert [c["candidate_id"] for c in payload["candidates"]] == ["candidate-001", "candidate-002"]
    assert [c["name"] for c in payload["candidates"]] == ["Same name", "Same name"]
    result = graph.invoke(Command(resume={"action": "approve"}), CONFIG)
    assert len(planner.calls) == 1
    assert len(github.calls) == 2 and len(search.calls) == 2
    assert result["plan_status"] == "approved" and result["current_round"] == 1
    assert isinstance(result["research_plan"], ResearchPlan)
    assert isinstance(result["report"], AssessmentReport)
    assert [c.repository_url for c in github.calls] == [c.repository_url for c in request.candidates]
    assert [c.candidate_id for c in result["github_signals"]] == ["candidate-001", "candidate-002"]
    assert result["evidence_validation"].total_records == 50
    assert isinstance(result["evidence"][0].payload, GitHubEvidencePayload)
    assert isinstance(result["evidence"][-1].payload, WebEvidencePayload)
    restored = graph.get_state(CONFIG).values
    assert isinstance(restored["evidence"][0].payload, GitHubEvidencePayload)
    assert isinstance(restored["evidence"][-1].payload, WebEvidencePayload)
    assert graph.get_state(CONFIG).next == ()
    assert result["errors"] == []
    assert "UNTRUSTED-SNIPPET" not in result["report"].model_dump_json()
    assert "UNTRUSTED-SNIPPET" not in planner.calls[0].model_dump_json()


def test_exact_partial_order_and_join_before_report():
    graph, planner, search, github = setup_graph()
    initial = list(graph.stream({"request": make_request()}, CONFIG, stream_mode="updates"))
    assert [next(iter(event)) for event in initial] == ["validate_request", "plan_research", "__interrupt__"]
    resumed = list(graph.stream(Command(resume={"action": "approve"}), CONFIG, stream_mode="updates"))
    names = [next(iter(event)) for event in resumed]
    assert names[0] == "human_review"
    assert set(names[1:3]) == {"collect_github", "web_research"}
    assert names[3:] == ["research_complete", "normalize_evidence",
                         "validate_evidence", "apply_risk_rules", "project_analysis", "analyze_adoption",
                         "validate_analysis", "generate_report", "finalize_metrics"]
    for event in resumed[1:3]:
        assert "current_round" not in next(iter(event.values()))
    assert set(resumed[4]["normalize_evidence"]) == {"evidence"}
    assert set(resumed[5]["validate_evidence"]) == {"evidence_validation"}


def test_valid_modified_plan_is_executed_without_replanning():
    graph, planner, search, github = setup_graph()
    request = make_request(2)
    paused = graph.invoke({"request": request}, CONFIG)
    modified = paused["research_plan"].model_dump(mode="json")
    modified["web_tasks"] = [modified["web_tasks"][1]]
    modified["web_tasks"][0].update(task_id="web-001", query="human edited query", candidate_id="candidate-002")
    result = graph.invoke(Command(resume={"action": "modify", "modified_plan": modified}), CONFIG)
    assert result["plan_status"] == "modified"
    assert len(planner.calls) == 1 and len(search.calls) == 1
    assert search.calls[0].query == "human edited query"
    assert search.calls[0].candidate_id == "candidate-002"
    assert result["request"] == request
    assert [c.repository_url for c in github.calls] == [c.repository_url for c in request.candidates]


@pytest.mark.parametrize("change", ["candidate", "name", "task", "targets", "limit", "repository", "search_count"])
def test_invalid_human_modification_never_runs_research(change):
    graph, planner, search, github = setup_graph()
    paused = graph.invoke({"request": make_request()}, CONFIG)
    modified = paused["research_plan"].model_dump(mode="json")
    if change == "candidate":
        modified["web_tasks"][0]["candidate_id"] = "candidate-999"
    elif change == "name":
        modified["web_tasks"][0]["candidate_id"] = "Same name"
    elif change == "task":
        modified["web_tasks"][0]["task_id"] = "invented-id"
    elif change == "targets":
        modified["comparison_targets"] = ["candidate-999"]
    elif change == "limit":
        modified["max_run_cost_usd"] = "1.00"
    elif change == "repository":
        modified["web_tasks"][0]["repository_url"] = "https://github.com/other/repo"
    else:
        modified["web_tasks"] = [
            {**modified["web_tasks"][0], "task_id": f"web-{i:03d}"} for i in range(1, 10)
        ]
    with pytest.raises(PlanReviewError) as caught:
        graph.invoke(Command(resume={"action": "modify", "modified_plan": modified}), CONFIG)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert search.calls == github.calls == []
    assert len(planner.calls) == 1
    assert graph.get_state(CONFIG).values["current_round"] == 0


def test_reject_cleanly_terminates_without_research():
    graph, planner, search, github = setup_graph()
    graph.invoke({"request": make_request()}, CONFIG)
    result = graph.invoke(Command(resume={"action": "reject"}), CONFIG)
    assert result["plan_status"] == "rejected" and result["current_round"] == 0
    assert search.calls == github.calls == []
    assert len(planner.calls) == 1
    assert result["report"].summary == "Research plan rejected. No external research performed."
    assert "evidence" not in result and "evidence_validation" not in result
    assert graph.get_state(CONFIG).next == ()


def test_stable_thread_and_missing_thread():
    graph, planner, search, github = setup_graph()
    with pytest.raises(ValueError):
        graph.invoke({"request": make_request()})
    assert planner.calls == []
    graph.invoke({"request": make_request()}, CONFIG)
    assert graph.get_state({"configurable": {"thread_id": "other"}}).values == {}
    assert graph.get_state(CONFIG).next == ("human_review",)
    assert search.calls == github.calls == []
    with pytest.raises(ValueError, match="No assessment request"):
        graph.invoke(Command(resume={"action": "approve"}), {"configurable": {"thread_id": "other"}})
    assert graph.get_state(CONFIG).next == ("human_review",)
    assert search.calls == github.calls == []
    graph.invoke(Command(resume={"action": "approve"}), CONFIG)
    assert len(planner.calls) == 1


@pytest.mark.parametrize("task_count", [0, 8])
def test_empty_or_maximum_web_plan_is_supported(task_count):
    graph, planner, search, github = setup_graph(task_count=task_count)
    request = make_request(3)
    graph.invoke({"request": request}, CONFIG)
    result = graph.invoke(Command(resume={"action": "approve"}), CONFIG)
    assert len(planner.calls) == 1 and len(github.calls) == 3
    assert len(search.calls) == task_count
    assert result["current_round"] == 1
    assert result["research_plan"].max_run_cost_usd == Decimal("0.15")


@pytest.mark.parametrize("branch", ["github", "web"])
def test_provider_failure_is_accounted_and_report_is_partial(branch):
    graph, planner, search, github = setup_graph()
    attempts = []

    def fail(value):
        attempts.append(value)
        if branch == "github":
            raise GitHubCollectionError("Mock GitHub failure")
        raise SearchResponseError("Mock search failure")

    if branch == "web":
        search.search = fail
    graph = build_test_graph(planner=planner, search_provider=search,
                                 github_collector=fail if branch == "github" else github)
    graph.invoke({"request": make_request()}, CONFIG)
    result = graph.invoke(Command(resume={"action": "approve"}), CONFIG)
    assert result["report"].status == "partial"
    state = graph.get_state(CONFIG).values
    assert state["current_round"] == 1 and state["report"].status == "partial"
    assert len(attempts) == (1 if branch == "github" else 2)


def test_real_t002_collector_reused_after_approval(github_candidate, github_payloads, fixed_now):
    from test_github_client import make_scenario_handler
    from authorization_helpers import grant

    requests = []
    handler = make_scenario_handler(github_payloads, requests)
    request = make_request()
    request.candidates = [github_candidate]
    auth = grant(request)
    config = {"configurable": {"thread_id": auth.run_id}}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        def collector(candidate):
            assert candidate == github_candidate
            return collect_engineering_signals(candidate, http_client=client, collected_at=fixed_now, authorization=auth)

        graph = build_test_graph(planner=FakePlanner(), search_provider=FakeSearchProvider(),
                                    github_collector=collector, authorization=auth)
        graph.invoke({"request": request}, config)
        assert requests == []
        result = graph.invoke(Command(resume={"action": "approve"}), config)
    signals = result["github_signals"][0]
    assert signals.candidate_id == "candidate-001"
    assert signals.candidate_name == github_candidate.name
    assert signals.repository_url == github_candidate.repository_url
    assert signals.engineering_signals.collection_metadata.github_api_calls_made == 9
    assert len(requests) == 9


@pytest.mark.parametrize("decision", [
    {"action": "unknown"}, {"action": "modify"},
    {"action": "approve", "request": {"candidates": []}},
])
def test_malformed_review_cannot_authorize_research(decision):
    graph, planner, search, github = setup_graph()
    graph.invoke({"request": make_request()}, CONFIG)
    with pytest.raises(PlanReviewError):
        graph.invoke(Command(resume=decision), CONFIG)
    assert len(planner.calls) == 1 and search.calls == github.calls == []


def test_approve_revalidates_saved_plan():
    graph, planner, search, github = setup_graph()
    paused = graph.invoke({"request": make_request()}, CONFIG)
    # Simulate invalid checkpoint data, then exercise the real approval boundary.
    tampered = paused["research_plan"].model_copy(deep=True)
    tampered.comparison_targets.append("candidate-999")
    graph.update_state(CONFIG, {"research_plan": tampered})
    with pytest.raises(ExecutionBlocked):
        graph.invoke(Command(resume={"action": "approve"}), CONFIG)
    assert search.calls == github.calls == []


def test_empty_resume_does_not_authorize_research():
    graph, planner, search, github = setup_graph()
    graph.invoke({"request": make_request()}, CONFIG)
    # LangGraph interprets {} as an empty interrupt-ID mapping, not a decision.
    paused = graph.invoke(Command(resume={}), CONFIG)
    assert "__interrupt__" in paused
    assert graph.get_state(CONFIG).next == ("human_review",)
    assert len(planner.calls) == 1 and search.calls == github.calls == []


def test_invalid_normalized_evidence_prevents_success_report(monkeypatch):
    original_normalizer = graph_module.normalize_records

    def bad_normalizer(*args):
        records = original_normalizer(*args)
        records[0] = records[0].model_copy(update={"title": "fabricated"})
        return records

    monkeypatch.setattr(graph_module, "normalize_records", bad_normalizer)
    graph, planner, search, github = setup_graph()
    graph.invoke({"request": make_request()}, CONFIG)
    result = graph.invoke(Command(resume={"action": "approve"}), CONFIG)
    assert result["report"].status == "failed"
    state = graph.get_state(CONFIG).values
    assert state["current_round"] == 1
    assert "evidence_validation" not in state and state["report"].status == "failed"
    assert len(planner.calls) == len(github.calls) == 1
    assert len(search.calls) == 2
