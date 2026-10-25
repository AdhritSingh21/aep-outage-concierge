"""Unit tests for alerts, guidance and messages on synthetic risk/restoration objects."""
from __future__ import annotations

import pandas as pd

from concierge import alerts, guidance, messages
from concierge.restoration import RestorationEstimate, STATE_UNKNOWN
from concierge.risk import RiskResult
from concierge.scenario import Scenario


def risk(band="High", p=0.55, conf="Medium", method="path"):
    return RiskResult(p, band, conf, 1.0, 0.8, {"circuit_damage_ref": 12}, [{"name": "x", "share": 0.5, "detail": "y"}], [], method)


def est(affected=True, mid=10.0, local=4.0, crews=3, delay=0.0, state=""):
    return RestorationEstimate("Observed damage set (Feb-2025 records on this circuit)", affected, None, 0.75 * mid, mid, 1.5 * mid, delay,
                               mid, local if affected else None, "F1" if affected else None, "R1" if affected else None,
                               "the recloser zone 1 device below the substation", 20, 3, 2, 500, crews, narrative="n", state=state)


def cust():
    return pd.Series({"premise_nu": "000000001", "city": "Roanoke", "circuit_id": "0000005", "service_kind": "Overhead",
                      "life_support": False, "has_path": True, "path_zone_ids": "F1|R1|B1", "path_depth": 2})


def test_alert_rules():
    scn = Scenario("000000001", hours_to_landfall=30)
    d = alerts.decide(risk("Low", 0.05), est(False), scn, medical_need=False)
    assert d.eligible_outage_alerts and d.enrolled and not d.eligible_pre_storm and not d.priority
    d = alerts.decide(risk("Moderate", 0.2), est(True, 6), scn, medical_need=False)
    assert d.eligible_pre_storm and d.channels == ["SMS", "email"]
    assert any(s["stage"] == "Pre-storm" and s["when"] == "24 h before the storm" for s in d.schedule)
    d = alerts.decide(risk("Low", 0.05), est(True, 30), scn, medical_need=False)
    assert d.eligible_pre_storm and "voice call" in d.channels
    assert any(e["active"] for e in d.escalations if e["trigger"].startswith("Estimate > 24"))
    d = alerts.decide(risk("Low", 0.05), est(True, 9), scn, medical_need=True)
    assert d.priority and "voice call" in d.channels
    assert any(e["active"] for e in d.escalations if e["trigger"].startswith("Medical"))
    # priority customers get the voice channel on every scheduled stage (E3/E4), not only the first
    assert all("voice call" in s["channels"] for s in d.schedule if s["stage"] != "Pre-storm reminder")
    # opted out = still eligible, not enrolled, nothing scheduled
    d = alerts.decide(risk(), est(), scn.with_(opted_in=False), medical_need=False)
    assert d.eligible_outage_alerts and not d.enrolled and not d.schedule and d.status_text == "Eligible · not enrolled"
    d = alerts.decide(risk("Moderate", 0.2, "Low", method="circuit_average"), est(True, 6), scn, medical_need=False)
    assert d.review_before_sending and any(e["active"] for e in d.escalations if e["trigger"].startswith("Low confidence"))
    # declared needs
    d = alerts.decide(risk(), est(True, 12), scn.with_(needs=("mobility",)), medical_need=False)
    assert d.priority and any(e["active"] for e in d.escalations if e["trigger"].startswith("Mobility"))
    d = alerts.decide(risk(), est(True, 2), scn.with_(needs=("critical_facility",)), medical_need=False)
    assert any(e["active"] for e in d.escalations if e["trigger"].startswith("Critical facility"))


def test_alert_thresholds_use_the_delay_once():
    scn = Scenario("000000001", crew_start_delay_h=6.0)
    # 20 h of work + 6 h delay = 26 h total → the 24 h rule fires; 20 h of work alone would not
    d = alerts.decide(risk("Low", 0.05), est(True, 20, delay=6.0), scn, medical_need=False)
    assert any(e["active"] for e in d.escalations if e["trigger"].startswith("Estimate > 24"))
    d = alerts.decide(risk("Low", 0.05), est(True, 20, delay=0.0), scn.with_(crew_start_delay_h=0.0), medical_need=False)
    assert not any(e["active"] for e in d.escalations if e["trigger"].startswith("Estimate > 24"))


