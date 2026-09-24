# TechLens AI

TechLens collects GitHub engineering signals and web source candidates for open-source technology due diligence. Human review precedes external research. T-004 converts collected observations into typed evidence records and validates their structure and provenance. The final report records collection and integrity status only; it does not evaluate technologies or recommend adoption.

## Setup

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required.

```console
uv sync
uv run pytest
```

All automated tests block real socket connections, clear provider credentials from the test environment, and use fakes or `httpx.MockTransport`. No real API key is needed for tests.

For manual production use, supply `OPENAI_API_KEY` and `TAVILY_API_KEY` through your process environment. `GITHUB_TOKEN` is optional for public repositories. Do not paste credentials into code, checkpoints, review payloads, or command history. `OPENAI_MODEL` overrides the default `gpt-5.6-luna`; there is no local model-price registry.

## Start, review, and resume

The ordering is fixed:

```text
validate_request -> plan_research -> human_review (interrupt)
                                      |
                  approve / modify ---+--- reject
                         |                    |
              collect_github + web_research    |
                         |                    |
                 research_complete            |
                         |                    |
                 normalize_evidence           |
                         |                    |
                  validate_evidence           |
                         +--------------------+
                                  |
                         generate_report_stub -> END
```

GitHub and Tavily execute only after `approve` or a valid `modify`. The OpenAI planner executes before the interrupt because its proposed plan is what the human reviews.

In a single Python session, start an assessment:

```python
import json
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from techlens import (
    AssessmentRequest, OpenAIResearchPlanner, ResearchConstraints,
    TavilySearchProvider, TeamProfile, TechnologyCandidate, build_graph,
)

request = AssessmentRequest(
    assessment_id="langgraph-review-001",
    candidates=[TechnologyCandidate(
        name="LangGraph",
        repository_url="https://github.com/langchain-ai/langgraph",
    )],
    team_profile=TeamProfile(primary_languages=["Python"]),
    constraints=ResearchConstraints(max_candidates=1, max_web_searches=2),
)
planner = OpenAIResearchPlanner()
search = TavilySearchProvider()
saver = InMemorySaver()
graph = build_graph(planner=planner, search_provider=search, checkpointer=saver)
config = {"configurable": {"thread_id": "langgraph-review-001"}}
paused = graph.invoke({"request": request}, config)
review = paused["__interrupt__"][0].value
print(json.dumps(review, indent=2))
```

Inspect the actual plan before executing either of the following resume examples. Keep the same graph, saver, providers, and `thread_id` alive. A different assessment needs a different thread ID.

To approve the reviewed plan:

```python
result = graph.invoke(Command(resume={"action": "approve"}), config)
print(result["report"].model_dump(mode="json"))
assert result["current_round"] == 1
planner.close()
search.close()
```

Alternatively, reject the pending plan with `Command(resume={"action": "reject"})`. The graph completes with a rejected stub report, zero GitHub/Tavily calls, and `current_round == 0`.

To modify, edit a copy of `review["research_plan"]` and submit `{"action": "modify", "modified_plan": edited_plan}` through `Command(resume=...)`. Preserve the four execution limits and canonical comparison targets. Task IDs must be `web-001`, `web-002`, etc., in submitted order; arbitrary IDs fail validation. Invalid decisions raise `PlanReviewError` and do not authorize research. There is no automatic failed-review recovery loop.

The interrupt payload includes a candidate catalog. IDs are assigned from original request order (`candidate-001`, `candidate-002`, `candidate-003`). Duplicate display names are valid. Web tasks and comparison targets reference IDs; `candidate_id=None` denotes cross-candidate research. A human modification may change a task's association to another existing ID but cannot alter the ID-to-repository mapping.

## Collection contracts

The planner uses the OpenAI Responses API with Pydantic Structured Outputs, a 30-second timeout, at most 4,096 output tokens, `max_retries=0`, and `store=False`. It has no tools. Request content is treated as data. Application code assigns task IDs and copies execution limits. It revalidates the plan after planning, review, and before research.

GitHub collection reuses T-002 `collect_engineering_signals(original_candidate)`. Results retain candidate ID, display name, original URL, and typed `EngineeringSignals`. Repository selection always comes from the original request. T-002's maximum remains 15 HTTP requests per candidate (45 for three candidates), with no retries. `BoundedCount(completeness="truncated")` is a lower bound, not an exact count. Missing practices in a truncated tree remain unknown.

Tavily uses direct `httpx` with one POST per approved task and these fixed options:

```json
{
  "search_depth": "basic",
  "topic": "general",
  "max_results": 3,
  "include_answer": false,
  "include_raw_content": false,
  "include_images": false,
  "auto_parameters": false,
  "include_usage": true
}
```

The body also includes the exact task query. Requests have a 10-second timeout, no retries, and no followed redirects. Results preserve task/query/candidate identity, title, URL spelling, snippet, score, optional publication-date text, provider, and optional credits used. A valid empty result list is a zero-hit search; malformed responses raise errors. Missing credits remain unknown, not zero.

Web snippets are untrusted and are stored verbatim without being sent to an LLM. A zero-hit task produces no web evidence; duplicate URLs remain distinct records when retrieved under separate tasks.

## Evidence integrity

After both research branches complete, TechLens emits 24 fixed GitHub metric records per original candidate, then one record per web result in approved task and result order. IDs (`evidence-001`, `evidence-002`, …) are assigned locally in that order. GitHub URLs come from the original request. Web URLs retain provider spelling, and every record keeps its task or candidate provenance.

GitHub bounded observations retain their observed count and `complete` or `truncated` status. Contributor shares describe only the observed contributor population. Repository practice records retain matched paths and tree-completeness information; a missing match in a truncated tree is marked unknown. Null license, release, and timestamp fields remain null.

`EvidenceValidationSummary` certifies structural consistency, source provenance, candidate association, and preservation of collection completeness flags. It does not certify factual truth, source credibility, or sufficient support for a conclusion. The report includes deterministic record counts without copying web snippets. Rejected runs bypass evidence processing and have no evidence fields.

`ClaimRecord` and `validate_claim_evidence_contract` define only the future claim-to-evidence reference rules. T-004 creates no claims and adds no claim state or claim interpretation.

## Bounds, errors, and limitations

- One planner request per normal assessment; zero additional planner requests on normal review resume.
- At most `max_web_searches` Tavily calls and three results per call; defaults allow eight calls and 24 results.
- One research round. Only the successful two-branch join sets `current_round=1`.
- `max_run_cost_usd` is preserved unchanged in the request and plan. T-003 does not estimate, account for, or enforce dollar expenditure; that belongs to the later budget task.
- Providers raise typed, sanitized errors outside active exception handlers. No authenticated exception is attached as their context, cause, or an exception attribute. Application code does not log or serialize request headers or raw responses.
- A failed branch prevents the success join/report. The other branch may already have made requests; no partial-failure recovery or retry is implemented.
- The in-memory saver is process-local. Restarting loses the paused run. Arbitrary replay, resubmission, or restarting an assessment can repeat calls; use only the documented start/review/resume flow.
- Candidate IDs are assessment-local and depend on original request order. Free-text planning quality still requires human review; structured validation does not prove factual accuracy.

Provider clients are caller-owned dependencies and must remain outside graph state/checkpoints. Tests inject `ResearchPlanner`, `SearchProvider`, and a GitHub collector callable directly into `build_graph`.

No evidence interpretation, claim generation, risk scoring, adoption recommendation, second round, database, frontend, or persistent checkpointer is implemented.
