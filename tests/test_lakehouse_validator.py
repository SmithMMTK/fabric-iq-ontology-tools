"""Tests for lakehouse_validator module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fabric_iq.lakehouse_validator import (
    LakehouseColumnInfo,
    LakehouseValidationReport,
    ValidationResult,
    get_lakehouse_metadata,
    log_validation_report,
    validate_lakehouse_types,
)
from fabric_iq.models import Column, Table


# ---------------------------------------------------------------------------
# LakehouseColumnInfo tests
# ---------------------------------------------------------------------------

class TestLakehouseColumnInfo:
    def test_display_type_decimal(self):
        info = LakehouseColumnInfo("T", "C", "decimal", 18, 0)
        assert info.display_type == "decimal(18,0)"

    def test_display_type_numeric_with_scale(self):
        info = LakehouseColumnInfo("T", "C", "numeric", 10, 2)
        assert info.display_type == "numeric(10,2)"

    def test_display_type_non_numeric(self):
        info = LakehouseColumnInfo("T", "C", "bigint", None, None)
        assert info.display_type == "bigint"

    def test_is_unsupported_decimal(self):
        info = LakehouseColumnInfo("T", "C", "decimal", 18, 0)
        assert info.is_unsupported is True

    def test_is_unsupported_money(self):
        info = LakehouseColumnInfo("T", "C", "money", None, None)
        assert info.is_unsupported is True

    def test_is_supported_int(self):
        info = LakehouseColumnInfo("T", "C", "int", None, None)
        assert info.is_unsupported is False

    def test_is_supported_bigint(self):
        info = LakehouseColumnInfo("T", "C", "bigint", None, None)
        assert info.is_unsupported is False


# ---------------------------------------------------------------------------
# ValidationResult / Report tests
# ---------------------------------------------------------------------------

class TestLakehouseValidationReport:
    def test_empty_report(self):
        report = LakehouseValidationReport(connected=True)
        assert report.has_issues is False
        assert report.unsupported_columns == []
        assert report.mismatched_columns == []

    def test_unsupported_columns(self):
        report = LakehouseValidationReport(
            connected=True,
            results=[
                ValidationResult("Trip", "FareAmount", "int64", "decimal(18,0)", True, True),
                ValidationResult("Trip", "TripID", "int64", "bigint", False, False),
            ],
        )
        assert report.has_issues is True
        assert len(report.unsupported_columns) == 1
        assert report.unsupported_columns[0].column_name == "FareAmount"

    def test_mismatched_columns(self):
        report = LakehouseValidationReport(
            connected=True,
            results=[
                ValidationResult("Trip", "TaxAmount", "int64", "decimal(18,0)", True, True),
                ValidationResult("Trip", "FareAmount", "double", "float", False, False),
            ],
        )
        assert len(report.mismatched_columns) == 1  # only the first is a mismatch
        assert report.mismatched_columns[0].column_name == "TaxAmount"


# ---------------------------------------------------------------------------
# get_lakehouse_metadata tests
# ---------------------------------------------------------------------------

class TestGetLakehouseMetadata:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "displayName": "MyLakehouse",
            "properties": {
                "sqlEndpointProperties": {
                    "connectionString": "server.database.fabric.microsoft.com"
                }
            },
        }
        mock_resp.raise_for_status = MagicMock()
        client = MagicMock()
        client.get.return_value = mock_resp

        sql_server, db = get_lakehouse_metadata(client, "ws-id", "lh-id")
        assert sql_server == "server.database.fabric.microsoft.com"
        assert db == "MyLakehouse"
        client.get.assert_called_once_with("workspaces/ws-id/lakehouses/lh-id")

    def test_missing_connection_string(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "displayName": "MyLakehouse",
            "properties": {"sqlEndpointProperties": {"connectionString": ""}},
        }
        mock_resp.raise_for_status = MagicMock()
        client = MagicMock()
        client.get.return_value = mock_resp

        with pytest.raises(RuntimeError, match="no SQL endpoint connection string"):
            get_lakehouse_metadata(client, "ws-id", "lh-id")

    def test_missing_display_name(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "displayName": "",
            "properties": {
                "sqlEndpointProperties": {"connectionString": "server.fabric.com"}
            },
        }
        mock_resp.raise_for_status = MagicMock()
        client = MagicMock()
        client.get.return_value = mock_resp

        with pytest.raises(RuntimeError, match="no displayName"):
            get_lakehouse_metadata(client, "ws-id", "lh-id")


# ---------------------------------------------------------------------------
# validate_lakehouse_types tests (mocked SQL)
# ---------------------------------------------------------------------------

def _make_tables() -> dict[str, Table]:
    """Build a minimal tables dict for testing."""
    return {
        "Trip": Table(
            name="Trip",
            schema="dbo",
            columns=[
                Column("TripID", "int64", "BigInt", "none"),
                Column("FareAmount", "int64", "BigInt", "sum"),
                Column("TipAmount", "int64", "BigInt", "sum"),
                Column("City", "string", "String", "none"),
            ],
            pk_column_names=["TripID"],
        ),
    }


class TestValidateLakehouseTypes:
    @patch("fabric_iq.lakehouse_validator._check_pyodbc", return_value=False)
    def test_skips_when_no_pyodbc(self, mock_check):
        client = MagicMock()
        cred = MagicMock()
        tables = _make_tables()

        report = validate_lakehouse_types(client, cred, "ws", "lh", tables)
        assert report.connected is False
        assert "pyodbc" in report.error_message

    @patch("fabric_iq.lakehouse_validator._check_pyodbc", return_value=True)
    @patch("fabric_iq.lakehouse_validator.query_lakehouse_columns")
    @patch("fabric_iq.lakehouse_validator.get_lakehouse_metadata")
    def test_detects_unsupported_decimal(self, mock_meta, mock_query, mock_check):
        mock_meta.return_value = ("server.fabric.com", "MyDB")
        mock_query.return_value = {
            "Trip": [
                LakehouseColumnInfo("Trip", "TripID", "bigint", None, None),
                LakehouseColumnInfo("Trip", "FareAmount", "decimal", 18, 0),
                LakehouseColumnInfo("Trip", "TipAmount", "decimal", 18, 0),
                LakehouseColumnInfo("Trip", "City", "nvarchar", None, None),
            ],
        }

        client = MagicMock()
        cred = MagicMock()
        tables = _make_tables()

        report = validate_lakehouse_types(client, cred, "ws", "lh", tables)
        assert report.connected is True
        assert len(report.unsupported_columns) == 2
        names = {r.column_name for r in report.unsupported_columns}
        assert names == {"FareAmount", "TipAmount"}

    @patch("fabric_iq.lakehouse_validator._check_pyodbc", return_value=True)
    @patch("fabric_iq.lakehouse_validator.query_lakehouse_columns")
    @patch("fabric_iq.lakehouse_validator.get_lakehouse_metadata")
    def test_detects_type_mismatch(self, mock_meta, mock_query, mock_check):
        mock_meta.return_value = ("server.fabric.com", "MyDB")
        mock_query.return_value = {
            "Trip": [
                LakehouseColumnInfo("Trip", "TripID", "bigint", None, None),
                LakehouseColumnInfo("Trip", "FareAmount", "decimal", 18, 0),
                LakehouseColumnInfo("Trip", "TipAmount", "bigint", None, None),
                LakehouseColumnInfo("Trip", "City", "nvarchar", None, None),
            ],
        }

        client = MagicMock()
        cred = MagicMock()
        tables = _make_tables()

        report = validate_lakehouse_types(client, cred, "ws", "lh", tables)
        # FareAmount: SM says int64 but lakehouse is decimal → mismatch + unsupported
        mismatched = report.mismatched_columns
        assert len(mismatched) == 1
        assert mismatched[0].column_name == "FareAmount"
        assert mismatched[0].is_mismatch is True

    @patch("fabric_iq.lakehouse_validator._check_pyodbc", return_value=True)
    @patch("fabric_iq.lakehouse_validator.query_lakehouse_columns")
    @patch("fabric_iq.lakehouse_validator.get_lakehouse_metadata")
    def test_all_columns_ok(self, mock_meta, mock_query, mock_check):
        mock_meta.return_value = ("server.fabric.com", "MyDB")
        mock_query.return_value = {
            "Trip": [
                LakehouseColumnInfo("Trip", "TripID", "bigint", None, None),
                LakehouseColumnInfo("Trip", "FareAmount", "bigint", None, None),
                LakehouseColumnInfo("Trip", "TipAmount", "bigint", None, None),
                LakehouseColumnInfo("Trip", "City", "nvarchar", None, None),
            ],
        }

        client = MagicMock()
        cred = MagicMock()
        tables = _make_tables()

        report = validate_lakehouse_types(client, cred, "ws", "lh", tables)
        assert report.connected is True
        assert report.has_issues is False

    @patch("fabric_iq.lakehouse_validator._check_pyodbc", return_value=True)
    @patch("fabric_iq.lakehouse_validator.get_lakehouse_metadata")
    def test_handles_connection_error(self, mock_meta, mock_check):
        mock_meta.side_effect = RuntimeError("No SQL endpoint")

        client = MagicMock()
        cred = MagicMock()
        tables = _make_tables()

        report = validate_lakehouse_types(client, cred, "ws", "lh", tables)
        assert report.connected is False
        assert "No SQL endpoint" in report.error_message


# ---------------------------------------------------------------------------
# log_validation_report tests
# ---------------------------------------------------------------------------

class TestLogValidationReport:
    def test_log_no_connection(self, caplog):
        report = LakehouseValidationReport(connected=False, error_message="no pyodbc")
        with caplog.at_level("WARNING"):
            log_validation_report(report)
        assert "no pyodbc" in caplog.text

    def test_log_all_ok(self, caplog):
        report = LakehouseValidationReport(connected=True)
        with caplog.at_level("INFO"):
            log_validation_report(report)
        assert "all columns OK" in caplog.text

    def test_log_unsupported(self, caplog):
        report = LakehouseValidationReport(
            connected=True,
            results=[
                ValidationResult("Trip", "FareAmount", "int64", "decimal(18,0)", True, True),
            ],
        )
        with caplog.at_level("WARNING"):
            log_validation_report(report)
        assert "UNSUPPORTED" in caplog.text
        assert "FareAmount" in caplog.text
