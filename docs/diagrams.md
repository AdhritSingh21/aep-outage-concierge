# Diagrams

Three Mermaid diagrams that GitHub renders inline: the architecture and data flow, the customer journey with the
alert schedule, and the development process. Colours follow the project's three labels: **Observed** (organizer data
as-is, blue), **Assumed** (a modeling choice listed in [ASSUMPTIONS.md](../ASSUMPTIONS.md), amber) and **Simulated**
(the output of a scenario run, green). Grey dashed boxes are code or plumbing that carries no number of its own.

Every figure quoted in a node is copied from a file a script produced (`data/data_report.md`,
`results/benchmark_summary.md`, `results/scale_probe.md`, `data/demo/examples.json`) or from `git log`; the sources
are listed under each diagram. Rule ids E1–E8 are the alert rules in `concierge/alerts.py`.

## (a) Architecture and data flow

```mermaid
flowchart TB
    classDef observed fill:#dce9f7,stroke:#1f5f99,stroke-width:1.5px,color:#0b2a44
    classDef assumed fill:#fdf0d5,stroke:#b07d0a,stroke-width:1.5px,color:#4a3600
    classDef simulated fill:#e2f3df,stroke:#2e7d32,stroke-width:1.5px,color:#0e3311
    classDef code fill:#f4f4f4,stroke:#666,stroke-dasharray:4 3,color:#222
    classDef ui fill:#ffffff,stroke:#333,stroke-width:2px,color:#111

    subgraph ORG["Organizer package (read locally, never committed)"]
        direction LR
        GIS["12 GIS shapefile layers<br/>Data1: customers, poles, primary lines, fuse banks, ARCOS damage, ambulance, fire, hospitals<br/>Data2: secondary lines, transformers, switches, recloser banks"]:::observed
        UPT["matched_upstream.txt<br/>premise_nu → transformer → fuses / reclosers → breaker<br/>117,595 of 117,642 premises have a path"]:::observed
        XLS["Install_Labor_HackOHIO.xlsx<br/>CU averages, adders, ILM = 4.5"]:::observed
    end

    subgraph DATA["Data layer · scripts/prepare_data.py"]
        direction LR
        SIO["shapefile_io.py<br/>stdlib .shp / .dbf reader, no GDAL<br/>WGS84 kept, USGS Web Mercator reprojected"]:::code
        ETL["etl.py<br/>typed parquet tables, placeholder repair dates nulled"]:::code
        LWB["labor_workbook.py<br/>CU, adders, connectors, ILM"]:::code
        PROC["data/processed/*.parquet (16 tables)<br/>data/data_report.md"]:::observed
    end

    subgraph TOPO["Topology and exposure · Assumed rules A2–A6 applied to Observed data"]
        direction LR
        TOP["topology.py<br/>protective-zone tree per circuit<br/>7,190 zones on 110 circuits, every pathed premise in one leaf zone"]:::assumed
        EXP["exposure.py<br/>canonical circuit ids (A3) · segment → zone by graph cut + transformer vote (A4)<br/>damage → circuit → zone (A5) · overhead miles per zone"]:::assumed
        PRI["priority.py<br/>facilities → counts per zone, no names or coordinates (A9)"]:::assumed
    end

    subgraph ANA["Analytics"]
        direction LR
        RISK["risk.py<br/>Poisson regression prior with overhead-mile offset (R2)<br/>empirical-Bayes zone rates λ = μ·(α+n)/(α+μ) (R3)<br/>P(outage) = 1 − exp(−severity · Σ λ on the path) (R4)<br/>bands, factors, confidence, no-path fallback (R5, R6)"]:::assumed
        LAB["labor.py<br/>task hours = (Σ bundle CU hours + adders) × ILM × labor factor<br/>ILM applied exactly once, covers drive and set-up (L2)"]:::assumed
        RVAL["results/risk_validation.md<br/>leave-one-circuit-out validation"]:::assumed
    end

    subgraph ENG["Scenario engine · every output is a pure function of one Scenario"]
        direction LR
        SCN["scenario.py<br/>Scenario: premise, severity, hours to storm, crews, access, damage source, labor factor, crew start delay, declared needs, opt-in"]:::code
        DSH["scenarios.py · DamageSets historical<br/>Observed Feb-2025 records on the circuit, one task per record"]:::observed
        DSS["scenarios.py · DamageSets simulated<br/>seeded Poisson draws from the zone rates, 20 draws"]:::simulated
        SCH["scheduling.py<br/>Instance → FIFO · customers-per-hour · dependency-aware (Horn tree-merge + swap pass)<br/>ScheduleResult + SolverStatus"]:::simulated
        RES["restoration.py<br/>RestorationEstimate: low / mid / high = labor × 0.75 / 1 / 1.5 (L8)<br/>repair completed ≠ service restored · affected / not affected / unknown"]:::simulated
    end

    subgraph CUST["Customer layer"]
        direction LR
        ALR["alerts.py<br/>eligibility, enrollment, timing, channels, escalation<br/>rules E1–E8 (C1)"]:::assumed
        GUI["guidance.py<br/>safety first, then preparation by band × duration and declared needs<br/>each item cites its source"]:::assumed
        MSG["messages.py<br/>four stages: pre-storm, outage confirmed, estimate updated, restoration complete"]:::code
        EVD["evidence.py<br/>why & limits, assumptions in play, what changed after testing"]:::code
        VIZ["viz.py<br/>dependency chain, zone map, restoration curves, crew Gantt"]:::code
    end

    JRN["journey.py · run(Scenario) → JourneyResult<br/>only SAFE_CUSTOMER_FIELDS reach the UI"]:::code

    subgraph APP["app.py · Streamlit"]
        direction LR
        SIDE["Sidebar: identify the location (example premises, premise id, circuit, map coordinates) and change the Scenario"]:::ui
        T1["Your outlook"]:::ui
        T2["Messages"]:::ui
        T3["Operations view"]:::ui
        T4["Why & limits"]:::ui
        T5["Data & results"]:::ui
    end

    subgraph SCR["Scripts that produce every quoted number"]
        direction LR
        BEN["run_benchmark.py → results/benchmark.csv, benchmark_summary.md, benchmark_headline.json, benchmark.png"]:::simulated
        PRB["scale_probe.py → results/scale_probe.md (Measured runtimes)"]:::code
        PIX["pick_examples.py → data/demo/examples.json (example premises, demo story)"]:::code
        SLM["slim_demo_bundle.py → data/demo (9 circuits, anonymized ids, coordinates rounded)"]:::observed
    end

    GIS --> SIO --> ETL
    UPT --> ETL
    XLS --> LWB --> PROC
    ETL --> PROC
    PROC --> TOP --> EXP
    PROC --> PRI
    EXP --> RISK --> RVAL
    PROC --> LAB
    EXP --> DSH
    RISK --> DSS
    SCN --> DSH
    SCN --> DSS
    LAB --> SCH
    DSH --> SCH
    DSS --> SCH
    PRI -->|critical-load weight, optional| SCH
    SCH --> RES
    RISK --> JRN
    RES --> JRN
    ALR --> JRN
    GUI --> JRN
    MSG --> JRN
    EVD --> JRN
    VIZ --> JRN
    SIDE -->|Scenario| JRN
    JRN --> T1
    JRN --> T2
    JRN --> T3
    JRN --> T4
    JRN --> T5
    SCH --> BEN --> T5
    PRB --> T5
    PIX --> SIDE
    PROC --> SLM --> SIDE

    subgraph KEY["Legend"]
        direction LR
        K1["Observed · organizer data as-is"]:::observed
        K2["Assumed · modeling choice, listed in ASSUMPTIONS.md"]:::assumed
        K3["Simulated · output of a scenario run"]:::simulated
        K4["code / plumbing"]:::code
    end
```

