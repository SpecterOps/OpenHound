import re
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urljoin

import httpx2
from dlt.common import json as dlt_json
from dlt.common import jsonpath

from .typing import Request


def get_response_json(response: httpx2.Response) -> Any:
    """Share one JSON decode between data selection and paginator state updates."""
    if "openhound_json" not in response.extensions:
        response.extensions["openhound_json"] = dlt_json.loadb(response.content)
    return response.extensions["openhound_json"]


class DataSelector:
    """Compile JSONPath once, using direct access for simple object keys."""

    def __init__(self, path: jsonpath.TJsonPath | None = None) -> None:
        self._key = (
            path if isinstance(path, str) and re.fullmatch(r"\w+", path) else None
        )
        self._path = (
            jsonpath.compile_path(path)
            if path is not None and self._key is None
            else None
        )

    def select(self, data: Any) -> Any:
        """Select the response body, an object field, or JSONPath matches."""
        if self._key is not None:
            return data.get(self._key) if isinstance(data, dict) else None
        if self._path is None:
            return data
        matches = [match.value for match in self._path.find(data)]
        return matches[0] if len(matches) == 1 else matches


class BasePaginator(ABC):
    """DLT-style lifecycle for explicit pagination.

    The client copies each paginator per pagination call to isolate mutable state.
    JSON-based implementations can use get_response_json() to reuse the body
    already decoded for data selection.
    """

    def __init__(self) -> None:
        self._has_next_page = True

    @property
    def has_next_page(self) -> bool:
        return self._has_next_page

    def init_request(self, request: Request) -> None:
        """Optionally set parameters for the first request."""

    @abstractmethod
    def update_state(
        self, response: httpx2.Response, data: list[Any] | None = None
    ) -> None:
        """Read the response and determine whether another page is available."""

    @abstractmethod
    def update_request(self, request: Request) -> None:
        """Modify the request arguments for the next page."""


class SinglePagePaginator(BasePaginator):
    """Read one response without attempting to infer a pagination strategy."""

    def update_state(
        self, response: httpx2.Response, data: list[Any] | None = None
    ) -> None:
        self._has_next_page = False

    def update_request(self, request: Request) -> None:
        pass


class BaseReferencePaginator(BasePaginator):
    """Base for pagination using a continuation URL or cursor."""

    def __init__(self) -> None:
        super().__init__()
        self.__next_reference: str | None = None

    @property
    def _next_reference(self) -> str | None:
        return self.__next_reference

    @_next_reference.setter
    def _next_reference(self, value: str | None) -> None:
        self.__next_reference = value
        self._has_next_page = value is not None


class BaseNextUrlPaginator(BaseReferencePaginator):
    """Update the request URL from a reference returned by the API."""

    def update_request(self, request: Request) -> None:
        if self._next_reference is not None:
            request.url = urljoin(request.url, self._next_reference)
            # The continuation URL contains its own query parameters.
            request.params = None


class HeaderLinkPaginator(BaseNextUrlPaginator):
    """Follow the configured relation in the response's Link header."""

    def __init__(self, links_next_key: str = "next") -> None:
        super().__init__()
        self.links_next_key = links_next_key

    def update_state(
        self, response: httpx2.Response, data: list[Any] | None = None
    ) -> None:
        self._next_reference = response.links.get(self.links_next_key, {}).get("url")


class JSONLinkPaginator(BaseNextUrlPaginator):
    """Follow a continuation URL selected from the decoded response body."""

    def __init__(self, next_url_path: jsonpath.TJsonPath = "next") -> None:
        super().__init__()
        self._selector = DataSelector(next_url_path)

    def update_state(
        self, response: httpx2.Response, data: list[Any] | None = None
    ) -> None:
        value = self._selector.select(get_response_json(response))
        if value is None or value == "" or value == []:
            self._next_reference = None
        elif not isinstance(value, str):
            raise TypeError("Pagination reference must be a URL string")
        else:
            self._next_reference = value
