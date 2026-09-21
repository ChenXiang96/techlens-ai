from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
import pytest

from techlens.github_client import (
    GitHubAuthenticationError,
    GitHubRateLimitError,
    GitHubRepositoryUnavailableError,
    GitHubResponseError,
    GitHubTransportError,
    collect_engineering_signals,
)
from techlens.github_models import EngineeringSignals
from techlens.models import TechnologyCandidate

Handler = Callable[[httpx.Request], httpx.Response]


def make_scenario_handler(
    payloads: dict[str, object],
    captured_requests: list[httpx.Request],
    *,
    truncated_endpoint: str | None = None,
) -> Handler:
    rate_headers = {
        "x-ratelimit-limit": "60",
        "x-ratelimit-remaining": "42",
        "x-ratelimit-reset": "1790000000",
    }

    def response_for_list(
        request: httpx.Request,
        endpoint: str,
        payload_key: str,
    ) -> httpx.Response:
        headers = dict(rate_headers)
        if endpoint == truncated_endpoint:
            headers["link"] = (
                '<https://api.github.com/next?page=3>; rel="next"'
            )
        return httpx.Response(
            200,
            headers=headers,
            json=payloads[payload_key],
        )

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        path = request.url.path

        if path == "/repos/example/project":
            return httpx.Response(200, headers=rate_headers, json=payloads["repository"])
        if path == "/repos/example/project/releases/latest":
            latest = payloads["latest_release"]
            if latest is None:
                return httpx.Response(404, headers=rate_headers, json={"message": "Not Found"})
            return httpx.Response(200, headers=rate_headers, json=latest)
        if path == "/repos/example/project/releases":
            return response_for_list(request, "releases", "releases")
        if path == "/repos/example/project/commits":
            return response_for_list(request, "commits", "commits")
        if path == "/repos/example/project/issues":
            return response_for_list(request, "issues", "issues")
        if path == "/repos/example/project/pulls":
            if request.url.params["state"] == "open":
                return response_for_list(request, "open_pull_requests", "open_pulls")
            return response_for_list(request, "closed_pull_requests", "closed_pulls")
        if path == "/repos/example/project/contributors":
            return response_for_list(request, "contributors", "contributors")
        if path.startswith("/repos/example/project/git/trees/"):
            return httpx.Response(200, headers=rate_headers, json=payloads["tree"])
        raise AssertionError(f"Unexpected mocked request: {request.url}")

    return handler


def collect_with_handler(
    candidate: TechnologyCandidate,
    fixed_now: datetime,
    handler: Handler,
    *,
    token: str | None = None,
) -> EngineeringSignals:
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return collect_engineering_signals(
            candidate,
            token=token,
            http_client=client,
            collected_at=fixed_now,
        )


def test_collects_normalized_signals_without_authentication(
    github_candidate: TechnologyCandidate,
    github_payloads: dict[str, object],
    fixed_now: datetime,
) -> None:
    requests: list[httpx.Request] = []
    handler = make_scenario_handler(github_payloads, requests)

    signals = collect_with_handler(github_candidate, fixed_now, handler)

    assert isinstance(signals, EngineeringSignals)
    assert signals.repository.full_name == "example/project"
    assert signals.repository.archived is False
    assert signals.repository.license == "MIT"
    assert signals.releases.latest_release_tag == "v2.0.0"
    assert signals.releases.releases_last_90d.observed == 1
    assert signals.releases.releases_last_365d.observed == 2
    assert signals.commits.commits_last_30d.observed == 1
    assert signals.commits.commits_last_90d.observed == 2
    assert signals.issues.open_issues_observed.observed == 2
    assert signals.issues.recently_updated_open_issues_90d.observed == 1
    assert signals.pull_requests.open_prs_observed.observed == 2
    assert signals.pull_requests.merged_prs_last_90d.observed == 1
    assert signals.contributors.top_contributor_share == pytest.approx(0.7)
    assert signals.contributors.top_3_contributor_share == pytest.approx(1.0)
    assert signals.repository_practices.ci.detected is True
    assert signals.repository_practices.tests.detected is True
    assert signals.repository_practices.security_policy.detected is True
    assert signals.repository_practices.dependency_automation.detected is True
    assert signals.collection_metadata.github_api_calls_made == 9
    assert signals.collection_metadata.authenticated is False
    assert signals.collection_metadata.rate_limit_remaining == 42
    assert signals.collection_metadata.truncated_endpoints == []

    assert all(request.headers["accept"] == "application/vnd.github+json" for request in requests)
    assert all(request.headers["user-agent"] == "TechLens" for request in requests)
    assert all(request.headers["x-github-api-version"] == "2026-03-10" for request in requests)
    assert all("authorization" not in request.headers for request in requests)


