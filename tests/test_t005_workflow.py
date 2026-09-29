from decimal import Decimal
from langgraph.types import Command
from fakes import FakePlanner, FakeAnalyzer, FakeSearchProvider, FakeGitHubCollector, fictional_rates, make_request
from techlens.graph import build_graph
from techlens.budget import AttemptOutcome
from techlens.github_client import GitHubRepositoryUnavailableError
from techlens.search import SearchResponseError, SearchTransportError, SearchQuotaError
import pytest


def configured(**kwargs):
    values = dict(planner=FakePlanner(), analyzer=FakeAnalyzer(), search_provider=FakeSearchProvider(),
                  github_collector=FakeGitHubCollector(), rates=fictional_rates(), sleep=lambda _: None)
    return build_graph(**{**values, **kwargs})


def test_complete_three_candidate_run():
    graph = configured()
    config = {"configurable": {"thread_id": "full"}}
    paused = graph.invoke({"request": make_request(3)}, config)
    assert paused["__interrupt__"] and paused["current_round"] == 0
    result = graph.invoke(Command(resume={"action": "approve"}), config)
    assert result["report"].status == "completed"
    assert len(result["evidence"]) == 74
    assert len(result["report"].sections) == 13
    assert result["run_metrics"].planner_attempts == result["run_metrics"].analyzer_attempts == 1
    assert result["run_metrics"].count_attempts == 2
    assert result["current_round"] == 1


def test_no_rates_and_zero_budget_stop_before_planner():
    for rates, amount in [(None, ".15"), (fictional_rates(), "0")]:
        planner = FakePlanner()
        graph = configured(planner=planner, rates=rates)
        request = make_request()
        request.constraints.max_run_cost_usd = Decimal(amount)
        result = graph.invoke({"request": request}, {"configurable": {"thread_id": "denied"}})
        assert planner.calls == []
        assert result["report"].status == "partial" and result["current_round"] == 0
        assert result["budget_ledger"].attempts == []


def finish(graph, request=None, thread="flow"):
    config = {"configurable": {"thread_id": thread}}
    initial = graph.invoke({"request": request or make_request()}, config)
    return graph.invoke(Command(resume={"action": "approve"}), config) if "__interrupt__" in initial else initial


def test_one_failed_search_keeps_other_results_and_github_failure_keeps_other_candidates():
    class Search(FakeSearchProvider):
        def search(self, task):
            if task.task_id == "web-001":
                raise SearchResponseError("fixture malformed retrieval")
            return super().search(task)
    def collector(candidate):
        if str(candidate.repository_url).endswith("project-1"):
            raise GitHubRepositoryUnavailableError("fixture unavailable")
        return FakeGitHubCollector()(candidate)
    result = finish(configured(search_provider=Search(), github_collector=collector), make_request(2))
    assert result["report"].status == "partial" and result["current_round"] == 1
    assert len(result["evidence"]) == 25
    assert result["github_signals"][0].candidate_id == "candidate-002"
    assert result["web_research_results"][0].task.task_id == "web-002"
    assert not result["run_metrics"].github_calls_complete
    assert result["run_metrics"].github_calls_measured == 9
    assert result["run_metrics"].collection_failures == 2


def test_all_research_unusable_skips_analyzer():
    analyzer = FakeAnalyzer()
    def no_github(_):
        raise GitHubRepositoryUnavailableError("fixture")
    class Search(FakeSearchProvider):
        def search(self, task):
            raise SearchQuotaError("fixture")
    result = finish(configured(analyzer=analyzer, github_collector=no_github, search_provider=Search()))
    assert result["report"].status == "partial" and result["evidence"] == []
    assert result["current_round"] == 1 and analyzer.calls == []
    assert len(result["report"].analyses[0].dimensions) == 6


def test_analysis_budget_denial_after_paid_research():
    analyzer = FakeAnalyzer()
    request = make_request()
    request.constraints.max_run_cost_usd = Decimal(".010")
    result = finish(configured(analyzer=analyzer), request)
    assert result["report"].status == "partial" and len(result["evidence"]) == 26
    assert analyzer.calls == [] and result["run_metrics"].search_attempts == 2
    assert "analyzer:budget_exhausted" in result["errors"]


@pytest.mark.parametrize("stage", ["planner", "analyzer"])
def test_preflight_timeout_has_no_generation_or_automatic_preflight_retry(stage):
    class TimeoutPlanner(FakePlanner):
        count_calls = 0
        def count(self, shared):
            self.count_calls += 1
            return AttemptOutcome(status="failed", category="timeout")
    class TimeoutAnalyzer(FakeAnalyzer):
        count_calls = 0
        def count(self, shared):
            self.count_calls += 1
            return AttemptOutcome(status="failed", category="timeout")
    adapter = TimeoutPlanner() if stage == "planner" else TimeoutAnalyzer()
    result = finish(configured(**{stage if stage == "planner" else "analyzer": adapter}))
    assert adapter.count_calls == 1 and adapter.calls == []
    attempt = next(a for a in result["budget_ledger"].attempts if a.operation_id == f"{stage}:count")
    assert attempt.state == "billing_unknown" and attempt.reservation_usd == Decimal(".002")


@pytest.mark.parametrize("category,expected", [("timeout", 2), ("rate_limit", 2), ("server", 2),
    ("authentication", 1), ("quota", 1), ("refusal", 1), ("malformed", 1), ("ambiguous", 1)])
def test_generation_retry_policy_and_count_reuse(category, expected):
    class Flaky(FakePlanner):
        count_calls = 0
        generation_calls = 0
        def count(self, shared):
            self.count_calls += 1
            return super().count(shared)
        def generate(self, shared):
            self.generation_calls += 1
            if self.generation_calls == 1:
                return AttemptOutcome(status="failed", category=category, retry_after=1.5)
            return super().generate(shared)
    planner, sleeps = Flaky(), []
    result = finish(configured(planner=planner, sleep=sleeps.append))
    assert planner.count_calls == 1 and planner.generation_calls == expected
    assert sleeps == ([1.5] if expected == 2 else [])
    assert any(a.state == "billing_unknown" for a in result["budget_ledger"].attempts)


def test_every_search_retry_uses_global_search_allowance():
    class Search(FakeSearchProvider):
        def search(self, task):
            self.calls.append(task)
            raise SearchTransportError("timeout")
    search = Search()
    request = make_request()
    request.constraints.max_web_searches = 2
    result = finish(configured(search_provider=search), request)
    assert len(search.calls) == 2 and {t.task_id for t in search.calls} == {"web-001"}
    assert result["web_outcomes"][1].status == "skipped_limit"
    assert result["run_metrics"].search_attempts == 2


def test_invalid_citation_draft_is_discarded_without_repair():
    class Analyzer(FakeAnalyzer):
        def generate(self, shared):
            result = super().generate(shared)
            result.data["candidates"][0]["dimensions"][0].update(judgment="favorable", claims=[{
                "statement": "Invalid fixture statement", "candidate_ids": ["candidate-001"], "evidence_ids": ["evidence-999"]}])
            return result
    analyzer = Analyzer()
    result = finish(configured(analyzer=analyzer))
    assert len(analyzer.calls) == 1 and result["claims"] == []
    assert result["report"].status == "partial"
    assert all(d.judgment == "insufficient_information" for d in result["report"].analyses[0].dimensions)
