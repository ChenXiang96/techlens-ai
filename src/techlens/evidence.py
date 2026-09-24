"""Pure evidence normalization and independent source-integrity checks."""

from datetime import UTC, datetime
from typing import Literal, NamedTuple, Sequence

from pydantic import ValidationError

from techlens.evidence_models import (
    BoundedObservedValue, ClaimRecord, EvidenceRecord, EvidenceValidationSummary,
    GitHubEvidencePayload, WebEvidencePayload,
)
from techlens.github_models import BoundedCount, EngineeringSignals, GitHubRepositoryRef, PracticeIndicator
from techlens.models import AssessmentRequest, ResearchPlan
from techlens.planning import PlannerError, candidate_catalog, validate_plan
from techlens.research_models import CandidateEngineeringSignals, WebResearchTaskResult


class EvidenceError(Exception):
    """Evidence processing failed without retaining external source objects."""


class EvidenceNormalizationError(EvidenceError):
    pass


class EvidenceValidationError(EvidenceError):
    pass


class ClaimEvidenceContractError(EvidenceError):
    pass


MetricKind = Literal["scalar", "count", "contributor", "practice"]


class MetricSpec(NamedTuple):
    key: str
    title: str
    path: tuple[str, str]
    kind: MetricKind


# This tuple, including its titles, is the canonical GitHub evidence order.
GITHUB_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("repository.archived", "GitHub repository archived", ("repository", "archived"), "scalar"),
    MetricSpec("repository.created_at", "GitHub repository created at", ("repository", "created_at"), "scalar"),
    MetricSpec("repository.pushed_at", "GitHub repository pushed at", ("repository", "pushed_at"), "scalar"),
    MetricSpec("repository.default_branch", "GitHub repository default branch", ("repository", "default_branch"), "scalar"),
    MetricSpec("repository.license", "GitHub repository license field", ("repository", "license"), "scalar"),
    MetricSpec("repository.repository_age_days", "GitHub repository age in days", ("repository", "repository_age_days"), "scalar"),
    MetricSpec("releases.latest_release_tag", "GitHub latest release tag", ("releases", "latest_release_tag"), "scalar"),
    MetricSpec("releases.latest_release_published_at", "GitHub latest release published at", ("releases", "latest_release_published_at"), "scalar"),
    MetricSpec("releases.releases_last_90d", "GitHub releases observed in 90 days", ("releases", "releases_last_90d"), "count"),
    MetricSpec("releases.releases_last_365d", "GitHub releases observed in 365 days", ("releases", "releases_last_365d"), "count"),
    MetricSpec("commits.commits_last_30d", "GitHub commits observed in 30 days", ("commits", "commits_last_30d"), "count"),
    MetricSpec("commits.commits_last_90d", "GitHub commits observed in 90 days", ("commits", "commits_last_90d"), "count"),
    MetricSpec("issues.open_issues_observed", "GitHub open issues observed", ("issues", "open_issues_observed"), "count"),
    MetricSpec("issues.recently_updated_open_issues_90d", "GitHub open issues updated in 90 days", ("issues", "recently_updated_open_issues_90d"), "count"),
    MetricSpec("pull_requests.open_prs_observed", "GitHub open pull requests observed", ("pull_requests", "open_prs_observed"), "count"),
    MetricSpec("pull_requests.merged_prs_last_90d", "GitHub merged pull requests observed in 90 days", ("pull_requests", "merged_prs_last_90d"), "count"),
    MetricSpec("contributors.contributors_observed", "GitHub contributors observed", ("contributors", "contributors_observed"), "count"),
    MetricSpec("contributors.total_contributions_observed", "GitHub contributions by observed contributors", ("contributors", "total_contributions_observed"), "contributor"),
    MetricSpec("contributors.top_contributor_share", "GitHub top share of observed contributions", ("contributors", "top_contributor_share"), "contributor"),
    MetricSpec("contributors.top_3_contributor_share", "GitHub top three share of observed contributions", ("contributors", "top_3_contributor_share"), "contributor"),
    MetricSpec("repository_practices.ci", "GitHub CI path observation", ("repository_practices", "ci"), "practice"),
    MetricSpec("repository_practices.tests", "GitHub test path observation", ("repository_practices", "tests"), "practice"),
    MetricSpec("repository_practices.security_policy", "GitHub security policy path observation", ("repository_practices", "security_policy"), "practice"),
    MetricSpec("repository_practices.dependency_automation", "GitHub dependency automation path observation", ("repository_practices", "dependency_automation"), "practice"),
)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _field(signals: EngineeringSignals, spec: MetricSpec) -> object:
    return getattr(getattr(signals, spec.path[0]), spec.path[1])


