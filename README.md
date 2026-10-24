# AEP AI Outage Concierge — HackOHI/O 2026

A customer-facing assistant for storm outages built on the AEP challenge data. For an anonymized premise it gives an
**explained outage risk**, a **restoration range** from labor-hour and crew-scheduling simulation, **preparation
guidance**, **alert eligibility**, and plain-language **messages for the four outage stages** (pre-storm, outage
confirmed, estimate updated, restoration complete). Its operational core is a **network-aware repair scheduler** that
minimizes customer-outage hours under limited crews and is benchmarked against FIFO and a customer-benefit heuristic
on identical scenarios.

Every number in the app, in this README and in `results/` is tagged **Observed** (organizer data as-is),
**Assumed** (a modeling choice listed in [ASSUMPTIONS.md](ASSUMPTIONS.md)) or **Simulated** (the output of a scenario run).

## Quick start

```bash
git clone https://github.com/AdhritSingh21/aep-outage-concierge
cd aep-outage-concierge
python -m venv .venv
.venv\Scripts\activate            # Windows   (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
streamlit run app.py               # runs on the committed demo bundle in data/demo
```

Full organizer dataset (optional, ~450 MB extracted): download the package from
https://github.com/hackohio/aep_challenge_hack26 and run

```bash
python scripts/prepare_data.py --source "path/to/aep_challenge_hack26-main.zip"
```

which extracts the archives, builds `data/processed/*.parquet`, derives the zone tree, exposure and damage
attribution, fits the risk model, writes `data/data_report.md` and `results/risk_validation.md`, and rebuilds
`data/demo`. Tests: `python -m pytest -q` (unit tests always run; `-m data` tests need `data/processed`).
Benchmark: `python scripts/run_benchmark.py` (about 30 s) regenerates `results/benchmark.*`.

## Demo in 3 steps

1. Click **Load demo story** in the sidebar. A premise on a hard-hit circuit is loaded with the Observed Feb-2025
   damage set and the calibrated crew count. The dependency chain shows the damage nearest the customer being
   repaired hours before service returns, because a zone further upstream is still being repaired.
2. Double the crews. The schedule, the restoration range, the chain hours, the explanation and the
   *estimate-updated* message all move together (they are pure functions of one `Scenario`).
3. Toggle **Difficult access**. Every task gets the workbook's restricted-access adder and the same four things move
   the other way. The Operations view shows FIFO, the customers-per-hour heuristic and our policy on identical inputs.

## What the challenge asked and where it is

| Brief requirement | Where |
|---|---|
| Accept a premise, circuit or map location | sidebar: example premises, anonymized premise id, circuit, lat/lon → nearest premise |
| Outage-risk estimate / band with contributing factors and uncertainty | *Your outlook*: band, probability, confidence, factors, data flags (`concierge/risk.py`) |
| Restoration range considering damage, upstream dependencies, labor, nonproductive time; updates with assumptions | range + narrative + dependency chain (`labor.py`, `scheduling.py`, `restoration.py`) |
| Model output vs confirmed information | Observed / Assumed / Simulated tags on every card and message |
| Preparation and safety guidance, adapted to declared needs | *Prepare* section (`guidance.py`); medical-need toggle, nothing inferred |
| Alert eligibility, timing, channel, escalation; four messages | *Alerts and escalation*, *Messages* tab (`alerts.py`, `messages.py`) |
| Explainability, confidence, assumptions, missing data, privacy, fallback | *Why & limits* evidence panel, no-path fallback, PII never loaded |
| Judges can change a scenario input | severity, hours to landfall, crews, access, damage set, labor factor, crew start delay, medical need, opt-in |

## Methods

**Data (Observed).** The stdlib shapefile reader in `concierge/shapefile_io.py` loads the 12 GIS layers; `etl.py`
writes typed parquet tables. Verified facts that shape the design (details in `data/data_report.md`):
`matched_upstream.txt` keys on the customer `premise_nu` (117,595 of 117,642 premises have a path); recloser tokens
match the recloser layer by location, fuses by id; `report_len` is feet; the anonymized `circuit_id` differs between
GIS layers for ~14 circuits, so every layer is remapped to the transformer layer's unambiguous (station, circuit
label) key; the damage file's `NEAR_FID` was computed on unclipped layers and is ignored; repair dates are
day-granularity and 24 unrepaired rows carry a placeholder date.

**Topology.** Each protective device in a path (fuse, recloser, sectionalizer, breaker) is a *zone*; 7,190 zones form
one tree per circuit. Primary segments are assigned to zones by cutting the structure graph at device locations and
voting with the transformers inside each piece; damage records are attributed by key and nearest line. Every premise
belongs to exactly one leaf zone, so nothing is double-counted.

