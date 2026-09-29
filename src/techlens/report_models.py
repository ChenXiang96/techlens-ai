"""Report presentation and measured execution facts."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    number: int = Field(ge=1, le=13)
    title: str
    status: Literal["available", "insufficient_information", "not_applicable"]
    claim_ids: list[str] = Field(default_factory=list)
    risk_flag_ids: list[str] = Field(default_factory=list)
    limitation_ids: list[str] = Field(default_factory=list)
    text: str


class ReportClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: str
    statement: str
    candidate_ids: list[str]
    evidence_ids: list[str]


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: str
    candidate_id: str | None
    source_url: str


class RunMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    assessment_id: str
    status: Literal["running", "completed", "partial", "rejected", "failed"]
    started_at: datetime
    finished_at: datetime | None = None
    elapsed_seconds: float | None = None
    includes_human_wait: bool = True
    # Attempts count admitted ledger records; admission does not prove dispatch.
    planner_attempts: int = 0
    analyzer_attempts: int = 0
    llm_attempts: int = 0
    count_attempts: int = 0
    ambiguous_attempts: int = 0  # No settled provider outcome; transport may or may not have begun.
    search_attempts: int = 0
    github_calls_measured: int = 0
    github_calls_complete: bool = True
    input_tokens_known: int = 0
    output_tokens_known: int = 0
    token_usage_complete: bool = True
    credits_known: int = 0
    credits_complete: bool = True
    settled_estimate_usd: Decimal = Decimal(0)
    outstanding_reservations_usd: Decimal = Decimal(0)
    cost_status: Literal["estimated", "unknown_liability", "overrun"] = "estimated"
    research_rounds: int = 0
    evidence_count: int = 0
    claim_count: int = 0
    collection_failures: int = 0
    collection_skips: int = 0
    error_categories: list[str] = Field(default_factory=list)
