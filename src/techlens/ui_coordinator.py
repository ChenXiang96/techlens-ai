"""Process-local UI ownership/admission. This never creates live dependencies."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import secrets
from threading import RLock
from time import monotonic

from techlens.application import AssessmentSession, demo_dependencies, private_execution
from techlens.demo import demo_request
from techlens.presentation import PublicOutput
from techlens.ui_artifacts import artifacts
from techlens.ui_contracts import Action, PlanEdit, RequestForm, UIError, parse
from techlens.ui_presentation import plan_view, plan_changes, report_view, request_view
from techlens.ui_saved_reports import SavedViewStore


def identifier():
    return secrets.token_hex(16)


@dataclass
class Owner:
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    revision: int = 0
    touched: float = field(default_factory=monotonic)
    prepared: tuple | None = None


@dataclass
class Run:
    owner: str
    scenario: str
    phase: str = "starting"
    revision: int = 0
    session: object = None
    state: dict | None = None
    proposal: tuple | None = None
    abandoned: bool = False
    downloads: dict = field(default_factory=dict)
    artifact_error: bool = False
    active: bool = True


@dataclass
class Ticket:
    owner: str
    operation: str
    resource: str
    revision: int
    proposal: str | None = None
    identity: str | None = None
    receipt: dict | None = None


class Coordinator:
    """No persistence/recovery. Admission receipts are not paid attempt records."""
    def __init__(self, env, *, session_factory=AssessmentSession, scheduler=None, clock=monotonic):
        self.public = PublicOutput(env)
        self.clock = clock
        self.lock = RLock()
        self.owners, self.runs, self.tickets = {}, {}, {}
        self.jobs = 0
        self.closed = False
        self.saved = SavedViewStore(self)
        self.session_factory = session_factory
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="techlens-ui")
        self.schedule = scheduler or self.pool.submit

    def _expire(self):
        for key, owner in list(self.owners.items()):
            if self.clock() - owner.touched < 1800:
                continue
            owned = [(rid, r) for rid, r in self.runs.items() if r.owner == key]
            if any(r.active for _, r in owned):
                continue
            for rid, run in owned:
                if run.session:
                    run.session.close()
                del self.runs[rid]
            self.tickets = {t: v for t, v in self.tickets.items() if v.owner != key}
            self.saved.purge_owner(key)
            del self.owners[key]

    def owner(self, cookie=None, *, create=False):
        with self.lock:
            self._expire()
            if cookie in self.owners:
                self.owners[cookie].touched = self.clock()
                return cookie
            if not create or self.closed:
                raise UIError("Local session unavailable. Open the application home page; no run was restored.", 410)
            if len(self.owners) >= 4:
                raise UIError("Local owner capacity reached. No session was created.", 503)
            key = secrets.token_urlsafe(32)
            self.owners[key] = Owner(touched=self.clock())
            return key

    def _run(self, owner, resource):
        self.owner(owner)
        run = self.runs.get(resource)
        if run is None or run.owner != owner:
            raise UIError("Run unavailable in this local session; no run was recreated.", 404)
        return run

    def _ticket(self, owner, operation, resource, revision, proposal=None):
        for token, ticket in self.tickets.items():
            if (ticket.owner, ticket.operation, ticket.resource, ticket.revision, ticket.proposal) == (
                    owner, operation, resource, revision, proposal) and ticket.identity is None:
                return token
        # Retire obsolete UNUSED controls only. Consumed receipts remain reconcilable.
        self.tickets = {t: v for t, v in self.tickets.items() if not (
            v.owner == owner and v.resource == resource and v.identity is None and
            (v.revision != revision or (v.operation == operation and v.proposal != proposal)))}
        if len(self.tickets) >= 64:
            raise UIError("Local action capacity reached; no action was issued.", 503)
        token = secrets.token_urlsafe(32)
        self.tickets[token] = Ticket(owner, operation, resource, revision, proposal)
        return token

    def bootstrap(self, owner):
        with self.lock:
            item = self.owners[self.owner(owner)]
            actions = {} if self.closed else {op: self._ticket(owner, op, "owner", item.revision)
                                             for op in ("prepare", "start")}
            return {"csrf": item.csrf, "revision": item.revision, "actions": actions,
                    "prepared_id": item.prepared[0] if item.prepared else None,
                    "runs": [{"id": rid, "phase": r.phase, "scenario": r.scenario}
                             for rid, r in self.runs.items() if r.owner == owner],
                    "notice": "Process-local offline demos only. Restart loses sessions; never replay automatically."}

    def run_view(self, owner, rid):
        with self.lock:
            run = self._run(owner, rid)
            operations = []
            if not self.closed:
                if not run.abandoned:
                    operations.append("abandon")
                if run.phase == "awaiting_review" and not run.abandoned:
                    operations += ["approve", "reject", "preview"]
                    if run.proposal:
                        operations += ["apply", "cancel"]
                if run.phase == "terminal" and run.artifact_error and not run.active:
                    operations.append("artifacts")
            actions = {op: self._ticket(owner, op, rid, run.revision,
                       run.proposal[0] if op in {"apply", "cancel"} else None) for op in operations}
            return {"id": rid, "scenario": run.scenario, "phase": run.phase, "revision": run.revision,
                    "active": run.active, "abandoned": run.abandoned, "actions": actions,
                    "proposal_id": run.proposal[0] if run.proposal else None,
                    "downloads": sorted(run.downloads), "artifact_error": run.artifact_error,
                    "notice": "Abandonment blocks review; it cannot recall work already started or release liability."}

    def plan(self, owner, rid):
        with self.lock:
            run = self._run(owner, rid)
            if not run.state or "research_plan" not in run.state:
                raise UIError("Plan is not available.", 409)
            result = plan_view(run.session.request, run.state["research_plan"], self.public)
            if run.proposal:
                result["proposal"] = {"id": run.proposal[0],
                    "plan": run.proposal[1].model_dump(mode="json"),
                    "changes": plan_changes(run.state["research_plan"], run.proposal[1])}
            # Projection and controls are captured under the SAME coordinator lock.
            # A preceding status GET is not authority for this review snapshot.
            result["review"] = self.run_view(owner, rid)
            return result

    def prepared(self, owner, rid, *, download=False):
        with self.lock:
            item = self.owners[self.owner(owner)]
            if not item.prepared or item.prepared[0] != rid:
                raise UIError("Prepared request unavailable.", 404)
            return item.prepared[2] if download else deepcopy(item.prepared[1])

    def report(self, owner, rid):
        with self.lock:
            run = self._run(owner, rid)
            if run.phase != "terminal":
                raise UIError("No validated terminal report is available.", 409)
            return report_view(run.state, self.public, run.scenario)

    def download(self, owner, rid, kind):
        with self.lock:
            run = self._run(owner, rid)
            if kind == "plan" and run.state and "research_plan" in run.state:
                # Read-only serialization; no graph or artifact job.
                return self.public.json(plan_view(run.session.request, run.state["research_plan"],
                                                   self.public)["research_plan"]).encode("utf-8")
            if kind not in run.downloads:
                raise UIError("Cached download unavailable. No recovery work was started.", 404)
            return run.downloads[kind]

    def act(self, owner, operation, resource, submitted):
        """M-01: reconcile original content BEFORE current phase/revision checks."""
        action = parse(Action, submitted)
        self.public.check(action.data)
        # Shape checks precede token reconciliation, but not domain checks/UUID allocation.
        if operation == "prepare":
            payload = parse(RequestForm, action.data)
        elif operation == "preview":
            payload = parse(PlanEdit, action.data)
        elif operation == "start":
            if set(action.data) != {"scenario"} or action.data["scenario"] not in ("single", "comparison", "partial"):
                raise UIError("Only packaged synthetic scenarios are available.")
            payload = action.data["scenario"]
        elif operation == "apply":
            if set(action.data) != {"proposal_id"} or not isinstance(action.data["proposal_id"], str):
                raise UIError()
        elif operation in {"approve", "reject", "cancel", "abandon", "artifacts"}:
            if action.data:
                raise UIError()
        else:
            raise UIError()
        identity = hashlib.sha256(json.dumps({"revision": action.revision, "data": action.data},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
        with self.lock:
            self.owner(owner)
            ticket = self.tickets.get(action.token)
            if not ticket or (ticket.owner, ticket.operation, ticket.resource) != (owner, operation, resource):
                raise UIError("Action unavailable or incorrectly bound.", 409)
            if ticket.identity is not None:
                if ticket.identity != identity:
                    raise UIError("Conflicting reuse of an accepted action.", 409)
                return deepcopy(ticket.receipt)
            if self.closed:
                raise UIError("Local interface is shutting down.", 503)
            run = None if resource == "owner" else self._run(owner, resource)
            revision = run.revision if run else self.owners[owner].revision
            if action.revision != ticket.revision or revision != ticket.revision:
                raise UIError("Stale action. Refresh retained state; no work was repeated.", 409)
            job = operation in {"start", "approve", "reject", "apply", "artifacts"}
            if job and (self.jobs >= 2 or any(r.active for r in self.runs.values() if r.owner == owner)):
                raise UIError("Local job capacity reached. No action was accepted.", 503)
            validated = None
            if operation == "prepare":
                validated = payload.request()
                projected = request_view(validated, self.public)
                encoded = self.public.json(validated.model_dump(mode="json")).encode("utf-8")
            elif operation == "start":
                if len(self.runs) >= 8 or sum(r.owner == owner for r in self.runs.values()) >= 2:
                    raise UIError("Retained-run capacity reached; no run was created.", 503)
            else:
                if run.active and operation != "abandon":
                    raise UIError("Run is busy; no action was accepted.", 409)
                if operation == "abandon":
                    if run.abandoned:
                        raise UIError("Run already abandoned.", 409)
                elif operation == "artifacts":
                    if run.phase != "terminal" or not run.artifact_error:
                        raise UIError("Artifact retry is not available.", 409)
                elif run.phase != "awaiting_review" or run.abandoned:
                    raise UIError("Review is no longer pending.", 409)
                elif operation == "preview":
                    validated = payload.plan(run.session.request)
                    self.public.check(validated.model_dump(mode="json"))
                elif operation in {"apply", "cancel"}:
                    if not run.proposal or ticket.proposal != run.proposal[0]:
                        raise UIError("Proposal is unavailable or stale.", 409)
                    if operation == "apply" and action.data["proposal_id"] != run.proposal[0]:
                        raise UIError("Proposal substitution rejected.", 409)
                    if operation == "apply":
                        validated = run.session.decision("modify", run.proposal[1])
                elif operation in {"approve", "reject"}:
                    validated = run.session.decision(operation)
            # No domain failure/capacity denial has consumed the ticket yet.
            job_id = identifier()
            receipt = {"id": job_id, "operation": operation, "status": "running" if job else "completed"}
            ticket.identity, ticket.receipt = identity, receipt
            if operation == "prepare":
                rid = identifier()
                self.owners[owner].prepared = (rid, projected, encoded)
                self.owners[owner].revision += 1
                receipt["request_id"] = rid
            elif operation == "start":
                rid = identifier()
                run = Run(owner, payload)
                self.runs[rid] = run
                self.owners[owner].revision += 1
                receipt["run_id"] = rid
            else:
                rid = resource
                receipt["run_id"] = rid
                run.revision += 1
                if operation == "preview":
                    run.proposal = (identifier(), validated)
                    receipt["proposal_id"] = run.proposal[0]
                elif operation == "cancel":
                    run.proposal = None
                elif operation == "abandon":
                    run.abandoned = True
                    run.proposal = None
                    if not run.active and run.phase != "terminal":
                        run.phase = "abandoned"
                elif operation != "artifacts":
                    run.proposal = None
                    run.phase = "executing_demo"
            if job:
                run.active = True
                self.jobs += 1
                failed = False
                try:
                    self.schedule(self._work, rid, operation, validated, ticket)
                except Exception:
                    failed = True
                if failed:
                    self.jobs -= 1
                    run.active = False
                    receipt["status"] = "failed"
                    if operation == "artifacts":
                        run.artifact_error = True
                    else:
                        run.phase = "execution_error"
                    run.revision += 1
            return deepcopy(receipt)

    def _work(self, rid, operation, decision, ticket):
        run = self.runs[rid]
        success = False
        state, downloads = None, None
        try:
            with private_execution():
                if operation == "start":
                    session = self.session_factory(demo_request(run.scenario), demo_dependencies(run.scenario))
                    with self.lock:
                        run.session = session
                    state = session.start()
                elif operation == "artifacts":
                    state = run.state
                else:
                    state = run.session.resume(decision)
                if state.get("report") is not None:
                    report_view(state, self.public, run.scenario)
                    try:
                        downloads = artifacts(state, self.public, run.scenario)
                    except Exception:
                        pass  # Report validity is distinct from artifact availability.
                success = True
        except Exception:
            pass  # Fixed receipt only; no exception/request/raw state enters the API.
        finally:
            with self.lock:
                if success:
                    run.state = state
                    if state.get("report") is not None:
                        run.phase = "terminal"
                        run.artifact_error = True
                        if downloads is not None:
                            total = sum(len(b) for r in self.runs.values() for b in r.downloads.values())
                            total -= sum(len(b) for b in run.downloads.values())
                            if total + sum(map(len, downloads.values())) <= 32 * 1024**2:
                                run.downloads = downloads  # Publish all or none.
                                run.artifact_error = False
                    elif state.get("__interrupt__"):
                        run.phase = "abandoned" if run.abandoned else "awaiting_review"
                    else:
                        run.phase = "execution_error"
                    ticket.receipt["status"] = "completed"
                else:
                    if operation != "artifacts":
                        run.phase = "execution_error"
                    else:
                        run.artifact_error = True
                    ticket.receipt["status"] = "failed"
                run.active = False
                run.revision += 1
                self.jobs -= 1
                if run.session and (run.phase != "awaiting_review" or run.abandoned):
                    run.session.close()

    def close(self):
        with self.lock:
            self.closed = True
            self.saved.shutdown()
        # No forced preemption, graph replay, reconciliation or liability release.
        self.pool.shutdown(wait=True)
        with self.lock:
            for run in self.runs.values():
                if run.session:
                    run.session.close()
