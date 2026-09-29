"""Budgeted, process-local human-supervised assessment graph."""
from datetime import UTC, datetime
from time import sleep as real_sleep
from typing import TypedDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from techlens.github_client import collect_engineering_signals
from techlens.models import AssessmentRequest
from techlens.nodes import validate_request, human_review
from techlens.planning import validate_plan, PlanReviewError
from techlens.state import TechLensState
from techlens.execution import AttemptExecutor, ExecutionBlocked
from techlens.budget import BudgetLedger
from techlens.collection_models import CollectionCoverage, check_coverage
from techlens.evidence import normalize_records, validate_records, EvidenceError
from techlens.risk import apply_risk_rules
from techlens.analysis import project_evidence, finalize_analysis, AnalysisValidationError, unknown_analysis
from techlens.reporting import assemble_report, validate_report, report_status
from techlens.report_models import RunMetrics
from techlens.workflow import llm_graph, github_graph, web_graph


class _AssessmentInput(TypedDict):
    request: AssessmentRequest


class AssessmentGraph:
    """Retain this runner, its executor and its saver for same-process resume."""
    def __init__(self, graph, executor):
        self._graph, self.executor = graph, executor
        self.checkpointer = graph.checkpointer

    def get_state(self, config, **kwargs):
        return self._graph.get_state(config, **kwargs)

    def get_graph(self, **kwargs):
        return self._graph.get_graph(**kwargs)

    def update_state(self, *args, **kwargs):
        return self._graph.update_state(*args, **kwargs)

    def _head(self, config):
        snapshot = self._graph.get_state(config)
        return snapshot.config["configurable"].get("checkpoint_id") if snapshot.config else None

    def stream(self, value, config=None, **kwargs):
        config = dict(config or {})
        configurable = dict(config.get("configurable", {}))
        thread = configurable.get("thread_id")
        if not isinstance(thread, str) or not thread.strip():
            raise ValueError("A nonempty configurable.thread_id is required")
        if configurable.get("checkpoint_id") or configurable.get("checkpoint_ns"):
            raise ExecutionBlocked("Historical checkpoint execution is not supported")
        config["configurable"] = configurable
        config.setdefault("recursion_limit", 1000)
        head = self._head(config)
        self.executor.enter_run(thread, head)
        try:
            if head and value is not None and not isinstance(value, Command):
                raise ExecutionBlocked("An existing run cannot be initialized again")
            if not head and (value is None or isinstance(value, Command)):
                raise ValueError("No assessment request is available for this thread")
            if kwargs.pop("durability", "sync") != "sync":
                raise ExecutionBlocked("Synchronous checkpoints are required")
            yield from self._graph.stream(value, config, durability="sync", **kwargs)
        finally:
            self.executor.leave_run(thread, self._head(config))

    def invoke(self, value, config=None, **kwargs):
        for _ in self.stream(value, config, stream_mode="values", **kwargs):
            pass
        snapshot = self.get_state(config)
        result = dict(snapshot.values)
        interrupts = tuple(i for task in snapshot.tasks for i in task.interrupts)
        if interrupts:
            result["__interrupt__"] = interrupts
        return result


