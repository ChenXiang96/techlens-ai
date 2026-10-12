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


class AcceptingTerminal(Terminal):
    """Respond to the actual application-generated run ID, then review normally."""
    def __init__(self, output, action):
        super().__init__(action)
        self.output, self.accepted = output, False

    def readline(self, *args):
        if not self.accepted:
            self.accepted = True
            prompt = self.output.getvalue().split("Type AUTHORIZE LIVE ")[-1].split(" to continue:")[0]
            return f"AUTHORIZE LIVE {prompt}\n"
        return super().readline(*args)


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
    args = ["assess", "--live", "--request", str(rp), "--rate-card", str(cp)]
    if defect == "destination":
        args += ["--output-dir", str(tmp_path)]
    out = Terminal()
    code = main(args, stdin=io.StringIO() if defect == "noninteractive" else Terminal(), stdout=out,
        environ=env, factory=lambda *a: pytest.fail("Constructed clients before preconditions"))
    assert code == (7 if defect == "noninteractive" else 6 if defect == "destination" else 2)
    assert "FAKE-KEY" not in out.getvalue() and "SECRET" not in out.getvalue()


@pytest.mark.parametrize("action,code", [("approve\n", 0), ("reject\n", 3), ("", 130)])
def test_live_application_closes_owned_resources_with_offline_factory(tmp_path, action, code):
    from techlens.application import demo_dependencies
    rp, cp = tmp_path / "request.json", tmp_path / "rates.json"
    rp.write_text(demo_request("single").model_dump_json())
    cp.write_text(demo_rates().model_dump_json())
    closed = []

    def factory(stack, *_, **kwargs):
        stack.callback(closed.append, True)
        return demo_dependencies("single")

    out = Terminal()
    assert main(["assess", "--live", "--request", str(rp), "--rate-card", str(cp)], stdin=AcceptingTerminal(out, action),
        stdout=out, environ={"OPENAI_API_KEY": "FAKE", "TAVILY_API_KEY": "FAKE",
                                   "OPENAI_MODEL": "synthetic-local"}, factory=factory) == code
    assert closed == [True]


