"""Human-supervised planning, bounded research fan-out, and explicit join."""

from functools import partial
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from techlens.github_client import collect_engineering_signals
from techlens.models import AssessmentRequest
from techlens.nodes import (
    GitHubCollector, collect_github, generate_report_stub, human_review,
    plan_research, research_complete, route_review, validate_request, web_research,
)
from techlens.planning import ResearchPlanner
from techlens.search import SearchProvider
from techlens.state import TechLensState


class _AssessmentInput(TypedDict):
    request: AssessmentRequest


def build_graph(
    *,
    planner: ResearchPlanner,
    search_provider: SearchProvider,
    github_collector: GitHubCollector = collect_engineering_signals,
    checkpointer: InMemorySaver | None = None,
) -> CompiledStateGraph:
    """Keep this graph/saver and the same thread_id for start and resume."""
    graph = StateGraph(TechLensState, input_schema=_AssessmentInput)
    graph.add_node("validate_request", validate_request)
    graph.add_node("plan_research", partial(plan_research, planner=planner))
    graph.add_node("human_review", human_review)
    graph.add_node("collect_github", partial(collect_github, collector=github_collector))
    graph.add_node("web_research", partial(web_research, provider=search_provider))
    graph.add_node("research_complete", research_complete)
    graph.add_node("generate_report_stub", generate_report_stub)

    graph.add_edge(START, "validate_request")
    graph.add_edge("validate_request", "plan_research")
    graph.add_edge("plan_research", "human_review")
    graph.add_conditional_edges(
        "human_review", route_review,
        ["collect_github", "web_research", "generate_report_stub"],
    )
    graph.add_edge(["collect_github", "web_research"], "research_complete")
    graph.add_edge("research_complete", "generate_report_stub")
    graph.add_edge("generate_report_stub", END)
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())
