"""Plain-language customer messages for the four outage stages.

Every message separates **What we know** (Observed / confirmed), **What we estimate** (Simulated, a range) and
**What you can do** (actions). No address, name or internal field is ever included; the premise is referred to
only by its anonymized id and city.
"""
from __future__ import annotations

from dataclasses import dataclass

from .alerts import AlertDecision
from .restoration import RestorationEstimate, fmt_hours
from .risk import RiskResult
from .scenario import Scenario

STAGES = ("pre_storm", "outage_confirmed", "estimate_updated", "restoration_complete")
STAGE_TITLES = {"pre_storm": "Pre-storm", "outage_confirmed": "Outage confirmed", "estimate_updated": "Estimate updated",
                "restoration_complete": "Restoration complete"}


@dataclass
class Message:
    stage: str
    title: str
    channel: str
    know: str        # Observed / confirmed
    estimate: str    # Simulated / model
    actions: str
    footer: str

    @property
    def body(self) -> str:
        return f"What we know: {self.know}\nWhat we estimate: {self.estimate}\nWhat you can do: {self.actions}\n{self.footer}"


def _place(cust) -> str:
    city = str(cust.get("city", "") or "").strip()
    return f"premise {cust['premise_nu']}" + (f" in {city}" if city else "")


def _window(est: RestorationEstimate) -> str:
    if not est.affected:
        return "no interruption is expected from this damage set"
    lo = est.low_h + est.crew_start_delay_h
    hi = est.high_h + est.crew_start_delay_h
    return f"between {fmt_hours(lo)} and {fmt_hours(hi)} after crews start"


def build_all(cust, risk: RiskResult, est: RestorationEstimate, alerts: AlertDecision, scn: Scenario,
              previous: RestorationEstimate | None = None) -> dict[str, Message]:
    place = _place(cust)
    chan = ", ".join(alerts.channels)
    footer = "Estimates come from a model of your circuit and may change. Reply STOP to opt out. Emergencies: call 911."
    severity_txt = {0.5: "a minor storm", 1.0: "a storm like February 2025", 2.0: "a major storm", 3.0: "an extreme storm"}.get(scn.severity, f"a storm of {scn.severity:g}× the February 2025 intensity")
    conf = f"confidence {risk.confidence.lower()}" if risk.confidence not in ("None",) else "confidence not available"

    pre = Message(
        "pre_storm", STAGE_TITLES["pre_storm"], chan,
        know=f"A storm is expected in about {scn.hours_to_landfall:.0f} h. Your area ({place}) is served by circuit {cust['circuit_id']}, which had "
             f"{int(risk.components.get('circuit_damage_ref', 0)) if 'circuit_damage_ref' in risk.components else '—'} reported damage sites in the February 2025 storm.",
        estimate=f"Your chance of losing power in {severity_txt} is {risk.band.lower()} ({risk.percent_text}, {conf}). "
                 f"If you do, restoration would likely take {_window(est)}.",
        actions="Charge devices, set the fridge colder, keep a flashlight handy" + (", and check your medical-equipment batteries now" if alerts.priority else "") + ".",
        footer=footer,
    )
    confirmed = Message(
        "outage_confirmed", STAGE_TITLES["outage_confirmed"], chan,
        know=f"We have confirmed an outage affecting {place}. {est.n_tasks_circuit} damage sites have been reported on your circuit and "
             f"{est.crews} crew{'s are' if est.crews != 1 else ' is'} assigned.",
        estimate=(f"Service is expected back {_window(est)}; the most likely time is about {fmt_hours(est.mid_h + est.crew_start_delay_h)}. "
                  f"Your power depends on repairs in {est.blocking_zone_label} as well as the damage nearest you." if est.affected else
                  "Our model did not place damage on your line; a crew will verify. Expect an update within 2 h."),
        actions="Keep the fridge closed, unplug sensitive electronics, and stay clear of any downed line." + (" A wellness call is being arranged." if (alerts.priority and est.affected and est.mid_h + est.crew_start_delay_h > 8) else ""),
        footer=footer,
    )
    if previous is not None and previous.affected and est.affected:
        delta = (est.mid_h - previous.mid_h)
        direction = "earlier" if delta < 0 else "later"
        change = (f"Our estimate moved {fmt_hours(abs(delta))} {direction}: now {_window(est)} (most likely about "
                  f"{fmt_hours(est.mid_h + est.crew_start_delay_h)}), was {_window(previous)}.")
        why = []
        if est.crews != previous.crews:
            why.append(f"crews on your circuit changed from {previous.crews} to {est.crews}")
        if scn.access_difficulty and est.n_tasks_circuit == previous.n_tasks_circuit and est.crews == previous.crews:
            why.append("access conditions worsened")
        reason = (" Why: " + " and ".join(why) + ".") if why else ""
    else:
        change = f"Current estimate: {_window(est)}, most likely about {fmt_hours(est.mid_h + est.crew_start_delay_h)}." if est.affected else "No change: no damage is placed on your line."
        reason = ""
    updated = Message(
        "estimate_updated", STAGE_TITLES["estimate_updated"], "SMS, email",
        know=f"Crews are working on circuit {cust['circuit_id']}. {('Access to some sites is difficult, which slows repairs. ' if scn.access_difficulty else '')}"
             f"{est.n_tasks_on_path} repair{'s are' if est.n_tasks_on_path != 1 else ' is'} on the line between you and the substation.",
        estimate=change + reason,
        actions="If you depend on powered medical equipment and your backup is running low, call 911 or your provider now." if alerts.priority else
                "Discard perishable food that has been above 40 °F for more than 4 h; charge devices wherever you can.",
        footer=footer,
    )
    done = Message(
        "restoration_complete", STAGE_TITLES["restoration_complete"], "SMS, email",
        know=f"Power has been restored to {place}.",
        estimate="If you are still without power, there may be an issue with your own service line or meter: please report it so a crew can check.",
        actions="Reset clocks and timers, restock supplies, and check that the fridge held temperature (discard food that was above 40 °F for more than 4 h).",
        footer="Thank you for your patience. Reply STOP to opt out.",
    )
    return {"pre_storm": pre, "outage_confirmed": confirmed, "estimate_updated": updated, "restoration_complete": done}
