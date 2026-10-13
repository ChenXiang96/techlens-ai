import pytest
from techlens.application import AssessmentSession, demo_dependencies, private_execution
from techlens.demo import demo_request
from techlens.presentation import PublicOutput
from techlens.ui_presentation import report_view, plan_changes, request_view
from techlens.configuration import ApplicationError


@pytest.mark.parametrize("scenario", ["single", "comparison", "partial"])
def test_report_projection_exact_full_fields_no_raw_sources(scenario):
    with private_execution():
        session = AssessmentSession(demo_request(scenario), demo_dependencies(scenario))
        session.start()
        state = session.resume(session.decision("approve"))
        projection = report_view(state, PublicOutput({}), scenario)
        assert projection["report"] == state["report"].model_dump(mode="json")
        assert len(projection["report"]["sections"]) == 13
        assert "budget_ledger" not in projection
        assert "evidence" not in projection
        assert "admitted" in projection["accounting_notice"]


def test_public_secret_boundary_and_unsafe_homepage():
    request = demo_request("single")
    request.candidates[0].name = "fake-sentinel-secret"
    with pytest.raises(ApplicationError):
        request_view(request, PublicOutput({"OPENAI_API_KEY": "fake-sentinel-secret"}))
    request.candidates[0].name = "normal"
    request.candidates[0].homepage = "https://user:password@example.com/"
    with pytest.raises(ApplicationError):
        request_view(request, PublicOutput({}))


def test_full_positional_diff_every_field():
    from techlens.demo import CannedPlanner
    p = CannedPlanner()
    request = demo_request("comparison")
    original = p.finalize(p.generate(p.prepare(request)).data, request)
    changed = original.model_copy(update={"objective": "changed", "web_tasks": list(reversed(original.web_tasks))})
    result = plan_changes(original, changed)
    assert [x["field"] for x in result] == list(original.model_dump())
    assert {x["field"] for x in result if x["changed"]} == {"objective", "web_tasks"}
