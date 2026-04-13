import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel

from openhound.core.saved_searches import SavedSearches
from openhound.destinations.bloodhound.destination import Permissions, Strategy

TEST_DATA_DIR = Path(__file__).parent / "test_data" / "extensions" / "saved_searches"


class SavedSearch(BaseModel):
    # Required for an extension
    name: str
    description: str
    query: str


@pytest.fixture
def mock_bloodhound_api():
    """Mimic the BloodHound API to test saved search creation. Tracks all created/updated saved searches"""
    app = FastAPI()

    app.state.saved_searches = []
    app.state.saved_search_id_counter = 0
    app.state.permissions = {}

    @app.get("/api/v2/saved-queries")
    async def list_saved_queries():
        data = [
            {"id": sq["id"], "name": sq["name"], "query": sq["query"]}
            for sq in app.state.saved_searches
        ]
        return {"count": len(data), "data": data}

    @app.post("/api/v2/saved-queries")
    async def create_saved_query(search_content: SavedSearch):
        app.state.saved_search_id_counter += 1
        entry = {
            "id": app.state.saved_search_id_counter,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "user_id": "test-user",
            "name": search_content.name,
            "query": search_content.query,
            "description": search_content.description,
        }
        app.state.saved_searches.append(entry)
        return {"data": entry}

    @app.put("/api/v2/saved-queries/{query_id}")
    async def update_saved_query(query_id: int, search_content: SavedSearch):
        for sq in app.state.saved_searches:
            if sq["id"] == query_id:
                sq["name"] = search_content.name
                sq["query"] = search_content.query
                sq["description"] = search_content.description
                sq["updated_at"] = "2026-01-02T00:00:00Z"
                return {"data": sq}
        return Response(status_code=404)

    @app.put("/api/v2/saved-queries/{query_id}/permissions")
    async def set_saved_query_permissions(query_id: int, permissions: dict):
        app.state.permissions[query_id] = permissions
        return permissions

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


def test_saved_search_creates_new(mock_bh_requests, mock_bloodhound_api):
    """Test that creates a new saved searche when it does not already exist."""
    files = [TEST_DATA_DIR / "jamf_query_by_name.json"]
    pipeline = SavedSearches(progress="log")
    pipeline.run(files=files)

    assert len(mock_bloodhound_api.app.state.saved_searches) == 1
    created = mock_bloodhound_api.app.state.saved_searches[0]
    assert created["name"] == "Jamf: Account Access by Name"
    assert "jamf_Account" in created["query"]
    assert mock_bloodhound_api.app.state.permissions[created["id"]] == {"public": True}


def test_saved_search_skips_existing(mock_bh_requests, mock_bloodhound_api):
    """Test that duplicate saved searches creation is skipped by default"""
    files = [TEST_DATA_DIR / "jamf_query_by_name.json"]
    pipeline = SavedSearches(progress="log", strategy=Strategy.skip)

    # Run once
    pipeline.run(files=files)
    ## Run twice...
    pipeline.run(files=files)
    assert len(mock_bloodhound_api.app.state.saved_searches) == 1


def test_saved_search_overwrites_existing(mock_bh_requests, mock_bloodhound_api):
    """Test that duplicate saved search creation is overwritten when using the overwrite strategy."""
    files = [TEST_DATA_DIR / "jamf_query_by_name.json"]

    # First run with skip to create
    SavedSearches(progress="log", strategy=Strategy.skip).run(files=files)
    assert len(mock_bloodhound_api.app.state.saved_searches) == 1

    # Second run with overwrite (the dummy api sets the updated_at to a different timestamp to show that it was updated)
    SavedSearches(progress="log", strategy=Strategy.overwrite).run(files=files)

    # It should still be 1 search, but updated_at is now a new timestamp (overwritten in the dummy API)
    assert len(mock_bloodhound_api.app.state.saved_searches) == 1
    updated = mock_bloodhound_api.app.state.saved_searches[0]
    assert updated["updated_at"] == "2026-01-02T00:00:00Z"
    assert mock_bloodhound_api.app.state.permissions[updated["id"]] == {"public": True}


def test_saved_search_private_permissions(mock_bh_requests, mock_bloodhound_api):
    """Test that saved searches are not made public when private permissions are selected."""
    files = [TEST_DATA_DIR / "jamf_query_by_name.json"]
    pipeline = SavedSearches(progress="log", permissions=Permissions.private)
    pipeline.run(files=files)

    created = mock_bloodhound_api.app.state.saved_searches[0]
    assert created["id"] not in mock_bloodhound_api.app.state.permissions
