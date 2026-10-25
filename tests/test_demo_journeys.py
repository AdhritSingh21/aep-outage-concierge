"""Journey tests on the committed demo bundle (always run; no organizer data needed): the delay convention, restoration
states, band/percent agreement, the crash guard, the script-chosen examples, priority weighting and the audit of the
example premises the app shows first."""
from __future__ import annotations

import json

import pytest

from concierge import config
from concierge.data import DataBundle
from concierge.journey import run
from concierge.restoration import STATE_AFFECTED, STATE_NOT_AFFECTED, STATE_UNKNOWN, fmt_hours, fmt_range
from concierge.risk import band_for
from concierge.scenario import Scenario
from concierge.scenarios import DamageSets


@pytest.fixture(scope="module")
def ctx():
    d = DataBundle(config.DEMO)
    return d, DamageSets(d)


def _first_affected(d, s):
    for e in d.sample_premises():
        j = run(Scenario(e["premise_nu"], crews=s.default_crews(str(d.premise(e["premise_nu"])["circuit_id"]))), d, s)
        if j.restoration.state == STATE_AFFECTED:
            return e["premise_nu"], j
    pytest.skip("no affected example premise in the bundle")


def test_delay_is_counted_exactly_once(ctx):
    d, s = ctx
    pid, base = _first_affected(d, s)
    k = base.scenario.crews
    delayed = run(Scenario(pid, crews=k, crew_start_delay_h=6.0), d, s)
    a, b = base.restoration, delayed.restoration
    # schedules are solved from hour 0; the delay shifts every displayed time by exactly 6 h
    assert min(x.start for x in b.policies["dependency_aware"].assignments) == 0.0
    assert b.mid_total == pytest.approx(a.mid_total + 6.0)
    assert b.low_total == pytest.approx(a.low_total + 6.0) and b.high_total == pytest.approx(a.high_total + 6.0)
    assert b.total(b.service_restored_h) == pytest.approx(a.total(a.service_restored_h) + 6.0)
    assert delayed.path_table["repair_done_h"].dropna().max() == pytest.approx(base.path_table["repair_done_h"].dropna().max() + 6.0, abs=0.11)
    assert "after the storm ends" in b.clock_label and "after the storm ends" in delayed.messages["outage_confirmed"].estimate
    # the alert thresholds see the once-counted total
    mid = b.mid_total
    assert any(e["active"] for e in delayed.alerts.escalations if e["trigger"].startswith("Estimate > 24")) == (mid > 24)


def test_formatting_keeps_one_unit_per_range():
    assert fmt_range(8.0, 16.0) == "8 – 16 h"
    assert fmt_range(0.4, 3.0) == "< 1 – 3 h"
    assert fmt_range(22.0, 55.0) == "0.9 – 2.3 days"
    assert fmt_hours(0.3) == "< 1 h" and fmt_hours(19.4) == "19 h" and fmt_hours(60.0) == "2.5 days"
    assert "under 1 hour" not in fmt_hours(0.5)


def test_examples_cover_the_brief_states(ctx):
    d, s = ctx
    states = {}
    for e in d.sample_premises():
        c = d.premise(e["premise_nu"])
        j = run(Scenario(e["premise_nu"], crews=s.default_crews(str(c["circuit_id"]))), d, s)
        states[e["premise_nu"]] = j.restoration.state
        # band word and displayed percent never disagree
        if j.risk.percent_text not in (">99%", "<1%"):
            assert band_for(int(j.risk.percent_text.rstrip("%")) / 100) == j.risk.band
        # every message is grammatical whatever the state
        assert "would likely take no interruption" not in j.messages["pre_storm"].estimate
        assert "0 repairs" not in j.messages["estimate_updated"].know
        if j.restoration.state == STATE_NOT_AFFECTED and c["has_path"]:
            assert j.restoration.alt and "n_draws" in j.restoration.alt
        if not c["has_path"]:
            assert j.restoration.state == STATE_UNKNOWN and not j.risk.reliable
            assert "still assessing" in j.messages["pre_storm"].estimate
    assert STATE_AFFECTED in states.values() and STATE_UNKNOWN in states.values()


