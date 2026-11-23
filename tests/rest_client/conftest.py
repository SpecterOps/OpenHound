import httpx2
import pytest
from openhound.core.clients.rest_client import AsyncRESTClient

from .api import create_app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def api_app():
    return create_app()


@pytest.fixture
async def rest_client(api_app, anyio_backend):
    transport = httpx2.ASGITransport(app=api_app)
    async with httpx2.AsyncClient(transport=transport) as session:
        yield AsyncRESTClient("http://testserver", session=session)
