# Assumptions register

Every number the app shows is tagged **Observed** (organizer data as-is), **Assumed** (a modeling choice listed here),
or **Simulated** (the output of a scenario run). This file is the authoritative list of the Assumed items. Runtime
measurements in `results/scale_probe.md` carry a fourth, narrower label, **Measured**.

## Data and joins (verified, see data/data_report.md)
- A1. `matched_upstream.txt` keys on the customer `premise_nu` (9 digits, zero-padded). Customers without a path
  (47 of 117,642) use the circuit-average fallback and are flagged Low confidence; the same fallback applies when every
  zone on a path is missing from the zone table (one premise in the demo bundle), and the page never fails on it.
- A2. Protective zones are the fuse, recloser, sectionalizer and circuit-breaker tokens in each path; step
  transformers are pass-through. A zone's parent is the most frequent next token across paths (45 zones had
  conflicting parents in the organizer data; the majority wins).
- A3. The anonymized `circuit_id` differs between GIS layers for ~14 circuits. The transformer layer's
  `(station, cir_label)` key is unambiguous and agrees with the customer layer, so every other layer is remapped to
  it (`circuit_id_layer` keeps the original).
- A4. Primary segments are assigned to zones by cutting the structure graph at device locations and voting with the
  transformers inside each piece; pieces without transformers use the lowest common ancestor of their boundary
  devices; leftovers use the nearest transformer's zone; cross-circuit leftovers default to the breaker zone.
- A5. Damage records are attributed to a circuit by the `(station, cir_label)` key; when the point lies more than
  300 m from any line of that circuit, the nearest line's circuit wins. Zones come from the nearest primary segment
  (or the nearest fuse/recloser within 40 m for fuse-bank/recloser-bank records). `NEAR_FID/NEAR_DIST` are ignored.
- A6. Poles are tied to segments by shared location number, else to the nearest primary segment within 60 m;
  service-only poles stay unassigned and are not part of primary exposure.
- A7. `report_len` is feet; AEP layers are WGS84; USGS facility layers are Web Mercator and are reprojected.
- A8. The customer's location is the shapefile point (the service-point fields differ by < 100 m for 99.6 %). The
  committed demo bundle rounds it to 3 decimals (about 100 m) and carries no customer or structure identifiers.
- A9. A critical facility (hospital, fire station, ambulance service from the USGS layers) is served from the zone of
  the nearest primary segment when that segment is within 2,000 m (`concierge/priority.py`); facilities farther from
  any line are not mapped (31 of 52 in the demo bundle). Only counts per zone are stored and shown; names and
  coordinates never enter the bundle or the UI.

## Reference storm and risk model
- R1. The reference storm is Feb 12–18 2025 (1,254 of 1,659 damage records). Its footprint is assumed to have
  covered every circuit in the extract, so zero-damage circuits are informative zeros. One event cannot separate
  "where the storm hit" from "how vulnerable the lines are"; the model is a damage-intensity estimate, not a forecast.
- R2. Line damage (primary, secondary, pole records) follows a Poisson process with exposure = overhead primary
  miles; the rate per mile is a Poisson regression on circuit features (overhead share, lateral share, mean known
  pole year, pole-year coverage). Equipment damage (fuse, recloser, switch, transformer, other) uses one territory
  rate per equipment unit (overhead transformers + the zone's own device). Service-drop damage uses one territory
  rate per overhead-service customer; underground service has none; unknown service gets half.
- R3. Zone rates are gamma-Poisson posterior means that combine the circuit model (prior) with the zone's own
  Feb-2025 count; the dispersion is estimated by negative-binomial maximum likelihood over zones (parametric
  empirical Bayes: Efron & Hastie 2016 ch. 6; Hilbe 2011; Clayton & Kaldor 1987). A floor of 0.02 on the prior mean
  keeps zones without overhead exposure well-defined. Leave-one-circuit-out validation shows the circuit features add
  little over exposure alone (`results/risk_validation.md`); the zone's own count carries the estimate.
- R4. Any damage on a customer's upstream path interrupts their service (tie switches and back-feeds are not
  modeled). P(outage | storm) = 1 − exp(−severity × Σ zone rates on the path).
- R5. Severity options are multipliers on the Feb-2025 rates: 0.5, 1, 2, 3. Risk bands: Low < 15 %,
  Moderate 15–40 %, High 40–70 %, Very high ≥ 70 %. The band is decided on the rounded percent so the word and the
  number on the card never disagree; probabilities at or above 99.5 % print as "> 99 %".
- R6. Confidence grades data coverage, not forecast accuracy: High needs a resolved path, ≥ 10 reference-storm records
  on the circuit and pole-year coverage ≥ 50 %; Medium needs a resolved path; Low is the circuit-average fallback,
  which the app shows as "being assessed" and never sends as a number (rule E6).

## Labor and restoration
- L1. One damage record is one repair task with quantity 1 (the `Media` column is not interpreted as a quantity).
- L2. Task person-hours = (bundle hours + adders) × ILM × labor multiplier, where the bundles combine the workbook's
  CU averages per damage type (see `concierge/labor.py` for the exact composition) and ILM = 4.5 from the workbook.
  ILM is the organizer's indirect-labor (travel and nonproductive time) multiplier; it is applied exactly once and no
  separate travel time is added. Workbook hours are read as person-hours; a crew of 4 works a task in parallel, so task
  clock hours = person-hours ÷ 4 (a pole replacement ≈ 34 person-hours ≈ 8.5 clock hours for one crew).
