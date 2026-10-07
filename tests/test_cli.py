import io
import json

import pytest

from techlens.cli import main
from techlens.configuration import ApplicationError, parse_model, load_json
from techlens.demo import demo_request, demo_rates
from techlens.models import AssessmentRequest


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("args", [["--help"], ["--version"], ["demo", "--help"]])
def test_help_and_version_use_injected_public_output(args):
    out = io.StringIO()
    assert main(args, stdout=out, environ={}) == 0
    assert "techlens" in out.getvalue()


def test_doctor_is_local_and_secret_free():
    out = io.StringIO()
    code = main(["doctor"], stdout=out, environ={"OPENAI_API_KEY": "SECRET-SENTINEL"},
                factory=lambda *args: pytest.fail("Doctor constructed clients"))
    assert code == 0
    assert "SECRET-SENTINEL" not in out.getvalue()
    assert "OPENAI_API_KEY: present" in out.getvalue()
    assert "NOT VERIFIED" in out.getvalue()


@pytest.mark.parametrize("args", [["--bad=SECRET-SENTINEL"], ["demo", "--scenario", "SECRET-SENTINEL"],
    ["--help", "SECRET-SENTINEL"]])
def test_argument_errors_do_not_echo_secrets(args):
    out = io.StringIO()
    assert main(args, stdout=out, environ={"OPENAI_API_KEY": "SECRET-SENTINEL"}) in {2, 5}
    assert "SECRET-SENTINEL" not in out.getvalue()


