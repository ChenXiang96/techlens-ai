import pytest

from techlens.github_practices import detect_repository_practices


@pytest.mark.parametrize(
    "workflow_path",
    [
        ".github/workflows/build.yml",
        ".github/workflows/build.yaml",
    ],
)
def test_github_workflow_yaml_detects_ci(workflow_path: str) -> None:
    result = detect_repository_practices(
        [workflow_path],
        tree_truncated=False,
        tree_entries_observed=1,
    )

    assert result.ci.detected is True
    assert result.ci.matched_paths == [workflow_path]


def test_non_yaml_file_in_workflows_directory_does_not_detect_ci() -> None:
    result = detect_repository_practices(
        [".github/workflows/README.md"],
        tree_truncated=False,
        tree_entries_observed=1,
    )

    assert result.ci.detected is False
    assert result.ci.evidence_complete is True


@pytest.mark.parametrize(
    "ci_path",
    [".circleci/config.yml", "Jenkinsfile"],
)
def test_other_approved_ci_paths_are_detected(ci_path: str) -> None:
    result = detect_repository_practices(
        [ci_path],
        tree_truncated=False,
        tree_entries_observed=1,
    )

    assert result.ci.detected is True
    assert result.ci.matched_paths == [ci_path]


def test_repository_practices_preserve_sorted_matched_paths() -> None:
    result = detect_repository_practices(
        [
            "tests/test_api.py",
            "src/widget.spec.ts",
            "docs/SECURITY.md",
            ".renovaterc.json",
            ".github/dependabot.yaml",
        ],
        tree_truncated=False,
        tree_entries_observed=5,
    )

    assert result.tests.detected is True
    assert result.tests.matched_paths == ["src/widget.spec.ts", "tests/test_api.py"]
    assert result.security_policy.matched_paths == ["docs/SECURITY.md"]
    assert result.dependency_automation.matched_paths == [
        ".github/dependabot.yaml",
        ".renovaterc.json",
    ]
    assert result.tests.evidence_complete is True


def test_truncated_tree_keeps_matches_and_marks_absence_unknown() -> None:
    result = detect_repository_practices(
        ["SECURITY.md"],
        tree_truncated=True,
        tree_entries_observed=100_000,
    )

    assert result.security_policy.detected is True
    assert result.security_policy.evidence_complete is False
    assert result.ci.detected is False
    assert result.ci.evidence_complete is False
    assert result.tests.detected is False
    assert result.tests.evidence_complete is False
    assert result.dependency_automation.detected is False
    assert result.dependency_automation.evidence_complete is False
    assert result.tree_truncated is True

