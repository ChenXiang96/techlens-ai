from copy import deepcopy
from threading import Event, Thread
import pytest

from techlens.application import AssessmentSession
from techlens.ui_contracts import UIError
from techlens.ui_coordinator import Coordinator
from ui_support import Scheduler, action, start


@pytest.fixture
def local():
    scheduler = Scheduler()
    c = Coordinator({}, scheduler=scheduler)
    owner = c.owner(create=True)
    yield c, owner, scheduler
    while scheduler.calls:
        scheduler.finish()
    c.close()


def test_first_owner_refresh_isolation_capacity(local):
    c, owner, _ = local
    assert c.owner(owner, create=True) == owner
    assert not c.runs
    assert c.bootstrap(owner)["csrf"] == c.bootstrap(owner)["csrf"]
    for _ in range(3):
        c.owner(create=True)
    with pytest.raises(UIError):
        c.owner(create=True)
    with pytest.raises(UIError):
        c.run_view(owner, "missing")


def test_start_retry_running_completed_conflict_once(local):
    c, owner, scheduler = local
    submitted = action(c, owner, "start", payload={"scenario": "single"})
    receipt = c.act(owner, "start", "owner", submitted)
    assert c.act(owner, "start", "owner", submitted) == receipt
    assert len(scheduler.calls) == 1
    scheduler.finish()
    retained = c.runs[receipt["run_id"]].session
    assert retained.state["__interrupt__"]
    assert retained.state["current_round"] == 0
    assert retained.state["github_signals"] == []
    assert c.act(owner, "start", "owner", submitted)["status"] == "completed"
    conflict = deepcopy(submitted)
    conflict["data"]["scenario"] = "comparison"
    with pytest.raises(UIError):
        c.act(owner, "start", "owner", conflict)
    assert not scheduler.calls
    assert c.runs[receipt["run_id"]].session is retained


@pytest.mark.parametrize("scenario,status", [("single", "completed"), ("comparison", "completed"), ("partial", "partial")])
def test_real_session_approved_and_artifacts(local, scenario, status):
    c, owner, scheduler = local
    rid, _ = start(c, owner, scheduler, scenario)
    plan = c.plan(owner, rid)
    assert len(plan["research_plan"]["comparison_targets"]) == (2 if scenario == "comparison" else 1)
    submitted = action(c, owner, "approve", rid)
    receipt = c.act(owner, "approve", rid, submitted)
    assert c.act(owner, "approve", rid, submitted) == receipt
    scheduler.finish()
    assert c.act(owner, "approve", rid, submitted)["status"] == "completed"
    assert c.report(owner, rid)["report"]["status"] == status
    assert c.runs[rid].state["current_round"] == 1
    assert len(c.report(owner, rid)["report"]["sections"]) == 13
    assert set(c.runs[rid].downloads) == {"report.json", "report.md", "manifest.json", "bundle"}


def test_reject_zero_research(local):
    c, owner, scheduler = local
    rid, _ = start(c, owner, scheduler)
    c.act(owner, "reject", rid, action(c, owner, "reject", rid))
    scheduler.finish()
    assert c.report(owner, rid)["report"]["status"] == "rejected"
    state = c.runs[rid].state
    assert state["current_round"] == 0
    assert state["github_signals"] == state["web_research_results"] == []


def editable(c, owner, rid):
    plan = c.plan(owner, rid)["research_plan"]
    value = {key: plan[key] for key in ("objective", "research_questions", "engineering_checks", "web_tasks")}
    for task in value["web_tasks"]:
        del task["task_id"]
    value["objective"] = "Modified local objective"
    return value


def test_preview_cancel_apply_and_substitution(local):
    c, owner, scheduler = local
    rid, _ = start(c, owner, scheduler)
    session = c.runs[rid].session
    before = session.graph.get_state(session.config)
    invalid = editable(c, owner, rid)
    invalid["web_tasks"][0]["candidate_id"] = "candidate-999"
    with pytest.raises(UIError):
        c.act(owner, "preview", rid, action(c, owner, "preview", rid, invalid))
    assert session.graph.get_state(session.config) == before
    c.act(owner, "preview", rid, action(c, owner, "preview", rid, editable(c, owner, rid)))
    proposal = c.runs[rid].proposal[0]
    wrong = action(c, owner, "apply", rid, {"proposal_id": "replacement"})
    with pytest.raises(UIError):
        c.act(owner, "apply", rid, wrong)
    c.act(owner, "cancel", rid, action(c, owner, "cancel", rid))
    assert session.graph.get_state(session.config) == before
    assert c.runs[rid].proposal is None
    c.act(owner, "preview", rid, action(c, owner, "preview", rid, editable(c, owner, rid)))
    c.act(owner, "apply", rid, action(c, owner, "apply", rid, {"proposal_id": c.runs[rid].proposal[0]}))
    scheduler.finish()
    assert c.runs[rid].session is session
    assert c.runs[rid].state["research_plan"].objective == "Modified local objective"
    assert proposal != c.runs[rid].proposal


