"""Create a new ontology from a Semantic Model + Lakehouse combination.

Orchestrates:
  1. Fetch SM definition via Fabric API
  2. Parse TMDL tables / columns / relationships
  3. (Optional) Verify column types against lakehouse SQL endpoint
  4. (Optional) Apply config overrides for PKs and relationships
  5. Build ontology definition parts
  6. Create ontology & upload definition
"""

from __future__ import annotations

import logging
from pathlib import Path

from azure.core.credentials import TokenCredential

from fabric_iq.api_client import FabricClient
from fabric_iq.tmdl_parser import parse_semantic_model
from fabric_iq.definition_builder import build_definition_parts
from fabric_iq.lakehouse_validator import (
    validate_lakehouse_types,
    log_validation_report,
)
from fabric_iq.ontology_config import (
    OntologyConfig,
    apply_pk_overrides,
    apply_relationship_overrides,
    generate_config,
    load_config,
    save_config,
)

logger = logging.getLogger(__name__)


def create_ontology_from_semantic_model(
    client: FabricClient,
    workspace_id: str,
    semantic_model_id: str,
    lakehouse_id: str,
    *,
    display_name: str = "Generated_Ontology",
    description: str = "Auto-generated from Semantic Model",
    source_schema: str = "",
    config_path: str = "",
    save_config_path: str = "",
    exclude_decimal: bool = False,
    verify_lakehouse: bool = False,
    credential: TokenCredential | None = None,
) -> str:
    """End-to-end: parse SM → build definition → create & upload ontology.

    Parameters
    ----------
    client:
        An authenticated :class:`FabricClient`.
    workspace_id:
        Workspace GUID containing the Semantic Model and Lakehouse.
    semantic_model_id:
        The Semantic Model GUID.
    lakehouse_id:
        The Lakehouse GUID to bind entities to.
    display_name:
        Display name for the new ontology.
    description:
        Description for the new ontology.
    source_schema:
        Force a specific schema.  If empty, auto-detected from TMDL.
    config_path:
        Path to an ontology config JSON file.  If non-empty, PK and
        relationship overrides from the file are applied after TMDL parsing.
    save_config_path:
        If non-empty, write the detected/resolved PKs and relationships
        to this JSON file after parsing (and after config overrides).
        The file can be reviewed and passed back via ``--config``.
    exclude_decimal:
        If True, remove columns whose TMDL type is ``decimal``.
    verify_lakehouse:
        If True, query the lakehouse SQL endpoint to cross-check column
        types against the Semantic Model.  Requires ``pyodbc`` and ODBC
        Driver 18.
    credential:
        An Azure ``TokenCredential`` needed for SQL endpoint auth when
        *verify_lakehouse* is True.

    Returns
    -------
    str
        The newly created ontology ID.
    """
    logger.info("=" * 50)
    logger.info("CREATE Ontology from Semantic Model")
    logger.info("  Workspace:      %s", workspace_id)
    logger.info("  Semantic Model: %s", semantic_model_id)
    logger.info("  Lakehouse:      %s", lakehouse_id)
    logger.info("  Name:           %s", display_name)
    if config_path:
        logger.info("  Config:         %s", config_path)
    logger.info("=" * 50)

    # ---- Load optional config ----
    config: OntologyConfig | None = None
    if config_path:
        logger.info("[*] Loading config from %s …", config_path)
        config = load_config(config_path)
        logger.info("  Entities with PK overrides: %d", len(config.entities))
        if config.relationships is not None:
            logger.info("  Relationship overrides:     %d", len(config.relationships))
        else:
            logger.info("  Relationships:              from TMDL (no override)")

    # ---- Step 1: Fetch SM definition ----
    logger.info("[1/5] Fetching Semantic Model definition …")
    sm_def = client.get_semantic_model_definition(workspace_id, semantic_model_id)
    parts_count = len(sm_def.get("definition", {}).get("parts", []))
    logger.info("  Got %d parts.", parts_count)

    # ---- Step 2: Parse TMDL ----
    logger.info("[2/5] Parsing tables, columns, and relationships …")
    tables, relationships = parse_semantic_model(
        sm_def, source_schema_override=source_schema
    )

    # ---- Step 2a: Verify lakehouse column types ----
    if verify_lakehouse:
        logger.info("[2a] Verifying column types against lakehouse SQL endpoint …")
        if credential is None:
            logger.warning("  No credential provided — skipping lakehouse verification.")
        else:
            report = validate_lakehouse_types(
                client, credential, workspace_id, lakehouse_id, tables
            )
            log_validation_report(report)

    # ---- Step 2b: Exclude decimal columns if requested ----
    if exclude_decimal:
        for tname, table in tables.items():
            decimal_cols = [c.name for c in table.columns if c.data_type == "decimal"]
            if decimal_cols:
                table.columns = [c for c in table.columns if c.data_type != "decimal"]
                # Remove excluded columns from PKs too
                table.pk_column_names = [
                    pk for pk in table.pk_column_names if pk not in decimal_cols
                ]
                logger.info(
                    "  %s: excluded %d Decimal columns: %s",
                    tname, len(decimal_cols), decimal_cols,
                )
        logger.info("Decimal columns excluded from ontology definition.")

    # ---- Step 2c: Apply config overrides ----
    if config:
        logger.info("[2c] Applying config overrides …")
        apply_pk_overrides(tables, config)
        relationships = apply_relationship_overrides(tables, relationships, config)

    # ---- Log PK summary ----
    logger.info("PK summary:")
    for tname, tbl in tables.items():
        pk_info = ", ".join(tbl.pk_column_names) if tbl.pk_column_names else "(none – will infer from relationships)"
        logger.info("  %-30s PK: [%s]", tname, pk_info)
    # ---- Save detected config for review/reuse ----
    if save_config_path:
        detected_config = generate_config(tables, relationships)
        save_config(detected_config, save_config_path)
        logger.info(
            "Detected config saved to %s (%d entities, %d relationships)",
            save_config_path,
            len(detected_config.entities),
            len(detected_config.relationships) if detected_config.relationships else 0,
        )
    # ---- Step 3: Build definition parts ----
    logger.info("[3/5] Building ontology definition parts …")
    def_parts = build_definition_parts(
        tables, relationships, workspace_id, lakehouse_id, display_name,
        entity_configs=config.entities if config else None,
    )

    # ---- Step 4: Create ontology ----
    logger.info("[4/5] Creating ontology '%s' …", display_name)
    ontology_id = client.create_ontology(workspace_id, display_name, description)
    logger.info("  Created ontology: %s", ontology_id)

    # ---- Step 5: Upload definition ----
    logger.info("[5/5] Uploading ontology definition (%d parts) …", len(def_parts))
    client.update_ontology_definition(workspace_id, ontology_id, def_parts)

    logger.info("Ontology created successfully!")
    logger.info("  Workspace:     %s", workspace_id)
    logger.info("  Ontology:      %s", ontology_id)
    logger.info("  Name:          %s", display_name)
    logger.info("  Entities: %d  |  Relationships: %d", len(tables), len(relationships))
    return ontology_id


def generate_ontology_config(
    client: FabricClient,
    workspace_id: str,
    semantic_model_id: str,
    output_path: str,
    *,
    source_schema: str = "",
) -> None:
    """Fetch SM, parse TMDL, and write a config JSON capturing detected PKs & relationships.

    The user can then review/edit the file and pass it via ``--config``
    when creating the ontology.
    """
    logger.info("Generating ontology config …")
    logger.info("  Workspace:      %s", workspace_id)
    logger.info("  Semantic Model: %s", semantic_model_id)

    sm_def = client.get_semantic_model_definition(workspace_id, semantic_model_id)
    tables, relationships = parse_semantic_model(
        sm_def, source_schema_override=source_schema
    )

    config = generate_config(tables, relationships)
    save_config(config, output_path)
    logger.info("Config written to %s (%d entities, %d relationships)",
                output_path, len(config.entities),
                len(config.relationships) if config.relationships else 0)
