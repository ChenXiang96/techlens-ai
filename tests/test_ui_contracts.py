from copy import deepcopy
import pytest

from techlens.demo import CannedPlanner, demo_request
from techlens.ui_contracts import PlanEdit, RequestForm, UIError, decode_json, parse


def form(count=1):
    return {"candidates": [{"name": "Duplicate", "repository_url": f"https://github.com/example/repo-{n}"}
                           for n in range(count)]}


@pytest.mark.parametrize("count", [1, 2, 3])
def test_request_count_duplicate_names_order_defaults(count):
    value = parse(RequestForm, form(count)).request()
    assert len(value.candidates) == count
    assert [c.name for c in value.candidates] == ["Duplicate"] * count
    assert value.constraints.max_run_cost_usd == 0
    assert value.constraints.max_candidates == count
    assert value.team_profile.primary_languages == []
    assert value.team_profile.ai_experience is None


def test_exact_decimal_lists_and_context():
    value = parse(RequestForm, {**form(), "max_run_cost_usd": "1.0000000000000000000000000001",
        "primary_languages": [" Python ", "Rust,Go"], "team_size": "12", "ai_experience": " learning "}).request()
    assert str(value.constraints.max_run_cost_usd) == "1.0000000000000000000000000001"
    assert value.team_profile.primary_languages == ["Python", "Rust,Go"]
    assert value.team_profile.team_size == 12
    assert value.team_profile.ai_experience == "learning"


def test_skipped_whitespace_context_and_guided_decimal_semantics():
    supplied = form()
    supplied["candidates"][0]["homepage"] = " "
    value = parse(RequestForm, {**supplied, "max_run_cost_usd": " ", "ai_experience": " ",
                               "deployment_environment": "\t", "team_size": " "}).request()
    assert value.constraints.max_run_cost_usd == 0
    assert value.team_profile.ai_experience is None
    assert value.team_profile.deployment_environment is None
    assert value.candidates[0].homepage is None
    assert parse(RequestForm, {**form(), "max_run_cost_usd": "000.10"}).request().constraints.max_run_cost_usd == __import__("decimal").Decimal("0.10")


@pytest.mark.parametrize("extra", [{"max_run_cost_usd": "NaN"}, {"max_run_cost_usd": "1e2"},
    {"max_run_cost_usd": "-1"}, {"team_size": "0"}, {"max_web_searches": "9"},
    {"primary_languages": [" "]}, {"primary_languages": ["x"] * 21}, {"api_key": "sentinel"},
    {"live": True}, {"max_candidates": 3}])
def test_invalid_form(extra):
    with pytest.raises(UIError):
        parse(RequestForm, {**form(), **extra}).request()


@pytest.mark.parametrize("url", ["https://u:p@github.com/e/r", "https://gitlab.com/e/r", "javascript:alert(1)",
                                "https://github.com/e/r/issues/1"])
def test_invalid_repository(url):
    with pytest.raises(UIError):
        parse(RequestForm, {"candidates": [{"name": "x", "repository_url": url}]}).request()


def test_plan_edit_reconstructs_authoritative_fields():
    request = demo_request("comparison")
    planner = CannedPlanner()
    original = planner.finalize(planner.generate(planner.prepare(request)).data, request)
    value = original.model_dump(mode="json")
    editable = {key: value[key] for key in ("objective", "research_questions", "engineering_checks", "web_tasks")}
    for task in editable["web_tasks"]:
        del task["task_id"]
    editable["web_tasks"].reverse()
    modified = parse(PlanEdit, editable).plan(request)
    assert [t.task_id for t in modified.web_tasks] == ["web-001", "web-002"]
    assert modified.comparison_targets == ["candidate-001", "candidate-002"]
    assert modified.max_run_cost_usd == original.max_run_cost_usd
    for field in ("comparison_targets", "max_searches", "repository_url"):
        with pytest.raises(UIError):
            parse(PlanEdit, {**editable, field: "untrusted"})
    invented = deepcopy(editable)
    invented["web_tasks"][0]["candidate_id"] = "candidate-999"
    with pytest.raises(UIError):
        parse(PlanEdit, invented).plan(request)


@pytest.mark.parametrize("body", [b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'\xff', b'{}suffix',
                                   b'{"x":' + b'[' * 33 + b'0' + b']' * 33 + b'}'])
def test_json_complete_bounded(body):
    with pytest.raises(UIError) as caught:
        decode_json(body)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_json_hostile_text_is_data():
    assert decode_json(b'{"x":"<script>fetch(\\\"https://bad.invalid\\\")</script>"}')["x"].startswith("<script>")
