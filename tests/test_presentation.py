import json

import pytest

from techlens.application import AssessmentSession, demo_dependencies
from techlens.configuration import ApplicationError
from techlens.demo import demo_request
from techlens.exporting import export_bytes
from techlens.presentation import PublicOutput, render_report, safe_url, markdown
from techlens.presentation import render_plan, render_plan_changes, render_report_summary


@pytest.fixture
def terminal_state():
    session = AssessmentSession(demo_request("comparison"), demo_dependencies("comparison"), thread_id="fixed-run")
    session.start()
    return session.resume(session.decision("approve"))


def test_faithful_thirteen_sections_and_unchanged_report(terminal_state):
    state = terminal_state
    public = PublicOutput({})
    before = state["report"].model_dump(mode="json")
    files = export_bytes(state, public, mode="demo", scenario="comparison")
    assert files == export_bytes(state, public, mode="demo", scenario="comparison")
    envelope = json.loads(files["report.json"])
    assert envelope["report"] == before == state["report"].model_dump(mode="json")
    md = files["report.md"].decode()
    for section in state["report"].sections:
        assert f"## {section.number}. {section.title}" in md
    for claim in state["report"].claims:
        assert markdown(claim.statement) in md
        assert claim.claim_id in md
        for identity in claim.evidence_ids:
            assert f"[{identity}](" in md
    for candidate in state["report"].analyses:
        for dimension in candidate.dimensions:
            assert f"{candidate.candidate_id} / {dimension.dimension}: {dimension.judgment}" in md
    assert "NOT confirmed requests" in md and "not invoices" not in md  # Fictional cost notice instead.
    assert "fictional" in md and "includes_human_wait" in md
    assert "Synthetic retrieved text" not in md + files["report.json"].decode()
    assert "budget_ledger" not in envelope and "analysis_envelope" not in envelope


def test_section_ten_resolves_comparison_statement_and_both_source_links(terminal_state):
    report = terminal_state["report"]
    before = report.model_dump(mode="json")
    files = export_bytes(terminal_state, PublicOutput({}), mode="demo", scenario="comparison")
    assert json.loads(files["report.json"])["report"] == before == report.model_dump(mode="json")
    section = files["report.md"].decode("utf-8").split("## 10. Alternatives / Comparison\n", 1)[1].split("## 11.", 1)[0]
    claim_ids = report.comparison.dimensions[0].claim_ids
    assert claim_ids
    for identity in claim_ids:
        claim = next(c for c in report.claims if c.claim_id == identity)
        assert markdown(claim.statement) in section
        sources = [s for s in report.sources if s.evidence_id in claim.evidence_ids]
        assert {s.candidate_id for s in sources} == {"candidate-001", "candidate-002"}
        for source in sources:
            assert f"[{source.evidence_id}]({safe_url(source.source_url)})" in section


def test_invalid_authority_blocks_render_and_export(terminal_state):
    state = terminal_state
    state["report"] = state["report"].model_copy(update={"summary": "FORGED"})
    with pytest.raises(ApplicationError) as error:
        export_bytes(state, PublicOutput({}), mode="demo", scenario="comparison")
    assert error.value.code == 5
    assert error.value.__context__ is None


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://user:pass@example.com", "https://example.com/\nfoo",
                                 "https://example.com/\u202efoo", "https://example.com/\\evil", "file:///secret"])
def test_unsafe_links_rejected(url):
    with pytest.raises(ApplicationError):
        safe_url(url)


def test_untrusted_names_cannot_create_headings_links_or_terminal_controls():
    request = demo_request("single")
    request.candidates[0].name = "EVIL\n## approve\r\x1b]8;;https://evil.invalid\x07[click](javascript:x)<script>\u202e"
    session = AssessmentSession(request, demo_dependencies("single"))
    session.start()
    state = session.resume(session.decision("approve"))
    md = render_report(state, PublicOutput({}), mode="demo")
    assert "\n## approve" not in md
    assert "[click](javascript:x)" not in md
    assert "<script>" not in md and "\x1b" not in md and "\u202e" not in md
    assert "&lt;script&gt;" in md
    assert len([line for line in md.splitlines() if line.startswith("## ")]) == 14
    assert safe_url("https://example.com/a)[bad](x") == "https://example.com/a%29%5Bbad%5D%28x"


