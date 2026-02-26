"""Authentication helpers for Microsoft Fabric REST API.

Supports multiple authentication strategies (tried in order):
  1. ``FABRIC_ACCESS_TOKEN`` environment variable (pre-fetched JWT)
  2. Azure CLI  (``az login``)
  3. Azure PowerShell  (``Connect-AzAccount``)
  4. ``DefaultAzureCredential`` (env, managed-identity, VS Code, etc.)

If none of the ``azure-identity`` credential sources work, you can always
obtain a token from PowerShell and set it as an env var::

    $env:FABRIC_ACCESS_TOKEN = (Get-AzAccessToken -ResourceUrl "https://api.fabric.microsoft.com").Token
    python -m fabric_iq.cli ...
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from azure.identity import (
    AzureCliCredential,
    AzurePowerShellCredential,
    DefaultAzureCredential,
)
from azure.core.credentials import AccessToken, TokenCredential

from fabric_iq.config import FABRIC_RESOURCE_URL

logger = logging.getLogger(__name__)

# Fabric requires the .default scope appended to the resource URL
_SCOPES = [f"{FABRIC_RESOURCE_URL}/.default"]

# Environment variable name for a pre-fetched token
_TOKEN_ENV_VAR = "FABRIC_ACCESS_TOKEN"


@dataclass
class _StaticTokenCredential:
    """Wraps a raw JWT string as a ``TokenCredential``."""

    _token: str

    def get_token(self, *_scopes: str, **_kwargs) -> AccessToken:  # noqa: D401
        # Token lifetime unknown — set a generous expiry (1 hour from now).
        return AccessToken(self._token, int(time.time()) + 3600)


def get_credential(*, prefer_cli: bool = False) -> TokenCredential:
    """Return an Azure credential object.

    Resolution order:
      1. ``FABRIC_ACCESS_TOKEN`` env var  (static JWT)
      2. Azure CLI  (``az login``)
      3. Azure PowerShell  (``Connect-AzAccount``)
      4. ``DefaultAzureCredential``
    """
    # 1. Static token from environment
    static_token = os.environ.get(_TOKEN_ENV_VAR, "").strip()
    if static_token:
        logger.debug("Using static token from %s env var", _TOKEN_ENV_VAR)
        return _StaticTokenCredential(static_token)

    # 2. Azure CLI
    if prefer_cli:
        try:
            cred = AzureCliCredential()
            cred.get_token(*_SCOPES)
            logger.debug("Using AzureCliCredential")
            return cred
        except Exception:
            logger.debug("AzureCliCredential unavailable")

    # 3. Azure PowerShell
    try:
        cred = AzurePowerShellCredential()
        cred.get_token(*_SCOPES)
        logger.debug("Using AzurePowerShellCredential")
        return cred
    except Exception:
        logger.debug("AzurePowerShellCredential unavailable")

    # 4. Broad fallback
    return DefaultAzureCredential()


def get_access_token(credential: TokenCredential | None = None) -> str:
    """Obtain a bearer token string for the Fabric API."""
    if credential is None:
        credential = get_credential()

    token = credential.get_token(*_SCOPES)
    logger.debug("Access token acquired (expires %s)", token.expires_on)
    return token.token


def get_auth_headers(credential: TokenCredential | None = None) -> dict[str, str]:
    """Return HTTP headers with a valid Bearer token."""
    token = get_access_token(credential)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
