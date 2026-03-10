"""Ontology configuration file support.

Allows users to define explicit primary keys and relationships in a JSON
file, overriding the heuristic detection from TMDL parsing.

File format
-----------
.. code-block:: json

    {
        "entities": {
            "TableName": {
                "pk": ["Col1", "Col2"],
                "lakehouse_table": "lh_table_name",
                "lakehouse_schema": "raw",
                "column_mappings": {
                    "SMColumnName": "lakehouse_column_name"
                }
            }
        },
        "relationships": [
            {
                "from_table": "ManyFKTable",
                "from_column": "FKColumn",
                "to_table": "OnePKTable",
                "to_column": "PKColumn"
            }
        ]
    }

Semantics:
  - ``entities`` (optional): Per-entity overrides.  Only tables listed here
    have their settings overridden; all other tables use defaults/heuristics.
    - ``pk``: Primary key column names (overrides heuristic detection).
    - ``lakehouse_table``: Lakehouse table name if different from SM table.
    - ``lakehouse_schema``: Lakehouse schema if different from SM.
    - ``column_mappings``: SM column name → lakehouse column name mapping
      for data bindings and contextualizations.
  - ``relationships`` (optional): If present, **replaces** all TMDL-detected
    relationships.  Uses TMDL convention: ``from`` = many/FK side,
    ``to`` = one/PK side.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from fabric_iq.models import Relationship, Table

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class EntityConfig:
    """Per-entity overrides."""
    pk: list[str] = field(default_factory=list)
    lakehouse_table: str = ""    # Override binding table name (default: SM table name)
    lakehouse_schema: str = ""   # Override binding schema (default: SM schema)
    column_mappings: dict[str, str] = field(default_factory=dict)  # SM col → LH col


@dataclass
class RelationshipConfig:
    """Explicit relationship definition.

    Uses TMDL convention:
      from = many / FK side
      to   = one / PK side
    """
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class OntologyConfig:
    """Top-level ontology configuration."""
    entities: dict[str, EntityConfig] = field(default_factory=dict)
    relationships: list[RelationshipConfig] | None = None  # None = use TMDL


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_config(path: str | Path) -> OntologyConfig:
    """Read an ontology config JSON file.

    Returns an :class:`OntologyConfig` populated from the file.
    Raises ``FileNotFoundError`` or ``json.JSONDecodeError`` on bad input.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    entities: dict[str, EntityConfig] = {}
    for name, cfg in raw.get("entities", {}).items():
        pk = cfg.get("pk", [])
        if not isinstance(pk, list):
            pk = [pk]
        entities[name] = EntityConfig(
            pk=pk,
            lakehouse_table=cfg.get("lakehouse_table", ""),
            lakehouse_schema=cfg.get("lakehouse_schema", ""),
            column_mappings=cfg.get("column_mappings", {}),
        )

    relationships: list[RelationshipConfig] | None = None
    if "relationships" in raw:
        relationships = []
        for r in raw["relationships"]:
            relationships.append(
                RelationshipConfig(
                    from_table=r["from_table"],
                    from_column=r["from_column"],
                    to_table=r["to_table"],
                    to_column=r["to_column"],
                )
            )

    return OntologyConfig(entities=entities, relationships=relationships)


def save_config(config: OntologyConfig, path: str | Path) -> None:
    """Persist an :class:`OntologyConfig` to a JSON file."""
    data: dict = {}
    if config.entities:
        ent_data: dict[str, dict] = {}
        for name, ec in config.entities.items():
            entry: dict = {"pk": ec.pk}
            if ec.lakehouse_table:
                entry["lakehouse_table"] = ec.lakehouse_table
            if ec.lakehouse_schema:
                entry["lakehouse_schema"] = ec.lakehouse_schema
            if ec.column_mappings:
                entry["column_mappings"] = ec.column_mappings
            ent_data[name] = entry
        data["entities"] = ent_data
    if config.relationships is not None:
        data["relationships"] = [
            {
                "from_table": r.from_table,
                "from_column": r.from_column,
                "to_table": r.to_table,
                "to_column": r.to_column,
            }
            for r in config.relationships
        ]
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Config written to %s", path)


