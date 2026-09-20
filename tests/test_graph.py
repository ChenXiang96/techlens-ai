import techlens

from techlens import (
    AssessmentReport,
    AssessmentRequest,
    ResearchPlan,
    TeamProfile,
    TechnologyCandidate,
    build_graph,
)


def make_request() -> AssessmentRequest:
    return AssessmentRequest(
        assessment_id="assessment-001",
        candidates=[
            TechnologyCandidate(
                name="LangGraph",
                repository_url="https://github.com/langchain-ai/langgraph",
            )
        ],
        team_profile=TeamProfile(
            team_size=6,
            primary_languages=["Python"],
            backend_stack=["Django"],
        ),
        priorities=["Production readiness", "Maintenance health"],
    )


def test_package_imports() -> None:
    assert techlens.AssessmentRequest is AssessmentRequest
    assert callable(techlens.build_graph)


def test_graph_compiles() -> None:
    graph = build_graph()

    assert graph is not None


def test_graph_traversal_order() -> None:
    graph = build_graph()

    events = list(
        graph.stream({"request": make_request()}, stream_mode="updates")
    )
    node_names = [next(iter(event)) for event in events]

    assert node_names == [
        "validate_request",
        "plan_research",
        "research_stub",
        "generate_report_stub",
    ]


def test_graph_end_to_end_returns_typed_report_and_zero_round() -> None:
    result = build_graph().invoke({"request": make_request()})

    assert isinstance(result["request"], AssessmentRequest)
    assert isinstance(result["research_plan"], ResearchPlan)
    assert isinstance(result["report"], AssessmentReport)
    assert result["report"].status == "stub_complete"
    assert result["report"].assessment_id == "assessment-001"
    assert result["current_round"] == 0
    assert result["errors"] == []
    assert result["research_notes"]

