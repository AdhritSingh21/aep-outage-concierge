"""Scheduling tests on synthetic trees: feasibility, AND-restoration semantics, no double counting, toy optimality,
brute-force parity, identical inputs across policies, determinism."""
from __future__ import annotations

import itertools

import numpy as np
import pytest

from concierge.scheduling import (POLICIES, Instance, Task, brute_force_reference, compare_policies, evaluate,
                                  list_schedule, metrics_table, objective, order_dependency_aware, solve)

# tree: B (breaker) → R (recloser) → {F1, F2}; F3 under B
PARENT = {"B": None, "R": "B", "F1": "R", "F2": "R", "F3": "B"}
GROUPS = {"F1": 100, "F2": 50, "R": 10, "F3": 40, "B": 5}
LS = {"F1": 2, "F3": 1}


def make(tasks, crews=1, delay=0.0):
    return Instance("c", tasks, PARENT, GROUPS, LS, crews=crews, start_delay_h=delay)


def test_feasibility_and_bookkeeping():
    tasks = [Task("a", "R", 2.0, 0), Task("b", "F1", 1.0, 1), Task("c", "F3", 3.0, 2), Task("d", "F2", 1.5, 3)]
    inst = make(tasks, crews=2, delay=1.0)
    for p in POLICIES:
        r = solve(inst, p)
        assert sorted(r.order) == sorted(t.task_id for t in tasks)
        by_crew = {}
        for a in r.assignments:
            assert a.start >= 1.0 and a.end == pytest.approx(a.start + inst.task_by_id[a.task_id].hours)
            by_crew.setdefault(a.crew, []).append((a.start, a.end))
        for ivs in by_crew.values():
            ivs.sort()
            for (s1, e1), (s2, e2) in zip(ivs, ivs[1:]):
                assert s2 >= e1 - 1e-9
        assert r.metrics.total_crew_hours == pytest.approx(7.5)


def test_service_restoration_is_max_over_path_not_local_repair():
    # F1 (100 customers) repaired at hour 1 by crew 2 while R (upstream) finishes at hour 2 on crew 1
    tasks = [Task("r", "R", 2.0, 0), Task("f1", "F1", 1.0, 1)]
    inst = make(tasks, crews=2)
    completion = {"r": 2.0, "f1": 1.0}
    zone_done, restored, m = evaluate(inst, completion)
    assert restored["F1"] == 2.0          # service waits for the upstream recloser
    assert restored["F2"] == 2.0          # F2 has no local damage but is below R
    assert restored["R"] == 2.0
    assert restored["F3"] == 0.0 and restored["B"] == 0.0   # not affected at all
    assert m.affected_customers == 160 and m.customer_hours == pytest.approx(160 * 2.0)
    assert m.life_support_hours == pytest.approx(2 * 2.0) and m.life_support_t100_h == 2.0


def test_no_double_counting_of_customers():
    tasks = [Task("r", "R", 1.0, 0), Task("f1", "F1", 1.0, 1), Task("f2", "F2", 1.0, 2), Task("b", "B", 1.0, 3)]
    inst = make(tasks)
    assert inst.affected_customers == sum(GROUPS.values())   # breaker damaged → everyone, exactly once
    assert sum(inst.deepest_damaged_weights().values()) == inst.affected_customers
    assert inst.deepest_damaged_weights() == {"R": 10, "F1": 100, "F2": 50, "B": 45}   # F3 (40) + B direct (5)


def test_toy_dependency_vs_customers_first():
    # upstream R: 2 h, 10 own customers but 160 depend on it; downstream F1: 1 h, 100 customers
    tasks = [Task("r", "R", 2.0, 0), Task("f1", "F1", 1.0, 1)]
    inst = make(tasks, crews=1)
    res = compare_policies(inst)
    # customers-first ranks F1 (100/1) above R (160/2=80): F1 then R → everyone waits for R at hour 3
    assert res["customers_first"].order == ["f1", "r"]
    assert res["customers_first"].metrics.customer_hours == pytest.approx(160 * 3.0)
    # dependency-aware does R first: F2+R customers (60) restored at 2 h, F1 (100) at 3 h
    assert res["dependency_aware"].order == ["r", "f1"]
    assert res["dependency_aware"].metrics.customer_hours == pytest.approx(60 * 2.0 + 100 * 3.0)
    assert res["dependency_aware"].status.gap_pct == 0.0 and res["dependency_aware"].status.reference_kind.startswith("exact")


def test_brute_force_parity_small_instances():
    rng = np.random.default_rng(1)
    zones = list(PARENT)
    for trial in range(12):
        n = int(rng.integers(3, 7))
        tasks = [Task(f"t{i}", zones[int(rng.integers(len(zones)))], float(rng.uniform(0.5, 4)), i) for i in range(n)]
        for crews in (1, 2):
            inst = make(tasks, crews=crews)
            ref = brute_force_reference(inst)
            r = solve(inst, "dependency_aware")
            assert r.metrics.customer_hours >= ref - 1e-9
            if crews == 1:
                assert r.metrics.customer_hours == pytest.approx(ref)            # Horn + swaps is optimal for one crew here
            else:
                assert r.metrics.customer_hours <= 1.05 * ref                    # within 5% of best order on 2 crews
            assert r.status.reference_value == pytest.approx(ref)


def test_policies_share_identical_inputs_and_are_deterministic():
    rng = np.random.default_rng(2)
    zones = list(PARENT)
    tasks = [Task(f"t{i}", zones[int(rng.integers(len(zones)))], float(rng.uniform(0.5, 6)), i) for i in range(25)]
    inst = make(tasks, crews=3)
    a = compare_policies(inst, reference=False)
    b = compare_policies(inst, reference=False)
    hours = {p: r.metrics.total_crew_hours for p, r in a.items()}
    assert len(set(round(h, 9) for h in hours.values())) == 1
    for p in POLICIES:
        assert a[p].order == b[p].order and a[p].metrics.customer_hours == b[p].metrics.customer_hours
    assert a["dependency_aware"].metrics.customer_hours <= a["fifo"].metrics.customer_hours + 1e-9
    assert "heuristic" in " ".join(a["dependency_aware"].status.notes).lower()
    tbl = metrics_table(a)
    assert set(tbl["policy"]) == {"FIFO (report order)", "Customers-per-hour heuristic", "Dependency-aware (ours)"}


def test_many_crews_makespan_equals_longest_task():
    tasks = [Task(f"t{i}", "F1", h, i) for i, h in enumerate([1.0, 2.0, 5.0])]
    inst = make(tasks, crews=5)
    r = solve(inst, "fifo")
    assert r.metrics.makespan_h == 5.0 and r.group_restored["F1"] == 5.0


def test_horn_order_respects_upstream_first_on_single_crew():
    tasks = [Task("f1", "F1", 1.0, 0), Task("r", "R", 1.0, 1), Task("b", "B", 1.0, 2)]
    inst = make(tasks, crews=1)
    order = order_dependency_aware(inst)
    assert order.index("b") < order.index("r") < order.index("f1")
    assert objective(inst, order) == pytest.approx(brute_force_reference(inst))
