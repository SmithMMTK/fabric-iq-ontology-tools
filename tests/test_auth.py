"""Tests for auth module."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from fabric_iq.auth import (
    _StaticTokenCredential,
    get_access_token,
    get_auth_headers,
    get_credential,
)


class TestStaticTokenCredential:
    def test_get_token_returns_access_token(self):
        cred = _StaticTokenCredential("my-jwt-token")
        token = cred.get_token("https://example.com/.default")
        assert token.token == "my-jwt-token"

    def test_get_token_expiry_is_in_future(self):
        cred = _StaticTokenCredential("my-jwt-token")
        token = cred.get_token()
        assert token.expires_on > int(time.time())

    def test_get_token_ignores_extra_scopes(self):
        cred = _StaticTokenCredential("abc")
        token = cred.get_token("scope1", "scope2", tenant_id="t")
        assert token.token == "abc"


class TestGetCredential:
    def test_returns_static_credential_when_env_var_set(self, monkeypatch):
        monkeypatch.setenv("FABRIC_ACCESS_TOKEN", "  test-token  ")
        cred = get_credential()
        assert isinstance(cred, _StaticTokenCredential)
        assert cred.get_token().token == "test-token"

    def test_static_token_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("FABRIC_ACCESS_TOKEN", "  padded  ")
        cred = get_credential()
        assert isinstance(cred, _StaticTokenCredential)
        assert cred.get_token().token == "padded"

    def test_static_token_takes_priority_over_cli(self, monkeypatch):
        monkeypatch.setenv("FABRIC_ACCESS_TOKEN", "priority-token")
        with patch("fabric_iq.auth.AzureCliCredential") as mock_cli:
            cred = get_credential(prefer_cli=True)
        mock_cli.assert_not_called()
        assert isinstance(cred, _StaticTokenCredential)

    def test_returns_default_credential_when_no_env_var(self, monkeypatch):
        monkeypatch.delenv("FABRIC_ACCESS_TOKEN", raising=False)
        with (
            patch("fabric_iq.auth.AzurePowerShellCredential") as mock_ps,
            patch("fabric_iq.auth.DefaultAzureCredential") as mock_default,
        ):
            mock_ps.return_value.get_token.side_effect = Exception("unavailable")
            get_credential()
        mock_default.assert_called_once()

    def test_prefer_cli_tries_azure_cli_first(self, monkeypatch):
        monkeypatch.delenv("FABRIC_ACCESS_TOKEN", raising=False)
        mock_token = MagicMock()
        with patch("fabric_iq.auth.AzureCliCredential") as mock_cli:
            mock_cli.return_value.get_token.return_value = mock_token
            cred = get_credential(prefer_cli=True)
        assert cred is mock_cli.return_value

    def test_prefer_cli_falls_through_on_failure(self, monkeypatch):
        monkeypatch.delenv("FABRIC_ACCESS_TOKEN", raising=False)
        with (
            patch("fabric_iq.auth.AzureCliCredential") as mock_cli,
            patch("fabric_iq.auth.AzurePowerShellCredential") as mock_ps,
            patch("fabric_iq.auth.DefaultAzureCredential") as mock_default,
        ):
            mock_cli.return_value.get_token.side_effect = Exception("cli unavailable")
            mock_ps.return_value.get_token.side_effect = Exception("ps unavailable")
            get_credential(prefer_cli=True)
        mock_default.assert_called_once()

    def test_powershell_credential_used_when_available(self, monkeypatch):
        monkeypatch.delenv("FABRIC_ACCESS_TOKEN", raising=False)
        mock_token = MagicMock()
        with (
            patch("fabric_iq.auth.AzurePowerShellCredential") as mock_ps,
            patch("fabric_iq.auth.DefaultAzureCredential") as mock_default,
        ):
            mock_ps.return_value.get_token.return_value = mock_token
            cred = get_credential(prefer_cli=False)
        assert cred is mock_ps.return_value
        mock_default.assert_not_called()


class TestGetAccessToken:
    def test_uses_provided_credential(self):
        mock_cred = MagicMock()
        mock_cred.get_token.return_value = MagicMock(token="bearer-token", expires_on=9999999999)
        token = get_access_token(mock_cred)
        assert token == "bearer-token"

    def test_calls_get_credential_when_none(self, monkeypatch):
        monkeypatch.setenv("FABRIC_ACCESS_TOKEN", "env-token")
        token = get_access_token()
        assert token == "env-token"


class TestGetAuthHeaders:
    def test_returns_authorization_header(self):
        mock_cred = MagicMock()
        mock_cred.get_token.return_value = MagicMock(token="my-token", expires_on=9999999999)
        headers = get_auth_headers(mock_cred)
        assert headers["Authorization"] == "Bearer my-token"
        assert headers["Content-Type"] == "application/json"

    def test_header_format(self):
        mock_cred = MagicMock()
        mock_cred.get_token.return_value = MagicMock(token="tok", expires_on=9999999999)
        headers = get_auth_headers(mock_cred)
        assert headers["Authorization"].startswith("Bearer ")
