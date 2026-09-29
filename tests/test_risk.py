from datetime import timedelta
import pytest
from test_evidence import sources
from techlens.collection_models import CollectionCoverage, CollectionOutcome
from techlens.evidence import normalize_records, validate_records
from techlens.risk import apply_risk_rules


def coverage_for(request, plan, *, failed=None):
    return CollectionCoverage(
        github_outcomes=[CollectionOutcome(operation_id=f"github:candidate-{i:03d}", source_kind="github",
            target_id=f"candidate-{i:03d}", status="failed" if failed and i == 1 else "succeeded",
            error_category=failed if i == 1 else None) for i in range(1, len(request.candidates)+1)],
        web_outcomes=[CollectionOutcome(operation_id=f"web:{t.task_id}", source_kind="web", target_id=t.task_id,
                                        status="succeeded") for t in plan.web_tasks])


def evaluate(change=lambda s: None):
    request, plan, github, web = sources()
    change(github[0].engineering_signals)
    records = normalize_records(request, plan, github, web)
    validate_records(request, plan, github, web, records)
    evaluations, flags = apply_risk_rules(request, records, coverage_for(request, plan))
    return {e.rule_id: e for e in evaluations}, flags, records


@pytest.mark.parametrize("archived,expected", [(True, "triggered"), (False, "not_triggered")])
def test_archived_is_a_review_flag(archived, expected):
    results, flags, records = evaluate(lambda s: setattr(s.repository, "archived", archived))
    assert results["archived"].status == expected
    if archived:
        flag = next(f for f in flags if f.rule_id == "archived")
        assert flag.severity == "hard" and flag.provenance.evidence_ids == [records[0].evidence_id]


@pytest.mark.parametrize("license_value,expected", [(None, "triggered"), ("", "triggered"),
    ("  ", "triggered"), ("NOASSERTION", "triggered"), ("MIT", "not_triggered")])
def test_license_representation_is_preserved(license_value, expected):
    results, _, _ = evaluate(lambda s: setattr(s.repository, "license", license_value))
    assert results["license"].status == expected


@pytest.mark.parametrize("days,expected", [(None, "not_evaluable"), (364, "not_triggered"),
    (365, "triggered"), (366, "triggered"), (-1, "not_evaluable")])
def test_release_uses_snapshot_time(days, expected):
    def change(signals):
        signals.releases.latest_release_published_at = None if days is None else signals.collection_metadata.collected_at - timedelta(days=days)
    results, _, _ = evaluate(change)
    assert results["stale_release"].status == expected


@pytest.mark.parametrize("observed,complete,expected", [(4, "complete", "triggered"), (5, "complete", "not_triggered"),
    (4, "truncated", "not_evaluable"), (5, "truncated", "not_triggered")])
def test_commit_lower_bound_semantics(observed, complete, expected):
    def change(s):
        s.commits.commits_last_90d.observed = observed
        s.commits.commits_last_90d.completeness = complete
    results, _, _ = evaluate(change)
    assert results["low_commits"].status == expected


@pytest.mark.parametrize("share,total,complete,expected", [(.8, 100, "complete", "triggered"),
    (.79, 100, "complete", "not_triggered"), (.9, 100, "truncated", "not_evaluable"),
    (None, 0, "complete", "not_evaluable"), (.9, 0, "complete", "not_evaluable")])
def test_contributor_observed_population(share, total, complete, expected):
    def change(s):
        s.contributors.top_contributor_share = share
        s.contributors.total_contributions_observed = total
        s.contributors.contributors_observed.completeness = complete
    results, _, _ = evaluate(change)
    assert results["concentration"].status == expected


@pytest.mark.parametrize("truncated,detected,expected", [(False, False, "triggered"),
    (True, False, "not_evaluable"), (True, True, "not_triggered"), (False, True, "not_triggered")])
def test_practice_paths_do_not_infer_absence_from_incomplete_tree(truncated, detected, expected):
    def change(s):
        s.repository_practices.tree_truncated = truncated
        for field in ("ci", "tests", "security_policy", "dependency_automation"):
            p = getattr(s.repository_practices, field)
            p.evidence_complete = not truncated
            p.detected = detected
            p.matched_paths = ["test-path"] if detected else []
    results, _, _ = evaluate(change)
    assert results["ci"].status == results["security_policy"].status == expected
    assert results["issue_resolution"].status == "not_evaluable"


@pytest.mark.parametrize("category,status", [
    ("repository_unavailable", "triggered"),
    ("authentication_or_permission", "not_evaluable"),
    ("rate_limit", "not_evaluable"),
    ("timeout", "not_evaluable"),
    ("network", "not_evaluable"),
    ("provider_response", "not_evaluable"),
])
def test_failed_collection_preserves_category_without_inventing_repository_unavailability(category, status):
    request, plan, github, web = sources()
    coverage = coverage_for(request, plan, failed=category)
    records = normalize_records(request, plan, [], web, coverage)
    evaluations, flags = apply_risk_rules(request, records, coverage)
    access = next(e for e in evaluations if e.rule_id == "access")
    assert access.status == status and access.reason_code == category
    assert access.provenance.kind == "collection" and access.provenance.operation_id == "github:candidate-001"
    access_flags = [f for f in flags if f.rule_id == "access"]
    assert len(access_flags) == (1 if status == "triggered" else 0)
    if access_flags:
        assert access_flags[0].provenance == access.provenance
    assert all(e.status == "not_evaluable" for e in evaluations if e.rule_id != "access")


def test_successful_collection_does_not_trigger_access_risk():
    results, flags, _ = evaluate()
    assert results["access"].status == "not_triggered"
    assert results["access"].reason_code == "collection_succeeded"
    assert not [flag for flag in flags if flag.rule_id == "access"]


@pytest.mark.parametrize("status,category", [("skipped_budget", "budget_exhausted"),
                                             ("skipped_limit", "search_limit"),
                                             ("skipped_budget", "repository_unavailable")])
def test_skipped_collection_is_not_repository_unavailability(status, category):
    request, plan, _, web = sources()
    coverage = coverage_for(request, plan, failed="timeout")
    coverage.github_outcomes[0] = coverage.github_outcomes[0].model_copy(
        update={"status": status, "error_category": category})
    records = normalize_records(request, plan, [], web, coverage)
    evaluations, flags = apply_risk_rules(request, records, coverage)
    access = next(e for e in evaluations if e.rule_id == "access")
    assert access.status == "not_evaluable" and access.reason_code == category
    assert access.provenance.operation_id == "github:candidate-001"
    assert not [f for f in flags if f.rule_id == "access"]
