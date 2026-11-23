from unittest.mock import Mock, call

import pytest
from dlt.common import jsonpath
from openhound.core.clients.rest_client import JSONLinkPaginator

from .api import BASE_ITEM_COUNT


@pytest.mark.anyio
async def test_post_echo(rest_client):
    payload = {"message": "hello"}

    response = await rest_client.post("/echo", json=payload)

    assert response.status_code == 200
    assert response.json() == payload


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
