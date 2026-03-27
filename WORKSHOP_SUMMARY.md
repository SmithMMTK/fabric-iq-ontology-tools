# Workshop Summary — Fabric IQ Ontology Tools

## สรุปภาพรวม (Overview in Thai)

### โปรเจกต์นี้ทำอะไร?

**Fabric IQ Ontology Tools** คือเครื่องมือบน Command-line และ Python library สำหรับจัดการ **Microsoft Fabric IQ Ontology** ผ่าน Fabric REST API

### ความสามารถหลัก (Core Features)

| คำสั่ง                      | ทำอะไร                                                                    |
| --------------------------- | ------------------------------------------------------------------------- |
| `fabric-iq list`            | แสดงรายการ ontology ทั้งหมดใน workspace                                   |
| `fabric-iq export`          | ส่งออก ontology เป็นไฟล์ JSON เก็บไว้ในเครื่อง (backup, version control)  |
| `fabric-iq import`          | นำเข้า ontology จากไฟล์ (สร้างใหม่หรืออัปเดต) รองรับการย้ายข้าม workspace |
| `fabric-iq create`          | **สร้าง ontology ใหม่อัตโนมัติ** จาก Semantic Model + Lakehouse           |
| `fabric-iq fix-decimals`    | แก้ไขปัญหา decimal columns (Fabric Graph ไม่รองรับ) ด้วย PySpark notebook |
| `fabric-iq generate-config` | สร้างไฟล์ config สำหรับตรวจสอบและแก้ไข PK/relationships                   |

### กระบวนการทำงานหลัก (Main Workflow)

```
Semantic Model (TMDL)
        ↓
  [Parser อ่าน TMDL]
        ↓
  Tables + Columns + Relationships + Primary Keys
        ↓
  [ตรวจสอบ Lakehouse types]  ← (optional)
        ↓
  [แก้ไข decimal columns]     ← (optional)
        ↓
  [สร้าง Ontology JSON definition]
        ↓
  [Upload ไป Fabric via API]
        ↓
  ✅ Ontology พร้อมใช้งาน
```

### ใช้งานอย่างไร?

#### 1. ติดตั้ง

```bash
pip install -e .
```

#### 2. ตั้งค่า Environment Variables (`.env`)

```bash
cp .env.example .env
# แก้ไขใส่ GUID ของคุณ
```

```bash
WORKSPACE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
SEMANTIC_MODEL_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
LAKEHOUSE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

#### 3. Load Variables

```bash
source <(grep -v '^#' .env | sed 's/^/export /')
```

#### 4. Login

```bash
az login
```

#### 5. สร้าง Ontology

```bash
# แบบพื้นฐาน
fabric-iq create -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -l $LAKEHOUSE_ID \
    --display-name "My_Ontology"

# แบบเต็ม (แนะนำ)
fabric-iq create -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -l $LAKEHOUSE_ID \
    -c ontology_config.json \
    --fix-decimals \
    --verify-lakehouse \
    --display-name "My_Ontology"
