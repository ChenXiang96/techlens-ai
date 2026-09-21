"""Pure repository-path heuristics for GitHub engineering signals."""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import PurePosixPath

from techlens.github_models import PracticeIndicator, RepositoryPracticeSignals

_SECURITY_PATHS = {
    "security.md",
    ".github/security.md",
    "docs/security.md",
}

_DEPENDENCY_AUTOMATION_PATHS = {
    ".github/dependabot.yml",
    ".github/dependabot.yaml",
    "renovate.json",
    ".renovaterc",
    ".renovaterc.json",
}

_TEST_DIRECTORY_NAMES = {"test", "tests", "__tests__", "spec", "specs"}

_TEST_FILE_PATTERNS = (
    "test_*.py",
    "*_test.py",
    "*.test.js",
    "*.test.jsx",
    "*.test.ts",
    "*.test.tsx",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.ts",
    "*.spec.tsx",
)


def _normalized_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.casefold()


def _is_ci_path(path: str) -> bool:
    if path in {".circleci/config.yml", "jenkinsfile"}:
        return True
    if not path.startswith(".github/workflows/"):
        return False
    filename = PurePosixPath(path).name
    return filename.endswith((".yml", ".yaml"))


def _is_test_path(path: str) -> bool:
    pure_path = PurePosixPath(path)
    if any(part in _TEST_DIRECTORY_NAMES for part in pure_path.parts[:-1]):
        return True
    return any(fnmatch(pure_path.name, pattern) for pattern in _TEST_FILE_PATTERNS)


def _indicator(
    matched_paths: Iterable[str],
    *,
    tree_truncated: bool,
) -> PracticeIndicator:
    matches = sorted(set(matched_paths), key=str.casefold)
    return PracticeIndicator(
        detected=bool(matches),
        evidence_complete=not tree_truncated,
        matched_paths=matches,
    )


def detect_repository_practices(
    file_paths: Iterable[str],
    *,
    tree_truncated: bool,
    tree_entries_observed: int,
) -> RepositoryPracticeSignals:
    """Detect repository practices while preserving matched path evidence."""
    paths = list(file_paths)
    normalized = [(path, _normalized_path(path)) for path in paths]

    ci_paths = [path for path, value in normalized if _is_ci_path(value)]
    test_paths = [path for path, value in normalized if _is_test_path(value)]
    security_paths = [
        path for path, value in normalized if value in _SECURITY_PATHS
    ]
    dependency_paths = [
        path
        for path, value in normalized
        if value in _DEPENDENCY_AUTOMATION_PATHS
    ]

    return RepositoryPracticeSignals(
        ci=_indicator(ci_paths, tree_truncated=tree_truncated),
        tests=_indicator(test_paths, tree_truncated=tree_truncated),
        security_policy=_indicator(
            security_paths,
            tree_truncated=tree_truncated,
        ),
        dependency_automation=_indicator(
            dependency_paths,
            tree_truncated=tree_truncated,
        ),
        tree_truncated=tree_truncated,
        tree_entries_observed=tree_entries_observed,
    )

