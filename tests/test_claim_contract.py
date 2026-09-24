import pytest

from fakes import make_plan, make_request, make_signals
from techlens.evidence import (
    ClaimEvidenceContractError, normalize_records, validate_claim_evidence_contract,
)
from techlens.evidence_models import ClaimRecord
from techlens.planning import validate_plan
from techlens.research_models import CandidateEngineeringSignals, WebResearchTaskResult, WebSearchResult


def claim_sources():
    request = make_request(2)
    github = [CandidateEngineeringSignals(
        candidate_id=f"candidate-{index:03d}", candidate_name=candidate.name,
        repository_url=candidate.repository_url, engineering_signals=make_signals(candidate),
    ) for index, candidate in enumerate(request.candidates, start=1)]
    # A real cross-candidate web task provides unassigned evidence.
    plan = make_plan(request, 1).model_dump()
    plan["web_tasks"][0]["candidate_id"] = None
    plan = validate_plan(plan, request)
    web = [WebResearchTaskResult(
        task=plan.web_tasks[0], provider="fake",
        results=[WebSearchResult(title="General", url="https://example.com/general",
                                 content="untrusted", score=0.4)],
    )]
    records = normalize_records(request, plan, github, web)
    return request, records


def test_valid_candidate_and_general_claim_references():
    request, records = claim_sources()
    claims = [
        ClaimRecord(claim_id="claim-001", statement="Candidate-specific text",
                    candidate_ids=["candidate-001"], evidence_ids=["evidence-001", "evidence-049"]),
        ClaimRecord(claim_id="claim-002", statement="General text",
                    candidate_ids=[], evidence_ids=["evidence-049"]),
        ClaimRecord(claim_id="claim-003", statement="Two-candidate text",
                    candidate_ids=["candidate-001", "candidate-002"],
                    evidence_ids=["evidence-001", "evidence-025"]),
    ]
    assert validate_claim_evidence_contract(claims, records, request) is None


@pytest.mark.parametrize("change", [
    "noncanonical", "gap", "zero_evidence", "missing_evidence", "duplicate_evidence",
    "invented_candidate", "duplicate_candidate", "unrelated_candidate", "general_specific",
])
def test_invalid_claim_reference_contract(change):
    request, records = claim_sources()
    claim = {"claim_id": "claim-001", "statement": "Text",
             "candidate_ids": ["candidate-001"], "evidence_ids": ["evidence-001"]}
    claims = [claim]
    if change == "noncanonical":
        claim["claim_id"] = "human-selected"
    elif change == "gap":
        claims.append({**claim, "claim_id": "claim-003"})
    elif change == "zero_evidence":
        claim["evidence_ids"] = []
    elif change == "missing_evidence":
        claim["evidence_ids"] = ["evidence-999"]
    elif change == "duplicate_evidence":
        claim["evidence_ids"] = ["evidence-001", "evidence-001"]
    elif change == "invented_candidate":
        claim["candidate_ids"] = ["candidate-999"]
    elif change == "duplicate_candidate":
        claim["candidate_ids"] = ["candidate-001", "candidate-001"]
    elif change == "unrelated_candidate":
        claim["evidence_ids"] = ["evidence-025"]
    else:
        claim["candidate_ids"] = []
    with pytest.raises(ClaimEvidenceContractError):
        validate_claim_evidence_contract(claims, records, request)
