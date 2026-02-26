"""Parse TMDL (Tabular Model Definition Language) content from Semantic Model definitions.

Extracts tables, columns, relationships, and schema information needed to
build ontology definitions.
"""

from __future__ import annotations

import base64
import logging
import re

from fabric_iq.config import TMDL_TYPE_MAP
from fabric_iq.models import Column, Table, Relationship, new_unique_id

logger = logging.getLogger(__name__)


def _decode_b64(payload: str) -> str:
    """Decode a Base64 payload to a UTF-8 string."""
    return base64.b64decode(payload).decode("utf-8")


def parse_table(tmdl: str, *, source_schema_override: str = "") -> Table | None:
    """Parse a single table TMDL file into a :class:`Table`.

    Parameters
    ----------
    tmdl:
        The raw TMDL text for a single table file.
    source_schema_override:
        If non-empty, forces this schema instead of auto-detecting.

    Returns
    -------
    Table | None
        A parsed table, or *None* if the TMDL cannot be parsed.
    """
    # Extract table name from first line: "table <Name>"
    m = re.search(r"(?m)^table\s+(.+)\s*$", tmdl)
    if not m:
        return None
    table_name = m.group(1).strip()

    # Detect schema from sourceLineageTag: [Schema].[Table]
    detected_schema = "dbo"
    m_schema = re.search(r"sourceLineageTag:\s*\[(\w+)\]\.\[(\w+)\]", tmdl)
    if m_schema:
        detected_schema = m_schema.group(1)

    schema = source_schema_override if source_schema_override else detected_schema

    # Parse columns by splitting into column blocks
    columns: list[Column] = []
    col_blocks = re.split(r"(?m)(?=^\s+column\s+)", tmdl)

    for block in col_blocks:
        m_col = re.search(r"(?m)^\s+column\s+(\S+)", block)
        if not m_col:
            continue
        col_name = m_col.group(1)
        col_data_type = "string"
        m_dt = re.search(r"(?m)^\s+dataType:\s*(\w+)", block)
        if m_dt:
            col_data_type = m_dt.group(1)

        summarize_by = "none"
        m_sb = re.search(r"(?m)^\s+summarizeBy:\s*(\w+)", block)
        if m_sb:
            summarize_by = m_sb.group(1)

        value_type = TMDL_TYPE_MAP.get(col_data_type, "String")

        if col_data_type == "decimal":
            logger.warning(
                "  Column '%s': Decimal type not supported by Fabric Graph → mapped to Double",
                col_name,
            )

        columns.append(
            Column(
                name=col_name,
                data_type=col_data_type,
                value_type=value_type,
                summarize_by=summarize_by,
                ontology_id=new_unique_id(),
            )
        )

    if not columns:
        return None

    # Detect primary key columns heuristically.
    #
    # Rules (in priority order):
    #   1. Columns with ``isKey: true`` in TMDL → explicit PK.
    #   2. Column named exactly ``<TableName>ID`` → always a PK column.
    #   3. Other leading *ID columns with ``summarizeBy: none`` that don't
    #      look like foreign keys to another table (i.e., name doesn't
    #      match ``<OtherKnownTable>ID``).  We stop at the first column
    #      that doesn't qualify.
    #
    # The combination of 2 + 3 catches composite keys like
    # SalesOrderDetail (SalesOrderID + SalesOrderDetailID) and junction
    # tables like CustomerAddress (CustomerID + AddressID).

    pk_cols: list[str] = []

    # Strategy 1: isKey annotation
    for block in col_blocks:
        m_col = re.search(r"(?m)^\s+column\s+(\S+)", block)
        if not m_col:
            continue
        if re.search(r"(?m)^\s+isKey:\s*true", block, re.IGNORECASE):
            pk_cols.append(m_col.group(1))

    # Strategy 2+3: table's own ID column + leading compatible ID columns
    if not pk_cols:
        own_id_name = f"{table_name}ID"
        own_id_found = False
        for col in columns:
            if col.name.lower() == own_id_name.lower():
                pk_cols.append(col.name)
                own_id_found = True
            elif (
                col.name.upper().endswith("ID")
                and col.summarize_by == "none"
                and not own_id_found  # only consider columns before <Table>ID
            ):
                # Leading FK/key column (e.g. SalesOrderID in SalesOrderDetail)
                pk_cols.append(col.name)
            elif own_id_found:
                break  # stop once we've passed the table's own ID column

        # If the table's own ID col wasn't found, fall back to leading
        # contiguous *ID columns with summarizeBy: none
        if not own_id_found:
            pk_cols = []
            for col in columns:
                if col.name.upper().endswith("ID") and col.summarize_by == "none":
                    pk_cols.append(col.name)
                else:
                    break

    return Table(
        name=table_name,
        schema=schema,
        columns=columns,
        pk_column_names=pk_cols,
        entity_type_id=new_unique_id(),
    )


