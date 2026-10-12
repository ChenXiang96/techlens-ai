"""Offline helpers: real issuance, production graph/review/admission, mocked I/O only."""
import os
from decimal import Decimal
from langgraph.types import Command
from techlens.authorization import authorize_live_run
from techlens.presentation import PublicOutput
from techlens.graph import build_graph
from techlens.budget import AttemptOutcome
from fakes import make_request, fictional_rates, FakePlanner, FakeAnalyzer, FakeSearchProvider, FakeGitHubCollector


def grant(request=None, rates=None, **env):
    request, rates = request or make_request(), rates or fictional_rates()
    values = {"OPENAI_API_KEY": "OPENAI-FAKE-SENTINEL", "TAVILY_API_KEY": "TAVILY-FAKE-SENTINEL",
              "GITHUB_TOKEN": "", "OPENAI_MODEL": rates.model, **env}
    return authorize_live_run(request, rates, values,
        read=lambda prompt: prompt.removeprefix("Type ").removesuffix(" to continue: "),
        write=lambda text: None, public=PublicOutput(values), interactive=True)


def planner(provider_type, *, request=None, api_key=None, model=None, http_client=None):
    request = request or make_request()
    model = model or os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    # A failed attempt retains > half the ceiling, so legacy single-operation
    # transport tests cannot add an application retry. SDK max_retries remains 0.
    rate = request.constraints.max_run_cost_usd * Decimal("600000") / provider_type.max_output_tokens
    auth = grant(request, fictional_rates(model=model, output_usd_per_million=rate),
                 OPENAI_API_KEY=api_key or os.getenv("OPENAI_API_KEY", "OPENAI-FAKE-SENTINEL"))
    return provider_type(api_key=api_key, model=model, http_client=http_client, authorization=auth)


def run_llm(provider, shared, request=None, *, kind="generate"):
    request = request or make_request()
    auth = provider.authorization
    outcomes = {}
    original_count, original_generate = provider.count, provider.generate
    def count(value):
        result = (AttemptOutcome(status="succeeded", data={"input_tokens": 100})
                  if kind == "generate" else original_count(value))
        outcomes["count"] = result
        return result
    def generate(value):
        result = (AttemptOutcome(status="failed", category="refusal")
                  if kind == "count" else original_generate(value))
        outcomes["generate"] = result
        return result
    provider.count, provider.generate = count, generate
    provider.prepare = lambda value: shared
    graph = build_graph(planner=provider if provider.stage == "planner" else FakePlanner(),
        analyzer=provider if provider.stage == "analyzer" else None,
        search_provider=FakeSearchProvider(), github_collector=FakeGitHubCollector(),
        rates=auth._rates, authorization=auth, sleep=lambda _: None)
    config = {"configurable": {"thread_id": auth.run_id}}
    state = graph.invoke({"request": request}, config)
    if provider.stage == "analyzer":
        graph.invoke(Command(resume={"action": "approve"}), config)
    auth.close()
    return outcomes


def research_call(request, auth, *, search=None, collector=None):
    graph = build_graph(planner=FakePlanner(task_count=1 if search else 0), analyzer=None,
        search_provider=search or FakeSearchProvider(), github_collector=collector or FakeGitHubCollector(),
        rates=auth._rates, authorization=auth, sleep=lambda _: None)
    config = {"configurable": {"thread_id": auth.run_id}}
    graph.invoke({"request": request}, config)
    return graph.invoke(Command(resume={"action": "approve"}), config)


def collect_engineering_signals(candidate, *, token=None, http_client=None, collected_at=None):
    from techlens.github_client import collect_engineering_signals as collect
    request = make_request()
    request.candidates = [candidate]
    auth = grant(request, GITHUB_TOKEN=token if token is not None else os.getenv("GITHUB_TOKEN", ""))
    results, errors = [], []
    def collector(value):
        try:
            result = collect(value, token=token, http_client=http_client, collected_at=collected_at, authorization=auth)
            results.append(result)
            return result
        except Exception as error:
            errors.append(error)
            raise
    research_call(request, auth, collector=collector)
    if errors:
        raise errors[0]
    return results[0]


def search_once(handler, token):
    import httpx
    from techlens.tavily_search import TavilySearchProvider
    request = make_request()
    request.constraints = request.constraints.model_copy(update={"max_web_searches": 1})
    auth = grant(request, TAVILY_API_KEY=token)
    errors, results = [], []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = TavilySearchProvider(http_client=client, authorization=auth)
        search = provider.search
        def capture(task):
            try:
                result = search(task)
                results.append(result)
                return result
            except Exception as error:
                errors.append(error)
                raise
        provider.search = capture
        research_call(request, auth, search=provider)
    if errors:
        raise errors[0]
    return results[0]
