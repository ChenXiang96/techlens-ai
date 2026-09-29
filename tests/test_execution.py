from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from fakes import FakePlanner, FakeAnalyzer, FakeSearchProvider, make_request
from test_t005_workflow import configured
from test_budget import ledger, SHARED
from techlens.budget import reserve, AttemptOutcome
from techlens.execution import AttemptExecutor, ExecutionBlocked
from techlens.reporting import validate_report, ReportValidationError


def test_only_new_admission_creates_ready_and_commit_required():
    executor = AttemptExecutor()
    admission = reserve(ledger(), "planner:count", "count", SHARED)
    calls = []
    executor.register(admission)
    with pytest.raises(ExecutionBlocked, match="committed"):
        executor.dispatch("run", admission.attempt, committed=lambda: False, send=lambda: calls.append(1))
    assert calls == []
    existing = reserve(admission.ledger, "planner:count", "count", SHARED)
    fresh = AttemptExecutor()
    fresh.register(existing)
    unknown = fresh.dispatch("run", admission.attempt, committed=lambda: True, send=lambda: calls.append(1))
    assert unknown.category == "ambiguous" and calls == []


def test_cached_completion_survives_repeated_reserve_and_reconciliation_failure():
    executor = AttemptExecutor()
    admission = reserve(ledger(), "planner:count", "count", SHARED)
    executor.register(admission)
    calls = []
    def send():
        calls.append(1)
        return AttemptOutcome(status="succeeded", data={"input_tokens": 100})
    first = executor.dispatch("run", admission.attempt, committed=lambda: True, send=send)
    executor.register(admission)  # replaying old admission cannot reset the cached slot
    second = executor.dispatch("run", admission.attempt, committed=lambda: True, send=send)
    assert first == second and calls == [1]


def test_ambiguous_interruption_never_replays():
    executor = AttemptExecutor()
    admission = reserve(ledger(), "planner:count", "count", SHARED)
    executor.register(admission)
    calls = []
    def send():
        calls.append(1)
        raise RuntimeError("injected interruption after dispatch")
    with pytest.raises(RuntimeError):
        executor.dispatch("run", admission.attempt, committed=lambda: True, send=send)
    executor.register(admission)
    outcome = executor.dispatch("run", admission.attempt, committed=lambda: True, send=send)
    assert outcome.category == "ambiguous" and calls == [1]


def test_in_flight_competitor_suspends_and_independent_run_is_allowed():
    entered, release = Event(), Event()
    executor = AttemptExecutor()
    admission = reserve(ledger(), "planner:count", "count", SHARED)
    executor.register(admission)
    def send():
        entered.set()
        assert release.wait(5)
        return AttemptOutcome(status="succeeded", data={"input_tokens": 100})
    with ThreadPoolExecutor() as pool:
        future = pool.submit(executor.dispatch, "run", admission.attempt, committed=lambda: True, send=send)
        assert entered.wait(5)
        try:
            with pytest.raises(ExecutionBlocked, match="in flight"):
                executor.dispatch("run", admission.attempt, committed=lambda: True, send=send)
            executor.enter_run("a", None)
            executor.enter_run("b", None)
            with pytest.raises(ExecutionBlocked, match="already active"):
                executor.enter_run("a", None)
        finally:
            release.set()
        assert future.result().status == "succeeded"


class CountingPlanner(FakePlanner):
    def __init__(self):
        super().__init__()
        self.count_calls = 0
    def count(self, shared):
        self.count_calls += 1
        return super().count(shared)


@pytest.mark.parametrize("point", ["reservation", "outcome"])
def test_actual_checkpoint_failure_does_not_repeat_transport(point):
    class FailingSaver(InMemorySaver):
        fired = False
        def put(self, config, checkpoint, metadata, versions):
            value = checkpoint["channel_values"].get("budget_ledger")
            if value and value.attempts and not self.fired:
                attempt = value.attempts[0]
                if (point == "reservation" and attempt.state == "reserved") or (point == "outcome" and attempt.outcome):
                    self.fired = True
                    raise RuntimeError("injected checkpoint failure")
            return super().put(config, checkpoint, metadata, versions)
    saver, planner = FailingSaver(), CountingPlanner()
    graph = configured(checkpointer=saver, planner=planner)
    config = {"configurable": {"thread_id": "checkpoint"}}
    with pytest.raises(RuntimeError, match="checkpoint failure"):
        graph.invoke({"request": make_request()}, config)
    assert planner.count_calls == (0 if point == "reservation" else 1)
    resumed = graph.invoke(None, config)
    # Pending writes may restore the reservation without its runtime admission;
    # either a single dispatch or a truthful fail-closed partial is acceptable.
    assert planner.count_calls <= 1
    assert "__interrupt__" in resumed or resumed["report"].status == "partial"
    completed = graph.invoke(Command(resume={"action": "reject"}), config) if "__interrupt__" in resumed else resumed
    metrics, attempts = completed["run_metrics"], completed["budget_ledger"].attempts
    assert metrics.count_attempts == sum(a.kind == "count" for a in attempts)
    assert metrics.ambiguous_attempts == sum(a.outcome is None or a.outcome.category == "ambiguous" for a in attempts)
    assert metrics.count_attempts >= 1
    assert completed["budget_ledger"].outstanding >= 0
    validate_report(completed["report"], completed)


