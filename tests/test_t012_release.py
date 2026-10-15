"""Independent preparation checks; configured final artifacts are separate gates."""
import io
import json
import os
from pathlib import Path
import re
import tarfile
import tomllib
import zipfile

import pytest

from techlens.configuration import parse_model
from techlens.models import AssessmentRequest
from t012_release import (
    ReleaseCheckError, ROOT_FILES, SDIST_DIRECTORIES, archive_payloads, checked_name, extract_sdist,
    inspect_sdist, inspect_wheel, source_included,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["single", "comparison", "unknown-team"])
def test_preparation_examples_explicitly_zero_budget(name):
    data = json.loads((ROOT / f"examples/request-{name}.json").read_text(encoding="utf-8"))
    request = parse_model(data, AssessmentRequest)
    assert data["constraints"]["max_run_cost_usd"] == "0"
    assert request.constraints.max_run_cost_usd == 0
    assert request.constraints.max_candidates == len(request.candidates)
    assert request.constraints.max_research_rounds == 1
    assert request.constraints.max_web_searches == 2


def test_advanced_price_template_remains_non_executable():
    data = json.loads((ROOT / "examples/rate-card.template.json").read_text(encoding="utf-8"))
    assert data["count_authoritative_zero"] is False
    assert "NON-EXECUTABLE" in data["source_reference"]
    assert data["count_upper_fee"].startswith("REPLACE_WITH_")


def test_readme_separates_offline_preview_from_advanced_execution():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Initial offline preview" in text
    assert "fictional" in text and "does not research repositories" in text
    assert "Python 3.11" in text and "NOT VERIFIED" in text
    assert "not an inherent CLI/browser requirement" in text
    assert "No public registry package or release download is established" in text
    assert "-m pip install techlens" not in text
    assert "RAG, UI, databases" not in text
    assert "(docs/architecture.md)" in text and "(docs/preview-limitations.md)" in text


def test_local_documentation_links_resolve_without_network():
    files = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *sorted((ROOT / "docs").glob("*.md"))]
    for file in files:
        text = file.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            target = target.removeprefix("<").removesuffix(">")
            if "://" in target or target.startswith(("mailto:", "#")):
                continue
            path, _, anchor = target.partition("#")
            destination = (file.parent / path).resolve()
            assert destination.is_relative_to(ROOT), "Non-portable documentation link"
            assert destination.is_file(), f"Broken local link in {file.name}"
            if anchor:
                headings = re.findall(r"^#+\s+(.+)$", destination.read_text(encoding="utf-8"), re.MULTILINE)
                slugs = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}
                assert anchor in slugs, f"Broken local heading in {file.name}"


@pytest.mark.parametrize("name", ["README.md", "CONTRIBUTING.md", "docs/architecture.md",
    "docs/preview-limitations.md", "docs/release-notes.md", "docs/release-checklist.md"])
def test_release_documentation_has_no_machine_absolute_paths(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert not re.search(r"[CD]:[/\\](?:Users|Projects)", text, re.IGNORECASE)
    assert ".codex/" not in text and ".codex\\" not in text


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/private", "a\\b",
    "a/../b", "a//b", "a/./b", "NUL.txt", "a/CON", "a/trailing.", "a/trailing ", "a\nb"])
def test_archive_paths_rejected_before_extraction(name):
    with pytest.raises(ReleaseCheckError):
        checked_name(name)


@pytest.mark.parametrize("name", ["src/techlens/cli.py", "docs/architecture.md",
    "tests/test_t012_release.py", "qa/t011/acceptance-results.json", "evals/t006/v1/labels.jsonl", "LICENSE"])
def test_reviewed_source_inclusion(name):
    assert source_included(name)


@pytest.mark.parametrize("name", [".git/config", ".venv/private", "src/techlens/__pycache__/x.pyc",
    "tests/__pycache__/x.pyc", "qa/t012/release-readiness.json", "dist/old.whl", "captures/private.png"])
def test_release_outputs_and_private_files_excluded(name):
    assert not source_included(name)


