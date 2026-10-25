"""Data tests: verify the organizer joins, coordinate systems and units on the processed tables (marker: data)."""
from __future__ import annotations

import pandas as pd
import pytest

from concierge import config
from concierge.etl import join_statistics

pytestmark = pytest.mark.data


@pytest.fixture(scope="module")
def tables():
    names = ["customers", "transformers", "devices", "upstream", "primary", "damage", "facilities", "labor_cu",
             "labor_adders", "labor_meta", "poles"]
    return {n: pd.read_parquet(config.PROCESSED / f"{n}.parquet") for n in names}


@pytest.fixture(scope="module")
def stats(tables):
    return join_statistics(tables)


def test_premise_path_join(stats):
    assert stats["customers_with_path_pct"] >= 99.9
    assert stats["path_xfmr_loc_matches_customer_pct"] >= 99.9
    assert stats["path_circuit_matches_customer_pct"] >= 99.9


def test_transformer_join(stats):
    assert stats["customer_xfmr_in_transformer_layer_pct"] >= 99.9
    assert stats["path_xfmr_in_transformer_layer_pct"] >= 99.9
    assert stats["transformer_upstream_loc_matches_path_pct"] >= 99.0


def test_protective_device_tokens(stats):
    assert stats["fuse_tokens_id_in_layer_pct"] >= 98.0
    assert stats["recloser_tokens_loc_in_layer_pct"] >= 99.0
    # reclosers do NOT match by id (different source table) — guard against anyone "fixing" the join the wrong way
    assert stats["recloser_tokens_id_in_layer_pct"] < 5.0


def test_length_units_are_feet(stats):
    assert 0.95 <= stats["report_len_over_geodesic_ft_median"] <= 1.05


def test_coordinates(tables, stats):
    cust = tables["customers"]
    assert cust["lon"].between(-80.4, -79.5).all() and cust["lat"].between(36.9, 37.6).all()
    assert stats["facilities_in_clip_bbox_pct"] >= 99.0
    # service-point fields agree with the shape geometry to within ~100 m for nearly everyone
    d = (cust["lon"] - cust["service_lon"]).abs() + (cust["lat"] - cust["service_lat"]).abs()
    assert (d < 1e-3).mean() > 0.99


def test_damage_dates(tables):
    dmg = tables["damage"]
    assert dmg["in_reference_storm"].sum() > 1000
    assert dmg.loc[~dmg["repaired"], "repaired_date"].isna().all()
    assert dmg["repaired_date_was_placeholder"].sum() > 0
    assert not (dmg["repaired_date"].dt.strftime("%Y-%m-%d") == config.REPAIR_DATE_PLACEHOLDER).any()


def test_no_pii_columns(tables):
    for name, df in tables.items():
        for col in df.columns:
            assert col.lower() not in config.PII_FIELDS, f"{name}.{col}"


def test_labor_workbook(tables):
    cu = tables["labor_cu"]
    cats = set(cu["category"].str.lower())
    for needed in ("pole", "transformer", "fuse cutout", "recloser", "switch", "service", "crossarm", "connector"):
        assert any(needed in c for c in cats), needed
    assert cu["install_hours"].notna().all()
    assert tables["labor_meta"]["ilm"].iloc[0] == 4.5
    assert len(tables["labor_adders"]) >= 10
