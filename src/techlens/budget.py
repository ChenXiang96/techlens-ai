"""Serializable Decimal admission and idempotent reconciliation."""
import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RateCard(DomainModel):
    """Caller-trusted upper rates; no production price is assumed."""
    model: str
    currency: Literal["USD"] = "USD"
    source_reference: str = Field(min_length=1)
    effective_date: date
    input_usd_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_usd_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    count_upper_fee: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    count_fixed_fee: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    count_source_reference: str | None = None
    count_effective_date: date | None = None
    count_authoritative_zero: bool = False
    search_upper_fee: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    search_fixed_fee: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    search_source_reference: str | None = None
    search_effective_date: date | None = None

    @model_validator(mode="after")
    def validate_fees(self):
        for kind in ("count", "search"):
            upper, fixed = getattr(self, f"{kind}_upper_fee"), getattr(self, f"{kind}_fixed_fee")
            if fixed is not None and (upper is None or fixed > upper):
                raise ValueError("Fixed fee exceeds configured upper fee")
        return self


class Usage(DomainModel):
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    credits: int | None = Field(default=None, ge=0, strict=True)


class AttemptOutcome(DomainModel):
    status: Literal["succeeded", "failed"]
    category: str | None = None
    data: dict[str, JsonValue] | None = None
    usage: Usage = Field(default_factory=Usage)
    retry_after: float | None = Field(default=None, ge=0, le=5)

    @model_validator(mode="after")
    def consistent(self):
        if (self.status == "succeeded") != (self.category is None):
            raise ValueError("Attempt outcome category disagrees with status")
        if self.status == "failed" and self.data is not None:
            raise ValueError("Failed attempt cannot carry successful output")
        return self


class VerifiedInputCount(DomainModel):
    operation_id: str
    attempt_id: str
    model: str
    counting_contract_version: Literal["responses-shared-v1"] = "responses-shared-v1"
    input_tokens: int = Field(ge=0, strict=True)
    shared_request_digest: str


class AttemptRecord(DomainModel):
    operation_id: str
    attempt_id: str
    kind: Literal["count", "generation", "search"]
    request_digest: str
    input_token_bound: int | None = None
    output_token_bound: int | None = None
    state: Literal["reserved", "settled", "billing_unknown"] = "reserved"
    reservation_usd: Decimal = Field(ge=0, allow_inf_nan=False)
    settled_usd: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    outcome: AttemptOutcome | None = None


class BudgetLedger(DomainModel):
    run_id: str
    ceiling_usd: Decimal = Field(ge=0, allow_inf_nan=False)
    rates: RateCard | None = None
    attempts: list[AttemptRecord] = Field(default_factory=list)
    overrun: bool = False
    max_search_attempts: int = Field(default=8, gt=0)

    @model_validator(mode="after")
    def unique_attempts(self):
        if len({a.attempt_id for a in self.attempts}) != len(self.attempts):
            raise ValueError("Duplicate accounting attempt")
        if self.attempts and self.rates is None:
            raise ValueError("Accounted attempts require a trusted rate snapshot")
        return self

    @property
    def settled(self):
        return sum((a.settled_usd or Decimal(0) for a in self.attempts), Decimal(0))

    @property
    def outstanding(self):
        return sum((a.reservation_usd for a in self.attempts if a.state != "settled"), Decimal(0))


class Admission(DomainModel):
    ledger: BudgetLedger
    attempt: AttemptRecord | None
    is_new: bool
    denial: str | None = None


