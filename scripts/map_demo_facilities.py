"""Replace the facility layer in a bundle with a coordinate-free table (kind, circuit, zone, distance to the line).

The organizer's USGS facility layers carry names and coordinates. The app only needs *how many* critical facilities a
protective zone feeds, so the committed demo bundle stores that mapping and nothing else (CONTRIBUTING.md rule 4). Idempotent:
a table that already has the safe schema is left alone.

    python scripts/map_demo_facilities.py              # data/demo
    python scripts/map_demo_facilities.py --bundle data/processed
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from concierge import config, priority  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=str(config.DEMO))
    args = ap.parse_args()
    bundle = Path(args.bundle)
    p = bundle / "facilities.parquet"
    fac = pd.read_parquet(p)
    if "zone_id" in fac.columns and not {"lon", "lat", "name"} & set(fac.columns):
        print(f"{p}: already coordinate-free ({len(fac)} rows); nothing to do")
        return
    primary = pd.read_parquet(bundle / "primary.parquet")
    mapped = priority.map_facilities_to_zones(fac, primary)
    mapped.to_parquet(p, index=False)
    by_kind = mapped["kind"].value_counts().to_dict()
    print(f"{p}: {len(fac)} facilities with coordinates -> {len(mapped)} mapped to zones within {priority.MAX_FACILITY_SEGMENT_M:.0f} m "
          f"({len(fac) - len(mapped)} dropped as too far from any primary line); by kind {by_kind}; columns {list(mapped.columns)}")


if __name__ == "__main__":
    main()
