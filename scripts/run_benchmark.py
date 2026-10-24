"""Benchmark the scheduling policies on identical, reproducible instances across circuits.

For every circuit with at least --min-records Feb-2025 damage records, build the Observed damage set (and
--seeds Simulated sets at severity 1x), and for each crew count in {1, calibrated default, 2x default} run
FIFO, the customers-per-hour heuristic, dependency-aware without the swap pass, and dependency-aware with it.
All policies see the same tasks, hours and crews. Outputs (all Simulated, derived from Observed inputs):

    results/benchmark.csv          one row per (circuit, source, seed, crews, policy)
    results/benchmark_summary.md   measured reductions, win rates, worst cases, improvement-pass share, runtimes
    results/benchmark.png          distribution of the reduction in customer-outage hours

Nothing here is a claim beyond what the rows contain; losses are reported alongside wins.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from concierge import config  # noqa: E402
from concierge.data import DataBundle  # noqa: E402
from concierge.scenarios import DamageSets  # noqa: E402
from concierge.scheduling import solve  # noqa: E402

VARIANTS = {
    "fifo": dict(policy="fifo", improve=False),
    "customers_first": dict(policy="customers_first", improve=False),
    "dependency_aware_horn": dict(policy="dependency_aware", improve=False),
    "dependency_aware": dict(policy="dependency_aware", improve=True),
}


def run(min_records: int, seeds: int, time_budget: float, out: Path, limit: int | None) -> pd.DataFrame:
    data = DataBundle()
    sets = DamageSets(data)
    circuits = data.circuits[data.circuits["n_damage_ref"] >= min_records].sort_values("n_damage_ref", ascending=False)
    if limit:
        circuits = circuits.head(limit)
    rows = []
    t_start = time.time()
    for i, circ in enumerate(circuits["circuit_id"], 1):
        hist = sets.historical(circ)
        k0 = sets.default_crews(circ)
        crew_opts = sorted({1, k0, min(2 * k0, config.MAX_CREWS)})
        damage_sets = [("historical", None, hist)] + [("simulated", s, sets.simulated(circ, 1.0, 100 + s)) for s in range(seeds)]
        for source, seed, tasks in damage_sets:
            if len(tasks) < min_records:
                continue
            for k in crew_opts:
                inst = sets.instance(circ, tasks, k)
                crew_hours = None
                for name, spec in VARIANTS.items():
                    r = solve(inst, spec["policy"], improve=spec["improve"], reference=(len(tasks) <= 7 and name == "dependency_aware"),
                              time_budget_s=time_budget)
                    m = r.metrics
                    if crew_hours is None:
                        crew_hours = m.total_crew_hours
                    assert abs(m.total_crew_hours - crew_hours) < 1e-9, "policies must see identical inputs"
                    rows.append({
                        "circuit_id": circ, "source": source, "seed": seed, "crews": k, "variant": name, "n_tasks": len(tasks),
                        "affected_customers": m.affected_customers, "customer_hours": m.customer_hours,
                        "avg_hours_per_affected": m.customer_hours / m.affected_customers if m.affected_customers else np.nan,
                        "t50_h": m.t50_h, "t90_h": m.t90_h, "t100_h": m.t100_h, "life_support_t100_h": m.life_support_t100_h,
                        "crew_clock_hours": m.total_crew_hours, "runtime_s": r.status.runtime_s,
                        "improvement_pct": r.status.improvement_pct if r.status.improvement_pass else np.nan,
                        "reference_value": r.status.reference_value, "gap_pct": r.status.gap_pct,
                    })
        print(f"  [{i}/{len(circuits)}] circuit {circ}: {len(hist)} tasks, crews {crew_opts}, elapsed {time.time() - t_start:.0f}s")
    df = pd.DataFrame(rows)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "benchmark.csv", index=False)
    return df


def summarize(df: pd.DataFrame, out: Path, time_budget: float) -> str:
    df = df.copy()
    df["seed"] = df["seed"].fillna(-1).astype(int)  # Observed sets have no seed; keep them in the group keys
    key = ["circuit_id", "source", "seed", "crews"]
    wide = df.pivot_table(index=key, columns="variant", values="customer_hours").reset_index()
    wide["affected"] = df[df["variant"] == "fifo"].set_index(key)["affected_customers"].reindex(pd.MultiIndex.from_frame(wide[key])).to_numpy()
    wide["n_tasks"] = df[df["variant"] == "fifo"].set_index(key)["n_tasks"].reindex(pd.MultiIndex.from_frame(wide[key])).to_numpy()
    for base in ("fifo", "customers_first", "dependency_aware_horn"):
        wide[f"red_vs_{base}"] = 1 - wide["dependency_aware"] / wide[base]
    n = len(wide)

    def block(w: pd.DataFrame, title: str) -> list[str]:
        lines = [f"### {title} ({len(w)} instances)", "", "| baseline | mean reduction | median | weighted by affected customers | win rate (≤) | worst instance |", "|---|---|---|---|---|---|"]
        for base, label in (("fifo", "FIFO (report order)"), ("customers_first", "customers-per-hour heuristic"), ("dependency_aware_horn", "dependency-aware without swap pass")):
            r = w[f"red_vs_{base}"]
            wmean = float(np.average(r, weights=w["affected"])) if w["affected"].sum() else float("nan")
            lines.append(f"| {label} | {100 * r.mean():.1f}% | {100 * r.median():.1f}% | {100 * wmean:.1f}% | {100 * (r >= -1e-9).mean():.0f}% | {100 * r.min():+.1f}% |")
        return lines + [""]

    lines = ["# Scheduling benchmark (generated by scripts/run_benchmark.py)", "",
             f"Simulated schedules on Observed topology and labor data. {n} instances = {wide['circuit_id'].nunique()} circuits × "
             f"damage sets (Observed Feb-2025 records, plus seeded Poisson draws at 1×) × crew counts {{1, calibrated default, 2× default}}. "
             f"Every policy sees identical tasks, hours and crews. Metric: customer-outage hours (Σ customers × hours until service, each "
             f"premise counted once; service returns only when every damaged zone on the path is repaired). "
             f"'Reduction' is 1 − ours/baseline; negative = we lost. Swap pass budget {time_budget:g} s per instance.", ""]
    lines += block(wide, "All instances")
    for src in ("historical", "simulated"):
        lines += block(wide[wide["source"] == src], f"{'Observed Feb-2025 damage sets' if src == 'historical' else 'Simulated damage sets (1×)'}")
    lines += ["### By crew count (all sources)", "", "| crews | instances | mean reduction vs FIFO | vs customers-first | win rate vs customers-first |", "|---|---|---|---|---|"]
    wide["crew_bucket"] = np.where(wide["crews"] == 1, "1", np.where(wide["crews"] == wide.groupby("circuit_id")["crews"].transform("max"), "2× default", "default"))
    for b in ("1", "default", "2× default"):
        w = wide[wide["crew_bucket"] == b]
        if len(w):
            lines.append(f"| {b} | {len(w)} | {100 * w['red_vs_fifo'].mean():.1f}% | {100 * w['red_vs_customers_first'].mean():.1f}% | {100 * (w['red_vs_customers_first'] >= -1e-9).mean():.0f}% |")
    losses = wide[wide["red_vs_customers_first"] < -1e-9].sort_values("red_vs_customers_first")
    lines += ["", f"Instances where the customers-per-hour heuristic beat us: {len(losses)} of {n}."]
    if len(losses):
        lines += ["", "| circuit | source | seed | crews | tasks | our hours | heuristic hours | gap |", "|---|---|---|---|---|---|---|---|"]
        for _, r in losses.head(10).iterrows():
            lines.append(f"| {r['circuit_id']} | {r['source']} | {r['seed'] if r['seed'] >= 0 else '—'} | {r['crews']} | {int(r['n_tasks'])} | {r['dependency_aware']:,.0f} | {r['customers_first']:,.0f} | {100 * r['red_vs_customers_first']:+.1f}% |")
    da = df[df["variant"] == "dependency_aware"]
    small = da.dropna(subset=["gap_pct"])
    lines += ["", "### Solver status", "",
              f"- Swap-improvement pass: ran on {int(da['improvement_pct'].notna().sum())} instances, mean gain {da['improvement_pct'].mean():.2f}% "
              f"(max {da['improvement_pct'].max():.2f}%), runtime per dependency-aware solve mean {da['runtime_s'].mean():.2f} s, max {da['runtime_s'].max():.2f} s.",
              f"- Exact reference (≤ 7 tasks, brute force over all orders): {len(small)} instances; mean gap {small['gap_pct'].mean():.2f}%, "
              f"max gap {small['gap_pct'].max():.2f}%, instances at the optimum {int((small['gap_pct'] <= 1e-9).sum())}." if len(small) else
              "- No instance was small enough (≤ 7 tasks) for the brute-force reference.",
              "- With several crews the ordering is list-scheduled and therefore a heuristic; optimality holds only for one crew.", ""]
    hist_def = wide[(wide["source"] == "historical") & (wide["crew_bucket"] == "default")]
    t100 = df[(df["variant"] == "dependency_aware") & (df["source"] == "historical")].merge(hist_def[key], on=key)["t100_h"]
    if len(t100):
        lines += ["### Scale sanity check (not a fit)", "",
                  f"With the calibrated default crews and the Observed Feb-2025 damage sets, the dependency-aware schedule restores the last customer "
                  f"after a median of {t100.median() / 24:.1f} days (10th–90th percentile {np.percentile(t100, 10) / 24:.1f}–{np.percentile(t100, 90) / 24:.1f} days). "
                  f"The organizer's day-granularity repair records for that storm show report→repair gaps of 0–6 days for 97% of records. "
                  f"The crew default was calibrated to a 72 h FIFO makespan, so this agreement is partly by construction; it is a scale check, not validation.", ""]
    text = "\n".join(lines)
    (out / "benchmark_summary.md").write_text(text, encoding="utf8")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 4))
        groups = [100 * wide["red_vs_fifo"], 100 * wide["red_vs_customers_first"], 100 * wide["red_vs_dependency_aware_horn"]]
        names = ["vs FIFO", "vs customers-per-hour", "vs ours without swap pass"]
        try:
            ax.boxplot(groups, tick_labels=names, showmeans=True)   # matplotlib >= 3.9
        except TypeError:
            ax.boxplot(groups, labels=names, showmeans=True)        # older matplotlib
        ax.axhline(0, color="k", lw=0.8)
        ax.set_ylabel("reduction in customer-outage hours (%)")
        ax.set_title(f"Dependency-aware scheduling, {n} instances (Simulated)")
        fig.tight_layout()
        fig.savefig(out / "benchmark.png", dpi=150)
    except Exception as exc:  # pragma: no cover
        print(f"  (plot skipped: {exc})")
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-records", type=int, default=3)
    ap.add_argument("--seeds", type=int, default=2, help="simulated damage sets per circuit")
    ap.add_argument("--time-budget", type=float, default=0.5, help="swap-pass budget per instance (s)")
    ap.add_argument("--limit", type=int, default=None, help="only the N hardest-hit circuits (for quick runs)")
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--summarize-only", action="store_true", help="rebuild the summary and plot from an existing benchmark.csv")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if args.summarize_only:
        df = pd.read_csv(out / "benchmark.csv", dtype={"circuit_id": str})
    else:
        print("running benchmark…")
        df = run(args.min_records, args.seeds, args.time_budget, out, args.limit)
    text = summarize(df, out, args.time_budget)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
