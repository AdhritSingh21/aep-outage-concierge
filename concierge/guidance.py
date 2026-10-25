"""Preparation and safety guidance by risk band, expected duration, scenario and declared needs (Assumed content, C4).

Safety items are labeled SAFETY and always shown first. Nothing here infers a medical, mobility or facility need: those
items appear only when the customer declares the need in the session (or their account carries the life-support flag
they can see and switch off). Every item carries the public source its figure comes from (SOURCE_LABELS; the full
citations are in ASSUMPTIONS.md C4 and the README).
"""
from __future__ import annotations

from dataclasses import dataclass

from .restoration import STATE_AFFECTED, STATE_NOT_AFFECTED, STATE_UNKNOWN, RestorationEstimate
from .risk import RiskResult
from .scenario import Scenario

# Short publisher labels shown next to items; the citation strings (URLs) are in ASSUMPTIONS.md C4 / README "Sources"
SOURCE_LABELS = {
    "utility": "ESFI and utility downed-line guidance (35 ft; AEP: treat every downed line as live)",
    "cdc": "CDC generator and carbon-monoxide guidance",
    "usda": "FoodSafety.gov / USDA FSIS / FDA power-outage food safety",
    "ready": "Ready.gov (FEMA) power-outage preparedness",
    "redcross": "American Red Cross power-outage checklist",
    "nws": "National Weather Service flood safety",
    "team": "team assumption (C4)",
}

SAFETY = [
    ("SAFETY", "Stay at least 35 feet (10 m) from any downed line and report it. Treat every line as live.", "utility"),
    ("SAFETY", "Run generators outdoors only, at least 20 feet from windows, doors and vents; never in a garage or basement.", "cdc"),
    ("SAFETY", "Call 911 for a medical emergency; the utility's outage line is for power issues only.", "team"),
]


def duration_bucket(hours: float | None) -> str:
    if hours is None or hours != hours:
        return "unknown"
    if hours < 4:
        return "short"
    if hours < 24:
        return "day"
    if hours < 72:
        return "multi_day"
    return "long"


@dataclass
class GuidanceItem:
    tag: str      # SAFETY | PREPARE | DURING | MEDICAL | MOBILITY | FACILITY | NOTE | UPSTREAM
    text: str
    source: str = "team"

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)


