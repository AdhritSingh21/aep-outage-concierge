"""AEP AI Outage Concierge — Streamlit app (HackOHI/O 2026).

Every number on screen is labeled Observed (organizer data), Assumed (see ASSUMPTIONS.md) or Simulated (output of the
scenario). All outputs are pure functions of the Scenario built in the sidebar (concierge/journey.py); this file only
collects inputs and renders.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from concierge import config, evidence, messages as messages_mod, viz
from concierge.data import DataBundle
from concierge.journey import JourneyResult, run
from concierge.restoration import STATE_AFFECTED, STATE_NOT_AFFECTED, STATE_UNKNOWN, fmt_hours
from concierge.scenario import Scenario
from concierge.scenarios import DamageSets
from concierge.scheduling import METRIC_LABELS, POLICY_LABELS, metrics_table

st.set_page_config(page_title="AEP Outage Concierge", page_icon="⚡", layout="wide")

BAND_COLORS = {"Low": "#2e7d32", "Moderate": "#f9a825", "High": "#ef6c00", "Very high": "#c62828", "Unavailable": "#757575"}
TAG = {"Observed": "🟦 Observed", "Assumed": "🟨 Assumed", "Simulated": "🟪 Simulated"}
LEGEND = ("🟦 **Observed** = organizer data as-is · 🟨 **Assumed** = a modeling choice listed in ASSUMPTIONS.md · "
          "🟪 **Simulated** = the output of this scenario (model estimates, including the risk probability)")
QUESTIONS = ["Overview", "Will I lose power?", "How long could restoration take?", "What should I do to prepare?", "Do I qualify for outage alerts?"]
SEVERITY_PHRASE = {0.5: "in a minor storm (0.5× Feb-2025)", 1.0: "in a storm like Feb-2025 (1×)", 2.0: "in a major storm (2× Feb-2025)", 3.0: "in an extreme storm (3× Feb-2025)"}
GUIDE_TAGS = {"PREPARE": "Prepare", "DURING": "During the outage", "NOTE": "Note", "UPSTREAM": "Why a crew nearby may not mean power",
              "MOBILITY": "Mobility / accessibility need", "FACILITY": "Critical facility"}
ZONE_COLS = {"kind": "Zone type", "depth": "Devices below substation", "customers_downstream": "Customers downstream",
             "life_support_downstream": "Life-support downstream", "facilities_downstream": "Critical facilities downstream",
             "oh_miles": "Overhead miles", "risk_share": "Share of your hazard", "tasks": "Repair tasks", "repair_done_h": "Repair done (h)",
             "state": "State", "feb2025_records": "Feb-2025 damage records"}


@st.cache_resource(show_spinner="Loading circuit data…")
def bundle() -> tuple[DataBundle, DamageSets]:
    d = DataBundle()
    return d, DamageSets(d)


def get_journey(scn: Scenario, data: DataBundle, sets: DamageSets) -> JourneyResult:
    """Heavy outputs are cached per Scenario; the estimate-updated message is rebuilt against the scenario just left."""
    cache = st.session_state.setdefault("journeys", {})
    key = scn.key()
    prev: JourneyResult | None = st.session_state.get("last_journey")
    if key not in cache:
        cache[key] = run(scn, data, sets, previous=prev)
    j = cache[key]
    if prev is not None and prev.scenario.key() != key and prev.customer["premise_nu"] == j.customer["premise_nu"]:
        j.messages = messages_mod.build_all(j.customer, j.risk, j.restoration, j.alerts, scn,
                                            previous=prev.restoration, previous_scenario=prev.scenario)
    return j


def audit_append(scn: Scenario, j: JourneyResult) -> None:
    """In-session audit trail: every scenario run with a hash of its outputs (downloadable on Why & limits)."""
    log = st.session_state.setdefault("audit", [])
    digest = hashlib.sha1(json.dumps({"risk": j.risk.percent_text, "band": j.risk.band, "window": j.restoration.window_text,
                                      "alerts": j.alerts.status_text}, sort_keys=True).encode()).hexdigest()[:12]
    entry = {"time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "scenario": scn.to_dict(), "scenario_key": scn.key(),
             "risk_band": j.risk.band, "risk_percent": j.risk.percent_text, "confidence": j.risk.confidence,
             "restoration_state": j.restoration.state, "restoration_window": j.restoration.window_text,
             "alerts": j.alerts.status_text, "output_hash": digest}
    if not log or log[-1]["scenario_key"] != scn.key():
        log.append(entry)


def card(title_tag: str, headline: str, sub: str, color: str) -> None:
    st.markdown(f"<div style='border-left:8px solid {color};padding:0.4rem 0.8rem;min-height:6.2rem'>"
                f"<div style='font-size:0.8rem;opacity:0.75'>{title_tag}</div>"
                f"<div style='font-size:1.9rem;font-weight:700;line-height:1.15'>{headline}</div>"
                f"<div>{sub}</div></div>", unsafe_allow_html=True)


def reset_premise_state(premise: str, cust: pd.Series, default_crews: int) -> None:
    """Per-premise defaults must win over stale widget state when the premise changes (medical flag, calibrated crews)."""
    if st.session_state.get("last_premise") == premise:
        return
    st.session_state["last_premise"] = premise
    st.session_state["crews"] = int(default_crews)
    st.session_state["needs"] = ["medical"] if bool(cust["life_support"]) else []
    st.session_state["opted"] = True
    st.session_state["priority"] = False


def main() -> None:
    data, sets = bundle()
    customers = data.t["customers"]
    samples = data.sample_premises()
    demo = data.demo_story()
    rv = evidence.report_values()

    # ------------------------------------------------------------------ sidebar: demo story, question, location
    st.sidebar.title("⚡ Outage Concierge")
    extent = (f"demo bundle: {len(data.circuits)} of {rv.get('circuits', '?')} circuits, {len(customers):,} of {int(float(rv['customers'])):,} premises"
              if data.is_demo and rv.get("customers") else ("full organizer extract" if not data.is_demo else "demo bundle"))
    st.sidebar.caption(f"HackOHI/O 2026 · AEP challenge · {extent}")
    if st.sidebar.button("Load demo story", help="A hard-hit circuit where the damage nearest the customer is fixed hours before service returns; then two scenario changes that visibly move the estimate"):
        for k, v in config.DEMO_STORY.items():
            st.session_state[k] = v
        if demo:
            st.session_state["premise_text"] = demo["premise_nu"]
            st.session_state["crews"] = int(demo["crews"])
            st.session_state["last_premise"] = demo["premise_nu"]
        st.session_state["demo_step"] = 1
        st.session_state.pop("journeys", None)
        st.session_state.pop("last_journey", None)
    if st.session_state.get("demo_step") and demo:
        with st.sidebar.expander("Demo story (3 steps, numbers measured by scripts/pick_examples.py)", expanded=True):
            for i, step in enumerate(demo.get("steps", []), 1):
                st.markdown(f"{i}. **{step['title']}** – {step['expect']}")
    focus = st.sidebar.radio("Start with your question", QUESTIONS, key="focus")

    mode = st.sidebar.radio("Identify your location", ["Example premises", "Premise ID", "Circuit", "Map coordinates"], key="mode")
    premise = None
    if mode == "Example premises":
        labels = [f"{s['label']} · {s['premise_nu']}" for s in samples]
        pick = st.sidebar.selectbox("Example", labels, key="sample_pick")
        premise = samples[labels.index(pick)]["premise_nu"]
    elif mode == "Premise ID":
        if "premise_text" not in st.session_state:
            st.session_state["premise_text"] = samples[0]["premise_nu"]
        pid = st.sidebar.text_input("Anonymized premise number (9 digits)", key="premise_text")
        premise = pid.strip().zfill(9) if pid.strip() else None
    elif mode == "Circuit":
        circ = st.sidebar.selectbox("Circuit", sorted(customers["circuit_id"].unique()), key="circuit_pick")
        on_circ = customers.loc[customers["circuit_id"] == circ].sort_values(["has_path", "service_kind"], ascending=[False, True])
        opts = on_circ["premise_nu"].tolist()
        st.sidebar.caption(f"{len(opts):,} premises on this circuit; every one can be selected.")
        premise = st.sidebar.selectbox("Premise on that circuit", opts, key="circuit_premise")
    else:
        bb = data.bbox()
        lat = st.sidebar.number_input("Latitude", value=float(customers["lat"].median()), min_value=bb["lat_min"] - 0.5, max_value=bb["lat_max"] + 0.5, format="%.4f", key="lat")
        lon = st.sidebar.number_input("Longitude", value=float(customers["lon"].median()), min_value=bb["lon_min"] - 0.5, max_value=bb["lon_max"] + 0.5, format="%.4f", key="lon")
        premise, km = data.nearest_premise_with_distance(lon, lat)
        if km > 5:
            st.sidebar.warning(f"That point is {km:.0f} km from the nearest premise in this bundle (outside the circuits loaded); showing the nearest anonymized premise {premise}.")
        else:
            st.sidebar.caption(f"Nearest anonymized premise: {premise} ({km:.1f} km away). Coordinates are rounded to about 100 m in the bundle.")
    valid = bool(premise) and premise in data.customers.index
    if not valid:
        st.warning(f"Enter a valid anonymized premise number (see Example premises). This deployment covers {len(data.circuits)} circuits: "
                   + ", ".join(sorted(data.circuits.index)) + ".")
    cust = data.premise(premise) if valid else None
    default_crews = sets.default_crews(str(cust["circuit_id"])) if valid else 2
    if valid:
        reset_premise_state(premise, cust, default_crews)

    # ------------------------------------------------------------------ sidebar: scenario (judges: change anything)
    # widget defaults live in session_state (set once, or by the demo preset / premise reset) so no widget mixes a value= with a key
    for key, default in (("sev", list(config.SEVERITY_OPTIONS)[1]), ("hours", 12), ("crews", int(default_crews)), ("access", False),
                         ("source", "historical"), ("labor", 1.0), ("delay", 0), ("needs", []), ("priority", False), ("opted", True)):
        st.session_state.setdefault(key, default)
    st.sidebar.subheader("Scenario (judges: change anything)")
    sev_label = st.sidebar.select_slider("Storm severity", options=list(config.SEVERITY_OPTIONS), key="sev")
    st.sidebar.caption("Severity scales the Feb-2025 damage rates: it moves the outage risk, and the restoration range only with 'Simulated draws' (the Observed set is a replay).")
    hours = st.sidebar.slider("Hours until the storm arrives", 1, 72, key="hours")
    crews = st.sidebar.slider(f"Crews on your circuit (default {default_crews} is calibrated to the Feb-2025 workload)", config.MIN_CREWS, config.MAX_CREWS, key="crews")
    access = st.sidebar.toggle("Difficult access (ice / flooding)", key="access")
    source = st.sidebar.radio("Damage set", ["historical", "simulated"], key="source",
                              format_func=lambda s: "Observed Feb-2025 records (replay)" if s == "historical" else "Simulated draws at this severity")
    labor = st.sidebar.select_slider("Labor hours vs workbook", options=[0.75, 1.0, 1.25, 1.5], key="labor")
    delay = st.sidebar.slider("Crew start delay after the storm (h)", 0, 24, key="delay")
    needs = st.sidebar.multiselect("Needs you declare for this session (nothing is inferred)", options=list(config.NEEDS), key="needs",
                                   format_func=lambda k: config.NEEDS[k],
                                   help="The medical need is pre-filled from the account's own life-support flag when one is on file; remove it or add others freely.")
    prioritize = st.sidebar.toggle("Prioritize critical loads in the schedule (Assumed L10)", key="priority",
                                   help="Weights life-support customers and critical facilities in the dependency-aware ordering; the objective shown stays customer-outage hours.")
    opted = st.sidebar.toggle("Enrolled in outage alerts (opted in)", key="opted")
    if not valid:
        return

    scn = Scenario(premise_nu=premise, severity=config.SEVERITY_OPTIONS[sev_label], crews=int(crews), hours_to_landfall=float(hours),
                   access_difficulty=bool(access), labor_multiplier=float(labor), damage_source=source, medical_need="medical" in needs,
                   opted_in=bool(opted), crew_start_delay_h=float(delay), needs=tuple(needs), prioritize_critical=bool(prioritize))
    with st.spinner("Running the scenario…"):
        j = get_journey(scn, data, sets)
    st.session_state["last_journey"] = j
    audit_append(scn, j)
    risk, est, al = j.risk, j.restoration, j.alerts

    # ------------------------------------------------------------------ header
    st.title("Will I lose power, and for how long?")
    st.caption(f"Premise {j.customer['premise_nu']} · {j.customer['city'] or 'city n/a'} · circuit {j.customer['circuit_id']} · "
               f"{j.customer['service_kind']} service · {TAG['Observed']} data from the AEP HackOHI/O 2026 package")
    st.caption(LEGEND)

    tabs = st.tabs(["Your outlook", "Messages", "Operations view", "Why & limits", "Data & results"])

    # ------------------------------------------------------------------ tab 1: outlook
    with tabs[0]:
        c1, c2, c3 = st.columns(3)
        with c1:
            if risk.reliable:
                card(f"{TAG['Simulated']} · outage risk {SEVERITY_PHRASE.get(scn.severity, 'at this severity')}", risk.band,
                     f"{risk.percent_text} chance · confidence <b>{risk.confidence}</b>", BAND_COLORS.get(risk.band, "#777"))
            else:
                card(f"{TAG['Simulated']} · outage risk {SEVERITY_PHRASE.get(scn.severity, 'at this severity')}", "Being assessed",
                     f"circuit average suggests <b>{risk.band}</b> ({risk.percent_text}) · confidence <b>{risk.confidence}</b>", BAND_COLORS["Unavailable"])
        with c2:
            if est.state == STATE_AFFECTED:
                card(f"{TAG['Simulated']} · restoration range {est.clock_label}", est.window_text,
                     f"most likely about <b>{fmt_hours(est.mid_total)}</b> · {est.source.split(' (')[0]}", "#1f77b4")
            elif est.state == STATE_UNKNOWN:
                card(f"{TAG['Simulated']} · restoration", "Being assessed", "no upstream path on file; a person reviews before any time is sent", "#757575")
            else:
                alt = est.alt_window_text
                sub = (f"simulated storms at this severity: affected in {100 * est.alt['p_affected']:.0f}%, then <b>{alt}</b> {est.clock_label}" if alt
                       else "simulated storms at this severity never hit your path either")
                card(f"{TAG['Simulated']} · restoration (Feb-2025 replay)", "No damage on your line", sub, "#1f77b4")
        with c3:
            card(f"{TAG['Assumed']} rules · alerts", al.status_text.split(" · ")[0],
                 f"{' · '.join(al.status_text.split(' · ')[1:])} · pre-storm alert: {'yes' if al.eligible_pre_storm else 'no'}", "#6a1b9a")
        st.caption("Bands: Low < 15%, Moderate 15–40%, High 40–70%, Very high ≥ 70% (Assumed R5). Confidence grades the data behind your line "
                   "(High = path on file, ≥ 10 Feb-2025 reports on the circuit and pole ages known for ≥ 50% of poles; Medium = path on file; "
                   "Low = no path, circuit average), not forecast accuracy: the probability is a damage-intensity estimate from one storm.")

        sections = {
            "Will I lose power?": lambda: section_risk(j, scn),
            "How long could restoration take?": lambda: section_restoration(j, data),
            "What should I do to prepare?": lambda: section_prepare(j),
            "Do I qualify for outage alerts?": lambda: section_alerts(j),
        }
        order = list(sections)
        if focus in sections:
            order.remove(focus)
            order.insert(0, focus)
        for name in order:
            sections[name]()

    # ------------------------------------------------------------------ tab 2: messages
    with tabs[1]:
        st.caption("Drafts of the four messages this scenario would trigger. Each separates what we know (🟦 Observed), what we estimate "
                   "(🟪 Simulated) and what you can do. The estimate-updated draft compares against the scenario you just left and applies rule E5.")
        if not al.enrolled:
            st.info("Not enrolled: this customer qualifies for alerts but opted out, so none of these would be sent. Switch 'Enrolled in outage alerts' on to preview delivery.")
        for stage, m in j.messages.items():
            with st.container(border=True):
                sent = "would be sent" if m.would_send else "would not be sent"
                st.markdown(f"**{m.title}** · {m.channel} · *{sent}*")
                st.markdown(f"**What we know** ({TAG['Observed']}): {m.know}")
                st.markdown(f"**What we estimate** ({TAG['Simulated']}): {m.estimate}")
                st.markdown(f"**What you can do:** {m.actions}")
                if m.note:
                    st.caption(m.note)
                st.caption(f"{m.footer} · {len(m.body)} characters ({max(1, -(-len(m.body) // 160))} SMS segments)")

    # ------------------------------------------------------------------ tab 3: operations
    with tabs[2]:
        st.caption(f"{TAG['Simulated']} crew schedules for circuit {j.customer['circuit_id']} under identical inputs "
                   f"({est.n_tasks_circuit} tasks, {est.crews} crews, {est.source.split(' (')[0]}). Objective: customer-outage hours, "
                   "each premise counted once; a customer is restored only when every damaged zone on its path is repaired, so the circuit "
                   "comes back step by step, zone by zone. FIFO stands in for report-order dispatch and the customers-per-hour heuristic for "
                   "the common most-customers-first rule; both see exactly the same tasks, hours and crews.")
        if est.policies and est.instance is not None and len(est.policies) >= 2:
            tbl = metrics_table(est.policies).rename(columns=METRIC_LABELS)
            st.dataframe(tbl, hide_index=True, width="stretch")
            m = est.policies["dependency_aware"].metrics
            ls_txt = (f"{est.instance.affected_life_support} life-support customer{'s' if est.instance.affected_life_support != 1 else ''} affected, last one back at {fmt_hours(m.life_support_t100_h)}"
                      if est.instance.affected_life_support else "no life-support customer affected")
            fac_txt = (f"{est.instance.affected_facilities} critical facilit{'ies' if est.instance.affected_facilities != 1 else 'y'} affected, last one back at {fmt_hours(m.facility_t100_h)}"
                       if est.instance.affected_facilities else "no critical facility affected")
            st.caption(f"Priority loads on this circuit under the policy shown (hours after crews start): {ls_txt}; {fac_txt}. "
                       + ("Critical-load weighting is ON (Assumed L10)." if scn.prioritize_critical else "Critical-load weighting is off; switch it on in the sidebar to compare."))
            st.plotly_chart(viz.restoration_curves(est.policies, est.instance, est.service_restored_h if est.state == STATE_AFFECTED else None), width="stretch")
            st.caption("Customers restored over time (hours after crews start) for the three policies; the dotted line is your service.")
            pol = st.selectbox("Show crew timeline for", list(est.policies), format_func=lambda p: POLICY_LABELS[p], index=len(est.policies) - 1)
            path_zones = set(str(j.customer["path_zone_ids"]).split("|")) if j.customer["has_path"] else set()
            st.plotly_chart(viz.gantt(est.policies[pol], est.instance, path_zones), width="stretch")
            st.caption("Hatched red bars are repairs on your own upstream path; blue bars are the rest of the circuit.")
            st.markdown("**Solver status**")
            for k, v in j.evidence["Solver status"]:
                st.markdown(f"- {k}: {v}")
        else:
            st.write("No repair tasks in this damage set for this circuit.")

    # ------------------------------------------------------------------ tab 4: why & limits
    with tabs[3]:
        st.markdown("**How this assistant works.** There is no chatbot: a damage-rate model (Poisson prior per circuit, empirical-Bayes zone rates "
                    "from the Feb-2025 storm), a labor model from the organizer workbook, a dependency-aware repair scheduler and documented rules "
                    "generate every sentence from the one Scenario in the sidebar, so each answer is reproducible and auditable.")
        for section, rows in j.evidence.items():
            if section == "Solver status":
                continue
            st.subheader(section)
            for k, v in rows:
                st.markdown(f"- **{k}:** {v}")
        st.subheader("Privacy")
        st.markdown("- Premises are identified only by their anonymized number and city. Address, road, meter and annotation fields are dropped at the "
                    "ETL step and never loaded; the committed demo bundle carries no customer ids or structure numbers, and its coordinates are rounded "
                    "to about 100 m.\n"
                    "- Critical facilities appear only as counts per zone; their names and locations are not stored in the bundle or displayed.\n"
                    "- The needs list is declared for this session; the account's life-support flag pre-fills it and can be removed. In this prototype "
                    "there is no login, so that flag is visible to anyone who enters the premise number (a production build would require authentication).\n"
                    "- Audit trail: every scenario run in this session is logged with a hash of its outputs (download below).")
        log = st.session_state.get("audit", [])
        st.download_button("Download this session's audit log (JSON)", data=json.dumps(log, indent=1), file_name="outage_concierge_audit.json",
                           mime="application/json")
        st.caption(f"{len(log)} scenario run{'s' if len(log) != 1 else ''} logged this session.")

    # ------------------------------------------------------------------ tab 5: data & results
    with tabs[4]:
        st.caption(("Generated from the full organizer extract; this deployment runs the committed demo bundle "
                    f"({len(data.circuits)} circuits, {len(customers):,} premises). " if data.is_demo else "")
                   + "Every table below was produced by a script in scripts/ (see README).")
        for name, path, open_ in (("Scheduling benchmark", config.RESULTS / "benchmark_summary.md", True),
                                  ("Risk model validation", config.RESULTS / "risk_validation.md", True),
                                  ("Scaling probe (measured)", config.RESULTS / "scale_probe.md", False),
                                  ("Data report", config.DATA / "data_report.md", False),
                                  ("Assumptions register", config.ROOT / "ASSUMPTIONS.md", False)):
            with st.expander(name, expanded=open_):
                p = Path(path)
                st.markdown(p.read_text(encoding="utf8") if p.exists() else f"_{name} not generated yet._")
        st.subheader("Labor bundles (Observed CU averages combined per ASSUMPTIONS L2)")
        lb = data.labor.bundle_summary().rename(columns={"bundle": "Bundle", "base_hours": "Workbook hours (before ILM)",
                                                          "crew_hours_with_ilm": "Person-hours with ILM"})
        if "Person-hours with ILM" in lb.columns:
            lb["Clock hours for one 4-person crew"] = (lb["Person-hours with ILM"] / config.CREW_SIZE).round(1)
        st.dataframe(lb, hide_index=True, width="stretch")


# ---------------------------------------------------------------------- outlook sections
def section_risk(j: JourneyResult, scn: Scenario) -> None:
    risk = j.risk
    st.subheader("Will I lose power?")
    for f in risk.factors:
        share = f" ({'>99%' if f.get('share') and f['share'] > 0.995 else f'{f['share']:.0%}'} of your hazard)" if f.get("share") else ""
        st.markdown(f"- **{f['name']}**{share}: {f['detail']}")
    if risk.sensitivity:
        st.markdown("**What moves this risk** · " + " · ".join(f"{s:g}× storm: {100 * p:.0f}%" for s, p in sorted(risk.sensitivity.items()))
                    + ("  (near-certain at every severity: the storm slider cannot move this card)" if min(risk.sensitivity.values()) >= 0.995 else ""))
    for flag in risk.flags:
        st.warning(flag)
    if not risk.reliable:
        st.info("Fallback in use: without an upstream path on file the estimate uses the circuit average, cannot show your own dependency chain, "
                "and is shown as 'being assessed' rather than sent as a number (rule E6).")


def section_restoration(j: JourneyResult, data: DataBundle) -> None:
    est = j.restoration
    st.subheader("How long could restoration take?")
    st.write(est.narrative)
    if est.what_moves_it:
        st.markdown("**What moves this estimate** (ranked, Simulated)")
        for w in est.what_moves_it:
            st.markdown(f"- {w}")
    if est.draws and "service_p50" in est.draws:
        d = est.draws
        st.caption(f"{TAG['Simulated']} {d['n_draws']} draws: affected in {d['p_affected']:.0%}; service time P10/P50/P90 = "
                   f"{fmt_hours(est.total(d['service_p10']))}/{fmt_hours(est.total(d['service_p50']))}/{fmt_hours(est.total(d['service_p90']))} {est.clock_label}; "
                   f"mean {d['tasks_mean']:.0f} tasks per draw.")
    st.markdown("**Your upstream dependency chain** · substation → protective zones → you; a zone's shape and colour show whether it is undamaged, "
                "repaired before your service returns, or the last blocking repair.")
    if len(j.path_table):
        st.plotly_chart(viz.dependency_chain(j.path_table, est.total(est.service_restored_h) if est.state == STATE_AFFECTED else None,
                                             est.state == STATE_AFFECTED), width="stretch")
        if j.missing_path_zones:
            st.caption(f"{j.missing_path_zones} zone(s) on this path are missing from the zone table and are not drawn.")
        with st.expander("Zones on your path (substation → you)"):
            tbl = j.path_table.drop(columns=["zone_id", "label"]).rename(columns=ZONE_COLS)
            st.dataframe(tbl, hide_index=True, width="stretch")
        with st.expander("Map of your circuit's protective zones"):
            states = dict(zip(j.path_table["zone_id"], j.path_table["state"]))
            fig = viz.zone_map(data.circuit_zones(str(j.customer["circuit_id"])), list(j.path_table["zone_id"]), states,
                               j.customer["leaf_zone_id"] if isinstance(j.customer.get("leaf_zone_id"), str) else None)
            if fig is None:
                st.write("No zone coordinates are available for this circuit.")
            else:
                st.plotly_chart(fig, width="stretch")
                st.caption("Zone centroids only (Observed device locations); no premise or facility points are drawn. Your own zone is marked at its centroid, not at your home.")
    else:
        st.write("No upstream path is on file for this premise, so the chain cannot be drawn.")


def section_prepare(j: JourneyResult) -> None:
    st.subheader("What should I do to prepare?")
    st.caption(f"{TAG['Assumed']} guidance adapted from public safety sources (ASSUMPTIONS C4); scaled to your risk band, the expected duration and the needs you declared.")
    sources = []
    for g in j.guidance:
        if g.source not in sources:
            sources.append(g.source)
        if g.tag == "SAFETY":
            st.error(f"**SAFETY** · {g.text}")
        elif g.tag == "MEDICAL":
            st.warning(f"**MEDICAL NEED** · {g.text}")
        elif g.tag in ("MOBILITY", "FACILITY"):
            st.warning(f"**{GUIDE_TAGS[g.tag].upper()}** · {g.text}")
        else:
            st.markdown(f"- **{GUIDE_TAGS.get(g.tag, g.tag.title())}** · {g.text}")
    from concierge.guidance import SOURCE_LABELS
    st.caption("Sources: " + "; ".join(SOURCE_LABELS.get(s, s) for s in sources) + ". Full citations in README → Sources.")


def section_alerts(j: JourneyResult) -> None:
    al = j.alerts
    st.subheader("Do I qualify for outage alerts?")
    st.markdown(f"**{al.status_text}**")
    for r in al.reasons:
        st.markdown(f"- {r}")
    if al.schedule:
        st.markdown("**When and how you would be contacted** (Assumed E4, E5)")
        st.table(pd.DataFrame(al.schedule).rename(columns={"stage": "Message", "when": "When", "channels": "Channels"}))
    else:
        st.caption("No messages are scheduled: the customer is not enrolled.")
    esc = pd.DataFrame(al.escalations)
    esc["Active now?"] = esc["active"].map({True: "✅ yes", False: "no"})
    st.markdown("**Escalation triggers** (Assumed E6–E8; all rules shown, active ones marked)")
    st.table(esc.drop(columns=["active"]).rename(columns={"trigger": "Trigger", "action": "Action"}))
    with st.expander("Alert rules used (Assumed, not AEP policy)"):
        for r in al.rules:
            st.markdown(f"- **{r['id']}** ({r['basis']}): {r['rule']}")


if __name__ == "__main__" or True:
    main()
