import copy
from collections.abc import AsyncIterator, Iterable
from typing import Any, Self
from urllib.parse import urlparse

import httpx2
from dlt.common import jsonpath, logger
from dlt.common.configuration.resolve import resolve_configuration
from dlt.common.configuration.specs import RuntimeConfiguration
from dlt.sources.helpers.rest_client.redaction import sanitize_url
from dlt.sources.helpers.rest_client.utils import join_url

from .exceptions import IgnoreResponseException
from .paginators import BasePaginator, DataSelector, get_response_json
from .session import AsyncSession
from .typing import Hooks, Request


def _safe_url(url: httpx2.URL) -> str:
    """Reuse DLT's query redaction, also omitting URL credentials and fragments."""
    return sanitize_url(str(url.copy_with(username="", password="", fragment="")))


class PageData[T](list[T]):
    """One page of records with DLT-style request and pagination context.

    request is the shared mutable request, already updated for the next page.
    Consumers can modify it before advancing the iterator. To inspect the HTTP
    request that produced this page, use response.request instead. Context is
    live, not a snapshot, matching DLT.
    """

    def __init__(
        self,
        iterable: Iterable[T],
        request: Request,
        response: httpx2.Response,
        paginator: BasePaginator,
        auth: httpx2.Auth | None,
    ) -> None:
        super().__init__(iterable)
        self.request = request
        self.response = response
        self.paginator = paginator
        self.auth = auth


