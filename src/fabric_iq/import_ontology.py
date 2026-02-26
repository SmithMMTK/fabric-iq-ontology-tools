"""Import an ontology definition from local files into a Fabric workspace."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from fabric_iq.api_client import FabricClient
from fabric_iq.models import RemapConfig

logger = logging.getLogger(__name__)


def _load_definition_from_folder(input_folder: Path) -> dict:
    """Load ontology definition from a local export folder.

    Prefers ``_raw_definition.json`` for round-trip fidelity, falls back
    to rebuilding from individual files via ``_manifest.json``.
    """
    raw_path = input_folder / "_raw_definition.json"
    if raw_path.exists():
        logger.info("  Using _raw_definition.json for round-trip import.")
        return json.loads(raw_path.read_text(encoding="utf-8"))

    manifest_path = input_folder / "_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Neither _raw_definition.json nor _manifest.json found in {input_folder}"
        )

    logger.info("  Rebuilding definition from individual files …")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    parts: list[dict] = []

    for entry in manifest:
        file_path = input_folder / entry["path"]
        if not file_path.exists():
            logger.warning("  Skipping missing file: %s", entry["path"])
            continue

        content = file_path.read_text(encoding="utf-8")
        b64_payload = base64.b64encode(content.encode("utf-8")).decode("ascii")

        parts.append(
            {
                "path": entry["path"],
                "payload": b64_payload,
                "payloadType": "InlineBase64",
            }
        )
        logger.info("  Loaded: %s", entry["path"])

    return {"parts": parts}


def _apply_remap(definition: dict, remap: RemapConfig) -> int:
    """Remap data-source references in DataBinding / Contextualization parts.

    Returns the number of parts that were modified.
    """
    logger.info("  Remapping data source references:")
    logger.info("    WorkspaceId: %s → %s", remap.source_workspace_id, remap.target_workspace_id)
    logger.info("    ItemId:      %s → %s", remap.source_item_id, remap.target_item_id)

    remapped = 0
    for part in definition.get("parts", []):
        path: str = part.get("path", "")
        if "DataBindings/" not in path and "Contextualizations/" not in path:
            continue

        payload = part.get("payload", "")
        payload_type = part.get("payloadType", "InlineBase64")

        if payload_type == "InlineBase64":
            decoded = base64.b64decode(payload).decode("utf-8")
        else:
            decoded = payload

        updated = decoded.replace(remap.source_workspace_id, remap.target_workspace_id)
        updated = updated.replace(remap.source_item_id, remap.target_item_id)

        if updated != decoded:
            if payload_type == "InlineBase64":
                part["payload"] = base64.b64encode(updated.encode("utf-8")).decode("ascii")
            else:
                part["payload"] = updated
            remapped += 1
            logger.info("    Remapped: %s", path)

    logger.info("  Total parts remapped: %d", remapped)
    return remapped


def import_ontology(
    client: FabricClient,
    workspace_id: str,
    input_folder: str | Path,
    *,
    ontology_id: str = "",
    display_name: str = "Imported Ontology",
    description: str = "Ontology imported via script",
    update_metadata: bool = True,
    remap: RemapConfig | None = None,
) -> str:
    """Import an ontology definition from local files.

    Parameters
    ----------
    client:
        An authenticated :class:`FabricClient`.
    workspace_id:
        The target workspace GUID.
    input_folder:
        Path to the folder containing exported ontology files.
    ontology_id:
        Target ontology GUID.  If empty, a new ontology is created.
    display_name:
        Display name for the new ontology (only used when creating).
    description:
        Description for the new ontology (only used when creating).
    update_metadata:
        Whether to include ``?updateMetadata=True`` on the upload call.
    remap:
        Optional data-source remap configuration.

    Returns
    -------
    str
        The ontology ID (existing or newly created).
    """
    input_path = Path(input_folder)

    logger.info("=" * 50)
    logger.info("IMPORTING Ontology Definition")
    logger.info("  Workspace:   %s", workspace_id)
    logger.info("  Ontology:    %s", ontology_id or "(new)")
    logger.info("  From folder: %s", input_path)
    logger.info("=" * 50)

    # ---- Step 1: Load definition parts ----
    logger.info("[1/3] Building definition payload from local files …")
    definition = _load_definition_from_folder(input_path)

    # Optional remap
    if remap:
        _apply_remap(definition, remap)

    parts = definition.get("parts", [])
    logger.info("  Definition parts: %d", len(parts))

    # ---- Step 2: Create ontology if needed ----
    if not ontology_id:
        logger.info("[2/3] Creating new ontology in workspace …")
        ontology_id = client.create_ontology(workspace_id, display_name, description)
        logger.info("  Created ontology: %s", ontology_id)
    else:
        logger.info("[2/3] Target ontology exists: %s (will update definition)", ontology_id)

    # ---- Step 3: Upload definition ----
    logger.info("[3/3] Updating ontology definition …")
    client.update_ontology_definition(
        workspace_id,
        ontology_id,
        parts,
        update_metadata=update_metadata,
    )

    logger.info("Import complete!")
    logger.info("  Workspace: %s", workspace_id)
    logger.info("  Ontology:  %s", ontology_id)
    return ontology_id
