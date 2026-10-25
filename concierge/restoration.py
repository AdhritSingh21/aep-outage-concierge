"""Restoration-time estimate for one customer under a Scenario (Simulated output built on Observed damage/topology).

The range is a *range*, not a promise. For the Observed damage set all three points use the dependency-aware
schedule and differ only in the labor assumption: low = workbook hours × 0.75, mid = × 1.0, high = × 1.5 (every task
scales by the same factor and the ordering depends only on weight/duration ratios, so the schedule scales exactly).
For simulated damage sets low / mid / high are the 10th / 50th / 90th percentile service-restoration times over the
seeded draws in which the customer is affected; the schedule shown is the draw closest to the median.

Time convention: every schedule is solved with crews starting at hour 0 ("hours after crews start"). The scenario's
crew start delay is kept separately and added exactly once, at display time (``total()``), so the card, the chain, the
messages, the alert thresholds and the Operations view all agree.

States: ``affected`` (damage on the path), ``not_affected`` (a path is on file but this damage set misses it; a
secondary Simulated range is attached in ``alt``), ``unknown`` (no upstream path on file: nothing can be placed on
the network).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import config
from .data import DataBundle
from .scenario import Scenario
from .scenarios import DamageSets
from .scheduling import Instance, ScheduleResult, SolverStatus, solve

LABOR_LOW, LABOR_HIGH = 0.75, 1.5
STATE_AFFECTED, STATE_NOT_AFFECTED, STATE_UNKNOWN = "affected", "not_affected", "unknown"


@dataclass
class RestorationEstimate:
    source: str                         # "Observed damage set (Feb-2025)" | "Simulated damage sets"
    affected: bool
    p_affected: float | None            # simulated only
    low_h: float                        # hours after crews start (work time); use total() for time after the storm
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
    state: str = ""                     # affected | not_affected | unknown
    alt: dict = field(default_factory=dict)    # secondary Simulated range when the Observed set misses the customer
    range_basis: str = ""

    def __post_init__(self):
        if not self.state:
            self.state = STATE_AFFECTED if self.affected else STATE_NOT_AFFECTED
        if not self.range_basis:
            self.range_basis = ("10th–90th percentile of the simulated draws" if self.p_affected is not None
                                else "labor × 0.75 (low) / × 1.5 (high) around the dependency-aware schedule (L8)")

    # -- time after the storm ends (delay added exactly once)
    def total(self, h: float | None) -> float | None:
        return None if h is None else h + self.crew_start_delay_h

    @property
    def low_total(self) -> float:
        return self.low_h + self.crew_start_delay_h

    @property
    def mid_total(self) -> float:
        return self.mid_h + self.crew_start_delay_h

    @property
    def high_total(self) -> float:
        return self.high_h + self.crew_start_delay_h

    @property
    def clock_label(self) -> str:
        return "after crews start" if self.crew_start_delay_h <= 0 else "after the storm ends"

    @property
    def window_text(self) -> str:
        if self.state == STATE_AFFECTED:
            return fmt_range(self.low_total, self.high_total)
        if self.state == STATE_UNKNOWN:
            return "cannot be placed on the network"
        return "no damage on your line in this damage set"

    @property
    def alt_window_text(self) -> str | None:
        """Secondary Simulated range (when the Observed set misses the customer)."""
        if not self.alt or self.alt.get("affected_draws", 0) == 0:
            return None
        return fmt_range(self.alt["low_h"] + self.crew_start_delay_h, self.alt["high_h"] + self.crew_start_delay_h)


# ----------------------------------------------------------------------------- formatting
def fmt_hours(h: float | None, unit: str | None = None) -> str:
    """Hours as plain text: '< 1 h', '19 h', '2.3 days'. ``unit`` forces 'h' or 'days' so a range keeps one unit."""
    if h is None or h != h:
        return "—"
    if unit == "days" or (unit is None and h >= 48):
        return f"{h / 24:.1f} days"
    if h < 1:
        return "< 1 h"
    return f"{h:.0f} h"


def fmt_range(lo: float, hi: float) -> str:
    """A range in one unit: '8 – 16 h', '< 1 – 3 h', '2.1 – 3.2 days'."""
    if lo != lo or hi != hi:
        return "—"
    if hi >= 48:
        return f"{lo / 24:.1f} – {hi / 24:.1f} days"
    lo_txt = "< 1" if lo < 1 else f"{lo:.0f}"
    hi_txt = "< 1" if hi < 1 else f"{hi:.0f}"
    if lo_txt == hi_txt:
        return f"about {hi_txt} h" if hi >= 1 else "under 1 h"
    return f"{lo_txt} – {hi_txt} h"


def fmt_between(lo: float, hi: float) -> str:
    """'between 8 h and 16 h' / 'between 2.1 and 3.2 days' for sentences."""
    if hi >= 48:
        return f"between {lo / 24:.1f} and {hi / 24:.1f} days"
    return f"between {fmt_hours(lo, 'h')} and {fmt_hours(hi, 'h')}"


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


def _priority_weight(scn: Scenario) -> float:
    return config.PRIORITY_WEIGHT_CUSTOMERS if scn.prioritize_critical else 0.0


def _solve_policies(inst: Instance) -> dict[str, ScheduleResult]:
    return {
        "fifo": solve(inst, "fifo", improve=False, reference=False),
        "customers_first": solve(inst, "customers_first", improve=False, reference=False),
        "dependency_aware": solve(inst, "dependency_aware", improve=True, reference=len(inst.tasks) <= 7,
                                  max_evaluations=config.SWAP_EVALUATION_CAP),
    }


# ----------------------------------------------------------------------------- estimate
def estimate(scn: Scenario, data: DataBundle, sets: DamageSets | None = None) -> RestorationEstimate:
    sets = sets or DamageSets(data)
    cust = data.premise(scn.premise_nu)
    circuit_id = str(cust["circuit_id"])
    leaf = cust["leaf_zone_id"] if isinstance(cust.get("leaf_zone_id"), str) else None
    if leaf is not None and leaf not in data.zones.index:
        leaf = None   # path on file but its zones are missing from the zone table: treat as unknown (guarded, no crash)
    if scn.damage_source == "historical":
        est = _historical(scn, data, sets, circuit_id, leaf)
    else:
        est = _simulated(scn, data, sets, circuit_id, leaf)
    _narrate(est, data, scn, leaf)
    return est


def _finish(est_kwargs: dict, inst: Instance, res_by_policy: dict[str, ScheduleResult], leaf: str | None,
            data: DataBundle, scn: Scenario) -> RestorationEstimate:
    res = res_by_policy["dependency_aware"]
    service, local_t, local_z, blocking = customer_group_times(inst, res, leaf)
    chain = set(inst.chain.get(leaf, [])) if leaf else set()
    on_path = [t for t in inst.tasks if t.zone_id in chain]
    affected = bool(leaf) and blocking is not None
    return RestorationEstimate(
        **est_kwargs, affected=affected, crew_start_delay_h=scn.crew_start_delay_h,
        service_restored_h=service, local_repair_done_h=local_t, local_zone=local_z, blocking_zone=blocking,
        blocking_zone_label=zone_label(data, blocking), n_tasks_circuit=len(inst.tasks), n_tasks_on_path=len(on_path),
        n_zones_damaged_on_path=len({t.zone_id for t in on_path}), affected_customers_circuit=inst.affected_customers,
        crews=scn.crews, policies=res_by_policy, instance=inst, status=res.status,
        state=STATE_UNKNOWN if leaf is None else (STATE_AFFECTED if affected else STATE_NOT_AFFECTED),
    )


def _historical(scn: Scenario, data: DataBundle, sets: DamageSets, circuit_id: str, leaf: str | None) -> RestorationEstimate:
    tasks = sets.historical(circuit_id, scn.access_difficulty, scn.labor_multiplier)
    inst = sets.instance(circuit_id, tasks, scn.crews, 0.0, priority_weight=_priority_weight(scn))
    policies = _solve_policies(inst)
    mid = customer_group_times(inst, policies["dependency_aware"], leaf)[0]
    kwargs = dict(source="Observed damage set (Feb-2025 records on this circuit)", p_affected=None,
                  low_h=mid * LABOR_LOW, mid_h=mid, high_h=mid * LABOR_HIGH)
    est = _finish(kwargs, inst, policies, leaf, data, scn)
    if est.state == STATE_NOT_AFFECTED:
        est.alt = _draw_summary(scn, sets, circuit_id, leaf)
    return est


def _draw_summary(scn: Scenario, sets: DamageSets, circuit_id: str, leaf: str | None) -> dict:
    """Service times over the seeded simulated draws (Horn ordering only: fast, deterministic)."""
    times, affected_flags, n_tasks = [], [], []
    for k in range(scn.n_draws):
        seed = scn.seed * 1000 + k
        tasks = sets.simulated(circuit_id, scn.severity, seed, scn.access_difficulty, scn.labor_multiplier)
        inst = sets.instance(circuit_id, tasks, scn.crews, 0.0, priority_weight=_priority_weight(scn))
        res = solve(inst, "dependency_aware", improve=False, reference=False)
        service, _, _, blocking = customer_group_times(inst, res, leaf)
        affected_flags.append(blocking is not None)
        n_tasks.append(len(tasks))
        if blocking is not None:
            times.append(service)
    out = {"n_draws": scn.n_draws, "severity": scn.severity, "p_affected": float(np.mean(affected_flags)) if affected_flags else 0.0,
           "tasks_mean": float(np.mean(n_tasks)) if n_tasks else 0.0, "affected_draws": len(times)}
    if times:
        p10, p50, p90 = (float(np.percentile(times, q)) for q in (10, 50, 90))
        out.update({"low_h": p10, "mid_h": p50, "high_h": p90, "service_p10": p10, "service_p50": p50, "service_p90": p90})
    return out


def _simulated(scn: Scenario, data: DataBundle, sets: DamageSets, circuit_id: str, leaf: str | None) -> RestorationEstimate:
    draws = []
    for k in range(scn.n_draws):
        seed = scn.seed * 1000 + k
        tasks = sets.simulated(circuit_id, scn.severity, seed, scn.access_difficulty, scn.labor_multiplier)
        inst = sets.instance(circuit_id, tasks, scn.crews, 0.0, priority_weight=_priority_weight(scn))
        res = solve(inst, "dependency_aware", improve=False, reference=False)
        service, _, _, blocking = customer_group_times(inst, res, leaf)
        draws.append((inst, service, blocking is not None))
    affected = [(inst, s) for inst, s, a in draws if a]
    p_aff = float(np.mean([a for *_, a in draws])) if draws else 0.0
    n_mean = float(np.mean([len(inst.tasks) for inst, *_ in draws])) if draws else 0.0
    source = f"Simulated damage sets ({scn.n_draws} seeded draws at severity {scn.severity:g}×)"
    summary = {"n_draws": scn.n_draws, "severity": scn.severity, "p_affected": p_aff, "tasks_mean": n_mean, "affected_draws": len(affected)}
    if not affected:
        inst = draws[0][0] if draws else sets.instance(circuit_id, [], scn.crews, 0.0)
        policies = {"dependency_aware": solve(inst, "dependency_aware", improve=False, reference=False)}
        kwargs = dict(source=source, p_affected=p_aff, low_h=0.0, mid_h=0.0, high_h=0.0)
        est = _finish(kwargs, inst, policies, leaf, data, scn)
        if est.state == STATE_AFFECTED:   # cannot happen (no affected draw), kept for safety
            est.state = STATE_NOT_AFFECTED
        est.affected = False
        est.draws = summary
        return est
    times = np.array([s for _, s in affected])
    p10, p50, p90 = (float(np.percentile(times, q)) for q in (10, 50, 90))
    # display the draw whose service time is closest to the median, with the full policy comparison
    inst = min(affected, key=lambda d: abs(d[1] - p50))[0]
    policies = _solve_policies(inst)
    summary.update({"service_p10": p10, "service_p50": p50, "service_p90": p90})
    kwargs = dict(source=source, p_affected=p_aff, low_h=p10, mid_h=p50, high_h=p90)
    est = _finish(kwargs, inst, policies, leaf, data, scn)
    summary["displayed_service_h"] = est.service_restored_h
    est.draws = summary
    return est


# ----------------------------------------------------------------------------- sensitivity ("what moves this estimate")
def _resolve_mid(scn: Scenario, sets: DamageSets, circuit_id: str, leaf: str | None, crews: int, access: bool) -> float | None:
    """Midpoint (hours after crews start) for one variant of the Observed scenario, Horn ordering only (fast)."""
    tasks = sets.historical(circuit_id, access, scn.labor_multiplier)
    inst = sets.instance(circuit_id, tasks, crews, 0.0, priority_weight=_priority_weight(scn))
    res = solve(inst, "dependency_aware", improve=False, reference=False)
    svc, _, _, blocking = customer_group_times(inst, res, leaf)
    return None if blocking is None else float(svc)


def what_moves(est: RestorationEstimate, scn: Scenario, sets: DamageSets, circuit_id: str, leaf: str | None) -> list[str]:
    """Ranked, quantified sensitivities (Simulated; Horn-order approximation for the crew and access variants)."""
    if est.state != STATE_AFFECTED:
        return []
    base = est.mid_total
    items: list[tuple[float, str]] = []
    if est.p_affected is None:   # Observed set: cheap re-solves
        more = max(1, scn.crews * 2)
        m2 = _resolve_mid(scn, sets, circuit_id, leaf, more, scn.access_difficulty)
        if m2 is not None:
            d = (m2 + scn.crew_start_delay_h) - base
            items.append((abs(d), f"Crews {scn.crews} → {more}: about {fmt_hours(m2 + scn.crew_start_delay_h)} "
                                  f"({'no change' if abs(d) < 0.5 else f'{d:+.0f} h'}); more crews stop helping once every zone on your path has a crew."))
        ma = _resolve_mid(scn, sets, circuit_id, leaf, scn.crews, not scn.access_difficulty)
        if ma is not None:
            d = (ma + scn.crew_start_delay_h) - base
            label = "Access back to normal" if scn.access_difficulty else "Difficult access (ice / flooding)"
            items.append((abs(d), f"{label}: about {fmt_hours(ma + scn.crew_start_delay_h)} ({'no change' if abs(d) < 0.5 else f'{d:+.0f} h'}); "
                                  f"the workbook adds 0.5 h × ILM of indirect labor to every task."))
        hi = est.high_total
        items.append((abs(hi - base), f"Slower repairs (workbook hours × 1.5): about {fmt_hours(hi)} ({hi - base:+.0f} h); "
                                      f"faster repairs (× 0.75): about {fmt_hours(est.low_total)}."))
    else:
        items.append((abs(est.high_total - base), f"Storm-to-storm variation: {fmt_hours(est.low_total)} to {fmt_hours(est.high_total)} "
                                                  f"across the {est.draws.get('n_draws', scn.n_draws)} simulated draws (10th to 90th percentile)."))
    if scn.crew_start_delay_h:
        items.append((scn.crew_start_delay_h, f"Crew start delay: {scn.crew_start_delay_h:g} h of the estimate is waiting for crews to be released after the storm."))
    items.sort(key=lambda x: -x[0])
    out = [t for _, t in items]
    out.append("New upstream damage: anything reported between you and the substation extends your time, even if your own zone is already fixed.")
    return out


# ----------------------------------------------------------------------------- narrative
def _narrate(est: RestorationEstimate, data: DataBundle, scn: Scenario, leaf: str | None) -> None:
    if est.state == STATE_UNKNOWN:
        est.narrative = ("No upstream path is on file for this premise, so a restoration time cannot be placed on the "
                         "network. The circuit-wide schedule is shown in the Operations view instead, and a person would "
                         "review before any time is sent.")
        est.affected = False
        return
    if est.state == STATE_NOT_AFFECTED:
        if est.p_affected is not None:
            est.narrative = (f"In {100 * (1 - est.p_affected):.0f}% of {scn.n_draws} simulated storms of this severity no damage "
                             f"landed on your upstream path, so no restoration time applies in this damage set.")
        else:
            txt = ("In the Feb-2025 damage set none of the reported damage sat on your upstream path, so a replay of that "
                   "storm would not have interrupted your service.")
            if est.alt:
                if est.alt.get("affected_draws", 0):
                    txt += (f" In {est.alt['n_draws']} simulated storms of this severity you lost power in "
                            f"{100 * est.alt['p_affected']:.0f}% of them; when you did, service came back "
                            f"{fmt_between(est.alt['low_h'] + est.crew_start_delay_h, est.alt['high_h'] + est.crew_start_delay_h)} {est.clock_label}.")
                else:
                    txt += (f" In {est.alt['n_draws']} simulated storms of this severity your upstream path was never hit.")
            est.narrative = txt
        return
    local = est.local_repair_done_h
    svc = est.service_restored_h
    delay = est.crew_start_delay_h
    parts = [f"With {est.crews} crew{'s' if est.crews != 1 else ''} on your circuit and {est.n_tasks_circuit} repair tasks "
             f"({est.n_tasks_on_path} of them on your own upstream path across {est.n_zones_damaged_on_path} zone"
             f"{'s' if est.n_zones_damaged_on_path != 1 else ''}), the dependency-aware schedule restores your service about "
             f"{fmt_hours(est.mid_total)} {est.clock_label}."]
    if est.p_affected is not None:
        parts.append(f"That is the median of {est.draws.get('affected_draws', 0)} simulated storms in which you were affected "
                     f"({100 * est.p_affected:.0f}% of {scn.n_draws}); the schedule shown is the draw closest to the median "
                     f"(service back at about {fmt_hours(svc + delay)}).")
    if local is not None and est.local_zone != est.blocking_zone and svc - local > 0.5:
        parts.append(f"The damage nearest you ({zone_label(data, est.local_zone)}) is repaired at hour {local + delay:.0f}, "
                     f"but your lights come back only at hour {svc + delay:.0f}, when {est.blocking_zone_label} is repaired: "
                     f"a repair being finished is not the same as service being restored.")
    elif local is not None:
        parts.append(f"The last repair on your path is in {est.blocking_zone_label} at hour {svc + delay:.0f}"
                     + ("; all of the damage on your path is in that one zone, so repair and service return together." if est.n_zones_damaged_on_path == 1 else "."))
    if delay:
        parts.append(f"Crews are assumed to start {delay:g} h after the storm ends; that wait is included once in every time shown.")
    est.narrative = " ".join(parts)
