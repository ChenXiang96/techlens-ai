"""UI-only scoped loopback guard; the global offline fixture remains unchanged."""
from contextlib import contextmanager
import http.client
import json
import socket
from threading import Thread

from techlens.ui_coordinator import Coordinator
from techlens.ui_server import LocalServer

ORIGINAL_CONNECT = socket.socket.connect


class Scheduler:
    def __init__(self):
        self.calls = []

    def __call__(self, fn, *args):
        self.calls.append((fn, args))

    def finish(self):
        fn, args = self.calls.pop(0)
        fn(*args)


def action(c, owner, op, rid="owner", payload=None):
    view = c.bootstrap(owner) if rid == "owner" else c.run_view(owner, rid)
    return {"token": view["actions"][op], "revision": view["revision"], "data": payload or {}}


def start(c, owner, scheduler, scenario="single"):
    submitted = action(c, owner, "start", payload={"scenario": scenario})
    receipt = c.act(owner, "start", "owner", submitted)
    scheduler.finish()
    return receipt["run_id"], submitted


@contextmanager
def serving(monkeypatch, **options):
    c = Coordinator({})
    server = LocalServer(0, c, **options)
    endpoint = server.server_address
    def connect(sock, target):
        if target != endpoint:
            raise AssertionError("UI guard denied non-application endpoint")
        return ORIGINAL_CONNECT(sock, target)
    def create_connection(target, timeout=None, source_address=None, **kwargs):
        sock = socket.socket()
        sock.settimeout(timeout)
        connect(sock, target)
        return sock
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        c.close()
        thread.join(5)


class BrowserHTTP:
    def __init__(self, server):
        self.server = server
        self.cookie, self.csrf = "", ""

    def request(self, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        h = {"Cookie": self.cookie, "X-TechLens-CSRF": self.csrf,
             "Origin": self.server.origin, "Content-Type": "application/json", **(headers or {})}
        connection.request("GET" if body is None else "POST", path,
                           None if body is None else json.dumps(body), h)
        r = connection.getresponse()
        contents = r.read()
        if r.getheader("Set-Cookie"):
            self.cookie = r.getheader("Set-Cookie").split(";")[0]
        result = (r.status, dict(r.getheaders()), contents)
        connection.close()
        return result

    def boot(self):
        assert self.request("/")[0] == 200
        code, _, body = self.request("/api/bootstrap")
        assert code == 200
        result = json.loads(body)
        self.csrf = result["csrf"]
        return result


def raw(server, payload, *, half_close=False):
    sock = socket.socket()
    sock.settimeout(3)
    sock.connect(server.server_address)
    sock.sendall(payload)
    if half_close:
        sock.shutdown(socket.SHUT_WR)
    result = bytearray()
    while True:
        try:
            b = sock.recv(65536)
        except OSError:
            break
        if not b:
            break
        result.extend(b)
    sock.close()
    return bytes(result)
