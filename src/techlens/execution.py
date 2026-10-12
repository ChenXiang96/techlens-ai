"""Process-local at-most-once dispatch; accounting lives only in checkpoints."""
from copy import deepcopy
from threading import Lock
from contextlib import nullcontext
from techlens.budget import AttemptOutcome, BudgetLedger


class ExecutionBlocked(Exception):
    pass


class AttemptExecutor:
    def __init__(self):
        self._lock = Lock()
        self._slots = {}
        self._active_runs = set()
        self._heads = {}
        self._restored_runs = set()

    def enter_run(self, run_id, checkpoint_id):
        with self._lock:
            if run_id in self._active_runs:
                raise ExecutionBlocked("An invocation is already active for this run")
            if run_id in self._heads and self._heads[run_id] != checkpoint_id:
                raise ExecutionBlocked("Stale or externally modified checkpoint")
            if run_id not in self._heads and checkpoint_id is not None:
                # A fresh runtime cannot establish whether an older snapshot
                # predates dispatches absent from that snapshot. Never mint slots.
                self._restored_runs.add(run_id)
            self._active_runs.add(run_id)

    def leave_run(self, run_id, checkpoint_id):
        with self._lock:
            self._heads[run_id] = checkpoint_id
            self._active_runs.discard(run_id)

    def register(self, admission):
        if not admission.is_new or admission.attempt is None:
            return
        key = (admission.ledger.run_id, admission.attempt.attempt_id)
        with self._lock:
            # Repeated reserve nodes cannot renew dispatch permission.
            if key not in self._slots and admission.ledger.run_id not in self._restored_runs:
                self._slots[key] = ["READY", admission.attempt.request_digest, None]

    def dispatch(self, run_id, attempt, *, committed, send, authorization=None, adapter=None, ledger=None):
        if authorization:
            authorization.check_ledger(ledger)
        if not committed():
            raise ExecutionBlocked("Matching reservation has not been committed")
        key = (run_id, attempt.attempt_id)
        with self._lock:
            slot = self._slots.get(key)
            if slot is None or slot[1] != attempt.request_digest or slot[0] == "AMBIGUOUS":
                return AttemptOutcome(status="failed", category="ambiguous")
            if slot[0] == "OUTCOME_CACHED":
                return deepcopy(slot[2])
            if slot[0] == "IN_FLIGHT":
                raise ExecutionBlocked("Original attempt remains in flight; resume later")
            slot[0] = "IN_FLIGHT"
        try:
            scope = authorization._paid_scope(self, run_id, attempt, adapter) if authorization else nullcontext()
            with scope:
                outcome = send()
            outcome = AttemptOutcome.model_validate(outcome.model_dump())
            with self._lock:
                slot[2] = outcome.model_copy(deep=True)
                slot[0] = "OUTCOME_CACHED"
            return outcome
        finally:
            with self._lock:
                if slot[0] == "IN_FLIGHT":
                    slot[0] = "AMBIGUOUS"


def reservation_committed(saver, run_id, attempt, *, authorization=None):
    """Read actual saver records; an in-memory READY slot is not a commit."""
    for checkpoint in saver.list({"configurable": {"thread_id": run_id}}):
        raw = checkpoint.checkpoint["channel_values"].get("budget_ledger")
        if raw is None:
            continue
        ledger = BudgetLedger.model_validate(raw.model_dump() if isinstance(raw, BudgetLedger) else raw)
        if ledger.run_id == run_id and any(a == attempt for a in ledger.attempts):
            if authorization:
                authorization.check_ledger(ledger)
            return True
    return False
