from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2

hookfunction: str

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
    headers: httpx2.Headers
    params: dict[str, Any] | None = None
    json: Any = None
    data: Any = None
    auth: httpx2.Auth | None = None
    hooks: Hooks = field(default_factory=dict)
