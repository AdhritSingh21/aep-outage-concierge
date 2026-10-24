"""Unit tests for the risk model on synthetic circuits/zones (no organizer data needed)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from concierge import config
from concierge.risk import (RiskModel, band_for, customer_path_hazard, fit_rate_model, probability, score_customer,
                            zone_hazards)


def synthetic():
    rng = np.random.default_rng(0)
    n = 40
    oh = rng.uniform(5, 40, n)
    ug = rng.uniform(0, 10, n)
    lateral = oh * rng.uniform(0.3, 0.8, n)
    pole_year = rng.uniform(1960, 2010, n)
    rate = 0.4 * np.exp(0.3 * (1985 - pole_year) / 10)
    wire = rng.poisson(rate * oh)
    circuits = pd.DataFrame({
        "circuit_id": [f"{i:07d}" for i in range(1, n + 1)], "oh_miles": oh, "ug_miles": ug, "lateral_miles": lateral,
        "pole_year_mean": pole_year, "pole_year_known_pct": rng.uniform(30, 90, n),
        "n_damage_ref_wire": wire, "n_damage_ref_pole": 0, "n_damage_ref_equipment": rng.poisson(1.0, n),
        "n_damage_ref": wire + 1, "n_customers": 500,
    })
    circuits["n_damage_ref"] = circuits["n_damage_ref_wire"] + circuits["n_damage_ref_equipment"]
    zones = pd.DataFrame({
        "zone_id": ["B1", "R1", "F1", "F2"], "circuit_id": ["0000001"] * 4, "kind": ["breaker", "recloser", "fuse", "fuse"],
        "depth": [0, 1, 2, 2], "parent_zone_id": [None, "B1", "R1", "R1"], "oh_miles": [5.0, 3.0, 1.0, 0.0],
        "ug_miles": [0.0, 0.0, 0.0, 2.0], "n_transformers_overhead": [10, 5, 3, 0], "n_customers_direct": [50, 20, 30, 40],
        "n_customers_downstream": [140, 90, 30, 40], "pole_year_mean": [1980.0, 1990.0, 2000.0, np.nan],
        "pole_year_known_pct": [60.0, 60.0, 60.0, 0.0], "n_damage_ref": [3, 2, 1, 0],
        "n_damage_ref_line": [2, 2, 1, 0], "n_damage_ref_equipment": [1, 0, 0, 0],
    })
    damage = pd.DataFrame({"in_reference_storm": [True] * 6, "circuit_id": ["0000001"] * 6,
                           "asset_type": ["primary", "Pole", "Fuse Bank", "Transformer", "secondary", "secondary"]})
    customers = pd.DataFrame({
        "premise_nu": ["p1", "p2", "p3", "p4"], "circuit_id": ["0000001"] * 4,
        "service_kind": ["Overhead", "Underground", "Overhead", "Unknown"],
        "has_path": [True, True, False, True], "leaf_zone_id": ["F1", "F2", None, "B1"],
        "path_zone_ids": ["F1|R1|B1", "F2|R1|B1", None, "B1"], "life_support": [False, True, False, False],
    })
    return circuits, zones, damage, customers


@pytest.fixture(scope="module")
def fitted():
    circuits, zones, damage, customers = synthetic()
    model = fit_rate_model(circuits, damage, zones, customers)
    zh = zone_hazards(zones, circuits, model)
    return circuits, zones, damage, customers, model, zh


def test_fit_recovers_direction_and_validates(fitted):
    circuits, zones, damage, customers, model, zh = fitted
    assert model.coef["pole_year_centered"] < 0          # older poles → more damage in the synthetic truth
    assert model.n_circuits_fit == 40
    assert model.validation["loo_spearman_model"] > 0.3
    assert len(model.validation["calibration_quintiles"]) == 5
    assert model.rate_equipment_per_unit > 0 and model.rate_service_per_overhead_customer > 0


def test_zone_hazards_follow_exposure(fitted):
    circuits, zones, *_, model, zh = fitted
    prior = zone_hazards(zones, circuits, model, shrink=False).set_index("zone_id")
    assert prior.loc["B1", "mu_line"] > prior.loc["R1", "mu_line"] > prior.loc["F1", "mu_line"] > prior.loc["F2", "mu_line"] == 0
    assert prior.loc["F2", "lam_equipment"] == pytest.approx(model.rate_equipment_per_unit)  # fuse itself, no OH xfmr
    assert prior.loc["B1", "lam_equipment"] == pytest.approx(10 * model.rate_equipment_per_unit)
    assert model.dispersion_line > 0 and model.dispersion_equipment > 0


def test_shrinkage_moves_toward_observed_counts(fitted):
    circuits, zones, *_, model, zh = fitted
    z = zh.set_index("zone_id")
    # posterior lies between prior mean and observed count, on the observed side
    for zid in ("B1", "R1", "F1"):
        mu, n, lam = z.loc[zid, "mu_line"], z.loc[zid, "n_damage_ref_line"], z.loc[zid, "lam_line"]
        lo, hi = sorted([mu, n])
        assert lo - 1e-9 <= lam <= hi + 1e-9
    # a zone with no observed damage shrinks below its prior; a zone with more damage than expected rises above it
    assert z.loc["F2", "lam_line"] <= max(z.loc["F2", "mu_line"], model.mu_floor)
    assert z.loc["R1", "lam_line"] > z.loc["R1", "mu_line"] or z.loc["R1", "n_damage_ref_line"] <= z.loc["R1", "mu_line"]


def test_path_hazard_sums_each_zone_once(fitted):
    circuits, zones, damage, customers, model, zh = fitted
    ph = customer_path_hazard(customers, zh)
    z = zh.set_index("zone_id")["lam_total"]
    assert ph["p1"] == pytest.approx(z["F1"] + z["R1"] + z["B1"])
    assert ph["p4"] == pytest.approx(z["B1"])
    assert "p3" not in ph.index


def test_probability_and_bands():
    assert probability(0.0) == 0.0 and 0 < probability(0.1) < probability(1.0) < 1.0
    assert band_for(0.1) == "Low" and band_for(0.15) == "Moderate" and band_for(0.4) == "High" and band_for(0.9) == "Very high"


def test_score_monotone_in_severity_and_exposure(fitted):
    circuits, zones, damage, customers, model, zh = fitted
    circ = circuits.set_index("circuit_id").loc["0000001"]
    c1 = customers.iloc[0]
    r1 = score_customer(c1, zh, circ, model, 1.0)
    r2 = score_customer(c1, zh, circ, model, 2.0)
    assert 0 <= r1.probability <= 1 and r2.probability > r1.probability
    assert r1.method == "path" and r1.confidence in ("High", "Medium")
    # the customer on the breaker zone only has less exposure than one three zones deep
    r4 = score_customer(customers.iloc[3], zh, circ, model, 1.0)
    assert r4.hazard < r1.hazard
    assert any(f["name"] == "Upstream line exposure" for f in r1.factors)
    assert r1.components["zones_upstream"] == 3


def test_underground_service_has_no_service_hazard(fitted):
    circuits, zones, damage, customers, model, zh = fitted
    circ = circuits.set_index("circuit_id").loc["0000001"]
    ug = score_customer(customers.iloc[1], zh, circ, model, 1.0)
    assert ug.components["service"] == 0.0
    unknown = score_customer(customers.iloc[3], zh, circ, model, 1.0)
    assert unknown.components["service"] == pytest.approx(0.5 * model.rate_service_per_overhead_customer)
    assert any("unknown" in f.lower() for f in unknown.flags)


def test_fallbacks(fitted):
    circuits, zones, damage, customers, model, zh = fitted
    circ = circuits.set_index("circuit_id").loc["0000001"]
    nopath = customers.iloc[2]
    r = score_customer(nopath, zh, circ, model, 1.0, circuit_avg_hazard=0.3)
    assert r.method == "circuit_average" and r.confidence == "Low"
    assert any("No upstream path" in f for f in r.flags)
    assert r.hazard == pytest.approx(0.3 + model.rate_service_per_overhead_customer)
    r0 = score_customer(nopath, zh, circ, model, 1.0, circuit_avg_hazard=None)
    assert r0.method == "unavailable" and r0.band == "Unavailable" and r0.flags


def test_model_round_trip(tmp_path, fitted):
    *_, model, zh = fitted
    model.to_json(tmp_path / "m.json")
    back = RiskModel.from_json(tmp_path / "m.json")
    assert back.coef == model.coef and back.intercept == model.intercept