Sources for the figures in the nodes: 117,595 of 117,642 premises with a path, 7,190 zones, 110 circuits
(`data/data_report.md`, "Join and unit checks" and "Tables"); ILM = 4.5 (`concierge/config.py`, from the workbook);
20 simulated draws (`results/scale_probe.md`, rows 13–14); 16 processed tables (`data/data_report.md`, "Tables");
9 demo circuits (`data/demo/README.md`). Rule and assumption ids refer to `ASSUMPTIONS.md` and `concierge/alerts.py`.

## (b) Customer journey and alert schedule

Timing, channels and escalation triggers are quoted from `concierge/alerts.py` (`RULES`, `decide()`); the
estimate-updated dead-band is `UPDATE_THRESHOLD_H = 2.0` in `concierge/messages.py`. All of them are **Assumed**
rules (C1), not AEP policy.

```mermaid
flowchart TB
    classDef stage fill:#ffffff,stroke:#333,stroke-width:2px,color:#111
    classDef rule fill:#fdf0d5,stroke:#b07d0a,color:#4a3600
    classDef esc fill:#fde2e1,stroke:#b3261e,color:#4a0f0c
    classDef gate fill:#f4f4f4,stroke:#666,stroke-dasharray:4 3,color:#222
    classDef event fill:#dce9f7,stroke:#1f5f99,color:#0b2a44

    START(["Customer identified by anonymized premise_nu<br/>risk band, restoration estimate and declared needs come from one Scenario"]):::event
    ENR{"Enrolled in outage alerts?<br/>rule E1: every customer qualifies, messages go only to customers who opted in"}:::gate
    NONE["Status shown: Eligible · not enrolled<br/>nothing is sent, the four messages are shown as a preview only"]:::gate
    PREGATE{"Pre-storm alert due?<br/>rule E2: band ≥ Moderate, or a need declared, or expected restoration > 24 h"}:::gate
    SKIP["No proactive pre-storm alert (rule E2)<br/>outage alerts still apply if an outage occurs"]:::gate

    START --> ENR
    ENR -->|no| NONE
    ENR -->|yes| PREGATE
    PREGATE -->|no| SKIP --> STORM
    PREGATE -->|yes| PRE

    subgraph S1["Stage 1 · Pre-storm"]
        direction TB
        PRE["Pre-storm message<br/>what we know · risk band and probability · what you can do<br/><b>when:</b> 24 h before the storm, or now if the storm is ≤ 24 h away<br/>any declared need: 48 h before, or now if ≤ 48 h (rules E3 · E7 · E8)<br/><b>channels:</b> SMS + email, voice call added when a need is declared or the estimate exceeds 24 h (rule E4)<br/>rules E2 · E3 · E4 · E5"]:::stage
        REM["Pre-storm reminder<br/><b>when:</b> 6 h before the storm, dropped when the storm is ≤ 6 h away<br/><b>channel:</b> SMS<br/>rule E5"]:::stage
        PRE --> REM
    end

    STORM(["Storm arrives · damage reported on the circuit"]):::event
    REM --> STORM

    subgraph S2["Stage 2 · Outage confirmed"]
        OUT["Outage-confirmed message<br/>what we know · restoration range, not a single time · repair done ≠ lights on when an upstream zone is still damaged<br/><b>when:</b> within 15 min of the outage being confirmed<br/><b>channels:</b> same channels as stage 1<br/>rules E1 · E4 · E5"]:::stage
    end
    STORM --> OUT

    subgraph S3["Stage 3 · Estimate updated"]
        UPD["Estimate-updated message<br/>new range vs old range, and why (crews, access, labor hours, crew start delay, damage set)<br/><b>when:</b> only when the midpoint moves more than 2 h or the band changes (UPDATE_THRESHOLD_H = 2.0)<br/><b>channels:</b> same channels<br/>rule E5"]:::stage
    end
    OUT --> UPD
    UPD -->|another change that passes the dead-band| UPD

    subgraph S4["Stage 4 · Restoration complete"]
        DONE["Restoration-complete message<br/>service restored, what to check at home, how to report a problem<br/><b>when:</b> at restoration<br/><b>channels:</b> same channels<br/>rule E5"]:::stage
    end
    UPD --> DONE

    subgraph ESC["Escalation triggers evaluated at every stage (rules E3, E6, E7, E8)"]
        direction TB
        E3["Declared medical need and estimate > 8 h → human outreach / wellness check, medical-equipment battery guidance (E3)"]:::esc
        E7["Declared mobility or accessibility need and estimate > 8 h → transport to an accessible warming / cooling center offered, voice call (E7)"]:::esc
        E8["Declared critical facility → facility-operations contact, generator fuel check reminder at 8 h, status on every update (E8)"]:::esc
        E6A["Estimate > 24 h → warming / cooling center and food-safety guidance, voice call (E6, E4)"]:::esc
        E6B["Estimate > 72 h → daily updates and community resources (E6)"]:::esc
        E6C["Low confidence or no upstream path → a person reviews before any time estimate is sent, customer gets a 'we are assessing' message (E6)"]:::esc
        E6D["Difficult access on the circuit → the delay is explained in the estimate-updated message (E6)"]:::esc
    end
    OUT -.-> ESC
    UPD -.-> ESC

    DONE --> PRIV
    subgraph PRIV["Always"]
        direction LR
        P1["No address, road, meter or annotation field is ever rendered, premises appear as anonymized ids, city and circuit are the only location words"]:::rule
        P2["Every number is tagged Observed, Assumed or Simulated, safety lines come first and cite their source, 911 for medical emergencies"]:::rule
    end
```

