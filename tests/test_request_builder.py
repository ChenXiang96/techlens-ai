"""Guided request contract and provider-free publication evidence."""
from decimal import Decimal
import io
import json
from unittest.mock import Mock

import pytest

from techlens.cli import main
from techlens.configuration import ApplicationError, load_json, parse_model, live_configuration
from techlens.models import AssessmentRequest, ResearchConstraints
from techlens.presentation import PublicOutput, render_request_summary
from techlens.request_builder import build_request
from test_cli import Terminal
from fakes import fictional_rates


def replies(count=1, *, budget="", confirmation="SAVE", assessment_id="guided-request"):
    data = [assessment_id, str(count)]
    for index in range(count):
        data.extend(["Same name", f"https://github.com/example/repository-{index + 1}", ""])
    data.extend([""] * 8)  # team size, two lists, FOUR strings, priorities
    data.extend(["", "", budget, confirmation])
    return data


def build(values):
    output = []
    values = iter(values)
    request = build_request(lambda _: next(values), output.append, PublicOutput({}))
    return request, "".join(output)


@pytest.mark.parametrize("count", [1, 2, 3])
def test_guided_request_exact_fields_defaults_order_and_roundtrip(count, tmp_path):
    path, out = tmp_path / "request.json", Terminal()
    assert main(["request", "create", "--output", str(path)],
                stdin=Terminal("\n".join(replies(count)) + "\n"), stdout=out, environ={}) == 0
    request = parse_model(load_json(path), AssessmentRequest)
    assert request.assessment_id == "guided-request"
    assert [c.name for c in request.candidates] == ["Same name"] * count
    assert [str(c.repository_url) for c in request.candidates] == [
        f"https://github.com/example/repository-{index + 1}" for index in range(count)]
    assert all(c.homepage is None for c in request.candidates)
    assert request.team_profile.model_dump() == {
        "team_size": None, "primary_languages": [], "backend_stack": [], "ai_experience": None,
        "deployment_environment": None, "production_criticality": None, "framework_churn_tolerance": None}
    assert request.priorities == []
    assert request.constraints.model_dump() == {"max_candidates": count, "max_web_searches": 2,
        "max_research_rounds": 1, "max_run_cost_usd": Decimal(0)}
    assert ResearchConstraints().max_run_cost_usd == Decimal("0.15")
    assert "AI experience" in out.getvalue() and "Framework churn tolerance" in out.getvalue()
    assert "No assessment was started" in out.getvalue()
    assert set(tmp_path.iterdir()) == {path}


def test_all_supplied_context_lists_trimmed_without_inference_or_comma_splitting():
    values = ["", "1", "  Candidate  ", "https://github.com/example/project.git/", "https://example.com/docs",
              "7", " Python ", "Go, Rust", "", " FastAPI ", "", " novice ", " local ", " low ", " stable ",
              " simplicity ", " simplicity ", "", "3", "2", "0.0123", "save", "SAVE"]
    request, output = build(values)
    assert request.assessment_id.startswith("assessment-") and len(request.assessment_id) == 43
    assert request.team_profile.model_dump() == {"team_size": 7, "primary_languages": ["Python", "Go, Rust"],
        "backend_stack": ["FastAPI"], "ai_experience": "novice", "deployment_environment": "local",
        "production_criticality": "low", "framework_churn_tolerance": "stable"}
    assert request.priorities == ["simplicity", "simplicity"]
    assert request.constraints.max_run_cost_usd == Decimal("0.0123")
    assert request.constraints.max_research_rounds == 2
    assert "at most one research round" in output and "Enter exactly SAVE or CANCEL" in output


@pytest.mark.parametrize("bad", ["", "   ", "NAN", "-1", "0", "4", "1.5", "inf", "9999999999"])
def test_invalid_candidate_counts_receive_fixed_guidance(bad):
    if not bad.strip():  # Blank intentionally selects one, rather than becoming an invalid list item.
        request, _ = build(["id", bad, *replies()[2:]])
        assert len(request.candidates) == 1
    else:
        request, output = build(["id", bad, *replies()[1:]])
        assert len(request.candidates) == 1
        assert "Enter a candidate count from 1 to 3." in output
        assert bad not in output if bad == "9999999999" else True


@pytest.mark.parametrize("bad", ["https://gitlab.com/a/b", "not-a-url", "https://github.com/a/b/issues/1",
                                 "https://u:private@example.com/a/b", "https://github.com/", "javascript:x"])
