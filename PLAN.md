# PLAN.md — milestones and requirement traceability

Deadline: ~24 h from 2026-10-24. Priorities: (1) every customer-facing requirement, (2) network-aware scheduling vs
FIFO and a customer-benefit heuristic on identical scenarios, (3) one coherent demo with a dependency visual and an
evidence panel. Observed / Assumed / Simulated stay separate. No manufactured labels or claims.

## Milestones (check when verified and pushed)

- [x] **M0 — Environment, repo, data access.** Git installed, identity set, venv, scaffold, `.gitignore`, LICENSE,
      README skeleton, `data/README.md`, `scripts/prepare_data.py --extract`. Done when the private GitHub repo shows
      the first commit and extraction reproduces `data/extracted/{Data1,Data2,matched_upstream}`.
- [x] **M1 — Data layer and joins.** `shapefile_io`, `etl` → parquet, `data/data_report.md`. Done when `pytest -m data`
      passes: premise→path ≥ 99.9 %, transformer match ≥ 99.9 %, `report_len`/geodesic ratio in 0.95–1.05,
      facilities inside the clip bbox after reprojection.
- [x] **M2 — Topology, zones, exposure, damage attribution.** Zone tree per circuit, leaf zone per customer, OH miles
      and devices per zone, damage → circuit → zone. Done when every pathed customer has exactly one leaf zone, zone
      customers sum to circuit customers for all 110 circuits, zone OH miles sum to circuit OH miles ±1 %, ≥ 99 % of
      damage rows attributed, unmapped rows listed in the report.
- [x] **M3 — Risk model, explanation, confidence, fallback.** Circuit-level Poisson rate model with OH-mile offset,
      path aggregation, bands, factors, confidence, no-path fallback, `results/risk_validation.md`.
- [x] **M4 — Priority 1 complete.** `Scenario`, labor bundles (ILM once), FIFO schedule, restoration range with
      repair-vs-service text, alerts, guidance, four messages, dependency chain figure, Streamlit tabs
      Outlook / Messages / Why & limits / Data & results, selection by sample premise / ID / circuit / lat-lon.
- [x] **M5 — Priority 2.** `customers_first` and `dependency_aware` policies, `SolverStatus`, metrics without
      double-counting, `scripts/run_benchmark.py` (all circuits with ≥ 3 Feb-2025 records × crews × seeds),
      Operations tab. Done when scheduling tests pass and README reports measured results incl. losses.
- [x] **M6 — Priority 3.** Demo story preset, evidence/results panel, estimate-updated message diff. Done when the
      consistency test proves one `Scenario` change updates schedule, range, explanation and message together.
- [x] **M7 — Submission readiness.** Repository public, Streamlit Community Cloud deploy, README, `ASSUMPTIONS.md`,
      `data/demo` bundle, fresh-clone smoke test, PII/secret scan, logged-out link check.
- [x] **M8 — Judging readiness (after an adversarial audit of M7).** What the audit found and what changed:
      crew start delay was counted twice → schedules solved from hour 0, delay added once, tested (L11); the pre-storm
      message was ungrammatical for unaffected premises → explicit restoration states with a secondary simulated range
      (L12); the life-support example never showed medical handling → per-premise session resets, declared-needs list,
      script-chosen examples (`scripts/pick_examples.py`); one premise crashed the page → missing-zone guard; the
      storm slider was inert on the opening example → script-chosen first example whose band moves, plus a risk
      sensitivity line; the estimate-updated message reported moves that did not happen → rule E5 dead-band and real
      reasons; fallback premises were sent a number → "being assessed" everywhere (E6); facilities and accessibility
      needs were unused → facility counts per zone, critical-load weighting (L10), mobility and facility needs (C5);
      no map → zone-centroid map; no audit trail → downloadable session log (C6); number hygiene (benchmark default
      bucket, computed repair-gap sentence, `results/benchmark_headline.json`, `results/scale_probe.md`); public
      bundle slimmed (no ids, rounded coordinates, coordinate-free facilities); README rewritten with sources,
      diagrams, questions asked, what broke, impact and scaling. Done when `pytest -q` is green on the committed
      bundle, the judge walk-through shows every brief bullet on screen, and the deploy is refreshed from `main`.