def _checked_sources(
    request: AssessmentRequest,
    plan: ResearchPlan,
    github: Sequence[CandidateEngineeringSignals],
    web: Sequence[WebResearchTaskResult],
    error_type: type[EvidenceError],
) -> tuple[list[CandidateEngineeringSignals], list[WebResearchTaskResult]]:
    invalid = False
    try:
        request = AssessmentRequest.model_validate(request.model_dump())
        validate_plan(plan, request)
        catalog = candidate_catalog(request)
        checked_github = [CandidateEngineeringSignals.model_validate(item.model_dump()) for item in github]
        checked_web = [WebResearchTaskResult.model_validate(item.model_dump()) for item in web]
        if len(checked_github) != len(catalog) or len(checked_web) != len(plan.web_tasks):
            raise ValueError("Collection coverage differs from the request or plan")
        for entry, candidate, collected in zip(catalog, request.candidates, checked_github, strict=True):
            ref = GitHubRepositoryRef.from_candidate(candidate)
            repo = collected.engineering_signals.repository
            practices = collected.engineering_signals.repository_practices
            if (collected.candidate_id != entry.candidate_id
                    or collected.candidate_name != candidate.name
                    or collected.repository_url != candidate.repository_url
                    or repo.owner.casefold() != ref.owner.casefold()
                    or repo.name.casefold() != ref.repository.casefold()
                    or repo.full_name.casefold() != f"{ref.owner}/{ref.repository}".casefold()):
                raise ValueError("GitHub source does not match the original candidate")
            if any(indicator.evidence_complete != (not practices.tree_truncated)
                   for indicator in (practices.ci, practices.tests,
                                     practices.security_policy, practices.dependency_automation)):
                raise ValueError("Repository practice completeness differs from tree coverage")
        for task, retrieved in zip(plan.web_tasks, checked_web, strict=True):
            if retrieved.task != task:
                raise ValueError("Web result does not match its approved task")
    except (ValidationError, PlannerError, ValueError, TypeError, AttributeError):
        invalid = True
    if invalid:
        raise error_type("Collected research source integrity failed")
    return checked_github, checked_web


def _github_payload(signals: EngineeringSignals, spec: MetricSpec) -> GitHubEvidencePayload:
    source = _field(signals, spec)
    fields: dict[str, object] = {
        "metric_key": spec.key,
        "collected_at": _utc_iso(signals.collection_metadata.collected_at),
        "evidence_complete": None,
        "matched_paths": [],
        "population_scope": None,
    }
    if spec.kind == "count":
        assert isinstance(source, BoundedCount)
        fields.update(value=BoundedObservedValue(observed=source.observed),
                      completeness=source.completeness)
    elif spec.kind == "contributor":
        fields.update(value=source,
                      completeness=signals.contributors.contributors_observed.completeness,
                      population_scope="observed_contributors")
    elif spec.kind == "practice":
        assert isinstance(source, PracticeIndicator)
        completeness = "complete" if source.evidence_complete else "truncated" if source.detected else "unknown"
        fields.update(value=source.detected, completeness=completeness,
                      evidence_complete=source.evidence_complete,
                      matched_paths=list(source.matched_paths))
    else:
        fields.update(value=_utc_iso(source) if isinstance(source, datetime) else source,
                      completeness="complete")
    return GitHubEvidencePayload.model_validate(fields)