def build_graph(*, planner, search_provider, github_collector=collect_engineering_signals,
                analyzer=None, rates=None, checkpointer=None, executor=None,
                sleep=real_sleep, clock=lambda: datetime.now(UTC)):
    saver = checkpointer if checkpointer is not None else InMemorySaver()
    executor = executor if executor is not None else AttemptExecutor()
    graph = StateGraph(TechLensState, input_schema=_AssessmentInput)

    def initialize(state, config):
        update = validate_request(state, config)
        request = update["request"]
        run = config["configurable"]["thread_id"]
        return {**update, "budget_ledger": BudgetLedger(run_id=run,
                    ceiling_usd=request.constraints.max_run_cost_usd, rates=rates,
                    max_search_attempts=request.constraints.max_web_searches),
                "run_metrics": RunMetrics(run_id=run, assessment_id=request.assessment_id,
                                         status="running", started_at=clock())}

    def route_review(state):
        if state["plan_status"] == "rejected":
            return ["generate_report"]
        if state["plan_status"] in {"approved", "modified"}:
            return ["collect_github", "web_research"]
        raise PlanReviewError("Research requires human approval")

    def join(state):
        coverage = CollectionCoverage(github_outcomes=state["github_outcomes"], web_outcomes=state["web_outcomes"])
        invalid = False
        try:
            check_coverage(coverage, state["request"], state["research_plan"],
                           state["github_signals"], state["web_research_results"])
        except ValueError:
            invalid = True
        if invalid:
            return {"fatal_failure": "collection_integrity", "errors": [*state["errors"], "collection_integrity"]}
        return {"current_round": 1, "collection_coverage": coverage,
                "research_notes": ["Terminal collection outcomes accounted for."]}

    def normalize_evidence(state):
        failed = False
        try:
            records = normalize_records(state["request"], state["research_plan"], state["github_signals"],
                                        state["web_research_results"], state["collection_coverage"])
        except EvidenceError:
            failed = True
        if failed:
            return {"fatal_failure": "evidence_integrity", "errors": [*state["errors"], "evidence_integrity"]}
        return {"evidence": records}

    def validate_evidence(state):
        failed = False
        try:
            summary = validate_records(state["request"], state["research_plan"], state["github_signals"],
                        state["web_research_results"], state["evidence"], state["collection_coverage"])
        except EvidenceError:
            failed = True
        if failed:
            return {"fatal_failure": "evidence_integrity", "evidence": [], "errors": [*state["errors"], "evidence_integrity"]}
        return {"evidence_validation": summary}

    def risks(state):
        evaluations, flags = apply_risk_rules(state["request"], state["evidence"], state["collection_coverage"])
        return {"risk_evaluations": evaluations, "risk_flags": flags}

    def projection(state):
        failed = False
        try:
            validate_records(state["request"], state["research_plan"], state["github_signals"],
                state["web_research_results"], state["evidence"], state["collection_coverage"])
            envelope, manifest = project_evidence(state["request"], state["evidence"],
                                                 state["risk_flags"], state["collection_coverage"])
        except EvidenceError:
            return {"fatal_failure": "evidence_integrity", "evidence": [], "risk_flags": [],
                    "analysis_status": "unavailable", "errors": [*state["errors"], "evidence_integrity"]}
        except AnalysisValidationError:
            failed = True
        if failed or not manifest.eligible_evidence_ids:
            return {"analysis_status": "unavailable", "errors": [*state["errors"], "analysis:no_eligible_view"]}
        notes = (["analysis:context_omissions"] if manifest.omitted_evidence_ids else [])
        notes += ["analysis:excerpted_evidence"] if manifest.excerpted_evidence_ids else []
        return {"analysis_envelope": envelope, "analysis_projection": manifest, "analysis_status": "ready",
                "errors": [*state["errors"], *notes]}

    def validate_analysis(state):
        if state.get("analysis_status") != "draft_ready":
            return {"claims": [], "analysis_result": unknown_analysis(state["request"])}
        failed = False
        try:
            claims, result = finalize_analysis(state["analysis_draft"], state["request"],
                                               state["evidence"], state["analysis_projection"])
        except (AnalysisValidationError, EvidenceError, ValueError):
            failed = True
        if failed:
            return {"claims": [], "analysis_result": unknown_analysis(state["request"]), "analysis_status": "invalid",
                    "errors": [*state["errors"], "analysis:invalid_citations_or_draft"]}
        return {"claims": claims, "analysis_result": result, "analysis_status": "succeeded"}

    def report(state):
        if state.get("fatal_failure"):
            state = {**state, "claims": [], "evidence": [], "risk_flags": [], "analysis_result": unknown_analysis(state["request"])}
        return {"report": assemble_report(state)}

    def finalize(state):
        report = state["report"]
        validate_report(report, state)
        ledger, initial = state["budget_ledger"], state["run_metrics"]
        # Attempt totals are admissions, not proof that transport occurred.
        generations = [a for a in ledger.attempts if a.kind == "generation"]
        searches = [a for a in ledger.attempts if a.kind == "search"]
        ambiguous = [a for a in ledger.attempts if a.outcome is None or a.outcome.category == "ambiguous"]
        outcomes = state.get("github_outcomes", []) + state.get("web_outcomes", [])
        finished = clock()
        metrics = initial.model_copy(update={
            "status": report.status, "finished_at": finished,
            "elapsed_seconds": max(0, (finished - initial.started_at).total_seconds()),
            "planner_attempts": sum(a.operation_id == "planner:generate" for a in generations),
            "analyzer_attempts": sum(a.operation_id == "analyzer:generate" for a in generations),
            "llm_attempts": len(generations), "count_attempts": sum(a.kind == "count" for a in ledger.attempts),
            "ambiguous_attempts": len(ambiguous),
            "search_attempts": len(searches),
            "github_calls_measured": sum(g.engineering_signals.collection_metadata.github_api_calls_made for g in state.get("github_signals", [])),
            "github_calls_complete": all(o.status == "succeeded" for o in state.get("github_outcomes", [])),
            "input_tokens_known": sum(a.outcome.usage.input_tokens or 0 for a in generations if a.outcome),
            "output_tokens_known": sum(a.outcome.usage.output_tokens or 0 for a in generations if a.outcome),
            "token_usage_complete": all(a.outcome and a.outcome.usage.input_tokens is not None and a.outcome.usage.output_tokens is not None for a in ledger.attempts if a.kind == "generation"),
            "credits_known": sum(a.outcome.usage.credits or 0 for a in searches if a.outcome),
            "credits_complete": all(a.outcome and a.outcome.usage.credits is not None for a in ledger.attempts if a.kind == "search"),
            "settled_estimate_usd": ledger.settled, "outstanding_reservations_usd": ledger.outstanding,
            "cost_status": "overrun" if ledger.overrun else "unknown_liability" if any(a.state != "settled" for a in ledger.attempts) else "estimated",
            "research_rounds": state["current_round"], "evidence_count": len(report.sources), "claim_count": len(report.claims),
            "collection_failures": sum(o.status == "failed" for o in outcomes),
            "collection_skips": sum(o.status.startswith("skipped") for o in outcomes),
            "error_categories": sorted(set([o.error_category for o in outcomes if o.error_category] + state["errors"])),
        })
        final_report = report.model_copy(update={"metrics": metrics})
        validate_report(final_report, state)
        return {"run_metrics": metrics, "report": final_report}

    graph.add_node("validate_request", initialize)
    graph.add_node("plan_research", llm_graph("planner", planner, executor, saver, sleep))
    graph.add_node("human_review", human_review)
    graph.add_node("collect_github", github_graph(github_collector))
    graph.add_node("web_research", web_graph(search_provider, executor, saver, sleep))
    graph.add_node("research_complete", join)
    graph.add_node("normalize_evidence", normalize_evidence)
    graph.add_node("validate_evidence", validate_evidence)
    graph.add_node("apply_risk_rules", risks)
    graph.add_node("project_analysis", projection)
    graph.add_node("analyze_adoption", llm_graph("analyzer", analyzer, executor, saver, sleep))
    graph.add_node("validate_analysis", validate_analysis)
    graph.add_node("generate_report", report)
    graph.add_node("finalize_metrics", finalize)
    graph.add_edge(START, "validate_request")
    graph.add_edge("validate_request", "plan_research")
    graph.add_conditional_edges("plan_research", lambda s: "review" if "research_plan" in s else "report",
                                {"review": "human_review", "report": "generate_report"})
    graph.add_conditional_edges("human_review", route_review, ["collect_github", "web_research", "generate_report"])
    graph.add_edge(["collect_github", "web_research"], "research_complete")
    for source, target in [("research_complete", "normalize_evidence"), ("normalize_evidence", "validate_evidence"),
                           ("validate_evidence", "apply_risk_rules")]:
        graph.add_conditional_edges(source, lambda s: "failed" if s.get("fatal_failure") else "next",
                                    {"failed": "generate_report", "next": target})
    graph.add_edge("apply_risk_rules", "project_analysis")
    graph.add_conditional_edges("project_analysis", lambda s: "analyze" if s.get("analysis_status") == "ready" else "skip",
                                {"analyze": "analyze_adoption", "skip": "validate_analysis"})
    graph.add_edge("analyze_adoption", "validate_analysis")
    graph.add_edge("validate_analysis", "generate_report")
    graph.add_edge("generate_report", "finalize_metrics")
    graph.add_edge("finalize_metrics", END)
    return AssessmentGraph(graph.compile(checkpointer=saver), executor)
