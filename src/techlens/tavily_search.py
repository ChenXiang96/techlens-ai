"""Bounded Tavily Basic Search using direct httpx requests."""

import os

import httpx
from pydantic import ValidationError

from techlens.models import WebResearchTask
from techlens.authorization import require_authorization
from techlens.research_models import WebResearchTaskResult, WebSearchResult
from techlens.search import (
    SearchAuthenticationError,
    SearchConfigurationError,
    SearchError,
    SearchQuotaError,
    SearchRateLimitError,
    SearchResponseError,
    SearchTransportError,
    SearchServerError,
)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_TIMEOUT_SECONDS = 10.0


class TavilySearchProvider:
    def __init__(self, *, api_key: str | None = None, http_client: httpx.Client | None = None, authorization=None):
        self.authorization = require_authorization(authorization)
        key = authorization.credential("TAVILY_API_KEY", api_key)
        if not key or not key.strip():
            raise SearchConfigurationError("TAVILY_API_KEY is required")
        self._headers = {"Authorization": f"Bearer {key.strip()}"}
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else authorization.own(httpx.Client(trust_env=False, follow_redirects=False))
        authorization.register_provider(self, "web")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "TavilySearchProvider":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def search(self, task: WebResearchTask) -> WebResearchTaskResult:
        task = WebResearchTask.model_validate(task.model_dump())
        self.authorization.consume(self, f"web:{task.task_id}", task.model_dump(mode="json"), TAVILY_SEARCH_URL)
        failure: SearchError | None = None
        result = None
        try:
            response = self._client.post(
                TAVILY_SEARCH_URL,
                headers=self._headers,
                json={
                    "query": task.query,
                    "search_depth": "basic",
                    "topic": "general",
                    "max_results": 3,
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                    "auto_parameters": False,
                    "include_usage": True,
                },
                timeout=TAVILY_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
            status = response.status_code
            if status in {401, 403}:
                failure = SearchAuthenticationError("Tavily authentication or permission failure")
            elif status == 429:
                failure = SearchRateLimitError("Tavily rate limit exceeded")
            elif status in {432, 433}:
                failure = SearchQuotaError("Tavily quota or plan limit exceeded")
            elif status >= 500:
                failure = SearchServerError("Tavily server failure")
            elif status != 200:
                failure = SearchResponseError("Unexpected Tavily HTTP status")
            else:
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                    raise ValueError("Invalid results")
                results = []
                for item in payload["results"]:
                    if not isinstance(item, dict):
                        raise ValueError("Invalid result")
                    results.append(WebSearchResult(
                        title=item["title"],
                        url=item["url"],
                        content=item["content"],
                        score=item["score"],
                        published_date=item.get("published_date"),
                    ))
                usage = payload.get("usage")
                if usage is not None and not isinstance(usage, dict):
                    raise ValueError("Invalid usage")
                result = WebResearchTaskResult(
                    task=task,
                    provider="tavily",
                    results=results,
                    credits_used=usage["credits"] if usage is not None else None,
                )
        except httpx.TimeoutException:
            failure = SearchTransportError("timeout")
        except httpx.TransportError:
            failure = SearchTransportError("network")
        except (ValidationError, ValueError, TypeError, KeyError):
            failure = SearchResponseError("Malformed Tavily search response")

        # Do not retain the caught httpx/Pydantic exception or authenticated request.
        if failure is not None:
            raise failure
        if result is None:
            raise SearchResponseError("Missing Tavily search response")
        return result
