"""Benchmark the scheduling policies on identical, reproducible instances across circuits.

For every circuit with at least --min-records Feb-2025 damage records, build the Observed damage set (and
--seeds Simulated sets at severity 1x), and for each crew count in {1, calibrated default, 2x default} run
FIFO, the customers-per-hour heuristic, dependency-aware without the swap pass, and dependency-aware with it.
All policies see the same tasks, hours and crews. Outputs (all Simulated, derived from Observed inputs):

    results/benchmark.csv            one row per (circuit, source, seed, crews, policy)
    results/benchmark_summary.md     measured reductions, win rates, worst cases, improvement-pass share, runtimes
    results/benchmark_headline.json  the headline figures of the summary, for the app and README to read
    results/benchmark.png            distribution of the reduction in customer-outage hours

`--summarize-only` rebuilds the summary, the JSON and the plot from an existing benchmark.csv without re-solving.
Every figure in the summary and the JSON is computed here from the CSV or from the loaded data bundle; nothing is
hand-typed. Nothing here is a claim beyond what the rows contain; losses are reported alongside wins.
"""
from __future__ import annotations

import argparse
import json
import math
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
BASELINES = (("fifo", "FIFO (report order)"), ("customers_first", "customers-per-hour heuristic"),
             ("dependency_aware_horn", "dependency-aware without swap pass"))