def test_invalid_repository_is_not_echoed(bad):
    values = replies()
    values.insert(3, bad)
    request, output = build(values)
    assert str(request.candidates[0].repository_url) == "https://github.com/example/repository-1"
    assert "Enter an HTTPS GitHub repository-root URL" in output and json.dumps(bad) not in output


@pytest.mark.parametrize("bad", ["-1", "NaN", "Infinity", "1e2", "$1", "oops", "9" * 33])
def test_invalid_money_does_not_become_an_allowance(bad):
    values = replies()
    values.insert(len(values) - 2, bad)
    request, output = build(values)
    assert request.constraints.max_run_cost_usd == 0
    assert "Enter a finite, non-negative USD decimal" in output and json.dumps(bad) not in output


def test_invalid_blank_name_team_integer_homepage_and_field_bounds():
    values = replies()
    values[2:2] = ["   ", "x" * 257]
    # Inject invalid homepage before the valid skip, then invalid optional team size.
    values[6:6] = ["https://user:password@example.com", "javascript:x"]
    values[9:9] = ["-1", "1.5"]
    request, output = build(values)
    assert request.candidates[0].name == "Same name" and request.candidates[0].homepage is None
    assert request.team_profile.team_size is None
    assert "Candidate name is required" in output
    assert "credential-free HTTP/HTTPS homepage" in output
    assert "Enter a positive whole number" in output
    assert "password" not in output and "x" * 257 not in output


def test_guided_lists_bound_and_independent_defaults():
    values = replies()
    values[6:6] = [str(i) for i in range(21)]
    first, output = build(values)
    second, _ = build(replies())
    assert first.team_profile.primary_languages == [str(i) for i in range(20)]
    assert "at most 20 items" in output
    first.team_profile.backend_stack.append("change")
    assert second.team_profile.backend_stack == second.priorities == []


@pytest.mark.parametrize("budget", ["", "1.25"])
def test_builder_no_clients_authorization_session_or_transport(tmp_path, monkeypatch, budget):
    import techlens.cli as cli
    import httpx
    import openai
    spies = []
    for owner, name in ((cli, "AssessmentSession"), (cli, "authorize_live_run"),
                        (cli, "live_dependencies"), (httpx, "Client"), (openai, "OpenAI")):
        spy = Mock(side_effect=AssertionError("Forbidden construction"))
        monkeypatch.setattr(owner, name, spy)
        spies.append(spy)
    for name in ("request", "send"):
        spy = Mock(side_effect=AssertionError("Forbidden transport"))
        monkeypatch.setattr(httpx.Client, name, spy, raising=False)
        spies.append(spy)
    out, path = Terminal(), tmp_path / "new.json"
    assert cli.main(["request", "create", "--output", str(path)],
        stdin=Terminal("\n".join(replies(budget=budget)) + "\n"), stdout=out,
        environ={"OPENAI_API_KEY": "SECRET-FAKE-OPENAI", "TAVILY_API_KEY": "SECRET-FAKE-TAVILY",
                 "GITHUB_TOKEN": "SECRET-FAKE-GITHUB"}, factory=Mock(side_effect=AssertionError())) == 0
    assert all(s.call_count == 0 for s in spies)
    assert "SECRET-FAKE" not in out.getvalue() + path.read_text()


@pytest.mark.parametrize("point", [0, 3, 8, 15, 16])
@pytest.mark.parametrize("failure", [EOFError, KeyboardInterrupt])
def test_cancel_at_each_builder_phase_publishes_nothing(tmp_path, point, failure):
    class Interrupted(Terminal):
        count = 0
        def readline(self, *args):
            if self.count == point:
                raise failure()
            self.count += 1
            return super().readline(*args)
    path, out = tmp_path / "request.json", Terminal()
    assert main(["request", "create", "--output", str(path)],
        stdin=Interrupted("\n".join(replies()) + "\n"), stdout=out, environ={}) == 130
    assert not path.exists() and list(tmp_path.iterdir()) == []
    assert "No assessment was started" in out.getvalue()


def test_cancel_confirmation_noninteractive_and_overlong_line(tmp_path):
    path = tmp_path / "new.json"
    for stdin, stdout, code in ((Terminal("\n".join(replies(confirmation="CANCEL")) + "\n"), Terminal(), 130),
                               (io.StringIO("SAVE\n"), Terminal(), 2),
                               (Terminal("x" * 4097 + "\nSAVE\n"), Terminal(), 2)):
        assert main(["request", "create", "--output", str(path)], stdin=stdin, stdout=stdout, environ={}) == code
        assert not path.exists()


