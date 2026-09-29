"""Bounded analysis drafts and application-owned references."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

RUBRIC = ("maintenance_fit", "technical_stack_fit", "operational_fit", "maturity_fit",
          "risk_tolerance_fit", "ecosystem_fit")
Dimension = Literal["maintenance_fit", "technical_stack_fit", "operational_fit", "maturity_fit",
                    "risk_tolerance_fit", "ecosystem_fit"]
Judgment = Literal["favorable", "mixed", "unfavorable", "insufficient_information"]
SECTION_TITLES = (
    "Executive Summary", "Team & Adoption Context", "Technology Overview", "Engineering Health",
    "Architecture & Capabilities", "Security & Licensing", "Deployment / Operational Complexity",
    "Ecosystem & Maturity", "Key Risks", "Alternatives / Comparison", "Adoption Fit Analysis",
    "Evidence & Sources", "Known Uncertainties",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StatementDraft(StrictModel):
    statement: str = Field(min_length=1, max_length=800)
    candidate_ids: list[str] = Field(max_length=3)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class DimensionDraft(StrictModel):
    dimension: Dimension
    judgment: Judgment
    claims: list[StatementDraft] = Field(max_length=2)


class CandidateDraft(StrictModel):
    candidate_id: str
    dimensions: list[DimensionDraft] = Field(min_length=6, max_length=6)


class SectionDraft(StrictModel):
    section_number: int = Field(ge=3, le=8)
    claims: list[StatementDraft] = Field(max_length=2)


class AnalysisDraft(StrictModel):
    candidates: list[CandidateDraft] = Field(min_length=1, max_length=3)
    findings: list[SectionDraft] = Field(max_length=6)
    comparison: list[DimensionDraft] = Field(max_length=6)


class DimensionAssessment(StrictModel):
    dimension: Dimension
    judgment: Judgment
    claim_ids: list[str]


class CandidateAnalysis(StrictModel):
    candidate_id: str
    dimensions: list[DimensionAssessment]


class ComparisonAnalysis(StrictModel):
    status: Literal["available", "not_applicable", "insufficient_information"]
    candidate_ids: list[str]
    dimensions: list[DimensionAssessment]


class AnalysisProjection(StrictModel):
    version: Literal["t005-v1"] = "t005-v1"
    eligible_evidence_ids: list[str]
    omitted_evidence_ids: list[str]
    excerpted_evidence_ids: list[str]


class AnalysisResult(StrictModel):
    candidates: list[CandidateAnalysis]
    comparison: ComparisonAnalysis
    section_claims: dict[str, list[str]]
