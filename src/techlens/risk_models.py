"""Deterministic observations, not an overall technology score."""
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class RiskRuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = "t005-v1"
    stale_release_days: int = Field(default=365, gt=0)
    low_commits_90d: int = Field(default=5, ge=0)
    top_contributor_share: Decimal = Field(default=Decimal("0.80"), ge=0, le=1)


class EvidenceRiskProvenance(BaseModel):
    kind: Literal["evidence"] = "evidence"
    evidence_ids: list[str] = Field(min_length=1)


class CollectionRiskProvenance(BaseModel):
    kind: Literal["collection"] = "collection"
    operation_id: str


RiskProvenance = Annotated[EvidenceRiskProvenance | CollectionRiskProvenance,
                           Field(discriminator="kind")]


class RiskEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rule_id: str
    candidate_id: str
    version: str
    status: Literal["triggered", "not_triggered", "not_evaluable"]
    reason_code: str
    provenance: RiskProvenance | None = None


class RiskFlag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    flag_id: str
    rule_id: str
    candidate_id: str
    severity: Literal["hard", "warning", "operational_block"]
    statement: str
    provenance: RiskProvenance
