"""Checkpoint-separated provider attempts and terminal collection subgraphs."""
import json
from copy import deepcopy
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from pydantic import ValidationError
from techlens.budget import (BudgetLedger, AttemptRecord, AttemptOutcome, VerifiedInputCount,
                            Usage, digest, reserve, reconcile, retryable)
from techlens.execution import reservation_committed
from techlens.models import AssessmentRequest, ResearchPlan
from techlens.analysis_models import AnalysisDraft
from techlens.collection_models import CollectionOutcome
from techlens.research_models import CandidateEngineeringSignals, WebResearchTaskResult
from techlens.planning import candidate_catalog, validate_plan, PlannerError
from techlens.nodes import _approved_plan
from techlens.github_client import (GitHubCollectionError, GitHubRepositoryUnavailableError,
    GitHubAuthenticationError, GitHubRateLimitError, GitHubTransportError)
from techlens.search import (SearchError, SearchTransportError, SearchAuthenticationError,
    SearchQuotaError, SearchRateLimitError, SearchServerError)


class AttemptState(TypedDict, total=False):
    request: AssessmentRequest
    budget_ledger: BudgetLedger
    errors: list[str]
    research_plan: ResearchPlan
    analysis_envelope: dict
    analysis_draft: AnalysisDraft
    analysis_status: str
    shared: dict
    pending: AttemptRecord
    verified: VerifiedInputCount
    route: str
    number: int
    outcome: AttemptOutcome


class PlannerOutput(TypedDict, total=False):
    budget_ledger: BudgetLedger
    errors: list[str]
    research_plan: ResearchPlan


class AnalyzerOutput(TypedDict, total=False):
    budget_ledger: BudgetLedger
    errors: list[str]
    analysis_draft: AnalysisDraft
    analysis_status: str


def llm_graph(stage, adapter, executor, saver, sleep):
    graph = StateGraph(AttemptState, output_schema=PlannerOutput if stage == "planner" else AnalyzerOutput)

    def fail(state, reason):
        return {"errors": [*state.get("errors", []), f"{stage}:{reason}"],
                "analysis_status": "unavailable", "route": "end"}

    def prepare(state):
        if adapter is None:
            return fail(state, "not_configured")
        value = state["request"].model_copy(deep=True) if stage == "planner" else deepcopy(state["analysis_envelope"])
        shared = adapter.prepare(value)
        if len(json.dumps(shared).encode()) > 160000:
            return fail(state, "input_bound")
        return {"shared": shared, "number": 1, "route": "count"}

    def admit(state, kind):
        shared = state["shared"]
        payload = shared if kind == "count" else {**shared, "max_output_tokens": adapter.max_output_tokens, "store": False}
        operation = f"{stage}:count" if kind == "count" else f"{stage}:generate"
        admission = reserve(state["budget_ledger"], operation, kind, payload,
                            attempt_number=1 if kind == "count" else state["number"], count=state.get("verified"))
        if admission.denial:
            return fail(state, admission.denial)
        if not reservation_committed(saver, admission.ledger.run_id, admission.attempt):
            executor.register(admission)
        return {"budget_ledger": admission.ledger, "pending": admission.attempt, "route": "dispatch"}

    def dispatch(state, kind):
        ledger, attempt = state["budget_ledger"], state["pending"]
        expected = state["shared"] if kind == "count" else {
            **state["shared"], "max_output_tokens": adapter.max_output_tokens, "store": False}
        if digest(expected) != attempt.request_digest:
            raise ValueError("Prepared request differs from reservation")
        outcome = executor.dispatch(ledger.run_id, attempt,
            committed=lambda: reservation_committed(saver, ledger.run_id, attempt),
            send=lambda: adapter.count(state["shared"]) if kind == "count" else adapter.generate(state["shared"]))
        ledger = reconcile(ledger, attempt.attempt_id, outcome)
        update = {"budget_ledger": ledger, "outcome": outcome}
        if outcome.status == "failed":
            if kind == "generation" and retryable(outcome) and state["number"] < 2 and not ledger.overrun:
                sleep(outcome.retry_after if outcome.retry_after is not None else 0.25)
                return {**update, "number": state["number"] + 1, "route": "retry"}
            return {**update, **fail(state, outcome.category)}
        if kind == "count":
            return {**update, "verified": VerifiedInputCount(operation_id=attempt.operation_id,
                attempt_id=attempt.attempt_id, model=state["shared"]["model"],
                input_tokens=outcome.data["input_tokens"], shared_request_digest=digest(state["shared"])), "route": "generate"}
        invalid = False
        try:
            result = adapter.finalize(outcome.data, state["request"]) if stage == "planner" else AnalysisDraft.model_validate(outcome.data)
        except (ValueError, TypeError, PlannerError):
            invalid = True
        if invalid:
            return {**update, **fail(state, "invalid_draft")}
        return {**update, "route": "end", **({"research_plan": result} if stage == "planner" else
                {"analysis_draft": result, "analysis_status": "draft_ready"})}

    graph.add_node("prepare", prepare)
    graph.add_node("reserve_count", lambda s: admit(s, "count"))
    graph.add_node("dispatch_count", lambda s: dispatch(s, "count"))
    graph.add_node("reserve_generation", lambda s: admit(s, "generation"))
    graph.add_node("dispatch_generation", lambda s: dispatch(s, "generation"))
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges("prepare", lambda s: s["route"], {"count": "reserve_count", "end": END})
    graph.add_conditional_edges("reserve_count", lambda s: s["route"], {"dispatch": "dispatch_count", "end": END})
    graph.add_conditional_edges("dispatch_count", lambda s: s["route"], {"generate": "reserve_generation", "end": END})
    graph.add_conditional_edges("reserve_generation", lambda s: s["route"], {"dispatch": "dispatch_generation", "end": END})
    graph.add_conditional_edges("dispatch_generation", lambda s: s["route"], {"retry": "reserve_generation", "end": END})
    return graph.compile()


