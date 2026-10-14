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