Where each statement comes from: enrollment vs eligibility (`alerts.py` module docstring and `AlertDecision.status_text`);
E1–E8 (`alerts.py`, `RULES`); the 24 h / 48 h / now and 6 h reminder timing, "within 15 min", "midpoint moves > 2 h or
the band changes" and "at restoration" (`alerts.py`, `decide()`, the `schedule` list); the seven escalation rows
(`alerts.py`, `decide()`, the `escalations` list); the four message stages (`messages.py`, `STAGES`); privacy
(`concierge/config.py`, `PII_FIELDS`; `journey.py`, `SAFE_CUSTOMER_FIELDS`); safety sources (`guidance.py`, `SOURCE_LABELS`).

## (c) Development process

Milestones M0–M8 follow `PLAN.md`. Each box carries the short hash and the commit time from
`git log --format='%h %ad %s' --date=iso` (2026-10-24, UTC−4; the full output is in the table below). A commit time is
when a milestone was committed, not when work on it started; M0 through the first M7 commit all landed inside one hour
(16:19–17:16) and the last M7 commit (the live link) at 21:33, so the order is real but the spacing is not effort. The lower row is the "Cut first if time runs short" list from `PLAN.md` with what
actually happened to each item, linked to the milestone commit that contains it (found with `git log -S<symbol>`).

