# Assumptions register

Every number the app shows is tagged **Observed** (organizer data as-is), **Assumed** (a modeling choice listed here),
or **Simulated** (the output of a scenario run). This file is the authoritative list of the Assumed items.

## Data and joins (verified, see data/data_report.md)
- A1. `matched_upstream.txt` keys on the customer `premise_nu` (9 digits, zero-padded). Customers without a path
  (47 of 117,642) use the circuit-average fallback and are flagged Low confidence.
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
- A8. The customer's location is the shapefile point (the service-point fields differ by < 100 m for 99.6 %).

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
  Feb-2025 count; the dispersion is estimated by negative-binomial maximum likelihood over zones. A floor of 0.02
  on the prior mean keeps zones without overhead exposure well-defined.
- R4. Any damage on a customer's upstream path interrupts their service (tie switches and back-feeds are not
  modeled). P(outage | storm) = 1 − exp(−severity × Σ zone rates on the path).
- R5. Severity options are multipliers on the Feb-2025 rates: 0.5, 1, 2, 3. Risk bands: Low < 15 %,
  Moderate 15–40 %, High 40–70 %, Very high ≥ 70 %.
- R6. Confidence: High needs a resolved path, ≥ 10 reference-storm records on the circuit and pole-year coverage
  ≥ 50 %; Medium needs a resolved path; Low is the circuit-average fallback.

## Labor and restoration
- L1. One damage record is one repair task with quantity 1 (the `Media` column is not interpreted as a quantity).
- L2. Task person-hours = (bundle hours + adders) × ILM × labor multiplier, where the bundles combine the workbook's
  CU averages per damage type (see `concierge/labor.py` for the exact composition) and ILM = 4.5 from the workbook.
  ILM is applied exactly once and already covers drive, safety briefing and set-up time, so no separate travel
  time is added. Workbook hours are read as person-hours; a crew of 4 works a task in parallel, so task clock
  hours = person-hours ÷ 4 (a pole replacement ≈ 34 person-hours ≈ 8.5 clock hours for one crew).
- L3. Adders from the workbook: access issue → 0.5 h; backyard bucket → 0.5 h; track hoe / yard dog / backhoe →
  1.312 h; other special equipment → 0.5 h; the scenario's "difficult access" toggle adds 0.5 h to every task.
- L4. Default transformer size 25 kVA; default pole size Medium when the height is unknown.
- L5. Crews are identical 4-person crews, dedicated to the customer's circuit, start together at the scenario's
  start time, and never run out of material. A task is done by one crew without interruption. The default crew
  count per circuit is calibrated so that the Feb-2025 damage set would clear in about 72 clock hours under FIFO
  (the observed report→repair gaps were 0–6 days), bounded to 1–30; judges can change it freely.
- L6. A customer's service is restored when every damaged zone on their upstream path is repaired (and their own
  service line, if it was damaged). Repairs may be done in any physical order; "repair completed" and "service
  restored" are therefore different times and both are shown.
- L7. Repair dates in the damage file are day-granularity and are used only to select the reference event and as a
  coarse sanity check on the restoration scale; never as outage durations. `Repaired_D = 2025-10-24` on
  unrepaired rows is a placeholder and is treated as null.
- L8. Restoration ranges: low / mid / high all use the dependency-aware schedule with labor × 0.75 / × 1.0 / × 1.5
  (uniform scaling leaves the ordering unchanged, so the schedule scales exactly). Simulated damage sets use seeded
  Poisson draws per zone with the circuit's empirical damage-type and access-issue mix; the midpoint is the median
  service-restoration time over draws in which the customer is affected, low/high apply the labor factors to the
  10th/90th-percentile draws, and the displayed schedule is the draw closest to the median.

## Alerts, guidance, privacy
- C1. Alert rules are documented in `concierge/alerts.py` and shown in the app; they are the team's assumptions,
  not AEP policy.
- C2. The account's life-support flag is shown to that customer only as "declared medical need on file" and can be
  toggled; nothing about medical or accessibility needs is inferred.
- C3. No address, road, meter or annotation fields are ever rendered; premises are identified by anonymized ids.
