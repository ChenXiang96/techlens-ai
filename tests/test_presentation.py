import json

import pytest

from techlens.application import AssessmentSession, demo_dependencies
from techlens.configuration import ApplicationError
from techlens.demo import demo_request
from techlens.exporting import export_bytes
from techlens.presentation import PublicOutput, render_report, safe_url, markdown


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