- L3. Adders from the workbook: access issue → 0.5 h; backyard bucket → 0.5 h; track hoe / yard dog / backhoe →
  1.312 h; other special equipment → 0.5 h; the scenario's "difficult access" toggle adds 0.5 h to every task.
- L4. Default transformer size 25 kVA; default pole size Medium when the height is unknown.
- L5. Crews are identical 4-person crews, dedicated to the customer's circuit, start together, and never run out of
  material. A task is done by one crew without interruption. The default crew count per circuit is calibrated so that
  the Feb-2025 damage set would clear in about 72 clock hours under FIFO (the observed report→repair gaps are
  computed in `results/benchmark_summary.md`), bounded to 1–30; judges can change it freely.
- L6. A customer's service is restored when every damaged zone on their upstream path is repaired (and their own
  service line, if it was damaged). Repairs may be done in any physical order; "repair completed" and "service
  restored" are therefore different times and both are shown.
- L7. Repair dates in the damage file are day-granularity and are used only to select the reference event and as a
  coarse sanity check on the restoration scale; never as outage durations. `Repaired_D = 2025-10-24` on
  unrepaired rows is a placeholder and is treated as null.
- L8. Restoration ranges: for the Observed set low / mid / high all use the dependency-aware schedule with labor
  × 0.75 / × 1.0 / × 1.5 (uniform scaling leaves the ordering unchanged, so the schedule scales exactly). Simulated
  damage sets use seeded Poisson draws per zone with the circuit's empirical damage-type and access-issue mix; low /
  mid / high are the 10th / 50th / 90th percentile service-restoration times over the draws in which the customer is
  affected (the labor factors are not applied on top), and the displayed schedule is the draw closest to the median.
- L9. The swap-improvement pass in the app stops after 1,500 objective evaluations (`config.SWAP_EVALUATION_CAP`), with
  a 1 s ceiling as a safety net, so the displayed schedule is the same on every machine that reaches the cap; the
  benchmark script uses a 0.5 s time budget and says so. `results/scale_probe.md` measures where the cap binds.
- L10. "Prioritize critical loads" (off by default and in every benchmark) adds 200 customers-equivalent per
  life-support customer and per critical facility to the dependency-aware ordering weights; the objective reported
  stays plain customer-outage hours, and the life-support and facility restoration times are reported alongside.
- L11. Time convention: every schedule is solved with crews starting at hour 0 ("after crews start"); the scenario's
  crew start delay is added exactly once, at display time, and every card, chain, message, alert threshold and the
  Operations view use that same total ("after the storm ends"). A unit test enforces it.
- L12. When the Observed Feb-2025 set leaves a customer's path untouched, the app also runs the seeded simulated draws
  at the chosen severity and shows "affected in P % of draws; when affected, X–Y h" as a secondary Simulated range,
  so a judge always sees a restoration answer or an explicit reason for not having one.

## Alerts, guidance, privacy
- C1. Alert rules E1–E8 are documented in `concierge/alerts.py` and shown in the app; they are the team's
  assumptions, not AEP policy. Eligibility (does the scenario qualify) is separate from enrollment (did the customer
  opt in): an opted-out customer still qualifies, nothing is sent, and the drafts say so. The estimate-updated message
  follows E5: nothing is sent for a move of 2 h or less.
- C2. The account's life-support flag pre-fills the "medical equipment" need for that premise and can be removed;
  nothing about medical, mobility or facility needs is inferred. In this prototype there is no login, so the flag is
  visible to anyone who enters the premise number (a production build would require authentication).
- C3. No address, road, meter or annotation fields are ever rendered; premises are identified by anonymized ids.
  Critical facilities appear only as counts per zone (A9). The map shows zone centroids only, never premise points.
- C4. Preparation and safety guidance is adapted from public sources; the figures are theirs, the wording is ours:
  - 35 ft from downed lines, treat every line as live: ESFI, "Downed Power Line Safety" (esfi.org); Puget Sound
    Energy, "Downed power lines" (pse.com); 10 m: Electrical Safety Authority Ontario (esasafe.com). AEP Ohio's own
    page says at least 10 ft and "treat as live" (aepohio.com/safety/power-lines); we keep the more conservative figure.
  - Generators outdoors, ≥ 20 ft from windows, doors and vents; never heat with a gas range or oven: CDC, "What to Do
    to Protect Yourself During a Power Outage" and the CDC carbon-monoxide PSA toolkit (cdc.gov).
  - Fridge about 4 h unopened, full freezer about 48 h, discard perishables above 40 °F for 2 h or more:
    FoodSafety.gov, "Food Safety During Power Outage"; USDA FSIS, "Keeping Food Safe During an Emergency"; FDA, "Food
    and Water Safety During Power Outages and Floods".
  - One gallon of water per person per day: Ready.gov (FEMA), "Water".
  - Full gas tank, refrigerated medications, medical devices that need power: American Red Cross, "Power Outage
    Safety" and the "Power Outage Preparedness Checklist" (PDF).
  - Never drive or walk through flooded roads; downed lines can energize water: NOAA National Weather Service, "Flood
    Safety for You and Your Family" / "Turn Around Don't Drown".
  Items marked "team assumption" (911 routing, the day-long-outage default, the upstream note) have no external source.
- C5. Needs a customer can declare for the session: powered medical equipment (E3), mobility / accessibility (E7),
  critical facility operator (E8). Each adds guidance items and alert handling; none is ever inferred from data.
- C6. Audit trail: every scenario run in a session is logged with its inputs and a hash of its outputs and can be
  downloaded from the Why & limits tab. No log leaves the session.
