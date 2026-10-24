"""One call turns a Scenario into every customer-facing output, consistently."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import alerts as alerts_mod
from . import evidence as evidence_mod
from . import guidance as guidance_mod
from . import messages as messages_mod
from . import restoration as restoration_mod
from .data import DataBundle
from .restoration import RestorationEstimate, zone_label
from .risk import RiskResult, score_customer
from .scenario import Scenario
from .scenarios import DamageSets

SAFE_CUSTOMER_FIELDS = ["premise_nu", "city", "circuit_id", "service_kind", "life_support", "has_path", "leaf_zone_id",
                        "path_zone_ids", "path_depth", "lon", "lat"]


@dataclass
class JourneyResult:
    scenario: Scenario
    customer: pd.Series
    circuit: pd.Series
    medical_need: bool
    risk: RiskResult
    restoration: RestorationEstimate
    alerts: alerts_mod.AlertDecision
    guidance: list
    messages: dict
    evidence: dict
    path_table: pd.DataFrame = field(default_factory=pd.DataFrame)


def path_table(data: DataBundle, cust: pd.Series, risk: RiskResult, est: RestorationEstimate) -> pd.DataFrame:
    """Zones from the substation down to the customer with damage, repair and risk information."""
    if not cust["has_path"]:
        return pd.DataFrame()
    zids = str(cust["path_zone_ids"]).split("|")[::-1]  # breaker first
    zh = data.zone_hazard.set_index("zone_id")
    inst, res = est.instance, est.policies.get("dependency_aware") if est.policies else None
    tasks_in_zone: dict[str, int] = {}
    if inst is not None:
        for t in inst.tasks:
            tasks_in_zone[t.zone_id] = tasks_in_zone.get(t.zone_id, 0) + 1
    rows = []
    total_h = max(risk.hazard, 1e-9)
    for zid in zids:
        z = data.zones.loc[zid]
        n_tasks = tasks_in_zone.get(zid, 0)
        done = res.zone_done.get(zid) if res is not None else None
        if n_tasks == 0:
            state = "no damage"
        elif est.blocking_zone == zid:
            state = "blocking (last on your path)"
        else:
            state = "repaired before service returns"
        lam = float(zh.loc[zid, "lam_total"]) if zid in zh.index else 0.0
        rows.append({
            "zone_id": zid, "kind": z["kind"], "depth": int(z["depth"]), "label": zone_label(data, zid),
            "customers_downstream": int(z["n_customers_downstream"]), "oh_miles": round(float(z["oh_miles"]), 2),
            "risk_share": round(risk.severity * lam / total_h, 2) if risk.method == "path" else None,
            "tasks": n_tasks, "repair_done_h": round(done + est.crew_start_delay_h, 1) if done is not None else None,
            "state": state, "feb2025_records": int(z.get("n_damage_ref", 0)),
        })
    return pd.DataFrame(rows)


def run(scn: Scenario, data: DataBundle, sets: DamageSets | None = None, previous: JourneyResult | None = None) -> JourneyResult:
    sets = sets or DamageSets(data)
    cust_full = data.premise(scn.premise_nu)
    cust = cust_full[[c for c in SAFE_CUSTOMER_FIELDS if c in cust_full.index]]
    circuit = data.circuit(str(cust["circuit_id"]))
    medical = bool(cust["life_support"]) if scn.medical_need is None else bool(scn.medical_need)

    zh = data.circuit_hazard(str(cust["circuit_id"]))
    risk = score_customer(cust_full, zh, circuit, data.model, scn.severity,
                          circuit_avg_hazard=None if cust["has_path"] else data.circuit_average_hazard(str(cust["circuit_id"])))
    risk.components["circuit_damage_ref"] = int(circuit["n_damage_ref"])

    est = restoration_mod.estimate(scn, data, sets)
    al = alerts_mod.decide(risk, est, scn, medical)
    guide = guidance_mod.build(risk, est, medical, scn.hours_to_landfall)
    prev_est = previous.restoration if previous is not None and previous.customer["premise_nu"] == cust["premise_nu"] else None
    msgs = messages_mod.build_all(cust, risk, est, al, scn, previous=prev_est)
    ev = evidence_mod.build(scn, data, cust, circuit, risk, est, al, medical)
    return JourneyResult(scn, cust, circuit, medical, risk, est, al, guide, msgs, ev, path_table(data, cust, risk, est))
