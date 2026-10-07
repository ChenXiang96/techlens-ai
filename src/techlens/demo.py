"""Small package-owned synthetic scenarios. No transports or evaluation/test imports."""
from copy import deepcopy
from datetime import UTC, date, datetime

from techlens.analysis_models import RUBRIC
from techlens.budget import AttemptOutcome, RateCard, Usage
from techlens.github_models import EngineeringSignals
from techlens.models import AssessmentRequest
from techlens.planning import candidate_catalog, request_limits, validate_plan
from techlens.research_models import WebResearchTaskResult, WebSearchResult
from techlens.search import SearchResponseError

NOW = datetime(2026, 10, 5, tzinfo=UTC)


def demo_request(scenario):
    count = 2 if scenario == "comparison" else 1
    return AssessmentRequest.model_validate({
        "assessment_id": f"synthetic-{scenario}",
        "candidates": [{"name": f"Synthetic Project {n}",
                        "repository_url": f"https://github.com/synthetic-example/project-{n}"}
                       for n in range(1, count + 1)],
        "team_profile": {"primary_languages": ["Python"]},
        "constraints": {"max_web_searches": count, "max_candidates": count}})


def demo_rates():
    return RateCard(model="synthetic-local", source_reference="FICTIONAL DEMO ONLY",
        effective_date=date(2026, 10, 5), input_usd_per_million="1", output_usd_per_million="1",
        max_input_tokens=32000, max_output_tokens=8192,
        count_upper_fee="0.002", count_fixed_fee="0.002", count_source_reference="FICTIONAL DEMO COUNT",
        count_effective_date=date(2026, 10, 5), search_upper_fee="0.001", search_fixed_fee="0.001",
        search_source_reference="FICTIONAL DEMO SEARCH", search_effective_date=date(2026, 10, 5))


def synthetic_signals(candidate):
    owner, name = str(candidate.repository_url).rstrip("/").split("/")[-2:]
    count = {"observed": 0, "completeness": "complete"}
    practice = {"detected": False, "evidence_complete": True, "matched_paths": []}
    return EngineeringSignals.model_validate({
        "repository": {"owner": owner, "name": name, "full_name": f"{owner}/{name}", "archived": False,
            "created_at": NOW, "pushed_at": NOW, "default_branch": "main", "license": None, "repository_age_days": 0},
        "releases": {"latest_release_tag": None, "latest_release_published_at": None,
            "releases_last_90d": count, "releases_last_365d": count, "pages_fetched": 1},
        "commits": {"commits_last_30d": count, "commits_last_90d": count, "pages_fetched": 1},
        "issues": {"open_issues_observed": count, "recently_updated_open_issues_90d": count, "pages_fetched": 1},
        "pull_requests": {"open_prs_observed": count, "merged_prs_last_90d": count,
            "open_pages_fetched": 1, "closed_pages_fetched": 1},
        "contributors": {"contributors_observed": count, "total_contributions_observed": 0, "pages_fetched": 1},
        "repository_practices": {"ci": practice, "tests": practice, "security_policy": practice,
            "dependency_automation": practice, "tree_truncated": False, "tree_entries_observed": 0},
        "collection_metadata": {"collected_at": NOW, "github_api_calls_made": 0,
            "authenticated": False, "api_version": "2026-03-10"}})


class CannedPlanner:
    model = "synthetic-local"
    max_output_tokens = 4096

    def __init__(self):
        self.count_calls = self.generation_calls = 0

    def prepare(self, request):
        return {"model": self.model, "instructions": "Canned synthetic plan", "input": request.model_dump(mode="json")}

    def count(self, shared):
        self.count_calls += 1
        return AttemptOutcome(status="succeeded", data={"input_tokens": 100})

    def generate(self, shared):
        self.generation_calls += 1
        request = AssessmentRequest.model_validate(shared["input"])
        ids = [c.candidate_id for c in candidate_catalog(request)]
        return AttemptOutcome(status="succeeded", usage=Usage(input_tokens=100, output_tokens=50), data={
            "objective": "Demonstrate supervised synthetic collection",
            "research_questions": ["What observations are available?"],
            "engineering_checks": ["Repository metadata and practice observations"],
            "comparison_targets": ids, "web_tasks": [
                {"task_id": f"web-{i:03d}", "candidate_id": identity,
                 "query": f"Synthetic documentation {identity}", "purpose": "Demonstrate retrieval provenance"}
                for i, identity in enumerate(ids, 1)], **request_limits(request),
            "max_run_cost_usd": str(request.constraints.max_run_cost_usd)})

    def finalize(self, output, request):
        return validate_plan(output, request)


class CannedAnalyzer(CannedPlanner):
    max_output_tokens = 8192

    def prepare(self, envelope):
        return {"model": self.model, "instructions": "Canned synthetic observations", "input": deepcopy(envelope)}

    def generate(self, shared):
        self.generation_calls += 1
        data = shared["input"]
        dims = [{"dimension": d, "judgment": "insufficient_information", "claims": []} for d in RUBRIC]
        claims = [{"statement": "The synthetic repository metadata records archived as false.",
                   "candidate_ids": [e["candidate_id"]], "evidence_ids": [e["evidence_id"]]}
                  for e in data["evidence"] if e["payload"].get("metric_key") == "repository.archived"]
        comparison = deepcopy(dims) if len(data["candidates"]) > 1 else []
        ids = [c["candidate_id"] for c in data["candidates"]]
        # Only the eligible, visible observations justify this canned comparison.
        metrics = ("repository.archived", "releases.latest_release_tag")
        observations = {(e["candidate_id"], e["payload"].get("metric_key")): e
                        for e in data["evidence"] if e["payload"].get("metric_key") in metrics}
        if comparison and all((identity, metric) in observations for identity in ids for metric in metrics):
            if all(observations[(identity, metrics[0])]["payload"]["value"] is False and
                   observations[(identity, metrics[1])]["payload"]["value"] is None for identity in ids):
                comparison[0] = {"dimension": "maintenance_fit", "judgment": "mixed", "claims": [{
                    "statement": "In this synthetic comparison, both repositories are recorded as unarchived, "
                                 "but neither has a recorded latest release tag. These mixed maintenance "
                                 "observations do not establish overall readiness or a preferred candidate.",
                    "candidate_ids": ids,
                    "evidence_ids": [observations[(identity, metric)]["evidence_id"]
                                     for identity in ids for metric in metrics]}]}
        return AttemptOutcome(status="succeeded", usage=Usage(input_tokens=100, output_tokens=80), data={
            "candidates": [{"candidate_id": c["candidate_id"], "dimensions": dims} for c in data["candidates"]],
            "findings": [{"section_number": 4, "claims": claims}],
            "comparison": comparison})


class CannedSearch:
    def __init__(self, partial=False):
        self.calls = 0
        self.partial = partial

    def search(self, task):
        self.calls += 1
        if self.partial:
            raise SearchResponseError("Synthetic retrieval unavailable")
        return WebResearchTaskResult(task=task, provider="synthetic", credits_used=1, results=[
            WebSearchResult(title="Synthetic documentation", url="https://synthetic.invalid/docs",
                            content="Synthetic retrieved text, not a real source or model assessment.", score=0.5)])


class CannedGitHub:
    def __init__(self):
        self.calls = []

    def __call__(self, candidate):
        self.calls.append(candidate)
        return synthetic_signals(candidate)
