"""One process, one retained assessment session, explicitly owned resources."""
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import os
from uuid import uuid4
from threading import RLock

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
from techlens.presentation import safe_url, render_plan, render_plan_changes, REVIEW_HELP
from techlens.research_models import PlanReviewDecision
from techlens.authorization import require_authorization, LiveAuthorizationError
from techlens.request_builder import InputBoundaryError


_privacy_lock = RLock()
_privacy_users = 0
_privacy_previous = None


@contextmanager
def private_execution():
    """Suppress inherited tracing/logging, without importing the evaluation harness."""
    names = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "OPENAI_LOG")
    global _privacy_users, _privacy_previous
    with _privacy_lock:
        if _privacy_users == 0:
            _privacy_previous = ({name: os.environ.get(name) for name in names}, logging.root.manager.disable)
        _privacy_users += 1
        for name in names:
            os.environ[name] = "false" if name != "OPENAI_LOG" else ""
        logging.disable(logging.CRITICAL)
    try:
        with tracing_context(enabled=False):
            yield
    finally:
        with _privacy_lock:
            _privacy_users -= 1
            if _privacy_users == 0:
                previous, disabled = _privacy_previous
                logging.disable(disabled)
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
                _privacy_previous = None


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


def live_dependencies(stack, rates, model, env, *, authorization=None, client_factory=httpx.Client):
    authorization = require_authorization(authorization)
    authorization.check(rates=rates)
    if model != authorization.model:
        raise LiveAuthorizationError("Provider model differs from authorization")
    stack.callback(authorization.close)
    from techlens.openai_planner import OpenAIResearchPlanner
    from techlens.openai_analyzer import OpenAIAdoptionAnalyzer
    from techlens.tavily_search import TavilySearchProvider
    from techlens.github_client import collect_engineering_signals

    def client():
        resource = client_factory(follow_redirects=False, trust_env=False)
        authorization.own(resource)  # Own it BEFORE constructing an adapter.
        return resource

    complete = False
    try:
        with private_execution():
            planner = OpenAIResearchPlanner(model=model, http_client=client(), authorization=authorization)
            analyzer = OpenAIAdoptionAnalyzer(model=model, http_client=client(), authorization=authorization)
            search = TavilySearchProvider(http_client=client(), authorization=authorization)
            github_http = client()
        def github(candidate):
            return collect_engineering_signals(candidate, token=authorization.credential("GITHUB_TOKEN"),
                http_client=github_http, authorization=authorization)
        github.authorization = authorization
        result = Dependencies(planner, analyzer, search, github, rates)
        complete = True
    except Exception:
        pass
    finally:
        if not complete:
            authorization.close()
    if not complete:
        raise LiveAuthorizationError("Live dependency construction failed; permission revoked")
    return result


class AssessmentSession:
    def __init__(self, request, dependencies, *, thread_id=None, authorization=None):
        self.authorization = authorization
        if authorization:
            authorization.check(request=request, rates=dependencies.rates, run_id=thread_id)
        self.request = request
        self.executor, self.saver = AttemptExecutor(), InMemorySaver()
        self.config = {"configurable": {"thread_id": thread_id or (authorization.run_id if authorization else str(uuid4()))}}
        options = {"clock": dependencies.clock} if dependencies.clock else {}
        self.graph = build_graph(planner=dependencies.planner, analyzer=dependencies.analyzer,
            search_provider=dependencies.search, github_collector=dependencies.github,
            rates=dependencies.rates, executor=self.executor, checkpointer=self.saver,
            authorization=authorization, **options)
        self.state = None

    def close(self):
        if self.authorization:
            self.authorization.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

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


def run_review(session, read, write, public, *, mode=None):
    state = session.start()
    if not state.get("__interrupt__"):
        return state
    plan = validate_plan(state["research_plan"], session.request)
    catalog = [c.model_dump(mode="json") for c in candidate_catalog(session.request)]
    for candidate in catalog:
        safe_url(candidate["repository_url"])
    mode = mode or ("live" if session.authorization else "demo")
    write(render_plan(session.request, plan, public, mode=mode))
    while True:
        raw = read("Action [approve / modify / reject / save-plan / view / json / help]: ")
        public.check(raw)
        action = raw.strip().casefold()
        if action not in {"approve", "modify", "reject", "save-plan", "view", "json", "help"}:
            write("Invalid action; research remains unapproved.\n")
            continue
        try:
            if action in {"view", "json", "help"}:
                if action == "view":
                    write(render_plan(session.request, plan, public, mode=mode))
                elif action == "json":
                    write(public.json({"candidates": catalog, "research_plan": plan.model_dump(mode="json")}))
                else:
                    write(REVIEW_HELP)
                continue
            if action == "save-plan":
                path = read("New plan JSON file: ").strip()
                public.check(path)
                save_plan(path, plan, public)
                write("Plan saved. Review is still pending.\n")
                continue
            modified = None
            if action == "modify":
                path = read("Modified plan JSON file: ").strip()
                public.check(path)
                modified = parse_model(load_json(path), ResearchPlan)
                public.check(modified.model_dump(mode="json"))
            decision = session.decision(action, modified)
            if action == "modify":
                write(render_plan_changes(session.request, plan, decision.modified_plan, public))
                while True:
                    answer = read("Type APPLY to submit the modified plan, or CANCEL to retain the pending plan: ")
                    public.check(answer)
                    if answer in {"APPLY", "CANCEL"}:
                        break
                    write("Enter exactly APPLY or CANCEL. Research remains unapproved.\n")
                if answer == "CANCEL":
                    write("Modification cancelled. The original plan is still pending.\n")
                    continue
        except InputBoundaryError:
            # The bounded read may leave a suffix. It is not another user action.
            raise
        except ApplicationError as error:
            write(public.terminal(str(error)) + "\n")
            continue
        # Unexpected graph failure is NOT an invalid-input loop or retry.
        return session.resume(decision)
