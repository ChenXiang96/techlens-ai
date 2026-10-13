import json
import socket
import pytest

from ui_support import BrowserHTTP, raw, serving, action


def test_http_initial_cookie_refresh_origin_host_csrf_isolation(monkeypatch):
    with serving(monkeypatch) as server:
        browser = BrowserHTTP(server)
        boot = browser.boot()
        cookie = browser.cookie
        assert browser.boot()["csrf"] == boot["csrf"]
        assert browser.cookie == cookie
        assert len(server.coordinator.owners) == 1
        assert not server.coordinator.runs
        other = BrowserHTTP(server)
        assert other.boot()["csrf"] != boot["csrf"]
        for headers in ({"Host": "localhost"}, {"Origin": "https://evil.invalid"},
                        {"Sec-Fetch-Site": "cross-site"}, {"X-TechLens-CSRF": "wrong"}):
            assert browser.request("/api/runs/" + "a" * 32, headers=headers)[0] == 403
        assert browser.request("/api/demos/start", {"token": boot["actions"]["start"], "revision": 0,
            "data": {"scenario": "single", "live": True}})[0] == 400
        assert not server.coordinator.runs
        with pytest.raises(AssertionError):
            socket.create_connection(("example.com", 443))


def test_assets_security_allowlist_no_remote_refs(monkeypatch):
    with serving(monkeypatch) as server:
        browser = BrowserHTTP(server)
        for path in ("/", "/app.js", "/app.css"):
            code, headers, body = browser.request(path)
            assert code == 200
            assert headers["Cache-Control"] == "no-store"
            assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
            assert headers["X-Content-Type-Options"] == "nosniff"
            assert b"https://" not in body
            assert b"innerHTML" not in body
        browser.boot()
        for path in ("/../pyproject.toml", "/%2e%2e", "/app.js?x=1", "/api/live", "/favicon.ico"):
            assert browser.request(path)[0] != 200


@pytest.mark.parametrize("headers,body", [
    (b"Content-Length: 2\r\nContent-Length: 2\r\n", b"{}"),
    (b"Transfer-Encoding: chunked\r\n", b"0\r\n\r\n"),
    (b"Content-Length: 1048577\r\n", b""),
    (b"Content-Length: 4\r\nContent-Encoding: gzip\r\n", b"null"),
    (b"Content-Length: 2\r\nContent-Type: text/plain\r\n", b"{}"),
    (b"Content-Length: 13\r\n", b'{"x":1,"x":2}'),
    (b"Content-Length: 1\r\n", b"\xff"),
    (b"Content-Length: 50\r\n", b"{}"),
    (b"Content-Length: 3\r\n", b"{}x"),
])
def test_real_socket_framing_rejects_without_effect(monkeypatch, headers, body):
    with serving(monkeypatch) as server:
        header = (f"POST /api/demos/start HTTP/1.1\r\nHost: {server.authority}\r\n"
                  f"Origin: {server.origin}\r\n").encode()
        if b"Content-Type:" not in headers:
            header += b"Content-Type: application/json\r\n"
        result = raw(server, header + headers + b"\r\n" + body, half_close=True)
        assert not result.startswith(b"HTTP/1.1 200")
        assert not server.coordinator.runs
        assert not server.coordinator.owners


@pytest.mark.parametrize("payload", [b"GET /" + b"a" * 4096 + b" HTTP/1.1\r\n",
    b"GET / HTTP/1.1\r\nX: " + b"a" * 8192 + b"\r\n\r\n",
    b"GET / HTTP/1.1\r\n" + b"X: " + b"a" * 8000 + b"\r\n" + b"Y: " + b"a" * 8000 + b"\r\n" + b"Z: " + b"a" * 8000 + b"\r\n" + b"Q: " + b"a" * 8000 + b"\r\n" + b"A: " + b"a" * 1000 + b"\r\n\r\n",
    b"GET / HTTP/1.1\r\n" + b"X" * 100 + b"\r\n"], ids=["line", "header-line", "header-total", "invalid-header"])
def test_real_socket_line_header_bounds(monkeypatch, payload):
    with serving(monkeypatch) as server:
        assert b"200 OK" not in raw(server, payload, half_close=True)
        assert not server.coordinator.owners


def test_deadline_and_suffix_not_reinterpreted(monkeypatch):
    with serving(monkeypatch, read_deadline=0.1, inactivity=0.1) as server:
        result = raw(server, f"GET / HTTP/1.1\r\nHost: {server.authority}\r\n".encode())
        assert result.startswith(b"HTTP/1.1 408")
        first = f"GET / HTTP/1.1\r\nHost: {server.authority}\r\n\r\n".encode()
        result = raw(server, first + first, half_close=True)
        assert result.count(b"HTTP/1.1") == 1
        assert len(server.coordinator.owners) == 1


def test_http_worker_bound_before_thread_creation(monkeypatch):
    with serving(monkeypatch) as server:
        for _ in range(8):
            assert server.workers.acquire(blocking=False)
        try:
            response = raw(server, b"")
            assert response.startswith(b"HTTP/1.1 503")
            assert not server.coordinator.owners
        finally:
            for _ in range(8):
                server.workers.release()


def test_http_request_prepare_download_and_stale_restart(monkeypatch):
    with serving(monkeypatch) as server:
        browser = BrowserHTTP(server)
        boot = browser.boot()
        body = {"token": boot["actions"]["prepare"], "revision": boot["revision"],
            "data": {"candidates": [{"name": "<script>bad()</script>", "repository_url": "https://github.com/e/r"}]}}
        code, _, receipt = browser.request("/api/requests/prepare", body)
        assert code == 200
        rid = json.loads(receipt)["request_id"]
        code, h, contents = browser.request(f"/api/requests/{rid}/download")
        assert code == 200
        assert h["Content-Disposition"] == 'attachment; filename="techlens-request.json"'
        assert json.loads(contents)["constraints"]["max_run_cost_usd"] == "0"
        other = BrowserHTTP(server)
        other.boot()
        assert other.request(f"/api/requests/{rid}/download")[0] == 404
        assert not server.coordinator.runs
        old_cookie = browser.cookie
    with serving(monkeypatch) as restarted:
        stale = BrowserHTTP(restarted)
        stale.cookie = old_cookie
        assert stale.request("/api/bootstrap")[0] == 410
        stale.boot()
        assert stale.request(f"/api/requests/{rid}")[0] == 404
        assert not restarted.coordinator.runs


@pytest.mark.parametrize("sentinel,path,headers", [
    ("SYNTHETIC DEMO", "/", {}),
    ("Local Host validation failed", "/", {"Host": "bad-host"})])
def test_fixed_and_asset_text_also_pass_known_credential_boundary(monkeypatch, sentinel, path, headers):
    from techlens.presentation import PublicOutput
    with serving(monkeypatch) as server:
        server.coordinator.public = PublicOutput({"OPENAI_API_KEY": sentinel})
        code, _, body = BrowserHTTP(server).request(path, headers=headers)
        assert code == 503 and body == b""
        assert sentinel.encode() not in body
        assert not server.coordinator.runs
