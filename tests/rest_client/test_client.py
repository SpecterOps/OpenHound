import asyncio
from unittest.mock import Mock, call

import pendulum
import pytest
from dlt.common import jsonpath

from openhound.core.clients.rest_client import (
    HeaderCursorPaginator,
    HeaderLinkPaginator,
    JSONLinkPaginator,
    JSONResponseCursorPaginator,
    OffsetPaginator,
    PageNumberPaginator,
    SinglePagePaginator,
)
from openhound.core.clients.rest_client.auth import (
    BearerTokenAuth,
    OAuth2ClientCredentials,
)

from .api import BASE_ITEM_COUNT


@pytest.mark.anyio
async def test_post_echo(rest_client):
    payload = {"message": "hello"}

    response = await rest_client.post("/echo", json=payload)

    assert response.status_code == 200
    assert response.json() == payload


@pytest.mark.anyio
async def test_bearer_authentication(rest_client):
    response = await rest_client.get(
        "/authenticated", auth=BearerTokenAuth(token="test-token")
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


@pytest.mark.anyio
async def test_oauth2_client_credentials(rest_client):
    credentials = OAuth2ClientCredentials(
        access_token_url="http://testserver/token",
        client_id="test-client",
        client_secret="test-secret",
        session=rest_client.session,
    )

    response = await rest_client.get("/authenticated", auth=credentials)

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


@pytest.mark.anyio
async def test_oauth2_refresh_expired_token(rest_client):
    credentials = OAuth2ClientCredentials(
        access_token="expired-token",
        access_token_url="http://testserver/token",
        client_id="test-client",
        client_secret="test-secret",
        session=rest_client.session,
    )
    credentials.token_expiry = pendulum.now().subtract(seconds=1)

    response = await rest_client.get("/authenticated", auth=credentials)

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}
    assert credentials.access_token == "test-token"


@pytest.mark.anyio
async def test_retry_on_service_unavailable(rest_client, api_app):
    response = await rest_client.get("/retry")

    assert response.status_code == 200
    assert api_app.state.retry_attempts == 2


@pytest.mark.anyio
async def test_retry_exhaustion(rest_client, api_app):
    response = await rest_client.get("/retry-exhaustion")

    assert response.status_code == 503
    assert api_app.state.retry_exhaustion_attempts == 2


@pytest.mark.anyio
async def test_pagination(rest_client):
    pages = [
        page
        async for page in rest_client.paginate(
            "/paginated/items",
            params={"page_size": 5},
            data_selector="data.items",
            paginator=JSONLinkPaginator("pagination.next"),
        )
    ]

    assert len(pages) > 1
    assert [item["id"] for page in pages for item in page] == list(
        range(1, BASE_ITEM_COUNT)
    )


@pytest.mark.anyio
async def test_page_number_pagination(rest_client):
    pages = [
        page
        async for page in rest_client.paginate(
            "/paginated/pages",
            params={"page_size": 5},
            data_selector="data.items",
            paginator=PageNumberPaginator(
                base_page=1, total_path="pagination.total_pages"
            ),
        )
    ]

    assert len(pages) == 4
    assert [item["id"] for page in pages for item in page] == list(
        range(1, BASE_ITEM_COUNT)
    )


@pytest.mark.anyio
async def test_offset_pagination(rest_client):
    pages = [
        page
        async for page in rest_client.paginate(
            "/paginated/offset",
            data_selector="data.items",
            paginator=OffsetPaginator(limit=5, total_path="pagination.total_items"),
        )
    ]

    assert len(pages) == 4
    assert [item["id"] for page in pages for item in page] == list(
        range(1, BASE_ITEM_COUNT)
    )


@pytest.mark.anyio
async def test_json_cursor_pagination(rest_client):
    pages = [
        page
        async for page in rest_client.paginate(
            "/paginated/cursor",
            params={"page_size": 5},
            data_selector="data.items",
            paginator=JSONResponseCursorPaginator(cursor_path="pagination.next_cursor"),
        )
    ]

    assert len(pages) == 4
    assert [item["id"] for page in pages for item in page] == list(
        range(1, BASE_ITEM_COUNT)
    )


@pytest.mark.anyio
async def test_header_link_pagination(rest_client):
    pages = [
        page
        async for page in rest_client.paginate(
            "/paginated/header-link",
            params={"page_size": 5},
            data_selector="data.items",
            paginator=HeaderLinkPaginator(),
        )
    ]

    assert len(pages) == 4
    assert [item["id"] for page in pages for item in page] == list(
        range(1, BASE_ITEM_COUNT)
    )


@pytest.mark.anyio
async def test_header_cursor_pagination(rest_client):
    pages = [
        page
        async for page in rest_client.paginate(
            "/paginated/header-cursor",
            params={"page_size": 5},
            data_selector="data.items",
            paginator=HeaderCursorPaginator(cursor_key="X-Next-Cursor"),
        )
    ]

    assert len(pages) == 4
    assert [item["id"] for page in pages for item in page] == list(
        range(1, BASE_ITEM_COUNT)
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "expected_ids"),
    [("/object", [1]), ("/list", list(range(1, BASE_ITEM_COUNT)))],
)
async def test_single_page_pagination(rest_client, path, expected_ids):
    pages = [
        page
        async for page in rest_client.paginate(path, paginator=SinglePagePaginator())
    ]

    assert len(pages) == 1
    assert [item["id"] for item in pages[0]] == expected_ids


@pytest.mark.anyio
async def test_data_selector_compiled_once(rest_client, monkeypatch):
    compile_path = Mock(wraps=jsonpath.compile_path)
    monkeypatch.setattr(jsonpath, "compile_path", compile_path)

    pages = [
        page
        async for page in rest_client.paginate(
            "/paginated/items",
            params={"page_size": 5},
            data_selector="data.items",
            paginator=JSONLinkPaginator("pagination.next"),
        )
    ]

    assert len(pages) > 1
    assert compile_path.call_args_list.count(call("data.items")) == 1
    assert compile_path.call_args_list.count(call("pagination.next")) == 1


@pytest.mark.anyio
async def test_requests_are_non_blocking(rest_client, api_app):
    waiting = asyncio.create_task(rest_client.get("/wait"))
    try:
        await asyncio.wait_for(api_app.state.request_started.wait(), timeout=5)

        payload = {"message": "hello"}
        response = await asyncio.wait_for(
            rest_client.post("/echo", json=payload), timeout=5
        )

        assert response.status_code == 200
        assert response.json() == payload
        assert not waiting.done()
    finally:
        api_app.state.release_request.set()
        waiting_response = await asyncio.wait_for(waiting, timeout=5)

    assert waiting_response.status_code == 200