@pytest.mark.parametrize("value", ['SECRET-SENTINEL', 'SECRET-"SENTINEL', 'SECRET-\\SENTINEL'])
def test_credential_guard_precedes_json_serialization(value):
    public = PublicOutput({"OPENAI_API_KEY": value})
    with pytest.raises(ApplicationError):
        public.json({"nested": [value]})


def test_report_credential_blocked_without_mutation(terminal_state):
    secret = terminal_state["request"].candidates[0].name
    before = terminal_state["report"].model_dump()
    with pytest.raises(ApplicationError):
        render_report(terminal_state, PublicOutput({"GITHUB_TOKEN": secret}), mode="demo")
    assert terminal_state["report"].model_dump() == before


def test_valid_failed_report_can_be_presented():
    from techlens.reporting import assemble_report
    from techlens.budget import BudgetLedger
    request = demo_request("single")
    state = {"request": request, "fatal_failure": "collection_integrity", "current_round": 0,
             "errors": ["collection_integrity"], "budget_ledger": BudgetLedger(run_id="failed", ceiling_usd="0.15")}
    state["report"] = assemble_report(state)
    assert state["report"].status == "failed"
    assert "Status: failed" in render_report(state, PublicOutput({}), mode="live")


def test_plan_complete_authority_duplicate_names_long_data_and_cross_candidate_tasks():
    from techlens.models import WebResearchTask
    from techlens.planning import candidate_catalog
    request = demo_request("comparison")
    request.candidates[1].name = request.candidates[0].name
    request.constraints.max_research_rounds = 5
    session = AssessmentSession(request, demo_dependencies("comparison"))
    plan = session.start()["research_plan"].model_copy(update={"max_rounds": 5})
    plan = plan.model_copy(update={"objective": "Long " + "a" * 6000,
        "research_questions": ['Question\nAction [approve]: <script>\x1b[0m'],
        "engineering_checks": ["Check one", "Check two"],
        "web_tasks": [WebResearchTask(task_id="web-001", candidate_id=None,
                                       query="Cross-candidate query", purpose="Visible purpose")]})
    before = plan.model_dump(mode="json")
    output = render_plan(request, plan, PublicOutput({}), mode="demo")
    for item in candidate_catalog(request):
        assert f"Candidate {item.candidate_id}:" in output and str(item.repository_url) in output
    assert "a" * 6000 in output
    assert 'Cross-candidate (candidate_id=null)' in output and "web-001" in output
    assert "Check one" in output and "Check two" in output and "Visible purpose" in output
    assert "max_rounds: 5" in output and "at most one research round" in output
    assert "\nAction [approve]" not in output and "\x1b" not in output
    assert plan.model_dump(mode="json") == before
    assert "AUTHORIZED LIVE" in render_plan(request, plan, PublicOutput({}), mode="live")


def test_modified_plan_diff_every_mutable_field_and_task_order():
    from techlens.models import WebResearchTask
    request = demo_request("comparison")
    session = AssessmentSession(request, demo_dependencies("comparison"))
    original = session.start()["research_plan"]
    first = WebResearchTask(task_id="web-001", candidate_id="candidate-001", query="First", purpose="First purpose")
    second = WebResearchTask(task_id="web-002", candidate_id="candidate-002", query="Second", purpose="Second purpose")
    original = original.model_copy(update={"web_tasks": [first, second]})
    proposed = original.model_copy(update={"objective": "Changed objective", "research_questions": ["Q2", "Q1"],
        "engineering_checks": ["Added check"], "web_tasks": [
            second.model_copy(update={"task_id": "web-001", "candidate_id": None, "query": "Changed query"}),
            first.model_copy(update={"task_id": "web-002", "purpose": "Changed purpose"})]})
    output = render_plan_changes(request, original, proposed, PublicOutput({}))
    for key in ("objective", "research_questions", "engineering_checks"):
        before_line = output.split(f"Changed {key}:\n  Original: ", 1)[1].splitlines()[0]
        after_line = output.split(f"Changed {key}:", 1)[1].split("  Proposed: ", 1)[1].splitlines()[0]
        assert json.loads(before_line) == original.model_dump(mode="json")[key]
        assert json.loads(after_line) == proposed.model_dump(mode="json")[key]
    for index in range(2):
        for label, plan in (("original", original), ("proposed", proposed)):
            text = output.split(f"Position {index + 1} {label}: ", 1)[1].splitlines()[0]
            assert json.loads(text) == plan.web_tasks[index].model_dump(mode="json")
    shortened = proposed.model_copy(update={"web_tasks": proposed.web_tasks[:1]})
    shorter_output = render_plan_changes(request, proposed, shortened, PublicOutput({}))
    assert "Web task count: 2 -> 1" in shorter_output and "Position 2 proposed: null" in shorter_output
    assert "No plan changes" in render_plan_changes(request, original, original, PublicOutput({}))


