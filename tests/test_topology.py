"""Unit tests for the zone tree and the LCA helper on a synthetic upstream table."""
from __future__ import annotations

import pandas as pd

from concierge.topology import (attach_customer_zones, build_zone_tree, lowest_common_ancestor, transformer_zones,
                                zone_customer_counts)

# zone ids are kind prefix + equipment id: fuses F1/F2/F3, recloser R4, breaker B5


def make_upstream():
    # circuit 0000001: breaker B5; recloser R4 under B5; fuses F1, F2 under R4; fuse F3 directly under B5
    rows = []

    def path(premise, tokens):
        for pos, (table, eq_id, eq_type, loc) in enumerate(tokens):
            rows.append((premise, pos, table, eq_id, eq_type, loc))

    xf = lambda i: ("eo_power_xfrmr_inst", f"9{i}", "Distribution", f"X{i}")
    step = ("eo_power_xfrmr_inst", "77", "Step", "XS")
    F1 = ("eo_isolating_eqpt_inst", "1", "Fuse", "LF1")
    F2 = ("eo_isolating_eqpt_inst", "2", "Fuse", "LF2")
    F3 = ("eo_isolating_eqpt_inst", "3", "Fuse", "LF3")
    R4 = ("eo_isolating_eqpt_inst", "4", "Recloser (Hydraulic)", "LR4")
    B5 = ("eo_circuit", "5", "Circuit Breaker", "0000001")
    path("000000001", [xf(1), F1, R4, B5])
    path("000000002", [xf(1), F1, R4, B5])
    path("000000003", [xf(2), step, F2, R4, B5])
    path("000000004", [xf(3), F3, B5])
    path("000000005", [xf(4), R4, B5])  # served directly from the recloser zone
    return pd.DataFrame(rows, columns=["premise_nu", "pos", "table", "eq_id", "eq_type", "loc"])


def make_customers():
    return pd.DataFrame({
        "premise_nu": ["000000001", "000000002", "000000003", "000000004", "000000005", "000000006"],
        "xfmr_loc": ["X1", "X1", "X2", "X3", "X4", "X9"],
        "circuit_id": ["0000001"] * 6,
        "life_support": [False, True, False, False, False, False],
    })


def test_zone_tree_structure():
    zones, leaf, stats = build_zone_tree(make_upstream())
    z = zones.set_index("zone_id")
    assert set(z.index) == {"F1", "F2", "F3", "R4", "B5"}
    assert z.loc["F1", "parent_zone_id"] == "R4" and z.loc["F2", "parent_zone_id"] == "R4"
    assert z.loc["R4", "parent_zone_id"] == "B5" and z.loc["F3", "parent_zone_id"] == "B5"
    assert pd.isna(z.loc["B5", "parent_zone_id"]) and z.loc["B5", "depth"] == 0
    assert z.loc["F1", "depth"] == 2 and z.loc["F1", "path_zone_ids"] == "F1|R4|B5"
    assert (z["circuit_id"] == "0000001").all()
    assert z.loc["R4", "kind"] == "recloser" and z.loc["B5", "kind"] == "breaker"
    assert stats["zones_with_conflicting_parents"] == 0 and stats["non_breaker_roots"] == 0
    assert dict(zip(leaf["premise_nu"], leaf["leaf_zone_id"])) == {
        "000000001": "F1", "000000002": "F1", "000000003": "F2", "000000004": "F3", "000000005": "R4"}


def test_customer_counts_count_each_premise_once():
    zones, leaf, _ = build_zone_tree(make_upstream())
    cust = attach_customer_zones(make_customers(), zones, leaf)
    assert cust["has_path"].tolist() == [True] * 5 + [False]
    z = zone_customer_counts(zones, cust).set_index("zone_id")
    assert z.loc["F1", "n_customers_direct"] == 2 and z.loc["R4", "n_customers_direct"] == 1
    assert z.loc["R4", "n_customers_downstream"] == 4  # F1 (2) + F2 (1) + direct (1)
    assert z.loc["B5", "n_customers_downstream"] == 5  # every pathed customer exactly once
    assert z["n_customers_direct"].sum() == 5
    assert z.loc["R4", "n_life_support_downstream"] == 1 and z.loc["F3", "n_life_support_downstream"] == 0


def test_transformer_zone_fallbacks():
    zones, leaf, _ = build_zone_tree(make_upstream())
    cust = attach_customer_zones(make_customers(), zones, leaf)
    xf = pd.DataFrame({"loc": ["X1", "X4", "X7", "X8"], "upstream_loc": ["LF1", "LR4", "LF2", "nowhere"],
                       "circuit_id": ["0000001"] * 4})
    t = transformer_zones(xf, cust, zones)
    assert t["zone_id"].tolist() == ["F1", "R4", "F2", "B5"]
    assert t["zone_source"].tolist() == ["customer_paths", "customer_paths", "upstream_loc", "circuit_breaker"]


def test_lowest_common_ancestor():
    chains = {"F1": ["F1", "R4", "B5"], "F2": ["F2", "R4", "B5"], "F3": ["F3", "B5"], "R4": ["R4", "B5"], "B5": ["B5"]}
    assert lowest_common_ancestor(["F1", "F2"], chains) == "R4"
    assert lowest_common_ancestor(["F1", "R4"], chains) == "R4"
    assert lowest_common_ancestor(["F1", "F3"], chains) == "B5"
    assert lowest_common_ancestor(["F1"], chains) == "F1"
    assert lowest_common_ancestor(["nope"], chains) is None
