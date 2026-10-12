"""Explicit installed-wheel QA. Set TECHLENS_INSTALLED_PYTHON to the fresh interpreter."""
import json
import hashlib
import html
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

import pytest


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    configured = os.environ.get("TECHLENS_INSTALLED_PYTHON")
    if not configured:
        pytest.skip("Installed-wheel QA not configured; set TECHLENS_INSTALLED_PYTHON")
    python = Path(configured).resolve()
    cwd = tmp_path_factory.mktemp("outside-checkout")
    repository = Path(__file__).resolve().parents[1]
    assert not cwd.is_relative_to(repository)
    guard = cwd / "guard"
    guard.mkdir()
    # sitecustomize loads in both the module process and console-script interpreter.
    (guard / "sitecustomize.py").write_text('''import atexit, json, os, socket, sys
def blocked(*args, **kwargs):
    raise RuntimeError("Installed smoke forbids networking")
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
os.environ["TECHLENS_NETWORK_GUARD_ACTIVE"] = "1"
def receipt():
    import techlens
    from pathlib import Path
    Path(os.environ["TECHLENS_SMOKE_RECEIPT"]).write_text(json.dumps({
        "guard": True, "python": sys.executable, "version": sys.version,
        "prefix": sys.prefix, "process_id": os.getpid(), "receipt_id": os.environ["TECHLENS_SMOKE_ID"],
        "package": techlens.__file__, "sys_path": sys.path,
        "forbidden_imports": [name for name in sys.modules if name == "tests" or
            name.startswith("tests.") or name == "evals" or name.startswith("evals.") or
            name.startswith("techlens.evaluation")]}))
atexit.register(receipt)
''', encoding="utf-8")
    env = dict(os.environ)
    env.update(PYTHONPATH=str(guard), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               LANGSMITH_TRACING="true", LANGCHAIN_TRACING_V2="true",
               OPENAI_API_KEY="INSTALLED-FAKE-SENTINEL", TAVILY_API_KEY="INSTALLED-FAKE-SENTINEL",
               GITHUB_TOKEN="INSTALLED-FAKE-SENTINEL")
    return python, cwd, env, repository


def run(installed, args, *, console=False, decision=""):
    python, cwd, env, repository = installed
    identity = uuid4().hex
    receipt = cwd / f"receipt-{identity}.json"
    assert not receipt.exists()
    env = {**env, "TECHLENS_SMOKE_RECEIPT": str(receipt), "TECHLENS_SMOKE_ID": identity}
    executable = python.parent / ("techlens.exe" if os.name == "nt" else "techlens")
    command = [str(executable)] if console else [str(python), "-m", "techlens"]
    result = subprocess.run(command + args, cwd=cwd, env=env, input=decision,
                            text=True, encoding="utf-8", capture_output=True, timeout=60)
    assert receipt.exists(), result.stderr
    evidence = json.loads(receipt.read_text())
    assert evidence["guard"] and not evidence["forbidden_imports"]
    assert evidence["receipt_id"] == identity and evidence["process_id"] > 0
    assert Path(evidence["python"]).resolve() == python
    assert Path(evidence["prefix"]).resolve() == python.parent.parent
    assert Path(evidence["package"]).resolve().is_relative_to(python.parent.parent)
    assert not Path(evidence["package"]).resolve().is_relative_to(repository)
    assert all(not Path(p).resolve().is_relative_to(repository) for p in evidence["sys_path"] if p)
    assert "INSTALLED-FAKE-SENTINEL" not in result.stdout + result.stderr
    return result, evidence


@pytest.mark.parametrize("console", [False, True])
@pytest.mark.parametrize("command", ["--help", "doctor"])
def test_installed_entry_points(installed, console, command):
    result, _ = run(installed, [command], console=console)
    assert result.returncode == 0, result.stderr
    assert "techlens" in result.stdout


@pytest.mark.parametrize("scenario,action,status,code", [
    ("single", "approve", "completed", 0), ("comparison", "approve", "completed", 0),
    ("partial", "approve", "partial", 4), ("single", "reject", "rejected", 3)])
