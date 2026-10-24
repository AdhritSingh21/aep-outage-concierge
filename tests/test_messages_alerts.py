"""Unit tests for alerts, guidance and messages on synthetic risk/restoration objects."""
from __future__ import annotations

import pandas as pd

from concierge import alerts, guidance, messages
from concierge.restoration import RestorationEstimate
from concierge.risk import RiskResult
from concierge.scenario import Scenario


def risk(band="High", p=0.55, conf="Medium"):
    return RiskResult(p, band, conf, 1.0, 0.8, {"circuit_damage_ref": 12}, [{"name": "x", "share": 0.5, "detail": "y"}], [], "path")


def est(affected=True, mid=10.0, local=4.0, crews=3):
    return RestorationEstimate("Observed damage set (Feb-2025 records on this circuit)", affected, None, 0.75 * mid, mid, 1.5 * mid, 0.0,
                               mid, local if affected else None, "F1" if affected else None, "R1" if affected else None,
                               "the recloser zone 1 device below the substation", 20, 3, 2, 500, crews, narrative="n")


def cust():
    return pd.Series({"premise_nu": "000000001", "city": "Roanoke", "circuit_id": "0000005", "service_kind": "Overhead",
                      "life_support": False, "has_path": True, "path_zone_ids": "F1|R1|B1", "path_depth": 2})


def test_alert_rules():
    scn = Scenario("000000001", hours_to_landfall=30)
    d = alerts.decide(risk("Low", 0.05), est(False), scn, medical_need=False)
    assert d.eligible_outage_alerts and not d.eligible_pre_storm and not d.priority
    d = alerts.decide(risk("Moderate", 0.2), est(True, 6), scn, medical_need=False)
    assert d.eligible_pre_storm and d.channels == ["SMS", "email"]
    assert any(s["stage"] == "pre_storm" and s["when"] == "24 h before the storm" for s in d.schedule)
    d = alerts.decide(risk("Low", 0.05), est(True, 30), scn, medical_need=False)
    assert d.eligible_pre_storm and "voice call" in d.channels
    assert any(e["active"] for e in d.escalations if e["trigger"].startswith("Estimate > 24"))
    d = alerts.decide(risk("Low", 0.05), est(True, 9), scn, medical_need=True)
    assert d.priority and "voice call" in d.channels
    assert any(e["active"] for e in d.escalations if e["trigger"].startswith("Medical"))
    d = alerts.decide(risk(), est(), scn.with_(opted_in=False), medical_need=False)
    assert not d.eligible_outage_alerts and not d.eligible_pre_storm and not d.schedule
    d = alerts.decide(risk("Moderate", 0.2, "Low"), est(True, 6), scn, medical_need=False)
    assert any(e["active"] for e in d.escalations if e["trigger"].startswith("Low confidence"))


def test_guidance_shape():
    g = guidance.build(risk("Very high", 0.9), est(True, 40), medical_need=False, hours_to_landfall=12)
    tags = [x.tag for x in g]
    assert tags[:3] == ["SAFETY"] * 3 and "PREPARE" in tags and "DURING" in tags and "MEDICAL" not in tags
    assert any("1–3 days" in x.text for x in g)
    gm = guidance.build(risk("Low", 0.05), est(False), medical_need=True, hours_to_landfall=12)
    assert any(x.tag == "MEDICAL" for x in gm)
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
    assert "8 h and 15 h" in msgs["outage_confirmed"].estimate or "between" in msgs["outage_confirmed"].estimate
    assert "recloser zone" in msgs["outage_confirmed"].estimate


def test_estimate_updated_diffs_previous_and_names_crew_change():
    before = est(True, 20.0, 6.0, crews=3)
    after = est(True, 10.0, 3.0, crews=6)
    scn = Scenario("000000001", crews=6)
    d = alerts.decide(risk(), after, scn, medical_need=False)
    msgs = messages.build_all(cust(), risk(), after, d, scn, previous=before)
    e = msgs["estimate_updated"].estimate
    assert "earlier" in e and "crews on your circuit changed from 3 to 6" in e


def test_not_affected_messages():
    scn = Scenario("000000001")
    d = alerts.decide(risk("Low", 0.1), est(False), scn, medical_need=False)
    msgs = messages.build_all(cust(), risk("Low", 0.1), est(False), d, scn)
    assert "did not place damage" in msgs["outage_confirmed"].estimate
    assert "no interruption" in msgs["pre_storm"].estimate
