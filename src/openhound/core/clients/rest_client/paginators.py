import re
from abc import ABC, abstractmethod
from typing import Any, cast
from urllib.parse import urljoin, urlparse

import httpx2
from dlt.common import json as dlt_json
from dlt.common import jsonpath
from dlt.common.utils import str2bool

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

    The client copies its default paginator, but uses per-call instances directly.
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

    def __str__(self) -> str:
        return f"{type(self).__name__} at {id(self):x}"


class SinglePagePaginator(BasePaginator):
    """Read one response without attempting to infer a pagination strategy."""

    def update_state(
        self, response: httpx2.Response, data: list[Any] | None = None
    ) -> None:
        self._has_next_page = False

    def update_request(self, request: Request) -> None:
        pass


class RangePaginator(BasePaginator):
    """Base for numeric query- or JSON-body-based pagination, as in DLT.

    The maximum is exclusive. A configured has_more flag takes precedence over
    numeric bounds, matching DLT; an empty page stops first when enabled.
    """

    def __init__(
        self,
        param_name: str | None,
        initial_value: int,
        value_step: int,
        base_index: int = 0,
        maximum_value: int | None = None,
        total_path: jsonpath.TJsonPath | None = None,
        error_message_items: str = "items",
        stop_after_empty_page: bool = True,
        *,
        has_more_path: jsonpath.TJsonPath | None = None,
        param_body_path: str | None = None,
    ) -> None:
        super().__init__()
        if (
            total_path is None
            and maximum_value is None
            and has_more_path is None
            and not stop_after_empty_page
        ):
            raise ValueError(
                "One of 'total_path', 'maximum_value', 'has_more_path', or "
                "'stop_after_empty_page' must be provided."
            )
        if bool(param_name) == bool(param_body_path):
            raise ValueError(
                "Either 'param_name' or 'param_body_path' must be provided, not both."
            )
        self.param_name = param_name
        self.param_body_path = param_body_path
        self.initial_value = initial_value
        self.current_value = initial_value
        self.value_step = value_step
        self.base_index = base_index
        self.maximum_value = maximum_value
        self.total_path = jsonpath.compile_path(total_path) if total_path else None
        self.error_message_items = error_message_items
        self.stop_after_empty_page = stop_after_empty_page
        self.has_more_path = (
            jsonpath.compile_path(has_more_path) if has_more_path else None
        )

    def init_request(self, request: Request) -> None:
        self._has_next_page = True
        self.current_value = self.initial_value
        self.update_request(request)

    def update_state(
        self, response: httpx2.Response, data: list[Any] | None = None
    ) -> None:
        if self._stop_after_this_page(data):
            self._has_next_page = False
            return

        total: Any = None
        if self.total_path is not None:
            body = get_response_json(response)
            matches = self.total_path.find(body)
            total = matches[0].value if matches else None
            if total is None:
                self._handle_missing_total(body)
            try:
                total = int(total)
            except ValueError:
                self._handle_invalid_total(total)

        self.current_value += self.value_step
        if (total is not None and self.current_value >= total + self.base_index) or (
            self.maximum_value is not None and self.current_value >= self.maximum_value
        ):
            self._has_next_page = False
        if self.has_more_path is not None:
            body = get_response_json(response)
            matches = self.has_more_path.find(body)
            has_more: Any = matches[0].value if matches else None
            if has_more is None:
                self._handle_missing_has_more(body)
            elif isinstance(has_more, str):
                try:
                    has_more = str2bool(has_more)
                except ValueError:
                    self._handle_invalid_has_more(has_more)
            elif not isinstance(has_more, bool):
                self._handle_invalid_has_more(has_more)
            self._has_next_page = has_more

    def _stop_after_this_page(self, data: list[Any] | None = None) -> bool:
        return self.stop_after_empty_page and not data

    def _handle_missing_total(self, response_json: dict[str, Any]) -> None:
        raise ValueError(
            f"Total `{self.error_message_items}` not found in the response in"
            f" `{self.__class__.__name__}` .Expected a response with a `{self.total_path}` key, got"
            f" `{response_json}`."
        )

    def _handle_invalid_total(self, total: Any) -> None:
        raise ValueError(
            f"'{self.total_path}' is not an `int` in the response in `{self.__class__.__name__}`."
            f" Expected an integer, got `{total}`"
        )

    def _handle_missing_has_more(self, response_json: dict[str, Any]) -> None:
        raise ValueError(
            f"Has more value not found in the response in `{self.__class__.__name__}`."
            f"Expected a response with a `{self.has_more_path}` key, got"
            f" `{response_json}`."
        )

    def _handle_invalid_has_more(self, has_more: Any) -> None:
        raise ValueError(
            f"'{self.has_more_path}' is not a `bool` in the response in"
            f" `{self.__class__.__name__}`. Expected a boolean, got `{has_more}`"
        )

    def update_request(self, request: Request) -> None:
        if self.param_body_path:
            self._update_request_with_body_path(
                request, self.param_body_path, self.current_value
            )
        else:
            self._update_request_with_param_name(
                request, self.param_name, self.current_value
            )

    @staticmethod
    def _update_request_with_param_name(
        request: Request, param_name: str | None, current_value: int
    ) -> None:
        if not param_name:
            raise ValueError("'param_name' must not be empty.")
        if request.params is None:
            request.params = {}
        request.params[param_name] = current_value

    @staticmethod
    def _update_request_with_body_path(
        request: Request, body_path: str, current_value: int
    ) -> None:
        if not body_path:
            raise ValueError("`body_path` must not be empty.")
        if request.json is None:
            request.json = {}
        jsonpath.set_value_at_path(request.json, body_path, current_value)


