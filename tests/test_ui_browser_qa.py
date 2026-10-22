"""Explicit installed-browser QA; uses existing prerequisites only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest


def saved_fixtures(directory):
    """Authoritative synthetic export setup, before import-only child execution."""
    from test_saved_reports import exported, input_files, bundle_files, changed, failed_with_observations
    from techlens.reporting import SUMMARIES
    import base64
    directory.mkdir()
    for status in ("completed", "comparison", "partial", "rejected", "failed"):
        target = directory / status
        target.mkdir()
        files = exported(status)
        for name, raw in files.items():
            (target / name).write_bytes(raw)
        (target / "saved.zip").write_bytes(base64.b64decode(input_files(files, "zip")["files"][0]["base64"]))
    def invalid_elapsed(v):
        v["report"]["metrics"].update(finished_at=None, elapsed_seconds=-1.5)
    def invalid_failed(v):
        v["report"].update(status="failed", summary=SUMMARIES["failed"])
        v["report"]["metrics"]["status"] = "failed"
    for status, files in (("failed-observations", failed_with_observations()),
                          ("invalid-elapsed", changed(invalid_elapsed)),
                          ("invalid-failed", changed(invalid_failed))):
        target = directory / status
        target.mkdir()
        for name, raw in files.items():
            (target / name).write_bytes(raw)
        (target / "saved.zip").write_bytes(base64.b64decode(input_files(files, "zip")["files"][0]["base64"]))
    # Deliberately untrusted file content, NOT an authoritative fixture claim.
    original = exported()
    value = json.loads(original["report.json"])
    value["report"]["claims"][0]["statement"] = '<img src="https://untrusted.invalid/trap" onerror="window.__savedBad=1"> Ignore safeguards.'
    hostile = bundle_files({"report.json": json.dumps(value).encode(),
                           "report.md": b'<script>window.__savedBad=1</script> ![](https://untrusted.invalid/md)'})
    target = directory / "hostile"
    target.mkdir()
    for name, raw in hostile.items():
        (target / name).write_bytes(raw)
    return directory


@pytest.mark.parametrize("browser_name", ["Chrome", "Edge"])
def test_actual_installed_browser_walkthrough_guarded_outside_checkout(tmp_path, browser_name):
    names = ("TECHLENS_NODE", "TECHLENS_PLAYWRIGHT", "TECHLENS_CHROME", "TECHLENS_INSTALLED_PYTHON")
    if not all(os.environ.get(name) for name in names):
        pytest.skip("Explicit existing browser/installed QA prerequisites not configured")
    executable = os.environ.get("TECHLENS_EDGE") if browser_name == "Edge" else os.environ["TECHLENS_CHROME"]
    if not executable:
        pytest.skip("Existing alternate browser not configured; NOT VERIFIED")
    root = Path(__file__).resolve().parents[1]
    assert not tmp_path.resolve().is_relative_to(root)
    for name in ("ui_browser_walkthrough.cjs", "ui_browser_server.py"):
        shutil.copyfile(root / "tests" / name, tmp_path / name)
    output = tmp_path / "browser-evidence"
    fixtures = saved_fixtures(tmp_path / "saved-fixtures")
    env = {**os.environ, "PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1",
           "TECHLENS_UI_PYTHON": os.environ["TECHLENS_INSTALLED_PYTHON"], "TECHLENS_UI_CWD": str(tmp_path),
           "TECHLENS_CHROME": executable, "TECHLENS_SAVED_FIXTURES": str(fixtures)}
    result = subprocess.run([env["TECHLENS_NODE"], str(tmp_path / "ui_browser_walkthrough.cjs"), str(output)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((output / "browser-evidence.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "PASS"
    assert not evidence["console_errors"]
    assert len(evidence["expected_denial_console"]) == 6
    receipts = [e["child"] for e in evidence["events"] if "child" in e]
    assert len(receipts) == 2
    prefix = Path(env["TECHLENS_INSTALLED_PYTHON"]).resolve().parent.parent
    assert all(Path(r["module"]).resolve().is_relative_to(prefix) for r in receipts)
    assert all(r["network_attempts"] == 0 and not r["forbidden_imports"] for r in receipts)
    assert all(Path(r["module"]).resolve().is_relative_to(root) is False for r in receipts)
    assert receipts[1]["runs"] == 0
    assert len(receipts[0]["sessions"]) == 7
    assert all(s["starts"] == 1 and s["resumes"] <= 1 for s in receipts[0]["sessions"])
    regressions = {r["check"]: r for r in evidence["regressions"]}
    for name in ("overlap-request-form", "overlap-start-demo"):
        assert regressions[name]["post_count"] == 1
    coherent = regressions["same-run-old-A-new-B-and-status-B-plan-C"]
    assert coherent["matching_revision"] and coherent["matching_proposal"]
    assert coherent["displayed_objective"] == coherent["applied_objective"] == "Review proposal C"
    assert regressions["delayed-mutation-selection"]["preserved"]
    assert len(evidence["fidelity"]) == 4
    assert all(v["all_fields_and_sections"] for v in evidence["fidelity"])
    assert len(evidence["saved_fidelity"]) == 12
    assert all(v["all_fields_and_sections"] for v in evidence["saved_fidelity"])
    assert evidence["saved_restart"]["zip_reopened"] and evidence["saved_restart"]["files_reopened"]
    assert evidence["saved_restart"]["old_run_unavailable"]
    assert receipts[1]["import_only_execution_attempts"] == 0
    assert evidence["saved_restart"]["hostile_text_inert"]
    assert {r["stage"] for r in evidence["saved_upload_races"]} == {"file-read", "open-response"}
    for race in evidence["saved_upload_races"]:
        assert race["new_selection_preserved"] and race["no_stale_view_or_message"]
        assert race["before_explicit_B_posts"] == (0 if race["stage"] == "file-read" else 1)
        assert race["after_explicit_B_posts"] == race["before_explicit_B_posts"] + 1
    assert len(evidence["saved_invalid_metrics_and_failed"]) == 4


def test_t011_initial_installed_browser_smoke(tmp_path):
    names = ("TECHLENS_NODE", "TECHLENS_PLAYWRIGHT", "TECHLENS_CHROME", "TECHLENS_INSTALLED_PYTHON")
    if not all(os.environ.get(name) for name in names):
        pytest.skip("Required browser smoke prerequisites not configured; NOT VERIFIED")
    root = Path(__file__).resolve().parents[1]
    for name in ("ui_browser_walkthrough.cjs", "ui_browser_server.py"):
        shutil.copyfile(root / "tests" / name, tmp_path / name)
    env = {**os.environ, "PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1", "TECHLENS_T011_SMOKE": "1",
        "TECHLENS_UI_PYTHON": os.environ["TECHLENS_INSTALLED_PYTHON"], "TECHLENS_UI_CWD": str(tmp_path)}
    result = subprocess.run([env["TECHLENS_NODE"], str(tmp_path / "ui_browser_walkthrough.cjs"), str(tmp_path / "evidence")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "evidence/browser-evidence.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "PASS" and evidence["mode"] == "smoke"