def normalize_records(
    request: AssessmentRequest,
    plan: ResearchPlan,
    github: Sequence[CandidateEngineeringSignals],
    web: Sequence[WebResearchTaskResult],
) -> list[EvidenceRecord]:
    """Copy selected source observations without interpretation or deduplication."""
    checked_github, checked_web = _checked_sources(
        request, plan, github, web, EvidenceNormalizationError,
    )
    records: list[EvidenceRecord] = []
    invalid = False
    try:
        for entry, candidate, collected in zip(
            candidate_catalog(request), request.candidates, checked_github, strict=True,
        ):
            for spec in GITHUB_METRICS:
                records.append(EvidenceRecord(
                    evidence_id=f"evidence-{len(records) + 1:03d}",
                    candidate_id=entry.candidate_id,
                    source_kind="github",
                    title=spec.title,
                    source_url=str(candidate.repository_url),
                    payload=_github_payload(collected.engineering_signals, spec),
                ))
        for retrieved in checked_web:
            for result in retrieved.results:
                records.append(EvidenceRecord(
                    evidence_id=f"evidence-{len(records) + 1:03d}",
                    candidate_id=retrieved.task.candidate_id,
                    source_kind="web",
                    title=result.title,
                    source_url=result.url,
                    payload=WebEvidencePayload(
                        task_id=retrieved.task.task_id,
                        query=retrieved.task.query,
                        provider=retrieved.provider,
                        content=result.content,
                        score=result.score,
                        published_date=result.published_date,
                    ),
                ))
    except (ValidationError, ValueError, TypeError, AttributeError, AssertionError):
        invalid = True
    if invalid:
        raise EvidenceNormalizationError("Evidence normalization failed")
    expected = 24 * len(request.candidates) + sum(len(item.results) for item in checked_web)
    if len(GITHUB_METRICS) != 24 or len(records) != expected:
        raise EvidenceNormalizationError("Evidence coverage count is incomplete")
    return records


def _validate_github_record(
    record: EvidenceRecord,
    spec: MetricSpec,
    candidate_id: str,
    repository_url: str,
    signals: EngineeringSignals,
) -> None:
    if (record.source_kind != "github" or record.candidate_id != candidate_id
            or record.source_url != repository_url or record.title != spec.title
            or not isinstance(record.payload, GitHubEvidencePayload)
            or record.payload.metric_key != spec.key):
        raise EvidenceValidationError("GitHub evidence identity or provenance differs from source")
    payload = record.payload
    try:
        if payload.collected_at != _utc_iso(signals.collection_metadata.collected_at):
            raise ValueError("Snapshot timestamp differs")
        source = _field(signals, spec)
        if spec.kind == "count":
            valid = (isinstance(source, BoundedCount)
                     and isinstance(payload.value, BoundedObservedValue)
                     and payload.value.observed == source.observed
                     and payload.completeness == source.completeness)
        elif spec.kind == "contributor":
            valid = (payload.value == source
                     and payload.completeness == signals.contributors.contributors_observed.completeness
                     and payload.population_scope == "observed_contributors")
        elif spec.kind == "practice":
            valid = (isinstance(source, PracticeIndicator)
                     and payload.value is source.detected
                     and payload.evidence_complete is source.evidence_complete
                     and payload.matched_paths == source.matched_paths
                     and payload.completeness == (
                         "complete" if source.evidence_complete else "truncated" if source.detected else "unknown"
                     ))
        else:
            expected = _utc_iso(source) if isinstance(source, datetime) else source
            valid = payload.value == expected and payload.completeness == "complete"
    except (ValueError, TypeError, AttributeError):
        raise EvidenceValidationError("GitHub evidence value differs from source") from None
    if not valid:
        raise EvidenceValidationError("GitHub evidence value differs from source")


def _validate_web_record(record: EvidenceRecord, retrieved: WebResearchTaskResult, result: object) -> None:
    if (record.source_kind != "web"
            or record.candidate_id != retrieved.task.candidate_id
            or record.title != result.title
            or record.source_url != result.url
            or not isinstance(record.payload, WebEvidencePayload)):
        raise EvidenceValidationError("Web evidence identity or provenance differs from source")
    payload = record.payload
    if (payload.task_id != retrieved.task.task_id or payload.query != retrieved.task.query
            or payload.provider != retrieved.provider or payload.content != result.content
            or payload.score != result.score or payload.published_date != result.published_date):
        raise EvidenceValidationError("Web evidence value differs from source")