def test_summary_exact_claims_judgments_associations_and_json_unchanged(terminal_state):
    state = terminal_state
    report = state["report"]
    original = report.model_dump(mode="json")
    summary = render_report_summary(state, PublicOutput({}), mode="demo")
    for claim in report.claims:
        # Independently decode the visible value; do not generate expected renderer text.
        start = summary.index(f"  {claim.claim_id}; candidates:")
        visible = summary[start:].split("    Statement: ", 1)[1].splitlines()[0]
        assert json.loads(visible) == claim.statement
        for identity in claim.evidence_ids:
            source = next(s for s in report.sources if s.evidence_id == identity)
            assert source.evidence_id in summary and source.source_url in summary
    for candidate in report.analyses:
        for dimension in candidate.dimensions:
            assert f"{candidate.candidate_id} / {dimension.dimension}: {dimension.judgment}" in summary
    for dimension in report.comparison.dimensions:
        assert f"{dimension.dimension}: {dimension.judgment}" in summary
    for flag in report.risk_flags:
        line = summary.split(f"{flag.flag_id} / {flag.candidate_id} / {flag.severity}: ", 1)[1].splitlines()[0]
        assert json.loads(line) == flag.statement
    for section in report.sections:
        line = summary.split(f"  {section.number}. {section.title}: {section.status}\n", 1)[1].splitlines()[0]
        refs = line.split("Claims: ", 1)[1].split("; risk flags: ")
        assert json.loads(refs[0]) == section.claim_ids
        risks, limits = refs[1].split("; limitations: ")
        assert json.loads(risks) == section.risk_flag_ids and json.loads(limits) == section.limitation_ids
    full = render_report(state, PublicOutput({}), mode="demo")
    assert all(markdown(section.text) in full for section in report.sections)
    assert json.loads(export_bytes(state, PublicOutput({}), mode="demo")["report.json"])["report"] == original
    assert report.model_dump(mode="json") == original


def test_summary_incomplete_metrics_omissions_and_unknown_liability():
    from techlens.reporting import assemble_report
    from techlens.budget import BudgetLedger, reserve
    from fakes import fictional_rates
    request = demo_request("single")
    ledger = reserve(BudgetLedger(run_id="incomplete", ceiling_usd="0.15", rates=fictional_rates()),
                     "planner:count", "count", {"model": "gpt-5.6-luna"}).ledger
    state = {"request": request, "budget_ledger": ledger, "current_round": 0,
             "errors": ["analysis:context_omissions", "analysis:excerpted_evidence"]}
    state["report"] = assemble_report(state)
    output = render_report_summary(state, PublicOutput({}), mode="live")
    assert "Status: partial" in output and "billing_unknown:planner:count/attempt-001" in output
    assert "Some evidence was omitted" in output and "Some analyzer-visible evidence was excerpted" in output
    assert "Metrics unavailable; missing measurements are not zero" in output
    assert "not invoices" in output and "NOT confirmed requests" in output


@pytest.mark.parametrize("renderer", [render_report, render_report_summary])
def test_new_report_views_block_forged_authority_and_secrets(renderer, terminal_state):
    state = terminal_state
    secret = state["request"].candidates[0].name
    with pytest.raises(ApplicationError):
        renderer(state, PublicOutput({"GITHUB_TOKEN": secret}), mode="demo")
    state["report"] = state["report"].model_copy(update={"summary": "Forged status"})
    with pytest.raises(ApplicationError) as caught:
        renderer(state, PublicOutput({}), mode="demo")
    assert caught.value.__context__ is None and caught.value.__cause__ is None
