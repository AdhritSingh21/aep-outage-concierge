"""Labor hours per repair task from the organizer workbook (Observed CU averages) combined into damage-type bundles
(Assumed composition, see ASSUMPTIONS.md L1–L4).

    task_hours = (Σ bundle CU hours + adders) × ILM × labor_multiplier

ILM (indirect labor multiplier, 4.5 in the workbook) is applied exactly once and already covers drive, safety
briefing and set-up time, so no travel term is added anywhere else.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# (category, size-or-None, quantity, include removal hours)
BUNDLES: dict[str, list[tuple[str, str | None, float, bool]]] = {
    "pole": [("Pole", "size", 1, True), ("Crossarm", None, 1, True), ("Bracket", None, 1, False),
             ("Insulator", None, 3, True), ("Pin", None, 3, True), ("Grounding", None, 1, False), ("Guying", None, 1, False),
             ("Anchor", None, 1, False), ("Dead-End", None, 2, False), ("Connector", None, 4, False)],
    "primary_wire": [("Dead-End", None, 2, False), ("Connector", None, 2, False), ("Insulator", None, 1, False), ("Pin", None, 1, False)],
    "primary_equipment": [("Equipment Lead", None, 1, True), ("Connector", None, 2, False)],
    "secondary_wire": [("Secondary Conductor Attachment", None, 2, False), ("Connector", None, 2, False)],
    "service": [("Service", None, 1, True), ("Connector", None, 2, False)],
    "fuse_bank": [("Fuse Cutout", None, 1, True), ("Connector", None, 2, False)],
    "recloser_bank": [("Recloser", None, 1, True), ("Equipment Lead", None, 1, False), ("Connector", None, 2, False)],
    "switch": [("Switch", None, 1, True), ("Connector", None, 2, False)],
    "transformer": [("Transformer", "kva", 1, True), ("Equipment Lead", None, 1, False), ("Grounding", None, 1, False),
                    ("Fuse Cutout", None, 1, False), ("Connector", None, 2, False)],
    "capacitor_bank": [("Switch", None, 1, True), ("Equipment Lead", None, 1, False), ("Bracket", None, 1, False), ("Connector", None, 2, False)],
    "light": [("Bracket", None, 1, False), ("Connector", None, 1, False)],
}

# Observed asset types in the damage file → bundle (damage_type refines primary records)
ASSET_TO_BUNDLE = {
    "pole": "pole", "primary": "primary_wire", "secondary": "secondary_wire", "fuse bank": "fuse_bank",
    "recloser bank": "recloser_bank", "switch": "switch", "transformer": "transformer", "step transformer": "transformer",
    "capacitor bank": "capacitor_bank", "light": "light", "unknown": "primary_wire",
}
# Special-equipment labels in the damage file → workbook adder (original intent)
SPECIAL_EQUIPMENT_ADDER = {
    "Backyard Bucket": "Backyard Adder",
    "Track Hoe": "Remote Location Adder", "Yard Dog": "Remote Location Adder", "Backhoe": "Remote Location Adder",
}
DEFAULT_SPECIAL_ADDER = "Specialized Handling Adder"
ACCESS_ISSUE_ADDER = "Difficult Location"
ACCESS_DIFFICULTY_ADDER = "Access Constraint Adder"
DEFAULT_KVA = 25
DEFAULT_POLE_SIZE = "Medium"


@dataclass
class TaskHours:
    bundle: str
    base_hours: float          # Σ CU hours before adders and ILM
    adder_hours: float         # workbook adders before ILM
    ilm: float
    labor_multiplier: float
    components: list[tuple[str, float]] = field(default_factory=list)
    adders: list[tuple[str, float]] = field(default_factory=list)

    @property
    def hours(self) -> float:
        return (self.base_hours + self.adder_hours) * self.ilm * self.labor_multiplier


class LaborTable:
    """Lookups over the parsed workbook tables (labor_cu, labor_adders, labor_meta)."""

    def __init__(self, cu: pd.DataFrame, adders: pd.DataFrame, ilm: float):
        self.cu = cu
        self.ilm = float(ilm)
        self._adders = {str(r["original_intent"]): float(r["install_hours"]) for _, r in adders.iterrows()
                        if pd.notna(r.get("install_hours"))}
        self._by_cat: dict[str, pd.DataFrame] = {k.lower(): g for k, g in cu.groupby("category")}
        missing = {c for b in BUNDLES.values() for c, *_ in b} - {k for k in cu["category"]}
        if missing:
            raise ValueError(f"labor workbook lacks CU categories needed by the bundles: {sorted(missing)}")
        for name in (ACCESS_ISSUE_ADDER, ACCESS_DIFFICULTY_ADDER, DEFAULT_SPECIAL_ADDER, "Backyard Adder", "Remote Location Adder"):
            if name not in self._adders:
                raise ValueError(f"labor workbook lacks adder '{name}'")

    @classmethod
    def from_processed(cls, processed) -> "LaborTable":
        cu = pd.read_parquet(processed / "labor_cu.parquet")
        adders = pd.read_parquet(processed / "labor_adders.parquet")
        ilm = float(pd.read_parquet(processed / "labor_meta.parquet")["ilm"].iloc[0])
        return cls(cu, adders, ilm)

    # -- CU lookups
    def cu_hours(self, category: str, size: str | None = None, kva: float | None = None,
                 pole_height_ft: float | None = None) -> tuple[float, float]:
        g = self._by_cat[category.lower()]
        if size == "size":  # pole size by height
            label = DEFAULT_POLE_SIZE
            if pole_height_ft is not None and not pd.isna(pole_height_ft):
                label = "Small" if pole_height_ft <= 35 else ("Medium" if pole_height_ft <= 55 else "Large")
            row = g[g["size"] == label].iloc[0]
        elif size == "kva":
            want = float(kva) if kva is not None and not pd.isna(kva) else DEFAULT_KVA
            sizes = pd.to_numeric(g["size"], errors="coerce")
            row = g.iloc[(sizes - want).abs().argsort().iloc[0]]
        else:
            row = g.iloc[0]
        rem = row["remove_hours"] if pd.notna(row["remove_hours"]) else 0.0
        return float(row["install_hours"]), float(rem)

    def adder(self, name: str) -> float:
        return self._adders[name]

    # -- task hours
    def bundle_for(self, asset_type: str, damage_type: str = "") -> str:
        key = (asset_type or "unknown").strip().lower()
        if key not in ASSET_TO_BUNDLE:
            raise KeyError(f"no labor bundle for asset type {asset_type!r}")
        bundle = ASSET_TO_BUNDLE[key]
        dt = (damage_type or "").lower()
        if key == "primary" and "equipment" in dt:
            bundle = "primary_equipment"
        if key == "secondary" and "service" in dt:
            bundle = "service"
        return bundle

    def task_hours(self, asset_type: str, damage_type: str = "", access_issue: bool = False,
                   special_equipment: str = "", access_difficulty: bool = False, labor_multiplier: float = 1.0,
                   kva: float | None = None, pole_height_ft: float | None = None) -> TaskHours:
        bundle = self.bundle_for(asset_type, damage_type)
        comps: list[tuple[str, float]] = []
        base = 0.0
        for category, size, qty, with_removal in BUNDLES[bundle]:
            inst, rem = self.cu_hours(category, size, kva=kva, pole_height_ft=pole_height_ft)
            h = qty * (inst + (rem if with_removal else 0.0))
            comps.append((f"{category} ×{qty:g}" + (" (install+remove)" if with_removal else ""), h))
            base += h
        adders: list[tuple[str, float]] = []
        if access_issue:
            adders.append((ACCESS_ISSUE_ADDER, self.adder(ACCESS_ISSUE_ADDER)))
        se = (special_equipment or "").strip()
        if se:
            name = SPECIAL_EQUIPMENT_ADDER.get(se, DEFAULT_SPECIAL_ADDER)
            adders.append((f"{name} ({se})", self.adder(name)))
        if access_difficulty:
            adders.append((ACCESS_DIFFICULTY_ADDER + " (scenario)", self.adder(ACCESS_DIFFICULTY_ADDER)))
        return TaskHours(bundle, base, sum(h for _, h in adders), self.ilm, labor_multiplier, comps, adders)

    def bundle_summary(self) -> pd.DataFrame:
        """Hours per bundle at default sizes, for the evidence panel and README (before and after ILM)."""
        rows = []
        for name in BUNDLES:
            th = TaskHours(name, 0.0, 0.0, self.ilm, 1.0)
            base = 0.0
            for category, size, qty, with_removal in BUNDLES[name]:
                inst, rem = self.cu_hours(category, size)
                base += qty * (inst + (rem if with_removal else 0.0))
            rows.append({"bundle": name, "base_hours": round(base, 2), "crew_hours_with_ilm": round(base * self.ilm, 1)})
        return pd.DataFrame(rows)
