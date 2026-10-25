"""Compact evidence / results panel: what was Observed, what the model produced, which Assumptions were in play,
solver status, validation numbers, what changed after testing, and limitations.

Every number here is read from a script-produced artifact (results/*.json, data/data_report.md) or computed from the
loaded tables; nothing is typed in by hand (CLAUDE.md rule 3).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from . import config
from .alerts import AlertDecision
from .data import DataBundle
from .restoration import STATE_AFFECTED, STATE_UNKNOWN, RestorationEstimate, fmt_hours
from .risk import RiskResult
from .scenario import Scenario


def benchmark_summary(results_dir: Path | None = None) -> str | None:
    p = (results_dir or config.RESULTS) / "benchmark_summary.md"
    return p.read_text(encoding="utf8") if p.exists() else None


def benchmark_headline(results_dir: Path | None = None) -> dict | None:
    p = (results_dir or config.RESULTS) / "benchmark_headline.json"
    try:
        return json.loads(p.read_text(encoding="utf8")) if p.exists() else None
    except Exception:
        return None


def report_values(report_path: Path | None = None) -> dict[str, str]:
    """`| key | value |` rows of the generated data report (full organizer extract), as strings."""
    p = report_path or (config.DATA / "data_report.md")
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for m in re.finditer(r"^\|\s*([a-z0-9_]+)\s*\|\s*([^|]+?)\s*\|\s*$", p.read_text(encoding="utf8"), re.M):
        out[m.group(1)] = m.group(2)
    return out


def _pct(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if v != v else f"{100 * v:.0f}%"


def _num(d: dict | None, *keys, default=None):
    cur = d or {}
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def changed_after_testing(data: DataBundle) -> list[tuple[str, str]]:
    """What broke and what was changed, with the numbers the pipeline scripts produced (data report, risk model, benchmark)."""
    rv = report_values()
    val = data.model.validation if hasattr(data.model, "validation") else {}
    bh = benchmark_headline()
    rows = []
    if rv:
        rows.append(("Recloser join", f"matching path tokens to the recloser layer by id matched {rv.get('recloser_tokens_id_in_layer_pct', '?')}% of tokens; "
                                      f"by location {rv.get('recloser_tokens_loc_in_layer_pct', '?')}% → zones use location for reclosers, id for fuses (A2)"))
        rows.append(("Circuit keys", f"the anonymized circuit_id disagreed between GIS layers; remapping to the transformer layer's (station, circuit) key moved "
                                     f"{rv.get('circuit_ids_remapped_to_transformer_key', '?')} rows and left {rv.get('damage_unresolved_keys', '?')} damage keys unresolved (A3, A5)"))
        rows.append(("Units and dates", f"report_len confirmed as feet (ratio to geodesic length {rv.get('report_len_over_geodesic_ft_median', '?')}); "
                                        f"{rv.get('damage_placeholder_repair_dates_nulled', '?')} placeholder repair dates nulled and never used as durations (A7, L7)"))
    if val:
        rows.append(("Risk features", f"leave-one-circuit-out Spearman {val.get('loo_spearman_model', '?')} for the feature model vs {val.get('loo_spearman_exposure_only', '?')} "
                                      f"for overhead miles alone (MAE {val.get('loo_mae_model', '?')} vs {val.get('loo_mae_exposure_only', '?')}): the circuit features add little, "
                                      f"so the zone's own Feb-2025 count (empirical-Bayes shrinkage) carries the estimate and the app says so (R2, R3)"))
    if bh:
        rows.append(("Greedy heuristic", f"customers-per-hour ignores the AND-coupling between zones; the dependency-aware order cut customer-outage hours by "
                                         f"{_num(bh, 'all', 'vs_customers_first', 'mean_pct', default='?')}% on average across {_num(bh, 'instances', 'n_instances', default='?')} identical instances "
                                         f"and lost on {_num(bh, 'losses_vs_customers_first', 'n', default='?')} of them (worst {_num(bh, 'all', 'vs_customers_first', 'worst_pct', default='?')}%)"))
        rows.append(("Swap pass", f"adds {_num(bh, 'all', 'vs_dependency_aware_horn', 'mean_pct', default='?')}% on average over Horn's order alone; kept for its tail "
                                  f"(max {_num(bh, 'swap_pass', 'max_gain_pct', default='?')}%) and capped at {config.SWAP_EVALUATION_CAP} evaluations in the app so the schedule does not depend on the machine (L9)"))
        rows.append(("Benchmark bookkeeping", f"the first summary counted 'calibrated default' only for the {_num(bh, 'by_crews', 'default_multi_crew_circuits', 'n_circuits', default='?')} circuits whose default exceeds one crew "
                                              f"({_num(bh, 'by_crews', 'default_multi_crew_circuits', 'mean_reduction_vs_customers_first_pct', default='?')}% vs the heuristic); at every circuit's own default it is "
                                              f"{_num(bh, 'by_crews', 'default_all_circuits', 'mean_reduction_vs_customers_first_pct', default='?')}%, and the repair-gap sentence is now computed from the damage table instead of typed"))
    rows.append(("Crew start delay", "an early build added the delay in the schedule and again at display; schedules are now solved from hour 0 and the delay is added once (tested)"))
    return rows


def build(scn: Scenario, data: DataBundle, cust: pd.Series, circuit: pd.Series, risk: RiskResult,
          est: RestorationEstimate, alerts: AlertDecision, medical_need: bool) -> dict[str, list[tuple[str, str]]]:
    n_path_zones = len(str(cust["path_zone_ids"]).split("|")) if cust["has_path"] and isinstance(cust.get("path_zone_ids"), str) else 0
    fac = data.circuit_facilities(str(cust["circuit_id"]))
    fac_txt = ", ".join(f"{v} {k}" for k, v in fac.items() if v) or "none mapped"
    observed = [
        ("Circuit", f"{cust['circuit_id']} — {int(circuit['n_customers']):,} customers, {circuit['oh_miles']:.1f} mi overhead / {circuit['ug_miles']:.1f} mi underground primary, {int(circuit['n_zones'])} protective zones"),
        ("Feb-2025 damage on circuit", f"{int(circuit['n_damage_ref'])} records (wire {int(circuit['n_damage_ref_wire'])}, pole {int(circuit['n_damage_ref_pole'])}, equipment {int(circuit['n_damage_ref_equipment'])})"
                                       + (f"; access issues on {_pct(circuit['access_issue_rate_ref'])} of them" if int(circuit['n_damage_ref']) else "")),
        ("Feb-2025 affected share", f"damage sat on the upstream path of {_pct(circuit.get('affected_share_ref', 0))} of the circuit's customers"),
        ("Your service", f"{cust['service_kind']} service; upstream path " + (f"resolved ({n_path_zones} zone{'s' if n_path_zones != 1 else ''} to the substation)" if cust['has_path'] else "not on file")),
        ("Priority loads on circuit", f"{int(circuit.get('n_life_support', 0))} life-support customers (Observed account flags); critical facilities fed from this circuit: {fac_txt} (counts only, locations never shown)"),
        ("Declared need on file", "life-support flag (shown to this premise only; you can switch the toggle off)" if bool(cust["life_support"]) else "none"),
        ("Labor reference", f"workbook CU averages; indirect-labor multiplier (ILM) {data.labor.ilm:g} covers travel, safety briefing and set-up"),
    ]
    sens = " · ".join(f"{s:g}× {100 * p:.0f}%" for s, p in sorted(risk.sensitivity.items())) if risk.sensitivity else "n/a"
    if est.state == STATE_AFFECTED:
        rest = f"{est.window_text} {est.clock_label}; most likely {fmt_hours(est.mid_total)}; basis: {est.range_basis}; source: {est.source}"
        rvs = (f"damage nearest you done at {fmt_hours(est.total(est.local_repair_done_h))}, service at {fmt_hours(est.total(est.service_restored_h))} ({est.blocking_zone_label})"
               if est.local_repair_done_h is not None else "—")
    elif est.state == STATE_UNKNOWN:
        rest, rvs = f"cannot be placed on the network (no upstream path on file); source: {est.source}", "—"
    else:
        rest = f"no damage on your line in this damage set ({est.source})"
        if est.alt_window_text:
            rest += f"; simulated storms of this severity: affected in {_pct(est.alt['p_affected'])}, then {est.alt_window_text} {est.clock_label}"
        rvs = "—"
    model = [
        ("Outage risk", f"{risk.band} — {risk.percent_text} at severity {scn.severity:g}× (hazard Λ = {risk.hazard:.2f}; confidence {risk.confidence}; method {risk.method})"
                        + ("; shown as 'being assessed' because the fallback is in use" if not risk.reliable else "")),
        ("Risk by severity", sens),
        ("Restoration", rest),
        ("Repair vs service", rvs),
        ("Alerts", f"{alerts.status_text}; pre-storm {'yes' if alerts.eligible_pre_storm else 'no'}; channels {', '.join(alerts.channels)}"
                   + ("; a person reviews before any number is sent (E6)" if alerts.review_before_sending else "")),
    ]
    needs_txt = ", ".join(config.NEEDS[n] for n in scn.needs) if scn.needs else "none declared in this session"
    assumptions = [
        ("Storm", f"severity {scn.severity:g}× the Feb-2025 rates; footprint covers all circuits (R1, R5); arrives in {scn.hours_to_landfall:g} h"),
        ("Crews", f"{scn.crews} identical 4-person crews on this circuit, released {scn.crew_start_delay_h:g} h after the storm ends; that delay is added once to every time shown (L5)"),
        ("Labor", f"ILM {data.labor.ilm:g} applied once (covers travel and nonproductive time); task clock hours = person-hours ÷ {config.CREW_SIZE}; labor × {scn.labor_multiplier:g}; range: {est.range_basis} (L2, L8)"),
        ("Access", "difficult access adds 0.5 h × ILM to every task (L3)" if scn.access_difficulty else "no circuit-wide access penalty (L3)"),
        ("Damage set", "Observed Feb-2025 records on this circuit, quantity 1 each (L1)" if scn.damage_source == "historical" else f"{scn.n_draws} seeded Poisson draws from the zone hazards (L8)"),
        ("Priority loads", f"life-support customers and critical facilities weighted {config.PRIORITY_WEIGHT_CUSTOMERS:g}× in the ordering (L10)" if scn.prioritize_critical else "not weighted in the ordering; reported separately (L10)"),
        ("Medical need", "declared by the customer in this session" if medical_need and not bool(cust["life_support"]) else ("account flag, shown to this premise only (C2)" if medical_need else "none declared")),
        ("Other declared needs", needs_txt),
    ]
    st = est.status
    solver = []
    if st is not None:
        solver = [
            ("Policy shown", f"{st.policy} — {st.order_source}"),
            ("Instance", f"{st.n_tasks} tasks, {st.crews} crews, {est.affected_customers_circuit:,} affected customers (each counted once)"),
            ("Improvement pass", f"{'ran' if st.improvement_pass else 'not run'}" + (f": {st.improvement_iterations} swap evaluations, {st.improvement_pct:g}% better" if st.improvement_pass else "")),
            ("Optimality", (f"{st.reference_kind}: gap {st.gap_pct:g}%" if st.reference_value is not None else "no exact reference for this size; optimal for one crew by construction, heuristic for several")),
            ("Runtime", f"{st.runtime_s:.2f} s" + ("; " + " ".join(n for n in st.notes if "cap" in n) if any("cap" in n for n in st.notes) else "")),
        ]
    val = getattr(data.model, "validation", {}) or {}
    bh = benchmark_headline()
    validation = []
    if val:
        validation.append(("Risk prior, leave-one-circuit-out", f"Spearman {val.get('loo_spearman_model', '?')} (model) vs {val.get('loo_spearman_exposure_only', '?')} (overhead miles only); "
                                                               f"MAE {val.get('loo_mae_model', '?')} vs {val.get('loo_mae_exposure_only', '?')} records per circuit, {val.get('n_circuits', '?')} circuits — the features add little; the honest reading is that one storm cannot separate storm path from line vulnerability"))
    if bh:
        validation.append(("Scheduler benchmark", f"{_num(bh, 'instances', 'n_instances', default='?')} identical instances on {_num(bh, 'instances', 'n_circuits', default='?')} circuits: "
                                                  f"{_num(bh, 'all', 'vs_fifo', 'mean_pct', default='?')}% fewer customer-outage hours than FIFO, "
                                                  f"{_num(bh, 'all', 'vs_customers_first', 'mean_pct', default='?')}% fewer than the customers-per-hour heuristic "
                                                  f"({_num(bh, 'by_crews', 'default_all_circuits', 'mean_reduction_vs_customers_first_pct', default='?')}% at every circuit's calibrated crew count; "
                                                  f"win rate {_num(bh, 'all', 'vs_customers_first', 'win_rate_pct', default='?')}%, {_num(bh, 'losses_vs_customers_first', 'n', default='?')} losses, worst {_num(bh, 'all', 'vs_customers_first', 'worst_pct', default='?')}%); "
                                                  f"brute force on {_num(bh, 'brute_force_reference', 'instances', default='?')} small instances: {_num(bh, 'brute_force_reference', 'at_optimum', default='?')} at the optimum, "
                                                  f"max gap {_num(bh, 'brute_force_reference', 'max_gap_pct', default='?')}%"))
    validation.append(("Unit tests", "AND-restoration semantics, no double counting, brute-force parity, one-Scenario consistency, delay counted once, band/percent agreement, bundle privacy (tests/)"))
    limits = [
        ("One storm", "the risk model is anchored on a single event; it cannot separate storm path from line vulnerability"),
        ("No outage labels", "no per-customer outage records exist; probabilities are damage-intensity estimates, not calibrated forecasts; 'confidence' grades data coverage, not forecast accuracy"),
        ("Dates", "repair records are day-granularity and never used as durations"),
        ("Switching", "tie switches and back-feeds are not modeled; any upstream damage is assumed to interrupt service"),
        ("Travel", "no site-to-site travel model: travel and nonproductive time enter only through the workbook's indirect-labor multiplier"),
        ("Coverage", f"{int((data.t['customers']['has_path'] == False).sum())} of {len(data.t['customers']):,} premises in the loaded bundle have no upstream path and use the fallback"
                     + ("; this deployment runs the 9-circuit demo bundle, the Data report describes the full extract" if data.is_demo else "")),
    ]
    return {"Observed": observed, "Model output (Simulated)": model, "Assumptions in play": assumptions,
            "Solver status": solver, "Validation (script-produced)": validation,
            "What we changed after testing": changed_after_testing(data), "Limitations": limits}
