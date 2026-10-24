"""Restoration-time estimate for one customer under a Scenario (Simulated output built on Observed damage/topology).

The range is a *range*, not a promise. All three points use the dependency-aware schedule; they differ in the labor
assumption: low = workbook hours × 0.75, mid = × 1.0, high = × 1.5. Because every task scales by the same factor
and the ordering depends only on weight/duration ratios, the schedule scales exactly, so the range needs one solve.
For simulated damage sets the midpoint is the median service-restoration time over seeded draws in which the
customer is affected at all, and low/high apply the labor factors to the 10th/90th percentile draws.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .data import DataBundle
from .scenario import Scenario
from .scenarios import DamageSets
from .scheduling import Instance, ScheduleResult, SolverStatus, compare_policies, solve

LABOR_LOW, LABOR_HIGH = 0.75, 1.5


@dataclass
class RestorationEstimate:
    source: str                         # "Observed damage set (Feb-2025)" | "Simulated damage sets"
    affected: bool
    p_affected: float | None            # simulated only
    low_h: float
    mid_h: float
    high_h: float
    crew_start_delay_h: float
    service_restored_h: float           # displayed schedule, hours after crews start
    local_repair_done_h: float | None   # completion of the damaged zone nearest the customer (displayed schedule)
    local_zone: str | None
    blocking_zone: str | None           # zone whose repair completes last on the customer's path
    blocking_zone_label: str
    n_tasks_circuit: int
    n_tasks_on_path: int
    n_zones_damaged_on_path: int
    affected_customers_circuit: int
    crews: int
    policies: dict[str, ScheduleResult] = field(default_factory=dict)   # displayed instance, every policy, identical inputs
    instance: Instance | None = None
    status: SolverStatus | None = None
    narrative: str = ""
    what_moves_it: list[str] = field(default_factory=list)
    draws: dict = field(default_factory=dict)

    @property
    def window_text(self) -> str:
        if not self.affected:
            return "no interruption expected from this damage set"
        return f"{fmt_hours(self.low_h + self.crew_start_delay_h)} – {fmt_hours(self.high_h + self.crew_start_delay_h)}"


def fmt_hours(h: float) -> str:
    if h != h:  # NaN
        return "—"
    if h < 1:
        return "under 1 hour"
    if h < 48:
        return f"{h:.0f} h"
    return f"{h / 24:.1f} days"


def zone_label(data: DataBundle, zone_id: str | None) -> str:
    if zone_id is None or zone_id not in data.zones.index:
        return "—"
    z = data.zones.loc[zone_id]
    if z["kind"] == "breaker":
        return "the substation breaker zone"
    return f"the {z['kind']} zone {int(z['depth'])} device{'s' if z['depth'] != 1 else ''} below the substation"


def customer_group_times(inst: Instance, res: ScheduleResult, leaf_zone: str | None) -> tuple[float, float | None, str | None, str | None]:
    """(service time, local repair time, local zone, blocking zone) for the customer's leaf zone."""
    if not leaf_zone:
        return float("nan"), None, None, None
    chain = inst.chain.get(leaf_zone, [leaf_zone])
    damaged = [z for z in chain if z in inst.damaged_zones]
    if not damaged:
        return 0.0, None, None, None
    local = damaged[0]
    times = {z: res.zone_done[z] for z in damaged}
    blocking = max(times, key=times.get)
    return times[blocking], times[local], local, blocking


def estimate(scn: Scenario, data: DataBundle, sets: DamageSets | None = None) -> RestorationEstimate:
    sets = sets or DamageSets(data)
    cust = data.premise(scn.premise_nu)
    circuit_id = str(cust["circuit_id"])
    leaf = cust["leaf_zone_id"] if isinstance(cust.get("leaf_zone_id"), str) else None
    if scn.damage_source == "historical":
        return _historical(scn, data, sets, circuit_id, leaf)
    return _simulated(scn, data, sets, circuit_id, leaf)


def _finish(est_kwargs: dict, inst: Instance, res_by_policy: dict[str, ScheduleResult], leaf: str | None,
            data: DataBundle, scn: Scenario) -> RestorationEstimate:
    res = res_by_policy["dependency_aware"]
    service, local_t, local_z, blocking = customer_group_times(inst, res, leaf)
    chain = set(inst.chain.get(leaf, [])) if leaf else set()
    on_path = [t for t in inst.tasks if t.zone_id in chain]
    est = RestorationEstimate(
        **est_kwargs, affected=bool(leaf) and blocking is not None, crew_start_delay_h=scn.crew_start_delay_h,
        service_restored_h=service, local_repair_done_h=local_t, local_zone=local_z, blocking_zone=blocking,
        blocking_zone_label=zone_label(data, blocking), n_tasks_circuit=len(inst.tasks), n_tasks_on_path=len(on_path),
        n_zones_damaged_on_path=len({t.zone_id for t in on_path}), affected_customers_circuit=inst.affected_customers,
        crews=scn.crews, policies=res_by_policy, instance=inst, status=res.status,
    )
    _narrate(est, data, scn, leaf)
    return est


def _historical(scn: Scenario, data: DataBundle, sets: DamageSets, circuit_id: str, leaf: str | None) -> RestorationEstimate:
    tasks = sets.historical(circuit_id, scn.access_difficulty, scn.labor_multiplier)
    inst = sets.instance(circuit_id, tasks, scn.crews, scn.crew_start_delay_h)
    policies = {
        "fifo": solve(inst, "fifo", improve=False, reference=False),
        "customers_first": solve(inst, "customers_first", improve=False, reference=False),
        "dependency_aware": solve(inst, "dependency_aware", improve=True, reference=len(tasks) <= 7),
    }
    mid = customer_group_times(inst, policies["dependency_aware"], leaf)[0]
    kwargs = dict(source="Observed damage set (Feb-2025 records on this circuit)", p_affected=None,
                  low_h=mid * LABOR_LOW, mid_h=mid, high_h=mid * LABOR_HIGH)
    return _finish(kwargs, inst, policies, leaf, data, scn)


