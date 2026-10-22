import json
from threading import Event, Thread

import pytest

from techlens.ui_contracts import UIError
from techlens.ui_coordinator import Coordinator
from test_saved_reports import exported, input_files
from ui_support import BrowserHTTP, raw, serving


def submission(c, owner, files=None):
    listing = c.saved.listing(owner)
    return {"token": listing["open_token"], "revision": listing["revision"],
            "data": input_files(exported() if files is None else files)}


def opened(c, owner, body=None):
    body = submission(c, owner) if body is None else body
    with c.saved.begin(owner) as lease:
        return c.saved.open(owner, body, lease)


@pytest.fixture
def saved():
    c = Coordinator({})
    owner = c.owner(create=True)
    yield c, owner
    c.close()


def test_saved_immutable_projection_namespace_owner_isolation_and_no_execution_revision(saved):
    c, owner = saved
    body = submission(c, owner)
    before = c.owners[owner].revision
    receipt = opened(c, owner, body)
    key = receipt["saved_id"]
    assert key.startswith("saved-") and len(key) == 38
    view = c.saved.read(owner, key)
    assert isinstance(c.saved.views[key].projection, bytes)
    view["snapshot"]["report"]["summary"] = "not retained"
    assert c.saved.read(owner, key)["snapshot"]["report"]["summary"] != "not retained"
    assert c.owners[owner].revision == before
    assert not c.runs and not c.tickets and c.jobs == 0
    other = c.owner(create=True)
    with pytest.raises(UIError):
        c.saved.read(other, key)
    with pytest.raises(UIError):
        c.run_view(owner, key)
    with pytest.raises(UIError):
        c.saved.read(owner, "a" * 32)
    assert not c.saved.listing(other)["views"]
    assert opened(c, owner, body) == receipt


def test_close_replay_never_resurrects_and_releases_count_bytes(saved):
    c, owner = saved
    body = submission(c, owner)
    original = opened(c, owner, body)
    key = original["saved_id"]
    opened(c, owner)
    denied = submission(c, owner)
    with pytest.raises(UIError, match="capacity"):
        opened(c, owner, denied)
    assert c.saved.tickets[denied["token"]].digest is None
    view = c.saved.read(owner, key)
    close = {"token": view["close_token"], "revision": view["revision"], "data": {}}
    receipt = c.saved.close(owner, key, close)
    assert c.saved.close(owner, key, close) == receipt
    assert opened(c, owner, body) == original
    assert key not in c.saved.views
    assert len(c.saved.views) == 1
    assert opened(c, owner)["status"] == "opened"


def test_failed_validation_reconciles_fixed_receipt_without_upload_retention(saved):
    c, owner = saved
    bad = submission(c, owner, {"report.json": b"bad"})
    receipt = opened(c, owner, bad)
    assert receipt["status"] == "failed"
    assert opened(c, owner, bad) == receipt
    assert not c.saved.views and c.saved.lease is None
    assert len(json.dumps(c.saved.tickets[bad["token"]].receipt)) < 1024
    assert not hasattr(c.saved.tickets[bad["token"]], "data")
    conflict = {**bad, "data": input_files(exported())}
    with pytest.raises(UIError, match="Conflicting"):
        opened(c, owner, conflict)
    # A deliberate fresh intent after a failure is permitted, not automatic replay.
    assert opened(c, owner)["status"] == "opened"


@pytest.mark.parametrize("failure", ["read", "json", "action", "verification"])
def test_lease_exception_cleanup_reusable(saved, monkeypatch, failure):
    c, owner = saved
    with c.saved.begin(owner) as lease:
        try:
            if failure in ("read", "json"):
                raise RuntimeError("fault")
            if failure == "action":
                c.saved.open(owner, {}, lease)
            else:
                def fail(*args):
                    raise RuntimeError("injected verifier interruption")
                with monkeypatch.context() as m:
                    m.setattr("techlens.ui_saved_reports.verify_saved_report", fail)
                    assert c.saved.open(owner, submission(c, owner), lease)["status"] == "failed"
        except (RuntimeError, UIError):
            assert failure != "verification"
    assert c.saved.lease is None and not c.saved.views
    assert opened(c, owner)["status"] == "opened"


