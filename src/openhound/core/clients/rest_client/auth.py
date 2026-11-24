import asyncio
import math
from base64 import b64encode
from collections.abc import AsyncGenerator, Generator, Iterable
from dataclasses import field
from typing import Annotated, Any, Final, Literal, Self
from urllib.parse import urlencode
from weakref import WeakValueDictionary

import httpx2
from dlt.common.configuration import configspec
from dlt.common.configuration.specs import CredentialsConfiguration
from dlt.common.configuration.specs.base_configuration import NotResolved
from dlt.common.configuration.specs.exceptions import NativeValueError
from dlt.common.exceptions import MissingDependencyException
from dlt.common.pendulum import pendulum
from dlt.common.typing import TSecretStrValue

from .session import AsyncSession

# DLT's configspec currently requires a traditional alias for field hints.
TApiKeyLocation = Literal["header", "cookie", "query", "param"]


# Like DLT's default requests client, reuse one client across authenticators and
# refreshes. Its pools are opened lazily and follow the extraction loop lifetime.
_default_session = AsyncSession()


class AuthConfigBase(httpx2.Auth, CredentialsConfiguration):
    """Native httpx2 auth that DLT can populate from secrets or environment."""

    def __bool__(self) -> bool:
        return True

    async def prepare_auth(self, request: httpx2.Request) -> httpx2.Request:
        """Prepare authentication before REST retries, as DLT prepares requests.

        These DLT-style authenticators modify one outgoing request. Apply their
        headers/query once, rather than creating temporary credential objects.
        """
        return next(self.auth_flow(request))


@configspec
class BearerTokenAuth(AuthConfigBase):
    """Set the Authorization header using a bearer token."""

    token: TSecretStrValue = None  # type: ignore[assignment]

    def parse_native_representation(self, value: Any) -> None:
        if not isinstance(value, str):
            raise NativeValueError(
                type(self),
                value,
                "`token` argument of `BearerTokenAuth` must be a `str`. Got value of type"
                f" `{type(value)}`",
            )
        self.token = value

    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response]:
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


@configspec
class APIKeyAuth(AuthConfigBase):
    """Place an API key in a header or query parameter, as in DLT.

    Cookie authentication remains unimplemented, matching DLT 1.26.0.
    """

    name: str = "Authorization"
    api_key: TSecretStrValue = None  # type: ignore[assignment]
    location: TApiKeyLocation = "header"

    def parse_native_representation(self, value: Any) -> None:
        if not isinstance(value, str):
            raise NativeValueError(
                type(self),
                value,
                "`api_key` argument of `APIKeyAuth` must be a `str`. Got value of type"
                f" `{type(value)}`",
            )
        self.api_key = value

    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response]:
        if self.location == "header":
            request.headers[self.name] = self.api_key
        elif self.location in ("query", "param"):
            if self.api_key is not None:
                # requests.prepare_url appends auth params, preserving existing
                # query entries even when they have the same name.
                query = request.url.query.decode("ascii")
                encoded = urlencode({self.name: self.api_key})
                query = f"{query}&{encoded}" if query else encoded
                request.url = request.url.copy_with(query=query.encode("ascii"))
        elif self.location == "cookie":
            raise NotImplementedError()
        yield request


@configspec
class HttpBasicAuth(AuthConfigBase):
    """Set HTTP Basic authentication, accepting DLT's username/password fields."""

    username: str = None  # type: ignore[assignment]
    password: TSecretStrValue = None  # type: ignore[assignment]

    def parse_native_representation(self, value: Any) -> None:
        if isinstance(value, Iterable) and not isinstance(value, str):
            pair = list(value)
            if len(pair) == 2:
                self.username, self.password = pair
                return
        raise NativeValueError(
            type(self),
            value,
            "`username` and `password` arguments of `HttpBasicAuth` must be a `str` values. "
            f"Got values of type `{type(value)}`",
        )

    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response]:
        encoded = b64encode(f"{self.username}:{self.password}".encode()).decode()
        request.headers["Authorization"] = f"Basic {encoded}"
        yield request


@configspec
class OAuth2AuthBase(AuthConfigBase):
    """Use an existing OAuth2 access token without refreshing it."""

    access_token: TSecretStrValue = None  # type: ignore[assignment]

    def parse_native_representation(self, value: Any) -> None:
        if not isinstance(value, str):
            raise NativeValueError(
                type(self),
                value,
                "`access_token` argument of `OAuth2AuthBase` must be a `str`. Got value of type"
                f" `{type(value)}`",
            )
        self.access_token = value

    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response]:
        request.headers["Authorization"] = f"Bearer {self.access_token}"
        yield request


