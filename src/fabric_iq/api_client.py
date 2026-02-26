"""Low-level Fabric REST API client with Long Running Operation (LRO) support."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from azure.core.credentials import TokenCredential

from fabric_iq.auth import get_access_token
from fabric_iq.config import FABRIC_API_BASE, DEFAULT_RETRY_SECONDS, MAX_WAIT_SECONDS

logger = logging.getLogger(__name__)


class FabricApiError(Exception):
    """Raised when a Fabric REST API call fails."""

    def __init__(self, message: str, status_code: int | None = None, detail: str = ""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class FabricClient:
    """Thin wrapper around ``requests`` for the Fabric REST API.

    Handles:
      - Bearer-token injection (with automatic refresh via credential)
      - Long Running Operation (LRO) polling
      - Common CRUD helpers for ontologies & semantic models
    """

    def __init__(
        self,
        credential: TokenCredential,
        *,
        api_base: str = FABRIC_API_BASE,
        retry_seconds: int = DEFAULT_RETRY_SECONDS,
        max_wait_seconds: int = MAX_WAIT_SECONDS,
    ):
        self._credential = credential
        self._api_base = api_base.rstrip("/")
        self._retry_seconds = retry_seconds
        self._max_wait_seconds = max_wait_seconds
        self._session = requests.Session()
        self._refresh_headers()

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------
    def _refresh_headers(self) -> None:
        token = get_access_token(self._credential)
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    def refresh_token(self) -> None:
        """Explicitly refresh the bearer token (useful for long scripts)."""
        self._refresh_headers()
        logger.info("Token refreshed")

    # ------------------------------------------------------------------
    # Raw HTTP
    # ------------------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self._api_base}/{path.lstrip('/')}"

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        return self._session.get(self._url(path), **kwargs)

    def post(self, path: str, json: Any = None, **kwargs: Any) -> requests.Response:
        return self._session.post(self._url(path), json=json, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> requests.Response:
        return self._session.delete(self._url(path), **kwargs)

    # ------------------------------------------------------------------
    # LRO polling
    # ------------------------------------------------------------------
    def poll_lro(self, location_url: str) -> dict:
        """Poll a Fabric Long Running Operation until terminal state.

        Parameters
        ----------
        location_url:
            The ``Location`` header value from the 202 response.

        Returns
        -------
        dict
            The final operation status payload (``status == "Succeeded"``).

        Raises
        ------
        FabricApiError
            If the operation fails or times out.
        """
        elapsed = 0
        while elapsed < self._max_wait_seconds:
            time.sleep(self._retry_seconds)
            elapsed += self._retry_seconds

            resp = self._session.get(location_url)
            resp.raise_for_status()
            body = resp.json()
            status = body.get("status", "Unknown")

            logger.info("  Polling LRO … status=%s (elapsed=%ds)", status, elapsed)

            if status == "Succeeded":
                return body
            if status == "Failed":
                error = body.get("error", {})
                raise FabricApiError(
                    f"LRO failed: {error.get('errorCode', 'Unknown')} – {error.get('message', '')}",
                    detail=str(body),
                )

        raise FabricApiError(f"LRO timed out after {self._max_wait_seconds}s")

    def post_with_lro(self, path: str, json: Any = None) -> dict | None:
        """POST and handle 200/201/202 transparently.

        Returns
        -------
        dict | None
            Parsed JSON body for 200/201, or the LRO *result* for 202.
        """
        resp = self.post(path, json=json)
        code = resp.status_code

        if code in (200, 201):
            return resp.json() if resp.text else None

        if code == 202:
            location = resp.headers.get("Location") or resp.headers.get("location")
            op_id = resp.headers.get("x-ms-operation-id")
            if not location and op_id:
                location = self._url(f"operations/{op_id}")
            if not location:
                raise FabricApiError("202 with no Location header", status_code=202)

            self.poll_lro(location)

            # Fetch the /result sub-resource
            result_resp = self._session.get(f"{location}/result")
            if result_resp.status_code == 200:
                return result_resp.json()
            return None

        # Unexpected status
        detail = ""
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise FabricApiError(
            f"Unexpected HTTP {code}",
            status_code=code,
            detail=str(detail),
        )

    # ------------------------------------------------------------------
    # Ontology CRUD
    # ------------------------------------------------------------------
    def list_ontologies(self, workspace_id: str) -> list[dict]:
        """Return list of ontologies in a workspace."""
        resp = self.get(f"workspaces/{workspace_id}/ontologies")
        resp.raise_for_status()
        return resp.json().get("value", [])

    def create_ontology(
        self,
        workspace_id: str,
        display_name: str,
        description: str = "",
    ) -> str:
        """Create a new (empty) ontology. Returns the ontology ID."""
        body = {"displayName": display_name, "description": description}
        result = self.post_with_lro(f"workspaces/{workspace_id}/ontologies", json=body)
        if not result or "id" not in result:
            raise FabricApiError("Failed to get ontology ID after creation")
        ont_id: str = result["id"]
        logger.info("Created ontology %s", ont_id)
        return ont_id

    def delete_ontology(self, workspace_id: str, ontology_id: str) -> None:
        """Delete an ontology."""
        resp = self.delete(f"workspaces/{workspace_id}/ontologies/{ontology_id}")
        resp.raise_for_status()
        logger.info("Deleted ontology %s", ontology_id)

    def get_ontology_definition(self, workspace_id: str, ontology_id: str) -> dict:
        """Retrieve the full definition (``{ parts: [...] }``)."""
        result = self.post_with_lro(
            f"workspaces/{workspace_id}/ontologies/{ontology_id}/getDefinition",
            json={"definition": {"parts": []}},
        )
        return result.get("definition", {}) if result else {}

    def update_ontology_definition(
        self,
        workspace_id: str,
        ontology_id: str,
        parts: list[dict],
        *,
        update_metadata: bool = True,
    ) -> None:
        """Upload / replace the ontology definition."""
        path = f"workspaces/{workspace_id}/ontologies/{ontology_id}/updateDefinition"
        if update_metadata:
            path += "?updateMetadata=True"

        body = {"definition": {"parts": parts}}
        self.post_with_lro(path, json=body)
        logger.info("Updated definition for ontology %s (%d parts)", ontology_id, len(parts))

    # ------------------------------------------------------------------
    # Semantic Model helpers
    # ------------------------------------------------------------------
    def get_semantic_model_definition(self, workspace_id: str, sm_id: str) -> dict:
        """Retrieve the TMDL definition of a Semantic Model."""
        result = self.post_with_lro(
            f"workspaces/{workspace_id}/semanticModels/{sm_id}/getDefinition",
        )
        return result if result else {}
