"""Alert eligibility, timing, channels and escalation triggers (Assumed rules C1; shown in the app as a table)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .restoration import RestorationEstimate
from .risk import RiskResult
from .scenario import Scenario

BAND_RANK = {"Low": 0, "Moderate": 1, "High": 2, "Very high": 3, "Unavailable": -1}

RULES = [
    {"id": "E1", "rule": "Outage alerts (outage confirmed, estimate updated, restoration complete) go to customers who opted in.", "basis": "Assumed"},
    {"id": "E2", "rule": "A proactive pre-storm alert is sent when the risk band is Moderate or higher, or a medical need is declared, or the expected restoration exceeds 24 h.", "basis": "Assumed"},
    {"id": "E3", "rule": "Declared medical need: voice call added to SMS, pre-storm alert as early as possible, wellness check if the estimate exceeds 8 h.", "basis": "Assumed"},
    {"id": "E4", "rule": "Channels: SMS + email by default; voice call when a medical need is declared or the estimate exceeds 24 h.", "basis": "Assumed"},
    {"id": "E5", "rule": "Timing: pre-storm at 24 h and 6 h before arrival (or immediately if closer); outage confirmed within 15 min; estimate updated when the midpoint moves more than 2 h or the band changes; restoration complete at restore.", "basis": "Assumed"},
    {"id": "E6", "rule": "Escalation: estimate over 24 h → warming/cooling center and food-safety guidance; over 72 h → daily updates and community resources; Low confidence → a person reviews before any number is sent; difficult access → explain the delay.", "basis": "Assumed"},
]


@dataclass
class AlertDecision:
    eligible_outage_alerts: bool
    eligible_pre_storm: bool
    priority: bool
    reasons: list[str]
    channels: list[str]
    schedule: list[dict]
    escalations: list[dict]
    rules: list[dict] = field(default_factory=lambda: RULES)


def decide(risk: RiskResult, est: RestorationEstimate, scn: Scenario, medical_need: bool) -> AlertDecision:
    reasons: list[str] = []
    mid = est.mid_h + est.crew_start_delay_h if est.affected else 0.0
    eligible_outage = scn.opted_in
    if not scn.opted_in:
        reasons.append("Not opted in to outage alerts: no automatic messages; opting in enables all four alert types.")
    pre = BAND_RANK.get(risk.band, 0) >= 1 or medical_need or mid > 24
    if BAND_RANK.get(risk.band, 0) >= 1:
        reasons.append(f"Risk band is {risk.band} (rule E2).")
    if medical_need:
        reasons.append("Declared medical need on file: priority handling (rule E3).")
    if mid > 24:
        reasons.append(f"Expected restoration ≈ {mid:.0f} h exceeds 24 h (rule E2).")
    if not pre:
        reasons.append("Risk is Low, no medical need and a short expected restoration: no proactive pre-storm alert (rule E2); outage alerts still apply if an outage occurs.")
    channels = ["SMS", "email"]
    if medical_need or mid > 24:
        channels.append("voice call")
    h = scn.hours_to_landfall
    schedule = []
    if pre and scn.opted_in:
        first = "now" if h <= 24 else "24 h before the storm"
        schedule.append({"stage": "pre_storm", "when": first if not medical_need else ("now" if h <= 48 else "48 h before the storm"), "channels": ", ".join(channels)})
        if h > 6:
            schedule.append({"stage": "pre_storm (reminder)", "when": "6 h before the storm", "channels": "SMS"})
    if scn.opted_in:
        schedule.append({"stage": "outage_confirmed", "when": "within 15 min of the outage being confirmed", "channels": ", ".join(channels)})
        schedule.append({"stage": "estimate_updated", "when": "when the midpoint moves > 2 h or the band changes", "channels": "SMS, email"})
        schedule.append({"stage": "restoration_complete", "when": "at restoration", "channels": "SMS, email"})
    escalations = [
        {"trigger": "Medical need and estimate > 8 h", "action": "Human outreach / wellness check; medical-equipment battery guidance", "active": bool(medical_need and mid > 8)},
        {"trigger": "Estimate > 24 h", "action": "Warming/cooling center and food-safety guidance; voice call", "active": mid > 24},
        {"trigger": "Estimate > 72 h", "action": "Daily updates; community resources", "active": mid > 72},
        {"trigger": "Low confidence or no upstream path", "action": "A person reviews before any time estimate is sent; customer gets a 'we are assessing' message", "active": risk.confidence in ("Low", "None")},
        {"trigger": "Difficult access on the circuit", "action": "Explain the delay in the estimate-updated message", "active": scn.access_difficulty},
    ]
    return AlertDecision(eligible_outage, pre and scn.opted_in, medical_need, reasons, channels, schedule, escalations)
