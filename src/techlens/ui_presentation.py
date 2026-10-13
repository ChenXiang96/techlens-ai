"""Faithful data projections. Browser renders text, not provider or user markup."""
from techlens.planning import candidate_catalog, validate_plan
from techlens.presentation import validated_report, safe_url, DEMO_NOTICE, DISCLAIMER


def request_view(request, public):
    value = request.model_dump(mode="json")
    catalog = [c.model_dump(mode="json") for c in candidate_catalog(request)]
    for c in request.candidates:
        safe_url(str(c.repository_url))
        if c.homepage:
            safe_url(str(c.homepage))
    public.check(value)
    return {"request": value, "candidates": catalog,
            "unknown_context": [key for key, item in value["team_profile"].items() if item in (None, [])],
            "notice": "This prepares input; no repository has been researched. No live permission is granted."}


def plan_view(request, plan, public):
    plan = validate_plan(plan, request)
    result = {"candidates": request_view(request, public)["candidates"],
              "research_plan": plan.model_dump(mode="json"), "notice": DEMO_NOTICE}
    public.check(result)
    return result


def plan_changes(original, proposal):
    """Full positional change inventory, including unchanged immutable fields."""
    before, after = original.model_dump(mode="json"), proposal.model_dump(mode="json")
    return [{"field": field, "before": before[field], "after": after[field],
             "changed": before[field] != after[field]} for field in before]


def report_view(state, public, scenario):
    report, catalog = validated_report(state, public)
    # All typed report fields, no raw evidence, ledger, checkpoint or snippets.
    return {"mode": "demo", "scenario": scenario, "notice": DEMO_NOTICE,
            "disclaimer": DISCLAIMER,
            "accounting_notice": "Usage and prices are fictional. Attempts mean admitted, not confirmed transport. "
                "Missing values are unknown, not zero. Outstanding liability is an estimate, not an invoice. "
                "Elapsed time includes human review wait.",
            "candidates": catalog,
            "report": report.model_dump(mode="json")}
