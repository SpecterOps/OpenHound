from dataclasses import dataclass
from typing import Any

import httpx2


@dataclass
class Request:
    """Mutable request arguments, encoded by httpx2 when sent.

    Like DLT's requests.Request, this keeps parameters and JSON editable by
    paginators before each send rather than storing an already encoded body.
    """

    method: str
    url: str
    headers: httpx2.Headers
    params: dict[str, Any] | None = None
    json: Any = None
    data: Any = None
    auth: httpx2.Auth | None = None