```mermaid
flowchart LR
    classDef done fill:#e2f3df,stroke:#2e7d32,stroke-width:1.5px,color:#0e3311
    classDef wip fill:#fdf0d5,stroke:#b07d0a,stroke-width:1.5px,color:#4a3600
    classDef kept fill:#e2f3df,stroke:#2e7d32,stroke-dasharray:4 3,color:#0e3311
    classDef deferred fill:#fde2e1,stroke:#b3261e,stroke-dasharray:4 3,color:#4a0f0c

    M0["M0 · 16:19 · 3eb8200<br/>scaffold, CLAUDE.md working agreement, PLAN.md, data access script"]:::done
    M1["M1 · 16:30 · da154e6<br/>stdlib shapefile reader, ETL to parquet, labor workbook parser, join / CRS / unit tests, data report"]:::done
    M2["M2 · 16:38 · b1e64bf<br/>protective-zone tree, canonical circuit ids, segment / pole / damage attribution, zone and circuit exposure"]:::done
    M3["M3 · 16:48 · ece9eb4<br/>circuit Poisson rate model, empirical-Bayes zone hazards, path-aggregated risk with factors, confidence, fallback, LOO validation"]:::done
    M4["M4 · 17:07 · d7c2ba2<br/>Scenario state, labor bundles with ILM, damage sets, scheduling engine, restoration ranges, alerts, guidance, four messages, dependency chain, Streamlit journey"]:::done
    M5["M5 · 17:11 · a8e1fba<br/>scheduling benchmark across circuits, measured gains and losses in results/, scale sanity tests"]:::done
    M6["M6 · 17:13 · f416704<br/>demo story preset with repair-vs-service gap, demo bundle covering example and demo circuits"]:::done
    M7["M7 · 17:16 → 21:33 · 8a0cc54, f55d5f8, dfe6483<br/>fresh-clone smoke test, repository public, logged-out link check, live Streamlit Community Cloud link"]:::done
    M8["M8 · branch claude/festive-hamilton-j2gxrk, not yet committed when this file was written<br/>judging-readiness fixes from the pre-judging audit: delay counted once, crash guard, example premises and demo story chosen by script, scale probe, benchmark number hygiene, priority locations, diagrams and pitch"]:::wip

    M0 --> M1 --> M2 --> M3 --> M4 --> M5 --> M6 --> M7 --> M8

    subgraph CUT["PLAN.md · Cut first if time runs short · what happened"]
        direction TB
        C1["1 Streamlit Cloud deploy · KEPT, live link in README (M7, dfe6483)"]:::kept
        C2["2 Monte-Carlo simulated damage · KEPT, seeded Poisson draws per zone, 20 draws (M4)"]:::kept
        C3["3 Swap improvement pass · KEPT, mean gain 1.39 %, max 20.42 % over Horn's order (M4 code, M5 measurement)"]:::kept
        C4["4 Gantt · KEPT, crew timeline in the Operations view (M4)"]:::kept
        C5["5 Pole-age feature · KEPT in the Poisson prior, adds little: Spearman 0.658 vs 0.644 exposure-only (M3)"]:::kept
        C6["6 Facility proximity · DEFERRED through M7 (layers loaded, unused) · M8 adds facility counts per zone with no coordinates"]:::deferred
        C7["7 Graph-cut zone assignment · KEPT, transformer vote 57,735 segments, boundary LCA 3,254, overhead miles conserved on 110 of 110 circuits (M2)"]:::kept
        C8["8 Lat / lon entry · KEPT as map coordinates → nearest premise (M4)"]:::kept
        C9["9 Benchmark breadth · KEPT, 46 circuits × 3 damage sets × crew counts = 306 instances (M5)"]:::kept
    end

    C7 -.-> M2
    C5 -.-> M3
    C2 -.-> M4
    C4 -.-> M4
    C8 -.-> M4
    C3 -.-> M5
    C9 -.-> M5
    C1 -.-> M7
    C6 -.-> M8
```