def validate_records(
    request: AssessmentRequest,
    plan: ResearchPlan,
    github: Sequence[CandidateEngineeringSignals],
    web: Sequence[WebResearchTaskResult],
    evidence: Sequence[EvidenceRecord],
) -> EvidenceValidationSummary:
    """Inspect each supplied record against its original source, without renormalizing."""
    checked_github, checked_web = _checked_sources(
        request, plan, github, web, EvidenceValidationError,
    )
    invalid = False
    try:
        records = [EvidenceRecord.model_validate(
            item.model_dump() if isinstance(item, EvidenceRecord) else item
        ) for item in evidence]
    except (ValidationError, ValueError, TypeError, AttributeError):
        invalid = True
    if invalid:
        raise EvidenceValidationError("Malformed evidence record")
    expected_github = 24 * len(request.candidates)
    expected_web = sum(len(item.results) for item in checked_web)
    if len(GITHUB_METRICS) != 24 or len(records) != expected_github + expected_web:
        raise EvidenceValidationError("Evidence source coverage count differs")
    valid_ids = {entry.candidate_id for entry in candidate_catalog(request)}
    ids = [record.evidence_id for record in records]
    if (len(set(ids)) != len(ids)
            or ids != [f"evidence-{index:03d}" for index in range(1, len(records) + 1)]
            or any(record.candidate_id not in valid_ids | {None} for record in records)):
        raise EvidenceValidationError("Evidence IDs or candidate associations are invalid")

    position = 0
    for entry, candidate, collected in zip(
        candidate_catalog(request), request.candidates, checked_github, strict=True,
    ):
        for spec in GITHUB_METRICS:
            _validate_github_record(
                records[position], spec, entry.candidate_id,
                str(candidate.repository_url), collected.engineering_signals,
            )
            position += 1
    for retrieved in checked_web:
        for result in retrieved.results:
            _validate_web_record(records[position], retrieved, result)
            position += 1
    if position != len(records):
        raise EvidenceValidationError("Evidence source coverage is incomplete")
    incomplete = sum(
        record.payload.completeness != "complete"
        for record in records[:expected_github]
        if isinstance(record.payload, GitHubEvidencePayload)
    )
    return EvidenceValidationSummary(
        total_records=len(records), github_records=expected_github,
        web_records=expected_web, incomplete_github_records=incomplete,
        untrusted_web_records=expected_web,
    )


def validate_claim_evidence_contract(
    claims: Sequence[ClaimRecord],
    evidence: Sequence[EvidenceRecord],
    request: AssessmentRequest,
) -> None:
    """Check IDs and associations only; never assess statement truth or support."""
    invalid = False
    try:
        request = AssessmentRequest.model_validate(request.model_dump())
        records = [EvidenceRecord.model_validate(
            item.model_dump() if isinstance(item, EvidenceRecord) else item
        ) for item in evidence]
        checked_claims = [ClaimRecord.model_validate(
            item.model_dump() if isinstance(item, ClaimRecord) else item
        ) for item in claims]
    except (ValidationError, ValueError, TypeError, AttributeError):
        invalid = True
    if invalid:
        raise ClaimEvidenceContractError("Invalid claim or evidence structure")
    evidence_by_id = {item.evidence_id: item for item in records}
    allowed_candidates = {item.candidate_id for item in candidate_catalog(request)}
    if len(evidence_by_id) != len(records):
        raise ClaimEvidenceContractError("Evidence IDs must be unique")
    if any(item.candidate_id not in allowed_candidates | {None} for item in records):
        raise ClaimEvidenceContractError("Evidence candidate IDs are invalid")
    for index, claim in enumerate(checked_claims, start=1):
        if claim.claim_id != f"claim-{index:03d}":
            raise ClaimEvidenceContractError("Claim IDs must be canonical")
        if (len(set(claim.candidate_ids)) != len(claim.candidate_ids)
                or any(candidate_id not in allowed_candidates for candidate_id in claim.candidate_ids)):
            raise ClaimEvidenceContractError("Claim candidate IDs are invalid")
        if len(set(claim.evidence_ids)) != len(claim.evidence_ids):
            raise ClaimEvidenceContractError("Claim evidence IDs must be unique")
        for evidence_id in claim.evidence_ids:
            record = evidence_by_id.get(evidence_id)
            if record is None:
                raise ClaimEvidenceContractError("Claim references unknown evidence")
            if (record.candidate_id is not None
                    and record.candidate_id not in claim.candidate_ids):
                raise ClaimEvidenceContractError("Claim and evidence candidate IDs are incompatible")
