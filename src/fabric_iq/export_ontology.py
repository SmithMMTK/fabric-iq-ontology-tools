"""Export an ontology definition to local JSON files."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from fabric_iq.api_client import FabricClient

logger = logging.getLogger(__name__)


def export_ontology(
    client: FabricClient,
    workspace_id: str,
    ontology_id: str,
    output_folder: str | Path,
) -> Path:
    """Export an ontology definition to a folder of decoded JSON files.

    Parameters
    ----------
    client:
        An authenticated :class:`FabricClient`.
    workspace_id:
        The workspace GUID containing the ontology.
    ontology_id:
        The ontology GUID to export.
    output_folder:
        Local folder to write files into.

    Returns
    -------
    Path
        The output folder path.
    """
    output = Path(output_folder)
    output.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 50)
    logger.info("EXPORTING Ontology Definition")
    logger.info("  Workspace: %s", workspace_id)
    logger.info("  Ontology:  %s", ontology_id)
    logger.info("=" * 50)

    # ---- Step 1: Fetch definition via API ----
    logger.info("[1/3] Calling getDefinition API …")
    definition = client.get_ontology_definition(workspace_id, ontology_id)
    parts = definition.get("parts", [])

    if not parts:
        raise RuntimeError("No definition parts returned from the API.")

    logger.info("[2/3] Received %d definition parts.", len(parts))

    # Save raw definition for round-trip fidelity
    raw_path = output / "_raw_definition.json"
    raw_path.write_text(json.dumps(definition, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- Step 2: Decode and save each part ----
    logger.info("[3/3] Saving definition parts to: %s", output)

    manifest: list[dict] = []

    for part in parts:
        part_path: str = part["path"]
        payload: str = part["payload"]
        payload_type: str = part.get("payloadType", "InlineBase64")

        full_path = output / part_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        if payload_type == "InlineBase64":
            decoded = base64.b64decode(payload).decode("utf-8")
            # Pretty-print JSON where possible
            try:
                obj = json.loads(decoded)
                decoded = json.dumps(obj, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                pass
            full_path.write_text(decoded, encoding="utf-8")
        else:
            full_path.write_text(payload, encoding="utf-8")

        manifest.append({"path": part_path, "file": str(full_path)})
        logger.info("  Saved: %s", part_path)

    # Save manifest
    manifest_path = output / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info("Export complete! %d parts saved to %s", len(parts), output)
    return output
