"""Controlled transport probes through actual graph workers and reviewed scopes."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from copy import deepcopy
from decimal import Decimal
import json
import httpx
import pytest
from langgraph.types import Command
from authorization_helpers import grant, planner as authorized_planner, run_llm, research_call
from fakes import make_request, fictional_rates, FakePlanner, FakeSearchProvider, FakeGitHubCollector
from techlens.authorization import LiveAuthorizationError
from techlens.graph import build_graph
from techlens.openai_planner import OpenAIResearchPlanner
from techlens.tavily_search import TavilySearchProvider
from techlens.github_client import collect_engineering_signals
from test_openai_planner import response_for
from test_github_client import make_scenario_handler


@pytest.mark.parametrize("control", ["ceiling", "delete_history", "replace_history", "search_limit",
                                    "empty_update", "goto", "parent", "invalid_review"])
@pytest.mark.parametrize("action", ["approve", "modify", "reject"])
@pytest.mark.parametrize("count_fee", [None, Decimal("0.002")])
def test_live_command_controls_preserve_checkpoint_and_later_review(control, action, count_fee):
    from techlens.planning import PlanReviewError
    request = make_request()
    request.constraints = request.constraints.model_copy(update={"max_run_cost_usd": Decimal("0.010")})
    rates = fictional_rates(search_upper_fee=Decimal("0.009"), search_fixed_fee=Decimal("0.009"),
                            count_fixed_fee=count_fee)
    auth, calls, github = grant(request, rates), [], FakeGitHubCollector()
    with httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or
            httpx.Response(200, json={"results": []}))) as client:
        provider = TavilySearchProvider(authorization=auth, http_client=client)
        graph = build_graph(planner=FakePlanner(task_count=1), search_provider=provider,
                            github_collector=github, rates=rates, authorization=auth)
        config = {"configurable": {"thread_id": auth.run_id}}
        paused = graph.invoke({"request": request}, config)
        ledger = paused["budget_ledger"]
        changes = {"ceiling": {"ceiling_usd": Decimal("100")}, "delete_history": {"attempts": []},
                   "replace_history": {"attempts": [ledger.attempts[0]]},
                   "search_limit": {"max_search_attempts": 999}}
        controls = {"resume": {"action": "approve"}}
        if control in changes:
            controls["update"] = {"budget_ledger": ledger.model_copy(update=changes[control])}
        elif control == "empty_update":
            controls["update"] = {}
        elif control == "goto":
            controls["goto"] = "web_research"
        elif control == "parent":
            controls["graph"] = Command.PARENT
        else:
            controls["resume"] = {"action": "modify", "modified_plan": {}}
        before = deepcopy(list(graph.checkpointer.list(config)))
        heads, slots = deepcopy(graph.executor._heads), deepcopy(graph.executor._slots)
        with pytest.raises((LiveAuthorizationError, PlanReviewError)):
            graph.invoke(Command(**controls), config)
        assert list(graph.checkpointer.list(config)) == before
        assert graph.executor._heads == heads and graph.executor._slots == slots
        assert auth._approved is None and calls == github.calls == []
        decision = {"action": action}
        if action == "modify":
            decision["modified_plan"] = paused["research_plan"].model_dump()
        result = graph.invoke(Command(resume=decision), config)
    assert calls == []  # .00213 settled/outstanding + .009 search exceeds .010.
    final = result["budget_ledger"]
    assert final.ceiling_usd == Decimal("0.010") and final.max_search_attempts == request.constraints.max_web_searches
    assert final.attempts == ledger.attempts
    assert final.outstanding == (Decimal("0.002") if count_fee is None else Decimal(0))
    assert final.settled == (Decimal("0.00013") if count_fee is None else Decimal("0.00213"))
    assert result["plan_status"] == ("modified" if action == "modify" else "rejected" if action == "reject" else "approved")


@pytest.mark.parametrize("field,value", [("ceiling_usd", Decimal("100")), ("max_search_attempts", 999)])
@pytest.mark.parametrize("boundary", ["admission", "dispatch", "committed_scope"])
def test_live_ledger_allowances_independently_checked(field, value, boundary):
    from langgraph.checkpoint.memory import InMemorySaver
    from techlens.budget import BudgetLedger, reserve
    from techlens.execution import AttemptExecutor
    request, rates, calls = make_request(), fictional_rates(), []
    auth = grant(request, rates)
    saver, executor = InMemorySaver(), AttemptExecutor()
    auth.bind(executor, saver, rates)
    with httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r))) as client:
        provider = OpenAIResearchPlanner(authorization=auth, http_client=client)
        shared = provider.prepare(request)
        ledger = BudgetLedger(run_id=auth.run_id, ceiling_usd=request.constraints.max_run_cost_usd,
                              max_search_attempts=request.constraints.max_web_searches, rates=rates)
        admission = reserve(ledger, "planner:count", "count", shared)
        bad = admission.ledger.model_copy(update={field: value})
        executor.register(admission)
        executor.enter_run(auth.run_id, None)
        with pytest.raises(LiveAuthorizationError, match="allowances"):
            if boundary == "admission":
                auth.before_admission({"request": request, "budget_ledger": bad}, provider)
            else:
                # A mismatched committed ledger must fail independently of the caller's ledger.
                from langgraph.checkpoint.base import empty_checkpoint
                checkpoint = empty_checkpoint()
                checkpoint["channel_values"] = {"budget_ledger": bad}
                checkpoint["channel_versions"] = {"budget_ledger": "1"}
                saver.put({"configurable": {"thread_id": auth.run_id, "checkpoint_ns": ""}},
                          checkpoint, {"source": "input", "step": 0, "parents": {}}, {"budget_ledger": "1"})
                executor.dispatch(auth.run_id, admission.attempt, committed=lambda: True,
                    send=lambda: provider.count(shared), authorization=auth, adapter=provider,
                    ledger=admission.ledger if boundary == "committed_scope" else bad)
        assert calls == [] and admission.ledger.outstanding == rates.count_upper_fee
    auth.close()


def test_live_initial_mapping_rejects_extra_state_before_checkpoint():
    auth = grant()
    graph = build_graph(planner=FakePlanner(), search_provider=FakeSearchProvider(),
                        rates=fictional_rates(), authorization=auth)
    config = {"configurable": {"thread_id": auth.run_id}}
    with pytest.raises(LiveAuthorizationError):
        graph.invoke({"request": make_request(), "plan_status": "approved"}, config)
    assert list(graph.checkpointer.list(config)) == [] and graph.executor._slots == {}
    paused = graph.invoke({"request": make_request()}, config)
    assert "__interrupt__" in paused
    graph.invoke(Command(resume={"action": "reject"}), config)


@pytest.mark.parametrize("change", [{"rates": None}, {"run_id": None}])
def test_live_ledger_missing_binding_is_not_an_optional_check(change):
    from techlens.budget import BudgetLedger
    request, rates = make_request(), fictional_rates()
    auth = grant(request, rates)
    ledger = BudgetLedger(run_id=auth.run_id, ceiling_usd=request.constraints.max_run_cost_usd,
                          max_search_attempts=request.constraints.max_web_searches, rates=rates)
    with pytest.raises(LiveAuthorizationError):
        auth.check_ledger(ledger.model_copy(update=change))
    auth.close()


@pytest.mark.parametrize("operation", ["count", "generate", "search"])
def test_paid_scope_is_atomic_single_use_at_real_boundary(operation):
    calls, denials, escaped = [], [], []
    def handler(request):
        calls.append(request)
        if operation == "search":
            return httpx.Response(200, json={"results": []})
        if operation == "count":
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        return httpx.Response(200, json=response_for())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        request = make_request()
        if operation == "search":
            auth = grant(request)
            provider = TavilySearchProvider(authorization=auth, http_client=client)
        else:
            provider = authorized_planner(OpenAIResearchPlanner, http_client=client)
            auth = provider.authorization
        original = getattr(provider, operation)
        def twice(value):
            escaped.append(lambda: original(value))
            result = original(value)
            with pytest.raises(LiveAuthorizationError):
                original(value)
            denials.append(True)
            return result
        setattr(provider, operation, twice)
        if operation == "search":
            research_call(request, auth, search=provider)
        else:
            run_llm(provider, provider.prepare(request), kind=operation)
        with pytest.raises(LiveAuthorizationError):
            escaped[0]()
    assert len(calls) == len(denials) == 1


def test_scope_does_not_allow_wrong_adapter_operation_or_payload():
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or
            httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100}))) as client:
        provider = authorized_planner(OpenAIResearchPlanner, http_client=client)
        other = OpenAIResearchPlanner(authorization=provider.authorization, http_client=client)
        original = provider.count
        original_generate = provider.generate
        def probe(shared):
            for attempt in (lambda: other.count(shared), lambda: original_generate(shared),
                            lambda: original({**shared, "model": "different"})):
                with pytest.raises(LiveAuthorizationError):
                    attempt()
            return original(shared)
        provider.count = probe
        run_llm(provider, provider.prepare(make_request()), kind="count")
    assert len(calls) == 1


def test_revocation_between_github_pages_blocks_next_request(github_candidate, github_payloads, fixed_now):
    request = make_request()
    request.candidates = [github_candidate]
    auth, calls = grant(request), []
    normal = make_scenario_handler(github_payloads, calls, truncated_endpoint="releases")
    def handler(value):
        response = normal(value)
        if value.url.path.endswith("/releases"):
            auth.close()
        return response
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        def collector(candidate):
            return collect_engineering_signals(candidate, http_client=client, collected_at=fixed_now, authorization=auth)
        with pytest.raises(LiveAuthorizationError):
            research_call(request, auth, collector=collector)
    assert len(calls) == 3
    assert calls[-1].url.params["page"] == "1"


def test_actual_fanout_and_independent_runs_isolate_scopes(github_candidate, github_payloads, fixed_now):
    barrier = Barrier(4)
    observations = []
    def run(index):
        request = make_request()
        request.candidates = [github_candidate]
        auth = grant(request, GITHUB_TOKEN=f"GH-FAKE-{index}", TAVILY_API_KEY=f"WEB-FAKE-{index}")
        calls = []
        github_handler = make_scenario_handler(github_payloads, calls)
        def handler(value):
            if value.url.host == "api.tavily.com":
                barrier.wait(timeout=20)
                assert value.headers["authorization"] == f"Bearer WEB-FAKE-{index}"
                observations.append((index, "web"))
                return httpx.Response(200, json={"results": []})
            if value.url.path == "/repos/example/project":
                barrier.wait(timeout=20)
            assert value.headers["authorization"] == f"Bearer GH-FAKE-{index}"
            observations.append((index, "github"))
            return github_handler(value)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            search = TavilySearchProvider(http_client=client, authorization=auth)
            def collector(candidate):
                return collect_engineering_signals(candidate, http_client=client, collected_at=fixed_now, authorization=auth)
            result = research_call(request, auth, search=search, collector=collector)
            assert result["current_round"] == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, [1, 2]))
    assert observations.count((1, "github")) == observations.count((2, "github")) == 9
    assert observations.count((1, "web")) == observations.count((2, "web")) == 1


def test_environment_changes_do_not_replace_absent_github_token(monkeypatch, github_candidate, github_payloads, fixed_now):
    request = make_request()
    request.candidates = [github_candidate]
    auth, calls = grant(request, GITHUB_TOKEN=""), []
    monkeypatch.setenv("GITHUB_TOKEN", "NEW-SECRET-SENTINEL")
    with httpx.Client(transport=httpx.MockTransport(make_scenario_handler(github_payloads, calls))) as client:
        research_call(request, auth, collector=lambda candidate: collect_engineering_signals(
            candidate, authorization=auth, http_client=client, collected_at=fixed_now))
    assert calls and all("authorization" not in r.headers for r in calls)


def test_denial_after_reservation_preserves_liability_and_no_retry():
    auth, calls = grant(), []
    with httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r))) as client:
        provider = OpenAIResearchPlanner(authorization=auth, http_client=client)
        graph = build_graph(planner=provider, search_provider=FakeSearchProvider(), github_collector=FakeGitHubCollector(),
                            rates=fictional_rates(), authorization=auth)
        original = graph.executor.dispatch
        def revoke(*args, **kwargs):
            auth.close()
            return original(*args, **kwargs)
        graph.executor.dispatch = revoke
        config = {"configurable": {"thread_id": auth.run_id}}
        with pytest.raises(LiveAuthorizationError):
            graph.invoke({"request": make_request()}, config)
        ledgers = [item.checkpoint["channel_values"]["budget_ledger"] for item in graph.checkpointer.list(config)
                   if "budget_ledger" in item.checkpoint["channel_values"]]
        ledger = max(ledgers, key=lambda item: len(item.attempts))
    assert calls == [] and len(ledger.attempts) == 1
    assert ledger.outstanding == fictional_rates().count_upper_fee


@pytest.mark.parametrize("point", ["reservation", "outcome"])
def test_authorized_checkpoint_failure_and_cached_reconciliation(point):
    from langgraph.checkpoint.memory import InMemorySaver
    class FailingSaver(InMemorySaver):
        fired = False
        def put(self, config, checkpoint, metadata, versions):
            ledger = checkpoint["channel_values"].get("budget_ledger")
            if ledger and ledger.attempts and not self.fired:
                attempt = ledger.attempts[0]
                if (point == "reservation" and attempt.state == "reserved") or (point == "outcome" and attempt.outcome):
                    self.fired = True
                    raise RuntimeError("injected checkpoint failure")
            return super().put(config, checkpoint, metadata, versions)
    calls, auth, saver = [], grant(), FailingSaver()
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100}
            if request.url.path.endswith("/input_tokens") else response_for())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAIResearchPlanner(authorization=auth, http_client=client)
        graph = build_graph(planner=provider, search_provider=FakeSearchProvider(), github_collector=FakeGitHubCollector(),
                            rates=fictional_rates(), authorization=auth, checkpointer=saver)
        config = {"configurable": {"thread_id": auth.run_id}}
        with pytest.raises(RuntimeError, match="checkpoint failure"):
            graph.invoke({"request": make_request()}, config)
        assert len(calls) == (0 if point == "reservation" else 1)
        state = graph.invoke(None, config)
        assert calls.count("/v1/responses/input_tokens") <= 1
        if "__interrupt__" in state:
            state = graph.invoke(Command(resume={"action": "reject"}), config)
        assert state["budget_ledger"].outstanding >= 0


def test_fresh_runtime_cannot_authorize_existing_checkpoint():
    from langgraph.checkpoint.memory import InMemorySaver
    saver, first = InMemorySaver(), grant()
    graph = build_graph(planner=FakePlanner(), search_provider=FakeSearchProvider(),
        rates=fictional_rates(), authorization=first, checkpointer=saver)
    config = {"configurable": {"thread_id": first.run_id}}
    graph.invoke({"request": make_request()}, config)
    second = grant()
    restored = build_graph(planner=FakePlanner(), search_provider=FakeSearchProvider(),
        rates=fictional_rates(), authorization=second, checkpointer=saver)
    with pytest.raises(LiveAuthorizationError):
        restored.invoke(Command(resume={"action": "approve"}), config)
    assert first._approved is second._approved is None
    first.close()
    second.close()
