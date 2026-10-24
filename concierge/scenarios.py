"""Damage sets for a circuit: the Observed Feb-2025 records, or Simulated draws from the zone hazards."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config
from .data import DataBundle
from .labor import LaborTable
from .scheduling import Instance, Task

LINE_TYPES = ("primary", "Pole", "secondary")
EQUIPMENT_TYPES = ("Fuse Bank", "Transformer", "Recloser Bank", "Switch", "Capacitor Bank", "Light")
DAMAGE_TYPE_FOR = {"primary": "Damaged Wire", "secondary": "Damaged Wire", "Pole": "Damaged Pole", "Fuse Bank": "Damaged Switch",
                   "Recloser Bank": "Damaged Switch", "Switch": "Damaged Switch", "Transformer": "Damaged Equipment",
                   "Capacitor Bank": "Damaged Equipment", "Light": "Damaged Light"}


def _mix(d: pd.DataFrame, types: tuple[str, ...], fallback: dict[str, float]) -> dict[str, float]:
    sub = d[d["asset_type"].isin(types)]
    if len(sub) < 5:
        return fallback
    vc = sub["asset_type"].value_counts(normalize=True)
    return {k: float(vc.get(k, 0.0)) for k in types}


class DamageSets:
    """Builds task lists for one circuit under a scenario."""

    def __init__(self, data: DataBundle, labor: LaborTable | None = None):
        self.data = data
        self.labor = labor or data.labor
        ref = data.damage[data.damage["in_reference_storm"]]
        self.territory_line_mix = _mix(ref, LINE_TYPES, {"primary": 0.56, "Pole": 0.30, "secondary": 0.14})
        self.territory_equipment_mix = _mix(ref, EQUIPMENT_TYPES, {"Fuse Bank": 0.65, "Transformer": 0.17, "Recloser Bank": 0.13,
                                                                   "Switch": 0.04, "Capacitor Bank": 0.005, "Light": 0.005})
        self.territory_access_rate = float(ref["access_issue"].mean()) if len(ref) else 0.32
        self.territory_special_rate = float((ref["special_equipment"] != "").mean()) if len(ref) else 0.17

    # -- Observed
    def historical(self, circuit_id: str, access_difficulty: bool = False, labor_multiplier: float = 1.0) -> list[Task]:
        d = self.data.circuit_damage(circuit_id, reference_only=True)
        tasks = []
        for order, (_, r) in enumerate(d.iterrows()):
            th = self.labor.task_hours(r["asset_type"], r["damage_type"], bool(r["access_issue"]), str(r["special_equipment"]),
                                       access_difficulty, labor_multiplier)
            tasks.append(Task(task_id=f"D{int(r['damage_id'])}", zone_id=str(r["zone_id"]), hours=th.hours / config.CREW_SIZE,
                              order=order, asset_type=str(r["asset_type"]), damage_type=str(r["damage_type"]),
                              access_issue=bool(r["access_issue"]), source="Observed", person_hours=th.hours))
        return tasks

    def default_crews(self, circuit_id: str) -> int:
        """Assumed: enough crews for the Feb-2025 damage set to clear in about DEFAULT_CREW_TARGET_HOURS (FIFO makespan),
        bounded to [MIN_CREWS, MAX_CREWS]; circuits without reference damage get MIN_CREWS + 1."""
        total = sum(t.hours for t in self.historical(circuit_id))
        if total <= 0:
            return min(config.MAX_CREWS, config.MIN_CREWS + 1)
        return int(min(config.MAX_CREWS, max(config.MIN_CREWS, math.ceil(total / config.DEFAULT_CREW_TARGET_HOURS))))

    # -- Simulated
    def simulated(self, circuit_id: str, severity: float, seed: int, access_difficulty: bool = False,
                  labor_multiplier: float = 1.0) -> list[Task]:
        rng = np.random.default_rng(seed)
        zh = self.data.circuit_hazard(circuit_id)
        ref = self.data.circuit_damage(circuit_id, reference_only=True)
        line_mix = _mix(ref, LINE_TYPES, self.territory_line_mix)
        eq_mix = _mix(ref, EQUIPMENT_TYPES, self.territory_equipment_mix)
        access_rate = float(ref["access_issue"].mean()) if len(ref) >= 10 else self.territory_access_rate
        tasks: list[Task] = []
        for _, z in zh.iterrows():
            n_line = rng.poisson(severity * float(z["lam_line"]))
            n_eq = rng.poisson(severity * float(z["lam_equipment"]))
            for kind, n, mix in (("line", n_line, line_mix), ("equipment", n_eq, eq_mix)):
                if n == 0:
                    continue
                types = list(mix)
                probs = np.array([mix[t] for t in types], float)
                probs = probs / probs.sum() if probs.sum() > 0 else np.full(len(types), 1 / len(types))
                for _ in range(int(n)):
                    asset = str(rng.choice(types, p=probs))
                    access = bool(rng.random() < access_rate)
                    special = "Other" if rng.random() < self.territory_special_rate else ""
                    th = self.labor.task_hours(asset, DAMAGE_TYPE_FOR.get(asset, ""), access, special, access_difficulty, labor_multiplier)
                    tasks.append(Task(task_id=f"S{len(tasks)}", zone_id=str(z["zone_id"]), hours=th.hours / config.CREW_SIZE, order=0,
                                      asset_type=asset, damage_type=DAMAGE_TYPE_FOR.get(asset, ""), access_issue=access,
                                      source="Simulated", person_hours=th.hours))
        # report order is unknown for simulated damage: random, seeded
        perm = rng.permutation(len(tasks))
        tasks = [Task(**{**t.__dict__, "order": int(perm[i])}) for i, t in enumerate(tasks)]
        return tasks

    # -- Instance
    def instance(self, circuit_id: str, tasks: list[Task], crews: int, start_delay_h: float = 0.0) -> Instance:
        z = self.data.circuit_zones(circuit_id)
        parent = {zid: (p if isinstance(p, str) else None) for zid, p in zip(z["zone_id"], z["parent_zone_id"])}
        groups = {zid: int(n) for zid, n in zip(z["zone_id"], z["n_customers_direct"]) if n > 0}
        ls = {zid: int(n) for zid, n in zip(z["zone_id"], z["n_life_support_direct"]) if n > 0}
        down = {zid: int(n) for zid, n in zip(z["zone_id"], z["n_customers_downstream"])}
        return Instance(circuit_id, tasks, parent, groups, ls, down, crews=crews, start_delay_h=start_delay_h)