class AsyncRESTClient:
    """A DLT-style REST client using async HTTP/2 and explicit pagination.

    Unlike DLT's synchronous client, pagination and selectors are not detected
    automatically. paginate() requires an explicit paginator, including for
    single-page responses. Proxy and pool settings apply only to owned sessions.

    Owned pools are created lazily per event loop and closed by DLT's extraction
    shutdown, async with, or aclose(). A supplied session remains caller-owned;
    its protocol, proxy, and limits are not changed. Use it only on its owning
    event loop.

    A context must surround all resources sharing this client, not just a parent
    resource whose child transformers may still be running. Finishing pagination
    or cancelling one request does not close a pool shared by other resources.
    """

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        auth: httpx2.Auth | None = None,
        paginator: BasePaginator | None = None,
        data_selector: jsonpath.TJsonPath | None = None,
        session: AsyncSession | httpx2.AsyncClient | None = None,
        config: RuntimeConfiguration | None = None,
        *,
        proxy_url: str | None = None,
        limits: httpx2.Limits | None = None,
    ) -> None:
        self.base_url = base_url
        self.headers = headers
        self.auth = auth
        self.paginator = paginator
        self.data_selector = DataSelector(data_selector)
        self._config = (
            config
            if config is not None
            else resolve_configuration(RuntimeConfiguration())
        )
        self._owns_session = not isinstance(session, AsyncSession)
        self.session = (
            session
            if isinstance(session, AsyncSession)
            else AsyncSession(
                client=session,
                config=self._config,
                proxy_url=proxy_url,
                limits=limits,
            )
        )

    async def __aenter__(self) -> Self:
        if self._owns_session:
            await self.session.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close this loop's owned pool; supplied sessions remain caller-owned.

        Call after all requests and transformers sharing the pool have finished.
        Do not race closure against requests or another aclose(). Repeated calls
        after completed cleanup are harmless; a later request opens a new pool.
        This does not close pools belonging to other event loops or auth clients.
        Outside DLT, use async with or await aclose() before stopping the loop.
        """
        if self._owns_session:
            await self.session.aclose()

    def _create_request(
        self,
        path_or_url: str,
        method: str,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        auth: httpx2.Auth | None = None,
        hooks: Hooks | None = None,
    ) -> Request:
        if json and data:
            raise ValueError(
                "Cannot use both 'json' and 'data' parameters simultaneously. "
                "Use 'json' for JSON payloads or 'data' for form-encoded/raw data."
            )
        url = (
            path_or_url
            if urlparse(path_or_url).scheme in ("http", "https")
            else join_url(self.base_url, path_or_url)
        )
        request_headers = (self.headers or {}) | (headers or {})
        return Request(
            method=method,
            url=url,
            headers=request_headers,
            params=params,
            json=json,
            data=data,
            auth=auth or self.auth,
            hooks=hooks,
        )

    async def _send_request(self, request: Request, **kwargs: Any) -> httpx2.Response:
        prepared = await self.session.prepare_request(request)
        self._log_request(request, prepared.url)
        return await self.session.send(prepared, **kwargs)

    def _log_request(self, request: Request, prepared_url: httpx2.URL) -> None:
        # Never log bodies or headers, even at DEBUG: they may contain secrets.
        logger.info(
            f"Making {request.method.upper()} request to {_safe_url(prepared_url)}"
        )

    async def request(
        self, path: str = "", method: str = "GET", **kwargs: Any
    ) -> httpx2.Response:
        """Send without a default HTTP error handler; explicit hooks still run.

        hooks={'response': callback_or_list} accepts sync or async callbacks.
        Like requests/DLT hooks, they receive the response and send options as
        keyword arguments, and may return a replacement response or None.
        Hooks run on the final response of each attempt (after redirects).
        """
        request = self._create_request(
            path_or_url=path,
            method=method,
            headers=kwargs.pop("headers", None),
            params=kwargs.pop("params", None),
            json=kwargs.pop("json", None),
            data=kwargs.pop("data", None),
            auth=kwargs.pop("auth", None),
            hooks=kwargs.pop("hooks", None),
        )
        return await self._send_request(request, **kwargs)

    async def get(
        self, path: str, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> httpx2.Response:
        return await self.request(path, method="GET", params=params, **kwargs)

    async def post(
        self,
        path: str,
        json: Any = None,
        *,
        data: Any = None,
        **kwargs: Any,
    ) -> httpx2.Response:
        return await self.request(path, method="POST", json=json, data=data, **kwargs)

    async def paginate(
        self,
        path: str = "",
        method: str = "GET",
        params: dict[str, Any] | None = None,
        json: Any = None,
        auth: httpx2.Auth | None = None,
        paginator: BasePaginator | None = None,
        data_selector: jsonpath.TJsonPath | None = None,
        hooks: Hooks | None = None,
        headers: dict[str, str] | None = None,
        *,
        data: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[PageData[Any]]:
        """Yield list-compatible pages with request, response, paginator, and auth.

        Selectors compile once before iteration; JSON decodes once per response.
        Without a selector, the entire response body is selected. The paginator
        updates state and the next request before yielding, as in DLT.
        HTTP errors raise by default. Supplying a 'response' hook, even an empty
        list, replaces that handler. IgnoreResponseException stops iteration.
        """
        pagination = (
            paginator if paginator is not None else copy.deepcopy(self.paginator)
        )
        if pagination is None:
            raise ValueError(
                "paginate() requires a paginator on the client or call. "
                "Use SinglePagePaginator() for single-page responses."
            )
        # Reuse the client's compiled selector; compile an override only once.
        selector = (
            self.data_selector if data_selector is None else DataSelector(data_selector)
        )
        hooks = dict(hooks) if hooks is not None else {}
        if "response" not in hooks:
            hooks["response"] = self._raise_for_status
        request = self._create_request(
            path_or_url=path,
            method=method,
            params=params,
            headers=headers,
            json=json,
            data=data,
            auth=auth,
            hooks=hooks,
        )
        pagination.init_request(request)
        while True:
            try:
                response = await self._send_request(request, **kwargs)
            except IgnoreResponseException:
                return
            await response.aread()
            page = self.extract_response(response, selector)
            pagination.update_state(response, page)
            pagination.update_request(request)
            yield PageData(
                page,
                request=request,
                response=response,
                paginator=pagination,
                auth=request.auth,
            )
            if not pagination.has_next_page:
                return

    def _raise_for_status(self, response: httpx2.Response, **kwargs: Any) -> None:
        """DLT-style HTTP error messages with safe URLs and optional body text."""
        if response.status_code < 400:
            return
        error_type = "Client" if response.status_code < 500 else "Server"
        message = (
            f"{response.status_code} {error_type} Error: {response.reason_phrase} "
            f"for url: {_safe_url(response.url)}"
        )
        if self._config.http_show_error_body and response.text:
            body = response.text
            limit = self._config.http_max_error_body_length
            if len(body) > limit:
                body = body[:limit] + "… (truncated)"
            message += f"\nResponse: {body}"
        raise httpx2.HTTPStatusError(
            message, request=response.request, response=response
        )

    def extract_response(
        self,
        response: httpx2.Response,
        data_selector: jsonpath.TJsonPath | DataSelector | None,
    ) -> list[Any]:
        """Select records and normalize to a list, matching DLT's extraction.

        paginate() passes its precompiled DataSelector. Direct callers may also
        supply a JSONPath or None to select the whole body.
        """
        selector = (
            data_selector
            if isinstance(data_selector, DataSelector)
            else DataSelector(data_selector)
        )
        data = selector.select(get_response_json(response))
        if data is None:
            return []
        if not isinstance(data, list):
            return [data]
        return data
