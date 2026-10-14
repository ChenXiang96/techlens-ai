"""Bounded T011 checks of real installed Windows entry points, not fake streams."""
import hashlib
import errno
import json
import os
from pathlib import Path
import re
import socket
import http.client

import pytest
from ui_support import ORIGINAL_CONNECT
from t011_terminal import (NativeTerminal, TerminalBlocked, SENTINEL, checked_receipt,
                           guarded_environment, sanitized)


@pytest.fixture
def native(tmp_path):
    python = os.environ.get("TECHLENS_INSTALLED_PYTHON")
    if not python or os.name != "nt":
        pytest.skip("Native installed Windows prerequisites not configured; NOT VERIFIED")
    assert not tmp_path.resolve().is_relative_to(Path(__file__).resolve().parents[1])
    python = Path(python).resolve()
    receipt = tmp_path / "receipt.json"
    env = guarded_environment(tmp_path / "guard", receipt)
    return python, tmp_path, receipt, env


def execute(native, args, *, console=False, answers=()):
    python, cwd, receipt, env = native
    command = [str(python.parent / "techlens.exe")] if console else [str(python), "-B", "-m", "techlens"]
    with NativeTerminal(command + args, cwd, env) as terminal:
        try:
            for prompt, answer in answers:
                terminal.expect(prompt)
                terminal.write(answer + "\r")
            code, transcript = terminal.finish()
        finally:
            (cwd / "terminal.txt").write_text(sanitized(bytes(terminal.data)), encoding="utf-8")
    observation = checked_receipt(receipt, python)
    assert SENTINEL not in transcript
    (cwd / "terminal.txt").write_text(transcript, encoding="utf-8")
    return code, transcript, observation


@pytest.mark.parametrize("console", [False, True], ids=["module", "console"])
@pytest.mark.parametrize("command", ["--help", "doctor"])
def test_native_installed_entry_guard_and_real_terminal(native, console, command):
    code, text, receipt = execute(native, [command], console=console)
    assert code == 0
    # ConPTY emits incremental screen updates, not a byte-for-byte stdout log.
    assert ("show this help message" if command == "--help" else "techlens 0.1.0") in text
    assert receipt["sessions"] == [] and receipt["ledger_constructions"] == 0


@pytest.mark.parametrize("scenario,decision,status,code", [
    ("single", "approve", "completed", 0), ("comparison", "approve", "completed", 0),
    ("partial", "approve", "partial", 4), ("single", "reject", "rejected", 3)])
def test_native_cli_real_review_and_canonical_exports(native, scenario, decision, status, code):
    destination = native[1] / "new-report"
    actual, text, receipt = execute(native, ["demo", "--scenario", scenario, "--report-view", "full",
        "--output-dir", str(destination)], console=True, answers=[("Action [approve", decision)])
    assert actual == code
    assert "SYNTHETIC DEMO" in text and "Export complete" in text
    assert len(receipt["sessions"]) == 1
    session = receipt["sessions"][0]
    assert session["starts"] == session["resumes"] == 1 and session["status"] == status
    assert session["planner_generation"] == 1
    assert session["current_round"] == (0 if decision == "reject" else 1)
    if decision == "reject":
        assert session["github"] == session["search"] == session["analyzer_generation"] == 0
    data = json.loads((destination / "report.json").read_text(encoding="utf-8"))
    assert data["report"]["status"] == status
    assert {name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
            for name in session["canonical_hashes"]} == session["canonical_hashes"]
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is True and manifest["files"] == session["canonical_hashes"]


REQUEST_ANSWERS = [
    ("Assessment ID [", "native-acceptance"), ("Number of candidates [", "1"),
    ("Candidate 1 name:", "Synthetic local request"),
    ("Candidate 1 GitHub repository URL:", "https://github.com/example/project"),
    ("Candidate 1 homepage [", ""), ("Team size [", ""),
    ("Primary languages item 1", "Python, literal comma"), ("Primary languages item 2", ""),
    ("Backend stack item 1", ""), ("AI experience [", ""),
    ("Deployment environment [", ""), ("Production criticality [", ""),
    ("Framework churn tolerance [", ""), ("Priorities item 1", ""),
    ("Maximum web-search attempts [", ""), ("Declared maximum research rounds [", ""),
    ("Run budget in USD [", ""),
]


