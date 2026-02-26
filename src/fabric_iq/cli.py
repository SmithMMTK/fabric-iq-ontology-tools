"""Command-line interface for Fabric IQ Ontology tools.

Usage
-----
    fabric-iq list       --workspace-id <WS>
    fabric-iq export     --workspace-id <WS> --ontology-id <ONT> --output-folder ./export
    fabric-iq import     --workspace-id <WS> --input-folder ./export [--ontology-id <ONT>]
    fabric-iq create     --workspace-id <WS> --semantic-model-id <SM> --lakehouse-id <LH>
"""

from __future__ import annotations

import argparse
import logging
import sys

from fabric_iq.auth import get_credential
from fabric_iq.api_client import FabricClient, FabricApiError
from fabric_iq.export_ontology import export_ontology
from fabric_iq.import_ontology import import_ontology
from fabric_iq.create_ontology import create_ontology_from_semantic_model, generate_ontology_config
from fabric_iq.models import RemapConfig


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Quiet noisy libraries
    for name in ("azure", "urllib3", "requests", "msal"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fabric-iq",
        description="Fabric IQ – Ontology Export / Import / Create",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--api-base",
        default="https://api.fabric.microsoft.com/v1",
        help="Fabric REST API base URL",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ---- list ----
    p_list = sub.add_parser("list", help="List ontologies in a workspace")
    p_list.add_argument("-w", "--workspace-id", required=True, help="Workspace GUID")

    # ---- export ----
    p_export = sub.add_parser("export", help="Export ontology to local files")
    p_export.add_argument("-w", "--workspace-id", required=True)
    p_export.add_argument("-o", "--ontology-id", required=True)
    p_export.add_argument("-d", "--output-folder", default="./ontology-export")

    # ---- import ----
    p_import = sub.add_parser("import", help="Import ontology from local files")
    p_import.add_argument("-w", "--workspace-id", required=True)
    p_import.add_argument("-d", "--input-folder", required=True)
    p_import.add_argument("-o", "--ontology-id", default="", help="Existing ontology (empty = create new)")
    p_import.add_argument("--display-name", default="Imported Ontology")
    p_import.add_argument("--description", default="Ontology imported via script")
    p_import.add_argument("--no-update-metadata", action="store_true")
    # Remap options
    p_import.add_argument("--remap-src-workspace", default="")
    p_import.add_argument("--remap-src-item", default="")
    p_import.add_argument("--remap-tgt-workspace", default="")
    p_import.add_argument("--remap-tgt-item", default="")

    # ---- create ----
    p_create = sub.add_parser("create", help="Create ontology from Semantic Model + Lakehouse")
    p_create.add_argument("-w", "--workspace-id", required=True)
    p_create.add_argument("-s", "--semantic-model-id", required=True)
    p_create.add_argument("-l", "--lakehouse-id", required=True)
    p_create.add_argument("--display-name", default="Generated_Ontology")
    p_create.add_argument("--description", default="Auto-generated from Semantic Model")
    p_create.add_argument("--source-schema", default="", help="Override source schema (default: auto-detect)")
    p_create.add_argument(
        "-c", "--config", default="",
        help="Path to ontology config JSON (PK & relationship overrides)",
    )

    # ---- generate-config ----
    p_genconf = sub.add_parser(
        "generate-config",
        help="Generate an ontology config JSON from a Semantic Model (for review/editing)",
    )
    p_genconf.add_argument("-w", "--workspace-id", required=True)
    p_genconf.add_argument("-s", "--semantic-model-id", required=True)
    p_genconf.add_argument(
        "-o", "--output", default="ontology_config.json",
        help="Output file path (default: ontology_config.json)",
    )
    p_genconf.add_argument("--source-schema", default="", help="Override source schema")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    try:
        credential = get_credential(prefer_cli=True)
        client = FabricClient(credential, api_base=args.api_base)
    except Exception as exc:
        logger.error("Authentication failed: %s", exc)
        logger.error("Run 'az login' or 'Connect-AzAccount' first.")
        return 1

    try:
        if args.command == "list":
            ontologies = client.list_ontologies(args.workspace_id)
            if not ontologies:
                logger.info("No ontologies found in workspace %s", args.workspace_id)
            else:
                logger.info("Ontologies in workspace %s:", args.workspace_id)
                for ont in ontologies:
                    logger.info(
                        "  ID: %s  |  Name: %s  |  Description: %s",
                        ont.get("id"),
                        ont.get("displayName"),
                        ont.get("description", ""),
                    )

        elif args.command == "export":
            export_ontology(
                client,
                args.workspace_id,
                args.ontology_id,
                args.output_folder,
            )

        elif args.command == "import":
            remap = None
            if args.remap_src_workspace and args.remap_src_item:
                remap = RemapConfig(
                    source_workspace_id=args.remap_src_workspace,
                    source_item_id=args.remap_src_item,
                    target_workspace_id=args.remap_tgt_workspace or args.workspace_id,
                    target_item_id=args.remap_tgt_item,
                )

            import_ontology(
                client,
                args.workspace_id,
                args.input_folder,
                ontology_id=args.ontology_id,
                display_name=args.display_name,
                description=args.description,
                update_metadata=not args.no_update_metadata,
                remap=remap,
            )

        elif args.command == "create":
            create_ontology_from_semantic_model(
                client,
                args.workspace_id,
                args.semantic_model_id,
                args.lakehouse_id,
                display_name=args.display_name,
                description=args.description,
                source_schema=args.source_schema,
                config_path=args.config,
            )

        elif args.command == "generate-config":
            generate_ontology_config(
                client,
                args.workspace_id,
                args.semantic_model_id,
                args.output,
                source_schema=args.source_schema,
            )

    except FabricApiError as exc:
        logger.error("Fabric API Error: %s", exc)
        if exc.detail:
            logger.error("  Detail: %s", exc.detail)
        return 1
    except Exception as exc:
        logger.error("Error: %s", exc, exc_info=args.verbose)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
