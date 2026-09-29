import pytest
from test_evidence import sources
from test_risk import coverage_for
from techlens.evidence import normalize_records, validate_records, EvidenceNormalizationError, EvidenceValidationError


def test_partial_successes_remain_ordered_with_exact_registry_coverage():
    request, plan, github, web = sources(3, 3)
    coverage = coverage_for(request, plan, failed="repository_unavailable")
    records = normalize_records(request, plan, github[1:], web, coverage)
    summary = validate_records(request, plan, github[1:], web, records, coverage)
    assert summary.github_records == 48 and summary.web_records == 3
    assert records[0].candidate_id == "candidate-002" and records[24].candidate_id == "candidate-003"
    assert records[0].evidence_id == "evidence-001"
    with pytest.raises(EvidenceNormalizationError):
        normalize_records(request, plan, github[1:], web)


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "order", "fabricated", "wrong_success", "wrong_task"])
def test_coverage_errors_are_integrity_failures(corruption):
    request, plan, github, web = sources(2, 2)
    coverage = coverage_for(request, plan)
    records = normalize_records(request, plan, github, web)
    if corruption == "missing":
        coverage.github_outcomes.pop()
    elif corruption == "duplicate":
        coverage.github_outcomes.append(coverage.github_outcomes[0])
    elif corruption == "order":
        coverage.github_outcomes.reverse()
    elif corruption == "fabricated":
        coverage.github_outcomes[0] = coverage.github_outcomes[0].model_copy(update={"target_id": "candidate-999"})
    elif corruption == "wrong_success":
        coverage.github_outcomes[0] = coverage.github_outcomes[0].model_copy(update={"status": "failed", "error_category": "timeout"})
    else:
        web[0].task = web[1].task
    with pytest.raises(EvidenceNormalizationError):
        normalize_records(request, plan, github, web, coverage)
    with pytest.raises(EvidenceValidationError):
        validate_records(request, plan, github, web, records, coverage)


def test_zero_hits_and_failed_search_are_distinct():
    request, plan, github, web = sources()
    coverage = coverage_for(request, plan)
    web[0].results = []
    coverage.web_outcomes[1] = coverage.web_outcomes[1].model_copy(update={"status": "failed", "error_category": "timeout"})
    records = normalize_records(request, plan, github, web[:1], coverage)
    assert len(records) == 24 and coverage.web_outcomes[0].status == "succeeded"
    assert validate_records(request, plan, github, web[:1], records, coverage).web_records == 0
