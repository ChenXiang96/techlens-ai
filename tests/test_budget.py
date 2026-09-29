from decimal import Decimal
import pytest
from fakes import fictional_rates
from techlens.budget import (BudgetLedger, reserve, reconcile, AttemptOutcome, Usage,
                            VerifiedInputCount, digest)

SHARED = {"model": "gpt-5.6-luna", "instructions": "fixed", "input": [],
          "text": {"format": {"type": "json_schema", "strict": True, "schema": {}}}, "truncation": "disabled"}
GEN = {**SHARED, "max_output_tokens": 9900, "store": False}


def ledger(ceiling=".025", **kwargs):
    return BudgetLedger(run_id="run", ceiling_usd=Decimal(ceiling),
                        rates=fictional_rates(max_output_tokens=10000, **kwargs))


def counted(value):
    admission = reserve(value, "planner:count", "count", SHARED)
    value = reconcile(admission.ledger, admission.attempt.attempt_id,
                      AttemptOutcome(status="succeeded", data={"input_tokens": 100}))
    receipt = VerifiedInputCount(operation_id="planner:count", attempt_id=admission.attempt.attempt_id,
        model=SHARED["model"], input_tokens=100, shared_request_digest=digest(SHARED))
    return value, receipt


@pytest.mark.parametrize("kwargs", [dict(count_upper_fee=None, count_fixed_fee=None),
    dict(count_source_reference=None), dict(count_effective_date=None),
    dict(count_upper_fee=Decimal(0), count_fixed_fee=Decimal(0))])
def test_preflight_fee_must_be_independently_justified(kwargs):
    admission = reserve(ledger(**kwargs), "planner:count", "count", SHARED)
    assert admission.denial == "preflight_price_unproven" and not admission.is_new
    assert admission.ledger.attempts == []


def test_zero_budget_and_authoritative_zero_fee():
    assert reserve(ledger("0"), "planner:count", "count", SHARED).denial == "budget_exhausted"
    value, _ = counted(ledger(count_upper_fee=Decimal(0), count_fixed_fee=None, count_authoritative_zero=True))
    assert value.attempts[0].state == "settled" and value.settled == 0


def test_successful_preflight_then_generation_denial():
    value, count = counted(ledger(".008"))
    assert value.settled == Decimal(".002") and value.outstanding == 0
    admission = reserve(value, "planner:generate", "generation", GEN, count=count)
    assert admission.denial == "budget_exhausted" and len(admission.ledger.attempts) == 1


def test_preflight_timeout_retains_fee_no_usable_receipt():
    admission = reserve(ledger(), "planner:count", "count", SHARED)
    value = reconcile(admission.ledger, admission.attempt.attempt_id, AttemptOutcome(status="failed", category="timeout"))
    assert value.outstanding == Decimal(".002") and value.settled == 0
    forged = VerifiedInputCount(operation_id="planner:count", attempt_id=admission.attempt.attempt_id,
        model=SHARED["model"], input_tokens=100, shared_request_digest=digest(SHARED))
    assert reserve(value, "planner:generate", "generation", GEN, count=forged).denial == "generation_bound_unproven"


def test_successful_count_with_unknown_fee_remains_usable():
    value, count = counted(ledger(count_fixed_fee=None))
    assert value.outstanding == Decimal(".002")
    assert reserve(value, "planner:generate", "generation", GEN, count=count).is_new


def test_generation_retry_reuses_count_but_reserves_again_idempotently():
    value, count = counted(ledger())
    first = reserve(value, "planner:generate", "generation", GEN, count=count)
    repeated = reserve(first.ledger, "planner:generate", "generation", GEN, count=count)
    assert first.is_new and not repeated.is_new and repeated.ledger == first.ledger
    failed = AttemptOutcome(status="failed", category="timeout")
    value = reconcile(first.ledger, first.attempt.attempt_id, failed)
    assert reconcile(value, first.attempt.attempt_id, failed) == value
    second = reserve(value, "planner:generate", "generation", GEN, count=count, attempt_number=2)
    assert second.is_new and second.ledger.settled == Decimal(".002")
    assert second.ledger.outstanding == Decimal(".020")
    success = AttemptOutcome(status="succeeded", usage=Usage(input_tokens=100, output_tokens=3900))
    final = reconcile(second.ledger, second.attempt.attempt_id, success)
    assert final.settled == Decimal(".006") and final.outstanding == Decimal(".010")
    assert len([a for a in final.attempts if a.kind == "count"]) == 1


@pytest.mark.parametrize("field,value", [("instructions", "changed"), ("input", ["changed"]),
    ("model", "other"), ("text", {}), ("truncation", "auto"), ("reasoning", {"effort": "low"})])
def test_all_changed_token_relevant_settings_invalidate_receipt(field, value):
    current, count = counted(ledger())
    assert reserve(current, "planner:generate", "generation", {**GEN, field: value}, count=count).denial == "generation_bound_unproven"


def test_overrun_is_visible_and_stops_new_calls():
    value, count = counted(ledger())
    admitted = reserve(value, "planner:generate", "generation", GEN, count=count)
    value = reconcile(admitted.ledger, admitted.attempt.attempt_id,
        AttemptOutcome(status="succeeded", usage=Usage(input_tokens=101, output_tokens=10000)))
    assert value.overrun and value.settled > Decimal(".012")
    assert reserve(value, "planner:generate", "generation", GEN, count=count, attempt_number=2).denial == "budget_exhausted"


def test_conflicting_reconciliation_and_request_identity_rejected():
    value, _ = counted(ledger())
    with pytest.raises(ValueError, match="Conflicting"):
        reconcile(value, value.attempts[0].attempt_id, AttemptOutcome(status="failed", category="network"))
    with pytest.raises(ValueError, match="different request"):
        reserve(value, "planner:count", "count", {**SHARED, "instructions": "changed"})
