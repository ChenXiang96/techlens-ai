"""Small loopback-only HTTP boundary with bounded reads before parsing/effects."""
from http.cookies import SimpleCookie
from importlib.resources import files
import errno
import re
import secrets
import socket
import socketserver
from threading import BoundedSemaphore
from time import monotonic
from uuid import uuid4

from techlens.configuration import ApplicationError
from techlens.ui_contracts import UIError, decode_json
from techlens.ui_coordinator import Coordinator

ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; " \
      "font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
DOWNLOADS = {"plan": ("techlens-plan.json", "application/json"),
             "report.json": ("report.json", "application/json"),
             "report.md": ("report.md", "text/markdown; charset=utf-8"),
             "bundle": ("techlens-report.zip", "application/zip")}


class Reader:
    def __init__(self, connection, *, deadline=15, inactivity=5):
        self.connection = connection
        self.buffer = bytearray()
        self.end = monotonic() + deadline
        self.inactivity = inactivity

    def receive(self, maximum):
        remaining = self.end - monotonic()
        if remaining <= 0:
            raise UIError("Request read deadline exceeded.", 408)
        self.connection.settimeout(min(self.inactivity, remaining))
        failed = False
        try:
            data = self.connection.recv(min(16384, maximum))
        except (TimeoutError, OSError):
            failed = True
        if failed:
            raise UIError("Request read deadline exceeded.", 408)
        if not data:
            raise UIError("Incomplete request body or headers.")
        self.buffer.extend(data)

    def line(self, limit):
        while True:
            index = self.buffer.find(b"\n")
            if index >= 0:
                if index + 1 > limit or self.buffer[index - 1:index] != b"\r":
                    raise UIError("Request line or header exceeds bounds.", 413)
                result = bytes(self.buffer[:index - 1])
                del self.buffer[:index + 1]
                return result
            if len(self.buffer) >= limit:
                raise UIError("Request line or header exceeds bounds.", 413)
            self.receive(limit - len(self.buffer))

    def body(self, length):
        while len(self.buffer) < length:
            self.receive(length - len(self.buffer))
        return bytes(self.buffer[:length])  # Any pipelined suffix is discarded on close.


