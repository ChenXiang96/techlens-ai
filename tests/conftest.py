from datetime import UTC, datetime
import socket

import pytest

from techlens.models import TechnologyCandidate


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    """Every normal test is offline and independent of real credentials."""
    for name in ("OPENAI_API_KEY", "TAVILY_API_KEY", "GITHUB_TOKEN", "OPENAI_MODEL",
                 "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    def block_network(*args, **kwargs):
        raise AssertionError("Real network connections are forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", block_network)
    monkeypatch.setattr(socket.socket, "connect_ex", block_network)
    monkeypatch.setattr(socket, "create_connection", block_network)


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def github_candidate() -> TechnologyCandidate:
    return TechnologyCandidate(
        name="Example",
        repository_url="https://github.com/example/project",
    )


@pytest.fixture
def repository_payload() -> dict[str, object]:
    return {
        "owner": {"login": "example"},
        "name": "project",
        "full_name": "example/project",
        "archived": False,
        "created_at": "2020-01-01T00:00:00Z",
        "pushed_at": "2026-09-19T00:00:00Z",
        "default_branch": "main",
        "license": {"spdx_id": "MIT", "name": "MIT License"},
    }


@pytest.fixture
def github_payloads(repository_payload: dict[str, object]) -> dict[str, object]:
    return {
        "repository": repository_payload,
        "latest_release": {
            "tag_name": "v2.0.0",
            "published_at": "2026-09-01T00:00:00Z",
        },
        "releases": [
            {
                "tag_name": "v2.0.0",
                "draft": False,
                "published_at": "2026-09-01T00:00:00Z",
            },
            {
                "tag_name": "v1.0.0",
                "draft": False,
                "published_at": "2026-01-01T00:00:00Z",
            },
        ],
        "commits": [
            {"commit": {"committer": {"date": "2026-09-10T00:00:00Z"}}},
            {"commit": {"committer": {"date": "2026-07-01T00:00:00Z"}}},
        ],
        "issues": [
            {"number": 1, "updated_at": "2026-09-10T00:00:00Z"},
            {"number": 2, "updated_at": "2026-01-01T00:00:00Z"},
            {
                "number": 3,
                "updated_at": "2026-09-15T00:00:00Z",
                "pull_request": {"url": "https://api.github.com/pulls/3"},
            },
        ],
        "open_pulls": [
            {"number": 10, "merged_at": None},
            {"number": 11, "merged_at": None},
        ],
        "closed_pulls": [
            {"number": 8, "merged_at": "2026-09-05T00:00:00Z"},
            {"number": 7, "merged_at": "2026-01-01T00:00:00Z"},
            {"number": 6, "merged_at": None},
        ],
        "contributors": [
            {"login": "one", "contributions": 70},
            {"login": "two", "contributions": 20},
            {"login": "three", "contributions": 10},
        ],
        "tree": {
            "truncated": False,
            "tree": [
                {"type": "blob", "path": ".github/workflows/build.yml"},
                {"type": "blob", "path": "tests/test_core.py"},
                {"type": "blob", "path": "SECURITY.md"},
                {"type": "blob", "path": ".github/dependabot.yml"},
            ],
        },
    }
