import io
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile
from importlib.resources import files
import pytest
from techlens.cli import main


def test_packaged_ui_assets_and_lazy_cli_launch(monkeypatch):
    from techlens import ui_server
    calls = []
    monkeypatch.setattr(ui_server, "serve", lambda port, env, write: calls.append(port))
    assert main(["ui"], stdout=io.StringIO(), environ={}) == 0
    assert main(["ui", "--port", "8765"], stdout=io.StringIO(), environ={}) == 0
    assert calls == [0, 8765]
    for name in ("index.html", "app.js", "app.css"):
        assert files("techlens").joinpath("ui_assets", name).read_bytes()


@pytest.mark.parametrize("port", ["0", "-1", "65536", "abc"])
def test_invalid_port_never_serves(monkeypatch, port):
    from techlens import ui_server
    monkeypatch.setattr(ui_server, "serve", lambda *args: pytest.fail("server constructed"))
    assert main(["ui", "--port", port], stdout=io.StringIO(), environ={}) == 2


def test_bind_failure_sanitized_cleanup(monkeypatch):
    from techlens import ui_server
    from techlens.configuration import ApplicationError
    def fail(*args):
        raise OSError("sensitive sentinel path")
    monkeypatch.setattr(ui_server, "LocalServer", fail)
    with pytest.raises(ApplicationError) as error:
        ui_server.serve(8765, {}, lambda text: None)
    assert error.value.code == 6
    assert error.value.__context__ is None
    assert "sentinel" not in str(error.value)


def test_launch_output_failure_still_closes_owned_resources(monkeypatch):
    from techlens import ui_server
    from techlens.configuration import ApplicationError
    from techlens.ui_coordinator import Coordinator
    coordinator = Coordinator({})
    monkeypatch.setattr(ui_server, "Coordinator", lambda env: coordinator)
    closed = []
    class Server:
        origin = "http://127.0.0.1:12345"
        def __init__(self, *args):
            pass
        def serve_forever(self, **kwargs):
            pytest.fail("Failed launch output must not start serving")
        def server_close(self):
            closed.append(True)
    monkeypatch.setattr(ui_server, "LocalServer", Server)
    def blocked(text):
        raise ApplicationError("Public output blocked.")
    with pytest.raises(ApplicationError):
        ui_server.serve(0, {}, blocked)
    assert coordinator.closed
    assert closed == [True]
    assert coordinator.pool._shutdown


def test_installed_ui_source_wheel_identity_outside_checkout(tmp_path):
    python, wheel = os.environ.get("TECHLENS_INSTALLED_PYTHON"), os.environ.get("TECHLENS_UI_WHEEL")
    if not python or not wheel:
        pytest.skip("Explicit existing installed-wheel UI QA not configured")
    root = Path(__file__).resolve().parents[1]
    assert not tmp_path.resolve().is_relative_to(root)
    members = {"techlens/" + p.relative_to(root / "src/techlens").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (root / "src/techlens").rglob("*") if p.is_file() and (
                   p.suffix == ".py" or p.parent.name == "ui_assets")}
    with zipfile.ZipFile(wheel) as archive:
        assert {n: hashlib.sha256(archive.read(n)).hexdigest() for n in members} == members
    script = '''import hashlib, json, pathlib, socket, sys
def denied(*args, **kwargs):
    raise RuntimeError("Installed UI identity guard forbids outbound network")
socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
import techlens
import techlens.ui_server, techlens.ui_coordinator, techlens.ui_contracts
import techlens.ui_artifacts, techlens.ui_presentation
root = pathlib.Path(techlens.__file__).parent
members = {"techlens/" + p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
           for p in root.rglob("*") if p.is_file() and (p.suffix == ".py" or p.parent.name == "ui_assets")}
try:
    socket.create_connection(("example.invalid", 443))
except RuntimeError:
    pass
else:
    raise AssertionError("guard missing")
print(json.dumps({"hashes": members, "module": str(root), "prefix": sys.prefix,
    "python": sys.executable, "sys_path": sys.path, "guard": True,
    "forbidden_imports": [n for n in sys.modules if n == "tests" or n == "evals" or n.startswith("techlens.evaluation")]}))
'''
    result = subprocess.run([python, "-I", "-B", "-c", script], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["hashes"] == members
    assert receipt["guard"] and not receipt["forbidden_imports"]
    assert Path(receipt["module"]).resolve().is_relative_to(Path(python).resolve().parent.parent)
    assert all(not Path(p).resolve().is_relative_to(root) for p in receipt["sys_path"] if p)
    (tmp_path / "installed-ui-identity.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
