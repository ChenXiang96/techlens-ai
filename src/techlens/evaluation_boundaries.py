"""Canned, network-impossible boundary probes. Not a T-006B/live runner.

Every HTTP client is constructed here with MockTransport and .invalid URLs.
No credentials, live adapter, environment configuration or transport injection.
"""
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
from langgraph.types import Command
from langsmith import tracing_context

from techlens.budget import AttemptOutcome, RateCard, Usage, digest
from techlens.graph import build_graph
from techlens.planning import validate_plan
from techlens.research_models import WebResearchTaskResult

MARKER = "EVAL-LEAK-MARKER"
NOW = datetime(2026, 9, 30, tzinfo=UTC)


def fictional_rates():
    return RateCard(model="offline-fixture", source_reference="FICTIONAL fixture, not a live price",
        effective_date=date(2026, 9, 30), input_usd_per_million=Decimal("1"),
        output_usd_per_million=Decimal("1"), max_input_tokens=32000, max_output_tokens=8192,
        count_upper_fee=Decimal("0.002"), count_fixed_fee=Decimal("0.002"),
        count_source_reference="FICTIONAL count fixture", count_effective_date=date(2026, 9, 30),
        search_upper_fee=Decimal("0.001"), search_fixed_fee=Decimal("0.001"),
        search_source_reference="FICTIONAL search fixture", search_effective_date=date(2026, 9, 30))


class CannedTransport:
    """Only explicit in-memory routes exist; even attempted other hosts are recorded."""
    def __init__(self):
        self.calls = []
        self.denied = []
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle),
                                  follow_redirects=False, trust_env=False)

    def _handle(self, request):
        import json
        if request.url.host not in {"analyzer.invalid", "planner.invalid", "search.invalid"}:
            self.denied.append(str(request.url))
            raise ValueError("Unauthorized fake destination")
        body = json.loads(request.content)
        self.calls.append({"host": request.url.host, "path": request.url.path,
                           "input_digest": digest(body)})
        return httpx.Response(200, json={"ok": True})

    def send(self, host, path, body):
        self.client.post(f"https://{host}.invalid/{path}", json=body).raise_for_status()

    def close(self):
        self.client.close()


class CannedAdapter:
    model = "offline-fixture"
    max_output_tokens = 8192

    def __init__(self, transport, stage, output):
        self.transport, self.stage, self.output = transport, stage, output
        self.inputs = []

    def prepare(self, value):
        data = value.model_dump(mode="json") if hasattr(value, "model_dump") else deepcopy(value)
        return {"model": self.model, "input": data, "instructions": "Offline canned boundary probe"}

    def count(self, shared):
        self.transport.send(self.stage, "count", shared)
        return AttemptOutcome(status="succeeded", data={"input_tokens": 100})

    def generate(self, shared):
        self.transport.send(self.stage, "generate", shared)
        self.inputs.append(deepcopy(shared["input"]))
        return AttemptOutcome(status="succeeded", data=deepcopy(self.output),
                              usage=Usage(input_tokens=100, output_tokens=50))

    def finalize(self, output, request):
        return validate_plan(output, request)


def local_trace(case, sample):
    transport = CannedTransport()
    try:
        adapter = CannedAdapter(transport, "analyzer", sample.draft)
        before = digest(case.model_dump(mode="json"))
        outcome = adapter.generate(adapter.prepare(case.envelope))
        return {"route": "local_canned_adapter", "scope": "not_full_graph",
                "layers": {"boundary": "passed"}, "calls": transport.calls,
                "unauthorized_destinations": transport.denied,
                "authority_unchanged": before == digest(case.model_dump(mode="json")),
                "input_matches_projection": adapter.inputs == [case.envelope],
                "output_matches_sample": outcome.data == sample.draft,
                "marker_in_intended_input": MARKER in str(case.envelope),
                "marker_in_canned_output": MARKER in str(sample.draft),
                "meaning": "Canned transport/wiring only; no model resistance measured"}
    finally:
        transport.close()


