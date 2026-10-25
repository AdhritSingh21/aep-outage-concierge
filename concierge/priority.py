"""Priority locations: critical facilities (hospitals, fire stations, ambulance services) mapped to protective zones.

The organizer package ships the three USGS facility layers with coordinates. This module turns them into **counts per
zone** (the zone of the nearest primary segment) so the app and the scheduler can reason about critical loads without
ever rendering or storing a facility's name or location (CLAUDE.md rule 4). Life-support customers are already counted
per zone by topology.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .exposure import to_meters

FACILITY_KINDS = ("hospital", "fire", "ambulance")
SAFE_FACILITY_COLUMNS = ["kind", "circuit_id", "zone_id", "dist_m"]
# Assumed (A9): a facility is served from the zone of the nearest primary segment when that segment is within this distance
MAX_FACILITY_SEGMENT_M = 2000.0


def map_facilities_to_zones(facilities: pd.DataFrame, primary: pd.DataFrame, max_m: float = MAX_FACILITY_SEGMENT_M) -> pd.DataFrame:
    """Nearest primary-segment midpoint for every facility → that segment's zone (no coordinates in the result)."""
    if facilities is None or len(facilities) == 0 or primary is None or len(primary) == 0:
        return pd.DataFrame(columns=SAFE_FACILITY_COLUMNS)
    seg = primary[primary["zone_id"].notna() & primary["mid_lon"].notna() & primary["mid_lat"].notna()]
    if len(seg) == 0:
        return pd.DataFrame(columns=SAFE_FACILITY_COLUMNS)
    tree = cKDTree(to_meters(seg["mid_lon"], seg["mid_lat"]))
    d, idx = tree.query(to_meters(facilities["lon"], facilities["lat"]), k=1)
    out = pd.DataFrame({
        "kind": facilities["kind"].astype(str).to_numpy(),
        "circuit_id": seg["circuit_id"].to_numpy()[idx],
        "zone_id": seg["zone_id"].to_numpy()[idx],
        "dist_m": np.round(d, 0),
    })
    return out[out["dist_m"] <= max_m].reset_index(drop=True)


def facility_counts(zones: pd.DataFrame, fac_zone: pd.DataFrame) -> pd.DataFrame:
    """Per zone: facilities attached directly and facilities anywhere downstream (walking each facility's zone path)."""
    direct: dict[str, int] = {}
    down: dict[str, int] = {}
    paths = dict(zip(zones["zone_id"], zones["path_zone_ids"])) if "path_zone_ids" in zones.columns else {}
    for z in fac_zone["zone_id"].astype(str) if len(fac_zone) else []:
        direct[z] = direct.get(z, 0) + 1
        path = paths.get(z)
        chain = str(path).split("|") if isinstance(path, str) and path else [z]
        if z not in chain:
            chain = [z] + chain
        for x in chain:
            down[x] = down.get(x, 0) + 1
    out = pd.DataFrame({"zone_id": zones["zone_id"].astype(str)})
    out["n_facilities_direct"] = out["zone_id"].map(direct).fillna(0).astype(int)
    out["n_facilities_downstream"] = out["zone_id"].map(down).fillna(0).astype(int)
    return out.set_index("zone_id")


def facility_summary(fac_zone: pd.DataFrame, circuit_id: str | None = None) -> dict[str, int]:
    """Counts by kind (optionally for one circuit); never names or locations."""
    f = fac_zone if circuit_id is None else fac_zone[fac_zone["circuit_id"] == circuit_id]
    return {k: int((f["kind"] == k).sum()) for k in FACILITY_KINDS} if len(f) else {k: 0 for k in FACILITY_KINDS}
