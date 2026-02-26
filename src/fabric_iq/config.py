"""Configuration constants and settings for Fabric IQ."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Fabric REST API
# ---------------------------------------------------------------------------
FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
FABRIC_RESOURCE_URL = "https://api.fabric.microsoft.com"

# ---------------------------------------------------------------------------
# LRO polling defaults
# ---------------------------------------------------------------------------
DEFAULT_RETRY_SECONDS = 5
MAX_WAIT_SECONDS = 300

# ---------------------------------------------------------------------------
# TMDL data-type → Ontology value-type mapping
#
# Known Fabric IQ limitations (as of 2026-02):
#   - Decimal type is NOT supported by Fabric Graph → mapped to Double.
#     See: https://learn.microsoft.com/en-us/fabric/iq/ontology/resources-troubleshooting
#   - Column names with special chars (,;{}()\n\t= and spaces) break the
#     preview experience due to unsupported column mapping on delta tables.
#   - Data binding requires Direct Lake mode SM (Import mode not supported).
#   - OneLake security must be disabled on the lakehouse.
# ---------------------------------------------------------------------------
TMDL_TYPE_MAP: dict[str, str] = {
    "int64": "BigInt",
    "double": "Double",
    "decimal": "Double",   # Fabric Graph does not support Decimal → use Double
    "string": "String",
    "boolean": "Boolean",
    "dateTime": "DateTime",
    "binary": "String",
}

# ---------------------------------------------------------------------------
# Ontology JSON schema URLs
# ---------------------------------------------------------------------------
SCHEMA_ENTITY_TYPE = "https://developer.microsoft.com/json-schemas/fabric/item/ontology/entityType/1.0.0/schema.json"
SCHEMA_DATA_BINDING = "https://developer.microsoft.com/json-schemas/fabric/item/ontology/dataBinding/1.0.0/schema.json"
SCHEMA_RELATIONSHIP_TYPE = "https://developer.microsoft.com/json-schemas/fabric/item/ontology/relationshipType/1.0.0/schema.json"
SCHEMA_CONTEXTUALIZATION = "https://developer.microsoft.com/json-schemas/fabric/item/ontology/contextualization/1.0.0/schema.json"
SCHEMA_PLATFORM = "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json"


# ---------------------------------------------------------------------------
# Workspace / item config (can be overridden via env-vars or CLI)
# ---------------------------------------------------------------------------
@dataclass
class FabricConfig:
    """Runtime configuration loaded from environment or CLI arguments."""

    api_base: str = FABRIC_API_BASE

    # Source (export)
    source_workspace_id: str = ""
    source_ontology_id: str = ""

    # Target (import / create)
    target_workspace_id: str = ""
    target_ontology_id: str = ""

    # Lakehouse & Semantic Model for create-from-SM
    semantic_model_id: str = ""
    lakehouse_id: str = ""

    # Data-source remap
    remap_source_workspace_id: str = ""
    remap_source_item_id: str = ""
    remap_target_workspace_id: str = ""
    remap_target_item_id: str = ""

    # Local paths
    export_folder: str = "./ontology-export"

    @classmethod
    def from_env(cls) -> "FabricConfig":
        """Build config from environment variables (FABRIC_IQ_ prefix)."""
        return cls(
            api_base=os.getenv("FABRIC_IQ_API_BASE", FABRIC_API_BASE),
            source_workspace_id=os.getenv("FABRIC_IQ_SOURCE_WORKSPACE_ID", ""),
            source_ontology_id=os.getenv("FABRIC_IQ_SOURCE_ONTOLOGY_ID", ""),
            target_workspace_id=os.getenv("FABRIC_IQ_TARGET_WORKSPACE_ID", ""),
            target_ontology_id=os.getenv("FABRIC_IQ_TARGET_ONTOLOGY_ID", ""),
            semantic_model_id=os.getenv("FABRIC_IQ_SEMANTIC_MODEL_ID", ""),
            lakehouse_id=os.getenv("FABRIC_IQ_LAKEHOUSE_ID", ""),
            export_folder=os.getenv("FABRIC_IQ_EXPORT_FOLDER", "./ontology-export"),
        )
