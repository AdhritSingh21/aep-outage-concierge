"""ETL: organizer extract → data/processed/*.parquet + data/data_report.md.

Everything produced here is **Observed** data reshaped (field selection, type parsing, coordinate conversion).
No modeling happens in this module. PII fields (see config.PII_FIELDS) are never written to processed tables.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .labor_workbook import parse_labor_workbook
from .shapefile_io import (haversine_feet, polyline_length_feet, polylines_to_vertex_table, read_line_layer,
                           read_point_layer, web_mercator_to_lonlat)

FEET_PER_MILE = config.FEET_PER_MILE


# ----------------------------------------------------------------------------- helpers
def _str(s: pd.Series) -> pd.Series:
    """Nullable-int / float ids → clean strings ('' for null)."""
    if s.dtype == object:
        return s.fillna("").astype(str).str.strip()
    out = s.astype("Int64").astype(object)
    return out.where(out.notna(), "").astype(str)


def _year(s: pd.Series) -> pd.Series:
    y = pd.to_datetime(s, errors="coerce").dt.year.astype("Int64")
    return y.where(y > 1900)  # 1900-01-01 is a placeholder


def _kva(a: pd.Series, b: pd.Series, c: pd.Series) -> pd.Series:
    spec = a.where(a != "", b).where(lambda x: x != "", c)
    return pd.to_numeric(spec.str.extract(r"(\d+(?:\.\d+)?)\s*kVA", expand=False), errors="coerce")


# ----------------------------------------------------------------------------- layers
def load_customers(stem: Path) -> pd.DataFrame:
    cols = ["id", "premise_nu", "aep_locati", "aep_xfmr_l", "circuit_id", "kind_of_se", "critical_c", "city",
            "customer_c", "aep_string", "service_po", "service__1"]
    df = read_point_layer(stem, cols)
    out = pd.DataFrame({
        "premise_nu": _str(df["premise_nu"]).str.zfill(9),
        "customer_id": _str(df["id"]),
        "circuit_id": _str(df["circuit_id"]),
        "loc": _str(df["aep_locati"]),
        "xfmr_loc": _str(df["aep_xfmr_l"]),
        "service_kind": _str(df["kind_of_se"]).replace({"": "Unknown"}),
        "life_support": _str(df["critical_c"]).str.contains("Life Support", case=False),
        "city": _str(df["city"]).str.title(),
        "lon": df["lon"], "lat": df["lat"],
        "service_lon": pd.to_numeric(df["service_po"], errors="coerce"),
        "service_lat": pd.to_numeric(df["service__1"], errors="coerce"),
    })
    return out


def load_transformers(stem: Path) -> pd.DataFrame:
    cols = ["id", "location_n", "upstream_l", "circuit_id", "aep_circui", "aep_statio", "aep_custom", "a_phase_sp",
            "b_phase_sp", "c_phase_sp", "mounting", "structure_", "bank_confi", "device_typ", "a_phase_da"]
    df = read_point_layer(stem, cols)
    return pd.DataFrame({
        "xfmr_id": _str(df["id"]), "loc": _str(df["location_n"]), "upstream_loc": _str(df["upstream_l"]),
        "circuit_id": _str(df["circuit_id"]), "cir_label": _str(df["aep_circui"]), "station": _str(df["aep_statio"]),
        "customers_reported": df["aep_custom"].fillna(0).astype(int),
        "kva": _kva(_str(df["a_phase_sp"]), _str(df["b_phase_sp"]), _str(df["c_phase_sp"])),
        "mounting": _str(df["mounting"]), "structure": _str(df["structure_"]), "bank": _str(df["bank_confi"]),
        "device_type": _str(df["device_typ"]), "install_year": _year(df["a_phase_da"]),
        "lon": df["lon"], "lat": df["lat"],
    })


def load_devices(stem: Path, kind: str) -> pd.DataFrame:
    cols = ["id", "location_n", "circuit_id", "aep_circui", "aep_statio", "device_typ", "normal_sta", "mounting"]
    if kind != "switch":
        cols += ["upstream_l", "aep_custom"]
    df = read_point_layer(stem, cols)

    def col(name: str) -> pd.Series:  # layers differ slightly in schema (e.g. reclosers have no mounting)
        return _str(df[name]) if name in df.columns else pd.Series([""] * len(df), dtype=object)

    out = pd.DataFrame({
        "device_id": col("id"), "loc": col("location_n"), "kind": kind,
        "device_type": col("device_typ"), "circuit_id": col("circuit_id"),
        "cir_label": col("aep_circui"), "station": col("aep_statio"),
        "normal_state": col("normal_sta"), "mounting": col("mounting"),
        "upstream_loc": col("upstream_l"),
        "customers_reported": df["aep_custom"].fillna(0).astype(int) if "aep_custom" in df.columns else 0,
        "lon": df["lon"], "lat": df["lat"],
    })
    return out


def load_poles(stem: Path) -> pd.DataFrame:
    cols = ["id", "location_n", "material", "height", "class", "date_insta", "usage", "status"]
    df = read_point_layer(stem, cols)
    height = pd.to_numeric(_str(df["height"]).str.extract(r"(\d+)", expand=False), errors="coerce")
    return pd.DataFrame({
        "pole_id": _str(df["id"]), "loc": _str(df["location_n"]), "material": _str(df["material"]),
        "height_ft": height.astype("Int64"), "pole_class": _str(df["class"]),
        "install_year": _year(df["date_insta"]), "usage": _str(df["usage"]), "status": _str(df["status"]),
        "lon": df["lon"], "lat": df["lat"],
    })


def load_lines(stem: Path, kind: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    cols = ["id", "location_n", "location_1", "circuit_id", "aep_circui", "aep_statio", "report_len", "mounting",
            "usage", "phase_orde", "aep_date_i", "status"]
    df, lines = read_line_layer(stem, cols)
    mids = np.array([l[len(l) // 2] if len(l) else (np.nan, np.nan) for l in lines])
    out = pd.DataFrame({
        "seg_idx": np.arange(len(df), dtype=np.int32), "line_id": _str(df["id"]), "kind": kind,
        "loc_a": _str(df["location_n"]), "loc_b": _str(df["location_1"]),
        "circuit_id": _str(df["circuit_id"]), "cir_label": _str(df["aep_circui"]), "station": _str(df["aep_statio"]),
        "length_ft": pd.to_numeric(df["report_len"], errors="coerce").fillna(0.0),
        "mounting": _str(df["mounting"]), "usage": _str(df["usage"]), "phase": _str(df["phase_orde"]),
        "install_year": _year(df["aep_date_i"]), "status": _str(df["status"]),
        "mid_lon": mids[:, 0], "mid_lat": mids[:, 1],
        "geodesic_ft": [polyline_length_feet(l) for l in lines],
    })
    return out, polylines_to_vertex_table(lines)


def load_damage(stem: Path) -> pd.DataFrame:
    df = read_point_layer(stem)
    report = pd.to_datetime(df["Report_Tim"], errors="coerce")
    repaired = _str(df["Repaired"]).str.lower().eq("yes")
    rep_date = pd.to_datetime(df["Repaired_D"], errors="coerce")
    placeholder = rep_date.dt.strftime("%Y-%m-%d").eq(config.REPAIR_DATE_PLACEHOLDER) & ~repaired
    rep_date = rep_date.where(repaired & ~placeholder)
    start, end = pd.Timestamp(config.REFERENCE_STORM_START), pd.Timestamp(config.REFERENCE_STORM_END)
    return pd.DataFrame({
        "damage_id": np.arange(len(df), dtype=np.int32),
        "station": _str(df["Substation"]), "cir_label": _str(df["Circuit"]),
        "report_date": report, "repaired": repaired, "repaired_date": rep_date,
        "repaired_date_was_placeholder": placeholder,
        "asset_type": _str(df["Asset_Type"]).replace({"": "unknown"}),
        "damage_type": _str(df["Inform_Wor"]).replace({"": "unknown"}),
        "access_issue": _str(df["Access_Iss"]).str.lower().eq("yes"),
        "special_equipment": _str(df["Special_Eq"]),
        "media": pd.to_numeric(df["Media"], errors="coerce").fillna(0).astype(int),
        "in_reference_storm": report.between(start, end),
        "lon": df["lon"], "lat": df["lat"],
    })


def load_facilities(stems: dict[str, Path]) -> pd.DataFrame:
    parts = []
    for kind, stem in stems.items():
        df = read_point_layer(stem, ["NAME", "CITY", "STATE"])
        lon, lat = web_mercator_to_lonlat(df["lon"].to_numpy(), df["lat"].to_numpy())
        parts.append(pd.DataFrame({"kind": kind, "name": _str(df["NAME"]), "city": _str(df["CITY"]).str.title(),
                                   "lon": lon, "lat": lat}))
    return pd.concat(parts, ignore_index=True)


def load_upstream(txt: Path) -> pd.DataFrame:
    """Explode matched_upstream.txt into (premise_nu, pos, table, eq_id, eq_type, loc) rows, pos 0 = transformer."""
    rows = []
    with open(txt, encoding="utf8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            key = parts[0].strip().zfill(9)
            for pos, tok in enumerate(parts[1:]):
                sub = tok.split("*")
                if len(sub) != 4:
                    continue
                rows.append((key, pos, sub[0], sub[1], sub[2], sub[3]))
    df = pd.DataFrame(rows, columns=["premise_nu", "pos", "table", "eq_id", "eq_type", "loc"])
    df["pos"] = df["pos"].astype(np.int16)
    return df


# ----------------------------------------------------------------------------- build
def build_processed(extracted: Path, labor_xlsx: Path, processed: Path, report_path: Path) -> dict[str, pd.DataFrame]:
    processed.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    sf = config.SHAPEFILES
    tables: dict[str, pd.DataFrame] = {}
    print("  customers"); tables["customers"] = load_customers(sf["customers"])
    print("  transformers"); tables["transformers"] = load_transformers(sf["transformers"])
    print("  devices")
    tables["devices"] = pd.concat([load_devices(sf["fuses"], "fuse"), load_devices(sf["reclosers"], "recloser"),
                                   load_devices(sf["switches"], "switch")], ignore_index=True)
    print("  poles"); tables["poles"] = load_poles(sf["poles"])
    print("  primary"); tables["primary"], tables["primary_vertices"] = load_lines(sf["primary"], "primary")
    print("  secondary"); tables["secondary"], _ = load_lines(sf["secondary"], "secondary")
    print("  damage"); tables["damage"] = load_damage(sf["damage"])
    print("  facilities")
    tables["facilities"] = load_facilities({"hospital": sf["hospitals"], "fire": sf["fire"], "ambulance": sf["ambulance"]})
    print("  upstream paths"); tables["upstream"] = load_upstream(config.UPSTREAM_TXT if extracted == config.EXTRACTED
                                                                  else extracted / "matched_upstream" / "matched_upstream.txt")
    print("  labor workbook")
    labor = parse_labor_workbook(labor_xlsx)
    tables["labor_cu"] = labor["cu"]
    tables["labor_adders"] = labor["adders"]
    tables["labor_connectors"] = labor["connectors"]
    tables["labor_meta"] = pd.DataFrame([{"ilm": labor["ilm"]}])

    for name, df in tables.items():
        df.to_parquet(processed / f"{name}.parquet", index=False)
    report = data_report(tables)
    report_path.write_text(report, encoding="utf8")
    print(f"  wrote {len(tables)} tables and {report_path.name} in {time.time() - t0:.0f}s")
    return tables


# ----------------------------------------------------------------------------- report
def join_statistics(t: dict[str, pd.DataFrame]) -> dict[str, float | int | str]:
    cust, xf, dev, up, prim, dmg, fac = (t["customers"], t["transformers"], t["devices"], t["upstream"],
                                        t["primary"], t["damage"], t["facilities"])
    first = up[up["pos"] == 0]
    paths = first.set_index("premise_nu")
    joined = cust.join(paths[["loc", "eq_id"]].rename(columns={"loc": "path_xfmr_loc", "eq_id": "path_xfmr_id"}),
                       on="premise_nu")
    last = up.sort_values(["premise_nu", "pos"]).groupby("premise_nu").tail(1).set_index("premise_nu")
    joined = joined.join(last[["loc", "eq_type"]].rename(columns={"loc": "path_circuit", "eq_type": "path_last_type"}),
                         on="premise_nu")
    has_path = joined["path_xfmr_loc"].notna()
    xf_loc = set(xf["loc"])
    dev_by_kind = {k: g for k, g in dev.groupby("kind")}
    fuse_ids = set(dev_by_kind["fuse"]["device_id"]); fuse_locs = set(dev_by_kind["fuse"]["loc"])
    rec_locs = set(dev_by_kind["recloser"]["loc"]); rec_ids = set(dev_by_kind["recloser"]["device_id"])
    mid = up[(up["pos"] > 0) & (up["table"] == "eo_isolating_eqpt_inst")]
    fuse_tok = mid[mid["eq_type"] == "Fuse"].drop_duplicates("eq_id")
    rec_tok = mid[mid["eq_type"].str.startswith("Recloser")].drop_duplicates("eq_id")
    # transformer.upstream_loc vs the first *protective device* in the path (skip breaker-only and step-xfmr paths)
    nxt = up[(up["pos"] == 1) & (up["table"] == "eo_isolating_eqpt_inst")].set_index("premise_nu")["loc"]
    chk = first.join(nxt.rename("next_loc"), on="premise_nu").merge(xf[["loc", "upstream_loc"]], on="loc", how="inner")
    chk = chk[chk["next_loc"].notna()]
    # length units
    sample = prim[(prim["geodesic_ft"] > 5) & (prim["length_ft"] > 0)].head(5000)
    ratio = (sample["length_ft"] / sample["geodesic_ft"]).median()
    # facilities in bbox
    bbox = (cust["lon"].min() - 0.05, cust["lat"].min() - 0.05, cust["lon"].max() + 0.05, cust["lat"].max() + 0.05)
    fac_in = ((fac["lon"] >= bbox[0]) & (fac["lon"] <= bbox[2]) & (fac["lat"] >= bbox[1]) & (fac["lat"] <= bbox[3])).mean()
    return {
        "customers": len(cust), "paths": up["premise_nu"].nunique(),
        "customers_with_path": int(has_path.sum()),
        "customers_with_path_pct": round(100 * has_path.mean(), 3),
        "path_xfmr_loc_matches_customer_pct": round(100 * (joined.loc[has_path, "path_xfmr_loc"] == joined.loc[has_path, "xfmr_loc"]).mean(), 3),
        "path_circuit_matches_customer_pct": round(100 * (joined.loc[has_path, "path_circuit"] == joined.loc[has_path, "circuit_id"]).mean(), 3),
        "customer_xfmr_in_transformer_layer_pct": round(100 * cust["xfmr_loc"].isin(xf_loc).mean(), 3),
        "path_xfmr_in_transformer_layer_pct": round(100 * first["loc"].isin(xf_loc).mean(), 3),
        "fuse_tokens": len(fuse_tok), "fuse_tokens_id_in_layer_pct": round(100 * fuse_tok["eq_id"].isin(fuse_ids).mean(), 2),
        "fuse_tokens_loc_in_layer_pct": round(100 * fuse_tok["loc"].isin(fuse_locs).mean(), 2),
        "recloser_tokens": len(rec_tok), "recloser_tokens_id_in_layer_pct": round(100 * rec_tok["eq_id"].isin(rec_ids).mean(), 2),
        "recloser_tokens_loc_in_layer_pct": round(100 * rec_tok["loc"].isin(rec_locs).mean(), 2),
        "transformer_upstream_loc_matches_path_pct": round(100 * (chk["upstream_loc"] == chk["next_loc"]).mean(), 2),
        "primary_segments": len(prim), "primary_miles": round(prim["length_ft"].sum() / FEET_PER_MILE, 1),
        "primary_overhead_miles": round(prim.loc[prim["mounting"] == "Overhead", "length_ft"].sum() / FEET_PER_MILE, 1),
        "report_len_over_geodesic_ft_median": round(float(ratio), 4),
        "facilities": len(fac), "facilities_in_clip_bbox_pct": round(100 * float(fac_in), 1),
        "damage_records": len(dmg), "damage_in_reference_storm": int(dmg["in_reference_storm"].sum()),
        "damage_repaired": int(dmg["repaired"].sum()),
        "damage_placeholder_repair_dates_nulled": int(dmg["repaired_date_was_placeholder"].sum()),
        "life_support_customers": int(cust["life_support"].sum()),
        "circuits": cust["circuit_id"].nunique(),
    }


def data_report(t: dict[str, pd.DataFrame]) -> str:
    s = join_statistics(t)
    lines = ["# Data report (generated by concierge/etl.py)", "",
             "All values are **Observed** from the organizer extract after type parsing. Nothing here is modeled.", "",
             "## Tables", "", "| table | rows |", "|---|---|"]
    for name, df in t.items():
        lines.append(f"| {name} | {len(df):,} |")
    lines += ["", "## Join and unit checks", "", "| check | value |", "|---|---|"]
    for k, v in s.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## Notes", "",
              "- `matched_upstream.txt` keys on customer `premise_nu` (9 digits, zero-padded); customers without a path use the fallback branch.",
              "- Recloser tokens in paths match the Recloser layer by location, not by id (different source tables).",
              "- `report_len` is in feet (ratio to geodesic length ≈ 1).",
              "- Damage circuit key `(station, cir_label)` is resolved to `circuit_id` in exposure.py; `NEAR_FID/NEAR_DIST` are ignored.",
              f"- Reference storm window: {config.REFERENCE_STORM_START} to {config.REFERENCE_STORM_END}.",
              f"- `Repaired_D == {config.REPAIR_DATE_PLACEHOLDER}` on unrepaired rows is a placeholder and was nulled.",
              "- Repair dates are day-granularity and are never used as outage durations."]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- demo bundle
def build_demo_bundle(processed: Path, demo: Path, circuits: list[str] | None = None) -> None:
    """Copy a subset of circuits (all tables) into data/demo so the app runs without the full extract."""
    circuits = circuits or config.DEMO_CIRCUITS
    demo.mkdir(parents=True, exist_ok=True)
    tables = {p.stem: pd.read_parquet(p) for p in processed.glob("*.parquet")}
    cust = tables["customers"]
    keep_prem = set(cust.loc[cust["circuit_id"].isin(circuits), "premise_nu"])
    prim = tables.get("primary")
    keep_seg = set(prim.loc[prim["circuit_id"].isin(circuits), "seg_idx"]) if prim is not None else set()
    for name, df in tables.items():
        if "circuit_id" in df.columns:
            sub = df[df["circuit_id"].isin(circuits)]
        elif name == "upstream":
            sub = df[df["premise_nu"].isin(keep_prem)]
        elif name == "primary_vertices":
            sub = df[df["seg_idx"].isin(keep_seg)]
        elif name == "poles" and "circuit_id" not in df.columns:
            lon0, lon1 = cust.loc[cust["premise_nu"].isin(keep_prem), "lon"].agg(["min", "max"])
            lat0, lat1 = cust.loc[cust["premise_nu"].isin(keep_prem), "lat"].agg(["min", "max"])
            sub = df[(df["lon"].between(lon0, lon1)) & (df["lat"].between(lat0, lat1))]
        else:
            sub = df
        sub.to_parquet(demo / f"{name}.parquet", index=False)
    (demo / "README.md").write_text(
        "Demo bundle derived from the AEP HackOHI/O 2026 data (https://github.com/hackohio/aep_challenge_hack26) "
        f"restricted to circuits {', '.join(circuits)}. Anonymized premise IDs only; no address fields.\n", encoding="utf8")
