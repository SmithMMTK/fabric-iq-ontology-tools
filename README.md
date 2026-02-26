# Fabric IQ – Ontology Export / Import / Create

Command-line tool and Python library for managing **Microsoft Fabric IQ Ontology** definitions via the Fabric REST API.

## Features

| Command | Description |
|---------|-------------|
| `fabric-iq list` | List ontologies in a workspace |
| `fabric-iq export` | Export an ontology to local JSON files |
| `fabric-iq import` | Import ontology from local files (create new or update existing) |
| `fabric-iq create` | Create an ontology from a Semantic Model + Lakehouse |
| `fabric-iq fix-decimals` | Cast decimal columns in lakehouse to double (runs PySpark notebook in Fabric) |
| `fabric-iq generate-config` | Generate an ontology config JSON from a Semantic Model for review/editing |

## Prerequisites

- **Python 3.10+**
- **Azure CLI** logged in (`az login`), **Az PowerShell** (`Connect-AzAccount`), or `FABRIC_ACCESS_TOKEN` env var
- Appropriate permissions on the Fabric workspace (`Item.ReadWrite.All`)

## Installation

```bash
# From the project root:
pip install -e .

# Or install dependencies directly:
pip install -r requirements.txt
```

## Quick Start

```bash
# 1. Authenticate (pick one)
az login
# or: Connect-AzAccount (PowerShell)
# or: $env:FABRIC_ACCESS_TOKEN = (Get-AzAccessToken -ResourceUrl "https://api.fabric.microsoft.com").Token

# 2. List ontologies
fabric-iq list -w <WORKSPACE_ID>

# 3. Export an ontology
fabric-iq export -w <WORKSPACE_ID> -o <ONTOLOGY_ID> -d ./my-export

# 4. Import into a new ontology
fabric-iq import -w <WORKSPACE_ID> -d ./my-export --display-name "My Ontology"

# 5. Import with data-source remapping (cross-workspace)
fabric-iq import -w <TARGET_WS> -d ./my-export \
    --remap-src-workspace <SRC_WS> --remap-src-item <SRC_LH> \
    --remap-tgt-workspace <TGT_WS> --remap-tgt-item <TGT_LH>

# 6. Create ontology from Semantic Model + Lakehouse
fabric-iq create -w <WORKSPACE_ID> \
    -s <SEMANTIC_MODEL_ID> \
    -l <LAKEHOUSE_ID> \
    --display-name "My_Ontology"

# 7. Create with PK/relationship overrides from config file
fabric-iq create -w <WORKSPACE_ID> \
    -s <SEMANTIC_MODEL_ID> \
    -l <LAKEHOUSE_ID> \
    -c ontology_config.json \
    --display-name "My_Ontology"

# 8. Create and save detected config for review/reuse
fabric-iq create -w <WORKSPACE_ID> \
    -s <SEMANTIC_MODEL_ID> \
    -l <LAKEHOUSE_ID> \
    --save-config ontology_config.json \
    --display-name "My_Ontology"

# 9. Create with lakehouse type verification
fabric-iq create -w <WORKSPACE_ID> \
    -s <SEMANTIC_MODEL_ID> \
    -l <LAKEHOUSE_ID> \
    --verify-lakehouse \
    --display-name "My_Ontology"

# 10. Fix decimal columns in lakehouse (standalone)
fabric-iq fix-decimals -w <WORKSPACE_ID> -l <LAKEHOUSE_ID>

# 11. Fix specific tables only
fabric-iq fix-decimals -w <WORKSPACE_ID> -l <LAKEHOUSE_ID> -t Trip Payment

# 12. Create with auto-fix decimals (detect + fix + create in one step)
fabric-iq create -w <WORKSPACE_ID> \
    -s <SEMANTIC_MODEL_ID> \
    -l <LAKEHOUSE_ID> \
    --fix-decimals \
    --verify-lakehouse \
    --display-name "My_Ontology"

# 13. Generate config from a Semantic Model (for review/editing)
fabric-iq generate-config -w <WORKSPACE_ID> \
    -s <SEMANTIC_MODEL_ID> \
    -o ontology_config.json
```

## Usage as a Library

```python
from fabric_iq.auth import get_credential
from fabric_iq.api_client import FabricClient
from fabric_iq.export_ontology import export_ontology
from fabric_iq.import_ontology import import_ontology
from fabric_iq.create_ontology import create_ontology_from_semantic_model

# Authenticate
credential = get_credential(prefer_cli=True)
client = FabricClient(credential)

# List ontologies
ontologies = client.list_ontologies("<WORKSPACE_ID>")
for o in ontologies:
    print(f"{o['id']}  {o['displayName']}")

# Export
export_ontology(client, "<WS>", "<ONT_ID>", "./export")

# Import
import_ontology(client, "<WS>", "./export", display_name="Imported")

# Create from Semantic Model
create_ontology_from_semantic_model(
    client,
    workspace_id="<WS>",
    semantic_model_id="<SM_ID>",
    lakehouse_id="<LH_ID>",
    display_name="My_Ontology",
)
```