def reserve(ledger, operation, kind, payload, *, attempt_number=1, count=None):
    """Only is_new=True permits creation of a process-local READY slot."""
    attempt_id = f"{operation}/attempt-{attempt_number:03d}"
    request_hash = digest(payload)
    existing = next((a for a in ledger.attempts if a.attempt_id == attempt_id), None)
    if existing:
        if existing.request_digest != request_hash or existing.kind != kind:
            raise ValueError("Attempt identity reused with different request")
        return Admission(ledger=ledger, attempt=existing, is_new=False)
    if not 1 <= attempt_number <= (1 if kind == "count" else 2):
        return Admission(ledger=ledger, attempt=None, is_new=False, denial="attempt_limit")
    if kind == "search" and sum(a.kind == "search" for a in ledger.attempts) >= ledger.max_search_attempts:
        return Admission(ledger=ledger, attempt=None, is_new=False, denial="search_limit")
    denial, cost = None, None
    rates = ledger.rates
    if ledger.ceiling_usd == 0 or ledger.overrun:
        denial = "budget_exhausted"
    elif rates is None:
        denial = "pricing_unknown"
    elif kind == "count":
        if (payload.get("model") != rates.model or rates.count_upper_fee is None
                or not rates.count_source_reference or rates.count_effective_date is None
                or (rates.count_upper_fee == 0 and not rates.count_authoritative_zero)):
            denial = "preflight_price_unproven"
        else:
            cost = rates.count_upper_fee
    elif kind == "search":
        if rates.search_upper_fee is None or not rates.search_source_reference or rates.search_effective_date is None:
            denial = "search_price_unproven"
        else:
            cost = rates.search_upper_fee
    else:
        shared = {k: v for k, v in payload.items() if k not in {"max_output_tokens", "store"}}
        count_attempt = next((a for a in ledger.attempts if count and a.attempt_id == count.attempt_id), None)
        if (count is None or count.shared_request_digest != digest(shared)
                or count.model != payload.get("model") or count.model != rates.model
                or count_attempt is None or count_attempt.kind != "count"
                or count_attempt.operation_id != operation.replace(":generate", ":count")
                or count.operation_id != count_attempt.operation_id
                or count_attempt.outcome is None or count_attempt.outcome.status != "succeeded"
                or count_attempt.outcome.data != {"input_tokens": count.input_tokens}
                or count_attempt.request_digest != count.shared_request_digest
                or count.input_tokens > rates.max_input_tokens
                or not 0 < payload.get("max_output_tokens", 0) <= rates.max_output_tokens):
            denial = "generation_bound_unproven"
        else:
            cost = (Decimal(count.input_tokens) * rates.input_usd_per_million
                    + Decimal(payload["max_output_tokens"]) * rates.output_usd_per_million) / Decimal(1_000_000)
    if denial is None and ledger.settled + ledger.outstanding + cost > ledger.ceiling_usd:
        denial = "budget_exhausted"
    if denial:
        return Admission(ledger=ledger, attempt=None, is_new=False, denial=denial)
    attempt = AttemptRecord(operation_id=operation, attempt_id=attempt_id, kind=kind,
                            request_digest=request_hash, reservation_usd=cost,
                            input_token_bound=count.input_tokens if kind == "generation" else None,
                            output_token_bound=payload["max_output_tokens"] if kind == "generation" else None)
    return Admission(ledger=ledger.model_copy(update={"attempts": [*ledger.attempts, attempt]}),
                     attempt=attempt, is_new=True)


def reconcile(ledger, attempt_id, outcome):
    attempt = next(a for a in ledger.attempts if a.attempt_id == attempt_id)
    if attempt.outcome is not None:
        if attempt.outcome != outcome:
            raise ValueError("Conflicting attempt reconciliation")
        return ledger
    rates, cost = ledger.rates, None
    if attempt.kind == "generation" and outcome.usage.input_tokens is not None and outcome.usage.output_tokens is not None:
        cost = (Decimal(outcome.usage.input_tokens) * rates.input_usd_per_million
                + Decimal(outcome.usage.output_tokens) * rates.output_usd_per_million) / Decimal(1_000_000)
    elif outcome.status == "succeeded":
        cost = rates.count_fixed_fee if attempt.kind == "count" else rates.search_fixed_fee if attempt.kind == "search" else None
        if attempt.kind == "count" and rates.count_authoritative_zero and rates.count_upper_fee == 0:
            cost = Decimal(0)
    token_overrun = attempt.kind == "generation" and (
        (outcome.usage.input_tokens is not None and outcome.usage.input_tokens > attempt.input_token_bound)
        or (outcome.usage.output_tokens is not None and outcome.usage.output_tokens > attempt.output_token_bound))
    updated = attempt.model_copy(update={"state": "settled" if cost is not None else "billing_unknown",
                                         "settled_usd": cost, "outcome": outcome})
    return ledger.model_copy(update={
        "attempts": [updated if a.attempt_id == attempt_id else a for a in ledger.attempts],
        "overrun": ledger.overrun or token_overrun or (cost is not None and cost > attempt.reservation_usd),
    })


def retryable(outcome):
    return outcome.category in {"timeout", "network", "rate_limit", "server"}