class CollectionState(TypedDict, total=False):
    request: AssessmentRequest
    research_plan: ResearchPlan
    plan_status: str
    current_round: int
    budget_ledger: BudgetLedger
    github_signals: list[CandidateEngineeringSignals]
    github_outcomes: list[CollectionOutcome]
    web_research_results: list[WebResearchTaskResult]
    web_outcomes: list[CollectionOutcome]
    cursor: int
    pending: AttemptRecord
    route: str


class GitHubOutput(TypedDict):
    github_signals: list[CandidateEngineeringSignals]
    github_outcomes: list[CollectionOutcome]


class WebOutput(TypedDict):
    web_research_results: list[WebResearchTaskResult]
    web_outcomes: list[CollectionOutcome]
    budget_ledger: BudgetLedger


def github_graph(collector):
    graph = StateGraph(CollectionState, output_schema=GitHubOutput)
    def start(state):
        _approved_plan(state)
        return {"cursor": 0, "github_signals": [], "github_outcomes": []}
    def collect(state):
        index = state["cursor"]
        candidate = state["request"].candidates[index]
        entry = candidate_catalog(state["request"])[index]
        category, signals = None, None
        try:
            signals = collector(candidate)
        except GitHubRepositoryUnavailableError:
            category = "repository_unavailable"
        except GitHubAuthenticationError:
            category = "authentication_or_permission"
        except GitHubRateLimitError:
            category = "rate_limit"
        except GitHubTransportError as error:
            category = error.kind
        except GitHubCollectionError:
            category = "provider_response"
        results = list(state["github_signals"])
        if category is None:
            results.append(CandidateEngineeringSignals(candidate_id=entry.candidate_id,
                candidate_name=candidate.name, repository_url=candidate.repository_url, engineering_signals=signals))
        outcome = CollectionOutcome(operation_id=f"github:{entry.candidate_id}", source_kind="github",
            target_id=entry.candidate_id, status="succeeded" if category is None else "failed", error_category=category)
        return {"github_signals": results, "github_outcomes": [*state["github_outcomes"], outcome], "cursor": index + 1}
    graph.add_node("start", start)
    graph.add_node("collect_one", collect)
    graph.add_edge(START, "start")
    graph.add_edge("start", "collect_one")
    graph.add_conditional_edges("collect_one", lambda s: "more" if s["cursor"] < len(s["request"].candidates) else "done",
                                {"more": "collect_one", "done": END})
    return graph.compile()