def test_archived_repository_and_missing_license_are_preserved(
    github_candidate: TechnologyCandidate,
    github_payloads: dict[str, object],
    fixed_now: datetime,
) -> None:
    repository = github_payloads["repository"]
    assert isinstance(repository, dict)
    repository["archived"] = True
    repository["license"] = None
    handler = make_scenario_handler(github_payloads, [])

    signals = collect_with_handler(github_candidate, fixed_now, handler)

    assert signals.repository.archived is True
    assert signals.repository.license is None


def test_no_releases_is_legitimate_and_latest_404_counts_as_api_call(
    github_candidate: TechnologyCandidate,
    github_payloads: dict[str, object],
    fixed_now: datetime,
) -> None:
    github_payloads["latest_release"] = None
    github_payloads["releases"] = []
    requests: list[httpx.Request] = []
    handler = make_scenario_handler(github_payloads, requests)

    signals = collect_with_handler(github_candidate, fixed_now, handler)

    assert signals.releases.latest_release_tag is None
    assert signals.releases.latest_release_published_at is None
    assert signals.releases.releases_last_90d.observed == 0
    assert signals.releases.releases_last_90d.completeness == "complete"
    assert signals.collection_metadata.github_api_calls_made == 9
    assert sum(request.url.path.endswith("/releases/latest") for request in requests) == 1


def test_page_cap_marks_observations_as_truncated(
    github_candidate: TechnologyCandidate,
    github_payloads: dict[str, object],
    fixed_now: datetime,
) -> None:
    requests: list[httpx.Request] = []
    handler = make_scenario_handler(
        github_payloads,
        requests,
        truncated_endpoint="commits",
    )

    signals = collect_with_handler(github_candidate, fixed_now, handler)

    assert signals.commits.pages_fetched == 2
    assert signals.commits.commits_last_30d.observed == 2
    assert signals.commits.commits_last_30d.completeness == "truncated"
    assert signals.commits.commits_last_90d.observed == 4
    assert signals.commits.commits_last_90d.completeness == "truncated"
    assert "commits" in signals.collection_metadata.truncated_endpoints
    commit_requests = [request for request in requests if request.url.path.endswith("/commits")]
    assert len(commit_requests) == 2
    assert commit_requests[-1].url.params["page"] == "2"


def test_contributor_proxy_is_explicitly_over_truncated_population(
    github_candidate: TechnologyCandidate,
    github_payloads: dict[str, object],
    fixed_now: datetime,
) -> None:
    handler = make_scenario_handler(
        github_payloads,
        [],
        truncated_endpoint="contributors",
    )

    signals = collect_with_handler(github_candidate, fixed_now, handler)

    assert signals.contributors.contributors_observed.observed == 6
    assert signals.contributors.contributors_observed.completeness == "truncated"
    assert signals.contributors.top_contributor_share == pytest.approx(0.35)
    assert signals.contributors.top_3_contributor_share == pytest.approx(0.8)
    assert "contributors" in signals.collection_metadata.truncated_endpoints


def test_truncated_tree_preserves_positive_matches_and_unknown_absence(
    github_candidate: TechnologyCandidate,
    github_payloads: dict[str, object],
    fixed_now: datetime,
) -> None:
    github_payloads["tree"] = {
        "truncated": True,
        "tree": [{"type": "blob", "path": "SECURITY.md"}],
    }
    handler = make_scenario_handler(github_payloads, [])

    signals = collect_with_handler(github_candidate, fixed_now, handler)

    practices = signals.repository_practices
    assert practices.security_policy.detected is True
    assert practices.security_policy.evidence_complete is False
    assert practices.ci.detected is False
    assert practices.ci.evidence_complete is False
    assert "repository_tree" in signals.collection_metadata.truncated_endpoints


