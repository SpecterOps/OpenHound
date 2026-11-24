from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx2

type HookFunction = Callable[
    ..., httpx2.Response | None | Awaitable[httpx2.Response | None]
]
type Hooks = dict[str, HookFunction | list[HookFunction]]


@dataclass
class Request:
    """Mutable request arguments, encoded by httpx2 when sent.

    Like DLT's requests.Request, this keeps parameters and JSON editable by
    paginators before each send rather than storing an already encoded body.
    """

    method: str
    url: str | None
    headers: dict[str, Any]
    params: dict[str, Any] | None = None
    json: Any = None
    data: Any = None
    auth: httpx2.Auth | None = None
    hooks: Hooks | None = None


@dataclass
class PreparedRequest:
    """Native request and frozen auth/hook context for one logical request."""

    request: httpx2.Request
    auth: httpx2.Auth | None
    hooks: list[HookFunction]

    @property
    def url(self) -> httpx2.URL:
        return self.request.url
