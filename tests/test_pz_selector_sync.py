import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel

from openhound.core.privilege_zones import PrivilegeZones
from openhound.destinations.bloodhound.destination import Strategy

TEST_DATA_DIR = Path(__file__).parent / "test_data" / "extensions" / "pz_selectors"


class Seed(BaseModel):
    type: int
    value: str


class SelectorData(BaseModel):
    name: str
    description: str
    seeds: list[Seed]


@pytest.fixture
def mock_bloodhound_api():
    """Mimic the BloodHound API to test privilege zone selector creation. Tracks all created/updated saved searches and privilege zone selectors."""
    app = FastAPI()
    app.state.asset_group_tags = [
        {
            "id": 1,
            "type": 1,
            "kind_id": 1,
            "name": "Tier Zero",
            "description": "Tier Zero assets",
        },
    ]
    app.state.selectors = {
        1: [],  # selectors for asset group tag id=1
    }

    app.state.updated_selectors = []
    app.state.selector_id_counter = 0

    @app.get("/api/v2/asset-group-tags")
    async def list_asset_group_tags():
        return {"data": {"tags": app.state.asset_group_tags}}

    @app.get("/api/v2/asset-group-tags/{zone_id}/selectors")
    async def list_selectors(zone_id: int):
        selectors = app.state.selectors.get(zone_id, [])
        return {"data": {"selectors": selectors}}

    @app.post("/api/v2/asset-group-tags/{zone_id}/selectors")
    async def create_selector(zone_id: int, pz_data: SelectorData):
        app.state.selector_id_counter += 1
        entry = {
            "id": app.state.selector_id_counter,
            "asset_group_tag_id": zone_id,
            "name": pz_data.name,
        }
        if zone_id not in app.state.selectors:
            app.state.selectors[zone_id] = []
        app.state.selectors[zone_id].append(entry)
        return entry

    @app.patch("/api/v2/asset-group-tags/{zone_id}/selectors/{selector_id}")
    async def update_selector(zone_id: int, selector_id: int, pz_data: SelectorData):
        for sel in app.state.selectors.get(zone_id, []):
            if sel["id"] == selector_id:
                sel["name"] = pz_data.name
                app.state.updated_selectors.append(sel["name"])
                return sel
        return Response(status_code=404)

    return TestClient(app)


@pytest.fixture
def mock_bh_requests(monkeypatch, mock_bloodhound_api):
    """Patches requests.request to route through the mock BloodHound API."""

    def mock_request(method, url, **kwargs):
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        if method.upper() == "GET":
            return mock_bloodhound_api.get(path)
        elif method.upper() == "POST":
            return mock_bloodhound_api.post(path, **kwargs)
        elif method.upper() == "PUT":
            return mock_bloodhound_api.put(path, **kwargs)
        elif method.upper() == "PATCH":
            return mock_bloodhound_api.patch(path, **kwargs)

    monkeypatch.setattr("requests.request", mock_request)


@pytest.fixture(autouse=True)
def set_env_vars():
    """Set dummy variables for testing."""
    os.environ["DESTINATION__BLOODHOUND__URL"] = "http://localhost:8000"
    os.environ["DESTINATION__BLOODHOUND__TOKEN"] = "test-key"


def test_pz_selector_creates_new(mock_bh_requests, mock_bloodhound_api):
    """Test that running the privilege zones pipeline creates a new selector."""
    files = [TEST_DATA_DIR / "jamf_tenant.json"]
    pipeline = PrivilegeZones(progress="log")
    pipeline.run(files=files)

    selectors = mock_bloodhound_api.app.state.selectors[1]
    assert len(selectors) == 1
    assert selectors[0]["name"] == "Jamf: Tenant"
    assert selectors[0]["asset_group_tag_id"] == 1


def test_pz_selector_skips_existing(mock_bh_requests, mock_bloodhound_api):
    """Test that duplicate PZ selector creation is skipped by default"""
    files = [TEST_DATA_DIR / "jamf_tenant.json"]
    pipeline = PrivilegeZones(progress="log", strategy=Strategy.skip)

    # Run once
    pipeline.run(files=files)
    # Running twice...
    pipeline.run(files=files)

    selectors = mock_bloodhound_api.app.state.selectors[1]
    assert len(selectors) == 1


def test_pz_selector_overwrites_existing(mock_bh_requests, mock_bloodhound_api):
    """Test that duplicate PZ selector creation is overwritten when using the overwrite strategy."""
    files = [TEST_DATA_DIR / "jamf_tenant.json"]

    # First run to create a new selector
    PrivilegeZones(progress="log", strategy=Strategy.skip).run(files=files)
    selectors = mock_bloodhound_api.app.state.selectors[1]
    assert len(selectors) == 1

    # Second run with overwrite
    PrivilegeZones(progress="log", strategy=Strategy.overwrite).run(files=files)

    # Should still be 1 selector and the updated selector list also includes an entry
    selectors = mock_bloodhound_api.app.state.selectors[1]
    updated_selectors = mock_bloodhound_api.app.state.updated_selectors
    assert len(selectors) == 1
    assert len(updated_selectors) == 1


def test_pz_selector_unknown_zone_skipped(mock_bh_requests, mock_bloodhound_api):
    """Test that the PZ selector is not created if the zone does not exist."""
    mock_bloodhound_api.app.state.asset_group_tags = []

    files = [TEST_DATA_DIR / "jamf_tenant.json"]
    pipeline = PrivilegeZones(progress="log")
    pipeline.run(files=files)
    selectors = mock_bloodhound_api.app.state.selectors[1]
    assert len(selectors) == 0