def test_schedule_failure_receipt_never_replays():
    calls = []
    def fail(*args):
        calls.append(args)
        raise RuntimeError("sensitive scheduling details")
    c = Coordinator({}, scheduler=fail)
    try:
        owner = c.owner(create=True)
        a = action(c, owner, "start", payload={"scenario": "single"})
        receipt = c.act(owner, "start", "owner", a)
        assert receipt["status"] == "failed"
        assert c.act(owner, "start", "owner", a) == receipt
        assert len(calls) == 1
        assert c.jobs == 0
        assert c.runs[receipt["run_id"]].phase == "execution_error"
    finally:
        c.close()


def test_controlled_overlap_actual_start_resume_counts():
    entered, release, resumed, done = Event(), Event(), Event(), Event()
    calls = {"start": 0, "resume": 0}
    class Controlled(AssessmentSession):
        def start(self):
            calls["start"] += 1
            entered.set()
            assert release.wait(10)
            return super().start()
        def resume(self, decision):
            calls["resume"] += 1
            resumed.set()
            assert done.wait(10)
            return super().resume(decision)
    c = Coordinator({}, session_factory=Controlled)
    try:
        owner = c.owner(create=True)
        a = action(c, owner, "start", payload={"scenario": "single"})
        receipt = c.act(owner, "start", "owner", a)
        assert entered.wait(10)
        assert c.act(owner, "start", "owner", a) == receipt
        release.set()
        c.pool.submit(lambda: None).result(10)
        # Use a deterministic completion barrier, not a timing sleep.
        with c.lock:
            slot = c.runs[receipt["run_id"]]
        # The session saver interrupt is reached before admission to review.
        c.pool.shutdown(wait=True)
        assert slot.phase == "awaiting_review"
        # Reuse a fresh UI job pool, not a graph/session/executor.
        from concurrent.futures import ThreadPoolExecutor
        c.pool = ThreadPoolExecutor(max_workers=2)
        c.schedule = c.pool.submit
        rid = receipt["run_id"]
        b = action(c, owner, "approve", rid)
        original = c.act(owner, "approve", rid, b)
        assert resumed.wait(10)
        assert c.act(owner, "approve", rid, b) == original
        done.set()
        c.pool.shutdown(wait=True)
        assert c.act(owner, "approve", rid, b)["status"] == "completed"
        assert calls == {"start": 1, "resume": 1}
    finally:
        release.set()
        done.set()
        c.close()


def test_owner_capacity_abandon_and_restart(local):
    c, owner, scheduler = local
    rid, _ = start(c, owner, scheduler)
    other = c.owner(create=True)
    with pytest.raises(UIError):
        c.plan(other, rid)
    c.act(owner, "abandon", rid, action(c, owner, "abandon", rid))
    assert c.runs[rid].phase == "abandoned"
    assert c.runs[rid].state["current_round"] == 0
    with pytest.raises(UIError):
        c.act(owner, "approve", rid, {"token": "missing", "revision": 0, "data": {}})
    restart = Coordinator({})
    try:
        with pytest.raises(UIError):
            restart.owner(owner)
        assert not restart.runs
    finally:
        restart.close()


def test_owner_idle_expiry_and_job_capacity():
    time = [0]
    scheduler = Scheduler()
    c = Coordinator({}, scheduler=scheduler, clock=lambda: time[0])
    try:
        owners = [c.owner(create=True) for _ in range(3)]
        for owner in owners[:2]:
            c.act(owner, "start", "owner", action(c, owner, "start", payload={"scenario": "single"}))
        with pytest.raises(UIError):
            c.act(owners[2], "start", "owner", action(c, owners[2], "start", payload={"scenario": "single"}))
        assert c.jobs == 2
        while scheduler.calls:
            scheduler.finish()
        time[0] = 1801
        with pytest.raises(UIError):
            c.owner(owners[0])
        assert not c.runs
    finally:
        c.close()


def test_stale_cross_owner_run_and_retained_capacity(local):
    c, owner, scheduler = local
    rid, _ = start(c, owner, scheduler)
    old = action(c, owner, "approve", rid)
    c.act(owner, "preview", rid, action(c, owner, "preview", rid, editable(c, owner, rid)))
    with pytest.raises(UIError):
        c.act(owner, "approve", rid, old)
    other = c.owner(create=True)
    with pytest.raises(UIError):
        c.act(other, "approve", rid, old)
    second, _ = start(c, owner, scheduler)
    with pytest.raises(UIError):
        c.act(owner, "approve", second, old)
    with pytest.raises(UIError):
        c.act(owner, "start", "owner", action(c, owner, "start", payload={"scenario": "single"}))
    assert not scheduler.calls


