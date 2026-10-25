"""Privacy slimming of the committed demo bundle (idempotent).

Rewrites data/demo/customers.parquet without the per-premise internal identifiers (``customer_id``, ``loc``,
``xfmr_loc``) and the second coordinate pair (``service_lon``, ``service_lat``), rounds ``lon``/``lat`` to
COORD_DECIMALS places (3: a shift of at most about 70 m at this latitude, the exact figure is computed into the README;
kept because the app's nearest-premise lookup needs them),
then regenerates data/demo/README.md from the tables on disk. No other table is touched.
``concierge.etl.build_demo_bundle`` applies the same rule when the bundle is rebuilt from the full extract, so on a
fresh bundle this script changes nothing.

Usage:
    python scripts/slim_demo_bundle.py              # slim data/demo in place and rewrite its README
    python scripts/slim_demo_bundle.py --check      # exit 1 if the bundle is not already slim; writes nothing
    python scripts/slim_demo_bundle.py --demo DIR   # another bundle directory

Labels: every count printed or written is **Observed** (row counts of the committed tables); file sizes and the
metres-per-0.001-degree figures are Measured from the data on disk.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from concierge import config  # noqa: E402
from concierge.data import TABLES as APP_TABLES  # noqa: E402  tables concierge.data.DataBundle loads at runtime

# Columns removed from customers.parquet in the public bundle (keep in sync with concierge.etl.build_demo_bundle).
DROP_COLUMNS = ("customer_id", "loc", "xfmr_loc", "service_lon", "service_lat")
COORD_COLUMNS = ("lon", "lat")
COORD_DECIMALS = 3
METRES_PER_DEGREE = 111_195.0  # mean metres per degree of latitude on the WGS84 sphere (6371.0088 km radius)
SOURCE_URL = "https://github.com/hackohio/aep_challenge_hack26"
# Labor tables read by concierge.labor.LaborTable.from_processed (besides concierge.data.TABLES).
APP_LABOR_TABLES = ("labor_cu", "labor_adders", "labor_meta")
# Column names that would mean a facility is identified or located (CLAUDE.md rule 4: no precise facility coordinates).
FACILITY_LOCATING_COLUMNS = ("lon", "lat", "x", "y", "longitude", "latitude", "name", "address")


# ----------------------------------------------------------------------------- slimming
def slim_customers(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the internal identifiers and round the coordinates. Idempotent: slim(slim(x)) == slim(x)."""
    out = df.drop(columns=[c for c in DROP_COLUMNS if c in df.columns]).copy()
    for c in COORD_COLUMNS:
        if c in out.columns:
            out[c] = out[c].round(COORD_DECIMALS)
    return out


def is_slim(df: pd.DataFrame) -> bool:
    return slim_customers(df).equals(df)


# ----------------------------------------------------------------------------- summary
def missing_path_zone_premises(customers: pd.DataFrame, zones: pd.DataFrame) -> list[str]:
    """Premises whose upstream path names a zone that is not in zones.parquet (a bundle restricted to a few circuits
    cuts the path of a customer whose breaker belongs to another circuit)."""
    known = set(zones["zone_id"].astype(str))
    pathed = customers[customers["has_path"].astype(bool)]
    tokens = pathed["path_zone_ids"].fillna("").astype(str).str.split("|")
    bad = tokens.apply(lambda ts: any(t and t not in known for t in ts))
    return sorted(pathed.loc[bad, "premise_nu"].astype(str))


def bundle_summary(demo: Path) -> dict:
    files = sorted(demo.glob("*.parquet"))
    tables = {p.stem: pd.read_parquet(p) for p in files}
    cust, zones = tables["customers"], tables["zones"]
    circuits = sorted(cust["circuit_id"].astype(str).unique())
    lat_mean = float(cust["lat"].mean())
    step = 10.0 ** -COORD_DECIMALS
    m_lat = METRES_PER_DEGREE * step
    m_lon = METRES_PER_DEGREE * step * math.cos(math.radians(lat_mean))
    app_tables = sorted(t for t in tables if t in set(APP_TABLES) | set(APP_LABOR_TABLES))
    fac = tables.get("facilities")
    return {
        "tables": [{"name": p.stem, "rows": len(tables[p.stem]), "bytes": p.stat().st_size,
                    "columns": list(tables[p.stem].columns)} for p in files],
        "total_bytes": sum(p.stat().st_size for p in files),
        "circuits": circuits,
        "customers": len(cust),
        "with_path": int(cust["has_path"].astype(bool).sum()),
        "without_path": int((~cust["has_path"].astype(bool)).sum()),
        "zones": len(zones),
        "damage": len(tables["damage"]) if "damage" in tables else 0,
        "distinct_points": int(len(cust[list(COORD_COLUMNS)].drop_duplicates())),
        "lat_mean": lat_mean,
        "m_per_step_lat": m_lat,
        "m_per_step_lon": m_lon,
        # rounding moves a point by at most half a grid step in each direction: half the cell diagonal
        "m_max_shift": 0.5 * math.hypot(m_lon, m_lat),
        "missing_path_zone_premises": missing_path_zone_premises(cust, zones),
        "customers_slim": is_slim(cust),
        # the other tables keyed on premise_nu (per-premise rows outside customers.parquet)
        "premise_keyed_tables": sorted(t for t, df in tables.items() if t != "customers" and "premise_nu" in df.columns),
        "app_tables": app_tables,
        "etl_only_tables": sorted(t for t in tables if t not in app_tables),
        "facilities_columns": list(fac.columns) if fac is not None else None,
        "facilities_locating_columns": [c for c in fac.columns if c.lower() in FACILITY_LOCATING_COLUMNS] if fac is not None else [],
        "other_files": sorted(p.name for p in demo.iterdir() if p.is_file() and p.suffix != ".parquet" and p.name != "README.md"),
    }