# ---------------------------------------------------------------------------
# Apply overrides
# ---------------------------------------------------------------------------

def apply_pk_overrides(
    tables: dict[str, Table],
    config: OntologyConfig,
) -> None:
    """Apply PK overrides from *config* to *tables* **in-place**.

    For every table listed in ``config.entities`` whose ``pk`` list is
    non-empty, the table's ``pk_column_names`` is replaced.
    """
    for table_name, ec in config.entities.items():
        if not ec.pk:
            continue
        table = tables.get(table_name)
        if table is None:
            logger.warning("Config references unknown table '%s' – skipping PK override", table_name)
            continue
        known_cols = {c.name for c in table.columns}
        bad = [c for c in ec.pk if c not in known_cols]
        if bad:
            logger.warning(
                "Table '%s': config PK columns %s not found in table – skipping override",
                table_name, bad,
            )
            continue
        logger.info("  PK override: %s → %s", table_name, ec.pk)
        table.pk_column_names = list(ec.pk)


def apply_relationship_overrides(
    tables: dict[str, Table],
    tmdl_relationships: list[Relationship],
    config: OntologyConfig,
) -> list[Relationship]:
    """Return the final relationship list after applying config overrides.

    - If ``config.relationships`` is *None*, return the original TMDL list.
    - If ``config.relationships`` is an empty list, return no relationships.
    - Otherwise, replace the TMDL list with config-defined relationships.

    Unknown tables are logged and silently skipped.
    """
    if config.relationships is None:
        return tmdl_relationships

    result: list[Relationship] = []
    known = set(tables.keys())
    for i, rc in enumerate(config.relationships):
        if rc.from_table not in known:
            logger.warning("Relationship #%d: unknown from_table '%s' – skipped", i, rc.from_table)
            continue
        if rc.to_table not in known:
            logger.warning("Relationship #%d: unknown to_table '%s' – skipped", i, rc.to_table)
            continue
        result.append(
            Relationship(
                rel_id=f"config_{i}",
                from_table=rc.from_table,
                from_col=rc.from_column,
                to_table=rc.to_table,
                to_col=rc.to_column,
            )
        )

    logger.info(
        "  Relationships: %d from config (replaced %d from TMDL)",
        len(result), len(tmdl_relationships),
    )
    return result


# ---------------------------------------------------------------------------
# Generate config from parsed data
# ---------------------------------------------------------------------------

def generate_config(
    tables: dict[str, Table],
    relationships: list[Relationship],
) -> OntologyConfig:
    """Build an :class:`OntologyConfig` from already-parsed TMDL data.

    This captures the heuristically-detected PKs and TMDL relationships
    so the user can review and edit them.  For tables with no explicit PK,
    the inferred entityIdParts (from FK columns) are included so the
    config is transparent about what the tool will actually use.
    """
    # Lazy import to avoid circular dependency
    from fabric_iq.definition_builder import (
        compute_entity_id_parts,
        normalize_relationships,
    )

    norm_rels = normalize_relationships(tables, relationships)
    id_parts_map = compute_entity_id_parts(tables, norm_rels)

    # Build a reverse lookup: ontology_id → column name
    col_name_by_id: dict[str, str] = {}
    for t in tables.values():
        for c in t.columns:
            col_name_by_id[c.ontology_id] = c.name

    entities = {}
    for name, t in sorted(tables.items()):
        if t.pk_column_names:
            pk = list(t.pk_column_names)
        else:
            # Resolve inferred entityIdParts back to column names
            pk = [
                col_name_by_id[oid]
                for oid in id_parts_map.get(name, [])
                if oid in col_name_by_id
            ]
        entities[name] = EntityConfig(pk=pk)

    rels = [
        RelationshipConfig(
            from_table=r.from_table,
            from_column=r.from_col,
            to_table=r.to_table,
            to_column=r.to_col,
        )
        for r in relationships
    ]
    return OntologyConfig(entities=entities, relationships=rels)
