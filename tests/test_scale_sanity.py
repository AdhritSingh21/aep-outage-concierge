"""Scale sanity check on real data (marker: data): with the calibrated default crews, the Observed Feb-2025 damage
sets restore within the day-scale the organizer's repair records show (0–6 days). This is a plausibility check,
not a validation: the crew default is calibrated to a 72 h FIFO makespan by construction."""
from __future__ import annotations

import pytest

from concierge.data import DataBundle
from concierge.scenarios import DamageSets
from concierge.scheduling import solve

pytestmark = pytest.mark.data


def test_default_crews_restore_hardest_circuits_within_observed_day_scale():
    data = DataBundle()
    sets = DamageSets(data)
    top = data.circuits.sort_values("n_damage_ref", ascending=False).head(5)
    for circ in top["circuit_id"]:
        tasks = sets.historical(circ)
        inst = sets.instance(circ, tasks, sets.default_crews(circ))
        res = solve(inst, "dependency_aware", improve=False, reference=False)
        days = res.metrics.t100_h / 24
        assert 0.5 <= days <= 6.0, f"circuit {circ}: {days:.1f} days"
        assert res.metrics.affected_customers <= int(data.circuits.loc[circ, "n_with_path"])


def test_demo_premise_shows_repair_vs_service_gap():
    data = DataBundle()
    sets = DamageSets(data)
    demo = sets.demo_premise()
    assert demo is not None and demo["repair_to_service_gap_h"] >= 2.0
    cust = data.premise(demo["premise_nu"])
    assert cust["leaf_zone_id"] == demo["leaf_zone_id"] and cust["circuit_id"] == demo["circuit_id"]
