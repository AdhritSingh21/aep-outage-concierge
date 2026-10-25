"""Plain-language customer messages for the four outage stages.

Every message separates **What we know** (Observed), **What we estimate** (Simulated, a range) and **What you can do**
(actions). Scenario assumptions (crew count, hours to the storm) are stated as assumptions, never as facts. No address,
name or internal field is ever included; the premise is referred to only by its anonymized id and city. The
estimate-updated message compares against the previous scenario and respects rule E5 (no update for moves of 2 h or
less). When the risk is unreliable (Low confidence / no upstream path) no number is sent: the customer gets the
"we are assessing" wording that rule E6 promises.
"""
from __future__ import annotations

from dataclasses import dataclass

from .alerts import STAGE_TITLES, AlertDecision
from .restoration import STATE_AFFECTED, STATE_NOT_AFFECTED, STATE_UNKNOWN, RestorationEstimate, fmt_between, fmt_hours
from .risk import RiskResult
from .scenario import Scenario

STAGES = ("pre_storm", "outage_confirmed", "estimate_updated", "restoration_complete")
UPDATE_THRESHOLD_H = 2.0   # rule E5
NO_CHANGE_H = 0.5


@dataclass
class Message:
    stage: str
    title: str
    channel: str
    know: str        # Observed / confirmed
    estimate: str    # Simulated / model
    actions: str
    footer: str
    would_send: bool = True
    note: str = ""   # why it would not be sent, or what would trigger it

    @property
    def body(self) -> str:
        return f"What we know: {self.know}\nWhat we estimate: {self.estimate}\nWhat you can do: {self.actions}\n{self.footer}"


def _place(cust) -> str:
    city = str(cust.get("city", "") or "").strip()
    return f"premise {cust['premise_nu']}" + (f" in {city}" if city else "")


def _window(est: RestorationEstimate) -> str:
    if est.state == STATE_AFFECTED:
        return f"{fmt_between(est.low_total, est.high_total)} {est.clock_label}"
    if est.state == STATE_UNKNOWN:
        return "not yet placed on the network"
    return "no damage placed on your line in this damage set"


def _severity_text(scn: Scenario) -> str:
    return {0.5: "a minor storm", 1.0: "a storm like February 2025", 2.0: "a major storm", 3.0: "an extreme storm"}.get(
        scn.severity, f"a storm of {scn.severity:g}× the February 2025 intensity")


def _risk_sentence(risk: RiskResult, scn: Scenario) -> str:
    if not risk.reliable:
        return (f"We are still assessing your area: no upstream path is on file for your premise, so a specialist will confirm "
                f"before we send a time estimate. The circuit average suggests a {risk.band.lower()} chance of losing power in "
                f"{_severity_text(scn)}.")
    conf = f"confidence {risk.confidence.lower()}"
    return f"Your chance of losing power in {_severity_text(scn)} is {risk.band.lower()} ({risk.percent_text}, {conf})."


def _restoration_sentence(est: RestorationEstimate) -> str:
    if est.state == STATE_AFFECTED:
        return f"If you do, restoration would likely take {_window(est)}."
    if est.state == STATE_UNKNOWN:
        return "If you do, we cannot yet place your outage on the network; a person will confirm a time."
    txt = "If you do, we have no repair time for you yet: the February 2025 damage pattern did not touch your line."
    if est.alt and est.alt.get("affected_draws", 0):
        txt += (f" In {est.alt['n_draws']} simulated storms of this severity you lost power in {100 * est.alt['p_affected']:.0f}% of them; "
                f"when you did, service came back {fmt_between(est.alt['low_h'] + est.crew_start_delay_h, est.alt['high_h'] + est.crew_start_delay_h)} {est.clock_label}.")
    else:
        txt += " We will send a range as soon as damage is reported between you and the substation."
    return txt