def parse_relationships(tmdl: str, known_tables: set[str]) -> list[Relationship]:
    """Parse the ``relationships.tmdl`` file.

    Parameters
    ----------
    tmdl:
        Raw TMDL text from ``definition/relationships.tmdl``.
    known_tables:
        Set of table names that were successfully parsed.  Relationships
        referencing unknown tables are silently skipped.

    Returns
    -------
    list[Relationship]
    """
    relationships: list[Relationship] = []
    blocks = re.split(r"(?m)(?=^relationship\s+)", tmdl)

    for block in blocks:
        m_rel = re.search(r"(?m)^relationship\s+(\S+)", block)
        if not m_rel:
            continue
        rel_id = m_rel.group(1)

        from_table = from_col = to_table = to_col = None

        m_from = re.search(r"fromColumn:\s*(\w+)\.(\w+)", block)
        if m_from:
            from_table, from_col = m_from.group(1), m_from.group(2)

        m_to = re.search(r"toColumn:\s*(\w+)\.(\w+)", block)
        if m_to:
            to_table, to_col = m_to.group(1), m_to.group(2)

        if not (from_table and to_table):
            continue
        if from_table not in known_tables or to_table not in known_tables:
            logger.debug("  Skipping relationship %s (%s → %s): unknown table", rel_id, from_table, to_table)
            continue

        relationships.append(
            Relationship(
                rel_id=rel_id,
                from_table=from_table,
                from_col=from_col,
                to_table=to_table,
                to_col=to_col,
            )
        )

    return relationships


def parse_semantic_model(
    sm_definition: dict,
    *,
    source_schema_override: str = "",
) -> tuple[dict[str, Table], list[Relationship]]:
    """Parse all tables and relationships from a Semantic Model definition.

    Parameters
    ----------
    sm_definition:
        The full SM definition payload (as returned by the API) containing
        ``definition.parts``.
    source_schema_override:
        If non-empty, forces this schema for all tables.

    Returns
    -------
    tuple[dict[str, Table], list[Relationship]]
        A dict of table-name → Table and a list of Relationships.
    """
    parts = sm_definition.get("definition", {}).get("parts", [])

    # ---- Parse tables ----
    tables: dict[str, Table] = {}
    for part in parts:
        if not part["path"].startswith("definition/tables/") or not part["path"].endswith(".tmdl"):
            continue
        tmdl = _decode_b64(part["payload"])
        table = parse_table(tmdl, source_schema_override=source_schema_override)
        if table:
            tables[table.name] = table
            logger.info(
                "  Table: %s.%s (%d columns) → EntityType %s",
                table.schema,
                table.name,
                len(table.columns),
                table.entity_type_id,
            )

    # ---- Parse relationships ----
    relationships: list[Relationship] = []
    for part in parts:
        if part["path"] == "definition/relationships.tmdl":
            tmdl = _decode_b64(part["payload"])
            relationships = parse_relationships(tmdl, set(tables.keys()))
            break

    for rel in relationships:
        logger.info("  Relationship: %s.%s → %s.%s", rel.from_table, rel.from_col, rel.to_table, rel.to_col)

    logger.info("  Found %d tables, %d relationships.", len(tables), len(relationships))
    return tables, relationships
