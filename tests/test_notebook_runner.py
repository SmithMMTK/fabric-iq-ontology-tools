"""Tests for notebook_runner module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest

from fabric_iq.notebook_runner import (
    DecimalFixResult,
    _generate_notebook_content,
    _create_notebook_item,
    _upload_notebook_definition,
    fix_decimal_columns,
    detect_decimal_columns,
)
from fabric_iq.lakehouse_validator import LakehouseColumnInfo


# ---------------------------------------------------------------------------
# DecimalFixResult tests
# ---------------------------------------------------------------------------

class TestDecimalFixResult:
    def test_success(self):
        r = DecimalFixResult(
            tables_fixed=["Trip"],
            columns_fixed={"Trip": ["FareAmount", "TipAmount"]},
            job_status="Completed",
        )
        assert r.success is True
        assert r.total_columns_fixed == 2

    def test_failure(self):
        r = DecimalFixResult(
            job_status="Failed",
            error_message="Notebook job failed",
        )
        assert r.success is False

    def test_empty(self):
        r = DecimalFixResult(job_status="Completed")
        assert r.success is True
        assert r.total_columns_fixed == 0


# ---------------------------------------------------------------------------
# _generate_notebook_content tests
# ---------------------------------------------------------------------------

class TestGenerateNotebookContent:
    def test_structure(self):
        cols = {
            "Trip": [
                LakehouseColumnInfo("Trip", "FareAmount", "decimal", 18, 0),
                LakehouseColumnInfo("Trip", "TipAmount", "decimal", 18, 0),
            ],
        }
        nb = _generate_notebook_content(cols, target_type="double")

        assert nb["nbformat"] == 4
        assert nb["metadata"]["kernelspec"]["name"] == "synapse_pyspark"
        assert len(nb["cells"]) == 2  # fix cell + verify cell
        assert nb["cells"][0]["cell_type"] == "code"

    def test_contains_table_and_column_names(self):
        cols = {
            "Payment": [
                LakehouseColumnInfo("Payment", "Amount", "decimal", 10, 2),
            ],
        }
        nb = _generate_notebook_content(cols, target_type="double")
        fix_source = "".join(nb["cells"][0]["source"])
        assert "Payment" in fix_source
        assert "Amount" in fix_source
        assert "double" in fix_source

    def test_lakehouse_metadata_embedded(self):
        cols = {
            "T": [LakehouseColumnInfo("T", "C", "decimal", 18, 0)],
        }
        nb = _generate_notebook_content(
            cols,
            workspace_id="ws-123",
            lakehouse_id="lh-456",
            lakehouse_name="MyLH",
        )
        trident = nb["metadata"]["trident"]
        assert trident["lakehouse"]["default_lakehouse"] == "lh-456"
        assert trident["lakehouse"]["default_lakehouse_name"] == "MyLH"
        assert trident["lakehouse"]["default_lakehouse_workspace_id"] == "ws-123"

    def test_no_lakehouse_metadata_when_empty(self):
        cols = {"T": [LakehouseColumnInfo("T", "C", "decimal", 18, 0)]}
        nb = _generate_notebook_content(cols)
        assert "trident" not in nb["metadata"]


# ---------------------------------------------------------------------------
# _create_notebook_item tests
# ---------------------------------------------------------------------------

class TestCreateNotebookItem:
    def test_returns_id(self):
        client = MagicMock()
        client.post_with_lro.return_value = {"id": "nb-123", "displayName": "test"}

        nb_id = _create_notebook_item(client, "ws-id", "test_nb")
        assert nb_id == "nb-123"
        client.post_with_lro.assert_called_once()
        call_args = client.post_with_lro.call_args
        assert "workspaces/ws-id/items" in call_args[0][0]

    def test_raises_on_no_id(self):
        client = MagicMock()
        client.post_with_lro.return_value = {}

        with pytest.raises(Exception):
            _create_notebook_item(client, "ws-id", "test_nb")


# ---------------------------------------------------------------------------
# _upload_notebook_definition tests
# ---------------------------------------------------------------------------

class TestUploadNotebookDefinition:
    def test_encodes_and_uploads(self):
        client = MagicMock()
        nb_json = {"nbformat": 4, "cells": []}

        _upload_notebook_definition(client, "ws-id", "nb-123", nb_json)

        client.post_with_lro.assert_called_once()
        call_args = client.post_with_lro.call_args
        path = call_args[0][0]
        body = call_args[1]["json"]

        assert "ws-id" in path
        assert "nb-123" in path
        assert "updateDefinition" in path
        assert body["definition"]["format"] == "ipynb"
        assert len(body["definition"]["parts"]) == 1
        assert body["definition"]["parts"][0]["path"] == "notebook-content.ipynb"

        # Verify payload is valid base64 of the notebook JSON
        import base64
        decoded = json.loads(
            base64.b64decode(body["definition"]["parts"][0]["payload"])
        )
        assert decoded["nbformat"] == 4


# ---------------------------------------------------------------------------
# detect_decimal_columns tests
# ---------------------------------------------------------------------------

class TestDetectDecimalColumns:
    @patch("fabric_iq.notebook_runner.query_lakehouse_columns")
    @patch("fabric_iq.notebook_runner.get_lakehouse_metadata")
    def test_filters_to_decimal_only(self, mock_meta, mock_query):
        mock_meta.return_value = ("server.fabric.com", "MyDB")
        mock_query.return_value = {
            "Trip": [
                LakehouseColumnInfo("Trip", "TripID", "bigint", None, None),
                LakehouseColumnInfo("Trip", "FareAmount", "decimal", 18, 0),
                LakehouseColumnInfo("Trip", "City", "nvarchar", None, None),
            ],
        }

        client = MagicMock()
        cred = MagicMock()

        result = detect_decimal_columns(client, cred, "ws", "lh", ["Trip"])
        assert "Trip" in result
        assert len(result["Trip"]) == 1
        assert result["Trip"][0].column_name == "FareAmount"

    @patch("fabric_iq.notebook_runner.query_lakehouse_columns")
    @patch("fabric_iq.notebook_runner.get_lakehouse_metadata")
    def test_empty_when_no_decimals(self, mock_meta, mock_query):
        mock_meta.return_value = ("server.fabric.com", "MyDB")
        mock_query.return_value = {
            "Trip": [
                LakehouseColumnInfo("Trip", "TripID", "bigint", None, None),
                LakehouseColumnInfo("Trip", "City", "nvarchar", None, None),
            ],
        }

        client = MagicMock()
        cred = MagicMock()

        result = detect_decimal_columns(client, cred, "ws", "lh", ["Trip"])
        assert result == {}


# ---------------------------------------------------------------------------
# fix_decimal_columns integration tests (all API calls mocked)
# ---------------------------------------------------------------------------

class TestFixDecimalColumns:
    @patch("fabric_iq.notebook_runner._delete_notebook")
    @patch("fabric_iq.notebook_runner._run_notebook", return_value="Completed")
    @patch("fabric_iq.notebook_runner._upload_notebook_definition")
    @patch("fabric_iq.notebook_runner._create_notebook_item", return_value="nb-999")
    @patch("fabric_iq.notebook_runner.get_lakehouse_metadata")
    @patch("fabric_iq.notebook_runner.detect_decimal_columns")
    def test_full_success_flow(
        self, mock_detect, mock_meta, mock_create, mock_upload, mock_run, mock_delete
    ):
        mock_detect.return_value = {
            "Trip": [LakehouseColumnInfo("Trip", "FareAmount", "decimal", 18, 0)],
        }
        mock_meta.return_value = ("server.fabric.com", "MyDB")

        client = MagicMock()
        cred = MagicMock()

        result = fix_decimal_columns(client, cred, "ws-id", "lh-id")

        assert result.success is True
        assert result.tables_fixed == ["Trip"]
        assert result.columns_fixed == {"Trip": ["FareAmount"]}
        assert result.notebook_id == "nb-999"
        mock_create.assert_called_once()
        mock_upload.assert_called_once()
        mock_run.assert_called_once()
        mock_delete.assert_called_once()

    @patch("fabric_iq.notebook_runner.detect_decimal_columns")
    def test_no_decimals_found(self, mock_detect):
        mock_detect.return_value = {}

        client = MagicMock()
        cred = MagicMock()

        result = fix_decimal_columns(client, cred, "ws-id", "lh-id")
        assert result.success is True
        assert result.total_columns_fixed == 0

    @patch("fabric_iq.notebook_runner._delete_notebook")
    @patch("fabric_iq.notebook_runner._run_notebook", return_value="Failed")
    @patch("fabric_iq.notebook_runner._upload_notebook_definition")
    @patch("fabric_iq.notebook_runner._create_notebook_item", return_value="nb-999")
    @patch("fabric_iq.notebook_runner.get_lakehouse_metadata")
    @patch("fabric_iq.notebook_runner.detect_decimal_columns")
    def test_notebook_failure(
        self, mock_detect, mock_meta, mock_create, mock_upload, mock_run, mock_delete
    ):
        mock_detect.return_value = {
            "Trip": [LakehouseColumnInfo("Trip", "Amt", "decimal", 18, 0)],
        }
        mock_meta.return_value = ("server.fabric.com", "MyDB")

        client = MagicMock()
        cred = MagicMock()

        result = fix_decimal_columns(client, cred, "ws-id", "lh-id")
        assert result.success is False
        assert "Failed" in result.error_message

    @patch("fabric_iq.notebook_runner._delete_notebook")
    @patch("fabric_iq.notebook_runner._run_notebook", return_value="Completed")
    @patch("fabric_iq.notebook_runner._upload_notebook_definition")
    @patch("fabric_iq.notebook_runner._create_notebook_item", return_value="nb-999")
    @patch("fabric_iq.notebook_runner.get_lakehouse_metadata")
    @patch("fabric_iq.notebook_runner.detect_decimal_columns")
    def test_no_cleanup_when_disabled(
        self, mock_detect, mock_meta, mock_create, mock_upload, mock_run, mock_delete
    ):
        mock_detect.return_value = {
            "Trip": [LakehouseColumnInfo("Trip", "Amt", "decimal", 18, 0)],
        }
        mock_meta.return_value = ("server.fabric.com", "MyDB")

        client = MagicMock()
        cred = MagicMock()

        result = fix_decimal_columns(client, cred, "ws-id", "lh-id", cleanup=False)
        assert result.success is True
        mock_delete.assert_not_called()

    @patch("fabric_iq.notebook_runner.detect_decimal_columns")
    def test_detection_error(self, mock_detect):
        mock_detect.side_effect = RuntimeError("No SQL endpoint")

        client = MagicMock()
        cred = MagicMock()

        result = fix_decimal_columns(client, cred, "ws-id", "lh-id")
        assert result.success is False
        assert "No SQL endpoint" in result.error_message
