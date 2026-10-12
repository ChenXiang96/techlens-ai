"""Process-local live consent and single-use transport scopes, never checkpoint data.

This is a trusted-application boundary, not a sandbox for arbitrary Python.
"""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from threading import RLock
from uuid import uuid4

from techlens.budget import digest
from techlens.planning import candidate_catalog, validate_plan


class LiveAuthorizationError(Exception):
    """Fixed, non-provider denial: must never become a retryable provider error."""


_ISSUER = object()
_scope = ContextVar("techlens_transport_scope", default=None)
OPENAI_BASE = "https://api.openai.com/v1/"
COUNT_URL = OPENAI_BASE + "responses/input_tokens"
GENERATION_URL = OPENAI_BASE + "responses"
SEARCH_URL = "https://api.tavily.com/search"
GITHUB_BASE = "https://api.github.com"


def _policy():
    from techlens import openai_planner, openai_analyzer, tavily_search, github_client
    return {"endpoints": [OPENAI_BASE, COUNT_URL, GENERATION_URL, SEARCH_URL, GITHUB_BASE,
                          tavily_search.TAVILY_SEARCH_URL, github_client.GITHUB_API_BASE_URL],
        "instructions": [openai_planner.PLANNER_INSTRUCTIONS, openai_analyzer.ANALYZER_INSTRUCTIONS],
        "schemas": [openai_planner._PlannerDraft.model_json_schema(), openai_analyzer.AnalysisDraft.model_json_schema()],
        "bounds": [openai_planner.OpenAIResearchPlanner.max_output_tokens,
                   openai_analyzer.OpenAIAdoptionAnalyzer.max_output_tokens,
                   github_client.GITHUB_PER_PAGE, github_client.GITHUB_MAX_PAGES],
        "contract": "responses-shared-v1/live-v1"}


def require_authorization(value):
    if not isinstance(value, LiveRunAuthorization):
        raise LiveAuthorizationError("Live authorization is required")
    value.check()
    return value


def authorize_live_run(request, rates, env, *, read, write, public, interactive):
    """The supported issuer. No boolean or serialized input substitutes for consent."""
    from techlens.configuration import live_configuration
    if not interactive:
        raise LiveAuthorizationError("Live authorization was not granted. No provider work started.")
    captured = dict(env)
    model = live_configuration(request, rates, captured)
    request = type(request).model_validate(request.model_dump(mode="json"))
    rates = type(rates).model_validate(rates.model_dump(mode="json"))
    run_id = str(uuid4())
    summary = {"run_id": run_id, "request": request.model_dump(mode="json"),
        "candidates": [c.model_dump(mode="json") for c in candidate_catalog(request)],
        "model": model, "rates": rates.model_dump(mode="json"),
        "endpoints": [COUNT_URL, GENERATION_URL, SEARCH_URL, GITHUB_BASE],
        "bounds": {"planner_output_tokens": 4096, "analyzer_output_tokens": 8192,
            "count_attempts_per_stage": 1, "generation_attempts_per_stage": 2,
            "search_attempts_total": request.constraints.max_web_searches,
            "search_attempts_per_task": 2, "results_per_search": 3,
            "github_pages_per_list": 2, "github_per_page": 100, "research_rounds": 1}}
    write("LIVE PROVIDER AUTHORIZATION — NOT A SYNTHETIC DEMO\n" + public.json(summary) + "\n"
          "OpenAI token counting and planning may incur charges before research-plan approval.\n"
          "GitHub collection and Tavily Basic searches require later research-plan approval.\n"
          "Rejecting that plan does not undo incurred or unresolved charges.\n"
          "Prices are caller-trusted references, not independently verified prices.\n"
          "Permission covers only this run and does not increase its USD allowance.\n"
          "Reservations and estimates are application accounting, not a provider invoice guarantee.\n")
    accepted = False
    try:
        accepted = read(f"Type AUTHORIZE LIVE {run_id} to continue: ") == f"AUTHORIZE LIVE {run_id}"
    except EOFError:
        pass
    if not accepted:
        raise LiveAuthorizationError("Live authorization was not granted. No provider work started.")
    return LiveRunAuthorization(_ISSUER, request, rates, model, captured, summary)


