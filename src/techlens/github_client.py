"""Bounded, deterministic collection from the GitHub REST API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from techlens.github_models import (
    BoundedCount,
    CollectionMetadata,
    CommitSignals,
    ContributorConcentrationProxy,
    EngineeringSignals,
    GitHubRepositoryRef,
    IssueSignals,
    PullRequestSignals,
    ReleaseSignals,
    RepositorySignals,
)
from techlens.github_practices import detect_repository_practices
from techlens.models import TechnologyCandidate

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
GITHUB_PER_PAGE = 100
GITHUB_MAX_PAGES = 2
GITHUB_TIMEOUT_SECONDS = 10.0


class GitHubCollectionError(Exception):
    """Base class for sanitized GitHub collection failures."""


class GitHubRepositoryUnavailableError(GitHubCollectionError):
    """The repository was not found or is inaccessible."""


class GitHubAuthenticationError(GitHubCollectionError):
    """GitHub rejected authentication or authorization."""


class GitHubRateLimitError(GitHubCollectionError):
    """GitHub rejected a request because of rate limiting."""

    def __init__(
        self,
        endpoint: str,
        *,
        rate_limit_remaining: int | None,
        rate_limit_reset_at: datetime | None,
        retry_after_seconds: int | None,
    ) -> None:
        super().__init__(f"GitHub rate limit exceeded while collecting {endpoint}")
        self.rate_limit_remaining = rate_limit_remaining
        self.rate_limit_reset_at = rate_limit_reset_at
        self.retry_after_seconds = retry_after_seconds


class GitHubTransportError(GitHubCollectionError):
    """A timeout or network failure occurred."""

    def __init__(self, endpoint: str, *, kind: Literal["timeout", "network"]):
        super().__init__(f"GitHub {kind} error while collecting {endpoint}")
        self.kind = kind


class GitHubResponseError(GitHubCollectionError):
    """GitHub returned an unexpected status or payload."""

    def __init__(self, endpoint: str, *, status_code: int | None = None):
        message = f"Unexpected GitHub response while collecting {endpoint}"
        if status_code is not None:
            message += f" (status {status_code})"
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class _PaginatedResult:
    items: list[dict[str, Any]]
    pages_fetched: int
    truncated: bool


def _integer_header(headers: httpx.Headers, name: str) -> int | None:
    value = headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _datetime_from_epoch(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC)


def _has_next_page(link_header: str | None) -> bool:
    if not link_header:
        return False
    return any(
        'rel="next"' in part
        for part in link_header.split(",")
    )


def _parse_datetime(value: object, endpoint: str) -> datetime:
    if not isinstance(value, str):
        raise GitHubResponseError(endpoint)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise GitHubResponseError(endpoint) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GitHubResponseError(endpoint)
    return parsed.astimezone(UTC)


def _required_dict(
    payload: dict[str, Any],
    key: str,
    endpoint: str,
) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise GitHubResponseError(endpoint)
    return value


def _required_str(
    payload: dict[str, Any],
    key: str,
    endpoint: str,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GitHubResponseError(endpoint)
    return value


def _required_bool(
    payload: dict[str, Any],
    key: str,
    endpoint: str,
) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise GitHubResponseError(endpoint)
    return value


def _bounded_count(observed: int, truncated: bool) -> BoundedCount:
    return BoundedCount(
        observed=observed,
        completeness="truncated" if truncated else "complete",
    )


class GitHubClient:
    """Small synchronous GitHub REST client with explicit collection bounds."""

    def __init__(
        self,
        *,
        token: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        resolved_token = token if token is not None else os.getenv("GITHUB_TOKEN")
        resolved_token = resolved_token.strip() if resolved_token else None

        self._headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "TechLens",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if resolved_token:
            self._headers["Authorization"] = f"Bearer {resolved_token}"

        self.authenticated = resolved_token is not None
        self.github_api_calls_made = 0
        self.rate_limit_limit: int | None = None
        self.rate_limit_remaining: int | None = None
        self.rate_limit_reset_at: datetime | None = None
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.Client()

    def close(self) -> None:
        if self._owns_client:
            self._http_client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _record_rate_limit(self, headers: httpx.Headers) -> None:
        limit = _integer_header(headers, "x-ratelimit-limit")
        remaining = _integer_header(headers, "x-ratelimit-remaining")
        reset = _integer_header(headers, "x-ratelimit-reset")
        if limit is not None:
            self.rate_limit_limit = limit
        if remaining is not None:
            self.rate_limit_remaining = remaining
        if reset is not None:
            self.rate_limit_reset_at = _datetime_from_epoch(reset)

    def _raise_for_status(self, response: httpx.Response, endpoint: str) -> None:
        status_code = response.status_code
        if status_code == 401:
            raise GitHubAuthenticationError(
                f"GitHub authentication failed while collecting {endpoint}"
            )

        if status_code in {403, 429}:
            retry_after = _integer_header(response.headers, "retry-after")
            message = ""
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("message"), str):
                    message = body["message"].casefold()
            except ValueError:
                pass

            rate_limited = (
                status_code == 429
                or self.rate_limit_remaining == 0
                or retry_after is not None
                or "rate limit" in message
            )
            if rate_limited:
                raise GitHubRateLimitError(
                    endpoint,
                    rate_limit_remaining=self.rate_limit_remaining,
                    rate_limit_reset_at=self.rate_limit_reset_at,
                    retry_after_seconds=retry_after,
                )
            raise GitHubAuthenticationError(
                f"GitHub authorization failed while collecting {endpoint}"
            )

        if status_code == 404:
            raise GitHubRepositoryUnavailableError(
                f"GitHub repository is unavailable while collecting {endpoint}"
            )

        raise GitHubResponseError(endpoint, status_code=status_code)

    def _request_json(
        self,
        endpoint: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        expected: Literal["object", "array"],
        not_found_is_none: bool = False,
        no_content_is_empty_array: bool = False,
    ) -> tuple[dict[str, Any] | list[Any] | None, httpx.Headers]:
        url = f"{GITHUB_API_BASE_URL}{path}"
        self.github_api_calls_made += 1
        transport_error: GitHubTransportError | None = None
        try:
            response = self._http_client.get(
                url,
                headers=self._headers,
                params=params,
                timeout=GITHUB_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            transport_error = GitHubTransportError(endpoint, kind="timeout")
        except httpx.TransportError:
            transport_error = GitHubTransportError(endpoint, kind="network")

        if transport_error is not None:
            raise transport_error

        self._record_rate_limit(response.headers)

        if response.status_code == 404 and not_found_is_none:
            return None, response.headers
        if response.status_code == 204 and no_content_is_empty_array:
            return [], response.headers
        if not 200 <= response.status_code < 300:
            self._raise_for_status(response, endpoint)

        try:
            payload = response.json()
        except ValueError:
            raise GitHubResponseError(
                endpoint,
                status_code=response.status_code,
            ) from None

        if expected == "object" and not isinstance(payload, dict):
            raise GitHubResponseError(endpoint, status_code=response.status_code)
        if expected == "array" and not isinstance(payload, list):
            raise GitHubResponseError(endpoint, status_code=response.status_code)
        return payload, response.headers

    def _paginate(
        self,
        endpoint: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        allow_no_content: bool = False,
    ) -> _PaginatedResult:
        collected: list[dict[str, Any]] = []
        base_params = dict(params or {})
        has_next = False

        for page in range(1, GITHUB_MAX_PAGES + 1):
            page_params = {
                **base_params,
                "per_page": GITHUB_PER_PAGE,
                "page": page,
            }
            payload, headers = self._request_json(
                endpoint,
                path,
                params=page_params,
                expected="array",
                no_content_is_empty_array=allow_no_content,
            )
            if not isinstance(payload, list):
                raise GitHubResponseError(endpoint)
            if not all(isinstance(item, dict) for item in payload):
                raise GitHubResponseError(endpoint)
            collected.extend(payload)
            has_next = _has_next_page(headers.get("link"))
            if not has_next:
                return _PaginatedResult(collected, page, False)

        return _PaginatedResult(collected, GITHUB_MAX_PAGES, has_next)

    def collect(
        self,
        candidate: TechnologyCandidate,
        *,
        collected_at: datetime | None = None,
    ) -> EngineeringSignals:
        """Collect normalized engineering facts for one GitHub candidate."""
        timestamp = collected_at or datetime.now(tz=UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("collected_at must be timezone-aware")
        timestamp = timestamp.astimezone(UTC)

        repository_ref = GitHubRepositoryRef.from_candidate(candidate)
        owner = quote(repository_ref.owner, safe="")
        repository = quote(repository_ref.repository, safe="")
        repository_path = f"/repos/{owner}/{repository}"
        truncated_endpoints: list[str] = []

        repository_payload, _ = self._request_json(
            "repository",
            repository_path,
            expected="object",
        )
        if not isinstance(repository_payload, dict):
            raise GitHubResponseError("repository")

        owner_payload = _required_dict(repository_payload, "owner", "repository")
        created_at = _parse_datetime(
            repository_payload.get("created_at"),
            "repository",
        )
        pushed_value = repository_payload.get("pushed_at")
        pushed_at = (
            None
            if pushed_value is None
            else _parse_datetime(pushed_value, "repository")
        )
        license_payload = repository_payload.get("license")
        if license_payload is None:
            license_name = None
        elif isinstance(license_payload, dict):
            license_name = license_payload.get("spdx_id") or license_payload.get("name")
            if license_name is not None and not isinstance(license_name, str):
                raise GitHubResponseError("repository")
        else:
            raise GitHubResponseError("repository")

        repository_signals = RepositorySignals(
            owner=_required_str(owner_payload, "login", "repository"),
            name=_required_str(repository_payload, "name", "repository"),
            full_name=_required_str(repository_payload, "full_name", "repository"),
            archived=_required_bool(repository_payload, "archived", "repository"),
            created_at=created_at,
            pushed_at=pushed_at,
            default_branch=_required_str(
                repository_payload,
                "default_branch",
                "repository",
            ),
            license=license_name,
            repository_age_days=max((timestamp - created_at).days, 0),
        )

        latest_release_payload, _ = self._request_json(
            "latest_release",
            f"{repository_path}/releases/latest",
            expected="object",
            not_found_is_none=True,
        )
        if latest_release_payload is None:
            latest_release_tag = None
            latest_release_published_at = None
        elif isinstance(latest_release_payload, dict):
            latest_release_tag = _required_str(
                latest_release_payload,
                "tag_name",
                "latest_release",
            )
            latest_release_published_at = _parse_datetime(
                latest_release_payload.get("published_at"),
                "latest_release",
            )
        else:
            raise GitHubResponseError("latest_release")

        releases = self._paginate(
            "releases",
            f"{repository_path}/releases",
        )
        if releases.truncated:
            truncated_endpoints.append("releases")
        cutoff_90d = timestamp - timedelta(days=90)
        cutoff_365d = timestamp - timedelta(days=365)
        release_dates: list[datetime] = []
        for release in releases.items:
            if release.get("draft") is True:
                continue
            published_at = release.get("published_at")
            if published_at is None:
                continue
            release_dates.append(_parse_datetime(published_at, "releases"))
        release_signals = ReleaseSignals(
            latest_release_tag=latest_release_tag,
            latest_release_published_at=latest_release_published_at,
            releases_last_90d=_bounded_count(
                sum(date >= cutoff_90d for date in release_dates),
                releases.truncated,
            ),
            releases_last_365d=_bounded_count(
                sum(date >= cutoff_365d for date in release_dates),
                releases.truncated,
            ),
            pages_fetched=releases.pages_fetched,
        )

        commits = self._paginate(
            "commits",
            f"{repository_path}/commits",
            params={
                "sha": repository_signals.default_branch,
                "since": cutoff_90d.isoformat().replace("+00:00", "Z"),
                "until": timestamp.isoformat().replace("+00:00", "Z"),
            },
        )
        if commits.truncated:
            truncated_endpoints.append("commits")
        commit_dates: list[datetime] = []
        for commit_item in commits.items:
            commit_payload = _required_dict(commit_item, "commit", "commits")
            committer = _required_dict(commit_payload, "committer", "commits")
            commit_dates.append(
                _parse_datetime(committer.get("date"), "commits")
            )
        cutoff_30d = timestamp - timedelta(days=30)
        commit_signals = CommitSignals(
            commits_last_30d=_bounded_count(
                sum(date >= cutoff_30d for date in commit_dates),
                commits.truncated,
            ),
            commits_last_90d=_bounded_count(
                sum(date >= cutoff_90d for date in commit_dates),
                commits.truncated,
            ),
            pages_fetched=commits.pages_fetched,
        )

        issues = self._paginate(
            "issues",
            f"{repository_path}/issues",
            params={"state": "open", "sort": "updated", "direction": "desc"},
        )
        if issues.truncated:
            truncated_endpoints.append("issues")
        issue_only = [item for item in issues.items if "pull_request" not in item]
        recent_issues = sum(
            _parse_datetime(item.get("updated_at"), "issues") >= cutoff_90d
            for item in issue_only
        )
        issue_signals = IssueSignals(
            open_issues_observed=_bounded_count(
                len(issue_only),
                issues.truncated,
            ),
            recently_updated_open_issues_90d=_bounded_count(
                recent_issues,
                issues.truncated,
            ),
            pages_fetched=issues.pages_fetched,
        )

        open_prs = self._paginate(
            "open_pull_requests",
            f"{repository_path}/pulls",
            params={"state": "open", "sort": "updated", "direction": "desc"},
        )
        if open_prs.truncated:
            truncated_endpoints.append("open_pull_requests")

        closed_prs = self._paginate(
            "closed_pull_requests",
            f"{repository_path}/pulls",
            params={"state": "closed", "sort": "updated", "direction": "desc"},
        )
        if closed_prs.truncated:
            truncated_endpoints.append("closed_pull_requests")
        merged_recently = 0
        for pull_request in closed_prs.items:
            merged_at = pull_request.get("merged_at")
            if merged_at is not None and _parse_datetime(
                merged_at,
                "closed_pull_requests",
            ) >= cutoff_90d:
                merged_recently += 1
        pull_request_signals = PullRequestSignals(
            open_prs_observed=_bounded_count(
                len(open_prs.items),
                open_prs.truncated,
            ),
            merged_prs_last_90d=_bounded_count(
                merged_recently,
                closed_prs.truncated,
            ),
            open_pages_fetched=open_prs.pages_fetched,
            closed_pages_fetched=closed_prs.pages_fetched,
        )

        contributors = self._paginate(
            "contributors",
            f"{repository_path}/contributors",
            params={"anon": "true"},
            allow_no_content=True,
        )
        if contributors.truncated:
            truncated_endpoints.append("contributors")
        contribution_counts: list[int] = []
        for contributor in contributors.items:
            contributions = contributor.get("contributions")
            if (
                not isinstance(contributions, int)
                or isinstance(contributions, bool)
                or contributions < 0
            ):
                raise GitHubResponseError("contributors")
            contribution_counts.append(contributions)
        contribution_counts.sort(reverse=True)
        total_contributions = sum(contribution_counts)
        if total_contributions:
            top_share = contribution_counts[0] / total_contributions
            top_3_share = sum(contribution_counts[:3]) / total_contributions
        else:
            top_share = None
            top_3_share = None
        contributor_signals = ContributorConcentrationProxy(
            contributors_observed=_bounded_count(
                len(contribution_counts),
                contributors.truncated,
            ),
            total_contributions_observed=total_contributions,
            top_contributor_share=top_share,
            top_3_contributor_share=top_3_share,
            pages_fetched=contributors.pages_fetched,
        )

        encoded_ref = quote(repository_signals.default_branch, safe="")
        tree_payload, _ = self._request_json(
            "repository_tree",
            f"{repository_path}/git/trees/{encoded_ref}",
            params={"recursive": "1"},
            expected="object",
        )
        if not isinstance(tree_payload, dict):
            raise GitHubResponseError("repository_tree")
        tree_entries = tree_payload.get("tree")
        tree_truncated = tree_payload.get("truncated")
        if not isinstance(tree_entries, list) or not isinstance(tree_truncated, bool):
            raise GitHubResponseError("repository_tree")
        if not all(isinstance(entry, dict) for entry in tree_entries):
            raise GitHubResponseError("repository_tree")
        if tree_truncated:
            truncated_endpoints.append("repository_tree")
        file_paths: list[str] = []
        for entry in tree_entries:
            if entry.get("type") != "blob":
                continue
            path = entry.get("path")
            if not isinstance(path, str):
                raise GitHubResponseError("repository_tree")
            file_paths.append(path)
        practice_signals = detect_repository_practices(
            file_paths,
            tree_truncated=tree_truncated,
            tree_entries_observed=len(tree_entries),
        )

        return EngineeringSignals(
            repository=repository_signals,
            releases=release_signals,
            commits=commit_signals,
            issues=issue_signals,
            pull_requests=pull_request_signals,
            contributors=contributor_signals,
            repository_practices=practice_signals,
            collection_metadata=CollectionMetadata(
                collected_at=timestamp,
                github_api_calls_made=self.github_api_calls_made,
                authenticated=self.authenticated,
                rate_limit_limit=self.rate_limit_limit,
                rate_limit_remaining=self.rate_limit_remaining,
                rate_limit_reset_at=self.rate_limit_reset_at,
                truncated_endpoints=sorted(truncated_endpoints),
                api_version=GITHUB_API_VERSION,
            ),
        )


def collect_engineering_signals(
    candidate: TechnologyCandidate,
    *,
    token: str | None = None,
    http_client: httpx.Client | None = None,
    collected_at: datetime | None = None,
) -> EngineeringSignals:
    """Collect GitHub facts with an optionally injected HTTP client."""
    client = GitHubClient(token=token, http_client=http_client)
    try:
        try:
            return client.collect(candidate, collected_at=collected_at)
        except ValidationError:
            raise GitHubResponseError("normalized_signals") from None
    finally:
        client.close()
