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
      Done when the journey runs for the four sample premises, every R1–R5 row below is exercised, scenario changes
      visibly update outputs, no PII renders, the no-path premise shows the Low-confidence fallback.
- [x] **M5 — Priority 2.** `customers_first` and `dependency_aware` policies, `SolverStatus`, metrics without
      double-counting, `scripts/run_benchmark.py` (all circuits with ≥ 3 Feb-2025 records × crews × seeds),
      Operations tab. Done when scheduling tests pass (feasibility, AND semantics, no double counting, toy optimality,
      brute-force parity ≤ 7 tasks, identical inputs across policies) and README reports measured results incl. losses.
- [x] **M6 — Priority 3.** Demo story preset (crews 2→4, access off→on), evidence/results panel, estimate-updated
      message diff. Done when the consistency test proves one `Scenario` change updates schedule, range, explanation
      and message together, and the dependency chain shows a blocking upstream zone in the demo.
- [x] **M7 — Submission readiness.** Repository made public on 2026-10-24 and checked logged-out; the Streamlit
      Community Cloud deploy remains a manual step for the account owner (README, "Hosting a demo link"). README complete, `ASSUMPTIONS.md`, `data/demo` bundle, fresh-clone smoke test,
      PII/secret scan, optional Streamlit Cloud deploy (ask before making the repo public), logged-out link check.

## Requirement traceability

| Brief requirement / MVP step | Feature (module) | Milestone | Judged / measured by |
|---|---|---|---|
| R1 Accept premise, circuit, or map location | sample premises, searchable anonymized ID, circuit, lat/lon → nearest premise (`app.py`) | M4 | all modes reach the same journey |
| R1 Risk estimate / band + signals | `risk.py` circuit rate model + upstream-path aggregation; severity input | M3 | `results/risk_validation.md` (leave-one-circuit-out) |
| R1 Contributing factors + uncertainty | factors, confidence, data flags | M3/M4 | every output carries confidence + flags |
| R2 Restoration range | `restoration.py` range (labor ×0.75–1.5; best/worst policy) | M4 | lower ≤ mid ≤ upper |
| R2 Damage, upstream dependencies, step restoration, labor, travel, nonproductive time | `labor.py` + ILM; zone tree; AND-restoration (`scheduling.py`, `restoration.py`) | M4/M5 | ILM-once and AND-semantics tests |
| R2 Update on damage/crew/access/network change | single `Scenario` state | M4/M6 | consistency test |
| R2 Model output vs confirmed info | Observed/Assumed/Simulated labels | M4 | string check |
| R3 Guidance by risk and duration, safety labeled, declared needs | `guidance.py`; medical-need toggle | M4 | renders for all band×duration cells |
| R4 Alert eligibility rules; timing, channel, escalation | `alerts.py` rule table in UI | M4 | escalation test |
| R4 Four messages | `messages.py` | M4 | all render, no unresolved placeholders |
| R5 Reasons, confidence, assumptions, missing data, privacy, fallback | Why & limits tab, evidence panel, fallback, PII guard | M3/M4/M6 | PII grep test; fallback test |
| Judges change ≥ 1 input | sidebar scenario controls | M4 | demo story |
| Benefit: fewer customer-outage hours with limited crews | `dependency_aware` vs `fifo` vs `customers_first` on identical seeded instances | M5 | `results/benchmark.csv` + summary; solver status |
| Benefit: operational realism | repair completion ≠ service restoration; dependency visual | M5/M6 | demo shows service later than local repair |

## Cut first if time runs short
1. Streamlit Cloud deploy. 2. Monte-Carlo simulated damage → deterministic scaled historical. 3. Swap improvement pass.
4. Gantt → completion table. 5. Pole-age feature. 6. Facility proximity. 7. Graph-cut zone assignment → nearest
transformer. 8. Lat/lon entry. 9. Benchmark breadth.
