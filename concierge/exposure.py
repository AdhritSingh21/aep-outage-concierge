"""Exposure per zone/circuit and damage attribution (Observed geometry + topology; attribution rules are Assumed).

- Primary segments are assigned to protective zones by cutting the structure graph at device locations and voting
  with the transformers inside each piece; pieces with no transformer use the lowest common ancestor of their
  boundary devices; fragments with neither use the nearest transformer's zone.
- Poles are attached to segments by shared location number, else by proximity.
- Damage records are attributed to a circuit via the (station, circuit label) key when it is unambiguous, otherwise
  via the nearest primary segment among the candidate circuits; then to a zone via the nearest segment (or the
  nearest device of the damaged kind when it is a fuse/recloser/switch).
"""
from __future__ import annotations

import math

import networkx as nx
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import config
from .topology import lowest_common_ancestor

M_PER_DEG_LAT = 111_320.0
REF_LAT = 37.2  # Observed: centre latitude of the clip


def to_meters(lon, lat):
    """Local equirectangular projection (metres) good enough for nearest-neighbour search inside the clip."""
    lon = np.asarray(lon, float)
    lat = np.asarray(lat, float)
    return np.column_stack([lon * M_PER_DEG_LAT * math.cos(math.radians(REF_LAT)), lat * M_PER_DEG_LAT])