BRUTE_FORCE_MAX_TASKS = 7      # instances up to this size get an exact reference (all orders enumerated)
REPAIR_GAP_WINDOW_DAYS = 6     # descriptive bin for the Observed report->repair gap; median/p90/max are reported too
EPS = 1e-9


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
                    r = solve(inst, spec["policy"], improve=spec["improve"],
                              reference=(len(tasks) <= BRUTE_FORCE_MAX_TASKS and name == "dependency_aware"),
                              time_budget_s=time_budget)
                    m = r.metrics
                    if crew_hours is None:
                        crew_hours = m.total_crew_hours
                    assert abs(m.total_crew_hours - crew_hours) < 1e-9, "policies must see identical inputs"
                    rows.append({
                        "circuit_id": circ, "source": source, "seed": seed, "crews": k, "default_crews": k0, "variant": name,
                        "swap_budget_s": time_budget,
                        "n_tasks": len(tasks), "affected_customers": m.affected_customers, "customer_hours": m.customer_hours,
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


# ---------------------------------------------------------------------------------------------------------------------
# helpers for the summary

def infer_default_crews(df: pd.DataFrame) -> pd.Series:
    """Each circuit's calibrated default crew count, indexed by circuit_id.

    Uses the `default_crews` column when the CSV has one (written by run() since this version). Older CSVs carry only
    the crew counts that run() built as sorted({1, k0, min(2*k0, MAX_CREWS)}), from which k0 is recovered exactly:
    three options -> the middle one; two options {1, 2} -> k0 = 1 (its 2x default is 2); two options {1, m != 2}
    -> k0 = m (2*k0 was capped at MAX_CREWS and coincided with k0 = MAX_CREWS). A single option means k0 = 1 = MAX.
    """
    if "default_crews" in df.columns and df["default_crews"].notna().all():
        return df.groupby("circuit_id")["default_crews"].first().astype(int)
    assert config.MAX_CREWS > 2, "crew options {1, 2} would be ambiguous when MAX_CREWS <= 2"
    out = {}
    for circ, crews in df.groupby("circuit_id")["crews"].unique().items():
        opts = sorted(int(c) for c in crews)
        if len(opts) == 3:
            out[circ] = opts[1]
        elif len(opts) == 2:
            out[circ] = 1 if opts[1] == 2 else opts[1]
        else:
            out[circ] = opts[0]
    return pd.Series(out, name="default_crews", dtype=int)


def repair_gap_stats() -> dict | None:
    """Observed report->repair gap (days) on repaired reference-storm records in whichever bundle DataBundle() loads.

    Dates are day-granularity (CLAUDE.md); placeholder repair dates were nulled in ETL and therefore drop out here.
    Returns None when no data bundle is available, so the summary says so instead of quoting a figure.
    """
    try:
        data = DataBundle()
    except FileNotFoundError:
        return None
    d = data.damage
    rows = d[d["in_reference_storm"] & d["repaired"] & d["repaired_date"].notna() & d["report_date"].notna()]
    if not len(rows):
        return None
    gap = (pd.to_datetime(rows["repaired_date"]) - pd.to_datetime(rows["report_date"])).dt.days.astype(int)
    bundle = "demo bundle" if data.is_demo else "processed extract"
    n_circ = int(len(data.circuits))
    return {
        "label": "Observed",
        "bundle": bundle, "n_circuits_in_bundle": n_circ, "n_records": int(len(gap)),
        "n_reference_storm_records_in_bundle": int(d["in_reference_storm"].sum()),
        "window_days": REPAIR_GAP_WINDOW_DAYS,
        "share_within_window_pct": round(100 * float(gap.between(0, REPAIR_GAP_WINDOW_DAYS).mean()), 1),
        "median_days": float(gap.median()), "p90_days": float(np.percentile(gap, 90)), "max_days": int(gap.max()),
        "n_negative": int((gap < 0).sum()),
        "basis": f"computed on {len(gap)} repaired reference-storm records ({config.REFERENCE_STORM_START} to "
                 f"{config.REFERENCE_STORM_END}) in the loaded bundle ({bundle}: {n_circ} circuits); day-granularity dates",
    }


def _jsonable(obj):
    """Convert numpy / pandas scalars and NaN so json.dump(..., allow_nan=False) accepts the result."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def _instance_id(r: pd.Series) -> dict:
    return {"circuit_id": str(r["circuit_id"]), "source": str(r["source"]),
            "seed": None if int(r["seed"]) < 0 else int(r["seed"]), "crews": int(r["crews"]), "n_tasks": int(r["n_tasks"])}


def _pct(x: float, nd: int = 1) -> float:
    """Fraction -> percent rounded to nd decimals; '+ 0.0' turns a rounded -0.0 (float noise below 1e-9) into 0.0."""
    return round(100 * float(x), nd) + 0.0


def reduction_stats(w: pd.DataFrame, base: str) -> dict:
    """Mean / median / affected-weighted mean reduction (%), win rate (%) and the worst instance for one baseline.

    A 'win' is ours <= baseline (ties count); callers must pass a non-empty frame.
    """
    r = w[f"red_vs_{base}"]
    wmean = float(np.average(r, weights=w["affected"])) if w["affected"].sum() else float("nan")
    worst = w.loc[r.idxmin()]
    return {"n": int(len(w)), "mean_pct": _pct(r.mean()), "median_pct": _pct(r.median()), "weighted_by_affected_pct": _pct(wmean),
            "win_rate_pct": _pct((r >= -EPS).mean(), 0), "worst_pct": _pct(r.min()), "worst_instance": _instance_id(worst)}


def header_sentence(df: pd.DataFrame, wide: pd.DataFrame) -> tuple[str, dict]:
    """The 'N instances = ...' sentence, with every count taken from the CSV, plus the same counts as a dict."""
    n = len(wide)
    n_circ = int(wide["circuit_id"].nunique())
    sets_per_circ = wide.groupby("circuit_id")[["source", "seed"]].apply(lambda g: len(g.drop_duplicates()))
    obs_per_circ = wide[wide["source"] == "historical"].groupby("circuit_id")["seed"].nunique().reindex(sets_per_circ.index).fillna(0).astype(int)
    sim_per_circ = wide[wide["source"] == "simulated"].groupby("circuit_id")["seed"].nunique().reindex(sets_per_circ.index).fillna(0).astype(int)
    opts_per_circ = wide.groupby("circuit_id")["crews"].nunique()
    by_opts = opts_per_circ.value_counts().sort_index()          # {2: 36, 3: 10} in the committed CSV
    n_default_one = int((wide.groupby("circuit_id")["default_crews"].first() == 1).sum())
    words = {1: "one", 2: "two", 3: "three"}
    if sets_per_circ.nunique() == 1:
        k_sets, k_sim = int(sets_per_circ.iloc[0]), int(sim_per_circ.iloc[0])
        sets_txt = f"{k_sets} damage sets ({int(obs_per_circ.iloc[0])} Observed Feb-2025 set + {k_sim} seeded Poisson draw{'s' if k_sim != 1 else ''} at 1×)"
        terms = " + ".join(f"{cnt} circuits × {k_sets} sets × {k} crew counts" for k, cnt in by_opts.items())
        arithmetic = f"{terms} = {sum(cnt * k_sets * k for k, cnt in by_opts.items())}"
    else:
        sets_txt = (f"{int(sets_per_circ.min())}–{int(sets_per_circ.max())} damage sets per circuit (1 Observed Feb-2025 set plus "
                    f"seeded Poisson draws at 1×; draws below the record minimum are skipped)")
        arithmetic = "Σ over circuits of damage sets × crew counts = " + str(int((sets_per_circ * opts_per_circ).sum()))
    opts_txt = ", ".join(f"{cnt} circuits have {words.get(k, k)}" for k, cnt in by_opts.items())
    coincide = (f" ({n_default_one} circuits' calibrated default is one crew, so for them 1 and the default coincide)"
                if n_default_one else "")
    sentence = (f"{n} instances = {n_circ} circuits × {sets_txt} × that circuit's distinct crew counts from {{1, calibrated default, 2× default}}: "
                f"{opts_txt}{coincide}, i.e. {arithmetic}.")
    counts = {"n_instances": int(n), "n_circuits": n_circ, "damage_sets_per_circuit": sorted(int(x) for x in sets_per_circ.unique()),
              "observed_sets_per_circuit": sorted(int(x) for x in obs_per_circ.unique()),
              "simulated_sets_per_circuit": sorted(int(x) for x in sim_per_circ.unique()),
              "circuits_by_crew_count_options": {str(int(k)): int(v) for k, v in by_opts.items()},
              "n_circuits_default_is_one_crew": n_default_one}
    return sentence, counts


# ---------------------------------------------------------------------------------------------------------------------

def summarize(df: pd.DataFrame, out: Path, time_budget: float) -> str:
    df = df.copy()
    if "swap_budget_s" in df.columns and df["swap_budget_s"].notna().all():
        time_budget = float(df["swap_budget_s"].iloc[0])  # the budget the rows were actually solved with, not the CLI default
    # Observed sets have no seed (None in a fresh run, NaN from the CSV); coerce explicitly and keep them in the group keys
    df["seed"] = pd.to_numeric(df["seed"], errors="coerce").fillna(-1).astype(int)
    key = ["circuit_id", "source", "seed", "crews"]
    wide = df.pivot_table(index=key, columns="variant", values="customer_hours").reset_index()
    fifo_rows = df[df["variant"] == "fifo"].set_index(key)
    wide["affected"] = fifo_rows["affected_customers"].reindex(pd.MultiIndex.from_frame(wide[key])).to_numpy()
    wide["n_tasks"] = fifo_rows["n_tasks"].reindex(pd.MultiIndex.from_frame(wide[key])).to_numpy()
    for base, _ in BASELINES:
        wide[f"red_vs_{base}"] = 1 - wide["dependency_aware"] / wide[base]
    n = len(wide)

    # crew-count flags: 'at calibrated default' is independent of the 1 / 2x-default buckets (a circuit whose default
    # is one crew is at its default when crews == 1), so the subsets below overlap and are labeled as such.
    default_by_circ = infer_default_crews(df)
    wide["default_crews"] = wide["circuit_id"].map(default_by_circ).astype(int)
    wide["max_crews"] = wide.groupby("circuit_id")["crews"].transform("max")
    wide["at_default"] = wide["crews"] == wide["default_crews"]
    wide["default_gt1"] = wide["default_crews"] > 1
    n_default_one = int((default_by_circ == 1).sum())
    crew_subsets = [
        ("1 crew", wide["crews"] == 1),
        ("calibrated default, every circuit", wide["at_default"]),
        ("calibrated default, only circuits whose default exceeds 1 crew", wide["at_default"] & wide["default_gt1"]),
        ("2× default", wide["crews"] == wide["max_crews"]),
    ]
    crew_json_keys = ["one_crew", "default_all_circuits", "default_multi_crew_circuits", "double_default"]

    def block(w: pd.DataFrame, title: str) -> tuple[list[str], dict]:
        if not len(w):
            return [f"### {title} (0 instances)", "", "No instances of this kind in benchmark.csv.", ""], {"n": 0}
        lines = [f"### {title} ({len(w)} instances)", "", "| baseline | mean reduction | median | weighted by affected customers | win rate (ours ≤ baseline) | worst instance |", "|---|---|---|---|---|---|"]
        stats = {}
        for base, label in BASELINES:
            s = reduction_stats(w, base)
            stats[f"vs_{base}"] = s
            lines.append(f"| {label} | {s['mean_pct']:.1f}% | {s['median_pct']:.1f}% | {s['weighted_by_affected_pct']:.1f}% | {s['win_rate_pct']:.0f}% | {s['worst_pct']:+.1f}% |")
        return lines + [""], {"n": int(len(w)), **stats}

    head, counts = header_sentence(df, wide)
    headline: dict = {
        "generated_by": "scripts/run_benchmark.py", "source_csv": "results/benchmark.csv",
        "labels": {"reductions_and_schedules": "Simulated (schedules computed on Observed topology and labor data)",
                   "repair_gap": "Observed (organizer damage records, day granularity)",
                   "runtime": "Measured (wall clock on the machine that ran the benchmark)"},
        "metric": "customer-outage hours; reduction = 1 - ours / baseline (negative = we lost)",
        "swap_pass_budget_s": float(time_budget), "instances": counts,
    }
    lines = ["# Scheduling benchmark (generated by scripts/run_benchmark.py)", "",
             f"Simulated schedules on Observed topology and labor data. {head} "
             f"Every policy sees identical tasks, hours and crews. Metric: customer-outage hours (Σ customers × hours until service, each "
             f"premise counted once; service returns only when every damaged zone on the path is repaired). "
             f"'Reduction' is 1 − ours/baseline; negative = we lost. Swap pass budget {time_budget:g} s per instance. "
             f"The headline figures are also written to benchmark_headline.json beside this file.", ""]
    blk, headline["all"] = block(wide, "All instances")
    lines += blk
    for src, title, jkey in (("historical", "Observed Feb-2025 damage sets", "observed_sets"), ("simulated", "Simulated damage sets (1×)", "simulated_sets")):
        blk, headline[jkey] = block(wide[wide["source"] == src], title)
        lines += blk

    lines += ["### By crew count (all sources)", "",
              "| crews | instances | circuits | mean reduction vs FIFO | vs customers-first | win rate vs customers-first |", "|---|---|---|---|---|---|"]
    headline["by_crews"] = {}
    for (label, mask), jkey in zip(crew_subsets, crew_json_keys):
        w = wide[mask]
        if len(w):
            lines.append(f"| {label} | {len(w)} | {w['circuit_id'].nunique()} | {100 * w['red_vs_fifo'].mean():.1f}% | "
                         f"{100 * w['red_vs_customers_first'].mean():.1f}% | {100 * (w['red_vs_customers_first'] >= -EPS).mean():.0f}% |")
            headline["by_crews"][jkey] = {
                "label": label, "n": int(len(w)), "n_circuits": int(w["circuit_id"].nunique()),
                "mean_reduction_vs_fifo_pct": _pct(w["red_vs_fifo"].mean()),
                "mean_reduction_vs_customers_first_pct": _pct(w["red_vs_customers_first"].mean()),
                "median_reduction_vs_customers_first_pct": _pct(w["red_vs_customers_first"].median()),
                "win_rate_vs_customers_first_pct": _pct((w["red_vs_customers_first"] >= -EPS).mean(), 0),
            }
    lines += ["", f"Rows overlap: the calibrated default is one crew for {n_default_one} of {wide['circuit_id'].nunique()} circuits "
                  f"(Assumed: the crew count that clears the Observed Feb-2025 set in about {config.DEFAULT_CREW_TARGET_HOURS:g} h of FIFO makespan, "
                  f"bounded to [{config.MIN_CREWS}, {config.MAX_CREWS}]), so their default instances are also their 1-crew instances. "
                  f"The 'every circuit' row is the gain at each circuit's own default; the next row is the subset whose default exceeds one crew."]
    headline["by_crews"]["n_circuits_default_is_one_crew"] = n_default_one

    losses = wide[wide["red_vs_customers_first"] < -EPS].sort_values("red_vs_customers_first")
    lines += ["", f"Instances where the customers-per-hour heuristic beat us: {len(losses)} of {n}."]
    headline["losses_vs_customers_first"] = {"n": int(len(losses)), "of": int(n), "instances": []}
    if len(losses):
        # two decimals here: a loss below 0.05 % would otherwise print as -0.0 % beside a row that is listed as a loss
        lines += ["", "| circuit | source | seed | crews | tasks | our hours | heuristic hours | gap |", "|---|---|---|---|---|---|---|---|"]
        for _, r in losses.head(10).iterrows():
            lines.append(f"| {r['circuit_id']} | {r['source']} | {r['seed'] if r['seed'] >= 0 else '—'} | {r['crews']} | {int(r['n_tasks'])} | {r['dependency_aware']:,.0f} | {r['customers_first']:,.0f} | {100 * r['red_vs_customers_first']:+.2f}% |")
        for _, r in losses.iterrows():
            headline["losses_vs_customers_first"]["instances"].append({
                **_instance_id(r), "our_customer_hours": round(float(r["dependency_aware"]), 1),
                "heuristic_customer_hours": round(float(r["customers_first"]), 1), "gap_pct": _pct(r["red_vs_customers_first"], 2)})

    da = df[df["variant"] == "dependency_aware"]
    small = da.dropna(subset=["gap_pct"])
    headline["swap_pass"] = {"label": "Simulated gain, Measured runtime", "instances": int(da["improvement_pct"].notna().sum()),
                             "mean_gain_pct": round(float(da["improvement_pct"].mean()), 2), "max_gain_pct": round(float(da["improvement_pct"].max()), 2),
                             "runtime_mean_s": round(float(da["runtime_s"].mean()), 2), "runtime_max_s": round(float(da["runtime_s"].max()), 2)}
    headline["brute_force_reference"] = ({"max_tasks": BRUTE_FORCE_MAX_TASKS, "instances": int(len(small)), "mean_gap_pct": round(float(small["gap_pct"].mean()), 2),
                                          "max_gap_pct": round(float(small["gap_pct"].max()), 2), "at_optimum": int((small["gap_pct"] <= EPS).sum())}
                                         if len(small) else {"max_tasks": BRUTE_FORCE_MAX_TASKS, "instances": 0})
    lines += ["", "### Solver status", "",
              f"- Swap-improvement pass: ran on {headline['swap_pass']['instances']} instances, mean gain {da['improvement_pct'].mean():.2f}% "
              f"(max {da['improvement_pct'].max():.2f}%), runtime per dependency-aware solve mean {da['runtime_s'].mean():.2f} s, max {da['runtime_s'].max():.2f} s (Measured).",
              f"- Exact reference (≤ {BRUTE_FORCE_MAX_TASKS} tasks, brute force over all orders): {len(small)} instances; mean gap {small['gap_pct'].mean():.2f}%, "
              f"max gap {small['gap_pct'].max():.2f}%, instances at the optimum {int((small['gap_pct'] <= EPS).sum())}." if len(small) else
              f"- No instance was small enough (≤ {BRUTE_FORCE_MAX_TASKS} tasks) for the brute-force reference.",
              "- With several crews the ordering is list-scheduled and therefore a heuristic; optimality holds only for one crew.", ""]

    # scale sanity check: time to the last customer at every circuit's own default (all circuits) and on the multi-crew subset
    da_hist = df[(df["variant"] == "dependency_aware") & (df["source"] == "historical")]
    t100_all = da_hist.merge(wide.loc[(wide["source"] == "historical") & wide["at_default"], key], on=key)["t100_h"]
    t100_multi = da_hist.merge(wide.loc[(wide["source"] == "historical") & wide["at_default"] & wide["default_gt1"], key], on=key)["t100_h"]
    headline["t100_days_observed_sets_at_default"] = {}
    for jkey, t in (("default_all_circuits", t100_all), ("default_multi_crew_circuits", t100_multi)):
        if len(t):
            headline["t100_days_observed_sets_at_default"][jkey] = {"n_circuits": int(len(t)), "median": round(float(t.median()) / 24, 1),
                                                                    "p10": round(float(np.percentile(t, 10)) / 24, 1), "p90": round(float(np.percentile(t, 90)) / 24, 1)}
    gap = repair_gap_stats()
    headline["repair_gap_days"] = gap
    if len(t100_all):
        sc = [f"At every circuit's own calibrated default crew count ({len(t100_all)} Observed Feb-2025 damage sets, {n_default_one} of them at one crew), "
              f"the dependency-aware schedule restores the last customer after a median of {t100_all.median() / 24:.1f} days "
              f"(10th–90th percentile {np.percentile(t100_all, 10) / 24:.1f}–{np.percentile(t100_all, 90) / 24:.1f} days)."]
        if len(t100_multi):
            sc.append(f"On the {len(t100_multi)} circuits whose default exceeds one crew the median is {t100_multi.median() / 24:.1f} days "
                      f"({np.percentile(t100_multi, 10) / 24:.1f}–{np.percentile(t100_multi, 90) / 24:.1f}).")
        if gap:
            def days(x: float) -> str:
                return f"{x:.0f} day" + ("" if round(x) == 1 else "s")
            sc.append(f"Observed: the organizer's day-granularity repair records for that storm show report→repair gaps of 0–{gap['window_days']} days "
                      f"for {gap['share_within_window_pct']:.1f}% of records (median {days(gap['median_days'])}, 90th percentile {days(gap['p90_days'])}, "
                      f"max {days(gap['max_days'])}; {gap['basis']}).")
        else:
            sc.append("The organizer's report→repair gap could not be computed because no data bundle was found; no figure is quoted.")
        sc.append(f"The crew default was calibrated to a {config.DEFAULT_CREW_TARGET_HOURS:g} h FIFO makespan, so this agreement is partly by construction; "
                  f"it is a scale check, not validation.")
        lines += ["### Scale sanity check (not a fit)", "", " ".join(sc), ""]

    text = "\n".join(lines)
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark_summary.md").write_text(text, encoding="utf8", newline="\n")
    with (out / "benchmark_headline.json").open("w", encoding="utf8", newline="\n") as fh:
        json.dump(_jsonable(headline), fh, indent=2, allow_nan=False)
        fh.write("\n")

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
    ap.add_argument("--summarize-only", action="store_true",
                    help="rebuild the summary, benchmark_headline.json and the plot from an existing benchmark.csv (no solving)")
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