@pytest.mark.parametrize("answer,expected", [("SAVE", 0), ("CANCEL", 130)])
def test_native_request_save_cancel_without_assessment(native, answer, expected):
    path = native[1] / "request.json"
    code, text, receipt = execute(native, ["request", "create", "--output", str(path)], console=True,
        answers=REQUEST_ANSWERS + [("Type SAVE", answer)])
    assert code == expected
    assert not receipt["sessions"] and receipt["ledger_constructions"] == 0
    assert path.exists() == (answer == "SAVE")
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        assert value["constraints"]["max_run_cost_usd"] == "0"
        assert value["team_profile"]["primary_languages"] == ["Python, literal comma"]
        assert value["team_profile"]["team_size"] is None


def test_native_fake_secret_echo_is_sanitized_and_not_saved(native):
    path = native[1] / "blocked.json"
    code, transcript, receipt = execute(native, ["request", "create", "--output", str(path)],
        answers=REQUEST_ANSWERS[:2] + [("Candidate 1 name:", SENTINEL)])
    assert code == 5 and not path.exists()
    assert SENTINEL not in transcript and not receipt["sessions"]


def test_native_interrupt_request_publishes_nothing(native):
    python, cwd, receipt_path, env = native
    path = cwd / "cancelled.json"
    with NativeTerminal([str(python), "-B", "-m", "techlens", "request", "create", "--output", str(path)], cwd, env) as terminal:
        terminal.expect("Assessment ID [")
        terminal.write("\x03")
        code, transcript = terminal.finish()
    assert code == 130 and not path.exists()
    assert not checked_receipt(receipt_path, python)["sessions"]
    (cwd / "terminal.txt").write_text(transcript, encoding="utf-8")


def test_native_transcript_redacts_echo_after_control_sequences():
    assert SENTINEL not in sanitized("\x1b[31m" + SENTINEL + "\x1b[0m")
    assert "[REDACTED FAKE SENTINEL]" in sanitized(SENTINEL)


@pytest.mark.parametrize("console", [False, True], ids=["module", "console"])
def test_actual_installed_ui_entry_local_assets_and_safe_shutdown(native, monkeypatch, console):
    python, cwd, receipt_path, env = native
    command = [str(python.parent / "techlens.exe")] if console else [str(python), "-B", "-m", "techlens"]
    with NativeTerminal(command + ["ui"], cwd, env) as terminal:
        terminal.expect("TechLens offline local UI:")
        address = re.search(r"http://127\.0\.0\.1:(\d+)/", sanitized(bytes(terminal.data)))
        assert address
        port = int(address.group(1))
        assert 49152 <= port <= 65535
        def connect(sock, target):
            assert target == ("127.0.0.1", port), "Only the exact owned application endpoint is permitted"
            return ORIGINAL_CONNECT(sock, target)
        monkeypatch.setattr(socket.socket, "connect", connect)
        def create_connection(target, timeout=None, source_address=None, **kwargs):
            assert target == ("127.0.0.1", port)
            sock = socket.socket()
            sock.settimeout(timeout)
            connect(sock, target)
            return sock
        monkeypatch.setattr(socket, "create_connection", create_connection)
        for path in ("/", "/app.js", "/app.css"):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", path)
            response = connection.getresponse()
            assert response.status == 200
            body = response.read()
            asset = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css"}[path]
            assert body == (Path(__file__).resolve().parents[1] / "src/techlens/ui_assets" / asset).read_bytes()
            connection.close()
        collision = cwd / "collision"
        collision.mkdir()
        collision_receipt = collision / "receipt.json"
        collision_env = guarded_environment(collision / "guard", collision_receipt)
        with NativeTerminal(command + ["ui", "--port", str(port)], collision, collision_env) as second:
            collision_code, collision_text = second.finish()
        assert collision_code == 6 and "could not bind" in collision_text
        assert not checked_receipt(collision_receipt, python)["sessions"]
        (collision / "terminal.txt").write_text(collision_text, encoding="utf-8")
        terminal.write("\x03")
        code, transcript = terminal.finish()
    assert code == 130
    receipt = checked_receipt(receipt_path, python)
    assert not receipt["sessions"] and receipt["ledger_constructions"] == 0
    (cwd / "terminal.txt").write_text(transcript, encoding="utf-8")


