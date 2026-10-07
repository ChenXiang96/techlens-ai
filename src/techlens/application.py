"""One process, one retained assessment session, explicitly owned resources."""
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import os
from uuid import uuid4

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from langsmith import tracing_context

from techlens.configuration import ApplicationError, load_json, parse_model
from techlens.execution import AttemptExecutor
from techlens.exporting import save_plan
from techlens.graph import build_graph
from techlens.models import ResearchPlan
from techlens.planning import validate_plan, PlannerError, candidate_catalog
from techlens.presentation import safe_url
from techlens.research_models import PlanReviewDecision


@contextmanager
def private_execution():
    """Suppress inherited tracing/logging, without importing the evaluation harness."""
    names = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "OPENAI_LOG")
    previous = {name: os.environ.get(name) for name in names}
    disabled = logging.root.manager.disable
    try:
        for name in names:
            os.environ[name] = "false" if name != "OPENAI_LOG" else ""
        logging.disable(logging.CRITICAL)
        with tracing_context(enabled=False):
            yield
    finally:
        logging.disable(disabled)
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@dataclass
class Dependencies:
    planner: object
    analyzer: object
    search: object
    github: object
    rates: object
    clock: object = None


def demo_dependencies(scenario):
    from techlens.demo import CannedPlanner, CannedAnalyzer, CannedSearch, CannedGitHub, demo_rates, NOW
    return Dependencies(CannedPlanner(), CannedAnalyzer(), CannedSearch(scenario == "partial"),
                        CannedGitHub(), demo_rates(), lambda: NOW)


def live_dependencies(stack, rates, model, env, *, client_factory=httpx.Client):
    from techlens.openai_planner import OpenAIResearchPlanner
    from techlens.openai_analyzer import OpenAIAdoptionAnalyzer
    from techlens.tavily_search import TavilySearchProvider
    from techlens.github_client import collect_engineering_signals

    def client():
        resource = client_factory(follow_redirects=False, trust_env=False)
        stack.callback(resource.close)  # Own it BEFORE constructing an adapter.
        return resource

    planner = OpenAIResearchPlanner(api_key=env["OPENAI_API_KEY"], model=model, http_client=client())
    analyzer = OpenAIAdoptionAnalyzer(api_key=env["OPENAI_API_KEY"], model=model, http_client=client())
    search = TavilySearchProvider(api_key=env["TAVILY_API_KEY"], http_client=client())
    github_http = client()
    return Dependencies(planner, analyzer, search,
        lambda candidate: collect_engineering_signals(candidate, token=env.get("GITHUB_TOKEN", ""),
                                                       http_client=github_http), rates)


class AssessmentSession:
    def __init__(self, request, dependencies, *, thread_id=None):
        self.request = request
        self.executor, self.saver = AttemptExecutor(), InMemorySaver()
        self.config = {"configurable": {"thread_id": thread_id or str(uuid4())}}
        options = {"clock": dependencies.clock} if dependencies.clock else {}
        self.graph = build_graph(planner=dependencies.planner, analyzer=dependencies.analyzer,
            search_provider=dependencies.search, github_collector=dependencies.github,
            rates=dependencies.rates, executor=self.executor, checkpointer=self.saver, **options)
        self.state = None

    def start(self):
        self.state = self.graph.invoke({"request": self.request}, self.config)
        return self.state

    def decision(self, action, modified=None):
        invalid = False
        try:
            if action == "modify":
                modified = validate_plan(modified, self.request)
            elif action == "approve":
                validate_plan(self.state["research_plan"], self.request)
            decision = PlanReviewDecision(action=action, modified_plan=modified)
        except (ValueError, TypeError, KeyError, PlannerError):
            invalid = True
        if invalid:
            raise ApplicationError("Review decision is invalid; research remains unapproved.")
        return decision

    def resume(self, decision):
        self.state = self.graph.invoke(Command(resume=decision.model_dump()), self.config)
        return self.state

    def interruption_summary(self):
        # Read only. No reconciliation, reservation release, or resume.
        try:
            values = self.graph.get_state(self.config).values
            ledger = values.get("budget_ledger")
            if ledger is not None:
                return ("Interrupted; no terminal report asserted. Last saved accounting (possibly incomplete): "
                        f"settled estimate USD {ledger.settled}; outstanding USD {ledger.outstanding}. "
                        "Later attempts/charges may not be reflected; no liability was released. Do not replay this run.")
        except Exception:
            pass
        return "Interrupted; accounting unavailable, not zero. Charges may be unresolved. Do not replay this run."


def run_review(session, read, write, public):
    state = session.start()
    if not state.get("__interrupt__"):
        return state
    plan = validate_plan(state["research_plan"], session.request)
    catalog = [c.model_dump(mode="json") for c in candidate_catalog(session.request)]
    for candidate in catalog:
        safe_url(candidate["repository_url"])
    display = public.json({"candidates": catalog, "research_plan": plan.model_dump(mode="json")})
    # JSON escapes all user-controlled newlines; application owns every prompt.
    write("RESEARCH PLAN — review before external research\n" + display)
    while True:
        action = read("Action [approve / modify / reject / save-plan]: ").strip().casefold()
        if action not in {"approve", "modify", "reject", "save-plan"}:
            write("Invalid action; research remains unapproved.\n")
            continue
        try:
            if action == "save-plan":
                save_plan(read("New plan JSON file: ").strip(), plan, public)
                write("Plan saved. Review is still pending.\n")
                continue
            modified = None
            if action == "modify":
                modified = parse_model(load_json(read("Modified plan JSON file: ").strip()), ResearchPlan)
                public.check(modified.model_dump(mode="json"))
            decision = session.decision(action, modified)
        except ApplicationError as error:
            write(public.terminal(str(error)) + "\n")
            continue
        # Unexpected graph failure is NOT an invalid-input loop or retry.
        return session.resume(decision)
