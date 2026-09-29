import pytest
from fakes import FakeAnalyzer
from test_t005_workflow import configured, finish
from techlens.reporting import validate_report, ReportValidationError


class CitedAnalyzer(FakeAnalyzer):
    def generate(self, shared):
        result = super().generate(shared)
        result.data["candidates"][0]["dimensions"][0].update(judgment="mixed", claims=[{
            "statement": "The recorded repository state should be considered with the team context.",
            "candidate_ids": ["candidate-001"], "evidence_ids": ["evidence-001"]}])
        return result


@pytest.fixture
def assessed():
    return finish(configured(analyzer=CitedAnalyzer()))


def test_report_is_readable_cited_and_not_an_extra_generation(assessed):
    report = assessed["report"]
    assert len(report.sections) == 13 and report.claims[0].claim_id == "claim-001"
    assert report.team_profile.primary_languages == ["Python"]
    assert report.metrics.llm_attempts == 2 and report.metrics.count_attempts == 2
    assert report.comparison.status == "not_applicable"
    validate_report(report, assessed)


@pytest.mark.parametrize("field", ["roster", "status", "flags", "sections", "source", "claims", "summary", "context", "limits", "metric", "omitted_claim"])
def test_independent_validator_rejects_corrupted_report(assessed, field):
    report = assessed["report"].model_copy(deep=True)
    if field == "roster":
        report.candidate_ids.append("candidate-999")
    elif field == "status":
        report.status = "partial"
    elif field == "flags":
        report.risk_flags.clear()
    elif field == "sections":
        report.sections.pop()
    elif field == "source":
        report.sources[0] = report.sources[0].model_copy(update={"source_url": "https://example.com/fabricated"})
    elif field == "claims":
        report.claims[0] = report.claims[0].model_copy(update={"statement": "Uncited replacement"})
    elif field == "summary":
        report.summary = "Everyone should adopt this technology."
    elif field == "context":
        report.team_profile.production_criticality = "Invented requirement"
    elif field == "limits":
        report.sections[12].limitation_ids.clear()
    elif field == "metric":
        report.metrics = report.metrics.model_copy(update={"evidence_count": 999})
    else:
        for section in report.sections:
            section.claim_ids.clear()
    with pytest.raises(ReportValidationError):
        validate_report(report, assessed)


def test_validator_rejects_claim_moved_from_adoption_fit_to_overview(assessed):
    report = assessed["report"].model_copy(deep=True)
    report.sections[10].claim_ids.clear()
    report.sections[2] = report.sections[2].model_copy(update={
        "claim_ids": ["claim-001"], "status": "available",
        "text": "See referenced observations and claims."})
    with pytest.raises(ReportValidationError, match="section differs"):
        validate_report(report, assessed)


def test_validator_rejects_citation_free_overview_marked_available(assessed):
    report = assessed["report"].model_copy(deep=True)
    assert report.sections[2].claim_ids == []
    report.sections[2] = report.sections[2].model_copy(update={
        "status": "available", "text": "See referenced observations and claims."})
    with pytest.raises(ReportValidationError, match="section differs"):
        validate_report(report, assessed)


@pytest.mark.parametrize("section_index,field,replacement", [
    (0, "claim_ids", []), (0, "risk_flag_ids", []), (0, "limitation_ids", []),
    (8, "risk_flag_ids", []), (8, "text", "Insufficient information."),
    (12, "limitation_ids", []), (12, "status", "insufficient_information"),
])
def test_validator_checks_summary_risk_and_uncertainty_section_mappings(
        assessed, section_index, field, replacement):
    report = assessed["report"].model_copy(deep=True)
    assert getattr(report.sections[section_index], field) != replacement
    report.sections[section_index] = report.sections[section_index].model_copy(update={field: replacement})
    with pytest.raises(ReportValidationError, match="section differs"):
        validate_report(report, assessed)


def test_validator_checks_comparison_section_mapping_for_multiple_candidates():
    from fakes import make_request
    result = finish(configured(analyzer=CitedAnalyzer()), make_request(2), thread="comparison-section")
    report = result["report"].model_copy(deep=True)
    assert report.sections[9].claim_ids == [] and report.sections[9].status == "insufficient_information"
    report.sections[9] = report.sections[9].model_copy(update={
        "claim_ids": ["claim-001"], "status": "available",
        "text": "See referenced observations and claims."})
    with pytest.raises(ReportValidationError, match="section differs"):
        validate_report(report, result)