class LiveRunAuthorization:
    def __init__(self, issuer, request, rates, model, env, summary):
        if issuer is not _ISSUER:
            raise LiveAuthorizationError("Live authorization must be issued interactively")
        self._lock = RLock()
        self._active = True
        self._request = request.model_copy(deep=True)
        self._rates = rates.model_copy(deep=True)
        self._request_digest = digest(request.model_dump(mode="json"))
        self._rates_digest = digest(rates.model_dump(mode="json"))
        self.run_id, self.model = summary["run_id"], model
        self._identity = (self.run_id, model)
        self._summary_digest = digest(summary)
        self._policy_digest = digest(_policy())
        self._env = {k: env.get(k, "") for k in ("OPENAI_API_KEY", "TAVILY_API_KEY", "GITHUB_TOKEN")}
        self._resources = ExitStack()
        self._executor = self._saver = None
        self._providers = {}
        self._approved = None
        self._collections = set()

    def __repr__(self):
        return "<LiveRunAuthorization>"

    def __reduce_ex__(self, protocol):
        raise LiveAuthorizationError("Live authorization cannot be serialized")

    def check(self, *, request=None, rates=None, run_id=None):
        with self._lock:
            if (not self._active or (self.run_id, self.model) != self._identity
                or digest(_policy()) != self._policy_digest
                or (run_id is not None and run_id != self.run_id)
                or (request is not None and digest(request.model_dump(mode="json")) != self._request_digest)
                or (rates is not None and digest(rates.model_dump(mode="json")) != self._rates_digest)):
                raise LiveAuthorizationError("Live authorization binding is invalid or inactive")

    def credential(self, name, supplied=None):
        self.check()
        value = self._env[name]
        if supplied is not None and supplied != value:
            raise LiveAuthorizationError("Provider configuration differs from authorization")
        return value

    def own(self, resource):
        self.check()
        self._resources.callback(resource.close)
        return resource

    def close(self):
        with self._lock:
            self._active = False
        self._resources.close()

    def register_provider(self, provider, role, model=None):
        self.check()
        if model is not None and model != self.model:
            raise LiveAuthorizationError("Provider model differs from authorization")
        with self._lock:
            self._providers[id(provider)] = (provider, role)

    def bind(self, executor, saver, rates):
        self.check(rates=rates)
        with self._lock:
            if self._executor is not None:
                raise LiveAuthorizationError("Live authorization already belongs to a runner")
            self._executor, self._saver = executor, saver

    def _attest_review(self, state):
        """Called only on the graph edge after human_review successfully returns."""
        self.check(request=state["request"])
        if state["plan_status"] not in {"approved", "modified"}:
            raise LiveAuthorizationError("Research has not been approved")
        plan = validate_plan(state["research_plan"], self._request)
        self._approved = digest(plan.model_dump(mode="json"))

    def check_research(self, state):
        self.check(request=state["request"])
        if (self._approved is None or state.get("plan_status") not in {"approved", "modified"}
            or digest(state["research_plan"].model_dump(mode="json")) != self._approved):
            raise LiveAuthorizationError("Research approval does not match this runtime")

    def check_ledger(self, ledger):
        """Accounting may evolve; the originally authorized allowances may not."""
        if ledger is None or ledger.rates is None or ledger.run_id != self.run_id:
            raise LiveAuthorizationError("Live dispatch requires the authorized ledger")
        self.check(rates=ledger.rates, run_id=ledger.run_id)
        limits = self._request.constraints
        if (ledger.ceiling_usd != limits.max_run_cost_usd
                or ledger.max_search_attempts != limits.max_web_searches):
            raise LiveAuthorizationError("Ledger allowances differ from live authorization")

    def before_admission(self, state, adapter):
        self.check_ledger(state["budget_ledger"])
        self.check(request=state["request"], rates=state["budget_ledger"].rates,
                   run_id=state["budget_ledger"].run_id)
        if getattr(adapter, "authorization", None) is not self:
            raise LiveAuthorizationError("Provider does not belong to this live run")
        self._check_provider(adapter)

    def _check_provider(self, adapter):
        record = self._providers.get(id(adapter))
        if record is None:
            raise LiveAuthorizationError("Unregistered live provider")
        role = record[1]
        if role in {"planner", "analyzer"} and (
            adapter.model != self.model or adapter.max_output_tokens != (4096 if role == "planner" else 8192)):
            raise LiveAuthorizationError("Provider bounds differ from authorization")

    @contextmanager
    def _paid_scope(self, executor, run_id, attempt, adapter):
        from techlens.execution import reservation_committed
        self.check(run_id=run_id)
        if (executor is not self._executor or run_id not in executor._active_runs
            or executor._slots.get((run_id, attempt.attempt_id), [None])[0] != "IN_FLIGHT"
              or not reservation_committed(self._saver, run_id, attempt, authorization=self)
            or id(adapter) not in self._providers):
            raise LiveAuthorizationError("Live dispatch requires the owning checkpointed executor")
        if not attempt.operation_id.startswith("planner:") and self._approved is None:
            raise LiveAuthorizationError("Research has not been approved")
        scope = {"owner": self, "adapter": adapter, "operation": attempt.operation_id,
                 "digest": attempt.request_digest, "used": False, "active": True, "kind": "paid"}
        token = _scope.set(scope)
        try:
            yield
        finally:
            with self._lock:
                scope["active"] = False
            _scope.reset(token)

    def consume(self, adapter, operation, payload, endpoint):
        """Atomic one-use claim immediately before the SDK/HTTP call."""
        with self._lock:
            self.check()
            self._check_provider(adapter)
            scope = _scope.get()
            expected_url = COUNT_URL if operation.endswith(":count") else (
                SEARCH_URL if operation.startswith("web:") else GENERATION_URL)
            registration = self._providers.get(id(adapter))
            role = "web" if operation.startswith("web:") else operation.split(":")[0]
            if (scope is None or scope["owner"] is not self or scope["kind"] != "paid"
                or not scope["active"] or scope["used"] or scope["adapter"] is not adapter
                or scope["operation"] != operation or scope["digest"] != digest(payload)
                or endpoint != expected_url or registration != (adapter, role)):
                raise LiveAuthorizationError("No matching unused paid dispatch permission")
            scope["used"] = True

    @contextmanager
    def github_scope(self, state, index):
        self.check_research(state)
        candidate = self._request.candidates[index]
        with self._lock:
            if index in self._collections:
                raise LiveAuthorizationError("GitHub collection cannot be replayed")
            self._collections.add(index)
        scope = {"owner": self, "kind": "github", "active": True,
                 "candidate": digest(candidate.model_dump(mode="json")), "requests": set()}
        token = _scope.set(scope)
        try:
            yield
        finally:
            with self._lock:
                scope["active"] = False
            _scope.reset(token)

    def github_request(self, candidate, endpoint, path, params):
        from techlens.github_models import GitHubRepositoryRef
        from urllib.parse import quote
        with self._lock:
            self.check()
            scope = _scope.get()
            ref = GitHubRepositoryRef.from_candidate(candidate)
            prefix = f"/repos/{quote(ref.owner, safe='')}/{quote(ref.repository, safe='')}"
            allowed = {"repository": "", "latest_release": "/releases/latest", "releases": "/releases",
                "commits": "/commits", "issues": "/issues", "open_pull_requests": "/pulls",
                "closed_pull_requests": "/pulls", "contributors": "/contributors"}
            valid_path = path == prefix + allowed[endpoint] if endpoint in allowed else (
                endpoint == "repository_tree" and path.startswith(prefix + "/git/trees/")
                and "/" not in path[len(prefix + "/git/trees/"):])
            page = (params or {}).get("page", 1)
            key = (endpoint, page)
            if (scope is None or scope["owner"] is not self or scope["kind"] != "github"
                or not scope["active"] or scope["candidate"] != digest(candidate.model_dump(mode="json"))
                or not valid_path or page not in {1, 2}
                or ("page" in (params or {}) and params.get("per_page") != 100)
                or key in scope["requests"]):
                raise LiveAuthorizationError("No matching bounded GitHub dispatch permission")
            scope["requests"].add(key)
