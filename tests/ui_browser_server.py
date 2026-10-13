"""Standalone QA child: unconditional connect guard before TechLens imports."""
import json
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

from techlens.application import private_execution
from techlens.application import AssessmentSession
from techlens.ui_coordinator import Coordinator
from techlens.ui_server import LocalServer

class ObservedSession(AssessmentSession):
    """Observe actual retained graph entry; no QA HTTP route or execution bypass."""
    starts = 0
    resumes = 0

    def start(self):
        self.starts += 1
        return super().start()

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
print(json.dumps({"network_attempts": len(attempts), "runs": len(coordinator.runs),
    "terminal": sum(r.phase == "terminal" for r in coordinator.runs.values()),
    "module": __import__("techlens.ui_server", fromlist=["*"]).__file__,
    "python": sys.executable, "prefix": sys.prefix,
    "sessions": [{"run_id": rid, "starts": r.session.starts, "resumes": r.session.resumes,
                  "objective": r.state["research_plan"].objective, "phase": r.phase}
                 for rid, r in coordinator.runs.items() if r.session and r.state],
    "forbidden_imports": [n for n in sys.modules if n == "evals" or n.startswith("techlens.evaluation")]}), flush=True)