## Requirement traceability

| Brief requirement / MVP step | Feature (module) | Milestone | Judged / measured by |
|---|---|---|---|
| R1 Accept premise, circuit, or map location | example premises (script-chosen), anonymized ID (deploy coverage stated), every premise of a circuit, lat/lon → nearest premise with distance and out-of-area warning (`app.py`) | M4/M8 | all modes reach the same journey |
| R1 Risk estimate / band + signals | `risk.py` circuit rate model + upstream-path aggregation; severity input; band on the rounded percent | M3/M8 | `results/risk_validation.md` (leave-one-circuit-out) |
| R1 Contributing factors + uncertainty | factors, confidence (defined on screen), data flags, risk by severity line | M3/M8 | every output carries confidence + flags |
| R2 Restoration range | `restoration.py` range (labor ×0.75–1.5; P10–P90 for draws) with explicit state | M4/M8 | lower ≤ mid ≤ upper; delay counted once (test) |
| R2 Damage, upstream dependencies, step restoration, labor, travel, nonproductive time | `labor.py` + ILM; zone tree; AND-restoration (`scheduling.py`, `restoration.py`); step-by-step restoration curve | M4/M5 | ILM-once and AND-semantics tests |
| R2 Update on damage/crew/access/network change | single `Scenario` state; ranked, quantified "what moves this estimate" | M4/M8 | consistency test; demo story numbers from `pick_examples.py` |
| R2 Model output vs confirmed info | Observed/Assumed/Simulated labels on cards, evidence and message parts; assumptions never in "What we know" | M4/M8 | tests on message parts |
| R3 Guidance by risk and duration, safety labeled, declared needs | `guidance.py` with sources (C4); medical / mobility / critical-facility needs (C5) | M4/M8 | renders for all band×duration cells; needs tests |
| R4 Alert eligibility rules; timing, channel, escalation | `alerts.py` rules E1–E8; eligibility vs enrollment; all triggers shown with active state | M4/M8 | escalation tests |
| R4 Four messages | `messages.py`; rule E5 dead-band; would-send markers; opt-out gating | M4/M8 | all render, no unresolved placeholders, dead-band test |
| R5 Reasons, confidence, assumptions, missing data, privacy, fallback | Why & limits panel (validation numbers from `results/*.json`, what changed after testing), "being assessed" fallback, PII guard, audit log | M3/M4/M8 | bundle privacy test; fallback tests |
| Priority locations (life-support, hospitals, fire, ambulance) | `priority.py` facility counts per zone; L10 weighting toggle; life-support and facility restoration times | M8 | priority tests |
| Map experience | `viz.zone_map` zone centroids with the path highlighted | M8 | renders for every circuit with coordinates |
| Judges change ≥ 1 input | sidebar scenario controls; demo story with measured steps | M4/M8 | `data/demo/examples.json` |
| Benefit: fewer customer-outage hours with limited crews | `dependency_aware` vs `fifo` vs `customers_first` on identical seeded instances | M5 | `results/benchmark_headline.json` + summary; solver status |
| Benefit: operational realism | repair completion ≠ service restoration; dependency visual; step restoration | M5/M6 | demo shows service later than local repair |
| Scaling | `scripts/scale_probe.py` → `results/scale_probe.md` | M8 | measured, labeled |

## Cut first if time runs short (what actually happened)
1. Streamlit Cloud deploy — kept (live). 2. Monte-Carlo simulated damage — kept (seeded draws, L8). 3. Swap improvement
pass — kept, measured at 1.4 % mean gain, capped for determinism (L9). 4. Gantt — kept. 5. Pole-age feature — kept, shown
to add little (R3). 6. Facility proximity — cut in M7, built in M8 as counts per zone (A9). 7. Graph-cut zone
assignment — kept. 8. Lat/lon entry — kept, with an out-of-area guard. 9. Benchmark breadth — 46 circuits.