class PageNumberPaginator(RangePaginator):
    """Increment page numbers in a query parameter or dot-separated body path.

    total_path defaults to 'total', as in DLT. Set it to None when the API
    instead relies on empty pages, maximum_page, or has_more_path to stop.
    """

    def __init__(
        self,
        base_page: int = 0,
        page: int | None = None,
        page_param: str | None = None,
        total_path: jsonpath.TJsonPath | None = "total",
        maximum_page: int | None = None,
        stop_after_empty_page: bool = True,
        *,
        has_more_path: jsonpath.TJsonPath | None = None,
        page_body_path: str | None = None,
    ) -> None:
        if page_param is None and page_body_path is None:
            page_param = "page"
        super().__init__(
            param_name=page_param,
            param_body_path=page_body_path,
            initial_value=page if page is not None else base_page,
            value_step=1,
            base_index=base_page,
            maximum_value=maximum_page,
            total_path=total_path,
            error_message_items="pages",
            stop_after_empty_page=stop_after_empty_page,
            has_more_path=has_more_path,
        )

    def __str__(self) -> str:
        return (
            super().__str__()
            + f": current page: {self.current_value} "
            + (f"page_param: {self.param_name} " if self.param_name else "")
            + (
                f"page_body_path: {self.param_body_path} "
                if self.param_body_path
                else ""
            )
            + f"total_path: {self.total_path} maximum_value: {self.maximum_value} "
            f"has_more_path: {self.has_more_path}"
        )


class OffsetPaginator(RangePaginator):
    """Increment an offset by a fixed limit, in query parameters or JSON bodies."""

    def __init__(
        self,
        limit: int,
        offset: int = 0,
        offset_param: str | None = None,
        limit_param: str | None = None,
        total_path: jsonpath.TJsonPath | None = "total",
        maximum_offset: int | None = None,
        stop_after_empty_page: bool = True,
        *,
        has_more_path: jsonpath.TJsonPath | None = None,
        offset_body_path: str | None = None,
        limit_body_path: str | None = None,
    ) -> None:
        if offset_param is None and offset_body_path is None:
            offset_param = "offset"
        if limit_param is None and limit_body_path is None:
            limit_param = "limit"
        if bool(limit_param) == bool(limit_body_path):
            raise ValueError(
                "Either 'limit_param' or 'limit_body_path' must be provided, not both."
            )
        super().__init__(
            param_name=offset_param,
            param_body_path=offset_body_path,
            initial_value=offset,
            value_step=limit,
            maximum_value=maximum_offset,
            total_path=total_path,
            stop_after_empty_page=stop_after_empty_page,
            has_more_path=has_more_path,
        )
        self.limit = limit
        self.limit_param = limit_param
        self.limit_body_path = limit_body_path

    def init_request(self, request: Request) -> None:
        super().init_request(request)
        self._update_request_limit(request)

    def update_request(self, request: Request) -> None:
        super().update_request(request)
        self._update_request_limit(request)

    def _update_request_limit(self, request: Request) -> None:
        if self.limit_body_path:
            self._update_request_with_body_path(
                request, self.limit_body_path, self.limit
            )
        else:
            self._update_request_with_param_name(request, self.limit_param, self.limit)

    def __str__(self) -> str:
        return (
            super().__str__()
            + f": current offset: {self.current_value} "
            + (f"offset_param: {self.param_name} " if self.param_name else "")
            + (
                f"offset_body_path: {self.param_body_path} "
                if self.param_body_path
                else ""
            )
            + f"limit: {self.value_step} "
            + (f"limit_param: {self.limit_param} " if self.limit_param else "")
            + (
                f"limit_body_path: {self.limit_body_path} "
                if self.limit_body_path
                else ""
            )
            + f"total_path: {self.total_path} "
            + f"maximum_value: {self.maximum_value} has_more_path: {self.has_more_path}"
        )


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
        if self._next_reference:
            parsed_url = urlparse(self._next_reference)
            if not parsed_url.scheme:
                self._next_reference = urljoin(
                    cast(str, request.url), self._next_reference
                )
        request.url = self._next_reference
        # Clear even on the final page, matching DLT's exposed request context.
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

    def __str__(self) -> str:
        return super().__str__() + f": links_next_key: {self.links_next_key}"