@pytest.mark.parametrize("failure,code", [(KeyboardInterrupt, 130), (RuntimeError, 6)])
def test_provider_interruption_and_failure_close_and_do_not_retry(tmp_path, failure, code):
    from techlens.application import demo_dependencies
    rp, cp = tmp_path / "request.json", tmp_path / "rates.json"
    rp.write_text(demo_request("single").model_dump_json())
    cp.write_text(demo_rates().model_dump_json())
    calls, closed = [], []

    def factory(stack, *_, **kwargs):
        stack.callback(closed.append, True)
        deps = demo_dependencies("single")

        def fail(shared):
            calls.append(True)
            raise failure("PRIVATE-PROVIDER-OBJECT")

        deps.planner.count = fail
        return deps

    out = Terminal()
    assert main(["assess", "--live", "--request", str(rp), "--rate-card", str(cp)], stdin=AcceptingTerminal(out, "approve\n"),
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


@pytest.mark.parametrize("status,scenario,review,expected", [
    ("completed", "single", "approve", 0), ("partial", "partial", "approve", 4),
    ("rejected", "single", "reject", 3), ("failed", "single", "reject", 5)])
@pytest.mark.parametrize("viewer", ["done", "", "eof", "interrupt", "full-eof"])
def test_completed_viewer_phase_status_exports_and_zero_new_calls(tmp_path, monkeypatch, status, scenario, review, expected, viewer):
    import hashlib
    import techlens.cli as cli
    from techlens.application import demo_dependencies
    from techlens.reporting import assemble_report
    from techlens.budget import BudgetLedger
    from unittest.mock import Mock
    dependencies, sessions, baseline = demo_dependencies(scenario), [], []
    monkeypatch.setattr(cli, "demo_dependencies", lambda _: dependencies)
    original_session = cli.AssessmentSession
    def construct(*args, **kwargs):
        session = original_session(*args, **kwargs)
        session.start = Mock(wraps=session.start)
        session.resume = Mock(wraps=session.resume)
        sessions.append(session)
        return session
    monkeypatch.setattr(cli, "AssessmentSession", construct)
    if status == "failed":
        # Coherent integrity-failure state; the existing production validator still runs.
        def failed_review(session, *args, **kwargs):
            state = {"request": session.request, "fatal_failure": "collection_integrity", "current_round": 0,
                     "errors": ["collection_integrity"],
                     "budget_ledger": BudgetLedger(run_id="failed", ceiling_usd="0.15")}
            state["report"] = assemble_report(state)
            session.state = state
            return state
        monkeypatch.setattr(cli, "run_review", failed_review)
    destination, out = tmp_path / "bundle", Terminal()
    def observations():
        session = sessions[0]
        return (session.start.call_count, session.resume.call_count,
            dependencies.planner.count_calls, dependencies.planner.generation_calls,
            dependencies.analyzer.count_calls, dependencies.analyzer.generation_calls,
            dependencies.search.calls, list(dependencies.github.calls), session.state["report"].model_dump(mode="json"))
    class Input(Terminal):
        views = 0
        def readline(self, *args):
            if "Report [full / done; Enter=done]: " not in out.getvalue():
                return review + "\n"
            assert "Export complete: report.json, report.md, manifest.json." in out.getvalue()
            manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["complete"]
            data = {p.name: p.read_bytes() for p in destination.iterdir()}
            for name, value in manifest["files"].items():
                assert hashlib.sha256(data[name]).hexdigest() == value
            current = (data, observations())
            if not baseline:
                baseline.append(current)
            else:
                assert current == baseline[0]
            self.views += 1
            if viewer == "interrupt":
                raise KeyboardInterrupt()
            if viewer == "full-eof" and self.views == 1:
                return "full\n"
            return "" if viewer in {"eof", "full-eof"} else viewer + "\n"
    code = cli.main(["demo", "--scenario", scenario, "--output-dir", str(destination)],
                    stdin=Input(), stdout=out, environ={})
    assert code == (130 if viewer == "interrupt" else expected)
    assert f"Status: {status}" in out.getvalue()
    assert ({p.name: p.read_bytes() for p in destination.iterdir()}, observations()) == baseline[0]
    if viewer == "interrupt":
        assert "Report viewing cancelled" in out.getvalue()
    assert "no terminal report asserted" not in out.getvalue()
    if viewer == "full-eof":
        assert "## 13. Known Uncertainties" in out.getvalue()
    if status != "failed":
        assert sessions[0].start.call_count == sessions[0].resume.call_count == 1


def test_publication_failure_and_display_failure_do_not_enter_viewer_or_replay(tmp_path, monkeypatch):
    import techlens.cli as cli
    from techlens.application import demo_dependencies
    calls = []
    dependencies = demo_dependencies("single")
    monkeypatch.setattr(cli, "demo_dependencies", lambda _: dependencies)
    def fail_publish(*args):
        calls.append("publish")
        raise ApplicationError("Export failed; no assessment was repeated.", 6)
    monkeypatch.setattr(cli.ExportDestination, "publish", fail_publish)
    out = Terminal()
    assert cli.main(["demo", "--output-dir", str(tmp_path / "bundle")],
        stdin=Terminal("approve\nfull\n"), stdout=out, environ={}) == 6
    assert calls == ["publish"]
    assert "Report [full" not in out.getvalue() and "Export complete" not in out.getvalue()
    assert dependencies.planner.generation_calls == dependencies.analyzer.generation_calls == 1
    assert dependencies.search.calls == 1
    before = len(dependencies.github.calls)
    def fail_display(*args, **kwargs):
        raise ApplicationError("Public display blocked.", 5)
    monkeypatch.setattr(cli, "render_report_summary", fail_display)
    out = Terminal()
    assert cli.main(["demo"], stdin=Terminal("approve\nfull\n"), stdout=out, environ={}) == 5
    assert "Report [full" not in out.getvalue()
    assert dependencies.planner.generation_calls == dependencies.analyzer.generation_calls == 2
    assert len(dependencies.github.calls) == before + 1


def test_full_report_flag_preserves_full_export_and_has_no_optional_viewer():
    out = Terminal()
    assert main(["demo", "--report-view", "full"], stdin=Terminal("approve\n"), stdout=out, environ={}) == 0
    assert "## 13. Known Uncertainties" in out.getvalue() and "Report [full" not in out.getvalue()


def test_report_viewer_invalid_action_is_local_and_preserves_partial_code():
    out = Terminal()
    assert main(["demo", "--scenario", "partial"], stdin=Terminal("approve\nwrong\nfull\ndone\n"), stdout=out, environ={}) == 4
    assert "Enter full or done" in out.getvalue() and "Status: partial" in out.getvalue()


@pytest.fixture
def boundary_review(monkeypatch, tmp_path):
    """Real synthetic graph, outstanding count fee, and pre-review checkpoints."""
    from copy import deepcopy
    from unittest.mock import Mock
    import techlens.cli as cli
    from techlens.application import demo_dependencies

    deps = demo_dependencies("single")
    deps.rates = deps.rates.model_copy(update={"count_fixed_fee": None})
    monkeypatch.setattr(cli, "demo_dependencies", lambda _: deps)
    original = cli.AssessmentSession
    sessions, checkpoints = [], []
    proposal = tmp_path / "proposal.json"

    def construct(*args, **kwargs):
        session = original(*args, **kwargs)
        start = session.start
        def initial():
            state = start()
            checkpoints.append(deepcopy(list(session.saver.list(session.config))))
            data = state["research_plan"].model_dump(mode="json")
            data["objective"] = "Unsubmitted local modification"
            proposal.write_text(json.dumps(data), encoding="utf-8")
            return state
        session.start = Mock(side_effect=initial)
        session.resume = Mock(wraps=session.resume)
        sessions.append(session)
        return session
    monkeypatch.setattr(cli, "AssessmentSession", construct)

    def run(payload):
        class Input(Terminal):
            sizes = []
            def readline(self, size=-1):
                self.sizes.append(size)
                return super().readline(size)
        stream, out = Input(payload), Terminal()
        code = cli.main(["demo"], stdin=stream, stdout=out, environ={})
        return code, out.getvalue(), stream

    def assert_pending():
        session = sessions[0]
        assert session.start.call_count == 1 and session.resume.call_count == 0
        assert deps.planner.count_calls == deps.planner.generation_calls == 1
        assert deps.search.calls == deps.analyzer.count_calls == deps.analyzer.generation_calls == 0
        assert deps.github.calls == []
        assert list(session.saver.list(session.config)) == checkpoints[0]
        values = session.graph.get_state(session.config).values
        assert values["plan_status"] == "pending" and values["current_round"] == 0
        assert not values.get("report")
        ledger = values["budget_ledger"]
        assert len(ledger.attempts) == 2 and ledger.outstanding > 0
        assert any(a.state == "billing_unknown" for a in ledger.attempts)
    return run, assert_pending, proposal


@pytest.mark.parametrize("action", ["save-plan", "modify"])
@pytest.mark.parametrize("suffix", ["approve", "reject", "save-plan", "modify"])
def test_overlong_review_path_suffix_is_fatal_and_preserves_liability(boundary_review, action, suffix):
    run, assert_pending, _ = boundary_review
    # Exact Master reproductions included: the suffix is not a new physical line.
    code, text, stream = run(action + "\n" + "x" * 4099 + suffix + "\n")
    assert code == 2
    assert text.count("Input line exceeds the local input bound; operation cancelled.") == 1
    assert "x" * 20 not in text and "Status:" not in text
    assert stream.sizes == [4099, 4099]
    assert stream.read() == suffix + "\n"  # Remains unread; no drain or reinterpretation.
    assert_pending()


@pytest.mark.parametrize("suffix", ["APPLY", "approve", "reject", "save-plan", "modify", "CANCEL"])
def test_overlong_apply_confirmation_suffix_never_resumes(boundary_review, suffix):
    run, assert_pending, proposal = boundary_review
    code, text, stream = run(f"modify\n{proposal}\n" + "x" * 4099 + suffix + "\n")
    assert code == 2 and "PROPOSED PLAN CHANGES" in text
    assert text.count("Input line exceeds") == 1 and "x" * 20 not in text
    assert stream.sizes == [4099, 4099, 4099]
    assert stream.read() == suffix + "\n"
    assert_pending()


@pytest.mark.parametrize("terminator", ["\n", "\r\n", ""])
@pytest.mark.parametrize("size,expected", [(4096, 0), (4097, 2)])
def test_physical_input_exact_bound_overflow_and_final_line(boundary_review, terminator, size, expected):
    run, assert_pending, _ = boundary_review
    code, text, stream = run("approve" + " " * (size - 7) + terminator)
    assert code == expected and stream.sizes == ([4099] if expected == 2 else [4099, 4099])
    if expected == 2:
        assert "Input line exceeds" in text
        assert_pending()
    else:
        assert "Status: completed" in text


def test_ordinary_recoverable_review_errors_remain_pending(boundary_review):
    run, assert_pending, _ = boundary_review
    code, text, _ = run("wrong\nmodify\nmissing-plan.json\nview\nhelp\n")
    assert code == 130
    assert "Invalid action; research remains unapproved" in text
    assert "Input line exceeds" not in text
    assert_pending()


def test_overlong_live_consent_constructs_no_session_or_factory(tmp_path, monkeypatch):
    from unittest.mock import Mock
    import techlens.cli as cli
    request, rates = tmp_path / "request.json", tmp_path / "rates.json"
    request.write_text(demo_request("single").model_dump_json(), encoding="utf-8")
    rates.write_text(demo_rates().model_dump_json(), encoding="utf-8")
    session, factory = Mock(side_effect=AssertionError()), Mock(side_effect=AssertionError())
    monkeypatch.setattr(cli, "AssessmentSession", session)
    out, stream = Terminal(), Terminal("x" * 4099 + "AUTHORIZE LIVE forged\n")
    assert cli.main(["assess", "--live", "--request", str(request), "--rate-card", str(rates)],
        stdin=stream, stdout=out, factory=factory,
        environ={"OPENAI_API_KEY": "FAKE", "TAVILY_API_KEY": "FAKE", "OPENAI_MODEL": "synthetic-local"}) == 2
    assert session.call_count == factory.call_count == 0
    assert "Input line exceeds" in out.getvalue() and "x" * 20 not in out.getvalue()
    assert stream.read() == "AUTHORIZE LIVE forged\n"


@pytest.mark.parametrize("scenario,review", [("single", "approve"), ("partial", "approve"), ("single", "reject")])
def test_late_viewer_input_bound_preserves_report_export_and_execution(tmp_path, monkeypatch, scenario, review):
    from copy import deepcopy
    from unittest.mock import Mock
    import hashlib
    import techlens.cli as cli
    from techlens.application import demo_dependencies
    deps, captured = demo_dependencies(scenario), []
    monkeypatch.setattr(cli, "demo_dependencies", lambda _: deps)
    original = cli.AssessmentSession
    def construct(*args, **kwargs):
        session = original(*args, **kwargs)
        session.resume = Mock(wraps=session.resume)
        session.interruption_summary = Mock(side_effect=AssertionError("Unfinished diagnostic invoked"))
        captured.append(session)
        return session
    monkeypatch.setattr(cli, "AssessmentSession", construct)
    out, destination, baseline = Terminal(), tmp_path / "bundle", []
    def observations():
        return (deps.planner.count_calls, deps.planner.generation_calls,
                deps.analyzer.count_calls, deps.analyzer.generation_calls,
                deps.search.calls, list(deps.github.calls),
                deepcopy(list(captured[0].saver.list(captured[0].config))))
    class Input(Terminal):
        def readline(self, size=-1):
            if "Report [full / done" in out.getvalue():
                assert "Export complete" in out.getvalue()
                data = {p.name: p.read_bytes() for p in destination.iterdir()}
                manifest = json.loads(data["manifest.json"])
                assert manifest["complete"]
                assert all(hashlib.sha256(data[name]).hexdigest() == digest
                           for name, digest in manifest["files"].items())
                baseline.append((data, observations()))
            return super().readline(size)
    stream = Input(review + "\n" + "x" * 4099 + "full\n")
    assert cli.main(["demo", "--scenario", scenario, "--output-dir", str(destination)],
                    stdin=stream, stdout=out, environ={}) == 2
    assert len(baseline) == 1
    assert ({p.name: p.read_bytes() for p in destination.iterdir()}, observations()) == baseline[0]
    assert stream.read() == "full\n"
    assert captured[0].resume.call_count == 1 and captured[0].interruption_summary.call_count == 0
    assert "terminal assessment result remains available" in out.getvalue()
    assert "no terminal report asserted" not in out.getvalue() and "x" * 20 not in out.getvalue()
