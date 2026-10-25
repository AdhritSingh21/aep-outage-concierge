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
from .risk import RiskResult, score_customer, severity_sensitivity
from .scenario import Scenario
from .scenarios import DamageSets

# What the UI layer may see about a customer: the anonymized id, city, circuit, service kind, flags and path (no coordinates)
SAFE_CUSTOMER_FIELDS = ["premise_nu", "city", "circuit_id", "service_kind", "life_support", "has_path", "leaf_zone_id",
                        "path_zone_ids", "path_depth"]


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
    missing_path_zones: int = 0


def path_table(data: DataBundle, cust: pd.Series, risk: RiskResult, est: RestorationEstimate) -> pd.DataFrame:
    """Zones from the substation down to the customer with damage, repair and risk information (times after the storm)."""
    if not cust["has_path"] or not isinstance(cust.get("path_zone_ids"), str):
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
        if zid not in data.zones.index:   # guarded: a path zone missing from the zone table never crashes the page
            continue
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
            "customers_downstream": int(z["n_customers_downstream"]),
            "life_support_downstream": int(z.get("n_life_support_downstream", 0) or 0),
            "facilities_downstream": int(z.get("n_facilities_downstream", 0) or 0),
            "oh_miles": round(float(z["oh_miles"]), 2),
            "risk_share": round(risk.severity * lam / total_h, 2) if risk.method == "path" else None,
            "tasks": n_tasks, "repair_done_h": round(done + est.crew_start_delay_h, 1) if (done is not None and n_tasks) else None,
            "state": state, "feb2025_records": int(z.get("n_damage_ref", 0)),
        })
    return pd.DataFrame(rows)


def run(scn: Scenario, data: DataBundle, sets: DamageSets | None = None, previous: JourneyResult | None = None) -> JourneyResult:
    sets = sets or DamageSets(data)
    cust_full = data.premise(scn.premise_nu)
    cust = cust_full[[c for c in SAFE_CUSTOMER_FIELDS if c in cust_full.index]]
    circuit = data.circuit(str(cust["circuit_id"]))
    medical = bool(cust["life_support"]) if scn.medical_need is None else bool(scn.medical_need)
    medical = medical or "medical" in scn.needs

    zh = data.circuit_hazard(str(cust["circuit_id"]))
    zids = str(cust["path_zone_ids"]).split("|") if cust["has_path"] and isinstance(cust.get("path_zone_ids"), str) else []
    missing = sum(1 for z in zids if z not in data.zones.index)
    # the circuit average is the fallback when no path is on file or none of its zones exist in the zone table
    avg = data.circuit_average_hazard(str(cust["circuit_id"])) if (not zids or missing == len(zids)) else None
    risk = score_customer(cust_full, zh, circuit, data.model, scn.severity, circuit_avg_hazard=avg)
    risk.components["circuit_damage_ref"] = int(circuit["n_damage_ref"])
    risk.sensitivity = severity_sensitivity(cust_full, zh, circuit, data.model, circuit_avg_hazard=avg)

    est = restoration_mod.estimate(scn, data, sets)
    leaf = cust["leaf_zone_id"] if isinstance(cust.get("leaf_zone_id"), str) and cust["leaf_zone_id"] in data.zones.index else None
    est.what_moves_it = restoration_mod.what_moves(est, scn, sets, str(cust["circuit_id"]), leaf)
    al = alerts_mod.decide(risk, est, scn, medical)
    guide = guidance_mod.build(risk, est, medical, scn.hours_to_landfall, scn=scn, service_kind=str(cust["service_kind"]))
    same_premise = previous is not None and previous.customer["premise_nu"] == cust["premise_nu"]
    prev_est = previous.restoration if same_premise else None
    prev_scn = previous.scenario if same_premise else None
    msgs = messages_mod.build_all(cust, risk, est, al, scn, previous=prev_est, previous_scenario=prev_scn)
    ev = evidence_mod.build(scn, data, cust, circuit, risk, est, al, medical)
    return JourneyResult(scn, cust, circuit, medical, risk, est, al, guide, msgs, ev, path_table(data, cust, risk, est), missing)