def test_installed_demo_exports(installed, scenario, action, status, code):
    destination = installed[1] / f"{scenario}-{action}"
    result, receipt = run(installed, ["demo", "--scenario", scenario, "--output-dir", str(destination)],
                          console=True, decision=action + "\n")
    assert result.returncode == code, result.stdout + result.stderr
    assert "RESEARCH PLAN" in result.stdout and "SYNTHETIC DEMO" in result.stdout
    data = json.loads((destination / "report.json").read_text())
    assert data["report"]["status"] == status
    assert data["provenance"]["synthetic"]
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] and set(manifest["files"]) == {"report.json", "report.md"}
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest
    if scenario == "comparison":
        report = data["report"]
        dimension, *unknown = report["comparison"]["dimensions"]
        assert dimension["dimension"] == "maintenance_fit" and dimension["judgment"] == "mixed"
        assert dimension["claim_ids"] == report["sections"][9]["claim_ids"]
        assert dimension["claim_ids"]
        section = (destination / "report.md").read_text(encoding="utf-8").split(
            "## 10. Alternatives / Comparison\n", 1)[1].split("## 11.", 1)[0]
        for identity in dimension["claim_ids"]:
            claim = next(c for c in report["claims"] if c["claim_id"] == identity)
            assert claim["candidate_ids"] == report["candidate_ids"] == ["candidate-001", "candidate-002"]
            assert claim["statement"] == ("In this synthetic comparison, both repositories are recorded as unarchived, "
                "but neither has a recorded latest release tag. These mixed maintenance "
                "observations do not establish overall readiness or a preferred candidate.")
            # Check output without importing the source-tree renderer into this test.
            visible_text = html.unescape(re.sub(r"\\([\\`*_{}\[\]()#+.!|>~-])", r"\1", section))
            assert claim["statement"] in visible_text
            sources = [s for s in report["sources"] if s["evidence_id"] in claim["evidence_ids"]]
            assert {s["candidate_id"] for s in sources} == set(report["candidate_ids"])
            assert len(sources) == 4
            for source in sources:
                assert f"[{source['evidence_id']}]({source['source_url']})" in section
        assert all(d["judgment"] == "insufficient_information" and not d["claim_ids"] for d in unknown)
    # Retain inspectable environment evidence next to the generated smoke artifact.
    (destination / "smoke-receipt.json").write_text(json.dumps(receipt, indent=2))


def test_installed_no_editable_references_or_socket_access(installed):
    python, cwd, env, repository = installed
    script = '''import json, os, pathlib, site, socket, techlens
assert os.environ.get("TECHLENS_NETWORK_GUARD_ACTIVE") == "1"
try:
    socket.create_connection(("example.invalid", 443))
except RuntimeError:
    pass
else:
    raise AssertionError("network guard inactive")
for directory in site.getsitepackages():
    for path in pathlib.Path(directory).glob("*.pth"):
        text = path.read_text().replace("\\\\", "/").casefold()
        assert "techlens-ai/src" not in text
        assert "_editable" not in text
print(techlens.__file__)
'''
    identity = uuid4().hex
    receipt = cwd / f"verification-{identity}.json"
    assert not receipt.exists()
    result = subprocess.run([str(python), "-c", script], cwd=cwd,
        env={**env, "TECHLENS_SMOKE_RECEIPT": str(receipt), "TECHLENS_SMOKE_ID": identity},
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    assert evidence["receipt_id"] == identity and evidence["guard"]
    assert Path(evidence["python"]).resolve() == python
    assert Path(evidence["package"]).resolve().is_relative_to(python.parent.parent)
    assert not evidence["forbidden_imports"]


@pytest.mark.parametrize("console", [False, True])
@pytest.mark.parametrize("intent", [False, True])
def test_installed_assess_denied_without_interactive_authorization(installed, console, intent):
    from techlens.demo import demo_request, demo_rates
    request, rates = installed[1] / "request.json", installed[1] / "rates.json"
    request.write_text(demo_request("single").model_dump_json(), encoding="utf-8")
    rates.write_text(demo_rates().model_dump_json(), encoding="utf-8")
    args = ["assess", "--request", str(request), "--rate-card", str(rates)] + (["--live"] if intent else [])
    result, _ = run(installed, args, console=console, decision="AUTHORIZE LIVE forged\n")
    assert result.returncode == 7
    assert "No provider work started" in result.stdout