@pytest.mark.parametrize("transition", ["expiry", "shutdown", "close"])
def test_controlled_inflight_publish_checks_expiry_shutdown_and_revision(monkeypatch, transition):
    clock = [0]
    c = Coordinator({}, clock=lambda: clock[0])
    owner = c.owner(create=True)
    key = opened(c, owner)["saved_id"]
    body = submission(c, owner)
    from techlens.ui_saved_reports import verify_saved_report
    entered, release = Event(), Event()
    errors = []
    def verify(*args):
        entered.set()
        assert release.wait(10)
        return verify_saved_report(*args)
    monkeypatch.setattr("techlens.ui_saved_reports.verify_saved_report", verify)
    def worker():
        try:
            opened(c, owner, body)
        except UIError as error:
            errors.append(str(error))
    thread = Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(10)
        with pytest.raises(UIError, match="capacity"):
            c.saved.begin(owner)
        # The global lock is free during verification; unrelated UI reads work.
        assert c.bootstrap(owner)["runs"] == []
        if transition == "expiry":
            clock[0] = 1801
            with pytest.raises(UIError):
                c.owner(owner)
        elif transition == "shutdown":
            c.close()
        else:
            view = c.saved.read(owner, key)
            c.saved.close(owner, key, {"token": view["close_token"], "revision": view["revision"], "data": {}})
        release.set()
        thread.join(10)
        assert not thread.is_alive() and errors
        assert not c.saved.views and c.saved.lease is None
        assert not c.runs
        if transition == "expiry":
            assert owner not in c.owners and not c.saved.tickets
    finally:
        release.set()
        thread.join(10)
        c.close()


def test_independent_view_idle_expiry_and_ticket_bounds():
    clock = [0]
    c = Coordinator({}, clock=lambda: clock[0])
    try:
        owner = c.owner(create=True)
        key = opened(c, owner)["saved_id"]
        clock[0] = 1799
        c.bootstrap(owner)  # Keep owner, not saved view, alive.
        clock[0] = 1801
        assert not c.saved.listing(owner)["views"]
        with pytest.raises(UIError):
            c.saved.read(owner, key)
        for _ in range(20):
            try:
                body = submission(c, owner, {"report.json": b"bad"})
                opened(c, owner, body)
            except UIError as error:
                assert "capacity" in str(error)
                break
        else:
            pytest.fail("Unbounded saved action receipts")
        assert len(c.saved.tickets) <= 16
    finally:
        c.close()


def test_global_owner_view_bytes_and_import_limits_are_separate():
    c = Coordinator({})
    try:
        owners = [c.owner(create=True) for _ in range(4)]
        for owner in owners:
            for _ in range(2):
                assert opened(c, owner)["status"] == "opened"
        assert len(c.saved.views) == 8
        assert sum(len(v.projection) for v in c.saved.views.values()) <= 32 * 1024**2
        for owner in owners:
            assert sum(len(v.projection) for v in c.saved.views.values() if v.owner == owner) <= 8 * 1024**2
        lease = c.saved.begin(owners[0])
        try:
            with pytest.raises(UIError, match="capacity"):
                c.saved.begin(owners[1])
        finally:
            lease.release()
            lease.release()
        assert c.saved.lease is None
        assert not c.runs and c.jobs == 0 and not c.tickets
    finally:
        c.close()


