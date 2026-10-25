"""Scale probe: measured runtimes of the data load, the scheduler, the journey and the premise lookup.

Every row this script prints is a wall-clock measurement on the machine it ran on and is labeled
**Measured (this machine)**, except the one projected row (a full swap pass at 10x, which is arithmetic on a measured
per-evaluation cost and is labeled **Projected from Measured**). Where an objective value (customer-outage hours) is
quoted next to a timing it is the Simulated output of the scheduler on the Observed Feb-2025 damage set of the largest
demo circuit, and is shown only so the gain the swap pass buys per second of runtime is visible. Nothing here is a fit
or a forecast.

What is measured
  1. Module imports, then the data load (DataBundle + DamageSets construction).
  2. Largest circuit (most Feb-2025 records) at its calibrated default crews:
       Horn ordering alone; one exact objective evaluation (list schedule + objective); one measured adjacent-swap
       pass; the solver at the app's setting (swap pass capped at config.SWAP_EVALUATION_CAP evaluations with the 1 s
       time budget as a ceiling); the solver at the benchmark's setting (time box only, the budget read from
       results/benchmark_headline.json, 0.5 s when that file is absent); the swap descent run to convergence.
  3. A synthetic 10x instance: the same circuit's tasks replicated 10 times (deterministic, no randomness) with
       10x the crews: Horn time, one evaluation, projected full pass, the solver at the app's setting.
  4. Simulated-mode journey (concierge.journey.run, damage_source="simulated") at severity 1x and 3x, plus the
       Observed-set journey for reference.
  5. nearest_premise linear scan over every premise in the bundle.

Usage
  python scripts/scale_probe.py --date 2026-10-25 [--repeats 5] [--convergence-cap-s 120] [--out results/scale_probe.md]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import statistics
import sys
import time
import traceback
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LABEL = "Measured (this machine)"
LABEL_PROJECTED = "Projected from Measured (this machine)"


# ----------------------------------------------------------------------------- helpers
def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf8") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown CPU"


def _benchmark_budget_s(default: float = 0.5) -> float:
    """Swap-pass time box the committed benchmark was run with (results/benchmark_headline.json), else the CLI default."""
    import json

    path = ROOT / "results" / "benchmark_headline.json"
    try:
        value = json.loads(path.read_text(encoding="utf8")).get("swap_pass_budget_s")
        return float(value) if value is not None else default
    except (OSError, ValueError, AttributeError):
        return default


def _timed(fn, repeats: int) -> tuple[float, object]:
    """Median wall time over `repeats` calls (seconds) and the last return value."""
    times, out = [], None
    for _ in range(repeats):
        t0 = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times), out


def _ms(s: float) -> str:
    return f"{1000 * s:,.2f} ms"


def _s(s: float) -> str:
    return f"{s:,.2f} s"


def _pct(a: float, b: float) -> float:
    """Percent reduction from a to b."""
    return 100.0 * (a - b) / a if a > 0 else 0.0


# ----------------------------------------------------------------------------- probe
def probe(repeats: int, convergence_cap_s: float, circuit_override: str | None) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    ctx: dict = {}

    # 1. imports, then data load ---------------------------------------------------------------------------------
    t0 = time.perf_counter()
    from concierge import config, journey  # noqa: E402  (imported here so the import cost is measured, separately)
    from concierge.data import DataBundle  # noqa: E402
    from concierge.scenario import Scenario  # noqa: E402
    from concierge.scenarios import DamageSets  # noqa: E402
    from concierge.scheduling import improve_by_swaps, objective, order_dependency_aware, solve  # noqa: E402
    import_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    data = DataBundle()
    sets = DamageSets(data)
    load_s = time.perf_counter() - t0
    cap = getattr(config, "SWAP_EVALUATION_CAP", None)   # the app's deterministic cap on the swap pass (Assumed L9)
    ctx["cap"] = cap

    n_prem, n_zones, n_dmg = len(data.t["customers"]), len(data.zones), len(data.damage)
    ctx.update(bundle="demo bundle (data/demo)" if data.is_demo else "processed organizer extract (data/processed)",
               n_premises=n_prem, n_zones=n_zones, n_damage=n_dmg, n_circuits=len(data.circuits), import_s=import_s)
    rows.append({"measurement": "Data load (DataBundle + DamageSets)", "value": _s(load_s),
                 "detail": f"{ctx['bundle']}; {ctx['n_circuits']} circuits, {n_prem:,} premises, {n_zones:,} zones, "
                           f"{n_dmg:,} damage records (Observed counts); module imports (pandas, pyarrow, concierge) took a "
                           f"further {_s(import_s)}, not included"})

    # 2. largest circuit at default crews -----------------------------------------------------------------------
    if circuit_override:
        circ = circuit_override
    else:
        circ = str(data.t["circuits"].sort_values(["n_damage_ref", "circuit_id"], ascending=[False, True])["circuit_id"].iloc[0])
    tasks = sets.historical(circ)
    crews = sets.default_crews(circ)
    inst = sets.instance(circ, tasks, crews)
    n = len(tasks)
    ctx.update(circuit=circ, n_tasks=n, crews=crews, damaged_zones=len(inst.damaged_zones), groups=len(inst.groups),
               zones_circuit=len(inst.parent), affected=inst.affected_customers)
    who = f"circuit {circ}: {n} tasks (Observed Feb-2025 records), {len(inst.damaged_zones)} damaged zones, {crews} crews (calibrated default)"

    horn_s, horn_order = _timed(lambda: order_dependency_aware(inst), repeats)
    rows.append({"measurement": "Horn ordering alone, largest circuit", "value": _ms(horn_s), "detail": f"{who}; median of {repeats}"})

    eval_s, horn_val = _timed(lambda: objective(inst, horn_order), repeats)
    rows.append({"measurement": "One objective evaluation (list schedule + exact objective)", "value": _ms(eval_s),
                 "detail": f"{n} tasks on {crews} crews, {len(inst.groups)} customer groups; median of {repeats}"})

    t0 = time.perf_counter()
    _, pass_iters, pass_gain = improve_by_swaps(inst, horn_order, max_passes=1, time_budget_s=1e9)
    pass_s = time.perf_counter() - t0
    rows.append({"measurement": "One full adjacent-swap pass (measured)", "value": _s(pass_s),
                 "detail": f"{pass_iters} evaluations ({n} - 1 adjacent pairs); gain over Horn order {100 * pass_gain:.2f}%"})

    def _app_solve(instance):
        """The solver exactly as the app calls it (restoration._solve_policies): swap pass capped at SWAP_EVALUATION_CAP
        evaluations, with the solver's default 1 s time budget left in place as a safety ceiling."""
        return solve(instance, "dependency_aware", improve=True, reference=False, max_evaluations=cap)

    def _stop_reason(status, n_tasks: int) -> str:
        it = status.improvement_iterations
        if cap is not None and it >= cap:
            return f"stopped by the app's cap of {cap:,} evaluations (deterministic: same schedule on every machine)"
        if it % max(n_tasks - 1, 1) == 0:
            return "converged before the cap (a full pass with no improving swap)"
        return (f"stopped by the solver's 1 s time ceiling at {it:,} of the {cap:,} capped evaluations" if cap is not None
                else "stopped by the 1 s time budget")

    t0 = time.perf_counter()
    res_app = _app_solve(inst)
    app_wall_s = time.perf_counter() - t0
    st = res_app.status
    app_gain = _pct(horn_val, res_app.metrics.customer_hours)
    ceiling_hit = cap is not None and st.improvement_iterations < cap and st.improvement_iterations % max(n - 1, 1) != 0
    cap_needs_s = cap * eval_s if cap is not None else None   # projected time to reach the cap at the measured evaluation cost
    ctx.update(app_iters=st.improvement_iterations, app_gain=app_gain, app_passes=st.improvement_iterations / max(n - 1, 1),
               app_stop=_stop_reason(st, n), app_wall_s=app_wall_s, ceiling_hit=ceiling_hit, cap_needs_s=cap_needs_s)
    consequence = (f"; reaching the cap needs about {_s(cap_needs_s)} at the measured evaluation cost (projected), so on this machine "
                   f"the displayed schedule for this circuit is NOT machine-independent" if ceiling_hit else "")
    rows.append({"measurement": f"Solver at the app's setting (Horn + swap pass capped at {cap:,} evaluations, Assumed L9)" if cap is not None
                 else "Solver at the app's setting (Horn + swap pass, solver defaults)", "value": _s(app_wall_s),
                 "detail": f"{st.improvement_iterations:,} evaluations (~{st.improvement_iterations / max(n - 1, 1):.1f} passes); "
                           f"{ctx['app_stop']}{consequence}; gain over Horn order {app_gain:.2f}% "
                           f"({horn_val:,.0f} -> {res_app.metrics.customer_hours:,.0f} customer-outage h, Simulated schedule)"})

    bench_budget_s = _benchmark_budget_s()   # the benchmark's time box (scripts/run_benchmark.py --time-budget, default 0.5 s)
    t0 = time.perf_counter()
    res_default = solve(inst, "dependency_aware", improve=True, reference=False, time_budget_s=bench_budget_s)
    default_wall_s = time.perf_counter() - t0
    st = res_default.status
    default_gain = _pct(horn_val, res_default.metrics.customer_hours)
    ctx.update(default_iters=st.improvement_iterations, default_gain=default_gain, default_runtime=st.runtime_s,
               default_passes=st.improvement_iterations / max(n - 1, 1), bench_budget_s=bench_budget_s)
    rows.append({"measurement": f"Solver at the benchmark's setting (Horn + swap pass under the {bench_budget_s:g} s time box)",
                 "value": _s(default_wall_s),
                 "detail": f"{st.improvement_iterations:,} evaluations (~{st.improvement_iterations / max(n - 1, 1):.1f} passes) inside the "
                           f"{bench_budget_s:g} s time budget (scripts/run_benchmark.py --time-budget, as recorded in "
                           f"results/benchmark_headline.json; machine-dependent by construction); gain over Horn order "
                           f"{default_gain:.2f}% ({horn_val:,.0f} -> {res_default.metrics.customer_hours:,.0f} customer-outage h, Simulated schedule)"})

    t0 = time.perf_counter()
    conv_order, conv_iters, _ = improve_by_swaps(inst, horn_order, max_passes=10**6, time_budget_s=convergence_cap_s)
    conv_s = time.perf_counter() - t0
    conv_val = objective(inst, conv_order)
    converged = conv_s < convergence_cap_s
    conv_gain = _pct(horn_val, conv_val)
    ctx.update(conv_s=conv_s, conv_iters=conv_iters, conv_gain=conv_gain, converged=converged,
               conv_passes=conv_iters / max(n - 1, 1))
    rows.append({"measurement": "Swap descent to convergence (large budget)", "value": _s(conv_s),
                 "detail": f"{conv_iters:,} evaluations (~{conv_iters / max(n - 1, 1):.0f} passes); "
                           f"{'converged (a full pass with no improving swap)' if converged else f'stopped by the {convergence_cap_s:g} s cap, NOT converged'}; "
                           f"gain over Horn order {conv_gain:.2f}% ({horn_val:,.0f} -> {conv_val:,.0f} customer-outage h, Simulated schedule)"})

    # 3. synthetic 10x ---------------------------------------------------------------------------------------------
    factor = 10
    big_tasks = [replace(t, task_id=f"{t.task_id}r{j}", order=j * n + t.order) for j in range(factor) for t in tasks]
    inst10 = sets.instance(circ, big_tasks, factor * crews)
    n10 = len(big_tasks)
    who10 = f"{n10} tasks = {n} x {factor} replicas on the same {len(inst10.damaged_zones)} damaged zones, {factor * crews} crews"
    horn10_s, horn10_order = _timed(lambda: order_dependency_aware(inst10), repeats)
    rows.append({"measurement": "10x synthetic: Horn ordering", "value": _ms(horn10_s), "detail": f"{who10}; median of {repeats}"})
    eval10_s, _ = _timed(lambda: objective(inst10, horn10_order), repeats)
    rows.append({"measurement": "10x synthetic: one objective evaluation", "value": _ms(eval10_s), "detail": f"median of {repeats}"})
    pass10_proj_s = (n10 - 1) * eval10_s
    rows.append({"measurement": "10x synthetic: one full swap pass (projected, not run)", "value": _s(pass10_proj_s),
                 "detail": f"({n10} - 1) evaluations x {_ms(eval10_s)} per evaluation (row above); arithmetic on the measured evaluation cost",
                 "label": LABEL_PROJECTED})
    t0 = time.perf_counter()
    res10 = _app_solve(inst10)
    wall10_s = time.perf_counter() - t0
    stop10 = _stop_reason(res10.status, n10)
    rows.append({"measurement": "10x synthetic: solver at the app's setting", "value": _s(wall10_s),
                 "detail": f"{res10.status.improvement_iterations:,} evaluations (~{res10.status.improvement_iterations / max(n10 - 1, 1):.2f} of a pass); "
                           f"{stop10}; gain over Horn order {res10.status.improvement_pct:.2f}%"})
    ctx.update(n10=n10, crews10=factor * crews, horn_s=horn_s, horn10_s=horn10_s, eval_s=eval_s, eval10_s=eval10_s,
               pass_s=pass_s, pass10_proj_s=pass10_proj_s, iters10=res10.status.improvement_iterations,
               passes10=res10.status.improvement_iterations / max(n10 - 1, 1), wall10_s=wall10_s, stop10=stop10)

    # 4. journey ---------------------------------------------------------------------------------------------------
    c = data.t["customers"]
    cand = c[(c["circuit_id"] == circ) & c["has_path"]]["premise_nu"].astype(str).sort_values()
    premise = cand.iloc[0] if len(cand) else str(c["premise_nu"].iloc[0])
    ctx["premise"] = premise
    base = Scenario(premise_nu=premise, crews=crews)
    journeys = [("Journey, Observed Feb-2025 set (reference)", base),
                ("Journey, Simulated draws at severity 1x", base.with_(damage_source="simulated", severity=1.0)),
                ("Journey, Simulated draws at severity 3x", base.with_(damage_source="simulated", severity=3.0))]
    for name, scn in journeys:
        t0 = time.perf_counter()
        try:
            jr = journey.run(scn, data, sets)
            wall = time.perf_counter() - t0
            est = jr.restoration
            cap_txt = f"capped at {cap:,} evaluations" if cap is not None else "at the solver defaults"
            if scn.damage_source == "simulated":
                d = est.draws or {}
                detail = (f"premise {premise} on circuit {circ}, {crews} crews, {scn.n_draws} seeded draws "
                          f"(mean {d.get('tasks_mean', float('nan')):.0f} tasks per draw, affected in {100 * d.get('p_affected', 0.0):.0f}% of draws); "
                          f"each draw = Horn-only solve, then the displayed draw runs all three policies with the swap pass {cap_txt}; "
                          f"includes risk scoring, sensitivity variants, messages and evidence")
                ctx[f"journey_{scn.severity:g}x_tasks"] = d.get("tasks_mean", float("nan"))
                ctx["n_draws"] = scn.n_draws
            else:
                extra = (f"; plus {scn.n_draws} Horn-only simulated draws because the Observed set leaves this premise untouched"
                         if getattr(est, "alt", None) else "")
                detail = (f"premise {premise} on circuit {circ}, {crews} crews, {est.n_tasks_circuit} Observed tasks; three policies, "
                          f"swap pass {cap_txt}; includes risk scoring, sensitivity variants, messages and evidence{extra}")
            ctx[f"journey_{scn.damage_source}_{scn.severity:g}x_s"] = wall
            rows.append({"measurement": name, "value": _s(wall), "detail": detail})
        except Exception as exc:  # report, never hide
            wall = time.perf_counter() - t0
            rows.append({"measurement": name, "value": "failed", "detail": f"{type(exc).__name__}: {exc} (after {_s(wall)})"})
            ctx.setdefault("errors", []).append(traceback.format_exc())

    # 5. nearest_premise ------------------------------------------------------------------------------------------
    lon0, lat0 = float(c["lon"].mean()), float(c["lat"].mean())   # bundle centroid; coordinates are not printed
    np_s, _ = _timed(lambda: data.nearest_premise(lon0, lat0), repeats)
    ctx["nearest_s"] = np_s
    rows.append({"measurement": "nearest_premise scan", "value": _ms(np_s),
                 "detail": f"linear scan over {n_prem:,} premises from the bundle centroid; median of {repeats}"})
    return rows, ctx