def _why(scn: Scenario, prev_scn: Scenario | None, est: RestorationEstimate, prev: RestorationEstimate) -> list[str]:
    why: list[str] = []
    if prev_scn is None:
        if est.crews != prev.crews:
            why.append(f"crews on your circuit changed from {prev.crews} to {est.crews}")
        return why
    if scn.crews != prev_scn.crews:
        why.append(f"crews on your circuit changed from {prev_scn.crews} to {scn.crews}")
    if scn.access_difficulty != prev_scn.access_difficulty:
        why.append("access conditions worsened (ice / flooding)" if scn.access_difficulty else "access conditions improved")
    if scn.crew_start_delay_h != prev_scn.crew_start_delay_h:
        why.append(f"crews now start {scn.crew_start_delay_h:g} h after the storm instead of {prev_scn.crew_start_delay_h:g} h")
    if scn.labor_multiplier != prev_scn.labor_multiplier:
        why.append(f"repairs are assumed to take {scn.labor_multiplier:g}× the workbook hours instead of {prev_scn.labor_multiplier:g}×")
    if scn.damage_source != prev_scn.damage_source:
        why.append("the damage set changed to " + ("simulated draws" if scn.damage_source == "simulated" else "the Feb-2025 records"))
    elif scn.damage_source == "simulated" and scn.severity != prev_scn.severity:
        why.append(f"the storm severity changed from {prev_scn.severity:g}× to {scn.severity:g}×")
    if scn.prioritize_critical != prev_scn.prioritize_critical:
        why.append("critical loads are now prioritized" if scn.prioritize_critical else "critical-load prioritization was switched off")
    if not why and est.n_tasks_circuit != prev.n_tasks_circuit:
        why.append(f"the number of repair tasks on your circuit changed from {prev.n_tasks_circuit} to {est.n_tasks_circuit}")
    return why