class Handler(socketserver.BaseRequestHandler):
    def respond(self, status, data, content="application/json", extra=None):
        # Fixed diagnostics and packaged text also pass the captured-secret boundary.
        # ZIP bytes are built only from separately checked canonical text exports.
        if content != "application/zip":
            blocked = False
            try:
                self.server.coordinator.public.check(data.decode("utf-8"))
            except (ApplicationError, UnicodeError):
                blocked = True
            if blocked:
                status, data = 503, b""  # No diagnostic can disclose the blocked credential.
        names = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
                 408: "Request Timeout", 409: "Conflict", 410: "Gone", 413: "Content Too Large", 503: "Unavailable"}
        headers = {"Content-Type": content, "Content-Length": str(len(data)), "Connection": "close",
                   "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                   "Referrer-Policy": "no-referrer", "Content-Security-Policy": CSP,
                   "X-Frame-Options": "DENY", **(extra or {})}
        message = f"HTTP/1.1 {status} {names.get(status, 'Error')}\r\n"
        message += "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        self.request.settimeout(5)
        try:
            self.request.sendall(message.encode("ascii") + data)
        except OSError:
            pass

    def handle(self):
        error = None
        try:
            self.execute()
        except UIError as caught:
            error = (caught.status, str(caught))
        except ApplicationError:
            error = (400, "Local input or output integrity check failed.")
        except Exception:
            error = (503, "Local operation unavailable; no automatic replay.")
        if error:
            # Neither traceback nor input/header data is retained or logged.
            import json
            self.respond(error[0], json.dumps({"error": error[1]}).encode("utf-8"))

    def execute(self):
        server = self.server
        reader = Reader(self.request, deadline=server.read_deadline, inactivity=server.inactivity)
        raw = reader.line(4096)
        match = re.fullmatch(rb"(GET|POST) (/[^\s]*) HTTP/1\.[01]", raw)
        if not match:
            raise UIError("Unsupported request line.")
        method, path = (p.decode("ascii") for p in match.groups())
        if any(c in path for c in "%?#\\"):
            raise UIError("Unsupported local path.", 404)
        headers, total, count = {}, 0, 0
        while True:
            line = reader.line(8192)
            total += len(line) + 2
            if total > 32768:
                raise UIError("Headers exceed the local bound.", 413)
            if not line:
                break
            count += 1
            if count > 64 or b":" not in line:
                raise UIError("Invalid bounded headers.")
            key, value = line.split(b":", 1)
            if not re.fullmatch(rb"[A-Za-z0-9-]+", key) or any(c < 32 and c != 9 or c >= 127 for c in value):
                raise UIError("Invalid headers.")
            key, value = key.decode("ascii").lower(), value.decode("ascii").strip()
            if key in headers:
                raise UIError("Duplicate headers are not supported.")
            headers[key] = value
        if headers.get("host") != server.authority:
            raise UIError("Local Host validation failed.", 403)
        if headers.get("origin", server.origin) != server.origin:
            raise UIError("Local Origin validation failed.", 403)
        if headers.get("sec-fetch-site", "none") not in {"none", "same-origin"}:
            raise UIError("Cross-site requests are forbidden.", 403)
        if "transfer-encoding" in headers or "content-encoding" in headers:
            raise UIError("Unsupported request framing.")
        length = headers.get("content-length")
        if method == "POST":
            if headers.get("origin") != server.origin:
                raise UIError("Mutation requires the exact local Origin.", 403)
            if length is None or not re.fullmatch(r"[0-9]{1,9}", length):
                raise UIError("Mutation requires a bounded Content-Length.")
            if int(length) > 1048576:
                raise UIError("JSON body exceeds the local bound.", 413)
            if headers.get("content-type") != "application/json":
                raise UIError("Mutation requires application/json.")
            body = decode_json(reader.body(int(length)))  # All parsing before any action.
        elif length not in {None, "0"}:
            raise UIError("GET request body is not supported.")
        cookie = SimpleCookie()
        bad_cookie = False
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            bad_cookie = True
        if bad_cookie:
            raise UIError("Invalid local ownership cookie.", 403)
        supplied = cookie.get(server.cookie_name)
        supplied = supplied.value if supplied else None
        c = server.coordinator
        if method == "GET" and path == "/favicon.ico":
            self.respond(204, b"", "image/x-icon")
            return
        if method == "GET" and path in ASSETS:
            extra = None
            if path == "/":
                owner = c.owner(supplied, create=True)
                extra = {"Set-Cookie": f"{server.cookie_name}={owner}; HttpOnly; SameSite=Strict; Path=/"}
            name, mime = ASSETS[path]
            self.respond(200, files("techlens").joinpath("ui_assets", name).read_bytes(), mime, extra)
            return
        owner = c.owner(supplied)
        if method == "GET" and path == "/api/bootstrap":
            self.respond(200, c.public.json(c.bootstrap(owner)).encode())
            return
        import secrets
        if not secrets.compare_digest(headers.get("x-techlens-csrf", ""), c.owners[owner].csrf):
            raise UIError("Local CSRF validation failed.", 403)
        result, data, mime, extra = None, None, "application/json", None
        if method == "POST" and path in {"/api/requests/prepare", "/api/demos/start"}:
            operation = "prepare" if path.endswith("prepare") else "start"
            result = c.act(owner, operation, "owner", body)
        elif method == "GET" and re.fullmatch(r"/api/requests/[a-f0-9]{32}(?:/download)?", path):
            rid = path.split("/")[3]
            if path.endswith("/download"):
                data = c.prepared(owner, rid, download=True)
                extra = {"Content-Disposition": 'attachment; filename="techlens-request.json"'}
            else:
                result = c.prepared(owner, rid)
        elif re.fullmatch(r"/api/runs/[a-f0-9]{32}(?:/(?:plan|preview|decision|abandon|report|artifacts/retry|downloads/(?:plan|report.json|report.md|bundle)))?", path):
            parts = path.split("/")
            rid, suffix = parts[3], "/".join(parts[4:])
            if method == "GET":
                if suffix == "":
                    result = c.run_view(owner, rid)
                elif suffix == "plan":
                    result = c.plan(owner, rid)
                elif suffix == "report":
                    result = c.report(owner, rid)
                elif suffix.startswith("downloads/"):
                    kind = suffix.split("/")[1]
                    data = c.download(owner, rid, kind)
                    filename, mime = DOWNLOADS[kind]
                    extra = {"Content-Disposition": f'attachment; filename="{filename}"'}
                else:
                    raise UIError("Unsupported read route.", 405)
            elif suffix in {"preview", "decision", "abandon", "artifacts/retry"}:
                operation = {"artifacts/retry": "artifacts"}.get(suffix, suffix)
                if suffix == "decision":
                    if set(body) != {"action", "token", "revision", "data"} or body["action"] not in {"approve", "reject", "apply", "cancel"}:
                        raise UIError()
                    operation = body.pop("action")
                result = c.act(owner, operation, rid, body)
            else:
                raise UIError("Unsupported mutation route.", 405)
        else:
            raise UIError("Local route unavailable.", 404)
        if data is None:
            data = c.public.json(result).encode("utf-8")
        self.respond(200, data, mime, extra)


class LocalServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = False
    daemon_threads = False
    block_on_close = True
    request_queue_size = 8

    def server_bind(self):
        if self.server_address[1] != 0:
            return super().server_bind()  # Explicit ports never silently change.
        # OS port=0 may select browser-blocked service ports (observed: 6667).
        # Use the dynamic/private range, above the known restricted service ports.
        # Bound local collision handling; never retry permission/other failures.
        for _ in range(8):
            self.server_address = ("127.0.0.1", 49152 + secrets.randbelow(16384))
            try:
                return super().server_bind()
            except OSError as error:
                if error.errno not in {errno.EADDRINUSE, 10048}:
                    raise
        raise OSError(errno.EADDRINUSE, "No automatic loopback port available")

    def __init__(self, port, coordinator, *, read_deadline=15, inactivity=5):
        if type(port) is not int or not 0 <= port <= 65535:
            raise UIError("Invalid local port.")
        self.coordinator = coordinator
        self.workers = BoundedSemaphore(8)
        self.read_deadline, self.inactivity = read_deadline, inactivity
        self.cookie_name = "techlens_" + uuid4().hex
        super().__init__(("127.0.0.1", port), Handler)
        self.authority = f"127.0.0.1:{self.server_address[1]}"
        self.origin = "http://" + self.authority

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b"HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.workers.release()
            self.shutdown_request(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.workers.release()

    def handle_error(self, request, client_address):
        pass  # Never print tracebacks containing capabilities or supplied data.


def serve(port, env, write):
    coordinator = Coordinator(env)
    server = None
    failed = False
    try:
        server = LocalServer(port, coordinator)
    except OSError:
        failed = True
    if failed:
        coordinator.close()
        raise ApplicationError("Local UI could not bind its loopback port.", 6)
    try:
        write(f"TechLens offline local UI: {server.origin}/\nOpen this URL manually. "
              "No live execution; restart loses process-local runs. Press Ctrl+C to stop.\n")
        server.serve_forever(poll_interval=0.1)
    finally:
        with coordinator.lock:
            coordinator.closed = True
        try:
            write("Stopping admission and waiting for admitted synthetic work; no run will be replayed.\n")
        finally:
            try:
                server.server_close()
            finally:
                coordinator.close()