def test_zero_budget_blocks_existing_live_configuration_and_admission():
    from techlens.budget import BudgetLedger, reserve
    request, _ = build(replies())
    rates = fictional_rates()
    with pytest.raises(ApplicationError, match="ceiling"):
        live_configuration(request, rates, {"OPENAI_API_KEY": "FAKE", "TAVILY_API_KEY": "FAKE"})
    admission = reserve(BudgetLedger(run_id="local", ceiling_usd=0, rates=rates), "planner:count", "count", {})
    assert admission.denial and admission.attempt is None


def test_hostile_request_text_is_data_and_configured_secret_blocks_save(tmp_path):
    values = replies()
    values[2] = 'Name\x1b[31m\u202e[click](javascript:x)<script>'
    request, output = build(values)
    assert request.candidates[0].name == values[2]
    assert "\x1b" not in output and "\u202e" not in output
    summary = render_request_summary(request, PublicOutput({}))
    assert json.loads(summary.split("candidate-001: ")[1].splitlines()[0]) == values[2]
    values = replies()
    values[2] = "SECRET-FAKE"
    path, out = tmp_path / "blocked.json", Terminal()
    assert main(["request", "create", "--output", str(path)],
        stdin=Terminal("\n".join(values) + "\n"), stdout=out, environ={"GITHUB_TOKEN": "SECRET-FAKE"}) == 5
    assert "SECRET-FAKE" not in out.getvalue() and not path.exists()


@pytest.mark.parametrize("point", [0, 2, 16])
@pytest.mark.parametrize("suffix", ["SAVE", "approve", "save-plan"])
def test_overlong_builder_suffix_publishes_nothing_and_constructs_nothing(tmp_path, monkeypatch, point, suffix):
    import techlens.cli as cli
    import httpx
    import openai
    spies = []
    for owner, name in ((cli, "AssessmentSession"), (cli, "authorize_live_run"),
                        (httpx.Client, "__init__"), (httpx.Client, "send"),
                        (openai.OpenAI, "__init__"), (cli, "save_request")):
        spy = Mock(side_effect=AssertionError("Boundary must not be reached"))
        monkeypatch.setattr(owner, name, spy)
        spies.append(spy)
    prefix = "\n".join(replies()[:point]) + ("\n" if point else "")
    stream, out, path = Terminal(prefix + "x" * 4099 + suffix + "\n"), Terminal(), tmp_path / "request.json"
    assert cli.main(["request", "create", "--output", str(path)], stdin=stream, stdout=out,
        environ={"OPENAI_API_KEY": "FAKE-KEY", "TAVILY_API_KEY": "FAKE-KEY", "GITHUB_TOKEN": "FAKE-KEY"}) == 2
    assert all(s.call_count == 0 for s in spies) and list(tmp_path.iterdir()) == []
    assert "Input line exceeds" in out.getvalue() and "x" * 20 not in out.getvalue()
    assert stream.read() == suffix + "\n"


@pytest.mark.parametrize("raw", ["id" + "\r" * 4097 + "1\n",
                                  "id" + "\r" * 4096 + "\n",
                                  "id" + "\r" * 4095 + "\r\n",
                                  "id" + "\r" * 4095], ids=["master", "lf", "crlf", "eof"])
def test_repeated_trailing_cr_cannot_hide_raw_overflow(tmp_path, monkeypatch, raw):
    import techlens.cli as cli
    no_session = Mock(side_effect=AssertionError())
    monkeypatch.setattr(cli, "AssessmentSession", no_session)
    out, path = Terminal(), tmp_path / "request.json"
    assert cli.main(["request", "create", "--output", str(path)],
        stdin=Terminal(raw), stdout=out, environ={}) == 2
    assert "Number of candidates" not in out.getvalue()
    assert "Input line exceeds" in out.getvalue()
    assert not path.exists() and no_session.call_count == 0


@pytest.mark.parametrize("point", [0, 16])
def test_injected_builder_reader_uses_typed_fatal_bound(point):
    from techlens.request_builder import InputBoundaryError
    values = replies()[:point] + ["x" * 4097]
    with pytest.raises(InputBoundaryError) as caught:
        build(values)
    assert caught.value.code == 2
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "x" * 20 not in str(caught.value)
