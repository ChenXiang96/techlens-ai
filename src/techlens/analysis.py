"""Data projection and citation integrity; no claim of semantic verification."""
import json
from typing import Protocol
from techlens.analysis_models import (
    RUBRIC, AnalysisDraft, AnalysisProjection, AnalysisResult, CandidateAnalysis,
    ComparisonAnalysis, DimensionAssessment,
)
from techlens.evidence import validate_claim_evidence_contract
from techlens.evidence_models import ClaimRecord, WebEvidencePayload
from techlens.planning import candidate_catalog


class AnalysisValidationError(Exception):
    pass


class AdoptionAnalyzer(Protocol):
    def prepare(self, envelope: dict) -> dict: ...


def project_evidence(request, evidence, flags, coverage):
    included, omitted, excerpted, view = [], [], [], []
    web_count = 0
    for record in evidence:
        item = record.model_dump(mode="json")
        if isinstance(record.payload, WebEvidencePayload):
            web_count += 1
            if web_count > 24:
                omitted.append(record.evidence_id)
                continue
            if len(item["payload"]["content"]) > 1200:
                item["payload"]["content"] = item["payload"]["content"][:1200]
                excerpted.append(record.evidence_id)
        if len(json.dumps(item).encode()) > 8192:
            omitted.append(record.evidence_id)
            if record.evidence_id in excerpted:
                excerpted.remove(record.evidence_id)
            continue
        included.append(record.evidence_id)
        view.append(item)
    manifest = AnalysisProjection(eligible_evidence_ids=included, omitted_evidence_ids=omitted,
                                  excerpted_evidence_ids=excerpted)
    projected_flags = []
    for flag in flags:
        item = {k: v for k, v in flag.model_dump(mode="json").items() if k != "provenance"}
        if flag.provenance.kind == "evidence":
            item["eligible_evidence_ids"] = [e for e in flag.provenance.evidence_ids if e in included]
            item["omitted_evidence_count"] = sum(e not in included for e in flag.provenance.evidence_ids)
        else:
            item["collection_operation_id"] = flag.provenance.operation_id
        projected_flags.append(item)
    envelope = {"candidates": [c.model_dump(mode="json") for c in candidate_catalog(request)],
                "team_profile": request.team_profile.model_dump(mode="json"),
                "priorities": request.priorities, "rubric": list(RUBRIC), "evidence": view,
                "risk_flags": projected_flags,
                "coverage": coverage.model_dump(mode="json"), "projection": {
                    "version": manifest.version, "eligible_evidence_ids": included,
                    "omitted_record_count": len(omitted), "excerpted_evidence_ids": excerpted}}
    if len(json.dumps(envelope).encode()) > 131072:
        raise AnalysisValidationError("Analysis input exceeds application size bound")
    return envelope, manifest


def finalize_analysis(value, request, evidence, projection):
    failure = False
    try:
        draft = AnalysisDraft.model_validate(value.model_dump() if isinstance(value, AnalysisDraft) else value)
    except (ValueError, TypeError):
        failure = True
    if failure:
        raise AnalysisValidationError("Invalid analysis structure")
    ids = [c.candidate_id for c in candidate_catalog(request)]
    if [c.candidate_id for c in draft.candidates] != ids:
        raise AnalysisValidationError("Analysis candidate roster differs")
    claims, candidates, sections = [], [], {}
    allowed = set(projection.eligible_evidence_ids)
    evidence_map = {e.evidence_id: e for e in evidence}

    def add(statement, required_ids=None, comparative=False):
        if not set(statement.evidence_ids) <= allowed:
            raise AnalysisValidationError("Analysis cites evidence outside its view")
        if required_ids is not None and statement.candidate_ids != required_ids:
            raise AnalysisValidationError("Analysis claim candidate association differs")
        if comparative and not set(statement.candidate_ids) <= {
            evidence_map[e].candidate_id for e in statement.evidence_ids
        }:
            raise AnalysisValidationError("Comparison lacks per-candidate evidence")
        # Do not accept copied provider snippets as report prose.
        for e in evidence:
            if isinstance(e.payload, WebEvidencePayload) and len(e.payload.content) >= 16:
                if e.payload.content in statement.statement:
                    raise AnalysisValidationError("Raw snippet copied into report prose")
        claim = ClaimRecord(claim_id=f"claim-{len(claims)+1:03d}", **statement.model_dump())
        claims.append(claim)
        return claim.claim_id

    def dimensions(values, target_ids, comparative=False):
        if [d.dimension for d in values] != list(RUBRIC):
            raise AnalysisValidationError("Analysis rubric differs")
        result = []
        for d in values:
            if (d.judgment == "insufficient_information") != (not d.claims):
                raise AnalysisValidationError("Judgment must have cited reasoning or explicit unknown")
            result.append(DimensionAssessment(dimension=d.dimension, judgment=d.judgment,
                claim_ids=[add(c, target_ids, comparative) for c in d.claims]))
        return result

    for c in draft.candidates:
        candidates.append(CandidateAnalysis(candidate_id=c.candidate_id,
                          dimensions=dimensions(c.dimensions, [c.candidate_id])))
    if [s.section_number for s in draft.findings] != sorted({s.section_number for s in draft.findings}):
        raise AnalysisValidationError("Finding sections must be unique and ordered")
    for section in draft.findings:
        sections[str(section.section_number)] = [add(c, comparative=len(c.candidate_ids) > 1) for c in section.claims]
    if len(ids) == 1:
        if draft.comparison:
            raise AnalysisValidationError("Single candidate comparison is not applicable")
        comparison = ComparisonAnalysis(status="not_applicable", candidate_ids=ids, dimensions=[])
    else:
        comparison = ComparisonAnalysis(status="available", candidate_ids=ids,
                                         dimensions=dimensions(draft.comparison, ids, True))
    validate_claim_evidence_contract(claims, evidence, request)
    return claims, AnalysisResult(candidates=candidates, comparison=comparison, section_claims=sections)


def unknown_analysis(request):
    ids = [c.candidate_id for c in candidate_catalog(request)]
    def dims():
        return [DimensionAssessment(dimension=d, judgment="insufficient_information", claim_ids=[]) for d in RUBRIC]
    return AnalysisResult(candidates=[CandidateAnalysis(candidate_id=c, dimensions=dims()) for c in ids],
        comparison=ComparisonAnalysis(status="not_applicable" if len(ids) == 1 else "insufficient_information",
                                      candidate_ids=ids, dimensions=[] if len(ids) == 1 else dims()),
        section_claims={})
