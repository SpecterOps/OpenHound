import httpx2
import pytest
from dlt.common.configuration.specs import RuntimeConfiguration

from openhound.core.clients.rest_client import AsyncRESTClient, AsyncSession

from .api import create_app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def api_app():
    return create_app()


@pytest.fixture
async def rest_client(api_app, anyio_backend, request):
    transport = httpx2.ASGITransport(app=api_app)
    config = RuntimeConfiguration(
        request_max_attempts=2,
        request_backoff_factor=0,
        request_max_retry_delay=0,
    )
    async with AsyncSession(
        transport=transport,
        config=config,
        max_concurrent_requests=getattr(request, "param", None),
    ) as session:
        yield AsyncRESTClient("http://testserver", session=session)
