"""Preparation and safety guidance by risk band, expected duration and declared needs (Assumed content, plain language).

Safety items are labeled SAFETY and always shown first. Nothing here infers a medical or accessibility need: the
medical items appear only when the customer declares one (or their account already carries the flag they can see).
"""
from __future__ import annotations

from dataclasses import dataclass

from .restoration import RestorationEstimate
from .risk import RiskResult

SAFETY = [
    ("SAFETY", "Stay at least 35 feet (10 m) from any downed line and report it. Treat every line as live."),
    ("SAFETY", "Run generators outdoors only, 20 feet from windows and doors; never in a garage or basement."),
    ("SAFETY", "Call 911 for a medical emergency; the utility's outage line is for power issues only."),
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
    tag: str      # SAFETY | PREPARE | DURING | MEDICAL | ACCESS
    text: str


def build(risk: RiskResult, est: RestorationEstimate, medical_need: bool, hours_to_landfall: float) -> list[GuidanceItem]:
    items = [GuidanceItem(t, s) for t, s in SAFETY]
    band = risk.band
    mid = (est.mid_h + est.crew_start_delay_h) if est.affected else None
    bucket = duration_bucket(mid)

    # preparation scaled to the risk band
    if band in ("Moderate", "High", "Very high", "Unavailable"):
        items.append(GuidanceItem("PREPARE", f"Charge phones and power banks before the storm (about {hours_to_landfall:.0f} h away) and keep a flashlight within reach."))
        items.append(GuidanceItem("PREPARE", "Set the fridge and freezer to their coldest settings now; a closed fridge keeps food safe about 4 h, a full freezer about 48 h."))
    if band in ("High", "Very high"):
        items.append(GuidanceItem("PREPARE", "Fill containers with drinking water (1 gallon per person per day) and fill the tub if you have a well pump."))
        items.append(GuidanceItem("PREPARE", "Park vehicles clear of large trees and fuel up: gas pumps need power too."))
        items.append(GuidanceItem("PREPARE", "Have a warm-room plan (blankets, layers) or a cool-room plan depending on the season; never heat with a gas stove or grill indoors."))
    if band == "Low":
        items.append(GuidanceItem("PREPARE", "Risk is low for this scenario: keep a flashlight and a charged phone handy and watch for updates."))

    # expected-duration guidance
    if bucket == "short":
        items.append(GuidanceItem("DURING", "If the power goes out, keep the fridge closed and unplug sensitive electronics until it is back."))
    elif bucket == "day":
        items.append(GuidanceItem("DURING", "Plan for most of a day without power: keep the fridge closed, use coolers with ice for essentials, and check on neighbours who may need help."))
    elif bucket == "multi_day":
        items.append(GuidanceItem("DURING", "Plan for 1–3 days: arrange somewhere to charge devices and stay warm or cool (a friend's home, a library, a community center), and discard perishable food after 4 h above 40 °F (4 °C)."))
    elif bucket == "long":
        items.append(GuidanceItem("DURING", "Plan for several days: consider staying with family or at a shelter, move medications that need refrigeration to a cooler with ice, and expect daily updates rather than an exact time."))
    else:
        items.append(GuidanceItem("DURING", "No duration estimate is available yet; prepare as for a day-long outage and watch for an update."))

    if medical_need:
        items.append(GuidanceItem("MEDICAL", "You have a declared medical need: check battery levels on medical equipment now, keep your provider's number handy, and plan where you would go if the outage lasts longer than your backup power."))
        items.append(GuidanceItem("MEDICAL", "If equipment fails or supplies run low during an outage, call 911; do not wait for the power to return."))
    if est.affected and est.n_tasks_on_path and est.local_repair_done_h is not None and est.local_zone != est.blocking_zone:
        items.append(GuidanceItem("ACCESS", "Seeing a crew near you does not mean your power is about to return: repairs further up the line may still be in progress."))
    return items
