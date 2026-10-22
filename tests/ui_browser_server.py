"""Standalone QA child: unconditional connect guard before TechLens imports."""
import json
import hashlib
import os
import socket
import sys

attempts = []
def denied(*args, **kwargs):
    attempts.append("denied")
    raise RuntimeError("Browser QA child forbids outbound network")
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import_only_attempts = []
if os.environ.get("TECHLENS_IMPORT_ONLY") == "1":
    # Install the execution sentinel BEFORE any TechLens import. A profile hook
    # can reject construction even if an import unexpectedly tried to create a
    # session/ledger before the explicit class patches below could be installed.
    def preimport_execution_guard(frame, event, arg):
        if event != "call":
            return
        module, name = frame.f_globals.get("__name__", ""), frame.f_code.co_name
        instance = frame.f_locals.get("self")
        cls = type(instance)
        blocked = (module == "techlens.graph" and name == "build_graph" or
            module == "techlens.authorization" and name == "authorize_live_run" or
            name in {"__init__", "start", "resume"} and cls.__module__ == "techlens.application"
                and cls.__name__ == "AssessmentSession" or
            name == "__init__" and cls.__module__ == "techlens.budget" and cls.__name__ == "BudgetLedger")
        if blocked:
            import_only_attempts.append("preimport-execution")
            raise RuntimeError("Import-only preimport guard forbids assessment execution")
    sys.setprofile(preimport_execution_guard)

import httpx
import openai
def forbidden(*args, **kwargs):
    attempts.append("provider")
    raise RuntimeError("Browser QA forbids live client construction or transport")
httpx.Client.__init__ = forbidden
httpx.Client.send = forbidden
openai.OpenAI.__init__ = forbidden
import techlens.authorization as authorization
authorization.authorize_live_run = forbidden

from techlens.application import private_execution
from techlens.application import AssessmentSession
from techlens.ui_coordinator import Coordinator
from techlens.ui_server import LocalServer

if os.environ.get("TECHLENS_IMPORT_ONLY") == "1":
    # Installed product imports are complete, but no session/client/ledger has
    # been constructed. All import journeys below must remain pure file work.
    def execution_denied(*args, **kwargs):
        import_only_attempts.append("execution")
        raise RuntimeError("Import-only child forbids assessment execution")
    import techlens.budget, techlens.graph
    AssessmentSession.__init__ = execution_denied
    techlens.graph.build_graph = execution_denied
    techlens.budget.BudgetLedger.__init__ = execution_denied

constructed_sessions = []
class ObservedSession(AssessmentSession):
    """Observe actual retained graph entry; no QA HTTP route or execution bypass."""
    starts = 0
    resumes = 0

    def __init__(self, request, dependencies, **kwargs):
        constructed_sessions.append(self)
        self.observed_dependencies = dependencies
        super().__init__(request, dependencies, **kwargs)

    def start(self):
        self.starts += 1
        result = super().start()
        deps = self.observed_dependencies
        self.before_approval = {"github": len(deps.github.calls), "search": deps.search.calls,
                                "analyzer_generation": deps.analyzer.generation_calls}
        self.initial_plan_sha256 = hashlib.sha256(coordinator.public.json(
            result["research_plan"].model_dump(mode="json")).encode("utf-8")).hexdigest()
        return result

    def resume(self, decision):
        self.resumes += 1
        return super().resume(decision)


coordinator = Coordinator({"OPENAI_API_KEY": "BROWSER-FAKE-SECRET-SENTINEL"}, session_factory=ObservedSession)
server = LocalServer(0, coordinator)
print(server.origin, flush=True)

# stdin is QA control only, never a product/TTY bridge or HTTP action.
from threading import Thread
def control():
    sys.stdin.readline()
    server.shutdown()
Thread(target=control, daemon=True).start()
with private_execution():
    server.serve_forever(poll_interval=0.01)
server.server_close()
coordinator.close()
from techlens.exporting import export_bytes
def observed(rid, run):
    deps = run.session.observed_dependencies
    result = {"run_id": rid, "starts": run.session.starts, "resumes": run.session.resumes,
        "objective": run.state["research_plan"].objective, "phase": run.phase,
        "current_round": run.state["current_round"], "github": len(deps.github.calls),
        "search": deps.search.calls, "planner_generation": deps.planner.generation_calls,
        "analyzer_generation": deps.analyzer.generation_calls, "before_approval": run.session.before_approval,
        "initial_plan_sha256": run.session.initial_plan_sha256}
    if run.state.get("report"):
        result["authoritative_report"] = run.state["report"].model_dump(mode="json")
        result["canonical_hashes"] = {name: hashlib.sha256(data).hexdigest()
            for name, data in export_bytes(run.state, coordinator.public,
                mode="demo", scenario=run.scenario).items()}
    return result
encoded = json.dumps({"network_attempts": len(attempts), "runs": len(coordinator.runs),
    "import_only_execution_attempts": len(import_only_attempts),
    "sessions_constructed": len(constructed_sessions),
    "terminal": sum(r.phase == "terminal" for r in coordinator.runs.values()),
    "module": __import__("techlens.ui_server", fromlist=["*"]).__file__,
    "python": sys.executable, "prefix": sys.prefix,
    "sessions": [observed(rid, r)
                 for rid, r in coordinator.runs.items() if r.session and r.state],
    "forbidden_imports": [n for n in sys.modules if n == "evals" or n.startswith("techlens.evaluation")]})
assert len(encoded.encode()) <= 512 * 1024
assert "BROWSER-FAKE-SECRET-SENTINEL" not in encoded
print(encoded, flush=True)
