"""Protective-zone tree built from the organizer's matched_upstream paths (Observed topology).

A *zone* is the set of customers and lines that a protective device (fuse, recloser, sectionalizer, or the circuit
breaker) isolates. Each customer's path lists its service transformer, then the protective devices from the
customer upward, ending at the circuit breaker. Step transformers that appear in some paths are pass-through and
are not zones.

Zone ids are the organizer equipment id prefixed by kind (F fuse, R recloser, S sectionalizer, B breaker) so ids from
different source tables cannot collide.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

KIND_BY_TYPE = {
    "Fuse": "fuse",
    "Recloser (Hydraulic)": "recloser",
    "Recloser (Electronic)": "recloser",
    "Sectionalizer": "sectionalizer",
    "Circuit Breaker": "breaker",
}
PREFIX = {"fuse": "F", "recloser": "R", "sectionalizer": "S", "breaker": "B", "other": "X"}


def zone_rows(upstream: pd.DataFrame) -> pd.DataFrame:
    """Keep only zone-forming tokens (isolating devices + breaker) and attach zone ids, in path order."""
    up = upstream[upstream["table"] != "eo_power_xfrmr_inst"].copy()
    up["kind"] = up["eq_type"].map(KIND_BY_TYPE).fillna("other")
    up["zone_id"] = up["kind"].map(PREFIX) + up["eq_id"].astype(str)
    up = up.sort_values(["premise_nu", "pos"], kind="stable")
    up["parent_zone_id"] = up.groupby("premise_nu")["zone_id"].shift(-1)
    # the breaker token's loc is the circuit id; carry it to every token of the same premise
    circ = up[up["kind"] == "breaker"].groupby("premise_nu")["loc"].first()
    up["circuit_id"] = up["premise_nu"].map(circ)
    return up


def build_zone_tree(upstream: pd.DataFrame, devices: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Return (zones, customer_leaf, stats).

    zones columns: zone_id, kind, eq_type, loc, circuit_id, parent_zone_id, depth, path_zone_ids, lon, lat.
    customer_leaf columns: premise_nu, leaf_zone_id.
    """
    up = zone_rows(upstream)
    leaf = up.groupby("premise_nu").head(1)[["premise_nu", "zone_id"]].rename(columns={"zone_id": "leaf_zone_id"})

    votes = (up.dropna(subset=["parent_zone_id"]).groupby(["zone_id", "parent_zone_id"]).size()
             .rename("n").reset_index().sort_values(["zone_id", "n"], ascending=[True, False]))
    n_parents = votes.groupby("zone_id")["parent_zone_id"].nunique()
    parent = votes.drop_duplicates("zone_id").set_index("zone_id")["parent_zone_id"]

    attrs = (up.groupby("zone_id").agg(kind=("kind", "first"), eq_type=("eq_type", "first"), loc=("loc", "first"),
                                       circuit_id=("circuit_id", lambda s: s.mode().iloc[0] if s.notna().any() else None),
                                       n_premise_paths=("premise_nu", "nunique")).reset_index())
    attrs["parent_zone_id"] = attrs["zone_id"].map(parent)
    attrs.loc[attrs["kind"] == "breaker", "parent_zone_id"] = None

    # depth and ancestor chain by walking parents (guard against cycles / dangling parents)
    par = attrs.set_index("zone_id")["parent_zone_id"].to_dict()
    chain_cache: dict[str, list[str]] = {}

    def chain(z: str) -> list[str]:
        if z in chain_cache:
            return chain_cache[z]
        out = [z]
        seen = {z}
        cur = par.get(z)
        while isinstance(cur, str) and cur not in seen and cur in par:
            out.append(cur)
            seen.add(cur)
            cur = par.get(cur)
        chain_cache[z] = out
        return out

    attrs["path_zone_ids"] = attrs["zone_id"].map(lambda z: "|".join(chain(z)))
    attrs["depth"] = attrs["path_zone_ids"].str.count(r"\|")
    roots = attrs[attrs["parent_zone_id"].isna()]
    non_breaker_roots = int((roots["kind"] != "breaker").sum())

    if devices is not None and len(devices):
        dev = devices.drop_duplicates(["loc", "kind"]).set_index(["loc", "kind"])[["lon", "lat"]]
        key = pd.MultiIndex.from_arrays([attrs["loc"], attrs["kind"]])
        coords = dev.reindex(key)
        attrs["lon"] = coords["lon"].to_numpy()
        attrs["lat"] = coords["lat"].to_numpy()
    else:
        attrs["lon"] = np.nan
        attrs["lat"] = np.nan

    stats = {
        "zones": int(len(attrs)),
        "zones_by_kind": attrs["kind"].value_counts().to_dict(),
        "zones_with_conflicting_parents": int((n_parents > 1).sum()),
        "non_breaker_roots": non_breaker_roots,
        "max_depth": int(attrs["depth"].max()),
        "zones_with_coordinates_pct": round(100 * float(attrs["lon"].notna().mean()), 1),
    }
    cols = ["zone_id", "kind", "eq_type", "loc", "circuit_id", "parent_zone_id", "depth", "path_zone_ids",
            "n_premise_paths", "lon", "lat"]
    return attrs[cols].reset_index(drop=True), leaf.reset_index(drop=True), stats


