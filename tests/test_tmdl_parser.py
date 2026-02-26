"""Tests for TMDL parser."""

from fabric_iq.tmdl_parser import parse_table, parse_relationships


SAMPLE_TABLE_TMDL = """\
table Address
\tlineageTag: e260d02a-5d9f-45e4-ae55-efb20360894b
\tsourceLineageTag: [SalesLT].[Address]

\tcolumn AddressID
\t\tdataType: int64
\t\tformatString: 0
\t\tlineageTag: a0ead8cb-26b0-406c-8ae8-c994aeb24dcf
\t\tsourceLineageTag: AddressID
\t\tsummarizeBy: none
\t\tsourceColumn: AddressID

\t\tannotation SummarizationSetBy = Automatic

\tcolumn City
\t\tdataType: string
\t\tlineageTag: e8947107-9fdb-40e2-82dc-74b90d879306
\t\tsourceLineageTag: City
\t\tsummarizeBy: none
\t\tsourceColumn: City

\t\tannotation SummarizationSetBy = Automatic

\tcolumn ModifiedDate
\t\tdataType: dateTime
\t\tformatString: General Date
\t\tlineageTag: 1063ebd2-ff95-4faa-8c71-3b813f11acb5
\t\tsourceLineageTag: ModifiedDate
\t\tsummarizeBy: none
\t\tsourceColumn: ModifiedDate

\t\tannotation SummarizationSetBy = Automatic
"""

# Composite PK table: SalesOrderDetail has SalesOrderID (none) + SalesOrderDetailID (count)
SAMPLE_COMPOSITE_PK_TMDL = """\
table SalesOrderDetail
\tlineageTag: 11c74e18
\tsourceLineageTag: [SalesLT].[SalesOrderDetail]

\tcolumn SalesOrderID
\t\tdataType: int64
\t\tsummarizeBy: none
\t\tsourceColumn: SalesOrderID

\tcolumn SalesOrderDetailID
\t\tdataType: int64
\t\tsummarizeBy: count
\t\tsourceColumn: SalesOrderDetailID

\tcolumn OrderQty
\t\tdataType: int64
\t\tsummarizeBy: sum
\t\tsourceColumn: OrderQty

\tcolumn ProductID
\t\tdataType: int64
\t\tsummarizeBy: none
\t\tsourceColumn: ProductID
"""

# Table with self-referencing FK (ParentProductCategoryID)
SAMPLE_SELF_REF_TMDL = """\
table ProductCategory
\tlineageTag: abc123
\tsourceLineageTag: [SalesLT].[ProductCategory]

\tcolumn ProductCategoryID
\t\tdataType: int64
\t\tsummarizeBy: none
\t\tsourceColumn: ProductCategoryID

\tcolumn ParentProductCategoryID
\t\tdataType: int64
\t\tsummarizeBy: none
\t\tsourceColumn: ParentProductCategoryID

\tcolumn Name
\t\tdataType: string
\t\tsummarizeBy: none
\t\tsourceColumn: Name
"""

# Junction table: both leading columns are FKs (no own *ID)
SAMPLE_JUNCTION_TMDL = """\
table CustomerAddress
\tlineageTag: fe77096f
\tsourceLineageTag: [SalesLT].[CustomerAddress]

\tcolumn CustomerID
\t\tdataType: int64
\t\tsummarizeBy: none
\t\tsourceColumn: CustomerID

\tcolumn AddressID
\t\tdataType: int64
\t\tsummarizeBy: none
\t\tsourceColumn: AddressID

\tcolumn AddressType
\t\tdataType: string
\t\tsummarizeBy: none
\t\tsourceColumn: AddressType
"""


SAMPLE_REL_TMDL = """\
relationship 5432054533379879876
\tfromColumn: SalesOrderDetail.ProductID
\ttoColumn: Product.ProductID

relationship 5432072606439506404
\tfromColumn: SalesOrderDetail.SalesOrderID
\ttoColumn: SalesOrderHeader.SalesOrderID
"""


