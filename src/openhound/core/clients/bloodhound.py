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

import openhound

from .bhe_version import render_bhe_version
from .models import (
    AssetGroupsTags,
    CustomNodes,
    FileUploadJob,
    FileUploadJobResponse,
    FileUploadJobsResponse,
    RunCypher,
    SavedQueries,
    SavedQuery,
    Selectors,
)

logger = logging.getLogger(__name__)

# Seconds to wait for BloodHound when installing an extension schema: the first number is for
# opening the connection, the second for each wait on the reply. Without them a stalled server
# would block `schema upload` forever.
EXTENSION_UPLOAD_CONNECT_TIMEOUT_SECONDS = 10
EXTENSION_UPLOAD_READ_TIMEOUT_SECONDS = 120

# The same two kinds of limit, in seconds, for the edition question and the file-upload job calls
# used by `graph upload` and `convert --method ingest`. Attaching a file gets a longer reply limit,
# because BloodHound answers only after it has received and stored the whole file. Without these,
# a server that accepts the connection and then stops replying would block either command forever.
QUICK_REQUEST_TIMEOUT_SECONDS = (10, 60)
FILE_ATTACH_TIMEOUT_SECONDS = (10, 300)


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
        self.bhe_version = render_bhe_version(openhound.__version__)
        self.user_agent = f"openhound/{self.bhe_version}"

    @abstractmethod
    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: tuple[float, float] | None = None,
    ): ...

    def _request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: tuple[float, float] | None = None,
    ):

        logger.debug(
            f"Making request to BloodHound API at {self.base_uri}{path}",
            extra={"path": path, "method": method},
        )
        if extra_headers:
            headers = {**headers, **extra_headers}

        if timeout is None:
            response = requests.request(
                method=method,
                url=f"{self.base_uri}{path}",
                headers=headers,
                data=body,
            )
        else:
            response = requests.request(
                method=method,
                url=f"{self.base_uri}{path}",
                headers=headers,
                data=body,
                timeout=timeout,
            )

        if not 200 <= response.status_code < 300:
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

    def upsert_extension(self, body: bytes, content_type: str) -> bool:
        """Install a new OpenGraph extension schema, or update the one with the same name.

        BloodHound calls this operation an "upsert": it creates the extension when the
        schema name is new and updates it when the name already exists. The request body
        is either the schema as JSON, or a zip bundle that contains a ``schema.json`` file
        and optional ``saved_queries.json`` and ``pz_rules.json`` files. The content type
        tells BloodHound which of the two it is receiving.

        Args:
            body (bytes): The raw file contents to send.
            content_type (str): Either ``application/json`` or ``application/zip``.

        Returns:
            bool: True when an existing extension was updated, False when a new one was created.
        """
        path = "/api/v2/extensions"
        response = self.request(
            method="PUT",
            path=path,
            body=body,
            extra_headers={"Content-Type": content_type},
            timeout=(
                EXTENSION_UPLOAD_CONNECT_TIMEOUT_SECONDS,
                EXTENSION_UPLOAD_READ_TIMEOUT_SECONDS,
            ),
        )
        # BloodHound answers 200 (OK) for an update and 201 (Created) for a brand new extension.
        return response.status_code == 200

    def product_edition(self) -> str:
        """Ask BloodHound which edition it is, for example ``community`` or ``enterprise``.

        The answer comes from the ``product_edition`` field of ``GET /api/version``, which
        both editions provide to any signed-in caller.
        """
        response = self.request(
            method="GET", path="/api/version", timeout=QUICK_REQUEST_TIMEOUT_SECONDS
        )
        return response.json()["data"]["product_edition"]

    def start_file_upload_job(self) -> FileUploadJob:
        """Open a new file-upload job and return it.

        Files are attached to the job with :meth:`upload_file_to_job`, and the job is closed
        with :meth:`end_file_upload_job`. BloodHound only starts reading the files after the
        job is ended.
        """
        path = "/api/v2/file-upload/start"
        response = self.request(
            method="POST", path=path, timeout=QUICK_REQUEST_TIMEOUT_SECONDS
        )
        return FileUploadJobResponse.model_validate(response.json()).data

    def upload_file_to_job(
        self,
        job_id: int,
        body: bytes,
        content_type: str,
        file_name: str,
        content_encoding: str | None = None,
    ) -> None:
        """Attach one collection file to an open job.

        Args:
            job_id (int): The job returned by :meth:`start_file_upload_job`.
            body (bytes): The raw file contents, either an OpenGraph JSON document or a zip
                of such documents.
            content_type (str): ``application/json`` or ``application/zip``.
            file_name (str): The original file name, shown in BloodHound's file ingest page.
            content_encoding (str | None): ``gzip`` when ``body`` is gzip-compressed, so
                BloodHound unpacks it before reading. ``content_type`` still names the
                uncompressed file.
        """
        path = f"/api/v2/file-upload/{job_id}"
        extra_headers = {
            "Content-Type": content_type,
            "X-File-Upload-Name": file_name,
        }
        if content_encoding:
            extra_headers["Content-Encoding"] = content_encoding
        self.request(
            method="POST",
            path=path,
            body=body,
            extra_headers=extra_headers,
            timeout=FILE_ATTACH_TIMEOUT_SECONDS,
        )

    def end_file_upload_job(self, job_id: int) -> None:
        """Close a job so BloodHound starts reading the attached files."""
        path = f"/api/v2/file-upload/{job_id}/end"
        self.request(method="POST", path=path, timeout=QUICK_REQUEST_TIMEOUT_SECONDS)

    def file_upload_job(self, job_id: int) -> FileUploadJob | None:
        """Fetch the current state of one job, or None when BloodHound no longer lists it."""
        path = f"/api/v2/file-upload?id=eq:{job_id}"
        response = self.request(
            method="GET", path=path, timeout=QUICK_REQUEST_TIMEOUT_SECONDS
        )
        jobs = FileUploadJobsResponse.model_validate(response.json()).data
        return jobs[0] if jobs else None

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
        timeout: tuple[float, float] | None = None,
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
            "User-Agent": self.user_agent,
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
            timeout=timeout,
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
        timeout: tuple[float, float] | None = None,
    ):
        headers = {
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        return self._request(
            method=method,
            path=path,
            headers=headers,
            body=body,
            extra_headers=extra_headers,
            timeout=timeout,
        )
