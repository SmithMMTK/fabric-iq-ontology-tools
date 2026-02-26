"""Lakehouse column-type validation via SQL endpoint.

Connects to the lakehouse SQL analytics endpoint and queries
``INFORMATION_SCHEMA.COLUMNS`` to discover actual delta-table column types.
Compares them against Semantic Model (TMDL) declared types to detect
mismatches — most importantly ``decimal`` columns, which Fabric Graph
cannot read (returns null).

This module is **optional**.  If ``pyodbc`` or the ODBC Driver 18 for SQL
Server is not installed, all public functions degrade gracefully and log a
warning instead of raising.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field

from fabric_iq.models import Table

logger = logging.getLogger(__name__)

# Unsupported lakehouse types for Fabric Graph
# See: https://learn.microsoft.com/en-us/fabric/iq/ontology/concepts-generate#lakehouse-tables
UNSUPPORTED_LAKEHOUSE_TYPES = {"decimal", "numeric", "money", "smallmoney"}

# Mapping from lakehouse SQL types to TMDL-equivalent types (for display)
_SQL_TO_TMDL_HINT: dict[str, str] = {
    "bigint": "int64",
    "int": "int64",
    "smallint": "int64",
    "tinyint": "int64",
    "bit": "boolean",
    "float": "double",
    "real": "double",
    "decimal": "decimal",
    "numeric": "decimal",
    "money": "decimal",
    "smallmoney": "decimal",
    "varchar": "string",
    "nvarchar": "string",
    "char": "string",
    "nchar": "string",
    "text": "string",
    "ntext": "string",
    "date": "dateTime",
    "datetime": "dateTime",
    "datetime2": "dateTime",
    "smalldatetime": "dateTime",
    "datetimeoffset": "dateTime",
    "time": "string",
    "binary": "binary",
    "varbinary": "binary",
    "image": "binary",
    "uniqueidentifier": "string",
}


@dataclass
class LakehouseColumnInfo:
    """Actual column metadata from the lakehouse SQL endpoint."""

    table_name: str
    column_name: str
    data_type: str  # SQL type, e.g. "decimal", "int", "bigint"
    numeric_precision: int | None = None
    numeric_scale: int | None = None

    @property
    def display_type(self) -> str:
        """Human-readable type string (e.g. ``decimal(18,0)``)."""
        if self.numeric_precision is not None and self.data_type in (
            "decimal",
            "numeric",
            "money",
            "smallmoney",
        ):
            return f"{self.data_type}({self.numeric_precision},{self.numeric_scale or 0})"
        return self.data_type

    @property
    def is_unsupported(self) -> bool:
        """True if Fabric Graph cannot read this column type."""
        return self.data_type.lower() in UNSUPPORTED_LAKEHOUSE_TYPES


@dataclass
class ValidationResult:
    """Result of cross-checking SM types against lakehouse types."""

    table_name: str
    column_name: str
    tmdl_type: str  # Type declared in TMDL / Semantic Model
    lakehouse_type: str  # Actual type in lakehouse delta table
    is_unsupported: bool  # True if lakehouse type is unsupported by Graph
    is_mismatch: bool  # True if TMDL type doesn't match lakehouse reality


@dataclass
class LakehouseValidationReport:
    """Aggregate validation report."""

    connected: bool = False
    error_message: str = ""
    results: list[ValidationResult] = field(default_factory=list)

    @property
    def unsupported_columns(self) -> list[ValidationResult]:
        return [r for r in self.results if r.is_unsupported]

    @property
    def mismatched_columns(self) -> list[ValidationResult]:
        return [r for r in self.results if r.is_mismatch]

    @property
    def has_issues(self) -> bool:
        return bool(self.unsupported_columns or self.mismatched_columns)


def _check_pyodbc() -> bool:
    """Return True if pyodbc is importable."""
    try:
        import pyodbc  # noqa: F401

        return True
    except ImportError:
        return False


def get_lakehouse_metadata(client, workspace_id: str, lakehouse_id: str) -> tuple[str, str]:
    """Fetch the SQL endpoint connection string and database name for a lakehouse.

    Parameters
    ----------
    client:
        An authenticated :class:`FabricClient`.
    workspace_id:
        The workspace GUID.
    lakehouse_id:
        The lakehouse GUID.

    Returns
    -------
    tuple[str, str]
        ``(sql_connection_string, database_name)``

    Raises
    ------
    RuntimeError
        If the lakehouse metadata cannot be retrieved or lacks a SQL endpoint.
    """
    resp = client.get(f"workspaces/{workspace_id}/lakehouses/{lakehouse_id}")
    resp.raise_for_status()
    data = resp.json()

    sql_props = data.get("properties", {}).get("sqlEndpointProperties", {})
    connection_string = sql_props.get("connectionString", "")
    db_name = data.get("displayName", "")

    if not connection_string:
        raise RuntimeError(
            f"Lakehouse {lakehouse_id} has no SQL endpoint connection string. "
            "Ensure the lakehouse has a SQL analytics endpoint provisioned."
        )
    if not db_name:
        raise RuntimeError(
            f"Lakehouse {lakehouse_id} has no displayName in metadata."
        )

    return connection_string, db_name


def _connect_sql(sql_server: str, database: str, credential) -> "pyodbc.Connection":
    """Open a pyodbc connection to the lakehouse SQL endpoint using Azure token auth.

    Parameters
    ----------
    sql_server:
        The SQL endpoint hostname.
    database:
        The database (lakehouse display) name.
    credential:
        An Azure ``TokenCredential`` (e.g. ``AzureCliCredential``).

    Returns
    -------
    pyodbc.Connection
    """
    import pyodbc

    SQL_RESOURCE = "https://database.windows.net/.default"
    token = credential.get_token(SQL_RESOURCE).token
    token_bytes = token.encode("UTF-16-LE")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={sql_server};"
        f"DATABASE={database};"
        "Encrypt=Yes;"
        "TrustServerCertificate=No;"
        "Connection Timeout=30;"
    )

    conn = pyodbc.connect(conn_str, attrs_before={1256: token_struct})
    return conn


def query_lakehouse_columns(
    sql_server: str,
    database: str,
    credential,
    table_names: list[str],
) -> dict[str, list[LakehouseColumnInfo]]:
    """Query INFORMATION_SCHEMA.COLUMNS for the specified tables.

    Parameters
    ----------
    sql_server:
        The SQL endpoint hostname.
    database:
        The database (lakehouse display) name.
    credential:
        An Azure ``TokenCredential``.
    table_names:
        List of table names to query.

    Returns
    -------
    dict[str, list[LakehouseColumnInfo]]
        Mapping of table name → list of column info.
    """
    import pyodbc  # noqa: F811

    conn = _connect_sql(sql_server, database, credential)
    try:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in table_names)
        query = (
            f"SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, "
            f"NUMERIC_PRECISION, NUMERIC_SCALE "
            f"FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_NAME IN ({placeholders}) "
            f"ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        cursor.execute(query, table_names)

        result: dict[str, list[LakehouseColumnInfo]] = {}
        for row in cursor.fetchall():
            info = LakehouseColumnInfo(
                table_name=row[0],
                column_name=row[1],
                data_type=row[2],
                numeric_precision=row[3],
                numeric_scale=row[4],
            )
            result.setdefault(info.table_name, []).append(info)
        return result
    finally:
        conn.close()


def validate_lakehouse_types(
    client,
    credential,
    workspace_id: str,
    lakehouse_id: str,
    tables: dict[str, Table],
) -> LakehouseValidationReport:
    """Cross-check SM-declared column types against actual lakehouse delta-table types.

    Parameters
    ----------
    client:
        An authenticated :class:`FabricClient`.
    credential:
        An Azure ``TokenCredential`` for SQL endpoint access.
    workspace_id:
        Workspace GUID.
    lakehouse_id:
        Lakehouse GUID.
    tables:
        Parsed TMDL tables (from ``parse_semantic_model``).

    Returns
    -------
    LakehouseValidationReport
        Report with any mismatches or unsupported types detected.
    """
    report = LakehouseValidationReport()

    if not _check_pyodbc():
        report.error_message = (
            "pyodbc is not installed — skipping lakehouse type verification. "
            "Install with: pip install pyodbc"
        )
        logger.warning(report.error_message)
        return report

    try:
        sql_server, db_name = get_lakehouse_metadata(client, workspace_id, lakehouse_id)
        logger.info("  SQL endpoint: %s  |  Database: %s", sql_server, db_name)

        table_names = list(tables.keys())
        lh_columns = query_lakehouse_columns(sql_server, db_name, credential, table_names)
        report.connected = True

        # Build a lookup: (table, col) → LakehouseColumnInfo
        lh_lookup: dict[tuple[str, str], LakehouseColumnInfo] = {}
        for tname, cols in lh_columns.items():
            for col_info in cols:
                lh_lookup[(tname, col_info.column_name)] = col_info

        # Cross-check each SM column
        for tname, table in tables.items():
            for col in table.columns:
                lh_info = lh_lookup.get((tname, col.name))
                if lh_info is None:
                    # Column exists in SM but not in lakehouse — skip
                    continue

                # Determine what TMDL type the lakehouse type maps to
                expected_tmdl = _SQL_TO_TMDL_HINT.get(lh_info.data_type.lower(), "")
                is_mismatch = (
                    expected_tmdl != ""
                    and expected_tmdl != col.data_type
                )

                result = ValidationResult(
                    table_name=tname,
                    column_name=col.name,
                    tmdl_type=col.data_type,
                    lakehouse_type=lh_info.display_type,
                    is_unsupported=lh_info.is_unsupported,
                    is_mismatch=is_mismatch,
                )
                report.results.append(result)

    except Exception as exc:
        report.error_message = f"Lakehouse validation failed: {exc}"
        logger.warning(report.error_message)

    return report


def log_validation_report(report: LakehouseValidationReport) -> None:
    """Log a human-readable summary of the validation report."""
    if not report.connected:
        if report.error_message:
            logger.warning("  %s", report.error_message)
        return

    if not report.has_issues:
        logger.info("  Lakehouse type verification: all columns OK")
        return

    # Unsupported columns (e.g. decimal in lakehouse)
    unsupported = report.unsupported_columns
    if unsupported:
        logger.warning("  ⚠ UNSUPPORTED lakehouse types detected (%d columns):", len(unsupported))
        logger.warning(
            "    Fabric Graph cannot read these types — queries will return null."
        )
        for r in unsupported:
            logger.warning(
                "    %-30s %-20s lakehouse: %-18s  SM: %s",
                r.table_name,
                r.column_name,
                r.lakehouse_type,
                r.tmdl_type,
            )
        logger.warning(
            "    Fix: cast columns to supported types (e.g. INT, BIGINT, FLOAT) in the lakehouse, "
            "or use --exclude-decimal to remove them from the ontology."
        )

    # Type mismatches (SM says int64 but lakehouse has decimal, etc.)
    mismatches = [m for m in report.mismatched_columns if not m.is_unsupported]
    if mismatches:
        logger.warning("  ⚠ Type mismatches between SM and lakehouse (%d columns):", len(mismatches))
        for r in mismatches:
            logger.warning(
                "    %-30s %-20s SM: %-10s  lakehouse: %s",
                r.table_name,
                r.column_name,
                r.tmdl_type,
                r.lakehouse_type,
            )
