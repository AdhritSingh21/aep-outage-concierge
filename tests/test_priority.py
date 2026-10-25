"""Facility-to-zone mapping and priority weighting on synthetic data."""
from __future__ import annotations

import pandas as pd

from concierge import priority
from concierge.scheduling import Instance, Task, order_dependency_aware


def test_map_and_count_facilities():
    primary = pd.DataFrame({"seg_idx": [1, 2, 3], "zone_id": ["F1", "R", "B"], "circuit_id": ["c"] * 3,
                            "mid_lon": [-80.00, -80.01, -80.02], "mid_lat": [37.00, 37.00, 37.00]})
    fac = pd.DataFrame({"kind": ["hospital", "fire", "ambulance"], "name": ["x", "y", "z"],
                        "lon": [-80.0001, -80.0101, -79.5], "lat": [37.0001, 37.0001, 37.0]})
    mapped = priority.map_facilities_to_zones(fac, primary)
    assert list(mapped.columns) == priority.SAFE_FACILITY_COLUMNS and "name" not in mapped.columns
    assert mapped["zone_id"].tolist() == ["F1", "R"]            # the ambulance is 40+ km away → dropped
    zones = pd.DataFrame({"zone_id": ["B", "R", "F1"], "path_zone_ids": ["B", "R|B", "F1|R|B"]})
    counts = priority.facility_counts(zones, mapped)
    assert counts.loc["F1", "n_facilities_direct"] == 1 and counts.loc["R", "n_facilities_direct"] == 1
    assert counts.loc["B", "n_facilities_downstream"] == 2 and counts.loc["R", "n_facilities_downstream"] == 2 and counts.loc["F1", "n_facilities_downstream"] == 1
    assert priority.facility_summary(mapped) == {"hospital": 1, "fire": 1, "ambulance": 0}


def test_priority_weight_moves_a_small_group_with_a_hospital_forward():
    # B → {F1 (100 customers), F2 (2 customers + a hospital)}; both damaged, one crew
    parent = {"B": None, "F1": "B", "F2": "B"}
    tasks = [Task("f1", "F1", 1.0, 0), Task("f2", "F2", 1.0, 1)]
    plain = Instance("c", tasks, parent, {"F1": 100, "F2": 2}, {}, crews=1, group_facilities={"F2": 1})
    assert order_dependency_aware(plain) == ["f1", "f2"]
    weighted = Instance("c", tasks, parent, {"F1": 100, "F2": 2}, {}, crews=1, group_facilities={"F2": 1}, priority_weight=200.0)
    assert order_dependency_aware(weighted) == ["f2", "f1"]
    assert plain.deepest_damaged_weights() == {"F1": 100, "F2": 2}
    assert weighted.deepest_damaged_weights() == {"F1": 100, "F2": 202}
