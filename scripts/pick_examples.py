"""Choose the example premises and the demo story by explicit criteria and write data/<bundle>/examples.json.

Why a script: the app's dropdown and the "Load demo story" button must show states that demonstrate the brief
(a band that moves with the severity slider, a declared life-support account whose wellness-check escalation fires,
underground service, a quiet circuit, the no-path fallback) and the demo steps must quote numbers the model produced,
not hand-typed ones (CLAUDE.md rule 3). Run after rebuilding a bundle:

    python scripts/pick_examples.py            # writes examples.json next to the loaded bundle (demo or processed)
    python scripts/pick_examples.py --bundle data/demo
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from concierge import config  # noqa: E402
from concierge.data import DataBundle  # noqa: E402
from concierge.journey import run  # noqa: E402
from concierge.restoration import STATE_AFFECTED, fmt_hours  # noqa: E402
from concierge.risk import score_customer  # noqa: E402
from concierge.scenario import Scenario  # noqa: E402
from concierge.scenarios import DamageSets  # noqa: E402

CRITERIA = {
    "first": "overhead service, upstream path on file, affected by the Feb-2025 damage set, P(outage) at 1x between 40% and 90% so the "
             "severity slider visibly moves the band, largest repair-vs-service gap among candidates on the hardest-hit circuits",
    "life_support": "declared life-support account (Observed flag), affected by the Feb-2025 set, expected restoration > 8 h at the "
                    "calibrated crew count so the wellness-check escalation (rule E3) is active, P(outage) not saturated if possible",
    "underground": "underground service drop on a circuit with Feb-2025 damage, affected if possible",
    "quiet": "circuit with no Feb-2025 damage reports (territory model carries the estimate)",
    "fallback": "no upstream path on file (circuit-average fallback, Low confidence, 'being assessed')",
    "demo": "DamageSets.demo_premise(): hard-hit circuit, customer whose nearest damage is repaired >= 2 h before service returns; "
            "steps are the two scenario changes that move the midpoint the most (measured)",
}


def p_at(d: DataBundle, cust: pd.Series, sev: float) -> float:
    zh = d.circuit_hazard(str(cust["circuit_id"]))
    return score_customer(cust, zh, d.circuit(str(cust["circuit_id"])), d.model, sev).probability


def affected_in_observed(d: DataBundle, cust: pd.Series) -> bool:
    dz = set(d.circuit_damage(str(cust["circuit_id"]))["zone_id"].astype(str))
    return bool(dz & set(str(cust["path_zone_ids"]).split("|"))) if isinstance(cust.get("path_zone_ids"), str) else False


def pick_first(d: DataBundle, s: DamageSets, top_circuits: int = 6) -> dict | None:
    c = d.t["customers"]
    circuits = d.circuits.sort_values("n_damage_ref", ascending=False).head(top_circuits)["circuit_id"]
    cand = c[c["circuit_id"].isin(circuits) & c["has_path"] & (c["service_kind"] == "Overhead")]
    rows = []
    for _, cust in cand.iterrows():
        if not affected_in_observed(d, cust):
            continue
        p1 = p_at(d, cust, 1.0)
        if not 0.40 <= p1 <= 0.90:
            continue
        rows.append((cust["premise_nu"], str(cust["circuit_id"]), p1))
    if not rows:
        return None
    # one journey per candidate leaf zone (premises in the same leaf share the schedule), keep the largest gap
    best = None
    seen_leaf = set()
    for pid, circ, p1 in rows:
        leaf = d.premise(pid)["leaf_zone_id"]
        if leaf in seen_leaf:
            continue
        seen_leaf.add(leaf)
        j = run(Scenario(pid, crews=s.default_crews(circ)), d, s)
        e = j.restoration
        if e.state != STATE_AFFECTED:
            continue
        gap = (e.service_restored_h - (e.local_repair_done_h or e.service_restored_h))
        spread = j.risk.sensitivity[3.0] - j.risk.sensitivity[0.5]
        score = (round(gap, 1), round(spread, 2))
        if best is None or score > best[0]:
            best = (score, pid, circ, p1, gap, j.risk.sensitivity)
        if len(seen_leaf) >= 40:
            break
    if best is None:
        return None
    _, pid, circ, p1, gap, sens = best
    return {"premise_nu": pid, "label": f"Overhead service on a hard-hit circuit ({circ}); the band moves with the storm slider",
            "measured": {"p_1x": round(p1, 3), "repair_to_service_gap_h": round(gap, 1), "p_by_severity": {str(k): round(v, 3) for k, v in sens.items()}}}


def pick_life_support(d: DataBundle, s: DamageSets) -> dict | None:
    c = d.t["customers"]
    ls = c[c["life_support"] & c["has_path"]]
    best = None
    for _, cust in ls.iterrows():
        pid, circ = cust["premise_nu"], str(cust["circuit_id"])
        if not affected_in_observed(d, cust):
            continue
        j = run(Scenario(pid, crews=s.default_crews(circ), medical_need=True), d, s)
        e = j.restoration
        if e.state != STATE_AFFECTED:
            continue
        wellness = any(x["active"] for x in j.alerts.escalations if x["trigger"].startswith("Medical"))
        if not wellness:
            continue
        p1 = j.risk.probability
        score = (0 if p1 > 0.995 else 1, e.mid_total)   # prefer a non-saturated band, then the longer estimate
        if best is None or score > best[0]:
            best = (score, pid, circ, p1, e.mid_total)
    if best is None:
        return None
    _, pid, circ, p1, mid = best
    return {"premise_nu": pid, "label": "Declared life-support account (wellness-check escalation active)",
            "measured": {"p_1x": round(p1, 3), "most_likely_h_at_default_crews": round(mid, 1)}}


def pick_simple(d: DataBundle) -> list[dict]:
    c = d.t["customers"]
    circ = d.circuits
    out = []
    ug = c[(c["service_kind"] == "Underground") & c["has_path"] & (c["circuit_id"].isin(circ.index[circ["n_damage_ref"] > 0]))]
    ug_aff = [row for _, row in ug.iterrows() if affected_in_observed(d, row)]
    if ug_aff:
        out.append({"premise_nu": ug_aff[0]["premise_nu"], "label": "Underground service drop, upstream line damaged in Feb-2025"})
    elif len(ug):
        out.append({"premise_nu": ug.iloc[0]["premise_nu"], "label": "Underground service, circuit with Feb-2025 damage"})
    quiet = circ[circ["n_damage_ref"] == 0]["circuit_id"]
    q = c[c["circuit_id"].isin(quiet) & c["has_path"]]
    if len(q):
        out.append({"premise_nu": q.iloc[0]["premise_nu"], "label": "Circuit with no Feb-2025 damage reports"})
    nopath = c[~c["has_path"]]
    if len(nopath):
        out.append({"premise_nu": nopath.iloc[0]["premise_nu"], "label": "No upstream path on file (fallback, being assessed)"})
    return out


def demo_story(d: DataBundle, s: DamageSets) -> dict | None:
    demo = s.demo_premise()
    if not demo:
        return None
    pid, crews = demo["premise_nu"], int(demo["crews"])
    base = run(Scenario(pid, crews=crews), d, s)
    b = base.restoration
    variants = {
        "crews": (f"Halve the crews ({crews} → {max(1, crews // 2)})", {"crews": max(1, crews // 2)}),
        "access": ("Toggle difficult access (ice / flooding)", {"access_difficulty": True}),
        "labor": ("Slower repairs: workbook hours × 1.5", {"labor_multiplier": 1.5}),
        "delay": ("Crews released 6 h after the storm", {"crew_start_delay_h": 6.0}),
        "double": (f"Double the crews ({crews} → {2 * crews})", {"crews": 2 * crews}),
    }
    measured = []
    for key, (title, change) in variants.items():
        j = run(Scenario(pid, crews=crews).with_(**change), d, s, previous=base)
        e = j.restoration
        measured.append({"key": key, "title": title, "set": change, "most_likely_h": round(e.mid_total, 1),
                         "window": e.window_text, "delta_h": round(e.mid_total - b.mid_total, 1),
                         "update_message": j.messages["estimate_updated"].estimate})
    # step 2: the crew change that moves the midpoint most (the operational lever); step 3: the largest other mover
    crew_moves = [m for m in measured if m["key"] in ("crews", "double")]
    other_moves = [m for m in measured if m["key"] not in ("crews", "double")]
    movers = [max(crew_moves, key=lambda m: abs(m["delta_h"]))] if crew_moves else []
    if other_moves:
        movers.append(max(other_moves, key=lambda m: abs(m["delta_h"])))
    steps = [{"title": "Baseline", "set": {}, "expect": f"premise {pid} on circuit {demo['circuit_id']}, {crews} crews: service back about "
                                                       f"{fmt_hours(b.mid_total)} ({b.window_text}); the damage nearest you is repaired at "
                                                       f"{fmt_hours(b.local_repair_done_h)} but service returns at {fmt_hours(b.service_restored_h)} "
                                                       f"(gap {demo['repair_to_service_gap_h']} h)"}]
    for m in movers:
        steps.append({"title": m["title"], "set": m["set"], "expect": f"most likely moves {fmt_hours(b.mid_total)} → {fmt_hours(m['most_likely_h'])} "
                                                                  f"({m['delta_h']:+.1f} h); the card, chain, narrative and the estimate-updated message move together"})
    return {**demo, "baseline": {"most_likely_h": round(b.mid_total, 1), "window": b.window_text,
                                 "local_repair_done_h": round(b.local_repair_done_h or 0, 1), "service_restored_h": round(b.service_restored_h, 1)},
            "variants_measured": measured, "steps": steps}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=None, help="data directory (default: the bundle DataBundle resolves)")
    args = ap.parse_args()
    d = DataBundle(Path(args.bundle)) if args.bundle else DataBundle()
    s = DamageSets(d)
    examples = []
    first = pick_first(d, s)
    if first:
        examples.append(first)
    ls = pick_life_support(d, s)
    if ls:
        examples.append(ls)
    examples.extend(pick_simple(d))
    doc = {"generated_by": "scripts/pick_examples.py", "bundle": str(d.data_dir.relative_to(ROOT)) if d.data_dir.is_relative_to(ROOT) else str(d.data_dir),
           "criteria": CRITERIA, "examples": examples, "demo": demo_story(d, s)}
    out = d.data_dir / "examples.json"
    out.write_text(json.dumps(doc, indent=1), encoding="utf8")
    print(f"wrote {out}")
    for e in examples:
        print(f"  {e['premise_nu']}  {e['label']}  {e.get('measured', '')}")
    if doc["demo"]:
        for st in doc["demo"]["steps"]:
            print(f"  demo step: {st['title']} -> {st['expect']}")


if __name__ == "__main__":
    main()
