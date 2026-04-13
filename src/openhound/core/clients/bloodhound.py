import base64
import datetime
import hashlib
import hmac
import logging
from abc import ABC, abstractmethod
from datetime import timedelta

import requests
from dlt.common import json
from dlt.common.exceptions import DltException

from .models import (
    AssetGroupsTags,
    CustomNodes,
    RunCypher,
    SavedQueries,
    SavedQuery,
    Selectors,
)

logger = logging.getLogger(__name__)


class BloodHoundHTTPError(DltException):
    def __init__(self, reason: str, code: int):
        self.reason = reason
        self.code = code
        super().__init__(
            f"Failed connection to the BloodHound API, received HTTP error {code}: {reason}"
        )


class BloodHoundClient(ABC):
    def __init__(self, base_uri: str = "http://localhost:8000"):
        self.base_uri = base_uri

    @abstractmethod
    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ): ...

    def _request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ):

        logger.debug(
            f"Making request to BloodHound API at {self.base_uri}{path}",
            extra={"path": path, "method": method},
        )
        if extra_headers:
            headers = {**headers, **extra_headers}

        response = requests.request(
            method=method,
            url=f"{self.base_uri}{path}",
            headers=headers,
            data=body,
        )

        if response.status_code not in [200, 201, 202]:
            raise BloodHoundHTTPError(code=response.status_code, reason=response.text)

        return response

    def graph_search(self, query: str):
        path = f"/api/v2/graph-search?query={query}&type=exact"
        response = self.request(method="GET", path=path)
        return response

    def query(self, query: str, include_properties: bool = False) -> RunCypher:
        path = "/api/v2/graphs/cypher"
        response = self.request(
            method="POST",
            path=path,
            body=json.dumps(
                {"query": query, "include_properties": include_properties}
            ).encode(),
        )
        return RunCypher.model_validate(response.json())

    @property
    def saved_queries(self) -> SavedQueries:
        path = "/api/v2/saved-queries"
        response = self.request(method="GET", path=path)
        return SavedQueries.model_validate(response.json())

    def create_saved_query(self, body: str) -> SavedQuery:
        path = "/api/v2/saved-queries"
        response = self.request(method="POST", path=path, body=body.encode())
        return SavedQuery.model_validate(response.json())

    def update_saved_query(self, query_id: int, body: str) -> SavedQuery:
        path = f"/api/v2/saved-queries/{query_id}"
        response = self.request(method="PUT", path=path, body=body.encode())
        return SavedQuery.model_validate(response.json())

    def set_query_permissions(self, query_id: int, permissions: dict) -> dict | None:
        path = f"/api/v2/saved-queries/{query_id}/permissions"
        response = self.request(
            method="PUT", path=path, body=json.dumps(permissions).encode()
        )
        if response.status_code == 200:
            return response.json()
        return None

    def custom_node(self, body: str) -> CustomNodes:
        path = "/api/v2/custom-nodes"
        response = self.request(method="POST", path=path, body=body.encode())
        return CustomNodes.model_validate(response.json())

    @property
    def asset_group_tags(self) -> AssetGroupsTags:
        path = "/api/v2/asset-group-tags"
        response = self.request(method="GET", path=path)
        return AssetGroupsTags.model_validate(response.json())

    def selectors(self, zone_id: int) -> Selectors:
        # TODO: Implement proper pagination
        path = f"/api/v2/asset-group-tags/{zone_id}/selectors?limit=999"
        response = self.request(method="GET", path=path)
        return Selectors.model_validate(response.json())

    def create_selector(self, zone_id: int, body: str) -> dict:
        path = f"/api/v2/asset-group-tags/{zone_id}/selectors"
        response = self.request(method="POST", path=path, body=body.encode())
        return response.json()

    def update_selector(self, zone_id: int, selector_id: str, body: str) -> dict:
        path = f"/api/v2/asset-group-tags/{zone_id}/selectors/{selector_id}"
        response = self.request(method="PATCH", path=path, body=body.encode())
        return response.json()


class BloodHound(BloodHoundClient):
    def __init__(
        self, token_key: str, token_id: str, bhe_uri: str = "http://localhost:8000"
    ):
        super().__init__(base_uri=bhe_uri)
        self.token_key = token_key
        self.token_id = token_id

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        # HMAC Part 1
        digester = hmac.new(self.token_key.encode(), None, hashlib.sha256)
        digester.update(f"{method}{path}".encode())

        # HMAC Part 2
        digester = hmac.new(digester.digest(), None, hashlib.sha256)
        datetime_formatted = (
            datetime.datetime.now(datetime.UTC).astimezone() - timedelta(hours=0)
        ).isoformat("T")
        datetime_short = datetime_formatted[0:13]
        digester.update(datetime_short.encode())

        # HMAC Part 3
        digester = hmac.new(digester.digest(), None, hashlib.sha256)
        if body is not None:
            digester.update(body)

        sig = base64.b64encode(digester.digest()).decode()
        headers = {
            "User-Agent": "openhound/0.1.0",
            "Authorization": f"bhesignature {self.token_id}",
            "RequestDate": datetime_formatted,
            "Signature": sig,
            "Content-Type": "application/json",
        }
        return self._request(
            method=method,
            path=path,
            headers=headers,
            body=body,
            extra_headers=extra_headers,
        )


class BloodHoundJWT(BloodHoundClient):
    def __init__(self, token: str, base_uri: str = "http://localhost:8000"):
        super().__init__(base_uri=base_uri)
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        headers = {
            "User-Agent": "openhound/0.1.0",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        return self._request(
            method=method,
            path=path,
            headers=headers,
            body=body,
            extra_headers=extra_headers,
        )
