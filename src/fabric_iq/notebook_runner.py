"""Run a PySpark notebook in Fabric to fix decimal columns in lakehouse tables.

Creates a temporary Fabric notebook item, executes it against the target
lakehouse, waits for completion, and optionally cleans up afterwards.

Uses the Fabric REST API:
  - ``POST /v1/workspaces/{ws}/items`` – create notebook
  - ``POST /v1/workspaces/{ws}/items/{id}/updateDefinition`` – upload code
  - ``POST /v1/workspaces/{ws}/items/{id}/jobs/instances?jobType=RunNotebook`` – execute
  - ``GET  /v1/workspaces/{ws}/items/{id}/jobs/instances/{jid}`` – poll status
  - ``DELETE /v1/workspaces/{ws}/items/{id}`` – cleanup
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field

from fabric_iq.api_client import FabricClient, FabricApiError
from fabric_iq.lakehouse_validator import (
    LakehouseColumnInfo,
    get_lakehouse_metadata,
    query_lakehouse_columns,
    UNSUPPORTED_LAKEHOUSE_TYPES,
)

logger = logging.getLogger(__name__)

# Default polling interval and timeout for notebook jobs
_JOB_POLL_SECONDS = 10
_JOB_MAX_WAIT_SECONDS = 600  # 10 minutes


@dataclass
class DecimalFixResult:
    """Result of the fix-decimals notebook run."""

    tables_fixed: list[str] = field(default_factory=list)
    columns_fixed: dict[str, list[str]] = field(default_factory=dict)  # table → [cols]
    notebook_id: str = ""
    job_status: str = ""
    error_message: str = ""

    @property
    def success(self) -> bool:
        return self.job_status == "Completed" and not self.error_message

    @property
    def total_columns_fixed(self) -> int:
        return sum(len(cols) for cols in self.columns_fixed.values())


def detect_decimal_columns(
    client: FabricClient,
    credential,
    workspace_id: str,
    lakehouse_id: str,
    table_names: list[str] | None = None,
) -> dict[str, list[LakehouseColumnInfo]]:
    """Query the lakehouse SQL endpoint and return only the decimal columns.

    Parameters
    ----------
    client:
        Authenticated Fabric client.
    credential:
        Azure credential for SQL endpoint auth.
    workspace_id:
        Workspace GUID.
    lakehouse_id:
        Lakehouse GUID.
    table_names:
        Specific table names to check.  If None, queries all tables.

    Returns
    -------
    dict[str, list[LakehouseColumnInfo]]
        Mapping of table name → list of decimal/unsupported columns.
    """
    sql_server, db_name = get_lakehouse_metadata(client, workspace_id, lakehouse_id)

    if table_names is None:
        # Query all user tables
        import pyodbc
        import struct

        SQL_RESOURCE = "https://database.windows.net/.default"
        token = credential.get_token(SQL_RESOURCE).token
        token_bytes = token.encode("UTF-16-LE")
        token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
        conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={sql_server};"
            f"DATABASE={db_name};"
            "Encrypt=Yes;TrustServerCertificate=No;Connection Timeout=30;"
        )
        conn = pyodbc.connect(conn_str, attrs_before={1256: token_struct})
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE'"
            )
            table_names = [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    if not table_names:
        return {}

    all_columns = query_lakehouse_columns(sql_server, db_name, credential, table_names)

    # Filter to only unsupported (decimal) columns
    result: dict[str, list[LakehouseColumnInfo]] = {}
    for tname, cols in all_columns.items():
        decimal_cols = [c for c in cols if c.data_type.lower() in UNSUPPORTED_LAKEHOUSE_TYPES]
        if decimal_cols:
            result[tname] = decimal_cols
    return result


def _generate_notebook_content(
    decimal_columns: dict[str, list[LakehouseColumnInfo]],
    target_type: str = "double",
    workspace_id: str = "",
    lakehouse_id: str = "",
    lakehouse_name: str = "",
) -> dict:
    """Generate an .ipynb JSON with PySpark code to cast decimal columns.

    Returns a notebook dict (JSON-serializable) with lakehouse metadata
    embedded so Fabric attaches it automatically.
    """
    # Build the PySpark code for the fix — use CTAS + DROP + RENAME approach
    fix_lines = [
        "# Cast decimal columns using CREATE TABLE AS SELECT, then swap.\n",
        "# This avoids in-place overwrites that can fail with 403 / lock issues.\n",
        "import time as _time\n",
        "\n",
        "fixes = {\n",
    ]
    for tname, cols in decimal_columns.items():
        col_names = [c.column_name for c in cols]
        fix_lines.append(f"    {tname!r}: {col_names!r},\n")
    fix_lines.append("}\n")
    fix_lines.append(f"TARGET_TYPE = {target_type!r}\n")
    fix_lines.append("\n")
    fix_lines.extend([
        "for tname, col_names in fixes.items():\n",
        "    print(f'Fixing {tname} ...')\n",
        "    df = spark.table(tname)\n",
        "    all_cols = df.columns\n",
        "    select_parts = []\n",
        "    for c in all_cols:\n",
        "        if c in col_names:\n",
        "            select_parts.append(f'CAST(`{c}` AS {TARGET_TYPE}) AS `{c}`')\n",
        "        else:\n",
        "            select_parts.append(f'`{c}`')\n",
        "    select_sql = ', '.join(select_parts)\n",
        "    temp = f'{tname}__fix_{int(_time.time())}'\n",
        "    # Clean up any leftover temp tables from previous runs (drop table + remove files)\n",
        "    for t in spark.catalog.listTables():\n",
        "        if t.name.startswith(f'{tname}__fix_') or t.name == f'{tname}__decimal_fix':\n",
        "            print(f'  Cleaning up leftover table {t.name}')\n",
        "            spark.sql(f'DROP TABLE IF EXISTS {t.name} PURGE')\n",
        "    # Also remove any orphaned physical directories\n",
        "    import subprocess\n",
        "    for pattern in [f'Tables/dbo/{tname}__decimal_fix', f'Tables/dbo/{tname}__fix_*']:\n",
        "        try:\n",
        "            items = mssparkutils.fs.ls(f'Tables/dbo/')\n",
        "            for item in items:\n",
        "                if item.name.startswith(f'{tname}__fix_') or item.name == f'{tname}__decimal_fix':\n",
        "                    print(f'  Removing orphaned dir: {item.path}')\n",
        "                    mssparkutils.fs.rm(item.path, True)\n",
        "            break\n",
        "        except Exception as e:\n",
        "            print(f'  Note: {e}')\n",
        "            break\n",
        "    print(f'  Creating {temp} with casted columns ...')\n",
        "    spark.sql(f'CREATE TABLE {temp} AS SELECT {select_sql} FROM {tname}')\n",
        "    print(f'  Dropping original {tname} ...')\n",
        "    spark.sql(f'DROP TABLE {tname} PURGE')\n",
        "    print(f'  Renaming {temp} -> {tname} ...')\n",
        "    spark.sql(f'ALTER TABLE {temp} RENAME TO {tname}')\n",
        "    print(f'  {tname} done!')\n",
        "\n",
        "print('All tables fixed!')\n",
    ])

    fix_code = fix_lines  # Fabric requires source as list of strings

    # Build verification code
    verify_lines = [
        "print('Verification:')\n",
        "all_ok = True\n",
        "for tname in fixes.keys():\n",
        "    df = spark.table(tname)\n",
        "    remaining = [f.name for f in df.schema.fields if 'decimal' in f.dataType.simpleString().lower()]\n",
        "    if remaining:\n",
        "        print(f'  FAIL {tname}: still has decimal: {remaining}')\n",
        "        all_ok = False\n",
        "    else:\n",
        "        print(f'  OK {tname}')\n",
        "print('All OK!' if all_ok else 'Some columns still have decimal.')\n",
    ]

    verify_code = verify_lines  # Fabric requires source as list of strings

    # Assemble the notebook
    metadata = {
        "kernel_info": {"name": "synapse_pyspark"},
        "kernelspec": {
            "name": "synapse_pyspark",
            "language": "Python",
            "display_name": "Synapse PySpark",
        },
        "language_info": {"name": "python"},
    }

    # Add lakehouse attachment metadata (Fabric-specific)
    if workspace_id and lakehouse_id and lakehouse_name:
        metadata["trident"] = {
            "lakehouse": {
                "default_lakehouse": lakehouse_id,
                "default_lakehouse_name": lakehouse_name,
                "default_lakehouse_workspace_id": workspace_id,
                "known_lakehouses": [
                    {
                        "id": lakehouse_id,
                    }
                ],
            }
        }

    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": metadata,
        "cells": [
            {
                "cell_type": "code",
                "metadata": {"microsoft": {"language": "python", "language_group": "synapse_pyspark"}},
                "source": fix_code,
                "execution_count": None,
                "outputs": [],
            },
            {
                "cell_type": "code",
                "metadata": {"microsoft": {"language": "python", "language_group": "synapse_pyspark"}},
                "source": verify_code,
                "execution_count": None,
                "outputs": [],
            },
        ],
    }
    return notebook


def _create_notebook_item(
    client: FabricClient, workspace_id: str, display_name: str
) -> str:
    """Create an empty Notebook item in the Fabric workspace, return item ID."""
    body = {
        "displayName": display_name,
        "type": "Notebook",
    }
    result = client.post_with_lro(f"workspaces/{workspace_id}/items", json=body)
    if not result or "id" not in result:
        raise FabricApiError("Failed to create notebook item")
    return result["id"]


def _upload_notebook_definition(
    client: FabricClient,
    workspace_id: str,
    notebook_id: str,
    notebook_json: dict,
) -> None:
    """Upload the .ipynb content as the notebook definition."""
    payload_bytes = json.dumps(notebook_json).encode("utf-8")
    payload_b64 = base64.b64encode(payload_bytes).decode("ascii")

    body = {
        "definition": {
            "format": "ipynb",
            "parts": [
                {
                    "path": "notebook-content.ipynb",
                    "payload": payload_b64,
                    "payloadType": "InlineBase64",
                }
            ],
        }
    }
    path = f"workspaces/{workspace_id}/items/{notebook_id}/updateDefinition"
    client.post_with_lro(path, json=body)
    logger.info("  Uploaded notebook definition for %s", notebook_id)


def _run_notebook(
    client: FabricClient,
    workspace_id: str,
    notebook_id: str,
    *,
    poll_seconds: int = _JOB_POLL_SECONDS,
    max_wait_seconds: int = _JOB_MAX_WAIT_SECONDS,
) -> str:
    """Execute the notebook and return the final job status.

    Returns
    -------
    str
        Final status: ``"Completed"``, ``"Failed"``, ``"Cancelled"``, etc.
    """
    # Start the notebook job
    path = f"workspaces/{workspace_id}/items/{notebook_id}/jobs/instances?jobType=RunNotebook"
    resp = client.post(path)
    code = resp.status_code

    if code not in (200, 201, 202):
        detail = ""
        try:
            detail = resp.text
        except Exception:
            pass
        raise FabricApiError(
            f"Failed to start notebook job: HTTP {code}",
            status_code=code,
            detail=detail,
        )

    # Extract job location for polling
    location = resp.headers.get("Location") or resp.headers.get("location")
    if not location:
        # Try to build from operation-id
        op_id = resp.headers.get("x-ms-operation-id", "")
        if op_id:
            location = client._url(f"operations/{op_id}")

    if not location:
        raise FabricApiError("Notebook job started but no Location header for polling")

    logger.info("  Notebook job started — polling for completion …")

    # Poll for completion
    elapsed = 0
    while elapsed < max_wait_seconds:
        time.sleep(poll_seconds)
        elapsed += poll_seconds

        poll_resp = client.get(location.replace(client._api_base + "/", ""))
        if poll_resp.status_code != 200:
            # Try raw URL
            poll_resp = client._session.get(location)

        if poll_resp.status_code != 200:
            logger.warning("  Poll returned HTTP %d", poll_resp.status_code)
            continue

        body = poll_resp.json()
        status = body.get("status", "Unknown")
        logger.info("  Notebook job … status=%s (elapsed=%ds)", status, elapsed)

        if status in ("Completed", "Succeeded"):
            return "Completed"
        if status in ("Failed", "Cancelled", "Deduped"):
            error = body.get("failureReason", body.get("error", {}))
            logger.error("  Notebook job %s: %s", status, error)
            return status

    logger.error("  Notebook job timed out after %ds", max_wait_seconds)
    return "Timeout"


def _delete_notebook(client: FabricClient, workspace_id: str, notebook_id: str) -> None:
    """Delete the temporary notebook item."""
    try:
        resp = client.delete(f"workspaces/{workspace_id}/items/{notebook_id}")
        resp.raise_for_status()
        logger.info("  Cleaned up temporary notebook %s", notebook_id)
    except Exception as exc:
        logger.warning("  Failed to delete temporary notebook %s: %s", notebook_id, exc)


def fix_decimal_columns(
    client: FabricClient,
    credential,
    workspace_id: str,
    lakehouse_id: str,
    *,
    table_names: list[str] | None = None,
    target_type: str = "double",
    cleanup: bool = True,
) -> DecimalFixResult:
    """End-to-end: detect decimal columns → generate notebook → run in Fabric → cleanup.

    Parameters
    ----------
    client:
        Authenticated Fabric client.
    credential:
        Azure credential for SQL endpoint auth.
    workspace_id:
        Workspace GUID.
    lakehouse_id:
        Lakehouse GUID.
    table_names:
        Specific tables to fix.  None = auto-detect all tables with decimal columns.
    target_type:
        PySpark type to cast decimal columns to (default: ``"double"``).
    cleanup:
        If True, delete the temporary notebook after execution.

    Returns
    -------
    DecimalFixResult
    """
    result = DecimalFixResult()

    logger.info("=" * 50)
    logger.info("FIX DECIMAL COLUMNS in Lakehouse")
    logger.info("  Workspace:  %s", workspace_id)
    logger.info("  Lakehouse:  %s", lakehouse_id)
    logger.info("  Target type: %s", target_type)
    logger.info("=" * 50)

    # Step 1: Detect decimal columns via SQL endpoint
    logger.info("[1/4] Detecting decimal columns in lakehouse …")
    try:
        decimal_cols = detect_decimal_columns(
            client, credential, workspace_id, lakehouse_id, table_names
        )
    except Exception as exc:
        result.error_message = f"Failed to detect decimal columns: {exc}"
        logger.error("  %s", result.error_message)
        return result

    if not decimal_cols:
        logger.info("  No decimal columns found — nothing to fix!")
        result.job_status = "Completed"
        return result

    for tname, cols in decimal_cols.items():
        col_names = [c.column_name for c in cols]
        result.columns_fixed[tname] = col_names
        result.tables_fixed.append(tname)
        logger.info("  %s: %d decimal columns → %s", tname, len(cols), col_names)

    logger.info(
        "  Total: %d columns in %d tables to fix",
        result.total_columns_fixed,
        len(result.tables_fixed),
    )

    # Step 2: Get lakehouse metadata and generate notebook
    logger.info("[2/4] Generating fix notebook …")
    try:
        _, lh_name = get_lakehouse_metadata(client, workspace_id, lakehouse_id)
    except Exception:
        lh_name = ""

    notebook_json = _generate_notebook_content(
        decimal_cols,
        target_type=target_type,
        workspace_id=workspace_id,
        lakehouse_id=lakehouse_id,
        lakehouse_name=lh_name,
    )

    # Step 3: Create notebook in Fabric and upload definition
    logger.info("[3/4] Creating and running notebook in Fabric workspace …")
    nb_name = f"_fix_decimal_{int(time.time())}"
    try:
        nb_id = _create_notebook_item(client, workspace_id, nb_name)
        result.notebook_id = nb_id
        logger.info("  Created notebook: %s (%s)", nb_name, nb_id)

        _upload_notebook_definition(client, workspace_id, nb_id, notebook_json)

        # Step 4: Run the notebook
        logger.info("[4/4] Executing notebook …")
        status = _run_notebook(client, workspace_id, nb_id)
        result.job_status = status

        if status == "Completed":
            logger.info("  Notebook completed successfully!")
            logger.info(
                "  Fixed %d decimal columns in %d tables",
                result.total_columns_fixed,
                len(result.tables_fixed),
            )
        else:
            result.error_message = f"Notebook job ended with status: {status}"
            logger.error("  %s", result.error_message)

    except Exception as exc:
        result.error_message = f"Notebook execution failed: {exc}"
        logger.error("  %s", result.error_message)

    finally:
        # Cleanup
        if cleanup and result.notebook_id:
            _delete_notebook(client, workspace_id, result.notebook_id)

    return result
