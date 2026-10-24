"""Loads the processed (or demo) tables once and serves per-circuit views to the model layer."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

from . import config
from .labor import LaborTable
from .risk import RiskModel, customer_path_hazard

TABLES = ["customers", "zones", "circuits", "damage", "zone_hazard", "primary", "transformers", "facilities"]


def resolve_data_dir() -> Path:
    if (config.PROCESSED / "zone_hazard.parquet").exists():
        return config.PROCESSED
    if (config.DEMO / "zone_hazard.parquet").exists():
        return config.DEMO
    raise FileNotFoundError("No processed or demo data found. Run scripts/prepare_data.py (see data/README.md).")


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

    def circuit_average_hazard(self, circuit_id: str) -> float | None:
        if self._path_hazard is None:
            self._path_hazard = customer_path_hazard(self.t["customers"], self.zone_hazard)
        prem = self.t["customers"].loc[self.t["customers"]["circuit_id"] == circuit_id, "premise_nu"]
        vals = self._path_hazard.reindex(prem).dropna()
        return float(vals.mean()) if len(vals) else None

    def nearest_premise(self, lon: float, lat: float) -> str:
        c = self.t["customers"]
        d2 = (c["lon"] - lon) ** 2 + (c["lat"] - lat) ** 2
        return str(c.loc[d2.idxmin(), "premise_nu"])

    def sample_premises(self) -> list[dict]:
        """Curated examples covering the journeys the brief asks for (chosen from Observed data, ids anonymized)."""
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
