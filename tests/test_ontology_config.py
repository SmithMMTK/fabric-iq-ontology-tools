"""Tests for ontology_config module."""

import json
import tempfile
from pathlib import Path

from fabric_iq.models import Column, Table, Relationship
from fabric_iq.ontology_config import (
    OntologyConfig,
    EntityConfig,
    RelationshipConfig,
    apply_pk_overrides,
    apply_relationship_overrides,
    generate_config,
    load_config,
    save_config,
)


def _make_table(name: str, cols: list[str], pk: list[str] | None = None) -> Table:
    """Helper to build a Table with named columns."""
    columns = [
        Column(name=c, data_type="int64", value_type="BigInt", ontology_id=f"{name}_{c}")
        for c in cols
    ]
    return Table(
        name=name,
        schema="dbo",
        columns=columns,
        pk_column_names=pk or [],
        entity_type_id=f"eid_{name}",
    )


# ---------------------------------------------------------------------------
# load_config / save_config round-trip
# ---------------------------------------------------------------------------


class TestLoadSaveConfig:
    def test_round_trip(self, tmp_path: Path):
        cfg = OntologyConfig(
            entities={"Foo": EntityConfig(pk=["A", "B"])},
            relationships=[
                RelationshipConfig("X", "xc", "Y", "yc"),
            ],
        )
        p = tmp_path / "cfg.json"
        save_config(cfg, p)
        loaded = load_config(p)
        assert loaded.entities["Foo"].pk == ["A", "B"]
        assert len(loaded.relationships) == 1
        assert loaded.relationships[0].from_table == "X"
        assert loaded.relationships[0].to_column == "yc"

    def test_load_entities_only(self, tmp_path: Path):
        p = tmp_path / "pk_only.json"
        p.write_text(json.dumps({"entities": {"T1": {"pk": ["id"]}}}))
        loaded = load_config(p)
        assert loaded.entities["T1"].pk == ["id"]
        assert loaded.relationships is None  # not present → use TMDL

    def test_load_empty_relationships(self, tmp_path: Path):
        p = tmp_path / "no_rels.json"
        p.write_text(json.dumps({"entities": {}, "relationships": []}))
        loaded = load_config(p)
        assert loaded.relationships == []  # explicit empty list


# ---------------------------------------------------------------------------
# apply_pk_overrides
# ---------------------------------------------------------------------------


class TestApplyPKOverrides:
    def test_overrides_specific_table(self):
        tables = {
            "A": _make_table("A", ["X", "Y", "Z"], pk=["X"]),
            "B": _make_table("B", ["P", "Q"], pk=["P"]),
        }
        cfg = OntologyConfig(entities={"A": EntityConfig(pk=["X", "Y"])})
        apply_pk_overrides(tables, cfg)
        assert tables["A"].pk_column_names == ["X", "Y"]
        assert tables["B"].pk_column_names == ["P"]  # unchanged

    def test_unknown_table_skipped(self):
        tables = {"A": _make_table("A", ["X"], pk=["X"])}
        cfg = OntologyConfig(entities={"ZZZ": EntityConfig(pk=["Z"])})
        apply_pk_overrides(tables, cfg)  # should not raise
        assert tables["A"].pk_column_names == ["X"]

    def test_bad_column_skipped(self):
        tables = {"A": _make_table("A", ["X", "Y"], pk=["X"])}
        cfg = OntologyConfig(entities={"A": EntityConfig(pk=["X", "MISSING"])})
        apply_pk_overrides(tables, cfg)
        assert tables["A"].pk_column_names == ["X"]  # unchanged (bad col)


# ---------------------------------------------------------------------------
# apply_relationship_overrides
# ---------------------------------------------------------------------------