def tar_member(archive, name, content=b"example", kind=tarfile.REGTYPE):
    member = tarfile.TarInfo(name)
    member.type = kind
    member.size = len(content) if kind == tarfile.REGTYPE else 0
    member.linkname = "../outside" if kind != tarfile.REGTYPE else ""
    archive.addfile(member, io.BytesIO(content) if member.size else None)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_source_archive_links_and_special_members_rejected(tmp_path, kind):
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        tar_member(archive, "techlens-0.1.0/link", kind=kind)
    with pytest.raises(ReleaseCheckError, match="Unsupported"):
        extract_sdist(path, tmp_path / "extract")
    assert not (tmp_path / "extract").exists()


def test_duplicate_archive_members_rejected(tmp_path):
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        tar_member(archive, "techlens-0.1.0/a")
        tar_member(archive, "techlens-0.1.0/a")
    with pytest.raises(ReleaseCheckError, match="Duplicate"):
        archive_payloads(path)


def test_zip_symlink_rejected(tmp_path):
    path = tmp_path / "bad.whl"
    member = zipfile.ZipInfo("techlens/link")
    member.external_attr = 0o120777 << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, "outside")
    with pytest.raises(ReleaseCheckError, match="Unsupported"):
        archive_payloads(path)


def test_safe_source_extraction_is_new_owned_and_no_clobber(tmp_path):
    path = tmp_path / "safe.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        tar_member(archive, "techlens-0.1.0/README.md", b"reviewed")
    root = extract_sdist(path, tmp_path / "extract")
    assert (root / "README.md").read_bytes() == b"reviewed"
    with pytest.raises(ReleaseCheckError, match="must be new"):
        extract_sdist(path, tmp_path / "extract")
    assert (root / "README.md").read_bytes() == b"reviewed"


def test_reviewed_version_dependency_and_entry_point_contract():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == "0.1.0"
    assert project["project"]["requires-python"] == ">=3.11"
    assert project["project"]["scripts"] == {"techlens": "techlens.cli:main"}
    assert project["project"]["dependencies"] == [
        "httpx>=0.28,<1.0", "langgraph>=1.0,<2.0", "openai>=2.0,<3.0", "pydantic>=2.10,<3.0"]
    assert project["build-system"] == {
        "requires": ["hatchling>=1.27,<2.0"], "build-backend": "hatchling.build"}


def test_owner_approved_license_header_and_grant():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License\n\nCopyright (c) 2026 Xiang Chen\n")
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text
    assert "Ofek" not in text and "OWNER-APPROVED" not in text
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "MIT" and project["license-files"] == ["LICENSE"]


def test_explicit_sdist_policy_matches_reviewed_inventory():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    policy = project["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert set(policy["only-include"]) == ROOT_FILES | set(SDIST_DIRECTORIES)
    assert "/qa/t012/**" in policy["exclude"] and "**/__pycache__/**" in policy["exclude"]


def test_source_archive_inspection_rejects_fabricated_extra_payload(tmp_path):
    path = tmp_path / "extra.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        tar_member(archive, "techlens-0.1.0/PKG-INFO", b"Name: techlens\nVersion: 0.1.0\n")
        tar_member(archive, "techlens-0.1.0/private-secret.txt")
    with pytest.raises(ReleaseCheckError, match="inclusion or payload mismatch"):
        inspect_sdist(path, ROOT)


def test_archive_inspection_bounds_apply_before_reading(tmp_path, monkeypatch):
    import t012_release
    path = tmp_path / "large.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        tar_member(archive, "techlens-0.1.0/large", b"over bound")
    monkeypatch.setattr(t012_release, "MAX_BYTES", 4)
    with pytest.raises(ReleaseCheckError, match="bounds"):
        archive_payloads(path)


def configured_path(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not configured; final release gate NOT VERIFIED")
    path = Path(value).resolve()
    assert path.is_file()
    return path


def test_configured_final_licensed_wheel():
    wheel = configured_path("TECHLENS_T012_WHEEL")
    assert (ROOT / "LICENSE").is_file(), "Owner license gate incomplete"
    inspect_wheel(wheel, ROOT)


def test_configured_final_licensed_sdist_and_rebuild():
    sdist = configured_path("TECHLENS_T012_SDIST")
    rebuilt = configured_path("TECHLENS_T012_SDIST_WHEEL")
    inspect_sdist(sdist, ROOT)
    inspect_wheel(rebuilt, ROOT)