# ----------------------------------------------------------------------------- circuit id canonicalisation
def canonical_circuit_ids(df: pd.DataFrame, transformers: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Make a layer's circuit_id consistent with the customer/transformer layers.

    Observed: the anonymised ``circuit_id`` differs between GIS layers for ~14 circuits, while the
    ``(station, cir_label)`` key is unambiguous in the transformer layer and agrees with the customer layer.
    Rows whose key exists in the transformer layer get that circuit id; the original is kept in
    ``circuit_id_layer``. Returns (table, number of rows remapped).
    """
    xmap = (transformers[transformers["station"].ne("") & transformers["cir_label"].ne("")]
            .groupby(["station", "cir_label"])["circuit_id"].agg(lambda s: s.mode().iloc[0]))
    key = pd.MultiIndex.from_arrays([df["station"], df["cir_label"]])
    mapped = xmap.reindex(key).to_numpy()
    out = df.copy()
    if "circuit_id_layer" in out.columns:  # idempotent re-run
        out["circuit_id"] = out["circuit_id_layer"]
    out["circuit_id_layer"] = out["circuit_id"]
    use = pd.notna(mapped) & (mapped != out["circuit_id"].to_numpy())
    out.loc[use, "circuit_id"] = mapped[use]
    return out, int(use.sum())


# ----------------------------------------------------------------------------- segment pieces
def build_pieces(primary: pd.DataFrame, vertices: pd.DataFrame) -> tuple[pd.DataFrame, cKDTree]:
    """Consecutive-vertex pieces of every primary segment with a KD-tree on piece midpoints (metres)."""
    v = vertices.sort_values(["seg_idx", "vertex_idx"])
    same = v["seg_idx"].to_numpy()[1:] == v["seg_idx"].to_numpy()[:-1]
    a = v.iloc[:-1][same]
    b = v.iloc[1:][same]
    pieces = pd.DataFrame({
        "seg_idx": a["seg_idx"].to_numpy(),
        "ax": a["lon"].to_numpy(), "ay": a["lat"].to_numpy(), "bx": b["lon"].to_numpy(), "by": b["lat"].to_numpy(),
    })
    am = to_meters(pieces["ax"], pieces["ay"])
    bm = to_meters(pieces["bx"], pieces["by"])
    pieces["amx"], pieces["amy"], pieces["bmx"], pieces["bmy"] = am[:, 0], am[:, 1], bm[:, 0], bm[:, 1]
    mid = (am + bm) / 2
    tree = cKDTree(mid)
    return pieces.reset_index(drop=True), tree


def nearest_segments(pieces: pd.DataFrame, tree: cKDTree, lon, lat, k: int = 24) -> tuple[np.ndarray, np.ndarray]:
    """For each query point return (k candidate seg_idx, exact perpendicular distance in metres), sorted by distance."""
    p = to_meters(lon, lat)
    k = min(k, len(pieces))
    _, idx = tree.query(p, k=k)
    idx = np.atleast_2d(idx)
    A = np.stack([pieces["amx"].to_numpy()[idx], pieces["amy"].to_numpy()[idx]], axis=-1)
    B = np.stack([pieces["bmx"].to_numpy()[idx], pieces["bmy"].to_numpy()[idx]], axis=-1)
    P = p[:, None, :]
    AB = B - A
    denom = (AB ** 2).sum(-1)
    t = np.clip(((P - A) * AB).sum(-1) / np.where(denom == 0, 1, denom), 0, 1)
    proj = A + t[..., None] * AB
    d = np.sqrt(((P - proj) ** 2).sum(-1))
    order = np.argsort(d, axis=1)
    seg = pieces["seg_idx"].to_numpy()[idx]
    return np.take_along_axis(seg, order, 1), np.take_along_axis(d, order, 1)


# ----------------------------------------------------------------------------- segment → zone
def assign_segment_zones(primary: pd.DataFrame, zones: pd.DataFrame, transformers: pd.DataFrame,
                         pieces: pd.DataFrame, tree: cKDTree) -> tuple[pd.DataFrame, dict]:
    chains = {z: p.split("|") for z, p in zip(zones["zone_id"], zones["path_zone_ids"])}
    zone_circuit = zones.set_index("zone_id")["circuit_id"].to_dict()
    cut_by_circuit: dict[str, dict[str, str]] = {}
    for z, loc, circ, kind in zip(zones["zone_id"], zones["loc"], zones["circuit_id"], zones["kind"]):
        if kind != "breaker" and loc:
            cut_by_circuit.setdefault(circ, {}).setdefault(loc, z)
    breaker_by_circuit = zones[zones["kind"] == "breaker"].drop_duplicates("circuit_id").set_index("circuit_id")["zone_id"].to_dict()
    xf = transformers[transformers["zone_id"].notna()].reset_index(drop=True)
    xf_zone_by_loc: dict[str, str] = xf.groupby("loc")["zone_id"].first().to_dict()
    xf_m = to_meters(xf["lon"], xf["lat"])
    xf_tree_by_circuit: dict[str, tuple[cKDTree, np.ndarray]] = {}
    for circ, g in xf.groupby("circuit_id"):
        xf_tree_by_circuit[circ] = (cKDTree(xf_m[g.index.to_numpy()]), g["zone_id"].to_numpy())

    method = np.full(len(primary), "", dtype=object)
    zone = np.full(len(primary), None, dtype=object)
    counts = {"transformer_vote": 0, "boundary_lca": 0, "device_link": 0, "nearest_transformer": 0, "breaker_default": 0, "unassigned": 0}

    for circ, seg in primary.groupby("circuit_id"):
        cuts = cut_by_circuit.get(circ, {})
        breaker = breaker_by_circuit.get(circ)
        G = nx.Graph()
        ok = (seg["loc_a"] != "") & (seg["loc_b"] != "")
        for i, a, b in zip(seg.index[ok], seg.loc[ok, "loc_a"], seg.loc[ok, "loc_b"]):
            if a == b:
                continue
            G.add_edge(a, b)
            G[a][b].setdefault("segs", []).append(i)
        H = G.copy()
        H.remove_nodes_from([n for n in cuts if n in H])
        comp_of: dict[str, int] = {}
        comp_zone: dict[int, tuple[str | None, str]] = {}
        for cid, comp in enumerate(nx.connected_components(H)):
            for n in comp:
                comp_of[n] = cid
            # 1) transformers inside the component vote
            votes = [xf_zone_by_loc[n] for n in comp if n in xf_zone_by_loc]
            if votes:
                comp_zone[cid] = (pd.Series(votes).mode().iloc[0], "transformer_vote")
                continue
            # 2) boundary devices → LCA
            boundary = {cuts[nb] for n in comp for nb in G.neighbors(n) if nb in cuts}
            lca = lowest_common_ancestor(sorted(boundary), chains) if boundary else None
            if lca is not None:
                comp_zone[cid] = (lca, "boundary_lca")
                continue
            # 3) nearest transformer with a known zone in this circuit
            comp_zone[cid] = (None, "nearest_transformer")

        for i, a, b in zip(seg.index, seg["loc_a"], seg["loc_b"]):
            z, m = None, None
            if a in cuts and b in cuts and a != b:
                z = lowest_common_ancestor([cuts[a], cuts[b]], chains)
                m = "device_link" if z else None
            else:
                node = a if a in comp_of else (b if b in comp_of else None)
                if node is not None:
                    z, m = comp_zone[comp_of[node]]
            if z is None:
                m = "nearest_transformer"
                if circ in xf_tree_by_circuit:
                    t, zids = xf_tree_by_circuit[circ]
                    d, j = t.query(to_meters([primary.at[i, "mid_lon"]], [primary.at[i, "mid_lat"]])[0])
                    z = zids[j]
                if z is None:
                    z, m = breaker, "breaker_default"
            if z is None:
                m = "unassigned"
            zone[i] = z
            method[i] = m
            counts[m] += 1

    out = primary.copy()
    out["zone_id"] = zone
    out["zone_method"] = method
    # a zone from another circuit would be a topology error, not an attribution: drop it and count it
    zcirc = out["zone_id"].map(zone_circuit)
    cross = out["zone_id"].notna() & (zcirc != out["circuit_id"])
    counts["dropped_cross_circuit"] = int(cross.sum())
    out.loc[cross, "zone_id"] = None
    # segments that could not be placed (typically at circuit boundaries / tie points) default to the breaker zone
    # of their own circuit so that circuit mileage is conserved; circuits without customers stay unassigned
    missing = out["zone_id"].isna()
    fill = out.loc[missing, "circuit_id"].map(breaker_by_circuit)
    out.loc[missing & fill.notna(), "zone_id"] = fill[fill.notna()]
    out.loc[missing & fill.notna(), "zone_method"] = "breaker_default"
    counts["breaker_default"] = int((missing & fill.notna()).sum())
    out.loc[out["zone_id"].isna(), "zone_method"] = "unassigned"
    counts["unassigned"] = int(out["zone_id"].isna().sum())
    return out, counts


# ----------------------------------------------------------------------------- poles → zone
def assign_pole_zones(poles: pd.DataFrame, primary: pd.DataFrame, pieces: pd.DataFrame, tree: cKDTree,
                      max_m: float = 60.0) -> tuple[pd.DataFrame, dict]:
    by_loc: dict[str, tuple[str, str]] = {}
    for loc_a, loc_b, circ, z in zip(primary["loc_a"], primary["loc_b"], primary["circuit_id"], primary["zone_id"]):
        if z is None:
            continue
        for loc in (loc_a, loc_b):
            if loc and loc not in by_loc:
                by_loc[loc] = (circ, z)
    p = poles.copy()
    hit = p["loc"].map(by_loc)
    p["circuit_id"] = hit.map(lambda t: t[0] if isinstance(t, tuple) else None)
    p["zone_id"] = hit.map(lambda t: t[1] if isinstance(t, tuple) else None)
    p["zone_method"] = np.where(p["zone_id"].notna(), "location", "")
    miss = p["zone_id"].isna() & p["lon"].notna()
    if miss.any():
        seg, d = nearest_segments(pieces, tree, p.loc[miss, "lon"], p.loc[miss, "lat"], k=8)
        near = seg[:, 0]
        ok = d[:, 0] <= max_m
        idx = p.index[miss]
        p.loc[idx[ok], "circuit_id"] = primary["circuit_id"].to_numpy()[near[ok]]
        p.loc[idx[ok], "zone_id"] = primary["zone_id"].to_numpy()[near[ok]]
        p.loc[idx[ok], "zone_method"] = "nearest_segment"
    p.loc[p["zone_id"].isna(), "zone_method"] = "unassigned"
    stats = p["zone_method"].value_counts().to_dict()
    return p, stats


# ----------------------------------------------------------------------------- damage → circuit → zone
def circuit_key_map(*tables: pd.DataFrame) -> dict[tuple[str, str], set[str]]:
    m: dict[tuple[str, str], set[str]] = {}
    for t in tables:
        for st, lab, circ in zip(t["station"], t["cir_label"], t["circuit_id"]):
            if st and lab and circ:
                m.setdefault((st, lab), set()).add(circ)
    return m


def attribute_damage(damage: pd.DataFrame, primary: pd.DataFrame, pieces: pd.DataFrame, tree: cKDTree,
                     keymap: dict[tuple[str, str], set[str]], devices: pd.DataFrame, zones: pd.DataFrame,
                     max_key_m: float = 300.0, max_nearest_m: float = 500.0, device_snap_m: float = 40.0) -> tuple[pd.DataFrame, dict]:
    d = damage.copy()
    seg_circ = primary["circuit_id"].to_numpy()
    seg_zone = primary["zone_id"].to_numpy()
    cand_seg, cand_d = nearest_segments(pieces, tree, d["lon"], d["lat"], k=40)
    circuit = np.full(len(d), None, dtype=object)
    zone = np.full(len(d), None, dtype=object)
    method = np.full(len(d), "unresolved", dtype=object)
    dist = np.full(len(d), np.nan)
    for i, (st, lab) in enumerate(zip(d["station"], d["cir_label"])):
        cands = keymap.get((st, lab), set())
        segs, ds = cand_seg[i], cand_d[i]
        if len(cands) == 1:
            circuit[i] = next(iter(cands))
            method[i] = "key"
            same = [j for j in range(len(segs)) if seg_circ[segs[j]] == circuit[i]]
            if same and ds[same[0]] <= max_key_m:
                j = same[0]
            elif ds[0] <= max_nearest_m:
                # the record's circuit label disagrees with the line the point sits on: geometry wins
                j = 0
                circuit[i] = seg_circ[segs[0]]
                method[i] = "nearest_overrides_key"
            else:
                j = None
        else:
            pool = [j for j in range(len(segs)) if seg_circ[segs[j]] in cands] if cands else list(range(len(segs)))
            j = pool[0] if pool and ds[pool[0]] <= max_nearest_m else None
            if j is not None:
                circuit[i] = seg_circ[segs[j]]
                method[i] = "key+nearest" if cands else "nearest_only"
        if j is not None:
            zone[i] = seg_zone[segs[j]]
            dist[i] = ds[j]
    d["circuit_id"] = circuit
    d["zone_id"] = zone
    d["attribution_method"] = method
    d["attribution_dist_m"] = dist

    # device-kind snapping: repairing a damaged fuse/recloser/switch restores *its* zone
    kind_for = {"Fuse Bank": "fuse", "Recloser Bank": "recloser"}
    zone_by_loc_kind = zones[zones["kind"] != "breaker"].drop_duplicates(["loc", "kind"]).set_index(["loc", "kind"])["zone_id"]
    snapped = 0
    for asset, kind in kind_for.items():
        rows = d.index[(d["asset_type"] == asset) & d["circuit_id"].notna()]
        dev = devices[(devices["kind"] == kind)]
        if not len(rows) or not len(dev):
            continue
        t = cKDTree(to_meters(dev["lon"], dev["lat"]))
        dd, jj = t.query(to_meters(d.loc[rows, "lon"], d.loc[rows, "lat"]))
        for r, dist_m, j in zip(rows, dd, jj):
            if dist_m <= device_snap_m:
                z = zone_by_loc_kind.get((dev["loc"].iloc[j], kind))
                if isinstance(z, str) and z.split("|")[0] and zones.loc[zones["zone_id"] == z, "circuit_id"].iloc[0] == d.at[r, "circuit_id"]:
                    d.at[r, "zone_id"] = z
                    d.at[r, "attribution_method"] = d.at[r, "attribution_method"] + "+device_snap"
                    snapped += 1
    stats = {
        "damage_rows": int(len(d)),
        "damage_with_circuit_pct": round(100 * float(d["circuit_id"].notna().mean()), 2),
        "damage_with_zone_pct": round(100 * float(d["zone_id"].notna().mean()), 2),
        "damage_attribution_methods": d["attribution_method"].value_counts().to_dict(),
        "damage_device_snapped": snapped,
        "damage_unresolved_keys": sorted({f"{s}/{l}" for s, l, c in zip(d["station"], d["cir_label"], d["circuit_id"]) if c is None}),
        "damage_dist_median_m": round(float(np.nanmedian(dist)), 1) if np.isfinite(dist).any() else None,
    }
    return d, stats


# ----------------------------------------------------------------------------- aggregation
def zone_exposure(zones: pd.DataFrame, primary: pd.DataFrame, poles: pd.DataFrame, transformers: pd.DataFrame,
                  damage: pd.DataFrame) -> pd.DataFrame:
    z = zones.copy()
    mi = primary.assign(mi=primary["length_ft"] / config.FEET_PER_MILE)
    oh = mi[mi["mounting"] == "Overhead"].groupby("zone_id")["mi"].sum()
    ug = mi[mi["mounting"] != "Overhead"].groupby("zone_id")["mi"].sum()
    z["oh_miles"] = z["zone_id"].map(oh).fillna(0.0)
    z["ug_miles"] = z["zone_id"].map(ug).fillna(0.0)
    z["n_segments"] = z["zone_id"].map(primary.groupby("zone_id").size()).fillna(0).astype(int)
    pk = poles[poles["zone_id"].notna()]
    z["n_poles"] = z["zone_id"].map(pk.groupby("zone_id").size()).fillna(0).astype(int)
    yr = pk.dropna(subset=["install_year"]).groupby("zone_id")["install_year"].mean()
    z["pole_year_mean"] = z["zone_id"].map(yr)
    z["pole_year_known_pct"] = z["zone_id"].map(100 * pk.groupby("zone_id")["install_year"].apply(lambda s: s.notna().mean())).fillna(0.0)
    tx = transformers[transformers["zone_id"].notna()]
    z["n_transformers"] = z["zone_id"].map(tx.groupby("zone_id").size()).fillna(0).astype(int)
    z["n_transformers_overhead"] = z["zone_id"].map(tx[tx["mounting"] == "Overhead"].groupby("zone_id").size()).fillna(0).astype(int)
    dm = damage[damage["zone_id"].notna()]
    ref = dm[dm["in_reference_storm"]]
    z["n_damage_total"] = z["zone_id"].map(dm.groupby("zone_id").size()).fillna(0).astype(int)
    z["n_damage_ref"] = z["zone_id"].map(ref.groupby("zone_id").size()).fillna(0).astype(int)
    return z


def circuit_summary(customers: pd.DataFrame, zones: pd.DataFrame, primary: pd.DataFrame, poles: pd.DataFrame,
                    transformers: pd.DataFrame, damage: pd.DataFrame) -> pd.DataFrame:
    c = customers.groupby("circuit_id").agg(n_customers=("premise_nu", "size"), n_with_path=("has_path", "sum"),
                                            n_life_support=("life_support", "sum"),
                                            n_overhead_service=("service_kind", lambda s: int((s == "Overhead").sum())),
                                            city=("city", lambda s: s.mode().iloc[0] if len(s) else "")).reset_index()
    mi = primary.assign(mi=primary["length_ft"] / config.FEET_PER_MILE)
    g = mi.groupby("circuit_id")
    c["oh_miles"] = c["circuit_id"].map(mi[mi["mounting"] == "Overhead"].groupby("circuit_id")["mi"].sum()).fillna(0.0)
    c["ug_miles"] = c["circuit_id"].map(mi[mi["mounting"] != "Overhead"].groupby("circuit_id")["mi"].sum()).fillna(0.0)
    c["lateral_miles"] = c["circuit_id"].map(mi[mi["usage"] == "Lateral"].groupby("circuit_id")["mi"].sum()).fillna(0.0)
    c["n_segments"] = c["circuit_id"].map(g.size()).fillna(0).astype(int)
    c["n_zones"] = c["circuit_id"].map(zones.groupby("circuit_id").size()).fillna(0).astype(int)
    c["n_transformers"] = c["circuit_id"].map(transformers.groupby("circuit_id").size()).fillna(0).astype(int)
    pk = poles[poles["circuit_id"].notna()]
    c["n_poles"] = c["circuit_id"].map(pk.groupby("circuit_id").size()).fillna(0).astype(int)
    c["pole_year_mean"] = c["circuit_id"].map(pk.dropna(subset=["install_year"]).groupby("circuit_id")["install_year"].mean())
    c["pole_year_known_pct"] = c["circuit_id"].map(100 * pk.groupby("circuit_id")["install_year"].apply(lambda s: s.notna().mean())).fillna(0.0)
    dm = damage[damage["circuit_id"].notna()]
    ref = dm[dm["in_reference_storm"]]
    wire_types = {"primary", "secondary"}
    c["n_damage_total"] = c["circuit_id"].map(dm.groupby("circuit_id").size()).fillna(0).astype(int)
    c["n_damage_ref"] = c["circuit_id"].map(ref.groupby("circuit_id").size()).fillna(0).astype(int)
    c["n_damage_ref_wire"] = c["circuit_id"].map(ref[ref["asset_type"].isin(wire_types)].groupby("circuit_id").size()).fillna(0).astype(int)
    c["n_damage_ref_pole"] = c["circuit_id"].map(ref[ref["asset_type"] == "Pole"].groupby("circuit_id").size()).fillna(0).astype(int)
    c["n_damage_ref_equipment"] = c["n_damage_ref"] - c["n_damage_ref_wire"] - c["n_damage_ref_pole"]
    c["access_issue_rate_ref"] = c["circuit_id"].map(ref.groupby("circuit_id")["access_issue"].mean())
    labels = transformers.groupby("circuit_id").agg(station=("station", lambda s: s.mode().iloc[0] if len(s) else ""),
                                                    cir_label=("cir_label", lambda s: s.mode().iloc[0] if len(s) else ""))
    c = c.merge(labels, left_on="circuit_id", right_index=True, how="left")
    return c
