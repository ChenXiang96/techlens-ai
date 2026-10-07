from contextlib import ExitStack
import json

import pytest

from techlens.application import (AssessmentSession, demo_dependencies, live_dependencies,
                                 private_execution, run_review)
from techlens.configuration import ApplicationError
from techlens.demo import demo_request, demo_rates
from techlens.presentation import PublicOutput


@pytest.mark.parametrize("scenario,status,evidence", [("single", "completed", 25),
    ("comparison", "completed", 50), ("partial", "partial", 24)])
def test_real_demo_graph_session(scenario, status, evidence):
    dependencies = demo_dependencies(scenario)
    session = AssessmentSession(demo_request(scenario), dependencies)
    identities = (session.graph, session.saver, session.executor, session.config.copy())
    initial = session.start()
    assert initial["__interrupt__"]
    assert dependencies.planner.generation_calls == 1
    assert dependencies.search.calls == 0
    assert dependencies.github.calls == []
    assert dependencies.analyzer.count_calls == dependencies.analyzer.generation_calls == 0
    admitted = [a.attempt_id for a in initial["budget_ledger"].attempts]
    state = session.resume(session.decision("approve"))
    assert (session.graph, session.saver, session.executor, session.config) == identities
    assert [a.attempt_id for a in state["budget_ledger"].attempts][:2] == admitted
    assert dependencies.planner.generation_calls == 1
    assert state["report"].status == status
    assert state["current_round"] == 1
    assert len(state["evidence"]) == evidence
    assert dependencies.analyzer.generation_calls == 1


def test_comparison_has_visible_per_candidate_citations_and_preserves_unknown_dimensions():
    session = AssessmentSession(demo_request("comparison"), demo_dependencies("comparison"))
    session.start()
    state = session.resume(session.decision("approve"))
    report = state["report"]
    ids = ["candidate-001", "candidate-002"]
    maintenance, *other_dimensions = report.comparison.dimensions
    assert maintenance.dimension == "maintenance_fit" and maintenance.judgment == "mixed"
    assert len(maintenance.claim_ids) == 1
    claim = next(c for c in report.claims if c.claim_id == maintenance.claim_ids[0])
    assert claim.candidate_ids == ids
    assert claim.statement == ("In this synthetic comparison, both repositories are recorded as unarchived, "
        "but neither has a recorded latest release tag. These mixed maintenance "
        "observations do not establish overall readiness or a preferred candidate.")
    cited = [next(e for e in state["evidence"] if e.evidence_id == identity) for identity in claim.evidence_ids]
    assert set(claim.evidence_ids) <= set(state["analysis_projection"].eligible_evidence_ids)
    assert [(e.candidate_id, e.payload.metric_key, e.payload.value) for e in cited] == [
        (identity, metric, value) for identity in ids for metric, value in
        [("repository.archived", False), ("releases.latest_release_tag", None)]]
    assert report.sections[9].claim_ids == maintenance.claim_ids
    assert all(d.judgment == "insufficient_information" and not d.claim_ids for d in other_dimensions)
    assert all(d.judgment == "insufficient_information" for c in report.analyses for d in c.dimensions)


def test_comparison_does_not_invent_missing_visible_support():
    from techlens.demo import CannedAnalyzer
    analyzer = CannedAnalyzer()
    result = analyzer.generate({"input": {"candidates": [{"candidate_id": "candidate-001"},
        {"candidate_id": "candidate-002"}], "evidence": []}})
    assert all(d["judgment"] == "insufficient_information" and not d["claims"]
               for d in result.data["comparison"])