```

### สิ่งที่ Workshop นี้สอน

1. **การทำความเข้าใจ Fabric IQ Ontology**
   - Entity (tables) และ Properties (columns)
   - Relationships แบบ one-to-many / many-to-one
   - Data Bindings (เชื่อมกับ Lakehouse tables)
   - Contextualizations (เชื่อม relationships กับ FK/PK columns)

2. **การใช้ TMDL (Tabular Model Definition Language)**
   - อ่านและ parse คำจำกัดความจาก Semantic Model
   - ตรวจจับ Primary Keys อัตโนมัติ
   - แปลงความสัมพันธ์จาก TMDL เป็น Ontology

3. **การทำงานกับ Fabric REST API**
   - Authentication ผ่าน Azure CLI / PowerShell / Token
   - Long Running Operations (LRO) polling
   - CRUD operations สำหรับ ontologies

4. **การแก้ปัญหาที่พบบ่อย**
   - Decimal type ที่ Fabric Graph ไม่รองรับ → ใช้ PySpark แปลงเป็น double
   - Primary Key detection ที่ผิดพลาด → ใช้ config file override
   - Cross-workspace migration → ใช้ data-source remapping

5. **Best Practices**
   - ใช้ `.env` file เพื่อเก็บ credentials และ GUIDs
   - Export ontology เพื่อ backup และ version control
   - Generate config file เพื่อตรวจสอบก่อนสร้าง ontology
   - ทดสอบด้วย `--verify-lakehouse` ก่อน deploy

---

## Workshop Summary (English)

### What Does This Project Do?

**Fabric IQ Ontology Tools** is a CLI tool and Python library for managing **Microsoft Fabric IQ Ontology** definitions through the Fabric REST API.

### Core Capabilities

| Command                     | What It Does                                                                        |
| --------------------------- | ----------------------------------------------------------------------------------- |
| `fabric-iq list`            | List all ontologies in a workspace                                                  |
| `fabric-iq export`          | Export ontology to local JSON files (backup, version control, migration)            |
| `fabric-iq import`          | Import ontology from files (create new or update existing, cross-workspace support) |
| `fabric-iq create`          | **Auto-generate ontology** from Semantic Model + Lakehouse                          |
| `fabric-iq fix-decimals`    | Fix decimal columns (unsupported by Fabric Graph) via PySpark notebook              |
| `fabric-iq generate-config` | Generate config file for PK/relationship review and overrides                       |

### The Pipeline

```
Semantic Model (TMDL)
        ↓
  [Parse TMDL]
        ↓
  Tables + Columns + Relationships + Primary Keys
        ↓
  [Validate Lakehouse types]  ← (optional)
        ↓
  [Fix decimal columns]        ← (optional)
        ↓
  [Build Ontology JSON definition]
        ↓
  [Upload to Fabric via API]
        ↓
  ✅ Ontology Ready
```

### Quick Start

#### 1. Install

```bash
pip install -e .
```

#### 2. Set up `.env`

```bash
cp .env.example .env
# Edit with your actual GUIDs
```

#### 3. Load Variables

```bash
source <(grep -v '^#' .env | sed 's/^/export /')
```

#### 4. Authenticate

```bash
az login
```

#### 5. Create Ontology

```bash
# Basic
fabric-iq create -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -l $LAKEHOUSE_ID \
    --display-name "My_Ontology"

# Full (Recommended)
fabric-iq create -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -l $LAKEHOUSE_ID \
    -c ontology_config.json \
    --fix-decimals \
    --verify-lakehouse \
    --display-name "My_Ontology"
```

### What This Workshop Teaches

#### 1. **Understanding Fabric IQ Ontology**

- Entities (tables) and Properties (columns)
- One-to-many and many-to-one relationships
- Data Bindings (connecting to Lakehouse tables)
- Contextualizations (linking relationships to FK/PK columns)
- Direction conventions: source (one/PK) vs target (many/FK)

#### 2. **Working with TMDL (Tabular Model Definition Language)**

- Reading and parsing Semantic Model definitions
- Automatic Primary Key detection (3-tier heuristic)
- Relationship extraction and normalization
- Type mapping (TMDL → Ontology value types)

#### 3. **Fabric REST API Integration**

- Authentication strategies (Azure CLI, PowerShell, Token, DefaultAzureCredential)
- Long Running Operations (LRO) polling pattern
- CRUD operations for ontology items
- Definition upload/download with Base64 encoding

#### 4. **Common Issues and Solutions**

- **Decimal type unsupported** → Auto-fix with PySpark notebook (CTAS + DROP + RENAME)
- **Wrong PK detection** → Override with config file
- **Cross-workspace migration** → Use data-source remapping
- **Column type mismatches** → Validate with SQL endpoint before creating

#### 5. **Best Practices Demonstrated**

- Using `.env` files for secure credential management
- Exporting ontologies for backup and version control
- Generating config files for human review before automation
- Incremental workflow: detect → review → fix → create
- Testing with `--verify-lakehouse` before deployment

### Key Technical Concepts

#### Primary Key Detection Heuristic

1. **Explicit**: `isKey: true` annotation in TMDL
2. **Name-based**: Column named `<TableName>ID` + leading `*ID` columns
3. **Fallback**: Contiguous leading `*ID` columns with `summarizeBy: none`

#### Relationship Direction Mapping

```
TMDL:      from (many/FK) → to (one/PK)
Ontology:  target (many/FK) ← source (one/PK)
```

#### Data Binding Structure

- **Entity** → Lakehouse table
- **Property** → Lakehouse column
- **Contextualization** → Relationship data binding
  - `dataBindingTable` = FK table (target)
  - `sourceKeyRefBindings` = FK column → source entity's PK
  - `targetKeyRefBindings` = all entityIdParts of target entity

### Architecture Overview

```
CLI (cli.py)
  ├── auth.py                 → Azure authentication
  ├── api_client.py           → Fabric REST API + LRO
  ├── tmdl_parser.py          → Parse Semantic Model TMDL
  ├── definition_builder.py   → Build ontology JSON parts
  ├── ontology_config.py      → Config file support (overrides)
  ├── lakehouse_validator.py  → SQL endpoint validation
  ├── notebook_runner.py      → PySpark notebook execution
  ├── export_ontology.py      → Export to local files
  ├── import_ontology.py      → Import from local files
  └── create_ontology.py      → End-to-end orchestration