class JSONLinkPaginator(BaseNextUrlPaginator):
    """Follow a continuation URL selected from the decoded response body."""

    def __init__(self, next_url_path: jsonpath.TJsonPath = "next") -> None:
        super().__init__()
        self.next_url_path = jsonpath.compile_path(next_url_path)

    def update_state(
        self, response: httpx2.Response, data: list[Any] | None = None
    ) -> None:
        matches = self.next_url_path.find(get_response_json(response))
        self._next_reference = matches[0].value if matches else None

    def __str__(self) -> str:
        return super().__str__() + f": next_url_path: {self.next_url_path}"


class JSONResponseCursorPaginator(BaseReferencePaginator):
    """Read a cursor from JSON and place it in the query or request JSON body."""

    def __init__(
        self,
        cursor_path: jsonpath.TJsonPath = "cursors.next",
        cursor_param: str | None = None,
        cursor_body_path: str | None = None,
        *,
        stop_after_empty_page: bool = False,
        has_more_path: jsonpath.TJsonPath | None = None,
    ) -> None:
        super().__init__()
        if cursor_param is None and cursor_body_path is None:
            cursor_param = "cursor"
        elif cursor_param is not None and cursor_body_path is not None:
            raise ValueError(
                "Either 'cursor_param' or 'cursor_body_path' must be provided, not both."
            )
        self.cursor_path = jsonpath.compile_path(cursor_path)
        self.cursor_param = cursor_param
        self.cursor_body_path = cursor_body_path
        self.stop_after_empty_page = stop_after_empty_page
        self.has_more_path = (
            jsonpath.compile_path(has_more_path) if has_more_path else None
        )

    def update_state(
        self, response: httpx2.Response, data: list[Any] | None = None
    ) -> None:
        body = get_response_json(response)
        matches = self.cursor_path.find(body)
        self._next_reference = (
            matches[0].value if matches and matches[0].value else None
        )
        if self.stop_after_empty_page and not data:
            self._has_next_page = False
            return
        if self.has_more_path is not None:
            matches = self.has_more_path.find(body)
            has_more: Any = matches[0].value if matches else None
            if has_more is None:
                self._handle_missing_has_more(body)
            elif isinstance(has_more, str):
                try:
                    has_more = str2bool(has_more)
                except ValueError:
                    self._handle_invalid_has_more(has_more)
            elif not isinstance(has_more, bool):
                self._handle_invalid_has_more(has_more)
            self._has_next_page = has_more

    def _handle_missing_has_more(self, response_json: dict[str, Any]) -> None:
        raise ValueError(
            f"Has more value not found in the response in `{self.__class__.__name__}`."
            f"Expected a response with a `{self.has_more_path}` key, got"
            f" `{response_json}`."
        )

    def _handle_invalid_has_more(self, has_more: Any) -> None:
        raise ValueError(
            f"'{self.has_more_path}' is not a `bool` in the response in"
            f" `{self.__class__.__name__}`. Expected a boolean, got `{has_more}`"
        )

    def update_request(self, request: Request) -> None:
        if self.cursor_body_path:
            if request.json is None:
                request.json = {}
            jsonpath.set_value_at_path(
                request.json, self.cursor_body_path, self._next_reference
            )
        else:
            if request.params is None:
                request.params = {}
            request.params[cast(str, self.cursor_param)] = self._next_reference

    def __str__(self) -> str:
        return (
            super().__str__()
            + f": cursor_path: {self.cursor_path}"
            + (f" cursor_param: {self.cursor_param}" if self.cursor_param else "")
            + (
                f" cursor_body_path: {self.cursor_body_path}"
                if self.cursor_body_path
                else ""
            )
        )


class HeaderCursorPaginator(BaseReferencePaginator):
    """Read a continuation cursor from a response header."""

    def __init__(self, cursor_key: str = "next", cursor_param: str = "cursor") -> None:
        super().__init__()
        self.cursor_key = cursor_key
        self.cursor_param = cursor_param

    def update_state(
        self, response: httpx2.Response, data: list[Any] | None = None
    ) -> None:
        self._next_reference = response.headers.get(self.cursor_key)

    def update_request(self, request: Request) -> None:
        if request.params is None:
            request.params = {}
        request.params[self.cursor_param] = self._next_reference

    def __str__(self) -> str:
        return (
            super().__str__()
            + f": cursor_value: {self._next_reference} cursor_param: {self.cursor_param}"
        )