# ----------------------------------------------------------------------------- report
def table(rows: list[dict]) -> str:
    lines = ["| # | Measurement | Value | Detail | Label |", "|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['measurement']} | {r['value']} | {r['detail']} | {r.get('label', LABEL)} |")
    return "\n".join(lines)


def what_changes(ctx: dict) -> str:
    n, n10 = ctx["n_tasks"], ctx["n10"]
    j1 = ctx.get("journey_simulated_1x_s")
    j3 = ctx.get("journey_simulated_3x_s")
    jh = ctx.get("journey_historical_1x_s")
    j_txt = ""
    if j1 is not None and j3 is not None:
        j_txt = (f" Simulated mode runs {ctx.get('n_draws', 0)} Horn-only solves plus one capped solve: {_s(j1)} at 1x and {_s(j3)} at 3x "
                 f"on this circuit (mean {ctx.get('journey_1x_tasks', float('nan')):.0f} and {ctx.get('journey_3x_tasks', float('nan')):.0f} "
                 f"tasks per draw){f', against {_s(jh)} for the Observed set' if jh is not None else ''}; the cost is per circuit, so a 10x "
                 f"territory means 10x as many independent journeys of this size, not one journey 10x as slow.")
    conv = (f"ran to convergence in {_s(ctx['conv_s'])} ({ctx['conv_iters']:,} evaluations, about {ctx['conv_passes']:.0f} passes) for a "
            f"{ctx['conv_gain']:.2f}% gain over the Horn order" if ctx["converged"] else
            f"did not converge within the cap ({ctx['conv_iters']:,} evaluations, {ctx['conv_gain']:.2f}% gain over the Horn order)")
    cap = ctx.get("cap")
    cap_txt = (f"the app's setting (swap pass capped at {cap:,} evaluations, 1 s time ceiling)" if cap is not None
               else "the solver's default setting")
    ceiling_txt = (f" On this machine the 1 s ceiling, not the {cap:,}-evaluation cap, ends the pass on the largest demo circuit "
                   f"(reaching the cap needs about {_s(ctx['cap_needs_s'])} at the measured evaluation cost, projected), so the "
                   f"displayed schedule for this circuit is machine-dependent here; a faster machine reaches the cap and shows a "
                   f"slightly different, deterministic schedule." if ctx.get("ceiling_hit") else "")
    return (
        f"## What changes at 10x\n\n"
        f"Horn's tree-merge ordering costs {_ms(ctx['horn_s'])} at {n} tasks and {_ms(ctx['horn10_s'])} at {n10} tasks: its cost is a sort "
        f"of the tasks plus a merge loop over the damaged zones, and the synthetic replication keeps the same {ctx['damaged_zones']} damaged "
        f"zones, so ordering is not the bottleneck at either size. The exact objective evaluation grows from {_ms(ctx['eval_s'])} to "
        f"{_ms(ctx['eval10_s'])} per call and a full adjacent-swap pass from {_s(ctx['pass_s'])} (measured) to {_s(ctx['pass10_proj_s'])} "
        f"(projected), so the swap pass is the quadratic part: {cap_txt} fits {ctx['app_iters']:,} evaluations "
        f"(about {ctx['app_passes']:.1f} passes, {ctx['app_gain']:.2f}% gain) at {n} tasks but only {ctx['iters10']:,} evaluations "
        f"({ctx['passes10']:.2f} of a pass, {ctx['stop10']}) at {n10}. At {n} tasks the descent {conv}; at 10x one pass alone exceeds the "
        f"interactive budget, so the app would show the Horn order with the pass truncated, which is what the evaluation cap and the time "
        f"ceiling already do today.{ceiling_txt}{j_txt} The nearest-premise lookup is a linear scan ({_ms(ctx['nearest_s'])} over "
        f"{ctx['n_premises']:,} premises) that grows with the premise count; a spatial index removes that.\n\n"
        f"Three modeling points matter more than the runtimes. Per-circuit sharding: every circuit is its own protective-zone tree with its "
        f"own damage set, so a territory is a sum of independent solves that run in parallel and cache per scenario. Shared crew pool: the "
        f"prototype dedicates crews to one circuit; in a real storm a shared pool is allocated across circuits with travel between them "
        f"(the ILM folds travel into task hours), which turns per-circuit ordering into a territory-wide assignment problem. Tie switches: "
        f"circuits can be back-fed through normally-open tie switches, so service for a group becomes the earlier of two paths instead of "
        f"one AND-chain; that breaks the out-tree assumption Horn's optimality rests on and needs an explicit switching step in the schedule."
    )