def attach_customer_zones(customers: pd.DataFrame, zones: pd.DataFrame, leaf: pd.DataFrame) -> pd.DataFrame:
    """Add leaf_zone_id, path_zone_ids, path_depth, has_path to the customer table."""
    out = customers.merge(leaf, on="premise_nu", how="left")
    z = zones.set_index("zone_id")
    out["path_zone_ids"] = out["leaf_zone_id"].map(z["path_zone_ids"])
    out["path_depth"] = out["leaf_zone_id"].map(z["depth"]).astype("Int64")
    out["has_path"] = out["leaf_zone_id"].notna()
    return out


def zone_customer_counts(zones: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    """Add n_customers_direct / n_customers_downstream (and life-support counterparts) to zones.

    Direct = customers whose leaf zone is this zone. Downstream = direct + all descendant zones. Every customer is
    counted once per zone on its path, never twice.
    """
    c = customers[customers["has_path"]]
    direct = c.groupby("leaf_zone_id").size()
    direct_ls = c[c["life_support"]].groupby("leaf_zone_id").size()
    z = zones.copy()
    z["n_customers_direct"] = z["zone_id"].map(direct).fillna(0).astype(int)
    z["n_life_support_direct"] = z["zone_id"].map(direct_ls).fillna(0).astype(int)
    # downstream: explode each customer's ancestor chain once
    exploded = c["path_zone_ids"].str.split("|").explode()
    down = exploded.value_counts()
    down_ls = c.loc[c["life_support"], "path_zone_ids"].str.split("|").explode().value_counts()
    z["n_customers_downstream"] = z["zone_id"].map(down).fillna(0).astype(int)
    z["n_life_support_downstream"] = z["zone_id"].map(down_ls).fillna(0).astype(int)
    return z


def transformer_zones(transformers: pd.DataFrame, customers: pd.DataFrame, zones: pd.DataFrame) -> pd.DataFrame:
    """Assign each transformer a zone: majority leaf zone of its customers, else the zone of the device at its
    upstream_loc (same circuit), else none."""
    c = customers[customers["has_path"]]
    maj = c.groupby("xfmr_loc")["leaf_zone_id"].agg(lambda s: s.mode().iloc[0])
    t = transformers.copy()
    t["zone_id"] = t["loc"].map(maj)
    t["zone_source"] = np.where(t["zone_id"].notna(), "customer_paths", "")
    by_loc = zones[zones["kind"] != "breaker"].drop_duplicates(["loc", "circuit_id"]).set_index(["loc", "circuit_id"])["zone_id"]
    key = pd.MultiIndex.from_arrays([t["upstream_loc"], t["circuit_id"]])
    fallback = pd.Series(by_loc.reindex(key).to_numpy(), index=t.index)
    use = t["zone_id"].isna() & fallback.notna()
    t.loc[use, "zone_id"] = fallback[use]
    t.loc[use, "zone_source"] = "upstream_loc"
    # last resort: the circuit's breaker zone
    breaker = zones[zones["kind"] == "breaker"].drop_duplicates("circuit_id").set_index("circuit_id")["zone_id"]
    use = t["zone_id"].isna() & t["circuit_id"].map(breaker).notna()
    t.loc[use, "zone_id"] = t.loc[use, "circuit_id"].map(breaker)
    t.loc[use, "zone_source"] = "circuit_breaker"
    t.loc[t["zone_id"].isna(), "zone_source"] = "none"
    return t


def lowest_common_ancestor(zone_ids: list[str], chains: dict[str, list[str]]) -> str | None:
    """LCA of zones given their ancestor chains (leaf → root). Returns None if no common ancestor."""
    ids = [z for z in zone_ids if z in chains]
    if not ids:
        return None
    common = set(chains[ids[0]])
    for z in ids[1:]:
        common &= set(chains[z])
    if not common:
        return None
    # the deepest common ancestor is the one appearing first in any chain
    for z in chains[ids[0]]:
        if z in common:
            return z
    return None
