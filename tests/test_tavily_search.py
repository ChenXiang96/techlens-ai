import json

import httpx
import pytest

from fakes import make_plan, make_request
from techlens.search import (
    SearchAuthenticationError, SearchConfigurationError, SearchQuotaError,
    SearchRateLimitError, SearchResponseError, SearchTransportError,
)
from techlens.tavily_search import TavilySearchProvider

TOKEN = "tavily-unit-test-secret-sentinel"


def search_response():
    return {
        "results": [{"title": "Docs", "url": "https://example.com/Doc?q=a%2fb", "content": "untrusted",
                     "score": 0.7, "published_date": "Tue, 11 Mar 2025 17:00:00 GMT",
                     "raw_content": "ignored raw"}],
        "usage": {"credits": 1}, "answer": "ignored answer", "images": ["ignored image"],
    }


def run_search(handler):
    from authorization_helpers import search_once
    return search_once(handler, TOKEN)


def assert_safe(error, caplog):
    assert TOKEN not in str(error) and TOKEN not in repr(vars(error))
    assert "authorization" not in str(error).casefold()
    assert error.__context__ is None and error.__cause__ is None
    assert TOKEN not in caplog.text
    assert not any(isinstance(value, (httpx.Request, httpx.Response, BaseException)) for value in vars(error).values())


def test_exact_basic_search_contract_and_provenance(caplog):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "POST" and str(request.url) == "https://api.tavily.com/search"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert json.loads(request.content) == {
            "query": "documentation query 1", "search_depth": "basic", "topic": "general",
            "max_results": 3, "include_answer": False, "include_raw_content": False,
            "include_images": False, "auto_parameters": False, "include_usage": True,
        }
        assert all(value == 10.0 for value in request.extensions["timeout"].values())
        return httpx.Response(200, json=search_response())

    result = run_search(handler)
    assert len(calls) == 1
    assert result.provider == "tavily" and result.credits_used == 1
    assert result.task.task_id == "web-001" and result.task.candidate_id == "candidate-001"
    assert result.task.query == "documentation query 1"
    assert result.results[0].url == "https://example.com/Doc?q=a%2fb"
    assert result.results[0].published_date == "Tue, 11 Mar 2025 17:00:00 GMT"
    assert "ignored" not in result.model_dump_json()
    assert TOKEN not in result.model_dump_json() and TOKEN not in caplog.text


def test_zero_hits_and_missing_usage_are_legitimate():
    result = run_search(lambda _: httpx.Response(200, json={"results": []}))
    assert result.results == [] and result.credits_used is None


def test_three_results_allowed():
    payload = search_response()
    payload["results"] *= 3
    assert len(run_search(lambda _: httpx.Response(200, json=payload)).results) == 3


@pytest.mark.parametrize("status,kind", [
    (401, SearchAuthenticationError), (403, SearchAuthenticationError),
    (429, SearchRateLimitError), (432, SearchQuotaError), (433, SearchQuotaError),
    (400, SearchResponseError), (422, SearchResponseError), (500, SearchResponseError),
    (302, SearchResponseError),
])
def test_http_errors_are_sanitized_without_retry_or_redirect(status, kind, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://example.com/leak"},
                              json={"detail": {"error": TOKEN}})

    with pytest.raises(kind) as caught:
        run_search(handler)
    assert len(calls) == 1
    assert_safe(caught.value, caplog)


@pytest.mark.parametrize("kind", ["timeout", "network"])
def test_authenticated_transport_errors_detach_request_and_exception_chain(kind, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if kind == "timeout":
            raise httpx.ReadTimeout(TOKEN, request=request)
        raise httpx.ConnectError(TOKEN, request=request)

    with pytest.raises(SearchTransportError) as caught:
        run_search(handler)
    assert caught.value.kind == kind and len(calls) == 1
    assert_safe(caught.value, caplog)


@pytest.mark.parametrize("payload", [
    [], {}, {"results": None}, {"results": ["invalid"]}, {"results": [{}]},
    {"results": [], "usage": "invalid"}, {"results": [], "usage": {}},
    {"results": [], "usage": {"credits": -1}}, {"results": [], "usage": {"credits": True}},
])
def test_malformed_payload_never_becomes_success(payload, caplog):
    with pytest.raises(SearchResponseError) as caught:
        run_search(lambda _: httpx.Response(200, json=payload))
    assert_safe(caught.value, caplog)


def test_malformed_json_is_sanitized(caplog):
    with pytest.raises(SearchResponseError) as caught:
        run_search(lambda _: httpx.Response(200, text=TOKEN))
    assert_safe(caught.value, caplog)


def test_response_cannot_exceed_three_results():
    payload = search_response()
    payload["results"] *= 4
    with pytest.raises(SearchResponseError):
        run_search(lambda _: httpx.Response(200, json=payload))


@pytest.mark.parametrize("field,value", [("url", "not-url"), ("score", "invalid"),
                                         ("content", {}), ("published_date", 42)])
def test_invalid_result_fields_fail(field, value):
    payload = search_response()
    payload["results"][0][field] = value
    with pytest.raises(SearchResponseError):
        run_search(lambda _: httpx.Response(200, json=payload))


def test_credentials_configuration_and_override(monkeypatch):
    from techlens.authorization import LiveAuthorizationError
    from authorization_helpers import search_once
    with pytest.raises(LiveAuthorizationError):
        TavilySearchProvider()
    monkeypatch.setenv("TAVILY_API_KEY", TOKEN)
    seen = []
    handler = lambda r: (seen.append(r.headers["authorization"]) or httpx.Response(200, json={"results": []}))
    search_once(handler, TOKEN)
    search_once(handler, "explicit-fake-sentinel")
    assert seen == [f"Bearer {TOKEN}", "Bearer explicit-fake-sentinel"]
    with pytest.raises(LiveAuthorizationError):
        TavilySearchProvider(api_key="   ")