@pytest.mark.parametrize("location", ["root", "candidate", "team", "constraints"])
def test_nested_unknown_fields_rejected_without_changing_models(location):
    value = demo_request("single").model_dump(mode="json")
    target = {"root": value, "candidate": value["candidates"][0],
              "team": value["team_profile"], "constraints": value["constraints"]}[location]
    target["credential"] = "SECRET-SENTINEL"
    with pytest.raises(ApplicationError) as error:
        parse_model(value, AssessmentRequest)
    assert "SECRET-SENTINEL" not in str(error.value)
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert AssessmentRequest.model_validate(value)  # Domain policy remains unchanged.


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"x":NaN}', '{broken', '[' * 2000])
def test_json_input_boundary(tmp_path, raw):
    path = tmp_path / "input.json"
    path.write_text(raw)
    with pytest.raises(ApplicationError) as error:
        load_json(path)
    assert str(path) not in str(error.value)
    assert error.value.__context__ is None


@pytest.mark.parametrize("scenario,action,expected", [("single", "approve", 0),
    ("comparison", "approve", 0), ("partial", "approve", 4), ("single", "reject", 3)])
def test_cli_demo_export(tmp_path, scenario, action, expected, monkeypatch):
    import techlens.openai_planner as planner
    monkeypatch.setattr(planner, "OpenAIResearchPlanner", lambda **_: pytest.fail("Live fallback"))
    out, dest = io.StringIO(), tmp_path / "bundle"
    code = main(["demo", "--scenario", scenario, "--output-dir", str(dest)],
        stdin=io.StringIO(action + "\n"), stdout=out,
        environ={"OPENAI_API_KEY": "FAKE-SENTINEL", "TAVILY_API_KEY": "FAKE-SENTINEL"})
    assert code == expected
    assert "SYNTHETIC DEMO" in out.getvalue()
    envelope = json.loads((dest / "report.json").read_text())
    assert envelope["provenance"]["synthetic"] is True
    assert (dest / "manifest.json").exists()
    assert "FAKE-SENTINEL" not in out.getvalue()


def test_eof_after_planning_never_approves():
    out = io.StringIO()
    assert main(["demo"], stdin=io.StringIO(""), stdout=out, environ={}) == 130
    assert "settled estimate" in out.getvalue()
    assert "no terminal report asserted" in out.getvalue()
    assert "## 1." not in out.getvalue()


@pytest.mark.parametrize("defect", ["no_token", "no_count", "zero_untrusted", "wrong_model", "no_search",
                                      "output_bound", "endpoint", "noninteractive", "zero_budget", "destination"])
def test_live_blocked_before_factory(tmp_path, defect):
    request, rates = demo_request("single").model_dump(mode="json"), demo_rates().model_dump(mode="json")
    env = {"OPENAI_API_KEY": "FAKE-KEY", "TAVILY_API_KEY": "FAKE-KEY", "OPENAI_MODEL": "synthetic-local"}
    if defect == "no_token":
        env.pop("OPENAI_API_KEY")
    elif defect == "no_count":
        rates.update(count_upper_fee=None, count_fixed_fee=None)
    elif defect == "zero_untrusted":
        rates.update(count_upper_fee="0", count_fixed_fee="0")
    elif defect == "wrong_model":
        env["OPENAI_MODEL"] = "different"
    elif defect == "no_search":
        rates["search_source_reference"] = None
    elif defect == "output_bound":
        rates["max_output_tokens"] = 10
    elif defect == "endpoint":
        env["OPENAI_BASE_URL"] = "https://user:SECRET@evil.invalid"
    elif defect == "zero_budget":
        request["constraints"]["max_run_cost_usd"] = "0"
    rp, cp = tmp_path / "request.json", tmp_path / "rates.json"
    rp.write_text(json.dumps(request))
    cp.write_text(json.dumps(rates))
    args = ["assess", "--request", str(rp), "--rate-card", str(cp)]
    if defect == "destination":
        args += ["--output-dir", str(tmp_path)]
    out = Terminal()
    code = main(args, stdin=io.StringIO() if defect == "noninteractive" else Terminal(), stdout=out,
        environ=env, factory=lambda *a: pytest.fail("Constructed clients before preconditions"))
    assert code == (6 if defect == "destination" else 2)
    assert "FAKE-KEY" not in out.getvalue() and "SECRET" not in out.getvalue()


@pytest.mark.parametrize("action,code", [("approve\n", 0), ("reject\n", 3), ("", 130)])
def test_live_application_closes_owned_resources_with_offline_factory(tmp_path, action, code):
    from techlens.application import demo_dependencies
    rp, cp = tmp_path / "request.json", tmp_path / "rates.json"
    rp.write_text(demo_request("single").model_dump_json())
    cp.write_text(demo_rates().model_dump_json())
    closed = []

    def factory(stack, *_):
        stack.callback(closed.append, True)
        return demo_dependencies("single")

    assert main(["assess", "--request", str(rp), "--rate-card", str(cp)], stdin=Terminal(action),
        stdout=Terminal(), environ={"OPENAI_API_KEY": "FAKE", "TAVILY_API_KEY": "FAKE",
                                   "OPENAI_MODEL": "synthetic-local"}, factory=factory) == code
    assert closed == [True]


@pytest.mark.parametrize("failure,code", [(KeyboardInterrupt, 130), (RuntimeError, 6)])
def test_provider_interruption_and_failure_close_and_do_not_retry(tmp_path, failure, code):
    from techlens.application import demo_dependencies
    rp, cp = tmp_path / "request.json", tmp_path / "rates.json"
    rp.write_text(demo_request("single").model_dump_json())
    cp.write_text(demo_rates().model_dump_json())
    calls, closed = [], []

    def factory(stack, *_):
        stack.callback(closed.append, True)
        deps = demo_dependencies("single")

        def fail(shared):
            calls.append(True)
            raise failure("PRIVATE-PROVIDER-OBJECT")

        deps.planner.count = fail
        return deps

    out = Terminal()
    assert main(["assess", "--request", str(rp), "--rate-card", str(cp)], stdin=Terminal("approve\n"),
        stdout=out, environ={"OPENAI_API_KEY": "FAKE", "TAVILY_API_KEY": "FAKE",
                             "OPENAI_MODEL": "synthetic-local"}, factory=factory) == code
    assert closed == calls == [True]
    assert "PRIVATE-PROVIDER-OBJECT" not in out.getvalue()
    assert "## 1." not in out.getvalue()


def test_all_examples_match_existing_models_and_rates_template_is_not_executable():
    from pathlib import Path
    from techlens.budget import RateCard
    root = Path(__file__).resolve().parents[1] / "examples"
    for path in root.glob("request-*.json"):
        assert parse_model(load_json(path), AssessmentRequest)
    with pytest.raises(ApplicationError):
        parse_model(load_json(root / "rate-card.template.json"), RateCard)