def build(risk: RiskResult, est: RestorationEstimate, medical_need: bool, hours_to_landfall: float,
          scn: Scenario | None = None, service_kind: str | None = None) -> list[GuidanceItem]:
    items = [GuidanceItem(t, s, src) for t, s, src in SAFETY]
    needs = tuple(scn.needs) if scn is not None else ()
    access = bool(scn.access_difficulty) if scn is not None else False
    if access:
        items.append(GuidanceItem("SAFETY", "Never drive or walk through flooded or ice-covered roads: a downed line in water can energize it, and crews may not reach you until roads are clear.", "nws"))
    band = risk.band
    mid = est.mid_total if est.state == STATE_AFFECTED else None
    bucket = duration_bucket(mid)

    # preparation scaled to the risk band
    if band in ("Moderate", "High", "Very high", "Unavailable"):
        items.append(GuidanceItem("PREPARE", f"Charge phones and power banks before the storm (about {hours_to_landfall:.0f} h away) and keep a flashlight within reach.", "ready"))
        items.append(GuidanceItem("PREPARE", "Set the fridge and freezer to their coldest settings now; a closed fridge keeps food safe about 4 h, a full freezer about 48 h.", "usda"))
    if band in ("High", "Very high"):
        items.append(GuidanceItem("PREPARE", "Store drinking water (1 gallon per person per day) and fill the tub if you have a well pump.", "ready"))
        items.append(GuidanceItem("PREPARE", "Park vehicles clear of large trees and fuel up: gas pumps need power too.", "redcross"))
        items.append(GuidanceItem("PREPARE", "Have a warm-room plan (blankets, layers) or a cool-room plan depending on the season; never heat with a gas stove, oven or grill indoors.", "cdc"))
    if band == "Very high":
        items.append(GuidanceItem("PREPARE", "Assume you will lose power: decide now where you would go if the outage runs past a day, and tell someone your plan.", "team"))
    if band == "Low":
        items.append(GuidanceItem("PREPARE", "Risk is low for this scenario: keep a flashlight and a charged phone handy and watch for updates.", "team"))
    if bucket in ("multi_day", "long"):
        items.append(GuidanceItem("PREPARE", "Move medications that need refrigeration to a cooler with ice, and keep a written list of them in case you relocate.", "redcross"))
        items.append(GuidanceItem("PREPARE", "Arrange somewhere to stay with power (family, a friend, a shelter) in case the outage runs its full course.", "ready"))

    # expected-duration guidance, honest about the state of the estimate
    if est.state == STATE_UNKNOWN:
        items.append(GuidanceItem("DURING", "No upstream path is on file for your premise, so no duration can be estimated yet; prepare as for a day-long outage. A person will review before any time is sent.", "team"))
    elif est.state == STATE_NOT_AFFECTED:
        extra = ""
        if est.alt and est.alt.get("affected_draws", 0):
            extra = f" In simulated storms of this severity you lost power in {100 * est.alt['p_affected']:.0f}% of cases."
        items.append(GuidanceItem("DURING", "The Feb-2025 damage pattern did not touch your line, but a different storm could; prepare as for a day-long outage and watch for updates." + extra, "team"))
    elif bucket == "short":
        items.append(GuidanceItem("DURING", "If the power goes out, keep the fridge closed and unplug sensitive electronics until it is back.", "usda"))
    elif bucket == "day":
        items.append(GuidanceItem("DURING", "Plan for most of a day without power: keep the fridge closed, use coolers with ice for essentials, and check on neighbours who may need help.", "ready"))
    elif bucket == "multi_day":
        items.append(GuidanceItem("DURING", "Plan for 1–3 days: arrange somewhere to charge devices and stay warm or cool (a friend's home, a library, a community center), and discard perishable food once it has been above 40 °F (4 °C) for 2 hours or more.", "usda"))
    elif bucket == "long":
        items.append(GuidanceItem("DURING", "Plan for several days: consider staying with family or at a shelter, keep refrigerated medications in a cooler with ice, and expect daily updates rather than an exact time.", "redcross"))
    else:
        items.append(GuidanceItem("DURING", "No duration estimate is available yet; prepare as for a day-long outage and watch for an update.", "team"))

    if service_kind == "Underground":
        items.append(GuidanceItem("NOTE", "Your own service line is buried, so your risk comes from the overhead line and equipment upstream, not from your drop.", "team"))

    if medical_need:
        items.append(GuidanceItem("MEDICAL", "You have a declared medical need: check battery levels on medical equipment now, keep your provider's number handy, and plan where you would go if the outage lasts longer than your backup power.", "redcross"))
        items.append(GuidanceItem("MEDICAL", "If equipment fails or supplies run low during an outage, call 911; do not wait for the power to return.", "team"))
    if "mobility" in needs:
        items.append(GuidanceItem("MOBILITY", "You declared a mobility or accessibility need: keep mobility-device batteries charged, keep a manual backup within reach, and arrange a ride now with someone who can reach you if roads are closed.", "redcross"))
        items.append(GuidanceItem("MOBILITY", "If the estimate passes 8 h you will be offered transport to an accessible warming or cooling center; say yes early rather than late.", "team"))
    if "critical_facility" in needs:
        items.append(GuidanceItem("FACILITY", "You declared a critical facility: test the standby generator now, confirm fuel for at least the high end of the range, and keep your utility operations contact on hand.", "cdc"))
        items.append(GuidanceItem("FACILITY", "Priority restoration is reported in every update but never promised: plan on the high end of the range.", "team"))
    if est.state == STATE_AFFECTED and est.n_tasks_on_path and est.local_repair_done_h is not None and est.local_zone != est.blocking_zone:
        items.append(GuidanceItem("UPSTREAM", "Seeing a crew near you does not mean your power is about to return: repairs further up the line may still be in progress.", "team"))
    return items
