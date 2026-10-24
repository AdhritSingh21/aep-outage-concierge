"""Integration tests on the real processed data: the full journey for the sample premises, scenario consistency,
fallbacks and privacy (marker: data)."""
from __future__ import annotations

import pytest

from concierge import config
from concierge.data import DataBundle
from concierge.journey import run
from concierge.scenario import Scenario
from concierge.scenarios import DamageSets

pytestmark = pytest.mark.data


@pytest.fixture(scope="module")
def ctx():
    d = DataBundle()
    return d, DamageSets(d)


def test_sample_premises_complete_journey(ctx):
    data, sets = ctx
    samples = data.sample_premises()
    assert len(samples) >= 4
    for s in samples:
        j = run(Scenario(s["premise_nu"], crews=sets.default_crews(str(data.premise(s["premise_nu"])["circuit_id"]))), data, sets)
        assert 0 <= j.risk.probability <= 1 and j.risk.band in {b for _, b in config.RISK_BANDS} | {"Unavailable"}
        assert j.risk.factors and j.risk.confidence in ("High", "Medium", "Low", "None")
        assert set(j.messages) == {"pre_storm", "outage_confirmed", "estimate_updated", "restoration_complete"}
        assert j.guidance and j.guidance[0].tag == "SAFETY"
        assert j.alerts.rules and j.evidence["Observed"]
        if j.restoration.affected:
            assert j.restoration.low_h <= j.restoration.mid_h <= j.restoration.high_h
            assert j.restoration.service_restored_h >= (j.restoration.local_repair_done_h or 0)
        if not j.customer["has_path"]:
            assert j.risk.method in ("circuit_average", "unavailable") and j.risk.confidence in ("Low", "None")
            assert any("No upstream path" in f for f in j.risk.flags) or j.risk.method == "unavailable"
            assert len(j.path_table) == 0


def test_more_crews_update_every_output_together(ctx):
    data, sets = ctx
    prem = data.sample_premises()[0]["premise_nu"]
    k = sets.default_crews(str(data.premise(prem)["circuit_id"]))
    a = run(Scenario(prem, crews=k), data, sets)
    b = run(Scenario(prem, crews=2 * k), data, sets, previous=a)
    assert a.restoration.affected and b.restoration.affected
    assert b.restoration.mid_h < a.restoration.mid_h
    assert b.restoration.policies["dependency_aware"].metrics.customer_hours < a.restoration.policies["dependency_aware"].metrics.customer_hours
    # explanation, chain table and messages all carry the new numbers
    assert f"{2 * k} crews" in b.restoration.narrative
    assert b.path_table["repair_done_h"].dropna().max() <= a.path_table["repair_done_h"].dropna().max()
    assert "earlier" in b.messages["estimate_updated"].estimate and f"{k} to {2 * k}" in b.messages["estimate_updated"].estimate
    assert f"{2 * k} crew" in b.messages["outage_confirmed"].know
    # the risk does not depend on crews
    assert a.risk.probability == b.risk.probability


def test_access_difficulty_slows_everything(ctx):
    data, sets = ctx
    prem = data.sample_premises()[0]["premise_nu"]
    k = sets.default_crews(str(data.premise(prem)["circuit_id"]))
    a = run(Scenario(prem, crews=k), data, sets)
    b = run(Scenario(prem, crews=k, access_difficulty=True), data, sets, previous=a)
    assert b.restoration.mid_h > a.restoration.mid_h
    assert b.restoration.instance.total_hours > a.restoration.instance.total_hours
    assert "difficult" in b.messages["estimate_updated"].know.lower()
    assert any(e["active"] for e in b.alerts.escalations if "access" in e["trigger"].lower())


def test_identical_scenarios_are_identical(ctx):
    data, sets = ctx
    prem = data.sample_premises()[1]["premise_nu"]
    s1, s2 = Scenario(prem, crews=3, damage_source="simulated", n_draws=5), Scenario(prem, crews=3, damage_source="simulated", n_draws=5)
    assert s1.key() == s2.key()
    a, b = run(s1, data, sets), run(s2, data, sets)
    assert a.restoration.mid_h == b.restoration.mid_h and a.risk.probability == b.risk.probability
    assert a.messages["pre_storm"].body == b.messages["pre_storm"].body


def test_severity_moves_risk_and_simulated_damage(ctx):
    data, sets = ctx
    prem = data.sample_premises()[3]["premise_nu"]  # quiet circuit
    lo = run(Scenario(prem, severity=0.5, damage_source="simulated", n_draws=10), data, sets)
    hi = run(Scenario(prem, severity=3.0, damage_source="simulated", n_draws=10), data, sets)
    assert hi.risk.probability > lo.risk.probability
    assert hi.restoration.draws["tasks_mean"] >= lo.restoration.draws["tasks_mean"]
    assert hi.restoration.draws["p_affected"] >= lo.restoration.draws["p_affected"]


def test_no_pii_in_outputs(ctx):
    """The customer's real road name / address (from the raw organizer file) must never appear in any output, and the
    app-facing customer record must not carry PII columns at all."""
    data, sets = ctx
    raw = None
    dbf = config.SHAPEFILES["customers"].with_suffix(".dbf")
    if dbf.exists():
        from concierge.shapefile_io import read_dbf
        raw = read_dbf(dbf, ["premise_nu", "road_name", "address", "city"])
        raw["premise_nu"] = raw["premise_nu"].astype(str).str.zfill(9)
        raw = raw.set_index("premise_nu")
    for s in data.sample_premises()[:3]:
        j = run(Scenario(s["premise_nu"]), data, sets)
        text = (" ".join(m.body for m in j.messages.values()) + j.restoration.narrative
                + " ".join(f["detail"] for f in j.risk.factors) + " ".join(v for _, rows in j.evidence.items() for _, v in rows)).lower()
        assert all(c not in j.customer.index for c in config.PII_FIELDS)
        assert s["premise_nu"] in text  # the anonymized id is the only identifier used
        if raw is not None and s["premise_nu"] in raw.index:
            for col in ("road_name", "address"):
                val = str(raw.loc[s["premise_nu"], col]).strip().lower()
                if len(val) >= 4:
                    assert val not in text, f"{col} leaked into outputs"
