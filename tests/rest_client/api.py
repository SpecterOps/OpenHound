import asyncio
from math import ceil
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Query, Request, Response

BASE_ITEM_COUNT = 20


def create_app() -> FastAPI:
    app = FastAPI(title="REST client test API")
    app.state.request_started = asyncio.Event()
    app.state.two_requests_started = asyncio.Event()
    app.state.wait_requests = 0
    app.state.release_request = asyncio.Event()
    app.state.retry_attempts = 0
    app.state.retry_exhaustion_attempts = 0
    items = [
        {"id": number, "name": f"Item {number}"} for number in range(1, BASE_ITEM_COUNT)
    ]

    @app.get("/object")
    async def get_object() -> dict[str, Any]:
        return items[0]

    @app.get("/list")
    async def get_list() -> list[dict[str, Any]]:
        return items

    @app.post("/echo")
    async def echo(payload: dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
        return payload

    @app.post("/token")
    async def token(request: Request) -> dict[str, Any]:
        form = parse_qs((await request.body()).decode())
        if form != {
            "client_id": ["test-client"],
            "client_secret": ["test-secret"],
            "grant_type": ["client_credentials"],
        }:
            raise HTTPException(status_code=401, detail="Invalid client credentials")
        return {
            "access_token": "test-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    @app.get("/authenticated")
    async def authenticated(request: Request) -> dict[str, bool]:
        if request.headers.get("Authorization") != "Bearer test-token":
            raise HTTPException(status_code=401, detail="Unauthorized")
        return {"authenticated": True}

    @app.get("/retry")
    async def retry() -> Response:
        app.state.retry_attempts += 1
        return Response(status_code=503 if app.state.retry_attempts == 1 else 200)

    @app.get("/retry-exhaustion")
    async def retry_exhaustion() -> Response:
        app.state.retry_exhaustion_attempts += 1
        return Response(status_code=503)

    @app.get("/wait")
    async def wait_for_release() -> dict[str, bool]:
        app.state.wait_requests += 1
        if app.state.wait_requests == 2:
            app.state.two_requests_started.set()
        app.state.request_started.set()
        await app.state.release_request.wait()
        return {"released": True}

    @app.get("/paginated/items", name="paginated_items")
    async def paginated_items(
        request: Request,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 2,
    ) -> dict[str, Any]:
        start = (page - 1) * page_size
        end = start + page_size
        next_url = None
        if end < len(items):
            next_url = str(
                request.url_for("paginated_items").include_query_params(
                    page=page + 1, page_size=page_size
                )
            )
        return {
            "data": {"items": items[start:end]},
            "pagination": {"next": next_url},
        }

    @app.get("/paginated/pages")
    async def paginated_pages(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 2,
    ) -> dict[str, Any]:
        start = (page - 1) * page_size
        return {
            "data": {"items": items[start : start + page_size]},
            "pagination": {"total_pages": ceil(len(items) / page_size)},
        }

    @app.get("/paginated/offset")
    async def paginated_offset(
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 2,
    ) -> dict[str, Any]:
        return {
            "data": {"items": items[offset : offset + limit]},
            "pagination": {"total_items": len(items)},
        }

    @app.get("/paginated/cursor")
    async def paginated_cursor(
        cursor: Annotated[int, Query(ge=0)] = 0,
        page_size: Annotated[int, Query(ge=1, le=100)] = 2,
    ) -> dict[str, Any]:
        end = cursor + page_size
        return {
            "data": {"items": items[cursor:end]},
            "pagination": {"next_cursor": str(end) if end < len(items) else None},
        }

    @app.get("/paginated/header-link", name="header_link_items")
    async def paginated_header_link(
        request: Request,
        response: Response,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 2,
    ) -> dict[str, Any]:
        start = (page - 1) * page_size
        end = start + page_size
        if end < len(items):
            next_url = request.url_for("header_link_items").include_query_params(
                page=page + 1, page_size=page_size
            )
            response.headers["Link"] = f'<{next_url}>; rel="next"'
        return {"data": {"items": items[start:end]}}

    @app.get("/paginated/header-cursor")
    async def paginated_header_cursor(
        response: Response,
        cursor: Annotated[int, Query(ge=0)] = 0,
        page_size: Annotated[int, Query(ge=1, le=100)] = 2,
    ) -> dict[str, Any]:
        end = cursor + page_size
        if end < len(items):
            response.headers["X-Next-Cursor"] = str(end)
        return {"data": {"items": items[cursor:end]}}

    return app