`git log --format='%h %ad %s' --date=iso` on this branch (verbatim):

| hash | committed (UTC−4) | subject |
|---|---|---|
| `3eb8200` | 2026-10-24 16:19:11 | M0: project scaffold, working agreement, plan, data access script |
| `da154e6` | 2026-10-24 16:30:20 | M1: stdlib shapefile reader, ETL to parquet, labor workbook parser, join/CRS/unit tests, data report |
| `b1e64bf` | 2026-10-24 16:38:58 | M2: protective-zone tree, canonical circuit ids, segment/pole/damage attribution, zone and circuit exposure |
| `ece9eb4` | 2026-10-24 16:48:52 | M3: circuit Poisson rate model with exposure offset, empirical-Bayes zone hazards, path-aggregated customer risk with factors, confidence and fallback; LOO validation report |
| `d7c2ba2` | 2026-10-24 17:07:12 | M4: scenario state, labor bundles with ILM and crew size, damage sets, scheduling engine (FIFO, customers-first, dependency-aware), restoration ranges, alerts, guidance, four messages, dependency chain, Streamlit journey |
| `a8e1fba` | 2026-10-24 17:11:45 | M5: scheduling benchmark across circuits (FIFO, customers-first, dependency-aware with/without swap pass), results and README with measured gains and losses, demo premise selection, scale sanity tests |
| `f416704` | 2026-10-24 17:13:52 | M6: demo story preset with repair-vs-service gap, demo bundle covering example and demo circuits, hosting notes |
| `8a0cc54` | 2026-10-24 17:16:42 | M7: submission readiness - hosting notes, scratch ignore, fresh-clone smoke test passed (unit tests, demo-bundle journey, headless Streamlit) |
| `f55d5f8` | 2026-10-24 17:18:38 | M7: repository public, logged-out link check passed |
| `dfe6483` | 2026-10-24 21:33:01 | M7: add live Streamlit Community Cloud demo link to README |

Sources for the cut-first row: swap-pass gain and the 306-instance count (`results/benchmark_summary.md`, "Solver
status" and the opening paragraph); Spearman 0.658 vs 0.644 (`results/risk_validation.md`, "Leave-one-circuit-out
validation"); segment attribution counts and overhead-mile conservation (`data/data_report.md`, "Derived topology");
20 draws (`results/scale_probe.md`, rows 13–14). Which commit introduced each feature: `git log -S"def gantt" --
concierge/viz.py`, `-S"def improve_by_swaps" -- concierge/scheduling.py`, `-S"simulated" -- concierge/scenarios.py`,
`-S"Map coordinates" -- app.py` (all `d7c2ba2`), `-S"pole_year_centered" -- concierge/risk.py` (`ece9eb4`),
`-S"def assign_segment_zones" -- concierge/exposure.py` (`b1e64bf`), `-S"streamlit.app" -- README.md` (`dfe6483`).

## Rendering check

Each block was rendered with `@mermaid-js/mermaid-cli` 11.4.2 (`mmdc -i <block>.mmd -o <block>.svg`, headless
Chrome with `--no-sandbox`) before this file was committed; the rendering log is kept outside the repository. GitHub
renders fenced ```` ```mermaid ```` blocks with its own Mermaid build, so if a diagram ever fails to render on
GitHub, open the block in https://mermaid.live to see the parser message.