def test_guidance_shape_and_needs():
    g = guidance.build(risk("Very high", 0.9), est(True, 40), medical_need=False, hours_to_landfall=12)
    tags = [x.tag for x in g]
    assert tags[:3] == ["SAFETY"] * 3 and "PREPARE" in tags and "DURING" in tags and "MEDICAL" not in tags
    assert any("1–3 days" in x.text for x in g)
    assert all(x.source for x in g)
    gm = guidance.build(risk("Low", 0.05), est(False), medical_need=True, hours_to_landfall=12)
    assert any(x.tag == "MEDICAL" for x in gm)
    scn = Scenario("000000001", needs=("mobility", "critical_facility"), access_difficulty=True)
    gn = guidance.build(risk(), est(True, 12), medical_need=False, hours_to_landfall=12, scn=scn, service_kind="Underground")
    tags = [x.tag for x in gn]
    assert "MOBILITY" in tags and "FACILITY" in tags and "NOTE" in tags
    assert sum(1 for t in tags if t == "SAFETY") == 4   # the flooded/iced road item appears with difficult access
    # band and duration both change the preparation list
    low = guidance.build(risk("Low", 0.05), est(True, 2), medical_need=False, hours_to_landfall=12)
    vh = guidance.build(risk("Very high", 0.95), est(True, 80), medical_need=False, hours_to_landfall=12)
    assert len([x for x in vh if x.tag == "PREPARE"]) > len([x for x in low if x.tag == "PREPARE"])
    assert guidance.duration_bucket(None) == "unknown" and guidance.duration_bucket(100) == "long"


def test_messages_render_all_stages_without_placeholders():
    scn = Scenario("000000001", crews=3)
    d = alerts.decide(risk(), est(), scn, medical_need=False)
    msgs = messages.build_all(cust(), risk(), est(), d, scn)
    assert set(msgs) == set(messages.STAGES)
    for m in msgs.values():
        body = m.body
        assert "{" not in body and "}" not in body and "None" not in body and "nan" not in body
        assert "What we know" in body and "What we estimate" in body and "What you can do" in body
    assert "between 8 h and 15 h" in msgs["outage_confirmed"].estimate
    assert "recloser zone" in msgs["outage_confirmed"].estimate
    # the crew count is an assumption, not a fact: it lives in the estimate part
    assert "crew" not in msgs["outage_confirmed"].know and "Assuming 3 crews" in msgs["outage_confirmed"].estimate


def test_estimate_updated_diffs_previous_and_names_crew_change():
    before = est(True, 20.0, 6.0, crews=3)
    after = est(True, 10.0, 3.0, crews=6)
    scn = Scenario("000000001", crews=6)
    d = alerts.decide(risk(), after, scn, medical_need=False)
    msgs = messages.build_all(cust(), risk(), after, d, scn, previous=before, previous_scenario=scn.with_(crews=3))
    e = msgs["estimate_updated"]
    assert "earlier" in e.estimate and "crews on your circuit changed from 3 to 6" in e.estimate and e.would_send


def test_estimate_updated_dead_band_and_reasons():
    scn = Scenario("000000001", crews=3)
    d = alerts.decide(risk(), est(), scn, medical_need=False)
    # identical estimate → "No change", nothing would be sent
    same = messages.build_all(cust(), risk(), est(), d, scn, previous=est(), previous_scenario=scn)["estimate_updated"]
    assert same.estimate.startswith("No change") and not same.would_send and "E5" in same.note
    # 1 h move → small change, under the 2 h threshold
    small = messages.build_all(cust(), risk(), est(True, 11.0), d, scn, previous=est(True, 10.0), previous_scenario=scn)["estimate_updated"]
    assert small.estimate.startswith("Small change") and not small.would_send
    # a delay-only change is attributed to the delay, never to access
    scn_delay = scn.with_(crew_start_delay_h=6.0, access_difficulty=True)
    prev_scn = scn.with_(access_difficulty=True)
    moved = messages.build_all(cust(), risk(), est(True, 10.0, delay=6.0), alerts.decide(risk(), est(True, 10.0, delay=6.0), scn_delay, False),
                               scn_delay, previous=est(True, 10.0), previous_scenario=prev_scn)["estimate_updated"]
    assert "moved 6 h later" in moved.estimate and "crews now start 6 h after the storm" in moved.estimate and "access" not in moved.estimate.split("Why:")[1]


def test_not_affected_and_unknown_messages_are_grammatical():
    scn = Scenario("000000001")
    d = alerts.decide(risk("Low", 0.1), est(False), scn, medical_need=False)
    msgs = messages.build_all(cust(), risk("Low", 0.1), est(False), d, scn)
    assert "did not place damage" in msgs["outage_confirmed"].estimate
    assert "would likely take no interruption" not in msgs["pre_storm"].estimate
    assert "did not touch your line" in msgs["pre_storm"].estimate
    # fallback: no number is sent, the customer gets the "we are assessing" wording (rule E6)
    unknown = est(False, state=STATE_UNKNOWN)
    r = risk("High", 0.58, "Low", method="circuit_average")
    d = alerts.decide(r, unknown, scn, medical_need=False)
    msgs = messages.build_all(cust(), r, unknown, d, scn)
    assert "still assessing" in msgs["pre_storm"].estimate and "58%" not in msgs["pre_storm"].estimate
    assert "cannot yet place" in msgs["outage_confirmed"].estimate
    assert "0 repairs" not in msgs["estimate_updated"].know


def test_opted_out_messages_are_marked_not_sent():
    scn = Scenario("000000001", opted_in=False)
    d = alerts.decide(risk(), est(), scn, medical_need=False)
    msgs = messages.build_all(cust(), risk(), est(), d, scn)
    assert all(not m.would_send for m in msgs.values()) and all("Not enrolled" in m.note for m in msgs.values())
