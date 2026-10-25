"""Loads the processed (or demo) tables once and serves per-circuit views to the model layer."""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import pandas as pd

from . import config, priority
from .labor import LaborTable
from .risk import RiskModel, customer_path_hazard

TABLES = ["customers", "zones", "circuits", "damage", "zone_hazard", "primary", "transformers", "facilities"]
EXAMPLES_FILE = "examples.json"   # written by scripts/pick_examples.py (script-chosen example premises and demo story)


def resolve_data_dir() -> Path:
    if (config.PROCESSED / "zone_hazard.parquet").exists():
        return config.PROCESSED
    if (config.DEMO / "zone_hazard.parquet").exists():
        return config.DEMO
    raise FileNotFoundError("No processed or demo data found. Run scripts/prepare_data.py (see data/README.md).")


def haversine_km(lon1: float, lat1: float, lon2, lat2):
    """Great-circle distance in km (vectorised over the second point)."""
    import numpy as np
    r = 6371.0088
    p1, p2 = math.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2) - p1
    dl = np.radians(lon2) - math.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


class DataBundle:
    def __init__(self, data_dir: Path | None = None, results_dir: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else resolve_data_dir()
        self.results_dir = Path(results_dir) if results_dir else config.RESULTS
        self.t: dict[str, pd.DataFrame] = {}
        for name in TABLES:
            p = self.data_dir / f"{name}.parquet"
            if p.exists():
                self.t[name] = pd.read_parquet(p)
        self.customers = self.t["customers"].set_index("premise_nu", drop=False)
        self.zones = self.t["zones"].set_index("zone_id", drop=False)
        self.circuits = self.t["circuits"].set_index("circuit_id", drop=False)
        self.damage = self.t["damage"]
        self.zone_hazard = self.t["zone_hazard"]
        self.model = RiskModel.from_json(self.results_dir / "risk_model.json")
        self.labor = LaborTable.from_processed(self.data_dir)
        self.is_demo = self.data_dir == config.DEMO
        self._path_hazard: pd.Series | None = None
        self.facility_zone = self._facility_zones()
        counts = priority.facility_counts(self.t["zones"], self.facility_zone)
        for col in ("n_facilities_direct", "n_facilities_downstream"):
            self.zones[col] = counts[col].reindex(self.zones.index).fillna(0).astype(int)
        self.t["zones"] = self.zones.reset_index(drop=True)

    def _facility_zones(self) -> pd.DataFrame:
        """Facilities as (kind, circuit, zone) only. A bundle may ship the coordinate-free table already
        (scripts/map_demo_facilities.py); the full extract still carries coordinates and is mapped here."""
        fac = self.t.get("facilities")
        if fac is None or len(fac) == 0:
            return pd.DataFrame(columns=priority.SAFE_FACILITY_COLUMNS)
        if "zone_id" in fac.columns:
            keep = [c for c in priority.SAFE_FACILITY_COLUMNS if c in fac.columns]
            return fac[keep].copy()
        if {"lon", "lat"} <= set(fac.columns) and "primary" in self.t:
            return priority.map_facilities_to_zones(fac, self.t["primary"])
        return pd.DataFrame(columns=priority.SAFE_FACILITY_COLUMNS)

    # -- lookups
    def premise(self, premise_nu: str) -> pd.Series:
        key = str(premise_nu).strip().zfill(9)
        if key not in self.customers.index:
            raise KeyError(f"premise {premise_nu} not found")
        return self.customers.loc[key]

    def circuit(self, circuit_id: str) -> pd.Series:
        return self.circuits.loc[circuit_id]

    def circuit_zones(self, circuit_id: str) -> pd.DataFrame:
        return self.zones[self.zones["circuit_id"] == circuit_id]

    def circuit_hazard(self, circuit_id: str) -> pd.DataFrame:
        return self.zone_hazard[self.zone_hazard["circuit_id"] == circuit_id]

    def circuit_damage(self, circuit_id: str, reference_only: bool = True) -> pd.DataFrame:
        d = self.damage[(self.damage["circuit_id"] == circuit_id) & self.damage["zone_id"].notna()]
        if reference_only:
            d = d[d["in_reference_storm"]]
        return d.sort_values(["report_date", "damage_id"])

    def circuit_facilities(self, circuit_id: str) -> dict[str, int]:
        return priority.facility_summary(self.facility_zone, circuit_id)

    def circuit_average_hazard(self, circuit_id: str) -> float | None:
        if self._path_hazard is None:
            self._path_hazard = customer_path_hazard(self.t["customers"], self.zone_hazard)
        prem = self.t["customers"].loc[self.t["customers"]["circuit_id"] == circuit_id, "premise_nu"]
        vals = self._path_hazard.reindex(prem).dropna()
        return float(vals.mean()) if len(vals) else None

    # -- location
    def bbox(self) -> dict[str, float]:
        c = self.t["customers"]
        return {"lon_min": float(c["lon"].min()), "lon_max": float(c["lon"].max()),
                "lat_min": float(c["lat"].min()), "lat_max": float(c["lat"].max())}

    def nearest_premise(self, lon: float, lat: float) -> str:
        return self.nearest_premise_with_distance(lon, lat)[0]

    def nearest_premise_with_distance(self, lon: float, lat: float) -> tuple[str, float]:
        """(anonymized premise id, great-circle distance in km) of the closest customer point."""
        c = self.t["customers"]
        d = haversine_km(lon, lat, c["lon"].to_numpy(), c["lat"].to_numpy())
        i = int(d.argmin())
        return str(c.iloc[i]["premise_nu"]), float(d[i])

    # -- curated examples (script-chosen when data/<bundle>/examples.json exists)
    def examples_file(self) -> Path:
        return self.data_dir / EXAMPLES_FILE

    def load_examples(self) -> dict | None:
        p = self.examples_file()
        if not p.exists():
            return None
        try:
            doc = json.loads(p.read_text(encoding="utf8"))
        except Exception:
            return None
        ex = [e for e in doc.get("examples", []) if str(e.get("premise_nu", "")).zfill(9) in self.customers.index]
        doc["examples"] = ex
        demo = doc.get("demo")
        if demo and str(demo.get("premise_nu", "")).zfill(9) not in self.customers.index:
            doc["demo"] = None
        return doc

    def sample_premises(self) -> list[dict]:
        """Curated examples covering the journeys the brief asks for.

        Preferred source: examples.json written by scripts/pick_examples.py (criteria documented there: a premise whose
        band moves with severity and that is affected in the Feb-2025 set, a life-support account whose wellness-check
        escalation fires, underground service, a quiet circuit, the no-path fallback). Fallback: simple heuristics on
        the loaded tables.
        """
        doc = self.load_examples()
        if doc and doc.get("examples"):
            return [{"premise_nu": str(e["premise_nu"]).zfill(9), "label": str(e["label"])} for e in doc["examples"]]
        return self._heuristic_examples()

    def demo_story(self) -> dict | None:
        doc = self.load_examples()
        return doc.get("demo") if doc else None

    def _heuristic_examples(self) -> list[dict]:
        c = self.t["customers"]
        circ = self.circuits
        picks: list[dict] = []
        heavy = circ.sort_values("n_damage_ref", ascending=False)["circuit_id"].iloc[0]
        cand = c[(c["circuit_id"] == heavy) & c["has_path"] & (c["service_kind"] == "Overhead")]
        if len(cand):
            picks.append({"premise_nu": cand.iloc[0]["premise_nu"], "label": f"Overhead service on the hardest-hit circuit ({heavy})"})
        ls = c[c["life_support"] & c["has_path"]]
        if len(ls):
            picks.append({"premise_nu": ls.iloc[0]["premise_nu"], "label": "Declared life-support account"})
        ug = c[(c["service_kind"] == "Underground") & c["has_path"] & (c["circuit_id"].isin(circ.index[circ["n_damage_ref"] > 0]))]
        if len(ug):
            picks.append({"premise_nu": ug.iloc[0]["premise_nu"], "label": "Underground service, circuit with Feb-2025 damage"})
        quiet = circ[circ["n_damage_ref"] == 0]["circuit_id"]
        q = c[c["circuit_id"].isin(quiet) & c["has_path"]]
        if len(q):
            picks.append({"premise_nu": q.iloc[0]["premise_nu"], "label": "Circuit with no Feb-2025 damage reports"})
        nopath = c[~c["has_path"]]
        if len(nopath):
            picks.append({"premise_nu": nopath.iloc[0]["premise_nu"], "label": "No upstream path on file (fallback)"})
        return picks


@lru_cache(maxsize=1)
def load_bundle() -> DataBundle:
    return DataBundle()