**Risk.** A Poisson regression of Feb-2025 line-damage counts per circuit with overhead miles as exposure gives a
prior rate; each zone's rate is the gamma-Poisson posterior that also uses the zone's own Feb-2025 count
(negative-binomial dispersion estimated by MLE). A customer is interrupted by damage anywhere on their upstream
path, so P(outage | storm severity s) = 1 − exp(−s·Σ zone rates on the path). Leave-one-circuit-out validation of the
prior and the resulting customer distribution are in `results/risk_validation.md`; the honest finding is that the
circuit features add little over exposure alone (Spearman 0.66 vs 0.64), and that the Feb-2025 pattern mixes storm
path with vulnerability, which one event cannot separate.

**Labor.** Workbook CU averages are combined into damage-type bundles (pole ≈ 34 person-hours with ILM 4.5, primary
wire ≈ 3.4, fuse bank ≈ 3.7, transformer ≈ 17.4), adders follow the workbook, ILM is applied exactly once and
already covers drive and set-up time, and a 4-person crew works a task in parallel.

**Scheduling.** Tasks sit in zones; K identical crews; a customer group is restored only when every damaged zone on
its path is repaired (*repair completed ≠ service restored*). Objective: customer-outage hours. Policies on identical
inputs: FIFO (report order), customers-per-hour (customers downstream ÷ hours, ignoring the AND-coupling), and ours:
Horn's tree-merge ordering on the damaged-zone tree with weights = customers whose deepest damaged zone is the node
(optimal for one crew with upstream-first precedence), list-scheduled on K crews, plus an adjacent-swap improvement
pass on the exact objective. Solver status (order source, improvement gain, brute-force gap on ≤ 7-task instances,
runtime) is shown in the app.

## Results (Simulated, from `scripts/run_benchmark.py`; full tables in `results/benchmark_summary.md`)

306 instances = 46 circuits with ≥ 3 Feb-2025 damage records × {Observed Feb-2025 set, 2 seeded simulated sets} ×
crews {1, calibrated default, 2× default}. Reduction = 1 − ours ÷ baseline in customer-outage hours.

| baseline | mean reduction | median | win rate | worst instance |
|---|---|---|---|---|
| FIFO (report order) | 51.5 % | 55.8 % | 100 % | 0.0 % |
| customers-per-hour heuristic | 9.6 % | 7.3 % | 99 % | −1.6 % |
| ours without the swap pass | 1.4 % | 0.0 % | 100 % | 0.0 % |

On the Observed Feb-2025 sets alone: 40.0 % vs FIFO, 10.0 % vs the heuristic. At the calibrated default crew count
the gain over the heuristic is 17.5 %. The heuristic beat us on 3 of 306 instances, by at most 1.6 % (listed in the
summary). On the 64 instances small enough for brute force, 60 are at the optimum; the largest gap is 9.2 %.
With the default crews the Observed Feb-2025 sets restore the last customer after a median of 2.7 days, inside the
0–6 day scale of the organizer's repair records (a scale check, partly by construction, not a validation).

![benchmark](results/benchmark.png)

## Limitations

- One reference storm; no per-customer outage records; the probability is a damage-intensity estimate, not a forecast.
- Tie switches and back-feeds are not modeled; any upstream damage is assumed to interrupt service.
- Crew counts, crew size, labor bundles and alert rules are the team's assumptions (register in `ASSUMPTIONS.md`).
- 47 premises have no upstream path and use a circuit-average fallback with Low confidence.

## Repository

```
app.py                 Streamlit UI (sidebar Scenario → tabs Outlook, Messages, Operations, Why & limits, Data & results)
concierge/             shapefile_io, etl, topology, exposure, risk, labor, scenarios, scheduling, restoration,
                       alerts, guidance, messages, viz, evidence, journey, data, scenario, config
scripts/               prepare_data.py (organizer zip → processed tables, model, demo bundle), run_benchmark.py
tests/                 unit tests (synthetic) + data tests (skipped without data/processed)
data/                  README.md (how to obtain the data, attribution); demo/ committed; raw/extracted/processed ignored
results/               risk_model.json, risk_validation.md, benchmark.csv, benchmark_summary.md, benchmark.png
ASSUMPTIONS.md  PLAN.md  CLAUDE.md
```

## Data and attribution

Organizer data is provided by **AEP** for **HackOHI/O 2026**: https://github.com/hackohio/aep_challenge_hack26.
It is not redistributed here except for the small derived demo bundle in `data/demo/` (a handful of circuits,
anonymized premise ids only). See [data/README.md](data/README.md).

## License

MIT for the code (see LICENSE). The challenge data remains the property of AEP / HackOHI/O.
