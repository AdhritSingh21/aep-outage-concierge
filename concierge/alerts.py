"""Alert eligibility, enrollment, timing, channels and escalation triggers (Assumed rules C1; shown in the app as a table).

Eligibility (does the scenario qualify?) is kept apart from enrollment (did the customer opt in?): a customer who
opted out still *qualifies*, nothing is sent, and the app says so instead of "not eligible".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .restoration import STATE_AFFECTED, RestorationEstimate
from .risk import RiskResult
from .scenario import Scenario

BAND_RANK = {"Low": 0, "Moderate": 1, "High": 2, "Very high": 3, "Unavailable": -1}

STAGE_TITLES = {"pre_storm": "Pre-storm", "pre_storm_reminder": "Pre-storm reminder", "outage_confirmed": "Outage confirmed",
                "estimate_updated": "Estimate updated", "restoration_complete": "Restoration complete"}

RULES = [
    {"id": "E1", "rule": "Outage alerts (outage confirmed, estimate updated, restoration complete) are sent to customers who opted in (enrolled); every customer qualifies.", "basis": "Assumed"},
    {"id": "E2", "rule": "A proactive pre-storm alert is sent when the risk band is Moderate or higher, or a need is declared, or the expected restoration exceeds 24 h.", "basis": "Assumed"},
    {"id": "E3", "rule": "Declared medical need: voice call added to SMS on every stage, pre-storm alert as early as possible, wellness check if the estimate exceeds 8 h.", "basis": "Assumed"},
    {"id": "E4", "rule": "Channels: SMS + email by default; voice call when a need is declared or the estimate exceeds 24 h; the same channels on every stage.", "basis": "Assumed"},
    {"id": "E5", "rule": "Timing: pre-storm at 24 h and 6 h before arrival (or immediately if closer); outage confirmed within 15 min; estimate updated only when the midpoint moves more than 2 h or the band changes; restoration complete at restore.", "basis": "Assumed"},
    {"id": "E6", "rule": "Escalation: estimate over 24 h → warming/cooling center and food-safety guidance; over 72 h → daily updates and community resources; Low confidence or no upstream path → a person reviews before any number is sent and the customer gets a 'we are assessing' message; difficult access → explain the delay.", "basis": "Assumed"},
    {"id": "E7", "rule": "Declared mobility or accessibility need: earliest pre-storm notice, voice call, and transport / accessible-shelter assistance offered when the estimate exceeds 8 h.", "basis": "Assumed"},
    {"id": "E8", "rule": "Declared critical facility: facility-operations wording, generator and fuel check reminder, priority restoration is reported but never promised.", "basis": "Assumed"},
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
    enrolled: bool = True
    needs: tuple[str, ...] = ()
    review_before_sending: bool = False   # E6: Low confidence / no path → a person reviews before any number goes out

    @property
    def status_text(self) -> str:
        if not self.enrolled:
            return "Eligible · not enrolled"
        return "Eligible · enrolled" + (" · priority" if self.priority else "")


def decide(risk: RiskResult, est: RestorationEstimate, scn: Scenario, medical_need: bool) -> AlertDecision:
    reasons: list[str] = []
    needs = tuple(scn.needs)
    mobility = "mobility" in needs
    facility = "critical_facility" in needs
    mid = est.mid_total if est.state == STATE_AFFECTED else 0.0
    priority = bool(medical_need or mobility or facility)
    review = not risk.reliable

    enrolled = bool(scn.opted_in)
    if not enrolled:
        reasons.append("Not enrolled: no automatic messages are sent; opting in enables all four alert types (rule E1).")
    pre = BAND_RANK.get(risk.band, 0) >= 1 or priority or mid > 24
    if BAND_RANK.get(risk.band, 0) >= 1:
        reasons.append(f"Risk band is {risk.band} (rule E2).")
    if medical_need:
        reasons.append("Declared medical need: priority handling, voice call on every stage (rule E3).")
    if mobility:
        reasons.append("Declared mobility / accessibility need: earliest notice and assistance offer (rule E7).")
    if facility:
        reasons.append("Declared critical facility: facility-operations wording and generator reminder (rule E8).")
    if mid > 24:
        reasons.append(f"Expected restoration ≈ {mid:.0f} h exceeds 24 h (rule E2).")
    if review:
        reasons.append("Confidence is Low (no upstream path on file): a person reviews before any time estimate is sent (rule E6).")
    if not pre:
        reasons.append("Risk is Low, no need declared and a short expected restoration: no proactive pre-storm alert (rule E2); outage alerts still apply if an outage occurs.")

    channels = ["SMS", "email"]
    if priority or mid > 24:
        channels.append("voice call")
    chan = ", ".join(channels)
    h = scn.hours_to_landfall
    schedule: list[dict] = []
    if enrolled:
        if pre:
            first = "now" if h <= 24 else "24 h before the storm"
            if priority:
                first = "now" if h <= 48 else "48 h before the storm"
            schedule.append({"stage": STAGE_TITLES["pre_storm"], "when": first, "channels": chan})
            if h > 6:
                schedule.append({"stage": STAGE_TITLES["pre_storm_reminder"], "when": "6 h before the storm", "channels": "SMS"})
        schedule.append({"stage": STAGE_TITLES["outage_confirmed"], "when": "within 15 min of the outage being confirmed", "channels": chan})
        schedule.append({"stage": STAGE_TITLES["estimate_updated"], "when": "when the midpoint moves > 2 h or the band changes", "channels": chan})
        schedule.append({"stage": STAGE_TITLES["restoration_complete"], "when": "at restoration", "channels": chan})
    escalations = [
        {"trigger": "Medical need and estimate > 8 h", "action": "Human outreach / wellness check; medical-equipment battery guidance", "active": bool(medical_need and mid > 8)},
        {"trigger": "Mobility / accessibility need and estimate > 8 h", "action": "Offer transport to an accessible warming/cooling center; voice call", "active": bool(mobility and mid > 8)},
        {"trigger": "Critical facility declared", "action": "Facility-operations contact; generator fuel check reminder at 8 h; status on every update", "active": bool(facility)},
        {"trigger": "Estimate > 24 h", "action": "Warming/cooling center and food-safety guidance; voice call", "active": mid > 24},
        {"trigger": "Estimate > 72 h", "action": "Daily updates; community resources", "active": mid > 72},
        {"trigger": "Low confidence or no upstream path", "action": "A person reviews before any time estimate is sent; customer gets a 'we are assessing' message", "active": review},
        {"trigger": "Difficult access on the circuit", "action": "Explain the delay in the estimate-updated message", "active": bool(scn.access_difficulty)},
    ]
    return AlertDecision(True, pre, priority, reasons, channels, schedule, escalations, enrolled=enrolled, needs=needs,
                         review_before_sending=review)