def test_first_example_moves_with_severity_and_life_support_example_escalates(ctx):
    d, s = ctx
    doc = d.load_examples()
    if not doc or not doc.get("examples"):
        pytest.skip("examples.json not generated")
    first = doc["examples"][0]["premise_nu"]
    j = run(Scenario(first, crews=s.default_crews(str(d.premise(first)["circuit_id"]))), d, s)
    sens = j.risk.sensitivity
    assert band_for(round(sens[0.5], 2)) != band_for(round(sens[3.0], 2))   # the storm slider visibly moves the band
    ls = [e for e in doc["examples"] if "life-support" in e["label"]]
    if ls:
        pid = ls[0]["premise_nu"]
        c = d.premise(pid)
        assert bool(c["life_support"])
        j = run(Scenario(pid, crews=s.default_crews(str(c["circuit_id"]))), d, s)   # medical_need None → account flag
        assert j.medical_need and j.alerts.priority and "voice call" in j.alerts.channels
        assert any(e["active"] for e in j.alerts.escalations if e["trigger"].startswith("Medical"))
        assert any(g.tag == "MEDICAL" for g in j.guidance)
        # the account flag can be switched off by the customer: nothing is inferred
        off = run(Scenario(pid, crews=j.scenario.crews, medical_need=False), d, s)
        assert not off.medical_need and not off.alerts.priority


def test_missing_path_zones_do_not_crash(ctx):
    d, s = ctx
    c = d.t["customers"]
    bad = [p for p, path in zip(c["premise_nu"], c["path_zone_ids"]) if isinstance(path, str) and any(z not in d.zones.index for z in path.split("|"))]
    if not bad:
        pytest.skip("every path zone exists in this bundle")
    j = run(Scenario(bad[0], crews=2), d, s)
    assert j.missing_path_zones >= 1 and len(j.path_table) <= len(str(d.premise(bad[0])["path_zone_ids"]).split("|"))
    assert j.risk.band and j.messages["pre_storm"].estimate


def test_demo_story_numbers_match_the_model(ctx):
    d, s = ctx
    demo = d.demo_story()
    if not demo:
        pytest.skip("no demo story in examples.json")
    base = run(Scenario(demo["premise_nu"], crews=int(demo["crews"])), d, s)
    assert base.restoration.state == STATE_AFFECTED
    assert round(base.restoration.mid_total, 1) == demo["baseline"]["most_likely_h"]
    for step in demo["steps"][1:]:
        j = run(Scenario(demo["premise_nu"], crews=int(demo["crews"])).with_(**step["set"]), d, s, previous=base)
        assert j.restoration.state == STATE_AFFECTED
        assert fmt_hours(j.restoration.mid_total) in step["expect"]


def test_priority_weighting_changes_the_order_not_the_inputs(ctx):
    d, s = ctx
    # a circuit with affected life-support customers or facilities under the Feb-2025 set
    for circ in d.circuits.sort_values("n_damage_ref", ascending=False).index:
        tasks = s.historical(circ)
        if not tasks:
            continue
        inst0 = s.instance(circ, tasks, 2)
        if not (inst0.affected_life_support or inst0.affected_facilities):
            continue
        c = d.t["customers"]
        pid = c[(c["circuit_id"] == circ) & c["has_path"]].iloc[0]["premise_nu"]
        a = run(Scenario(pid, crews=2), d, s)
        b = run(Scenario(pid, crews=2, prioritize_critical=True), d, s)
        ma, mb = a.restoration.policies["dependency_aware"].metrics, b.restoration.policies["dependency_aware"].metrics
        assert ma.total_crew_hours == pytest.approx(mb.total_crew_hours)      # identical inputs
        assert "customers-equivalent" in b.restoration.policies["dependency_aware"].status.order_source
        assert mb.life_support_t100_h <= ma.life_support_t100_h + 1e-9 or mb.facility_t100_h <= ma.facility_t100_h + 1e-9
        return
    pytest.skip("no circuit with affected priority loads in the bundle")


def test_facilities_are_counts_only(ctx):
    d, _ = ctx
    assert set(d.facility_zone.columns) <= {"kind", "circuit_id", "zone_id", "dist_m"}
    assert "n_facilities_downstream" in d.zones.columns
    total = sum(sum(d.circuit_facilities(c).values()) for c in d.circuits.index)
    assert total == len(d.facility_zone)


def test_examples_json_is_script_produced(ctx):
    d, _ = ctx
    p = d.examples_file()
    if not p.exists():
        pytest.skip("examples.json not generated")
    doc = json.loads(p.read_text(encoding="utf8"))
    assert doc["generated_by"] == "scripts/pick_examples.py" and doc["examples"]