def test_http_both_inputs_restart_and_execution_unavailable(monkeypatch):
    files = exported()
    with serving(monkeypatch) as server:
        b = BrowserHTTP(server)
        b.boot()
        for format in ("zip", "files"):
            listing = json.loads(b.request("/api/saved-reports")[2])
            body = {"token": listing["open_token"], "revision": listing["revision"], "data": input_files(files, format)}
            code, _, raw_body = b.request("/api/saved-reports/open", body)
            assert code == 200
            key = json.loads(raw_body)["saved_id"]
            view = json.loads(b.request("/api/saved-reports/" + key)[2])
            assert view["snapshot"] == json.loads(files["report.json"])
        assert not server.coordinator.runs
        old_cookie = b.cookie
    with serving(monkeypatch) as server:
        b = BrowserHTTP(server)
        b.cookie = old_cookie
        assert b.request("/api/saved-reports/" + key)[0] == 410
        b.boot()
        assert b.request("/api/saved-reports/" + key)[0] == 404
        assert not json.loads(b.request("/api/saved-reports")[2])["views"]


def test_actual_http_large_read_slot_does_not_lock_other_ui_and_denies_overlap(monkeypatch):
    from techlens.ui_server import Reader
    original = Reader.body
    entered, release = Event(), Event()
    held = [False]
    def body(reader, length):
        if not held[0]:
            held[0] = True
            entered.set()
            assert release.wait(10)
        return original(reader, length)
    with serving(monkeypatch) as server:
        b = BrowserHTTP(server)
        b.boot()
        listing = json.loads(b.request("/api/saved-reports")[2])
        submitted = {"token": listing["open_token"], "revision": listing["revision"], "data": input_files(exported())}
        monkeypatch.setattr(Reader, "body", body)
        results = []
        thread = Thread(target=lambda: results.append(b.request("/api/saved-reports/open", submitted)))
        thread.start()
        try:
            assert entered.wait(10)
            assert server.coordinator.saved.lease is not None
            assert json.loads(b.request("/api/bootstrap")[2])["runs"] == []
            assert b.request("/api/saved-reports/open", submitted)[0] == 503
        finally:
            release.set()
            thread.join(10)
        assert not thread.is_alive() and json.loads(results[0][2])["status"] == "opened"
        assert server.coordinator.saved.lease is None


@pytest.mark.parametrize("fault", ["csrf", "origin", "oversize", "disconnect", "json", "base64"])
def test_http_early_denial_and_cleanup_then_actual_reuse(monkeypatch, fault):
    with serving(monkeypatch, read_deadline=0.5, inactivity=0.2) as server:
        b = BrowserHTTP(server)
        b.boot()
        listing = json.loads(b.request("/api/saved-reports")[2])
        body = {"token": listing["open_token"], "revision": listing["revision"], "data": input_files(exported())}
        headers = (f"POST /api/saved-reports/open HTTP/1.1\r\nHost: {server.authority}\r\n"
            f"Origin: {'https://wrong.invalid' if fault == 'origin' else server.origin}\r\nCookie: {b.cookie}\r\n"
            f"X-TechLens-CSRF: {'wrong' if fault == 'csrf' else b.csrf}\r\nContent-Type: application/json\r\n")
        payload = b'{"x":1,"x":2}' if fault == "json" else b"{}"
        length = 7 * 1024**2 + 1 if fault == "oversize" else 100 if fault in ("disconnect", "csrf", "origin") else len(payload)
        if fault == "base64":
            bad = json.loads(json.dumps(body))
            bad["data"]["files"][0]["base64"] = "!!!!"
            assert json.loads(b.request("/api/saved-reports/open", bad)[2])["status"] == "failed"
        else:
            response = raw(server, (headers + f"Content-Length: {length}\r\n\r\n").encode() + payload, half_close=True)
            assert not response.startswith(b"HTTP/1.1 200")
        assert server.coordinator.saved.lease is None
        fresh = json.loads(b.request("/api/saved-reports")[2])
        body.update(token=fresh["open_token"], revision=fresh["revision"])
        assert json.loads(b.request("/api/saved-reports/open", body)[2])["status"] == "opened"
        assert not server.coordinator.runs
