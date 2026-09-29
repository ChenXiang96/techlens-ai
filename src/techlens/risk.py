"""Pure versioned heuristics over integrity-validated observations."""
from datetime import datetime
from decimal import Decimal
from techlens.evidence_models import GitHubEvidencePayload
from techlens.planning import candidate_catalog
from techlens.risk_models import (
    RiskRuleConfig, RiskEvaluation, RiskFlag, EvidenceRiskProvenance, CollectionRiskProvenance,
)

RULES = (
    ("archived", "hard", "The repository is recorded as archived; review maintenance suitability."),
    ("license", "hard", "The collector did not identify a license; clarify licensing."),
    ("access", "operational_block", "GitHub assessment is unavailable for the configured access."),
    ("stale_release", "warning", "The observed latest release meets the configured age threshold."),
    ("low_commits", "warning", "The complete 90-day commit count is below the configured threshold."),
    ("concentration", "warning", "The top contributor share meets the configured concentration threshold."),
    ("security_policy", "warning", "No recognized security-policy path was detected in the complete tree."),
    ("ci", "warning", "No recognized CI path was detected in the complete tree."),
    ("issue_resolution", "warning", "Issue-resolution health cannot be inferred from collected counts."),
)


def apply_risk_rules(request, evidence, coverage, config=RiskRuleConfig()):
    evaluations, flags = [], []
    for candidate in candidate_catalog(request):
        metrics = {r.payload.metric_key: r for r in evidence
                   if r.candidate_id == candidate.candidate_id and isinstance(r.payload, GitHubEvidencePayload)}
        outcome = next(o for o in coverage.github_outcomes if o.target_id == candidate.candidate_id)
        for rule, severity, statement in RULES:
            status, reason, provenance = "not_evaluable", "missing_observation", None
            keys = {
                "archived": ["repository.archived"], "license": ["repository.license"],
                "stale_release": ["releases.latest_release_published_at"],
                "low_commits": ["commits.commits_last_90d"],
                "concentration": ["contributors.top_contributor_share", "contributors.total_contributions_observed"],
                "security_policy": ["repository_practices.security_policy"],
                "ci": ["repository_practices.ci"],
            }.get(rule, [])
            if rule == "access":
                provenance = CollectionRiskProvenance(operation_id=outcome.operation_id)
                status = ("not_triggered" if outcome.status == "succeeded" else
                          "triggered" if outcome.status == "failed" and
                          outcome.error_category == "repository_unavailable" else
                          "not_evaluable")
                reason = outcome.error_category or "collection_succeeded"
            elif rule == "issue_resolution":
                reason = "resolution_metrics_not_collected"
            elif keys and all(k in metrics for k in keys):
                records = [metrics[k] for k in keys]
                provenance = EvidenceRiskProvenance(evidence_ids=[r.evidence_id for r in records])
                p = records[0].payload
                trigger = None
                if rule == "archived":
                    trigger = p.value
                elif rule == "license":
                    trigger = p.value is None or not p.value.strip() or p.value.strip().upper() == "NOASSERTION"
                elif rule == "stale_release" and p.value is not None:
                    age = (datetime.fromisoformat(p.collected_at) - datetime.fromisoformat(p.value)).total_seconds()
                    trigger = age >= config.stale_release_days * 86400 if age >= 0 else None
                elif rule == "low_commits":
                    n = p.value.observed
                    trigger = n < config.low_commits_90d if p.completeness == "complete" or n >= config.low_commits_90d else None
                elif rule == "concentration":
                    if p.completeness == "complete" and p.value is not None and records[1].payload.value > 0:
                        trigger = Decimal(str(p.value)) >= config.top_contributor_share
                elif rule in {"ci", "security_policy"}:
                    trigger = False if p.value else True if p.evidence_complete else None
                status = "not_evaluable" if trigger is None else "triggered" if trigger else "not_triggered"
                reason = "incomplete_or_null" if trigger is None else "predicate_true" if trigger else "predicate_false"
            evaluations.append(RiskEvaluation(rule_id=rule, candidate_id=candidate.candidate_id,
                version=config.version, status=status, reason_code=reason, provenance=provenance))
            if status == "triggered":
                flags.append(RiskFlag(flag_id=f"risk-{len(flags)+1:03d}", rule_id=rule,
                    candidate_id=candidate.candidate_id, severity=severity, statement=statement,
                    provenance=provenance))
    return evaluations, flags