def test_native_request_no_clobber_preserves_existing_file(native):
    path = native[1] / "existing.json"
    path.write_bytes(b"preserve-existing-content")
    code, _, receipt = execute(native, ["request", "create", "--output", str(path)],
        answers=REQUEST_ANSWERS + [("Type SAVE", "SAVE")])
    assert code != 0 and path.read_bytes() == b"preserve-existing-content"
    assert not receipt["sessions"] and receipt["ledger_constructions"] == 0


@pytest.mark.parametrize("failure_stage", [3, 8], ids=["attribute-init", "suspended-process"])
def test_partial_terminal_construction_closes_only_owned_resources(native, monkeypatch, failure_stage):
    original_check, original_close = NativeTerminal._check, NativeTerminal.close
    calls, closed = [], []
    def check(self, result):
        calls.append(result)
        if len(calls) == failure_stage:
            raise TerminalBlocked("Controlled partial construction")
        return original_check(self, result)
    def close(self):
        original_close(self)
        closed.append(self)
    monkeypatch.setattr(NativeTerminal, "_check", check)
    monkeypatch.setattr(NativeTerminal, "close", close)
    with pytest.raises(TerminalBlocked):
        NativeTerminal([str(native[0]), "-B", "-m", "techlens", "--help"], native[1], native[3])
    assert len(closed) == 1 and closed[0].closed
    assert not closed[0].reader.is_alive()
    assert not native[2].exists()  # Guard/product never executed before resume.


def test_native_prompt_deadline_closes_owned_tree_without_unbounded_wait(native):
    with NativeTerminal([str(native[0]), "-B", "-m", "techlens", "request", "create",
                         "--output", str(native[1] / "never.json")], native[1], native[3]) as terminal:
        terminal.expect("Assessment ID [")
        with pytest.raises(TerminalBlocked, match="bound"):
            terminal.expect("An impossible prompt", timeout=0)
    assert terminal.closed and not terminal.reader.is_alive()
    assert not (native[1] / "never.json").exists()


@pytest.mark.parametrize("failures", [0, 1, 8])
def test_default_port_avoids_reproduced_unsafe_service_port_and_bounds_collisions(monkeypatch, failures):
    import socketserver
    from techlens import ui_server
    attempts = []
    monkeypatch.setattr(ui_server.secrets, "randbelow", lambda limit: 0)
    def bind(server):
        attempts.append(server.server_address)
        if len(attempts) <= failures:
            raise OSError(errno.EADDRINUSE, "controlled owned-port collision")
    monkeypatch.setattr(socketserver.TCPServer, "server_bind", bind)
    server = ui_server.LocalServer.__new__(ui_server.LocalServer)
    server.server_address = ("127.0.0.1", 0)
    if failures == 8:
        with pytest.raises(OSError):
            server.server_bind()
    else:
        server.server_bind()
    assert attempts == [("127.0.0.1", 49152)] * min(failures + 1, 8)
    assert all(address[1] != 6667 for address in attempts)


def test_explicit_port_is_preserved_and_permission_failure_is_not_retried(monkeypatch):
    import socketserver
    from techlens.ui_server import LocalServer
    attempts = []
    def bind(server):
        attempts.append(server.server_address)
        raise OSError(errno.EACCES, "controlled permission denial")
    monkeypatch.setattr(socketserver.TCPServer, "server_bind", bind)
    server = LocalServer.__new__(LocalServer)
    server.server_address = ("127.0.0.1", 8765)
    with pytest.raises(OSError):
        server.server_bind()
    assert attempts == [("127.0.0.1", 8765)]