def readme_text(s: dict) -> str:
    miss = s["missing_path_zone_premises"]
    lines = [
        "# Demo bundle (data/demo)", "",
        f"Derived from the AEP HackOHI/O 2026 data package ({SOURCE_URL}), restricted to {len(s['circuits'])} circuits: "
        f"{', '.join(s['circuits'])}. Built by `concierge.etl.build_demo_bundle` (step 6 of "
        "`python scripts/prepare_data.py --source <organizer zip>`) and slimmed by `scripts/slim_demo_bundle.py`, which "
        "also generates this file. Every count below is **Observed** (rows of the committed tables); file sizes and the "
        "metre figures are Measured from the files on disk.", "",
        "## Privacy (CLAUDE.md rule 4)", "",
        f"- Premises are identified by the anonymized `premise_nu` only. `customers.parquet` ships without "
        f"{', '.join(f'`{c}`' for c in DROP_COLUMNS)} (internal location / customer identifiers and the second "
        "coordinate pair of the organizer layer).",
        f"- `customers.lon` / `customers.lat` are rounded to {COORD_DECIMALS} decimals: one step of "
        f"{10.0 ** -COORD_DECIMALS:g} degrees is {s['m_per_step_lon']:.0f} m east-west and {s['m_per_step_lat']:.0f} m "
        f"north-south at the bundle's mean latitude ({s['lat_mean']:.2f} N), so a rounded point lies at most "
        f"{s['m_max_shift']:.0f} m (half a grid cell) from the true location; the {s['customers']:,} premises fall on "
        f"{s['distinct_points']:,} distinct points. The coordinates are kept only so the app's find-by-coordinates lookup "
        "can pick the nearest premise.",
        "- No table carries an address field (`concierge.config.PII_FIELDS`); city and circuit are the only location words "
        "the app shows. Transformer, device, pole and line tables keep their equipment `loc` identifiers because the "
        "zone tree and exposure joins are keyed on them; they are equipment labels, not customer data.",
    ]
    if s["facilities_columns"] is not None:
        loc_cols = s["facilities_locating_columns"]
        lines.append(
            f"- Priority locations (`facilities.parquet`) are reduced to counts and distances per protective zone: columns "
            f"{', '.join(f'`{c}`' for c in s['facilities_columns'])}; "
            + ("no facility name or coordinate column." if not loc_cols
               else f"WARNING: locating column(s) {', '.join(f'`{c}`' for c in loc_cols)} present, which rule 4 forbids."))
    if s["premise_keyed_tables"]:
        pk = ", ".join(f"`{t}.parquet`" for t in s["premise_keyed_tables"])
        lines.append(
            f"- Besides `customers.parquet`, {pk} carries rows keyed on `premise_nu`: the organizer's matched_upstream.txt "
            "restricted to the bundle, i.e. the equipment identifiers (`eq_id`, `loc`) on each premise's path to the "
            "substation, from which the zone tree is rebuilt. The app does not read it at runtime (see the next bullet).")
    lines += [
        f"- Tables the app loads at runtime (`concierge.data.TABLES` plus the labor tables): "
        f"{', '.join(f'`{t}`' for t in s['app_tables'])}. Kept only so the derived tables can be rebuilt from the bundle: "
        f"{', '.join(f'`{t}`' for t in s['etl_only_tables']) or 'none'}.",
        "", "## Coverage", "",
        f"- {s['customers']:,} premises on {len(s['circuits'])} circuits; {s['with_path']:,} have an upstream path on file "
        f"(`has_path`), {s['without_path']} use the no-path fallback branch.",
        f"- {s['zones']:,} protective zones (`zones.parquet`), {s['damage']:,} damage records (`damage.parquet`).",
        f"- {len(miss)} premise(s) have an upstream path naming zones that are not in `zones.parquet`"
        + (f" ({', '.join(miss)})" if miss else "")
        + ": the path ends at the breaker of a circuit outside the bundle. Listed here so the app and the tests handle "
          "the case explicitly instead of discovering it on screen.",
        "", "## Tables", "",
        f"{len(s['tables'])} parquet files, {s['total_bytes'] / 1e6:.1f} MB in total.", "",
        "| table | rows | size (KB) | columns |", "|---|---|---|---|",
    ]
    for t in s["tables"]:
        lines.append(f"| {t['name']} | {t['rows']:,} | {t['bytes'] / 1e3:.1f} | {', '.join(t['columns'])} |")
    if s["other_files"]:
        lines += ["", "Other files in the bundle: " + ", ".join(f"`{f}`" for f in s["other_files"]) + "."]
    lines += ["", f"Regenerate with `python scripts/prepare_data.py --source <organizer zip>`; re-slim an existing "
                  "bundle with `python scripts/slim_demo_bundle.py`."]
    return "\n".join(lines) + "\n"


