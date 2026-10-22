"""Separate process-only saved views. No assessment objects or raw uploads retained."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import secrets

from techlens.saved_reports import PROJECTION_LIMIT, verify_saved_report
from techlens.ui_contracts import Action, UIError, parse


@dataclass
class SavedView:
    owner: str
    projection: bytes
    touched: float


@dataclass
class SavedTicket:
    owner: str
    operation: str
    resource: str
    revision: int
    digest: str | None = None
    receipt: dict | None = None


class ImportLease:
    """One global reader/validator, no queue; release is idempotent on every exit."""
    def __init__(self, store, owner):
        self.store, self.owner = store, owner
        self.reserved = False

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.release()

    def release(self):
        with self.store.c.lock:
            if self.store.lease is self:
                self.reserved = False
                self.store.lease = None


class SavedViewStore:
    def __init__(self, coordinator):
        self.c = coordinator
        self.views, self.tickets, self.revisions = {}, {}, {}
        self.lease = None
        self.closed = False

    def purge_owner(self, owner):
        self.views = {key: v for key, v in self.views.items() if v.owner != owner}
        self.tickets = {key: v for key, v in self.tickets.items() if v.owner != owner}
        self.revisions.pop(owner, None)

    def shutdown(self):
        with self.c.lock:
            self.closed = True
            self.views.clear()
            self.tickets.clear()
            self.revisions.clear()
            # An in-flight lease remains occupied until its handler finally exits.

    def expire(self):
        for key, view in list(self.views.items()):
            if self.c.clock() - view.touched >= 1800:
                del self.views[key]
                self.revisions[view.owner] = self.revisions.get(view.owner, 0) + 1
                self.tickets = {k: t for k, t in self.tickets.items()
                                if not (t.resource == key and t.digest is None)}

    def _owner(self, owner):
        self.c.owner(owner)
        self.expire()
        if self.closed or self.c.closed:
            raise UIError("Saved report interface is shutting down.", 503)

    def begin(self, owner):
        with self.c.lock:
            self._owner(owner)
            if self.lease is not None:
                raise UIError("Saved import capacity reached; no upload was admitted.", 503)
            lease = ImportLease(self, owner)
            self.lease = lease
            return lease

    def _ticket(self, owner, operation, resource):
        revision = self.revisions.get(owner, 0)
        # Retire obsolete unused controls; preserve small consumed receipts.
        self.tickets = {k: t for k, t in self.tickets.items()
                        if not (t.owner == owner and t.digest is None and t.revision != revision)}
        for key, ticket in self.tickets.items():
            if (ticket.owner, ticket.operation, ticket.resource, ticket.revision, ticket.digest) == (
                    owner, operation, resource, revision, None):
                return key
        if len(self.tickets) >= 64 or sum(t.owner == owner for t in self.tickets.values()) >= 16:
            raise UIError("Saved action capacity reached; no action was issued.", 503)
        key = secrets.token_urlsafe(32)
        self.tickets[key] = SavedTicket(owner, operation, resource, revision)
        return key

    def listing(self, owner):
        with self.c.lock:
            self._owner(owner)
            views = []
            for key, view in self.views.items():
                if view.owner == owner:
                    report = json.loads(view.projection)["snapshot"]["report"]
                    views.append({"id": key, "assessment_id": report["assessment_id"], "status": report["status"]})
            return {"revision": self.revisions.get(owner, 0), "open_token": self._ticket(owner, "open", "saved"),
                    "views": views}

    def read(self, owner, key):
        with self.c.lock:
            self._owner(owner)
            view = self.views.get(key)
            if view is None or view.owner != owner:
                raise UIError("Saved report unavailable; explicitly reopen the files.", 404)
            view.touched = self.c.clock()
            return {**json.loads(view.projection), "id": key, "revision": self.revisions.get(owner, 0),
                    "close_token": self._ticket(owner, "close", key)}

    def _action(self, owner, operation, resource, submitted):
        action = parse(Action, submitted)
        if set(submitted) != {"token", "revision", "data"}:
            raise UIError()
        digest = hashlib.sha256(json.dumps({"revision": action.revision, "data": action.data},
            sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        ticket = self.tickets.get(action.token)
        if not ticket or (ticket.owner, ticket.operation, ticket.resource) != (owner, operation, resource):
            raise UIError("Saved action unavailable or incorrectly bound.", 409)
        if ticket.digest is not None:
            if ticket.digest != digest:
                raise UIError("Conflicting reuse of a saved action.", 409)
            return action, ticket, digest, deepcopy(ticket.receipt)
        if ticket.revision != action.revision or ticket.revision != self.revisions.get(owner, 0):
            raise UIError("Stale saved action; refresh opened saved reports.", 409)
        return action, ticket, digest, None

    def open(self, owner, submitted, lease):
        with self.c.lock:
            self._owner(owner)
            if self.lease is not lease or lease.owner != owner:
                raise UIError("Saved import lease unavailable.", 409)
            action, ticket, digest, replay = self._action(owner, "open", "saved", submitted)
            if replay is not None:
                return replay  # Reconcile before new count/byte/revision reservation.
            owned = [v for v in self.views.values() if v.owner == owner]
            if (len(owned) >= 2 or len(self.views) >= 8 or
                    sum(len(v.projection) for v in owned) + PROJECTION_LIMIT > 8 * 1024**2 or
                    sum(len(v.projection) for v in self.views.values()) + PROJECTION_LIMIT > 32 * 1024**2):
                raise UIError("Saved view capacity reached; close a saved view before opening another.", 503)
            lease.reserved = True  # Global lease makes count+max-byte reservation atomic.
            ticket.digest = digest
            ticket.receipt = {"status": "checking"}
            revision = ticket.revision
        # No coordinator lock while parsing/decompressing/checking untrusted bytes.
        snapshot = None
        try:
            snapshot = verify_saved_report(action.data, self.c.public)
        except Exception:
            pass  # Fixed failed receipt, including unexpected interruption; no source retained.
        with self.c.lock:
            self.c._expire()
            self.expire()
            # Never touch/recreate an expired owner or publish after shutdown/change.
            valid = (not self.closed and not self.c.closed and owner in self.c.owners and
                     self.lease is lease and self.tickets.get(action.token) is ticket and
                     self.revisions.get(owner, 0) == revision)
            if not valid:
                if self.tickets.get(action.token) is ticket:
                    ticket.receipt = {"status": "failed", "error": "Saved import expired or changed; no view was published."}
                raise UIError("Saved import expired or changed; no saved view was published.", 409)
            if snapshot is None:
                ticket.receipt = {"status": "failed", "error": "Saved report file or supported structure check failed."}
            else:
                key = "saved-" + secrets.token_hex(16)
                self.views[key] = SavedView(owner, snapshot.projection, self.c.clock())
                self.revisions[owner] = revision + 1
                ticket.receipt = {"status": "opened", "saved_id": key}
            return deepcopy(ticket.receipt)

    def close(self, owner, key, submitted):
        with self.c.lock:
            self._owner(owner)
            action, ticket, digest, replay = self._action(owner, "close", key, submitted)
            if replay is not None:
                return replay
            if action.data:
                raise UIError()
            view = self.views.get(key)
            if view is None or view.owner != owner:
                raise UIError("Saved report unavailable.", 404)
            del self.views[key]
            self.revisions[owner] = ticket.revision + 1
            ticket.digest, ticket.receipt = digest, {"status": "closed", "saved_id": key}
            return deepcopy(ticket.receipt)