def render(rows: list[dict], ctx: dict, date: str, repeats: int, cap: float) -> str:
    lines = [
        "# Scale probe (generated by scripts/scale_probe.py)", "",
        f"- Date: {date}",
        f"- Machine: {_cpu_model()}, {os.cpu_count()} logical CPUs, {platform.platform()} ({platform.machine()})",
        f"- Python: {sys.version.split()[0]}",
        f"- Data: {ctx['bundle']}; {ctx['n_circuits']} circuits, {ctx['n_premises']:,} premises, {ctx['n_zones']:,} zones, "
        f"{ctx['n_damage']:,} damage records (Observed counts)",
        f"- Largest circuit by Feb-2025 records: {ctx['circuit']} ({ctx['n_tasks']} tasks, {ctx['damaged_zones']} damaged zones, "
        f"{ctx['affected']:,} affected customers, {ctx['crews']} crews = calibrated default, Assumed)",
        f"- Method: wall clock via time.perf_counter; micro-timings are the median of {repeats} repeats; the capped and time-boxed solves, "
        f"the convergence run (cap {cap:g} s) and the journeys ran once. Every row is a runtime measurement labeled **{LABEL}**, except "
        f"the one projected row, labeled **{LABEL_PROJECTED}** (arithmetic on a measured per-evaluation cost, not run). "
        f"Customer-outage-hour figures quoted in the detail column are Simulated schedule outputs on Observed inputs; the swap cap "
        f"({ctx['cap']:,} evaluations) is an Assumed constant (config.SWAP_EVALUATION_CAP, L9)." if ctx.get("cap") is not None else
        f"- Method: wall clock via time.perf_counter; micro-timings are the median of {repeats} repeats; the solves, the convergence run "
        f"(cap {cap:g} s) and the journeys ran once. Every row is a runtime measurement labeled **{LABEL}**, except the one projected row, "
        f"labeled **{LABEL_PROJECTED}**. Customer-outage-hour figures quoted in the detail column are Simulated schedule outputs on "
        f"Observed inputs.",
        f"- Regenerate: `python scripts/scale_probe.py --date {date} --repeats {repeats} --convergence-cap-s {cap:g}`",
        "", table(rows), "", what_changes(ctx), "",
    ]
    if ctx.get("errors"):
        lines += ["## Errors", "", "```", *ctx["errors"], "```", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="date stamped in the report header (YYYY-MM-DD)")
    ap.add_argument("--repeats", type=int, default=5, help="repeats for micro-timings (median is reported)")
    ap.add_argument("--convergence-cap-s", type=float, default=120.0, help="cap on the run-to-convergence swap descent")
    ap.add_argument("--circuit", default=None, help="override the circuit (default: most Feb-2025 records)")
    ap.add_argument("--out", default=str(ROOT / "results" / "scale_probe.md"))
    args = ap.parse_args(argv)
    rows, ctx = probe(args.repeats, args.convergence_cap_s, args.circuit)
    text = render(rows, ctx, args.date, args.repeats, args.convergence_cap_s)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf8")
    print(text)
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
