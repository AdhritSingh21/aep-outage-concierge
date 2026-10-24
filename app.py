"""AEP AI Outage Concierge — Streamlit app (HackOHI/O 2026).

Every number on screen is labeled Observed (organizer data), Assumed (see ASSUMPTIONS.md) or Simulated (scenario
output). All outputs are pure functions of the Scenario built in the sidebar.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from concierge import config, viz
from concierge.data import DataBundle
from concierge.journey import JourneyResult, run
from concierge.restoration import fmt_hours
from concierge.scenario import Scenario
from concierge.scenarios import DamageSets
from concierge.scheduling import POLICY_LABELS, metrics_table

st.set_page_config(page_title="AEP Outage Concierge", page_icon="⚡", layout="wide")

BAND_COLORS = {"Low": "#2e7d32", "Moderate": "#f9a825", "High": "#ef6c00", "Very high": "#c62828", "Unavailable": "#757575"}
TAG = {"Observed": "🟦 Observed", "Assumed": "🟨 Assumed", "Simulated": "🟪 Simulated"}


@st.cache_resource(show_spinner="Loading circuit data…")
def bundle() -> tuple[DataBundle, DamageSets]:
    d = DataBundle()
    return d, DamageSets(d)


def get_journey(scn: Scenario, data: DataBundle, sets: DamageSets) -> JourneyResult:
    cache = st.session_state.setdefault("journeys", {})
    key = scn.key()
    if key not in cache:
        prev = st.session_state.get("last_journey")
        cache[key] = run(scn, data, sets, previous=prev)
    return cache[key]


def main() -> None:
    data, sets = bundle()
    customers = data.t["customers"]
    samples = data.sample_premises()

    # ------------------------------------------------------------------ sidebar: location + scenario
    st.sidebar.title("⚡ Outage Concierge")
    st.sidebar.caption("HackOHI/O 2026 · AEP challenge · " + ("demo bundle" if data.is_demo else "full organizer extract"))
    if st.sidebar.button("Load demo story", help="Hard-hit circuit, customer behind damaged upstream zones; then change crews and access"):
        demo = st.session_state.get("demo_pick") or sets.demo_premise()
        st.session_state["demo_pick"] = demo
        for k, v in config.DEMO_STORY.items():
            st.session_state[k] = v
        if demo:
            st.session_state["premise_text"] = demo["premise_nu"]
            st.session_state["crews"] = demo["crews"]
        st.session_state["demo_step"] = 1
        st.session_state.pop("journeys", None)
    if st.session_state.get("demo_step"):
        demo = st.session_state.get("demo_pick") or {}
        with st.sidebar.expander("Demo story (3 steps)", expanded=True):
            st.markdown(f"1. **Baseline** – premise {demo.get('premise_nu', '?')} on circuit {demo.get('circuit_id', '?')} with the Observed "
                        f"Feb-2025 damage set and {demo.get('crews', '?')} crews. Note the repair-vs-service gap "
                        f"(~{demo.get('repair_to_service_gap_h', '?')} h) in the chain and the narrative.\n"
                        f"2. **Double the crews** – the schedule, the range, the chain hours, the explanation and the "
                        f"*estimate-updated* message all move together.\n"
                        f"3. **Toggle difficult access** – every task gets the access adder; watch the same four things move the other way.")
    mode = st.sidebar.radio("Identify your location", ["Example premises", "Premise ID", "Circuit", "Map coordinates"], key="mode", horizontal=False)
    premise = None
    if mode == "Example premises":
        labels = [f"{s['label']} · {s['premise_nu']}" for s in samples]
        pick = st.sidebar.selectbox("Example", labels, key="sample_pick")
        premise = samples[labels.index(pick)]["premise_nu"]
    elif mode == "Premise ID":
        pid = st.sidebar.text_input("Anonymized premise number (9 digits)", value=st.session_state.get("premise_text", samples[0]["premise_nu"]), key="premise_text")
        premise = pid.strip().zfill(9) if pid.strip() else None
    elif mode == "Circuit":
        circ = st.sidebar.selectbox("Circuit", sorted(customers["circuit_id"].unique()), key="circuit_pick")
        opts = customers.loc[customers["circuit_id"] == circ, "premise_nu"].head(200).tolist()
        premise = st.sidebar.selectbox("Premise on that circuit", opts, key="circuit_premise")
    else:
        lat = st.sidebar.number_input("Latitude", value=float(customers["lat"].median()), format="%.5f", key="lat")
        lon = st.sidebar.number_input("Longitude", value=float(customers["lon"].median()), format="%.5f", key="lon")
        premise = data.nearest_premise(lon, lat)
        st.sidebar.caption(f"Nearest anonymized premise: {premise}")
    if not premise or premise not in data.customers.index:
        st.warning("Enter a valid anonymized premise number (see Example premises).")
        return
    cust = data.premise(premise)
    default_crews = sets.default_crews(str(cust["circuit_id"]))

    st.sidebar.subheader("Scenario (judges: change anything)")
    sev_label = st.sidebar.select_slider("Storm severity", options=list(config.SEVERITY_OPTIONS), value=st.session_state.get("sev", list(config.SEVERITY_OPTIONS)[1]), key="sev")
    hours = st.sidebar.slider("Hours until the storm arrives", 1, 72, st.session_state.get("hours", 12), key="hours")
    crews = st.sidebar.slider(f"Crews on your circuit (default {default_crews} is calibrated to the Feb-2025 workload)", config.MIN_CREWS, config.MAX_CREWS,
                              int(st.session_state.get("crews", default_crews)), key="crews")
    access = st.sidebar.toggle("Difficult access (ice / flooding)", value=st.session_state.get("access", False), key="access")
    source = st.sidebar.radio("Damage set", ["historical", "simulated"], index=0 if st.session_state.get("source", "historical") == "historical" else 1, key="source",
                              format_func=lambda s: "Observed Feb-2025 records" if s == "historical" else "Simulated draws at this severity")
    labor = st.sidebar.select_slider("Labor hours vs workbook", options=[0.75, 1.0, 1.25, 1.5], value=st.session_state.get("labor", 1.0), key="labor")
    delay = st.sidebar.slider("Crew start delay after the storm (h)", 0, 24, int(st.session_state.get("delay", 0)), key="delay")
    medical_default = bool(cust["life_support"])
    medical = st.sidebar.toggle("I rely on powered medical equipment", value=st.session_state.get("medical", medical_default), key="medical",
                                help="Pre-filled from the declared flag on this account; nothing is inferred.")
    opted = st.sidebar.toggle("Opted in to outage alerts", value=True, key="opted")

    scn = Scenario(premise_nu=premise, severity=config.SEVERITY_OPTIONS[sev_label], crews=int(crews), hours_to_landfall=float(hours),
                   access_difficulty=bool(access), labor_multiplier=float(labor), damage_source=source, medical_need=bool(medical),
                   opted_in=bool(opted), crew_start_delay_h=float(delay))
    with st.spinner("Running the scenario…"):
        j = get_journey(scn, data, sets)
    st.session_state["last_journey"] = j
    risk, est, al = j.risk, j.restoration, j.alerts

    # ------------------------------------------------------------------ header
    st.title("Will I lose power, and for how long?")
    st.caption(f"Premise {j.customer['premise_nu']} · {j.customer['city'] or 'city n/a'} · circuit {j.customer['circuit_id']} · "
               f"{j.customer['service_kind']} service · {TAG['Observed']} data from the AEP HackOHI/O 2026 package")

    tabs = st.tabs(["Your outlook", "Messages", "Operations view", "Why & limits", "Data & results"])

    # ------------------------------------------------------------------ tab 1: outlook
    with tabs[0]:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"<div style='border-left:8px solid {BAND_COLORS.get(risk.band, '#777')};padding:0.4rem 0.8rem'>"
                        f"<div style='font-size:0.8rem;opacity:0.7'>{TAG['Simulated']} · outage risk if {sev_label.lower()} hits</div>"
                        f"<div style='font-size:2rem;font-weight:700'>{risk.band}</div>"
                        f"<div>{risk.percent_text} chance · confidence <b>{risk.confidence}</b></div></div>", unsafe_allow_html=True)
        with c2:
            st.markdown(f"<div style='border-left:8px solid #1f77b4;padding:0.4rem 0.8rem'>"
                        f"<div style='font-size:0.8rem;opacity:0.7'>{TAG['Simulated']} · restoration range after crews start</div>"
                        f"<div style='font-size:2rem;font-weight:700'>{est.window_text if est.affected else 'not affected'}</div>"
                        f"<div>most likely about <b>{fmt_hours(est.mid_h + est.crew_start_delay_h) if est.affected else '—'}</b> · {est.source.split(' (')[0]}</div></div>", unsafe_allow_html=True)
        with c3:
            st.markdown(f"<div style='border-left:8px solid #6a1b9a;padding:0.4rem 0.8rem'>"
                        f"<div style='font-size:0.8rem;opacity:0.7'>{TAG['Assumed']} rules · alerts</div>"
                        f"<div style='font-size:2rem;font-weight:700'>{'Eligible' if al.eligible_pre_storm or al.eligible_outage_alerts else 'Not eligible'}</div>"
                        f"<div>pre-storm: {'yes' if al.eligible_pre_storm else 'no'} · outage alerts: {'yes' if al.eligible_outage_alerts else 'no'}{' · priority' if al.priority else ''}</div></div>", unsafe_allow_html=True)

        st.subheader("Why this risk")
        for f in risk.factors:
            share = f" ({f['share']:.0%} of your hazard)" if f.get("share") else ""
            st.markdown(f"- **{f['name']}**{share}: {f['detail']}")
        for flag in risk.flags:
            st.warning(flag)
        if risk.method != "path":
            st.info("Fallback in use: without an upstream path on file the estimate uses the circuit average and cannot show your own dependency chain.")

        st.subheader("Your upstream dependency chain")
        if len(j.path_table):
            st.plotly_chart(viz.dependency_chain(j.path_table, est.service_restored_h + est.crew_start_delay_h if est.affected else None, est.affected), use_container_width=True)
            with st.expander("Zones on your path (substation → you)"):
                st.dataframe(j.path_table.drop(columns=["zone_id"]), hide_index=True, use_container_width=True)
        else:
            st.write("No upstream path is on file for this premise, so the chain cannot be drawn.")

        st.subheader("Restoration: repair completed is not service restored")
        st.write(est.narrative)
        if est.affected and est.what_moves_it:
            st.markdown("**What moves this estimate**")
            for w in est.what_moves_it:
                st.markdown(f"- {w}")
        if est.draws:
            d = est.draws
            if "service_p50" in d:
                st.caption(f"{TAG['Simulated']} {d['n_draws']} draws: affected in {d['p_affected']:.0%}; service time P10/P50/P90 = "
                           f"{d['service_p10']:.0f}/{d['service_p50']:.0f}/{d['service_p90']:.0f} h; mean {d['tasks_mean']:.0f} tasks per draw.")

        st.subheader("Prepare")
        for g in j.guidance:
            if g.tag == "SAFETY":
                st.error(f"**SAFETY** · {g.text}")
            elif g.tag == "MEDICAL":
                st.warning(f"**MEDICAL NEED** · {g.text}")
            else:
                st.markdown(f"- **{g.tag.title()}** · {g.text}")

        st.subheader("Alerts and escalation")
        for r in al.reasons:
            st.markdown(f"- {r}")
        if al.schedule:
            st.dataframe(pd.DataFrame(al.schedule), hide_index=True, use_container_width=True)
        esc = pd.DataFrame(al.escalations)
        st.dataframe(esc[esc["active"]].drop(columns=["active"]) if esc["active"].any() else pd.DataFrame({"trigger": ["none active"], "action": ["—"]}),
                     hide_index=True, use_container_width=True)
        with st.expander("Alert rules used (Assumed, not AEP policy)"):
            st.dataframe(pd.DataFrame(al.rules), hide_index=True, use_container_width=True)

    # ------------------------------------------------------------------ tab 2: messages
    with tabs[1]:
        st.caption("Four stages · each message separates what we know (Observed), what we estimate (Simulated) and what you can do. "
                   "The estimate-updated message compares against the previous scenario you ran.")
        for stage, m in j.messages.items():
            with st.container(border=True):
                st.markdown(f"**{m.title}** · {m.channel}")
                st.markdown(f"**What we know:** {m.know}")
                st.markdown(f"**What we estimate:** {m.estimate}")
                st.markdown(f"**What you can do:** {m.actions}")
                st.caption(m.footer)

    # ------------------------------------------------------------------ tab 3: operations
    with tabs[2]:
        st.caption(f"{TAG['Simulated']} crew schedules for circuit {j.customer['circuit_id']} under identical inputs "
                   f"({est.n_tasks_circuit} tasks, {est.crews} crews, {est.source.split(' (')[0]}). Objective: customer-outage hours, "
                   "each premise counted once; a customer is restored only when every damaged zone on its path is repaired.")
        if est.policies and est.instance is not None and len(est.policies) >= 2:
            tbl = metrics_table(est.policies)
            st.dataframe(tbl, hide_index=True, use_container_width=True)
            st.plotly_chart(viz.restoration_curves(est.policies, est.instance, est.service_restored_h if est.affected else None), use_container_width=True)
            pol = st.selectbox("Show crew timeline for", list(est.policies), format_func=lambda p: POLICY_LABELS[p], index=len(est.policies) - 1)
            path_zones = set(str(j.customer["path_zone_ids"]).split("|")) if j.customer["has_path"] else set()
            st.plotly_chart(viz.gantt(est.policies[pol], est.instance, path_zones), use_container_width=True)
            st.caption("Red bars are repairs on your own upstream path.")
            st.markdown("**Solver status**")
            for k, v in j.evidence["Solver status"]:
                st.markdown(f"- {k}: {v}")
        else:
            st.write("No repair tasks in this damage set for this circuit.")

    # ------------------------------------------------------------------ tab 4: why & limits
    with tabs[3]:
        for section, rows in j.evidence.items():
            st.subheader(section)
            for k, v in rows:
                st.markdown(f"- **{k}:** {v}")
        st.subheader("Privacy")
        st.markdown("- Premises are identified only by their anonymized number and city; address, road, meter and annotation fields are never loaded into the app tables.\n"
                    "- Critical-facility locations are not displayed.\n- The medical-need toggle is pre-filled from the account's own flag and can be changed; nothing is inferred.")

    # ------------------------------------------------------------------ tab 5: data & results
    with tabs[4]:
        for name, path in (("Data report", config.DATA / "data_report.md"), ("Risk model validation", config.RESULTS / "risk_validation.md"),
                           ("Scheduling benchmark", config.RESULTS / "benchmark_summary.md"), ("Assumptions register", config.ROOT / "ASSUMPTIONS.md")):
            with st.expander(name, expanded=(name == "Scheduling benchmark")):
                p = Path(path)
                st.markdown(p.read_text(encoding="utf8") if p.exists() else f"_{name} not generated yet._")
        st.subheader("Labor bundles (Observed CU averages combined per ASSUMPTIONS L2)")
        st.dataframe(data.labor.bundle_summary(), hide_index=True, use_container_width=True)


if __name__ == "__main__" or True:
    main()
