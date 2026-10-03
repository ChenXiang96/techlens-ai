"""Offline evaluation contracts; never imported by the assessment workflow."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from techlens.analysis_models import AnalysisProjection
from techlens.collection_models import CollectionCoverage
from techlens.evidence_models import EvidenceRecord
from techlens.models import AssessmentRequest, ResearchPlan
from techlens.research_models import CandidateEngineeringSignals, WebResearchTaskResult
from techlens.risk_models import RiskFlag

LAYERS = ("schema", "projection", "finalization", "source_integrity", "report", "boundary")
Label = Literal["supported", "contradicted", "unsupported", "indeterminate"]
ReviewStatus = Literal["fixture_expectation", "pending_human_review",
                       "adjudicated_human_review", "unresolved_dispute"]
LayerStatus = Literal["passed", "rejected", "not_exercised", "blocked_by_prior_rejection"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceBundle(Strict):
    profile_id: Literal["single", "pair", "partial"]
    request: AssessmentRequest
    plan: ResearchPlan
    github: list[CandidateEngineeringSignals]
    web: list[WebResearchTaskResult]
    coverage: CollectionCoverage


class Observation(Strict):
    observation_id: str
    kind: Literal["answerable", "uncertainty"]
    description: str = Field(min_length=1)


class AttackObjective(Strict):
    requested_behavior: str = Field(min_length=1)
    output_target: str = Field(min_length=1)
    boundary_target: str = Field(min_length=1)


class Case(Strict):
    case_id: str = Field(pattern=r"^C\d{2}$")
    category: str
    rationale: str = Field(min_length=1)
    request: AssessmentRequest
    evidence: list[EvidenceRecord] = Field(max_length=128)
    coverage: CollectionCoverage
    risk_flags: list[RiskFlag]
    projection: AnalysisProjection
    envelope: dict[str, JsonValue]
    source_profile_id: str
    source_profile_digest: str
    source_variant: SourceBundle | None = None
    source_variant_digest: str | None = None
    full_report: bool = False
    boundary: Literal["none", "local", "graph"] = "none"
    observations: list[Observation]
    invariants: list[str] = Field(min_length=1)
    attack_objective: AttackObjective | None = None

    @model_validator(mode="after")
    def required_sources(self):
        if (self.full_report or self.boundary == "graph") and self.source_variant is None:
            raise ValueError("Full report/graph cases require authoritative synthetic sources")
        if (self.source_variant is None) != (self.source_variant_digest is None):
            raise ValueError("Source variant and digest must occur together")
        if len({o.observation_id for o in self.observations}) != len(self.observations):
            raise ValueError("Duplicate reference observation")
        return self


class Unit(Strict):
    unit_id: str
    claim_index: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=800)


class Sample(Strict):
    sample_id: str
    case_id: str
    variant: Literal["control", "defect"]
    origin: Literal["canned", "actual"] = "canned"
    model: str | None = None
    prompt_digest: str
    draft: dict[str, JsonValue]
    units: list[Unit]
    sample_digest: str
    projection_digest: str
    segmentation_digest: str


class Review(Strict):
    provenance: Literal["fixture_expectation", "human_review"]
    status: ReviewStatus
    reviewer_ids: list[str] = Field(default_factory=list)
    review_reference: str | None = None
    disagreements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def provenance_matches(self):
        if (self.provenance == "fixture_expectation") != (self.status == "fixture_expectation"):
            raise ValueError("Fixture expectations cannot become human adjudication")
        if self.status == "fixture_expectation" and (self.reviewer_ids or self.review_reference):
            raise ValueError("Fixture expectations must not claim human review")
        if self.status == "adjudicated_human_review" and (not self.reviewer_ids or not self.review_reference):
            raise ValueError("Adjudication requires documented human review")
        if self.status == "unresolved_dispute" and not self.disagreements:
            raise ValueError("A dispute must preserve differing judgments")
        return self


class UnitLabel(Strict):
    unit_id: str
    label: Label | None
    rationale: str = Field(min_length=1)
    review: Review

    @model_validator(mode="after")
    def decided(self):
        if self.review.status in {"fixture_expectation", "adjudicated_human_review"} and self.label is None:
            raise ValueError("Decided labels require a semantic category")
        return self


class ObservationLabel(Strict):
    observation_id: str
    outcome: Literal["supported", "appropriate_abstention", "omitted", "incorrect", "unnecessary_abstention"]


class SampleLabels(Strict):
    sample_id: str
    sample_digest: str
    projection_digest: str
    segmentation_digest: str
    rubric_version: Literal["t006-v1"] = "t006-v1"
    review: Review
    units: list[UnitLabel]
    observations: list[ObservationLabel]
    task_completed: bool
    # A provisional observation about raw canned output, never causal model influence
    # or evidence that an application-owned action actually executed.
    canned_output_target: Literal["present", "absent", "undecided"] | None = None
    target_behavior_rationale: str | None = None
    expected_rejection: Literal["schema", "finalization"] | None = None
    rationale: str = Field(min_length=1)


class LayerOutcome(Strict):
    status: LayerStatus = "not_exercised"
    reason: str | None = None


class SampleResult(Strict):
    sample_id: str
    case_id: str
    layer_scope: Literal["frozen sample replay; graph routes have separate layer outcomes"] = "frozen sample replay; graph routes have separate layer outcomes"
    layers: dict[str, LayerOutcome]
    assessment_outcome: Literal["accepted", "rejected"]
    harness_expectation_met: bool
    claims: list[dict[str, JsonValue]]
    report_status: str | None = None
    current_round: int | None = None
    boundary_traces: list[dict[str, JsonValue]] = Field(default_factory=list)


class Measure(Strict):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None
    reason: str | None


class EvaluationRun(Strict):
    dataset_version: str
    status: Literal["harness_self_test / fixture_only"] = "harness_self_test / fixture_only"
    provenance: dict[str, JsonValue]
    samples: list[SampleResult]
    harness: dict[str, JsonValue]
    actual_model_semantics: dict[str, JsonValue]
    actual_model_prompt_injection: dict[str, JsonValue]
    external_truth_and_credibility: Literal["NOT EVALUATED"] = "NOT EVALUATED"
