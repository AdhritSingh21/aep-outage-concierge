"""Data tests for the derived zone tree, exposure conservation and damage attribution (marker: data)."""
from __future__ import annotations

import pandas as pd
import pytest

from concierge import config

pytestmark = pytest.mark.data


@pytest.fixture(scope="module")
def t():
    names = ["customers", "zones", "circuits", "primary", "transformers", "damage", "poles"]
    return {n: pd.read_parquet(config.PROCESSED / f"{n}.parquet") for n in names}


def test_every_pathed_customer_has_one_leaf_zone(t):
    c, z = t["customers"], t["zones"]
    pathed = c[c["has_path"]]
    assert pathed["leaf_zone_id"].isin(set(z["zone_id"])).all()
    assert (c["has_path"].mean()) >= 0.999
    assert c.loc[~c["has_path"], "leaf_zone_id"].isna().all()


def test_zone_tree_is_a_forest_of_breakers(t):
    z = t["zones"]
    roots = z[z["parent_zone_id"].isna()]
    assert (roots["kind"] == "breaker").all()
    assert roots["circuit_id"].is_unique and len(roots) == t["circuits"]["circuit_id"].nunique()
    assert z["depth"].max() <= 13
    # path chain always ends at the circuit's own breaker
    last = z["path_zone_ids"].str.split("|").str[-1]
    breaker_of = roots.set_index("circuit_id")["zone_id"]
    assert (last == z["circuit_id"].map(breaker_of)).all()


def test_customer_counts_are_conserved_per_circuit(t):
    c, z = t["customers"], t["zones"]
    zc = z.set_index("zone_id")["circuit_id"]
    pathed_c = c[c["has_path"]]
    # exact conservation against the circuit the path actually ends in
    by_path_circuit = pathed_c.groupby(pathed_c["leaf_zone_id"].map(zc)).size()
    direct = z.groupby("circuit_id")["n_customers_direct"].sum()
    assert (direct.reindex(by_path_circuit.index).fillna(0) == by_path_circuit).all()
    assert direct.sum() == len(pathed_c)
    breakers = z[z["kind"] == "breaker"].set_index("circuit_id")["n_customers_downstream"]
    assert (breakers.reindex(by_path_circuit.index) == by_path_circuit).all()
    # and near-exact against the customer layer's own circuit field (7 customers differ in the organizer data)
    by_own = pathed_c.groupby("circuit_id").size()
    assert (direct.reindex(by_own.index).fillna(0) - by_own).abs().sum() <= 20
    # downstream counts never exceed the parent's downstream count
    parent_down = z["parent_zone_id"].map(z.set_index("zone_id")["n_customers_downstream"])
    assert (z["n_customers_downstream"][parent_down.notna()] <= parent_down[parent_down.notna()]).all()


def test_overhead_miles_are_conserved_per_circuit(t):
    z, circ, prim = t["zones"], t["circuits"], t["primary"]
    assert prim["zone_id"].notna().mean() >= 0.99
    oh_zone = z.groupby("circuit_id")["oh_miles"].sum()
    oh_circ = circ.set_index("circuit_id")["oh_miles"]
    diff = (oh_zone.reindex(oh_circ.index).fillna(0) - oh_circ).abs()
    assert (diff <= 0.01 * oh_circ + 1e-6).mean() >= 0.99


def test_transformers_and_poles_have_zones(t):
    assert t["transformers"]["zone_id"].notna().mean() >= 0.99
    # poles are attached only when they carry primary line (shared location number or within 60 m of a primary
    # segment); service-only poles stay unassigned by design, so the bar is coverage of the primary population
    poles = t["poles"]
    assert poles["zone_id"].notna().mean() >= 0.65
    assert (poles["zone_method"] == "location").sum() >= 50_000


def test_damage_attribution(t):
    d, z = t["damage"], t["zones"]
    assert d["circuit_id"].notna().mean() >= 0.99
    assert d["zone_id"].notna().mean() >= 0.99
    zc = z.set_index("zone_id")["circuit_id"]
    ok = d["zone_id"].notna()
    assert (d.loc[ok, "zone_id"].map(zc) == d.loc[ok, "circuit_id"]).all()
    assert d.loc[ok, "attribution_dist_m"].median() < 5.0  # points lie on the lines
    ref = d[d["in_reference_storm"]]
    assert ref["circuit_id"].notna().sum() >= 1240


def test_circuit_summary_matches_customers(t):
    c, circ = t["customers"], t["circuits"]
    n = c.groupby("circuit_id").size()
    assert (circ.set_index("circuit_id")["n_customers"].reindex(n.index) == n).all()
    assert circ["n_life_support"].sum() == c["life_support"].sum()
    # circuit damage counts must equal what the attributed damage table says, and cover most of the territory
    d = t["damage"]
    ref = d[d["in_reference_storm"] & d["circuit_id"].notna()]
    on_customer_circuits = ref["circuit_id"].isin(set(circ["circuit_id"]))
    assert (~on_customer_circuits).sum() <= 20  # a few records sit on lines of circuits that serve no customers here
    per = ref[on_customer_circuits].groupby("circuit_id").size()
    assert (circ.set_index("circuit_id")["n_damage_ref"].reindex(per.index) == per).all()
    assert circ["n_damage_ref"].sum() == on_customer_circuits.sum()
    assert (circ["n_damage_ref"] > 0).sum() >= 60