def test_active_abandonment_shutdown_and_cleanup_do_not_reconcile():
    scheduler = Scheduler()
    c = Coordinator({}, scheduler=scheduler)
    owner = c.owner(create=True)
    a = action(c, owner, "start", payload={"scenario": "single"})
    rid = c.act(owner, "start", "owner", a)["run_id"]
    c.act(owner, "abandon", rid, action(c, owner, "abandon", rid))
    assert c.runs[rid].active
    scheduler.finish()
    run = c.runs[rid]
    assert run.phase == "abandoned" and run.state["current_round"] == 0
    before = run.session.graph.get_state(run.session.config)
    fresh = action(c, owner, "start", payload={"scenario": "single"})
    c.close()
    assert run.session.graph.get_state(run.session.config) == before
    with pytest.raises(UIError):
        c.act(owner, "start", "owner", fresh)


@pytest.mark.parametrize("operation", ["approve", "reject", "preview", "abandon", "apply", "cancel"])
def test_all_snapshot_controls_survive_unchanged_other_tab_reads(local, operation):
    c, owner, scheduler = local
    rid, _ = start(c, owner, scheduler)
    run = c.runs[rid]
    before = run.session.graph.get_state(run.session.config)
    original = run.state["research_plan"].objective
    edit = editable(c, owner, rid)
    c.act(owner, "preview", rid, action(c, owner, "preview", rid, edit))
    snapshot = c.run_view(owner, rid)
    assert set(snapshot["actions"]) == {"approve", "reject", "preview", "abandon", "apply", "cancel"}
    # Reading the plan issues its complete coherent control snapshot under the lock.
    for _ in range(3):
        assert c.run_view(owner, rid) == snapshot
        projection = c.plan(owner, rid)
        assert projection["review"] == snapshot
        assert projection["proposal"]["id"] == snapshot["proposal_id"]
        assert all(token in c.tickets for token in snapshot["actions"].values())
    payload = {"proposal_id": snapshot["proposal_id"]} if operation == "apply" else (
        {**edit, "objective": "Replacement B"} if operation == "preview" else {})
    submitted = {"token": snapshot["actions"][operation], "revision": snapshot["revision"], "data": payload}
    receipt = c.act(owner, operation, rid, submitted)
    assert c.act(owner, operation, rid, submitted) == receipt
    if operation in {"approve", "reject", "apply"}:
        assert len(scheduler.calls) == 1
        scheduler.finish()
        assert run.phase == "terminal"
        assert run.state["research_plan"].objective == (edit["objective"] if operation == "apply" else original)
        assert run.state["current_round"] == (0 if operation == "reject" else 1)
    else:
        assert not scheduler.calls
        assert run.session.graph.get_state(run.session.config) == before
        assert run.proposal is None if operation in {"cancel", "abandon"} else run.proposal[1].objective == "Replacement B"


def test_coherent_plan_snapshot_replacement_rejects_stale_apply_without_resume(local):
    c, owner, scheduler = local
    rid, _ = start(c, owner, scheduler)
    c.act(owner, "preview", rid, action(c, owner, "preview", rid, editable(c, owner, rid)))
    a = c.plan(owner, rid)
    c.act(owner, "cancel", rid, action(c, owner, "cancel", rid))
    replacement = {**editable(c, owner, rid), "objective": "Replacement B"}
    c.act(owner, "preview", rid, action(c, owner, "preview", rid, replacement))
    b = c.plan(owner, rid)
    assert b["review"]["revision"] > a["review"]["revision"]
    assert b["proposal"]["id"] != a["proposal"]["id"]
    stale = {"token": a["review"]["actions"]["apply"], "revision": a["review"]["revision"],
             "data": {"proposal_id": a["proposal"]["id"]}}
    with pytest.raises(UIError):
        c.act(owner, "apply", rid, stale)
    assert not scheduler.calls
    c.act(owner, "apply", rid, {"token": b["review"]["actions"]["apply"],
        "revision": b["review"]["revision"], "data": {"proposal_id": b["proposal"]["id"]}})
    assert len(scheduler.calls) == 1
    scheduler.finish()
    assert c.runs[rid].state["research_plan"].objective == "Replacement B"


def test_action_capacity_keeps_consumed_receipts_and_publishes_no_new_work(local):
    c, owner, scheduler = local
    payload = {"candidates": [{"name": "Capacity request", "repository_url": "https://github.com/example/project"}]}
    original = None
    for _ in range(64):
        try:
            submitted = action(c, owner, "prepare", payload=payload)
        except UIError as error:
            assert "capacity" in str(error)
            break
        receipt = c.act(owner, "prepare", "owner", submitted)
        if original is None:
            original = submitted, receipt
    else:
        pytest.fail("Unchanged action cap was not enforced")
    assert len(c.tickets) == 64
    before = c.owners[owner].prepared
    assert c.act(owner, "prepare", "owner", original[0]) == original[1]
    assert c.owners[owner].prepared is before
    assert not c.runs and not scheduler.calls and c.jobs == 0