def test_tree_ref_with_slash_is_encoded_as_one_path_component(
    github_candidate: TechnologyCandidate,
    github_payloads: dict[str, object],
    fixed_now: datetime,
) -> None:
    repository = github_payloads["repository"]
    assert isinstance(repository, dict)
    repository["default_branch"] = "feature/ci"
    requests: list[httpx.Request] = []
    handler = make_scenario_handler(github_payloads, requests)

    collect_with_handler(github_candidate, fixed_now, handler)

    tree_request = next(
        request for request in requests if "/git/trees/" in request.url.path
    )
    assert b"feature%2Fci" in tree_request.url.raw_path


def test_explicit_token_precedes_environment_and_never_leaks(
    github_candidate: TechnologyCandidate,
    fixed_now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "environment-unit-test-sentinel")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401, json={"message": "Bad credentials"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubAuthenticationError) as caught:
            collect_engineering_signals(
                github_candidate,
                token="explicit-unit-test-sentinel",
                http_client=client,
                collected_at=fixed_now,
            )

    assert requests[0].headers["authorization"] == "Bearer explicit-unit-test-sentinel"
    assert "explicit-unit-test-sentinel" not in str(caught.value)
    assert "environment-unit-test-sentinel" not in str(caught.value)
    assert "authorization" not in str(caught.value).casefold()


def test_environment_token_is_used_when_explicit_token_is_absent(
    github_candidate: TechnologyCandidate,
    fixed_now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "environment-unit-test-sentinel")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, json={"message": "Not Found"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubRepositoryUnavailableError):
            collect_engineering_signals(
                github_candidate,
                http_client=client,
                collected_at=fixed_now,
            )

    assert requests[0].headers["authorization"] == "Bearer environment-unit-test-sentinel"


@pytest.mark.parametrize(
    ("status_code", "headers", "error_type"),
    [
        (404, {}, GitHubRepositoryUnavailableError),
        (401, {}, GitHubAuthenticationError),
        (
            403,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000"},
            GitHubRateLimitError,
        ),
    ],
)
def test_http_failures_are_typed(
    github_candidate: TechnologyCandidate,
    fixed_now: datetime,
    status_code: int,
    headers: dict[str, str],
    error_type: type[Exception],
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers=headers,
            json={"message": "rate limit" if status_code == 403 else "failure"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(error_type):
            collect_engineering_signals(
                github_candidate,
                http_client=client,
                collected_at=fixed_now,
            )


@pytest.mark.parametrize(
    "kind",
    ["timeout", "network"],
)
def test_transport_failures_are_typed(
    github_candidate: TechnologyCandidate,
    fixed_now: datetime,
    kind: str,
) -> None:
    sentinel_token = "transport-secret-unit-test-sentinel"

    def handler(request: httpx.Request) -> httpx.Response:
        if kind == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        raise httpx.ConnectError("connection failed", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubTransportError) as caught:
            collect_engineering_signals(
                github_candidate,
                token=sentinel_token,
                http_client=client,
                collected_at=fixed_now,
            )

    assert caught.value.kind == kind
    assert sentinel_token not in str(caught.value)
    assert "authorization" not in str(caught.value).casefold()
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json=[]),
        httpx.Response(502, json={"message": "upstream failure"}),
    ],
)
def test_malformed_or_unexpected_repository_response_fails(
    github_candidate: TechnologyCandidate,
    fixed_now: datetime,
    response: httpx.Response,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return response

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubResponseError):
            collect_engineering_signals(
                github_candidate,
                http_client=client,
                collected_at=fixed_now,
            )


def test_invalid_normalized_payload_is_a_sanitized_response_error(
    github_candidate: TechnologyCandidate,
    repository_payload: dict[str, object],
    fixed_now: datetime,
) -> None:
    repository_payload["full_name"] = "   "

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=repository_payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubResponseError, match="normalized_signals"):
            collect_engineering_signals(
                github_candidate,
                http_client=client,
                collected_at=fixed_now,
            )