## Project Structure

```
├── src/
│   └── fabric_iq/
│       ├── __init__.py              # Package metadata
│       ├── cli.py                   # CLI entry point (fabric-iq command)
│       ├── config.py                # Constants & configuration
│       ├── auth.py                  # Azure authentication
│       ├── api_client.py            # Fabric REST API client + LRO polling
│       ├── models.py                # Data models (Table, Column, etc.)
│       ├── tmdl_parser.py           # TMDL parsing logic
│       ├── definition_builder.py    # Ontology JSON definition builder
│       ├── ontology_config.py       # Config file support (PK & relationship overrides)
│       ├── lakehouse_validator.py   # Lakehouse column-type verification via SQL endpoint
│       ├── notebook_runner.py       # Run PySpark notebooks in Fabric (fix-decimals)
│       ├── export_ontology.py       # Export functionality
│       ├── import_ontology.py       # Import functionality
│       └── create_ontology.py       # Create from Semantic Model
├── tests/
│   ├── test_tmdl_parser.py
│   ├── test_definition_builder.py
│   └── test_ontology_config.py
├── ontology_config.sample.json      # Example config file (PK & relationship overrides)
├── notebooks/
│   └── fix_decimal_columns.ipynb    # Standalone notebook to fix decimal columns (upload to Fabric)
├── backup/                          # Original PowerShell project backup
├── pyproject.toml                   # Python packaging config
├── requirements.txt
└── README.md
```

## Key Design Decisions

### Ontology Direction Convention
- **Ontology source** = one / PK / dimension side entity  
- **Ontology target** = many / FK / fact side entity  
- TMDL `from` = many/FK side → ontology **target**  
- TMDL `to` = one/PK side → ontology **source**

### Contextualization Rules
- `dataBindingTable` = the FK/many table (ontology target)
- `sourceKeyRefBindings` = FK column → source entity's entityIdParts property
- `targetKeyRefBindings` = **only** entityIdParts columns of the target entity

### Primary Key Detection
PKs are detected heuristically from TMDL in this order:
1. `isKey: true` annotation (explicit PK)
2. Column named `<TableName>ID` + leading `*ID` columns before it (catches composite keys like SalesOrderDetail)
3. Fallback: contiguous leading `*ID` columns with `summarizeBy: none`

Override with a config file (`-c`) when heuristics are insufficient.

### Ontology Config File
An optional JSON file to override detected PKs and/or relationships:
```json
{
  "entities": {
    "TableName": { "pk": ["Col1", "Col2"] }
  },
  "relationships": [
    { "from_table": "FKTable", "from_column": "FKCol",
      "to_table": "PKTable", "to_column": "PKCol" }
  ]
}
```
- `entities` — overrides PK for listed tables only; unlisted tables use auto-detection
- `relationships` — if present, **replaces** all TMDL-detected relationships; if omitted, TMDL relationships are used

Use `generate-config` to create a starting config from a Semantic Model, then edit as needed.

### Known Fabric IQ Limitations
- **Decimal type** is not supported by Fabric Graph — queries return null for Decimal columns in the lakehouse. The tool maps Decimal → `String` by default. Use `--exclude-decimal` to strip them entirely, or use `fabric-iq fix-decimals` / `--fix-decimals` to automatically cast them to `double` in the lakehouse via a PySpark notebook.
- **SM type masking**: The Semantic Model may report columns as `int64` even when the underlying lakehouse stores them as `decimal`. Use `--verify-lakehouse` to query the lakehouse SQL endpoint and detect such mismatches.  Requires `pip install pyodbc` and ODBC Driver 18 for SQL Server.
- **Import mode** Semantic Models do not support data binding (Direct Lake only)
- **OneLake security** must be disabled on the lakehouse
- **Column names** with special characters (`,;{}()\n\t= ` and spaces) break the preview experience

### LRO Handling
All Fabric API calls that may return 202 (Long Running Operation) are automatically
polled to completion with configurable retry interval and timeout.

## Running Tests

```bash
pip install -e ".[dev]"
pytest -v

# With lakehouse verification support:
pip install -e ".[dev,lakehouse]"
```

## Original PowerShell Script

The original PowerShell implementation is preserved in `backup/FabricIQ-OntologyExportImport.ps1`.
