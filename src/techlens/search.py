"""Small search-provider contract and sanitized failure types."""

from typing import Protocol

from techlens.models import WebResearchTask
from techlens.research_models import WebResearchTaskResult


class SearchError(Exception):
    """Search failed; not equivalent to a successful zero-hit response."""


class SearchConfigurationError(SearchError):
    pass


class SearchAuthenticationError(SearchError):
    pass


class SearchRateLimitError(SearchError):
    pass


class SearchQuotaError(SearchError):
    pass


class SearchTransportError(SearchError):
    def __init__(self, kind: str):
        super().__init__(f"Tavily {kind} failure")
        self.kind = kind


class SearchResponseError(SearchError):
    pass


class SearchServerError(SearchResponseError):
    pass


class SearchProvider(Protocol):
    def search(self, task: WebResearchTask) -> WebResearchTaskResult: ...
