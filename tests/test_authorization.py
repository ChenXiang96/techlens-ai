import io
import pickle
import pytest
import httpx
from langgraph.types import Command
from authorization_helpers import grant
from fakes import make_request, fictional_rates, FakePlanner, FakeSearchProvider, FakeGitHubCollector
from techlens.authorization import authorize_live_run, LiveAuthorizationError
from techlens.presentation import PublicOutput
from techlens.graph import build_graph
from techlens.openai_planner import OpenAIResearchPlanner
from techlens.openai_analyzer import OpenAIAdoptionAnalyzer
from techlens.tavily_search import TavilySearchProvider
from techlens.github_client import GitHubClient
from techlens.openai_requests import call_openai


@pytest.mark.parametrize("provider", [OpenAIResearchPlanner, OpenAIAdoptionAnalyzer, TavilySearchProvider, GitHubClient])
def test_real_constructors_deny_before_client_creation(provider, monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "Client", lambda **kw: calls.append(kw))
    with pytest.raises(LiveAuthorizationError) as caught:
        provider()
    assert calls == []
    assert caught.value.__cause__ is caught.value.__context__ is None


def test_raw_openai_helper_does_not_accept_an_sdk_client_as_permission():
    with pytest.raises(LiveAuthorizationError):
        call_openai(object(), {})


@pytest.mark.parametrize("reply", ["", "yes", "approve", "AUTHORIZE LIVE other-run"])
def test_exact_interactive_issuance_denies_other_actions(reply):
    with pytest.raises(LiveAuthorizationError):
        authorize_live_run(make_request(), fictional_rates(),
            {"OPENAI_API_KEY": "FAKE", "TAVILY_API_KEY": "FAKE"},
            read=lambda _: reply, write=lambda _: None, public=PublicOutput({}), interactive=True)


def test_grant_cannot_be_serialized_or_created_from_json():
    auth = grant()
    with pytest.raises(LiveAuthorizationError):
        pickle.dumps(auth)
    from techlens.authorization import require_authorization
    with pytest.raises(LiveAuthorizationError):
        require_authorization({"run_id": auth.run_id, "approved": True})
    auth.close()


@pytest.mark.parametrize("field", ["run", "request", "rates", "model", "endpoint", "bound"])
def test_mutated_binding_is_denied(field, monkeypatch):
    request, rates = make_request(), fictional_rates()
    auth = grant(request, rates)
    kwargs = {}
    if field == "run":
        kwargs["run_id"] = "another"
    elif field == "request":
        request.priorities.append("changed")
        kwargs["request"] = request
    elif field == "rates":
        kwargs["rates"] = rates.model_copy(update={"source_reference": "changed"})
    elif field == "model":
        auth.model = "changed"
    elif field == "endpoint":
        monkeypatch.setattr("techlens.tavily_search.TAVILY_SEARCH_URL", "https://invalid.example/search")
    else:
        monkeypatch.setattr(OpenAIResearchPlanner, "max_output_tokens", 9000)
    with pytest.raises(LiveAuthorizationError):
        auth.check(**kwargs)


def test_grant_only_direct_paid_call_and_cross_runner_are_denied():
    auth = grant()
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r))) as client:
        provider = OpenAIResearchPlanner(http_client=client, authorization=auth)
        with pytest.raises(LiveAuthorizationError):
            provider.count(provider.prepare(make_request()))
        with pytest.raises(LiveAuthorizationError):
            build_graph(planner=provider, search_provider=None, rates=fictional_rates())
    assert calls == []


def test_review_transition_and_live_state_edit_lifecycle():
    request, auth = make_request(), grant()
    github, web = FakeGitHubCollector(), FakeSearchProvider()
    graph = build_graph(planner=FakePlanner(), search_provider=web, github_collector=github,
                        rates=fictional_rates(), authorization=auth)
    config = {"configurable": {"thread_id": auth.run_id}}
    paused = graph.invoke({"request": request}, config)
    auth.check()
    assert auth._approved is None and github.calls == web.calls == []
    with pytest.raises(LiveAuthorizationError):
        graph.update_state(config, {"plan_status": "approved"})
    modified = paused["research_plan"].model_copy(deep=True)
    modified.web_tasks[0] = modified.web_tasks[0].model_copy(update={"query": "A revised query"})
    result = graph.invoke(Command(resume={"action": "modify", "modified_plan": modified.model_dump()}), config)
    assert result["current_round"] == 1 and len(github.calls) == 1 and len(web.calls) == 2
    with pytest.raises(LiveAuthorizationError):
        auth.check()
    assert graph.get_state(config).values["report"] == result["report"]


def test_invalid_review_does_not_attest_or_research():
    auth = grant()
    github = FakeGitHubCollector()
    graph = build_graph(planner=FakePlanner(), search_provider=FakeSearchProvider(), github_collector=github,
                        rates=fictional_rates(), authorization=auth)
    config = {"configurable": {"thread_id": auth.run_id}}
    paused = graph.invoke({"request": make_request()}, config)
    plan = paused["research_plan"].model_copy(deep=True)
    plan.comparison_targets[:] = ["candidate-999"]
    from techlens.planning import PlanReviewError
    with pytest.raises(PlanReviewError):
        graph.invoke(Command(resume={"action": "modify", "modified_plan": plan.model_dump()}), config)
    assert auth._approved is None and github.calls == []
    assert graph.get_state(config).values["current_round"] == 0


@pytest.mark.parametrize("live,reply", [(False, "approve\n"), (True, ""), (True, "yes\n"), (True, "reject\n")])
def test_cli_denied_never_constructs_or_fabricates(tmp_path, live, reply):
    from test_cli import Terminal
    from techlens.cli import main
    request, rates = tmp_path / "request.json", tmp_path / "rates.json"
    request.write_text(make_request().model_dump_json())
    rates.write_text(fictional_rates().model_dump_json())
    calls, out = [], Terminal()
    args = ["assess", "--request", str(request), "--rate-card", str(rates)] + (["--live"] if live else [])
    assert main(args, stdin=Terminal(reply), stdout=out,
        environ={"OPENAI_API_KEY": "SECRET-SENTINEL", "TAVILY_API_KEY": "SECRET-SENTINEL"},
        factory=lambda *a, **kw: calls.append(True)) == 7
    assert calls == [] and "SECRET-SENTINEL" not in out.getvalue()
    assert "No provider work started" in out.getvalue()
    assert not list(tmp_path.glob("report*"))
