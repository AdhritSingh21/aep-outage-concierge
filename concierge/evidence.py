"""Compact evidence / results panel: what was Observed, what the model produced, which Assumptions were in play,
solver status, benchmark summary (when results/ exists) and limitations."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config
from .alerts import AlertDecision
from .data import DataBundle
from .restoration import RestorationEstimate, fmt_hours
from .risk import RiskResult
from .scenario import Scenario


def benchmark_summary(results_dir: Path | None = None) -> str | None:
    p = (results_dir or config.RESULTS) / "benchmark_summary.md"
    return p.read_text(encoding="utf8") if p.exists() else None


def build(scn: Scenario, data: DataBundle, cust: pd.Series, circuit: pd.Series, risk: RiskResult,
          est: RestorationEstimate, alerts: AlertDecision, medical_need: bool) -> dict[str, list[tuple[str, str]]]:
    observed = [
        ("Circuit", f"{cust['circuit_id']} — {int(circuit['n_customers']):,} customers, {circuit['oh_miles']:.1f} mi overhead / {circuit['ug_miles']:.1f} mi underground primary, {int(circuit['n_zones'])} protective zones"),
        ("Feb-2025 damage on circuit", f"{int(circuit['n_damage_ref'])} records (wire {int(circuit['n_damage_ref_wire'])}, pole {int(circuit['n_damage_ref_pole'])}, equipment {int(circuit['n_damage_ref_equipment'])}); access issues on {100 * float(circuit['access_issue_rate_ref'] or 0):.0f}% of them"),
        ("Feb-2025 affected share", f"damage sat on the upstream path of {100 * float(circuit.get('affected_share_ref', 0)):.0f}% of the circuit's customers"),
        ("Your service", f"{cust['service_kind']} service; upstream path {'resolved (' + str(int(cust['path_depth'])) + ' zones)' if cust['has_path'] else 'not on file'}"),
        ("Declared need on file", "life-support flag" if bool(cust["life_support"]) else "none"),
        ("Labor reference", f"workbook CU averages, ILM {data.labor.ilm:g}"),
    ]
    model = [
        ("Outage risk", f"{risk.band} — {risk.percent_text} at severity {scn.severity:g}× (hazard Λ = {risk.hazard:.2f}; confidence {risk.confidence}; method {risk.method})"),
        ("Restoration", (f"{est.window_text}; most likely {fmt_hours(est.mid_h + est.crew_start_delay_h)} after crews start; "
                         f"source: {est.source}") if est.affected else f"not affected in this damage set ({est.source})"),
        ("Repair vs service", (f"damage nearest you done at {fmt_hours(est.local_repair_done_h + est.crew_start_delay_h)}, service at "
                               f"{fmt_hours(est.service_restored_h + est.crew_start_delay_h)} ({est.blocking_zone_label})") if est.affected and est.local_repair_done_h is not None else "—"),
        ("Alerts", f"pre-storm {'yes' if alerts.eligible_pre_storm else 'no'}; outage alerts {'yes' if alerts.eligible_outage_alerts else 'no'}; channels {', '.join(alerts.channels)}"),
    ]
    assumptions = [
        ("Storm", f"severity {scn.severity:g}× the Feb-2025 rates; footprint covers all circuits (R1, R5)"),
        ("Crews", f"{scn.crews} identical 4-person crews on this circuit, start {scn.crew_start_delay_h:g} h after the storm (L5)"),
        ("Labor", f"ILM applied once; task clock hours = person-hours ÷ 4; labor × {scn.labor_multiplier:g}; range × 0.75 / × 1.5 (L2, L8)"),
        ("Access", "difficult access adds 0.5 h × ILM to every task (L3)" if scn.access_difficulty else "no circuit-wide access penalty (L3)"),
        ("Damage set", "Observed Feb-2025 records on this circuit, quantity 1 each (L1)" if scn.damage_source == "historical" else f"{scn.n_draws} seeded Poisson draws from the zone hazards (L8)"),
        ("Medical need", "declared by the customer in this session" if medical_need and not bool(cust["life_support"]) else ("account flag, shown only to this customer (C2)" if medical_need else "none declared")),
    ]
    st = est.status
    solver = []
    if st is not None:
        solver = [
            ("Policy shown", f"{st.policy} — {st.order_source}"),
            ("Instance", f"{st.n_tasks} tasks, {st.crews} crews, {est.affected_customers_circuit:,} affected customers (each counted once)"),
            ("Improvement pass", f"{'ran' if st.improvement_pass else 'not run'}" + (f": {st.improvement_iterations} swap evaluations, {st.improvement_pct:g}% better" if st.improvement_pass else "")),
            ("Optimality", (f"{st.reference_kind}: gap {st.gap_pct:g}%" if st.reference_value is not None else "no exact reference for this size; optimal for one crew by construction, heuristic for several")),
            ("Runtime", f"{st.runtime_s:.2f} s"),
        ]
    limits = [
        ("One storm", "the risk model is anchored on a single event; it cannot separate storm path from line vulnerability"),
        ("No outage labels", "no per-customer outage records exist; probabilities are damage-intensity estimates, not calibrated forecasts"),
        ("Dates", "repair records are day-granularity and never used as durations"),
        ("Switching", "tie switches and back-feeds are not modeled; any upstream damage is assumed to interrupt service"),
        ("Coverage", f"{int((data.t['customers']['has_path'] == False).sum())} premises have no upstream path and use the fallback"),
    ]
    return {"Observed": observed, "Model output (Simulated)": model, "Assumptions in play": assumptions,
            "Solver status": solver, "Limitations": limits}
