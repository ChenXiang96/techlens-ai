import json
import httpx
import pytest
from fakes import make_request
from test_analysis import scenario
from test_openai_planner import response_for, draft_for, TOKEN
from techlens.openai_planner import OpenAIResearchPlanner
from techlens.openai_analyzer import OpenAIAdoptionAnalyzer


@pytest.mark.parametrize("kind", ["planner", "analyzer"])
def test_actual_count_and_generation_json_share_every_token_relevant_field(kind):
    bodies = []
    request, _, _, envelope, draft = scenario()
    def handler(http_request):
        bodies.append((http_request.url.path, json.loads(http_request.content)))
        if http_request.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        return httpx.Response(200, json=response_for(draft if kind == "analyzer" else draft_for(request)))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = (OpenAIAdoptionAnalyzer if kind == "analyzer" else OpenAIResearchPlanner)(api_key=TOKEN, http_client=client)
        shared = provider.prepare(envelope if kind == "analyzer" else request)
        shared["reasoning"] = {"effort": "low"}
        assert provider.count(shared).status == "succeeded"
        assert provider.generate(shared).status == "succeeded"
    assert [path for path, _ in bodies] == ["/v1/responses/input_tokens", "/v1/responses"]
    counted, generated = bodies[0][1], bodies[1][1]
    assert set(generated) - set(counted) == {"max_output_tokens", "store"}
    assert {k: v for k, v in generated.items() if k not in {"max_output_tokens", "store"}} == counted == shared
    assert generated["store"] is False and generated["max_output_tokens"] == provider.max_output_tokens
    assert counted["text"]["format"]["strict"] is True
    assert counted["truncation"] == "disabled"
    assert all(key not in counted for key in ("tools", "tool_choice", "conversation", "previous_response_id"))
    schema = counted["text"]["format"]["schema"]
    for definition in [schema, *schema.get("$defs", {}).values()]:
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])
    if kind == "analyzer":
        assert "UNTRUSTED-SNIPPET" in counted["input"][0]["content"]
        assert "UNTRUSTED-SNIPPET" not in counted["instructions"]


@pytest.mark.parametrize("stage", ["count", "generate"])
@pytest.mark.parametrize("category", ["timeout", "network", "authentication", "rate_limit", "quota", "server"])
def test_analyzer_boundaries_return_only_sanitized_fields(stage, category, caplog):
    from test_analysis import scenario
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if category == "timeout":
            raise httpx.ReadTimeout(TOKEN, request=request)
        if category == "network":
            raise httpx.ConnectError(TOKEN, request=request)
        status = {"authentication": 401, "rate_limit": 429, "quota": 429, "server": 503}[category]
        code = "insufficient_quota" if category == "quota" else "rate_limit_exceeded" if category == "rate_limit" else "error"
        return httpx.Response(status, json={"error": {"message": TOKEN, "code": code}}, headers={"retry-after": "999"})
    _, _, _, envelope, _ = scenario()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAIAdoptionAnalyzer(api_key=TOKEN, http_client=client)
        outcome = getattr(provider, stage)(provider.prepare(envelope))
    assert len(calls) == 1 and outcome.category == category
    assert outcome.retry_after is None or outcome.retry_after <= 5
    serialized = outcome.model_dump_json() + repr(vars(outcome)) + caplog.text
    assert TOKEN not in serialized and "authorization" not in serialized.casefold()