def build_all(cust, risk: RiskResult, est: RestorationEstimate, alerts: AlertDecision, scn: Scenario,
              previous: RestorationEstimate | None = None, previous_scenario: Scenario | None = None) -> dict[str, Message]:
    place = _place(cust)
    chan = ", ".join(alerts.channels)
    footer = "Estimates come from a model of your circuit and may change. Reply STOP to opt out. Emergencies: call 911."
    priority = alerts.priority
    medical = "medical" in alerts.needs or (priority and "mobility" not in alerts.needs and "critical_facility" not in alerts.needs)
    not_enrolled_note = "Not enrolled: this message would not be sent. Opt in to outage alerts to receive it." if not alerts.enrolled else ""
    n_ref = int(risk.components.get("circuit_damage_ref", 0)) if "circuit_damage_ref" in risk.components else None
    historical = scn.damage_source == "historical"

    pre_actions = "Charge devices, set the fridge colder, keep a flashlight handy"
    if medical:
        pre_actions += ", and check your medical-equipment batteries now"
    if "mobility" in alerts.needs:
        pre_actions += ", and arrange a ride now with someone who can reach you"
    if "critical_facility" in alerts.needs:
        pre_actions += ", and test your standby generator and fuel supply"
    pre = Message(
        "pre_storm", STAGE_TITLES["pre_storm"], chan,
        know=(f"Your area ({place}) is served by circuit {cust['circuit_id']}"
              + (f", which had {n_ref} reported damage sites in the February 2025 storm." if n_ref is not None else ".")),
        estimate=f"A storm is expected in about {scn.hours_to_landfall:.0f} h (scenario assumption). {_risk_sentence(risk, scn)} {_restoration_sentence(est)}",
        actions=pre_actions + ".",
        footer=footer,
        would_send=alerts.enrolled and alerts.eligible_pre_storm,
        note=not_enrolled_note or ("" if alerts.eligible_pre_storm else "Not triggered: the risk band is Low and no need is declared (rule E2)."),
    )

    if est.state == STATE_AFFECTED:
        conf_est = (f"Assuming {est.crews} crew{'s' if est.crews != 1 else ''} on your circuit"
                    + (f" and about {est.n_tasks_circuit} damage sites (Simulated draw)" if not historical else "")
                    + f", service is expected back {_window(est)}; the most likely time is about {fmt_hours(est.mid_total)}. "
                    f"Your power depends on repairs in {est.blocking_zone_label} as well as the damage nearest you.")
    elif est.state == STATE_UNKNOWN:
        conf_est = "We cannot yet place your outage on the network (no upstream path on file); a person is reviewing and will confirm a time."
    else:
        conf_est = "Our model did not place damage on your line in this damage set; a crew will verify. Expect an update within 2 h."
    confirmed = Message(
        "outage_confirmed", STAGE_TITLES["outage_confirmed"], chan,
        know=f"We have confirmed an outage affecting {place}."
             + (f" {est.n_tasks_circuit} damage sites were reported on your circuit in the February 2025 storm." if historical and est.state != STATE_UNKNOWN else ""),
        estimate=conf_est,
        actions="Keep the fridge closed, unplug sensitive electronics, and stay clear of any downed line."
                + (" A wellness call is being arranged." if (medical and est.state == STATE_AFFECTED and est.mid_total > 8) else "")
                + (" Transport to an accessible warming/cooling center is available on request." if ("mobility" in alerts.needs and est.state == STATE_AFFECTED and est.mid_total > 8) else ""),
        footer=footer, would_send=alerts.enrolled, note=not_enrolled_note,
    )

    # estimate updated: compare with the previous scenario (rule E5)
    update_note = not_enrolled_note
    if previous is not None and previous.state == STATE_AFFECTED and est.state == STATE_AFFECTED:
        delta = est.mid_total - previous.mid_total
        now = f"now {_window(est)} (most likely about {fmt_hours(est.mid_total)})"
        was = f"was {_window(previous)} (most likely about {fmt_hours(previous.mid_total)})"
        why = _why(scn, previous_scenario, est, previous)
        reason = (" Why: " + " and ".join(why) + ".") if why else ""
        if abs(delta) < NO_CHANGE_H:
            change = f"No change to your estimate: still {_window(est)}, most likely about {fmt_hours(est.mid_total)}."
            update_note = update_note or "Nothing moved, so no update would be sent (rule E5)."
            reason = ""
        elif abs(delta) <= UPDATE_THRESHOLD_H:
            change = f"Small change: {now}, {was}."
            update_note = update_note or f"Moved {abs(delta):.1f} h, under the 2 h update threshold: no message would be sent (rule E5)."
        else:
            change = f"Our estimate moved {fmt_hours(abs(delta), 'h')} {'earlier' if delta < 0 else 'later'}: {now}, {was}."
    elif est.state == STATE_AFFECTED:
        change, reason = f"Current estimate: {_window(est)}, most likely about {fmt_hours(est.mid_total)}.", ""
        update_note = update_note or "First estimate for this premise in this session; later changes are compared against it."
    elif est.state == STATE_UNKNOWN:
        change, reason = "Your outage is still being placed on the network; a person will confirm a time.", ""
    else:
        change, reason = "No change: no damage is placed on your line in this damage set.", ""
    if est.state == STATE_AFFECTED:
        know_upd = f"{est.n_tasks_on_path} repair{'s are' if est.n_tasks_on_path != 1 else ' is'} on the line between you and the substation."
    elif est.state == STATE_UNKNOWN:
        know_upd = "Your line is not yet placed on the network."
    else:
        know_upd = "No repairs are placed on your line in this damage set."
    updated = Message(
        "estimate_updated", STAGE_TITLES["estimate_updated"], chan,
        know=f"Crews are working on circuit {cust['circuit_id']}. "
             + ("Access to some sites is difficult, which slows repairs. " if scn.access_difficulty else "") + know_upd,
        estimate=change + reason,
        actions=("If you depend on powered medical equipment and your backup is running low, call 911 or your provider now."
                 if medical else "Discard perishable food that has been above 40 °F for 2 hours or more; charge devices wherever you can."),
        footer=footer, would_send=alerts.enrolled and not update_note.startswith(("Nothing", "Moved", "First")), note=update_note,
    )
    done = Message(
        "restoration_complete", STAGE_TITLES["restoration_complete"], chan,
        know=f"Power has been restored to {place}.",
        estimate="If you are still without power, there may be an issue with your own service line or meter: please report it so a crew can check.",
        actions="Reset clocks and timers, restock supplies, and check that the fridge held temperature (discard food that was above 40 °F for 2 hours or more).",
        footer="Thank you for your patience. Reply STOP to opt out.", would_send=alerts.enrolled, note=not_enrolled_note,
    )
    return {"pre_storm": pre, "outage_confirmed": confirmed, "estimate_updated": updated, "restoration_complete": done}