@pytest.mark.parametrize("fresh_executor", [False, True])
def test_graph_interruption_and_fresh_executor_restore_keep_liability(fresh_executor):
    class Interrupted(CountingPlanner):
        def count(self, shared):
            self.count_calls += 1
            raise RuntimeError("interrupted transport")
    planner, saver = Interrupted(), InMemorySaver()
    graph = configured(planner=planner, checkpointer=saver)
    config = {"configurable": {"thread_id": "ambiguous"}}
    with pytest.raises(RuntimeError, match="interrupted transport"):
        graph.invoke({"request": make_request()}, config)
    if fresh_executor:
        graph = configured(planner=planner, checkpointer=saver)
    result = graph.invoke(None, config)
    assert planner.count_calls == 1
    assert result["report"].status == "partial"
    assert result["budget_ledger"].outstanding > 0
    assert result["run_metrics"].ambiguous_attempts == 1


def test_same_process_resume_reject_stale_snapshot_and_accounting_isolation():
    planner = CountingPlanner()
    graph = configured(planner=planner)
    config = {"configurable": {"thread_id": "first"}}
    paused = graph.invoke({"request": make_request()}, config)
    old = graph.get_state(config).config
    second = graph.invoke({"request": make_request()}, {"configurable": {"thread_id": "second"}})
    rejected = graph.invoke(Command(resume={"action": "reject"}), config)
    assert rejected["budget_ledger"] == paused["budget_ledger"]
    assert rejected["budget_ledger"].run_id != second["budget_ledger"].run_id
    with pytest.raises(ExecutionBlocked, match="Historical"):
        graph.invoke(None, old)
    assert len(planner.calls) == 2


@pytest.mark.parametrize("operation", ["planner:count", "planner:generate", "analyzer:count", "analyzer:generate", "web:web-001"])
def test_cached_outcome_reapplied_after_failure_before_graph_reconciliation(monkeypatch, operation):
    import techlens.workflow as workflow
    from fakes import FakeAnalyzer, FakeSearchProvider
    original = workflow.reconcile
    failures = []
    def interrupted(ledger, attempt_id, outcome):
        if attempt_id.startswith(operation + "/") and not failures:
            failures.append(attempt_id)
            raise RuntimeError("injected reconciliation interruption")
        return original(ledger, attempt_id, outcome)
    monkeypatch.setattr(workflow, "reconcile", interrupted)
    planner, analyzer, search = CountingPlanner(), FakeAnalyzer(), FakeSearchProvider()
    graph = configured(planner=planner, analyzer=analyzer, search_provider=search)
    config = {"configurable": {"thread_id": "cached"}}
    if operation.startswith("planner"):
        with pytest.raises(RuntimeError, match="reconciliation interruption"):
            graph.invoke({"request": make_request()}, config)
        paused = graph.invoke(None, config)
        assert "__interrupt__" in paused
        result = graph.invoke(Command(resume={"action": "approve"}), config)
    else:
        graph.invoke({"request": make_request()}, config)
        with pytest.raises(RuntimeError, match="reconciliation interruption"):
            graph.invoke(Command(resume={"action": "approve"}), config)
        result = graph.invoke(None, config)
    assert result["report"].status == "completed"
    assert planner.count_calls == 1 and len(planner.calls) == len(analyzer.calls) == 1
    assert len(search.calls) == 2
    assert len(result["budget_ledger"].attempts) == 6


def test_concurrent_graph_invocations_are_isolated_and_same_run_is_rejected():
    entered, release = Event(), Event()
    class Blocking(CountingPlanner):
        def count(self, shared):
            if '"block"' in shared["input"][0]["content"]:
                entered.set()
                assert release.wait(5)
            return super().count(shared)
    graph = configured(planner=Blocking())
    request = make_request()
    request.priorities = ["block"]
    first_config = {"configurable": {"thread_id": "parallel-a"}}
    second_config = {"configurable": {"thread_id": "parallel-b"}}
    with ThreadPoolExecutor() as pool:
        future = pool.submit(graph.invoke, {"request": request}, first_config)
        assert entered.wait(5)
        try:
            with pytest.raises(ExecutionBlocked, match="already active"):
                graph.invoke(None, first_config)
            other = graph.invoke({"request": make_request()}, second_config)
        finally:
            release.set()
        first = future.result()
    assert first["budget_ledger"].run_id == "parallel-a"
    assert other["budget_ledger"].run_id == "parallel-b"
    assert first["budget_ledger"].settled == other["budget_ledger"].settled