class TestApplyRelationshipOverrides:
    def _tables(self):
        return {
            "Order": _make_table("Order", ["OrderID"]),
            "Detail": _make_table("Detail", ["DetailID", "OrderID"]),
        }

    def test_none_returns_tmdl(self):
        tmdl = [Relationship("r1", "Detail", "OrderID", "Order", "OrderID")]
        cfg = OntologyConfig()  # relationships=None
        result = apply_relationship_overrides(self._tables(), tmdl, cfg)
        assert len(result) == 1
        assert result[0].rel_id == "r1"

    def test_config_replaces_tmdl(self):
        tmdl = [Relationship("r1", "Detail", "OrderID", "Order", "OrderID")]
        cfg = OntologyConfig(
            relationships=[
                RelationshipConfig("Detail", "DetailID", "Order", "OrderID"),
            ],
        )
        result = apply_relationship_overrides(self._tables(), tmdl, cfg)
        assert len(result) == 1
        assert result[0].from_col == "DetailID"  # replaced

    def test_empty_config_clears_rels(self):
        tmdl = [Relationship("r1", "Detail", "OrderID", "Order", "OrderID")]
        cfg = OntologyConfig(relationships=[])
        result = apply_relationship_overrides(self._tables(), tmdl, cfg)
        assert result == []

    def test_unknown_table_skipped(self):
        cfg = OntologyConfig(
            relationships=[
                RelationshipConfig("Unknown", "col", "Order", "OrderID"),
            ],
        )
        result = apply_relationship_overrides(self._tables(), [], cfg)
        assert result == []


# ---------------------------------------------------------------------------
# generate_config
# ---------------------------------------------------------------------------


class TestGenerateConfig:
    def test_captures_pks_and_rels(self):
        tables = {
            "A": _make_table("A", ["AID", "Val"], pk=["AID"]),
            "B": _make_table("B", ["BID", "AID"], pk=["BID"]),
        }
        rels = [Relationship("r1", "B", "AID", "A", "AID")]
        cfg = generate_config(tables, rels)
        assert cfg.entities["A"].pk == ["AID"]
        assert cfg.entities["B"].pk == ["BID"]
        assert len(cfg.relationships) == 1
        assert cfg.relationships[0].from_table == "B"
        assert cfg.relationships[0].to_column == "AID"

    def test_infers_pk_for_fact_table(self):
        """Tables with no explicit PK should get inferred PKs from FK columns."""
        tables = {
            "Dim": _make_table("Dim", ["DimID", "Name"], pk=["DimID"]),
            "Fact": _make_table("Fact", ["DimID", "Amount"]),  # no PK
        }
        rels = [Relationship("r1", "Fact", "DimID", "Dim", "DimID")]
        cfg = generate_config(tables, rels)
        assert cfg.entities["Dim"].pk == ["DimID"]
        # Fact should have inferred PK from FK column
        assert "DimID" in cfg.entities["Fact"].pk
        assert len(cfg.entities["Fact"].pk) > 0


# ---------------------------------------------------------------------------
# Lakehouse mapping round-trip
# ---------------------------------------------------------------------------


class TestLakehouseMappingConfig:
    def test_round_trip_with_mappings(self, tmp_path: Path):
        cfg = OntologyConfig(
            entities={
                "Orders": EntityConfig(
                    pk=["OrderID"],
                    lakehouse_table="orders_raw",
                    lakehouse_schema="staging",
                    column_mappings={"OrderID": "order_id", "Total": "total_amount"},
                ),
                "Items": EntityConfig(pk=["ItemID"]),  # no mappings
            },
        )
        p = tmp_path / "cfg.json"
        save_config(cfg, p)
        loaded = load_config(p)

        assert loaded.entities["Orders"].lakehouse_table == "orders_raw"
        assert loaded.entities["Orders"].lakehouse_schema == "staging"
        assert loaded.entities["Orders"].column_mappings == {
            "OrderID": "order_id",
            "Total": "total_amount",
        }
        # Items should have empty defaults
        assert loaded.entities["Items"].lakehouse_table == ""
        assert loaded.entities["Items"].column_mappings == {}

    def test_load_partial_mappings(self, tmp_path: Path):
        """Config with only lakehouse_table, no column_mappings."""
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({
            "entities": {
                "T1": {
                    "pk": ["id"],
                    "lakehouse_table": "t1_lh",
                }
            }
        }))
        loaded = load_config(p)
        assert loaded.entities["T1"].lakehouse_table == "t1_lh"
        assert loaded.entities["T1"].lakehouse_schema == ""
        assert loaded.entities["T1"].column_mappings == {}

    def test_save_omits_empty_mappings(self, tmp_path: Path):
        """Empty lakehouse_table / column_mappings should not appear in JSON."""
        cfg = OntologyConfig(
            entities={"X": EntityConfig(pk=["A"])},
        )
        p = tmp_path / "cfg.json"
        save_config(cfg, p)
        raw = json.loads(p.read_text())
        assert "lakehouse_table" not in raw["entities"]["X"]
        assert "lakehouse_schema" not in raw["entities"]["X"]
        assert "column_mappings" not in raw["entities"]["X"]