```

### Use Cases Covered

1. **Ontology Creation** — Generate from Semantic Model + Lakehouse in one command
2. **Backup & Recovery** — Export/import for disaster recovery
3. **Cross-Workspace Migration** — Move ontologies between workspaces with remapping
4. **Version Control** — Store ontology definitions in Git
5. **PK/Relationship Customization** — Override auto-detection with config files
6. **Decimal Column Fix** — Auto-remediate type incompatibilities
7. **Type Validation** — Verify lakehouse schema before ontology creation

### Workshop Outcomes

By the end of this workshop, participants will be able to:

✅ Understand Fabric IQ Ontology architecture and concepts  
✅ Use CLI tools to manage ontologies programmatically  
✅ Parse TMDL and build ontology definitions  
✅ Handle edge cases (decimal types, PK detection, cross-workspace migration)  
✅ Integrate ontology management into CI/CD pipelines  
✅ Debug and troubleshoot common issues  
✅ Extend the tool with Python library for custom workflows

---

## Project Structure Quick Reference

```
fabric-iq-ontology-tools/
├── src/fabric_iq/              # Core library
│   ├── cli.py                  # CLI entry point
│   ├── auth.py                 # Azure authentication
│   ├── api_client.py           # REST API client
│   ├── tmdl_parser.py          # TMDL parsing
│   ├── definition_builder.py   # Ontology JSON builder
│   ├── ontology_config.py      # Config file support
│   ├── lakehouse_validator.py  # SQL endpoint validation
│   ├── notebook_runner.py      # PySpark execution
│   ├── export_ontology.py      # Export functionality
│   ├── import_ontology.py      # Import functionality
│   └── create_ontology.py      # Create orchestration
│
├── tests/                      # Unit tests (no credentials needed)
├── .env.example                # Environment variable template
├── ontology_config.sample.json # Example config file
├── README.md                   # Comprehensive documentation
└── WORKSHOP_SUMMARY.md         # This file
```

---

## Next Steps

After completing this workshop:

1. **Try the tool on your own data**
   - Use your Semantic Model and Lakehouse
   - Generate config and review PK/relationship detection
   - Create your first ontology

2. **Explore advanced features**
   - Cross-workspace migration with remapping
   - Custom Python scripts using the library
   - CI/CD integration for automated ontology deployment

3. **Contribute back**
   - Report issues or edge cases
   - Add new features (e.g., support for more data types)
   - Improve documentation with your learnings

4. **Read the full documentation**
   - [README.md](README.md) — Complete reference
   - [ontology_config.sample.json](ontology_config.sample.json) — Config examples
   - [tests/](tests/) — Unit tests as usage examples

---

## Resources

- **Fabric IQ Docs**: https://learn.microsoft.com/fabric/iq/
- **Fabric REST API**: https://learn.microsoft.com/rest/api/fabric/
- **TMDL Reference**: https://learn.microsoft.com/power-bi/developer/projects/tmdl
- **Project Repository**: https://github.com/wachirakarwinwit/fabric-iq-ontology-tools

---

**Happy Building! 🚀**