def _simulated(scn: Scenario, data: DataBundle, sets: DamageSets, circuit_id: str, leaf: str | None) -> RestorationEstimate:
    draws = []
    for k in range(scn.n_draws):
        seed = scn.seed * 1000 + k
        tasks = sets.simulated(circuit_id, scn.severity, seed, scn.access_difficulty, scn.labor_multiplier)
        inst = sets.instance(circuit_id, tasks, scn.crews, scn.crew_start_delay_h)
        res = solve(inst, "dependency_aware", improve=False, reference=False)
        service, _, _, blocking = customer_group_times(inst, res, leaf)
        draws.append((inst, service, blocking is not None))
    affected = [(inst, s) for inst, s, a in draws if a]
    p_aff = float(np.mean([a for *_, a in draws])) if draws else 0.0
    n_mean = float(np.mean([len(inst.tasks) for inst, *_ in draws])) if draws else 0.0
    summary = {"n_draws": scn.n_draws, "p_affected": p_aff, "tasks_mean": n_mean}
    if not affected:
        inst = draws[0][0] if draws else sets.instance(circuit_id, [], scn.crews, scn.crew_start_delay_h)
        policies = {"dependency_aware": solve(inst, "dependency_aware", improve=False, reference=False)}
        kwargs = dict(source=f"Simulated damage sets ({scn.n_draws} seeded draws at severity {scn.severity:g}×)",
                      p_affected=p_aff, low_h=0.0, mid_h=0.0, high_h=0.0)
        est = _finish(kwargs, inst, policies, leaf, data, scn)
        est.affected = False
        est.draws = summary
        _narrate(est, data, scn, leaf)
        return est
    times = np.array([s for _, s in affected])
    p10, p50, p90 = (float(np.percentile(times, q)) for q in (10, 50, 90))
    # display the draw whose service time is closest to the median, with the full policy comparison
    inst = min(affected, key=lambda d: abs(d[1] - p50))[0]
    policies = {
        "fifo": solve(inst, "fifo", improve=False, reference=False),
        "customers_first": solve(inst, "customers_first", improve=False, reference=False),
        "dependency_aware": solve(inst, "dependency_aware", improve=True, reference=len(inst.tasks) <= 7),
    }
    summary.update({"service_p10": p10, "service_p50": p50, "service_p90": p90, "affected_draws": len(affected)})
    kwargs = dict(source=f"Simulated damage sets ({scn.n_draws} seeded draws at severity {scn.severity:g}×)",
                  p_affected=p_aff, low_h=p10 * LABOR_LOW, mid_h=p50, high_h=p90 * LABOR_HIGH)
    est = _finish(kwargs, inst, policies, leaf, data, scn)
    est.draws = summary
    return est


def _narrate(est: RestorationEstimate, data: DataBundle, scn: Scenario, leaf: str | None) -> None:
    delay = est.crew_start_delay_h
    if leaf is None:
        est.narrative = ("No upstream path is on file for this premise, so a restoration time cannot be placed on the "
                         "network. The circuit-wide schedule is shown instead.")
        est.affected = False
        return
    if not est.affected:
        if est.p_affected is not None:
            est.narrative = (f"In {100 * (1 - est.p_affected):.0f}% of {scn.n_draws} simulated storms of this severity no damage "
                             f"landed on your upstream path. When it does, the schedule below applies.")
        else:
            est.narrative = ("In the Feb-2025 damage set none of the reported damage sat on your upstream path, so this "
                             "scenario would not have interrupted your service.")
        return
    local = est.local_repair_done_h
    svc = est.service_restored_h
    parts = [f"With {est.crews} crew{'s' if est.crews != 1 else ''} on your circuit and {est.n_tasks_circuit} repair tasks "
             f"({est.n_tasks_on_path} of them on your own upstream path across {est.n_zones_damaged_on_path} zone"
             f"{'s' if est.n_zones_damaged_on_path != 1 else ''}), the dependency-aware schedule restores your service about "
             f"{fmt_hours(svc + delay)} after crews start."]
    if local is not None and est.local_zone != est.blocking_zone and svc - local > 0.5:
        parts.append(f"The damage nearest you ({zone_label(data, est.local_zone)}) is repaired at hour {local + delay:.0f}, "
                     f"but your lights come back only at hour {svc + delay:.0f}, when {est.blocking_zone_label} is repaired: "
                     f"a repair being finished is not the same as service being restored.")
    elif local is not None:
        parts.append(f"The last repair on your path is in {est.blocking_zone_label} at hour {svc + delay:.0f}.")
    if est.p_affected is not None:
        parts.append(f"Across {scn.n_draws} simulated storms you were affected in {100 * est.p_affected:.0f}% of them.")
    if delay:
        parts.append(f"Crews are assumed to start {delay:.0f} h after the storm ends.")
    est.narrative = " ".join(parts)
    est.what_moves_it = [
        f"Crews: each extra crew on the circuit shortens the schedule (currently {est.crews}).",
        ("Access: difficult access is adding 0.5 h × ILM to every task." if scn.access_difficulty else
         "Access: an ice or flood access problem would add 0.5 h × ILM to every task."),
        "Labor uncertainty: the low end assumes repairs run 25% faster than the workbook averages, the high end 50% slower.",
        "Upstream damage: anything new reported between you and the substation extends your time, even if your own zone is already fixed.",
    ]
