"""Tests on the committed demo bundle (data/demo). NOT data-marked: they run in every checkout.

The bundle is public, so it must be slim (no PII fields, no internal customer identifiers, coarse coordinates),
complete (every table the app loads is present) and self-consistent with its generated README. The slimming rule
lives in scripts/slim_demo_bundle.py and is mirrored in concierge.etl.build_demo_bundle.
"""
from __future__ import annotations

import importlib.util
import re

import numpy as np
import pandas as pd
import pytest

from concierge import config
from concierge.data import TABLES

DEMO = config.DEMO
# the five columns the task removes from the public customers table; the script's constant must agree
DROPPED = ("customer_id", "loc", "xfmr_loc", "service_lon", "service_lat")
# tables the app reads besides concierge.data.TABLES (concierge.labor.LaborTable.from_processed)
LABOR_TABLES = ("labor_cu", "labor_adders", "labor_meta")


@pytest.fixture(scope="module")
def slim():
    spec = importlib.util.spec_from_file_location("slim_demo_bundle", config.ROOT / "scripts" / "slim_demo_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tables():
    return {p.stem: pd.read_parquet(p) for p in sorted(DEMO.glob("*.parquet"))}


@pytest.fixture(scope="module")
def readme():
    return (DEMO / "README.md").read_text(encoding="utf8")


def test_every_app_table_loads(tables):
    for name in list(TABLES) + list(LABOR_TABLES):
        assert name in tables, f"{name}.parquet missing from data/demo"
        assert len(tables[name]) > 0, f"{name}.parquet is empty"
    assert set(tables["customers"].columns) >= {"premise_nu", "circuit_id", "service_kind", "life_support", "city",
                                                "lon", "lat", "leaf_zone_id", "path_zone_ids", "path_depth", "has_path"}


def test_no_pii_or_internal_identifier_columns(tables, slim):
    assert tuple(slim.DROP_COLUMNS) == DROPPED
    pii = {f.lower() for f in config.PII_FIELDS}
    for name, df in tables.items():
        cols = {c.lower() for c in df.columns}
        assert not (cols & pii), f"{name}: PII column(s) {sorted(cols & pii)}"
    cust_cols = set(tables["customers"].columns)
    assert not (cust_cols & set(DROPPED)), f"customers: internal identifier(s) {sorted(cust_cols & set(DROPPED))}"


def test_facilities_carry_no_names_or_coordinates(tables, slim):
    """CLAUDE.md rule 4: no precise facility coordinates (and no names) in any output, the public bundle included."""
    fac = tables["facilities"]
    locating = [c for c in fac.columns if c.lower() in slim.FACILITY_LOCATING_COLUMNS]
    assert not locating, f"facilities.parquet has locating column(s) {locating}"
    assert "zone_id" in fac.columns and "circuit_id" in fac.columns  # counts per zone are what the model uses


def test_readme_names_every_premise_keyed_table(tables, readme):
    """A table keyed on premise_nu outside customers.parquet must be disclosed in the README's privacy section."""
    keyed = [t for t, df in tables.items() if t != "customers" and "premise_nu" in df.columns]
    for t in keyed:
        assert f"`{t}.parquet`" in readme, f"README does not disclose premise-keyed table {t}"


def test_customer_coordinates_are_coarse(tables, slim):
    c = tables["customers"]
    for col in ("lon", "lat"):
        v = c[col].to_numpy(dtype=float)
        assert np.isfinite(v).all()
        assert np.allclose(v, np.round(v, slim.COORD_DECIMALS), rtol=0, atol=1e-9), f"{col} has more than 3 decimals"
    # still inside the organizer clip (south-west Virginia) so the nearest-premise lookup stays meaningful
    assert c["lon"].between(-80.4, -79.5).all() and c["lat"].between(36.9, 37.6).all()


def test_slim_script_is_a_fixed_point_on_the_committed_bundle(tables, slim):
    c = tables["customers"]
    assert slim.slim_customers(c).equals(c)
    assert slim.is_slim(c)
    assert slim.check(DEMO) == []  # also covers "README.md is up to date with the tables on disk"


def test_circuit_sets_agree(tables):
    cust, zones, circ = tables["customers"], tables["zones"], tables["circuits"]
    circuits = set(cust["circuit_id"])
    assert set(zones["circuit_id"]) == circuits
    assert set(circ["circuit_id"]) == circuits
    per_circuit = cust.groupby("circuit_id").size()
    assert (circ.set_index("circuit_id")["n_customers"].reindex(per_circuit.index) == per_circuit).all()


def test_readme_counts_match_tables(tables, readme):
    rows = {m.group(1): int(m.group(2).replace(",", ""))
            for m in re.finditer(r"^\| (\w+) \| ([\d,]+) \|", readme, flags=re.M)}
    assert rows["customers"] == len(tables["customers"])
    assert rows["zones"] == len(tables["zones"])
    m = re.search(r"restricted to (\d+) circuits: ([0-9, ]+)\.", readme)
    assert m, "README does not state the circuit list"
    assert int(m.group(1)) == tables["customers"]["circuit_id"].nunique()
    assert sorted(m.group(2).split(", ")) == sorted(tables["customers"]["circuit_id"].unique())
    m = re.search(r"- ([\d,]+) premises on (\d+) circuits; ([\d,]+) have an upstream path", readme)
    assert m and int(m.group(1).replace(",", "")) == len(tables["customers"])
    assert int(m.group(3).replace(",", "")) == int(tables["customers"]["has_path"].sum())
    m = re.search(r"- ([\d,]+) protective zones", readme)
    assert m and int(m.group(1).replace(",", "")) == len(tables["zones"])


def test_path_structure_and_report_zones_missing_from_bundle(tables, slim):
    c, z = tables["customers"], tables["zones"]
    pathed, nopath = c[c["has_path"]], c[~c["has_path"]]
    tokens = pathed["path_zone_ids"].str.split("|")
    assert pathed["path_zone_ids"].notna().all() and (tokens.str.len() >= 1).all()
    assert (tokens.str[0] == pathed["leaf_zone_id"]).all()          # leaf first ...
    assert (tokens.str.len() - 1 == pathed["path_depth"]).all()      # ... breaker last, depth = edges
    assert nopath["leaf_zone_id"].isna().all() and nopath["path_zone_ids"].isna().all()
    # Zones referenced by paths are in zones.parquet except for premises whose path leaves the bundle's circuits.
    # That count is reported, not asserted: it is a property of the organizer data, not of this code.
    missing = slim.missing_path_zone_premises(c, z)
    known = set(z["zone_id"])
    others = pathed[~pathed["premise_nu"].isin(missing)]
    assert others["path_zone_ids"].str.split("|").explode().isin(known).all()
    print(f"\npremises with path zones missing from zones.parquet: {len(missing)} of {len(pathed):,} pathed "
          f"({', '.join(missing) or 'none'})")