def check(demo: Path) -> list[str]:
    """Problems that would make the bundle differ from what this script produces (empty when already slim)."""
    problems = []
    cust = pd.read_parquet(demo / "customers.parquet")
    present = [c for c in DROP_COLUMNS if c in cust.columns]
    if present:
        problems.append(f"customers.parquet still has {present}")
    for c in COORD_COLUMNS:
        if c in cust.columns and not cust[c].round(COORD_DECIMALS).equals(cust[c]):
            problems.append(f"customers.{c} is not rounded to {COORD_DECIMALS} decimals")
    readme = demo / "README.md"
    if not readme.exists() or readme.read_text(encoding="utf8") != readme_text(bundle_summary(demo)):
        problems.append("README.md is stale")
    return problems


# ----------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", default=str(config.DEMO), help="bundle directory (default data/demo)")
    ap.add_argument("--check", action="store_true", help="report whether the bundle is already slim; write nothing")
    args = ap.parse_args(argv)
    demo = Path(args.demo)
    path = demo / "customers.parquet"
    if not path.exists():
        print(f"no customers.parquet under {demo}", file=sys.stderr)
        return 2

    if args.check:
        problems = check(demo)
        for p in problems:
            print(f"  {p}")
        print("bundle is slim" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    before = pd.read_parquet(path)
    size_before = path.stat().st_size
    print(f"customers.parquet before: {len(before):,} rows, {size_before:,} bytes ({size_before / 1e3:.1f} KB)")
    print(f"  columns: {list(before.columns)}")
    print(f"  distinct lon/lat points: {len(before[list(COORD_COLUMNS)].drop_duplicates()):,}")
    after = slim_customers(before)
    if after.equals(before):
        print("  already slim; parquet left untouched")
    else:
        after.to_parquet(path, index=False)
    size_after = path.stat().st_size
    print(f"customers.parquet after:  {len(after):,} rows, {size_after:,} bytes ({size_after / 1e3:.1f} KB)")
    print(f"  columns: {list(after.columns)}")
    print(f"  dropped: {[c for c in DROP_COLUMNS if c in before.columns]}; lon/lat rounded to {COORD_DECIMALS} decimals")
    print(f"  distinct lon/lat points: {len(after[list(COORD_COLUMNS)].drop_duplicates()):,}")

    s = bundle_summary(demo)
    (demo / "README.md").write_text(readme_text(s), encoding="utf8")
    print(f"README.md rewritten: {s['customers']:,} premises, {len(s['circuits'])} circuits, {s['zones']:,} zones, "
          f"{s['damage']:,} damage records; {len(s['missing_path_zone_premises'])} premise(s) with path zones outside "
          f"the bundle {s['missing_path_zone_premises']}")
    print(f"  0.001 degree = {s['m_per_step_lon']:.0f} m east-west, {s['m_per_step_lat']:.0f} m north-south at "
          f"{s['lat_mean']:.2f} N; max shift from rounding {s['m_max_shift']:.0f} m")
    print(f"  premise-keyed tables besides customers: {s['premise_keyed_tables']}; ETL-only tables: {s['etl_only_tables']}")
    if s["facilities_locating_columns"]:
        print(f"  WARNING: facilities.parquet has locating column(s) {s['facilities_locating_columns']}")
    print("bundle sizes (bytes):")
    for t in s["tables"]:
        print(f"  {t['name']:<18} {t['rows']:>8,} rows {t['bytes']:>10,}")
    print(f"  {'total':<18} {'':>8} {s['total_bytes']:>10,}  ({s['total_bytes'] / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
