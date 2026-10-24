"""Crew scheduling on a circuit's protective-zone tree.

Model
-----
* Tasks: one per damage record (hours from labor.py), each inside a protective zone.
* Crews: K identical crews, one task at a time, no preemption, all available from ``start_delay_h``.
* Customer groups: one per leaf zone (every premise belongs to exactly one group, so nobody is counted twice).
* **Service restoration** of a group = the latest repair-completion time over the damaged zones on its upstream
  chain (0 if none is damaged). A crew finishing the damage nearest a customer does not restore the customer while a
  zone further upstream is still damaged. This is the distinction between *repair completed* and *service restored*.
* Objective: customer-outage hours = Σ_groups n_g · T_g (life-support customers are reported separately, not weighted).

Policies (all evaluated on the identical Instance):
* ``fifo``             – tasks in report order, earliest-free crew (what a simple dispatcher does).
* ``customers_first``  – tasks ranked by customers downstream of the task's zone ÷ task hours (a customer-benefit
                         heuristic that ignores the AND-coupling between zones on a path).
* ``dependency_aware`` – Horn's tree-merge algorithm on the damaged-zone tree with weights = customers whose deepest
                         damaged zone is that zone (optimal for one crew and upstream-first precedence), list-scheduled
                         on K crews, then an adjacent-swap improvement pass evaluated with the exact objective.

A brute-force reference (best of all task orders, exact for K = 1) is available for tiny instances and reported in
``SolverStatus`` so the heuristic's gap is visible rather than claimed.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

POLICIES = ("fifo", "customers_first", "dependency_aware")
POLICY_LABELS = {"fifo": "FIFO (report order)", "customers_first": "Customers-per-hour heuristic",
                 "dependency_aware": "Dependency-aware (ours)"}
BRUTE_FORCE_MAX_TASKS = 7


# ----------------------------------------------------------------------------- data classes
@dataclass(frozen=True)
class Task:
    task_id: str
    zone_id: str
    hours: float                 # clock hours for one crew (person-hours ÷ crew size)
    order: int
    asset_type: str = ""
    damage_type: str = ""
    access_issue: bool = False
    source: str = "Observed"
    person_hours: float = 0.0    # workbook hours × ILM (before dividing by crew size)


@dataclass
class Instance:
    circuit_id: str
    tasks: list[Task]
    parent: dict[str, str | None]            # zone → parent zone for every zone of the circuit
    group_customers: dict[str, int]          # leaf zone → customers attached directly
    group_life_support: dict[str, int] = field(default_factory=dict)
    zone_customers_downstream: dict[str, int] = field(default_factory=dict)
    crews: int = 2
    start_delay_h: float = 0.0

    def __post_init__(self):
        self.task_by_id = {t.task_id: t for t in self.tasks}
        self.damaged_zones = {t.zone_id for t in self.tasks}
        self.chain: dict[str, list[str]] = {}
        for z in set(self.parent) | set(self.group_customers) | self.damaged_zones:
            self.chain[z] = self._chain(z)
        # customer groups: leaf zone → (n, n_ls, damaged zones on its chain)
        self.groups: list[tuple[str, int, int, tuple[str, ...]]] = []
        for g, n in self.group_customers.items():
            if n <= 0:
                continue
            blocking = tuple(z for z in self.chain.get(g, [g]) if z in self.damaged_zones)
            self.groups.append((g, int(n), int(self.group_life_support.get(g, 0)), blocking))
        self.affected_customers = sum(n for _, n, _, b in self.groups if b)
        self.affected_life_support = sum(ls for _, _, ls, b in self.groups if b)
        self.total_hours = sum(t.hours for t in self.tasks)
        if not self.zone_customers_downstream:
            self.zone_customers_downstream = self._downstream_counts()

    def _chain(self, z: str) -> list[str]:
        out, seen, cur = [], set(), z
        while isinstance(cur, str) and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = self.parent.get(cur)
        return out

    def _downstream_counts(self) -> dict[str, int]:
        down: dict[str, int] = {}
        for g, n in self.group_customers.items():
            for z in self.chain.get(g, [g]):
                down[z] = down.get(z, 0) + n
        return down

    def deepest_damaged_weights(self) -> dict[str, int]:
        """Customers whose deepest damaged zone (first damaged zone walking upward) is each damaged zone."""
        w: dict[str, int] = {z: 0 for z in self.damaged_zones}
        for g, n, _, blocking in self.groups:
            if blocking:
                w[blocking[0]] += n
        return w


@dataclass
class Assignment:
    task_id: str
    crew: int
    start: float
    end: float


@dataclass
class Metrics:
    customer_hours: float
    affected_customers: int
    restored_customers: int
    makespan_h: float
    t50_h: float
    t90_h: float
    t100_h: float
    life_support_hours: float
    life_support_t100_h: float
    total_crew_hours: float


@dataclass
class SolverStatus:
    policy: str
    n_tasks: int
    crews: int
    runtime_s: float
    order_source: str
    improvement_pass: bool = False
    improvement_iterations: int = 0
    improvement_pct: float = 0.0
    reference_value: float | None = None       # best-of-all-orders objective (exact optimum for 1 crew)
    reference_kind: str | None = None
    gap_pct: float | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class ScheduleResult:
    policy: str
    order: list[str]
    assignments: list[Assignment]
    completion: dict[str, float]
    zone_done: dict[str, float]
    group_restored: dict[str, float]
    metrics: Metrics
    status: SolverStatus

    def as_frame(self, inst: Instance) -> pd.DataFrame:
        rows = []
        for a in self.assignments:
            t = inst.task_by_id[a.task_id]
            rows.append({"task_id": a.task_id, "zone_id": t.zone_id, "asset_type": t.asset_type, "damage_type": t.damage_type,
                         "hours": round(t.hours, 2), "crew": a.crew + 1, "start_h": round(a.start, 2), "end_h": round(a.end, 2)})
        return pd.DataFrame(rows).sort_values(["start_h", "crew"]).reset_index(drop=True)


# ----------------------------------------------------------------------------- evaluation
def list_schedule(inst: Instance, order: list[str]) -> list[Assignment]:
    """Earliest-free-crew list scheduling of tasks in the given order (no inserted idle time)."""
    free = np.full(inst.crews, float(inst.start_delay_h))
    out = []
    for tid in order:
        k = int(np.argmin(free))
        start = float(free[k])
        end = start + inst.task_by_id[tid].hours
        free[k] = end
        out.append(Assignment(tid, k, start, end))
    return out


def evaluate(inst: Instance, completion: dict[str, float]) -> tuple[dict[str, float], dict[str, float], Metrics]:
    zone_done: dict[str, float] = {}
    for tid, c in completion.items():
        z = inst.task_by_id[tid].zone_id
        zone_done[z] = max(zone_done.get(z, 0.0), c)
    group_restored: dict[str, float] = {}
    rows = []
    for g, n, ls, blocking in inst.groups:
        T = max((zone_done[z] for z in blocking), default=0.0)
        group_restored[g] = T
        if blocking:
            rows.append((T, n, ls))
    customer_hours = float(sum(T * n for T, n, _ in rows))
    ls_hours = float(sum(T * ls for T, _, ls in rows))
    affected = int(sum(n for _, n, _ in rows))
    rows.sort()
    cum = 0
    t50 = t90 = t100 = 0.0
    for T, n, _ in rows:
        cum += n
        if cum >= 0.5 * affected and t50 == 0.0:
            t50 = T
        if cum >= 0.9 * affected and t90 == 0.0:
            t90 = T
        t100 = T
    ls_t100 = max((T for T, _, ls in rows if ls > 0), default=0.0)
    makespan = max(completion.values(), default=0.0)
    m = Metrics(customer_hours, affected, affected, makespan, t50, t90, t100, ls_hours, ls_t100, inst.total_hours)
    return zone_done, group_restored, m


def objective(inst: Instance, order: list[str]) -> float:
    completion = {a.task_id: a.end for a in list_schedule(inst, order)}
    return evaluate(inst, completion)[2].customer_hours


# ----------------------------------------------------------------------------- orderings
def order_fifo(inst: Instance) -> list[str]:
    return [t.task_id for t in sorted(inst.tasks, key=lambda t: (t.order, t.task_id))]


def order_customers_first(inst: Instance) -> list[str]:
    down = inst.zone_customers_downstream
    return [t.task_id for t in sorted(inst.tasks, key=lambda t: (-(down.get(t.zone_id, 0) / max(t.hours, 1e-9)), t.order, t.task_id))]


def _damaged_tree(inst: Instance) -> dict[str, str | None]:
    """Parent map restricted to damaged zones (nearest damaged ancestor), None for roots."""
    parent: dict[str, str | None] = {}
    for z in inst.damaged_zones:
        anc = inst.chain.get(z, [z])[1:]
        parent[z] = next((a for a in anc if a in inst.damaged_zones), None)
    return parent


def order_dependency_aware(inst: Instance) -> list[str]:
    """Horn (1972) tree-merge for 1|out-tree|Σ w_j C_j on the damaged-zone tree.

    Nodes are damaged zones (tasks in one zone form one composite job, kept in report order); weight = customers
    whose deepest damaged zone is that node. Non-root node with the largest w/p ratio is merged into its parent
    (scheduled right after it) until only the artificial root remains; its sequence is the order.
    """
    weights = inst.deepest_damaged_weights()
    parent = _damaged_tree(inst)
    ROOT = "__root__"
    seq: dict[str, list[str]] = {ROOT: []}
    w: dict[str, float] = {ROOT: 0.0}
    p: dict[str, float] = {ROOT: 0.0}
    par: dict[str, str] = {}
    children: dict[str, set[str]] = {ROOT: set()}
    tasks_by_zone: dict[str, list[Task]] = {}
    for t in sorted(inst.tasks, key=lambda t: (t.order, t.task_id)):
        tasks_by_zone.setdefault(t.zone_id, []).append(t)
    for z, ts in tasks_by_zone.items():
        seq[z] = [t.task_id for t in ts]
        w[z] = float(weights.get(z, 0))
        p[z] = float(sum(t.hours for t in ts))
        par[z] = parent[z] if parent[z] is not None else ROOT
        children.setdefault(z, set())
    for z, u in par.items():
        children.setdefault(u, set()).add(z)
    alive = set(par)
    while alive:
        # non-root node with maximum ratio w/p (ties: shorter first, then id for determinism)
        v = max(alive, key=lambda z: (w[z] / max(p[z], 1e-9), -p[z], z))
        u = par[v]
        seq[u] = seq[u] + seq[v]
        w[u] += w[v]
        p[u] += p[v]
        for c in children.get(v, ()):
            par[c] = u
            children[u].add(c)
        children[u].discard(v)
        alive.discard(v)
    return seq[ROOT]


def improve_by_swaps(inst: Instance, order: list[str], max_passes: int = 25, time_budget_s: float = 1.0) -> tuple[list[str], int, float]:
    """Adjacent-swap descent on the exact objective. Returns (order, iterations, improvement fraction)."""
    best = objective(inst, order)
    start_val = best
    t0 = time.perf_counter()
    iters = 0
    for _ in range(max_passes):
        improved = False
        for i in range(len(order) - 1):
            cand = order[:]
            cand[i], cand[i + 1] = cand[i + 1], cand[i]
            val = objective(inst, cand)
            iters += 1
            if val < best - 1e-9:
                order, best, improved = cand, val, True
            if time.perf_counter() - t0 > time_budget_s:
                break
        if not improved or time.perf_counter() - t0 > time_budget_s:
            break
    return order, iters, (start_val - best) / start_val if start_val > 0 else 0.0


def brute_force_reference(inst: Instance) -> float | None:
    """Best objective over all task orders (list-scheduled). Exact optimum for one crew; a strong reference otherwise."""
    if len(inst.tasks) > BRUTE_FORCE_MAX_TASKS:
        return None
    ids = [t.task_id for t in inst.tasks]
    return min(objective(inst, list(perm)) for perm in itertools.permutations(ids))


# ----------------------------------------------------------------------------- solve
def solve(inst: Instance, policy: str, improve: bool = True, reference: bool = True, time_budget_s: float = 1.0) -> ScheduleResult:
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy}")
    t0 = time.perf_counter()
    status = SolverStatus(policy, len(inst.tasks), inst.crews, 0.0, "")
    if policy == "fifo":
        order, status.order_source = order_fifo(inst), "report order"
    elif policy == "customers_first":
        order, status.order_source = order_customers_first(inst), "customers downstream ÷ task hours"
    else:
        order, status.order_source = order_dependency_aware(inst), "Horn tree-merge on damaged-zone tree (weights = customers whose deepest damaged zone is the node)"
        if improve and len(order) > 1:
            order, iters, gain = improve_by_swaps(inst, order, time_budget_s=time_budget_s)
            status.improvement_pass, status.improvement_iterations, status.improvement_pct = True, iters, round(100 * gain, 2)
        else:
            status.notes.append("Swap-improvement pass not run for this schedule (Horn ordering only).")
    assignments = list_schedule(inst, order)
    completion = {a.task_id: a.end for a in assignments}
    zone_done, group_restored, metrics = evaluate(inst, completion)
    status.runtime_s = round(time.perf_counter() - t0, 4)
    if reference and len(inst.tasks) <= BRUTE_FORCE_MAX_TASKS and len(inst.tasks) > 0:
        ref = brute_force_reference(inst)
        status.reference_value = ref
        status.reference_kind = "exact optimum (1 crew)" if inst.crews == 1 else "best of all task orders (list-scheduled)"
        status.gap_pct = round(100 * (metrics.customer_hours - ref) / ref, 2) if ref and ref > 0 else 0.0
    elif reference:
        status.notes.append(f"No exact reference: {len(inst.tasks)} tasks exceed the brute-force limit of {BRUTE_FORCE_MAX_TASKS}.")
    if inst.crews > 1:
        status.notes.append("With several crews the ordering is a heuristic (list scheduling); optimality is only guaranteed for one crew.")
    return ScheduleResult(policy, order, assignments, completion, zone_done, group_restored, metrics, status)


def compare_policies(inst: Instance, policies: tuple[str, ...] = POLICIES, improve: bool = True, reference: bool = True,
                     time_budget_s: float = 1.0) -> dict[str, ScheduleResult]:
    """Run every policy on the identical instance (same tasks, hours, crews)."""
    return {p: solve(inst, p, improve=improve, reference=reference, time_budget_s=time_budget_s) for p in policies}


def metrics_table(results: dict[str, ScheduleResult]) -> pd.DataFrame:
    rows = []
    base = results.get("fifo")
    for p, r in results.items():
        m = r.metrics
        rows.append({"policy": POLICY_LABELS.get(p, p), "customer_outage_hours": round(m.customer_hours, 1),
                     "vs_fifo_pct": round(100 * (m.customer_hours - base.metrics.customer_hours) / base.metrics.customer_hours, 1) if base and base.metrics.customer_hours else 0.0,
                     "affected_customers": m.affected_customers,
                     "avg_hours_per_affected_customer": round(m.customer_hours / m.affected_customers, 1) if m.affected_customers else 0.0,
                     "t50_h": round(m.t50_h, 1), "t90_h": round(m.t90_h, 1),
                     "all_restored_h": round(m.t100_h, 1), "life_support_restored_h": round(m.life_support_t100_h, 1),
                     "crew_clock_hours": round(m.total_crew_hours, 1)})
    return pd.DataFrame(rows)
