import pytest


@pytest.mark.anyio
async def test_post_echo(rest_client):
    payload = {"message": "hello"}

    response = await rest_client.post("/echo", json=payload)

    assert response.status_code == 200
    assert response.json() == payload
