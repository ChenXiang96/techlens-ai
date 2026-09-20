"""Fixed LangGraph topology for the TechLens Day-1 workflow."""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from techlens.nodes import (
    generate_report_stub,
    plan_research,
    research_stub,
    validate_request,
)
from techlens.state import TechLensState


def build_graph() -> CompiledStateGraph:
    """Build and compile the deterministic T-001 graph."""
    graph = StateGraph(TechLensState)
    graph.add_node("validate_request", validate_request)
    graph.add_node("plan_research", plan_research)
    graph.add_node("research_stub", research_stub)
    graph.add_node("generate_report_stub", generate_report_stub)

    graph.add_edge(START, "validate_request")
    graph.add_edge("validate_request", "plan_research")
    graph.add_edge("plan_research", "research_stub")
    graph.add_edge("research_stub", "generate_report_stub")
    graph.add_edge("generate_report_stub", END)

    return graph.compile()