def test_synchronous_durability_cannot_be_overridden():
    planner = CountingPlanner()
    graph = configured(planner=planner)
    with pytest.raises(ExecutionBlocked, match="Synchronous"):
        graph.invoke({"request": make_request()}, {"configurable": {"thread_id": "async-denied"}}, durability="async")
    assert planner.count_calls == 0


def test_fresh_runtime_cannot_mint_ready_from_a_restored_pre_reservation_snapshot():
    executor = AttemptExecutor()
    executor.enter_run("run", "existing-checkpoint-before-reserve")
    admission = reserve(ledger(), "planner:count", "count", SHARED)
    assert admission.is_new  # New to the stale ledger is not proof of new dispatch.
    executor.register(admission)
    calls = []
    outcome = executor.dispatch("run", admission.attempt, committed=lambda: True, send=lambda: calls.append(1))
    assert outcome.category == "ambiguous" and calls == []


@pytest.mark.parametrize("stage,expected_kind", [
    ("planner_count", "count"), ("planner_generation", "generation"),
    ("analyzer_count", "count"), ("analyzer_generation", "generation"),
    ("web", "search"),
])
def test_ambiguous_paid_attempt_is_included_in_admitted_totals(stage, expected_kind):
    class InterruptedPlanner(FakePlanner):
        transport_calls = 0

        def count(self, shared):
            if stage == "planner_count":
                self.transport_calls += 1
                raise RuntimeError("injected uncertain planner count")
            return super().count(shared)

        def generate(self, shared):
            if stage == "planner_generation":
                self.transport_calls += 1
                raise RuntimeError("injected uncertain planner generation")
            return super().generate(shared)

    class InterruptedAnalyzer(FakeAnalyzer):
        transport_calls = 0

        def count(self, shared):
            if stage == "analyzer_count":
                self.transport_calls += 1
                raise RuntimeError("injected uncertain analyzer count")
            return super().count(shared)

        def generate(self, shared):
            if stage == "analyzer_generation":
                self.transport_calls += 1
                raise RuntimeError("injected uncertain analyzer generation")
            return super().generate(shared)

    class InterruptedSearch(FakeSearchProvider):
        transport_calls = 0

        def search(self, task):
            if stage == "web" and task.task_id == "web-001":
                self.transport_calls += 1
                raise RuntimeError("injected uncertain web search")
            return super().search(task)

    planner, analyzer, search = InterruptedPlanner(), InterruptedAnalyzer(), InterruptedSearch()
    graph = configured(planner=planner, analyzer=analyzer, search_provider=search)
    config = {"configurable": {"thread_id": f"uncertain-{stage}"}}
    if stage.startswith("planner"):
        with pytest.raises(RuntimeError, match="uncertain"):
            graph.invoke({"request": make_request()}, config)
    else:
        assert "__interrupt__" in graph.invoke({"request": make_request()}, config)
        with pytest.raises(RuntimeError, match="uncertain"):
            graph.invoke(Command(resume={"action": "approve"}), config)
    result = graph.invoke(None, config)
    attempts, metrics = result["budget_ledger"].attempts, result["run_metrics"]
    uncertain = [a for a in attempts if a.outcome and a.outcome.category == "ambiguous"]
    assert len(uncertain) == 1 and uncertain[0].kind == expected_kind
    assert uncertain[0].state == "billing_unknown"
    assert result["budget_ledger"].outstanding >= uncertain[0].reservation_usd > 0
    assert metrics.ambiguous_attempts == 1
    assert metrics.planner_attempts == sum(a.operation_id == "planner:generate" for a in attempts)
    assert metrics.analyzer_attempts == sum(a.operation_id == "analyzer:generate" for a in attempts)
    assert metrics.llm_attempts == sum(a.kind == "generation" for a in attempts)
    assert metrics.count_attempts == sum(a.kind == "count" for a in attempts)
    assert metrics.search_attempts == sum(a.kind == "search" for a in attempts)
    assert metrics.cost_status == "unknown_liability"
    assert (planner if stage.startswith("planner") else analyzer if stage.startswith("analyzer") else search).transport_calls == 1
    validate_report(result["report"], result)
    measured_field = "search_attempts" if expected_kind == "search" else (
        "count_attempts" if expected_kind == "count" else "llm_attempts")
    incorrect = metrics.model_copy(update={measured_field: 0})
    with pytest.raises(ReportValidationError, match="metrics differ"):
        validate_report(result["report"].model_copy(update={"metrics": incorrect}), result)