def graph_trace(case, sample, route="approve"):
    # LangGraph already depends on LangSmith. Explicitly disable inherited tracing
    # for these offline probes; this is not observability integration.
    with tracing_context(enabled=False):
        return _graph_trace(case, sample, route)


def _graph_trace(case, sample, route):
    """Exercise the existing complete production graph with only canned dependencies."""
    source = case.source_variant
    transport = CannedTransport()
    planner = CannedAdapter(transport, "planner", source.plan.model_dump(mode="json"))
    analyzer = CannedAdapter(transport, "analyzer", sample.draft)
    github_calls = []

    def github(candidate):
        github_calls.append(str(candidate.repository_url))
        found = next(g for g in source.github if str(g.repository_url) == str(candidate.repository_url))
        return found.engineering_signals.model_copy(deep=True)

    class Search:
        def search(self, task):
            transport.send("search", "search", task.model_dump(mode="json"))
            found = next(w for w in source.web if w.task.task_id == task.task_id)
            return WebResearchTaskResult.model_validate(found.model_dump(mode="json"))

    # No request mutation for denial: missing pricing exercises the real admission gate.
    graph = build_graph(planner=planner, analyzer=analyzer, search_provider=Search(),
        github_collector=github, rates=None if route == "budget_denial" else fictional_rates(),
        clock=lambda: NOW, sleep=lambda _: None)
    config = {"configurable": {"thread_id": f"eval-{sample.sample_id}-{route}"}}
    try:
        initial = list(graph.stream({"request": case.request}, config, stream_mode="updates"))
        paused = graph.get_state(config)
        preapproval = {"github": len(github_calls),
                       "search": sum(c["host"] == "search.invalid" for c in transport.calls),
                       "analyzer": sum(c["host"] == "analyzer.invalid" for c in transport.calls)}
        events = list(initial)
        if paused.next:
            action = "reject" if route == "reject" else "approve"
            events += list(graph.stream(Command(resume={"action": action}), config, stream_mode="updates"))
        state = graph.get_state(config).values
        nodes = [next(iter(e)) for e in events]
        # Parallel sibling completion order is not an evaluation observation.
        # Keep all reached nodes, canonicalizing only that unordered sibling pair.
        if "collect_github" in nodes and "web_research" in nodes:
            positions = sorted([nodes.index("collect_github"), nodes.index("web_research")])
            nodes[positions[0]], nodes[positions[1]] = "collect_github", "web_research"
        reached = {
            "schema": "passed" if state.get("analysis_status") == "succeeded" else "not_exercised",
            "projection": "passed" if "analysis_projection" in state else "not_exercised",
            "finalization": "passed" if state.get("analysis_status") == "succeeded" else "not_exercised",
            "source_integrity": "passed" if "evidence_validation" in state else "not_exercised",
            "report": "passed" if "report" in state else "not_exercised",
            "boundary": "passed",
        }
        if state.get("analysis_status") == "invalid":
            reached["finalization"] = "rejected"
        ledger = state["budget_ledger"]
        return {"route": route, "scope": "existing_full_graph_with_fakes", "layers": reached,
                "nodes": nodes, "preapproval": preapproval,
                "node_order_semantics": "Observed sequential phases; parallel collection siblings canonically ordered",
                "calls": transport.calls, "github_repository_urls": github_calls,
                "unauthorized_destinations": transport.denied,
                "authority_unchanged": state["request"] == case.request,
                "repository_authority": github_calls == ([str(c.repository_url) for c in case.request.candidates]
                    if route == "approve" else []),
                "input_matches_projection": analyzer.inputs == ([case.envelope] if route == "approve" else []),
                "current_round": state["current_round"], "report_status": state["report"].status,
                "marker_in_accepted_claims": any(MARKER in c.statement for c in state.get("claims", [])),
                "admitted_attempts": len(ledger.attempts),
                "settled_estimate_usd": str(ledger.settled),
                "outstanding_reservations_usd": str(ledger.outstanding),
                "errors": state["errors"],
                "meaning": "Fictional accounting and canned outputs; not a live measurement"}
    finally:
        transport.close()