def web_graph(provider, executor, saver, sleep):
    graph = StateGraph(CollectionState, output_schema=WebOutput)
    def start(state):
        _approved_plan(state)
        return {"cursor": 0, "web_outcomes": [], "web_research_results": []}
    def terminal(state, status, category, result=None):
        task = state["research_plan"].web_tasks[state["cursor"]]
        attempts = [a.attempt_id for a in state["budget_ledger"].attempts if a.operation_id == f"web:{task.task_id}"]
        outcome = CollectionOutcome(operation_id=f"web:{task.task_id}", source_kind="web", target_id=task.task_id,
                                    status=status, error_category=category, attempt_ids=attempts)
        return {"web_outcomes": [*state["web_outcomes"], outcome], "cursor": state["cursor"] + 1,
                "web_research_results": [*state["web_research_results"], *([result] if result else [])], "route": "next"}
    def admit(state):
        tasks = state["research_plan"].web_tasks
        if state["cursor"] == len(tasks):
            return {"route": "end"}
        task, ledger = tasks[state["cursor"]], state["budget_ledger"]
        attempts = [a for a in ledger.attempts if a.operation_id == f"web:{task.task_id}"]
        if sum(a.kind == "search" for a in ledger.attempts) >= state["request"].constraints.max_web_searches:
            return terminal(state, "failed" if attempts else "skipped_limit", "search_limit")
        number = len(attempts) if attempts and attempts[-1].state == "reserved" else len(attempts)+1
        admission = reserve(ledger, f"web:{task.task_id}", "search", task.model_dump(mode="json"), attempt_number=number)
        if admission.denial:
            return terminal(state, "failed" if attempts else "skipped_budget", admission.denial)
        if not reservation_committed(saver, admission.ledger.run_id, admission.attempt):
            executor.register(admission)
        return {"budget_ledger": admission.ledger, "pending": admission.attempt, "route": "dispatch"}
    def send(task):
        category, result = None, None
        try:
            result = provider.search(task)
            result = WebResearchTaskResult.model_validate(result.model_dump())
        except SearchTransportError as error:
            category = error.kind
        except SearchAuthenticationError:
            category = "authentication"
        except SearchQuotaError:
            category = "quota"
        except SearchRateLimitError:
            category = "rate_limit"
        except SearchServerError:
            category = "server"
        except (SearchError, ValidationError, ValueError, AttributeError):
            category = "malformed"
        return AttemptOutcome(status="failed" if category else "succeeded", category=category,
            data=result.model_dump(mode="json") if category is None else None,
            usage=Usage(credits=result.credits_used if category is None else None))
    def dispatch(state):
        ledger, attempt = state["budget_ledger"], state["pending"]
        task = state["research_plan"].web_tasks[state["cursor"]]
        if digest(task.model_dump(mode="json")) != attempt.request_digest:
            raise ValueError("Search task differs from reservation")
        outcome = executor.dispatch(ledger.run_id, attempt,
            committed=lambda: reservation_committed(saver, ledger.run_id, attempt), send=lambda: send(task))
        ledger = reconcile(ledger, attempt.attempt_id, outcome)
        current = {**state, "budget_ledger": ledger}
        if outcome.status == "succeeded":
            return {"budget_ledger": ledger, **terminal(current, "succeeded", None, WebResearchTaskResult.model_validate(outcome.data))}
        n = sum(a.operation_id == attempt.operation_id for a in ledger.attempts)
        if retryable(outcome) and n < 2 and not ledger.overrun:
            sleep(0.25)
            return {"budget_ledger": ledger, "route": "retry"}
        return {"budget_ledger": ledger, **terminal(current, "failed", outcome.category)}
    graph.add_node("start", start)
    graph.add_node("reserve_search", admit)
    graph.add_node("dispatch_search", dispatch)
    graph.add_edge(START, "start")
    graph.add_edge("start", "reserve_search")
    graph.add_conditional_edges("reserve_search", lambda s: s["route"],
        {"end": END, "next": "reserve_search", "dispatch": "dispatch_search"})
    graph.add_conditional_edges("dispatch_search", lambda s: s["route"], {"next": "reserve_search", "retry": "reserve_search"})
    return graph.compile()
