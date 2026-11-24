import asyncio
import inspect
from collections.abc import AsyncGenerator, Iterable
from contextlib import nullcontext
from typing import Any, Self, cast
from urllib.parse import urlencode

import httpx2
from dlt.common.configuration.resolve import resolve_configuration
from dlt.common.configuration.specs import RuntimeConfiguration
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
)

from .retry import get_retry_response, retry_if_status, wait_retry_after
from .typing import PreparedRequest, Request

url_adapter = TypeAdapter(AnyHttpUrl)


def _retry_result(state: RetryCallState) -> httpx2.Response:
    assert state.outcome is not None
    return state.outcome.result()


async def _close_retry_response(state: RetryCallState) -> None:
    response = get_retry_response(state)
    if response is not None:
        await response.aclose()


class AsyncSession:
    """Prepare like requests.Session; send with async HTTP and DLT-style retries.

    Owned native pools are lazy and per event loop. DLT closes their lifetime
    generators at extraction shutdown. Outside DLT, use async with or aclose().
    A supplied native client remains caller-owned, keeps its configuration, and
    is not wrapped in retries. Finish shared work before closing owned pools.
    max_concurrent_requests limits active sends per session and event loop.
    Streaming sends release their slot when response headers arrive.
    """

    def __init__(
        self,
        client: httpx2.AsyncClient | None = None,
        *,
        config: RuntimeConfiguration | None = None,
        proxy_url: str | None = None,
        limits: httpx2.Limits | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
        max_concurrent_requests: int | None = None,
    ) -> None:
        if max_concurrent_requests is not None and max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be at least 1.")
        self.client = client
        self._config = (
            config
            if config is not None
            else resolve_configuration(RuntimeConfiguration())
        )
        self.proxy_url = proxy_url
        self.limits = limits
        self.transport = transport
        self.max_concurrent_requests = max_concurrent_requests
        self._semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}
        self._runtimes: dict[asyncio.AbstractEventLoop, httpx2.AsyncClient] = {}
        self._lifetimes: dict[
            asyncio.AbstractEventLoop, AsyncGenerator[httpx2.AsyncClient]
        ] = {}

    def _create_client(self) -> httpx2.AsyncClient:
        """Create a native HTTP/2 client, with HTTP/1.1 fallback and lazy opening."""
        proxy = None
        if self.proxy_url:
            try:
                proxy = str(url_adapter.validate_python(self.proxy_url))
            except ValidationError:
                raise ValueError(
                    "Invalid proxy_url; expected an HTTP(S) proxy URL"
                ) from None
        return httpx2.AsyncClient(
            http2=True,
            follow_redirects=True,
            proxy=proxy,
            trust_env=not bool(proxy),
            timeout=self._config.request_timeout,
            limits=(
                self.limits
                if self.limits is not None
                else httpx2.Limits(max_connections=None, max_keepalive_connections=None)
            ),
            transport=self.transport,
        )

    async def _lifetime(self) -> AsyncGenerator[httpx2.AsyncClient]:
        loop = asyncio.get_running_loop()
        # No I/O before yielding: concurrent resources cannot interleave pool
        # creation. Retain the pool until all extraction generators finish.
        client = self._create_client()
        try:
            yield client
        finally:
            try:
                await client.aclose()
            finally:
                self._runtimes.pop(loop, None)
                self._lifetimes.pop(loop, None)
                self._semaphores.pop(loop, None)

    async def _runtime(self) -> httpx2.AsyncClient:
        if self.client is not None:
            return self.client
        loop = asyncio.get_running_loop()
        if loop not in self._runtimes:
            lifetime = self._lifetime()
            self._runtimes[loop] = await anext(lifetime)
            self._lifetimes[loop] = lifetime
        return self._runtimes[loop]

    async def __aenter__(self) -> Self:
        await self._runtime()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the current loop's owned pool, not a supplied native client.

        Call only after shared work has finished. Do not race close against
        another close or requests. A later request can open a new owned pool.
        """
        lifetime = self._lifetimes.get(asyncio.get_running_loop())
        if lifetime is not None:
            await lifetime.aclose()
        self._semaphores.pop(asyncio.get_running_loop(), None)

    async def prepare_request(self, request: Request, **kwargs: Any) -> PreparedRequest:
        """Build and authenticate once, before any send retries.

        Preparation is awaitable because refreshing credentials can perform HTTP.
        Native authenticators without prepare_auth retain httpx2's auth handling.
        """
        client = await self._runtime()
        if request.hooks is not None and any(
            event != "response" for event in request.hooks
        ):
            raise ValueError("Only 'response' hooks are supported.")

        # requests appends params to the existing query, omits None values, and
        # encodes booleans as True/False rather than httpx2's true/false.
        params: dict[str, Any] = {
            key: client.params.get_list(key) for key in client.params
        }
        params.update(request.params or {})
        pairs: list[tuple[str, Any]] = []
        for key, values in params.items():
            if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
                values = [values]
            pairs.extend((key, value) for value in values if value is not None)
        url = httpx2.URL(cast(str, request.url))
        query = url.query.decode("ascii")
        encoded = urlencode(pairs, doseq=True)
        if encoded:
            query = f"{query}&{encoded}" if query else encoded

        prepared = client.build_request(
            request.method,
            url,
            headers={name.lower(): value for name, value in request.headers.items()},
            json=request.json,
            data=request.data if request.data or request.json is None else None,
            **kwargs,
        )
        prepared.url = prepared.url.copy_with(
            query=query.encode("ascii") if query else None
        )
        auth = request.auth if request.auth is not None else client.auth
        prepare_auth = getattr(auth, "prepare_auth", None)
        if prepare_auth is not None:
            prepared = await prepare_auth(prepared)
            # Do not let URL credentials override a header prepared by our auth.
            auth = httpx2.Auth()
        response_hooks = (request.hooks or {}).get("response", [])
        hooks = [response_hooks] if callable(response_hooks) else list(response_hooks)
        return PreparedRequest(prepared, auth, hooks)

    async def send(self, prepared: PreparedRequest, **kwargs: Any) -> httpx2.Response:
        """Send the same prepared request on each attempt; hooks run within retries.

        All methods can be retried. Streaming request bodies are not rewound.
        REST hooks get a buffered response so sync callbacks can inspect JSON.
        Without hooks, stream=True returns a native streaming response.
        """
        client = await self._runtime()
        hook_kwargs = kwargs.copy()
        semaphore = None
        if self.max_concurrent_requests is not None:
            loop = asyncio.get_running_loop()
            if loop not in self._semaphores:
                self._semaphores[loop] = asyncio.Semaphore(self.max_concurrent_requests)
            semaphore = self._semaphores[loop]
        if "timeout" in kwargs:
            prepared.request.extensions["timeout"] = httpx2.Timeout(
                kwargs.pop("timeout")
            ).as_dict()
        if "allow_redirects" in kwargs:
            kwargs.setdefault("follow_redirects", kwargs.pop("allow_redirects"))

        async def send() -> httpx2.Response:
            async with semaphore if semaphore is not None else nullcontext():
                response = await client.send(
                    prepared.request, auth=prepared.auth, **kwargs
                )
            try:
                if prepared.hooks:
                    await response.aread()
                for hook in prepared.hooks:
                    result = hook(response, **hook_kwargs)
                    if inspect.isawaitable(result):
                        result = await result
                    if result is not None:
                        response = result
                return response
            except BaseException:
                await response.aclose()
                raise

        if self.client is not None:
            return await send()
        retry = AsyncRetrying(
            retry=(
                retry_if_exception_type(
                    (
                        httpx2.NetworkError,
                        httpx2.TimeoutException,
                        httpx2.RemoteProtocolError,
                        httpx2.ProxyError,
                    )
                )
                | retry_if_status()
            ),
            wait=wait_retry_after(
                multiplier=self._config.request_backoff_factor,
                max=self._config.request_max_retry_delay,
            ),
            stop=stop_after_attempt(self._config.request_max_attempts),
            reraise=True,
            retry_error_callback=_retry_result,
            before_sleep=_close_retry_response,
        )
        return await retry(send)

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx2.Response:
        send_kwargs = {
            name: kwargs.pop(name)
            for name in ("timeout", "follow_redirects", "allow_redirects", "stream")
            if name in kwargs
        }
        request = Request(
            method=method,
            url=url,
            headers=kwargs.pop("headers", None) or {},
            params=kwargs.pop("params", None),
            json=kwargs.pop("json", None),
            data=kwargs.pop("data", None),
            auth=kwargs.pop("auth", None),
            hooks=kwargs.pop("hooks", None),
        )
        prepared = await self.prepare_request(request, **kwargs)
        return await self.send(prepared, **send_kwargs)

    async def get(self, url: str, **kwargs: Any) -> httpx2.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx2.Response:
        return await self.request("POST", url, **kwargs)