class TestParseTable:
    def test_extracts_table_name(self):
        table = parse_table(SAMPLE_TABLE_TMDL)
        assert table is not None
        assert table.name == "Address"

    def test_detects_schema(self):
        table = parse_table(SAMPLE_TABLE_TMDL)
        assert table is not None
        assert table.schema == "SalesLT"

    def test_schema_override(self):
        table = parse_table(SAMPLE_TABLE_TMDL, source_schema_override="custom")
        assert table is not None
        assert table.schema == "custom"

    def test_parses_columns(self):
        table = parse_table(SAMPLE_TABLE_TMDL)
        assert table is not None
        assert len(table.columns) == 3
        names = [c.name for c in table.columns]
        assert "AddressID" in names
        assert "City" in names
        assert "ModifiedDate" in names

    def test_maps_data_types(self):
        table = parse_table(SAMPLE_TABLE_TMDL)
        assert table is not None
        col_map = {c.name: c for c in table.columns}
        assert col_map["AddressID"].value_type == "BigInt"
        assert col_map["City"].value_type == "String"
        assert col_map["ModifiedDate"].value_type == "DateTime"

    def test_assigns_unique_ids(self):
        table = parse_table(SAMPLE_TABLE_TMDL)
        assert table is not None
        ids = [c.ontology_id for c in table.columns]
        assert len(set(ids)) == len(ids)  # all unique
        assert table.entity_type_id not in ids

    def test_returns_none_for_empty(self):
        assert parse_table("not a table") is None

    def test_returns_none_for_no_columns(self):
        assert parse_table("table Empty\n\tlineageTag: abc\n") is None

    def test_pk_single_column(self):
        """Address → pk = [AddressID]."""
        table = parse_table(SAMPLE_TABLE_TMDL)
        assert table is not None
        assert table.pk_column_names == ["AddressID"]

    def test_pk_composite_detail_table(self):
        """SalesOrderDetail → pk = [SalesOrderID, SalesOrderDetailID]."""
        table = parse_table(SAMPLE_COMPOSITE_PK_TMDL)
        assert table is not None
        assert table.pk_column_names == ["SalesOrderID", "SalesOrderDetailID"]

    def test_pk_self_referencing_fk_excluded(self):
        """ProductCategory → pk = [ProductCategoryID] only (ParentProductCategoryID excluded)."""
        table = parse_table(SAMPLE_SELF_REF_TMDL)
        assert table is not None
        assert table.pk_column_names == ["ProductCategoryID"]

    def test_pk_junction_table(self):
        """CustomerAddress → pk = [CustomerID, AddressID]."""
        table = parse_table(SAMPLE_JUNCTION_TMDL)
        assert table is not None
        assert table.pk_column_names == ["CustomerID", "AddressID"]

    def test_summarize_by_parsed(self):
        table = parse_table(SAMPLE_COMPOSITE_PK_TMDL)
        assert table is not None
        col_map = {c.name: c for c in table.columns}
        assert col_map["SalesOrderID"].summarize_by == "none"
        assert col_map["SalesOrderDetailID"].summarize_by == "count"
        assert col_map["OrderQty"].summarize_by == "sum"


class TestParseRelationships:
    def test_parses_known_tables(self):
        known = {"SalesOrderDetail", "Product", "SalesOrderHeader"}
        rels = parse_relationships(SAMPLE_REL_TMDL, known)
        assert len(rels) == 2

    def test_skips_unknown_tables(self):
        known = {"SalesOrderDetail", "Product"}  # missing SalesOrderHeader
        rels = parse_relationships(SAMPLE_REL_TMDL, known)
        assert len(rels) == 1
        assert rels[0].from_table == "SalesOrderDetail"
        assert rels[0].to_table == "Product"

    def test_relationship_fields(self):
        known = {"SalesOrderDetail", "Product", "SalesOrderHeader"}
        rels = parse_relationships(SAMPLE_REL_TMDL, known)
        rel = rels[0]
        assert rel.from_table == "SalesOrderDetail"
        assert rel.from_col == "ProductID"
        assert rel.to_table == "Product"
        assert rel.to_col == "ProductID"