@configspec
class OAuth2ClientCredentials(OAuth2AuthBase):
    """Asynchronously acquire and refresh OAuth2 client-credentials tokens."""

    access_token: Annotated[TSecretStrValue | None, NotResolved()] = None  # type: ignore[assignment]
    access_token_url: str = None  # type: ignore[assignment]
    client_id: TSecretStrValue = None  # type: ignore[assignment]
    client_secret: TSecretStrValue = None  # type: ignore[assignment]
    access_token_request_data: dict[str, Any] = None  # type: ignore[assignment]
    default_token_expiration: int = 3600
    session: Annotated[AsyncSession, NotResolved()] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.access_token_request_data is None:
            self.access_token_request_data = {}
        self.token_expiry = pendulum.now()
        # Callers keep the lock alive while using it; idle locks must not retain
        # completed extraction loops when this authenticator is reused.
        self._refresh_locks: WeakValueDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = WeakValueDictionary()
        if self.session is None:
            self.session = _default_session

    def copy(self) -> Self:
        copied = super().copy()
        copied._refresh_locks = WeakValueDictionary()
        copied.token_expiry = self.token_expiry
        return copied

    def sync_auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response]:
        raise RuntimeError("OAuth2ClientCredentials requires an httpx2.AsyncClient.")

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        yield await self.prepare_auth(request)

    async def prepare_auth(self, request: httpx2.Request) -> httpx2.Request:
        lock = self._refresh_locks.setdefault(
            asyncio.get_running_loop(), asyncio.Lock()
        )
        async with lock:
            if self.access_token is None or self.is_token_expired():
                await self.obtain_token()
            return await super().prepare_auth(request)

    def is_token_expired(self) -> bool:
        return pendulum.now() >= self.token_expiry

    async def obtain_token(self) -> None:
        response = await self.session.post(
            self.access_token_url, **self.build_access_token_request()
        )
        # requests raises only for 4xx/5xx; httpx2 also raises for redirects.
        if response.status_code >= 400:
            response.raise_for_status()
        response_json = response.json()
        token = self.parse_access_token(response_json)
        expires_in_seconds = self.parse_expiration_in_seconds(response_json)
        token_expiry = pendulum.now().add(seconds=expires_in_seconds)
        self.parse_native_representation(token)
        self.token_expiry = token_expiry

    def build_access_token_request(self) -> dict[str, Any]:
        return {
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "data": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
                **self.access_token_request_data,
            },
        }

    def parse_expiration_in_seconds(self, response_json: Any) -> int:
        return int(response_json.get("expires_in", self.default_token_expiration))

    def parse_access_token(self, response_json: Any) -> str:
        token = response_json.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("OAuth access_token must be a non-empty string.")
        return token


@configspec
class OAuthJWTAuth(BearerTokenAuth):
    """Asynchronously exchange an RS256 JWT assertion for a bearer token.

    PyJWT and cryptography are optional dependencies, loaded only when signing.
    Private-key parsing and signing run in a worker thread.
    """

    token: Annotated[TSecretStrValue | None, NotResolved()] = None  # type: ignore[assignment]
    format: Final[Literal["JWT"]] = field(  # noqa: PYI064 - Preserve DLT's config hint.
        default="JWT", init=False, repr=False, compare=False
    )
    client_id: str = None  # type: ignore[assignment]
    private_key: TSecretStrValue = None  # type: ignore[assignment]
    auth_endpoint: str = None  # type: ignore[assignment]
    scopes: str | list[str] | None = None
    headers: dict[str, str] | None = None
    private_key_passphrase: TSecretStrValue | None = None
    default_token_expiration: int = 3600
    session: Annotated[AsyncSession, NotResolved()] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if isinstance(self.scopes, list):
            self.scopes = " ".join(self.scopes)
        self.token = None  # type: ignore[assignment]
        self.token_expiry: pendulum.DateTime | None = None
        self._refresh_locks: WeakValueDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = WeakValueDictionary()
        if self.session is None:
            self.session = _default_session

    def copy(self) -> Self:
        copied = super().copy()
        copied._refresh_locks = WeakValueDictionary()
        copied.token_expiry = self.token_expiry
        return copied

    def sync_auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response]:
        raise RuntimeError("OAuthJWTAuth requires an httpx2.AsyncClient.")

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        yield await self.prepare_auth(request)

    async def prepare_auth(self, request: httpx2.Request) -> httpx2.Request:
        lock = self._refresh_locks.setdefault(
            asyncio.get_running_loop(), asyncio.Lock()
        )
        async with lock:
            if self.token is None or self.is_token_expired():
                await self.obtain_token()
            return await super().prepare_auth(request)

    def is_token_expired(self) -> bool:
        return self.token_expiry is None or pendulum.now() >= self.token_expiry

    async def obtain_token(self) -> None:
        try:
            import jwt  # type: ignore[import-not-found]  # Optional dependency.
        except ModuleNotFoundError:
            raise MissingDependencyException("dlt OAuth helpers", ["PyJWT"])
        payload = self.create_jwt_payload()
        private_key = await asyncio.to_thread(self.load_private_key)
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": await asyncio.to_thread(
                jwt.encode, payload, private_key, algorithm="RS256"
            ),
        }
        response = await self.session.post(
            self.auth_endpoint, headers=self.headers, data=data
        )
        if response.status_code >= 400:
            response.raise_for_status()
        token_response = response.json()
        token = token_response.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("OAuth access_token must be a non-empty string.")
        expires_in_seconds = int(
            token_response.get("expires_in", self.default_token_expiration)
        )
        token_expiry = pendulum.now().add(seconds=expires_in_seconds)
        self.token = token
        self.token_expiry = token_expiry

    def create_jwt_payload(self) -> dict[str, Any]:
        now = pendulum.now()
        return {
            "iss": self.client_id,
            "sub": self.client_id,
            "aud": self.auth_endpoint,
            "exp": math.floor(now.add(hours=1).timestamp()),
            "iat": math.floor(now.timestamp()),
            "scope": self.scopes,
        }

    def load_private_key(self) -> Any:
        try:
            # Optional dependency; ordinary auth imports must not require it.
            from cryptography.hazmat.backends import (  # type: ignore[import-not-found]
                default_backend,
            )
            from cryptography.hazmat.primitives import (  # type: ignore[import-not-found]
                serialization,
            )
        except ModuleNotFoundError:
            raise MissingDependencyException("dlt OAuth helpers", ["cryptography"])
        return serialization.load_pem_private_key(
            self.private_key.encode(),
            password=(
                self.private_key_passphrase.encode()
                if self.private_key_passphrase
                else None
            ),
            backend=default_backend(),
        )