def test_invalid_review_loop_modify_and_exclusive_save(tmp_path):
    dependencies = demo_dependencies("single")
    session = AssessmentSession(demo_request("single"), dependencies)
    destination = tmp_path / "plan.json"
    bad = tmp_path / "bad.json"
    bad.write_text('{"invalid": 1}')
    replies = iter(["yes", "save-plan", str(destination), "modify", str(bad), "modify", str(destination)])

    def read(prompt):
        assert dependencies.search.calls == 0
        assert not dependencies.github.calls
        assert dependencies.analyzer.generation_calls == 0
        reply = next(replies)
        if reply == str(destination) and destination.exists():
            edited = json.loads(destination.read_text())
            edited["objective"] = "Human-edited collection objective"
            destination.write_text(json.dumps(edited))
        return reply

    output = []
    state = run_review(session, read, output.append, PublicOutput({}))
    assert state["plan_status"] == "modified"
    assert state["research_plan"].objective == "Human-edited collection objective"
    assert state["report"].status == "completed"
    assert dependencies.planner.generation_calls == 1
    assert any("Invalid action" in item for item in output)
    assert any("invalid or unknown" in item for item in output)


@pytest.mark.parametrize("decision", ["reject", "invent", "alter"])
def test_reject_and_bad_identity_do_not_research(decision):
    d = demo_dependencies("single")
    s = AssessmentSession(demo_request("single"), d)
    s.start()
    if decision == "reject":
        result = s.resume(s.decision("reject"))
        assert result["report"].status == "rejected"
        assert result["current_round"] == 0
    else:
        plan = s.state["research_plan"].model_dump()
        if decision == "invent":
            plan["web_tasks"][0]["candidate_id"] = "candidate-999"
        else:
            plan["comparison_targets"] = []
        with pytest.raises(ApplicationError):
            s.decision("modify", plan)
        assert s.graph.get_state(s.config).tasks[0].interrupts
    assert d.search.calls == 0 and not d.github.calls
    assert d.analyzer.generation_calls == 0


def test_interrupted_paid_attempt_is_not_replayed():
    d = demo_dependencies("single")
    calls = []

    def interrupt(shared):
        calls.append(shared)
        raise KeyboardInterrupt()

    d.planner.count = interrupt
    s = AssessmentSession(demo_request("single"), d)
    with pytest.raises(KeyboardInterrupt):
        s.start()
    summary = s.interruption_summary()
    assert "possibly incomplete" in summary and "Do not replay" in summary
    checkpoints = list(s.saver.list(s.config))
    ledgers = [c.checkpoint["channel_values"].get("budget_ledger") for c in checkpoints]
    assert any(ledger is not None and ledger.outstanding > 0 for ledger in ledgers)
    assert len(calls) == 1
    assert not s.graph.get_state(s.config).values.get("report")


@pytest.mark.parametrize("fail_at", [None, 1, 2, 3])
def test_http_ownership_even_partial_adapter_construction(monkeypatch, fail_at):
    import techlens.openai_planner as planner
    import techlens.openai_analyzer as analyzer
    import techlens.tavily_search as tavily
    clients, adapters = [], []

    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            assert kwargs == {"follow_redirects": False, "trust_env": False}
            clients.append(self)

        def close(self):
            self.closed = True

    def construct(**kwargs):
        adapters.append(kwargs)
        if len(adapters) == fail_at:
            raise RuntimeError("sentinel-private")
        return object()

    monkeypatch.setattr(planner, "OpenAIResearchPlanner", construct)
    monkeypatch.setattr(analyzer, "OpenAIAdoptionAnalyzer", construct)
    monkeypatch.setattr(tavily, "TavilySearchProvider", construct)
    try:
        with ExitStack() as stack:
            live_dependencies(stack, demo_rates(), "synthetic-local",
                {"OPENAI_API_KEY": "sentinel", "TAVILY_API_KEY": "sentinel"}, client_factory=Client)
    except RuntimeError:
        assert fail_at is not None
    assert clients and all(c.closed for c in clients)


def test_tracing_suppression_and_restore(monkeypatch):
    import os
    import langsmith
    from langsmith.run_helpers import get_tracing_context
    constructions = []

    def forbidden_client(*args, **kwargs):
        constructions.append(True)
        raise AssertionError("Observability client must not be constructed")

    monkeypatch.setattr(langsmith.Client, "__init__", forbidden_client)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with private_execution():
        assert os.environ["LANGSMITH_TRACING"] == "false"
        assert get_tracing_context()["enabled"] is False
        run_review(AssessmentSession(demo_request("single"), demo_dependencies("single")),
                   lambda _: "approve", lambda _: None, PublicOutput({}))
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert constructions == []
