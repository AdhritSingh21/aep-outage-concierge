# Demo bundle (data/demo)

Derived from the AEP HackOHI/O 2026 data package (https://github.com/hackohio/aep_challenge_hack26), restricted to 9 circuits: 0000005, 0000016, 0000018, 0000021, 0000026, 0000027, 0000058, 0000073, 0000114. Built by `concierge.etl.build_demo_bundle` (step 6 of `python scripts/prepare_data.py --source <organizer zip>`) and slimmed by `scripts/slim_demo_bundle.py`, which also generates this file. Every count below is **Observed** (rows of the committed tables); file sizes and the metre figures are Measured from the files on disk.

## Privacy (CLAUDE.md rule 4)

- Premises are identified by the anonymized `premise_nu` only. `customers.parquet` ships without `customer_id`, `loc`, `xfmr_loc`, `service_lon`, `service_lat` (internal location / customer identifiers and the second coordinate pair of the organizer layer).
- `customers.lon` / `customers.lat` are rounded to 3 decimals: one step of 0.001 degrees is 89 m east-west and 111 m north-south at the bundle's mean latitude (37.24 N), so a rounded point lies at most 71 m (half a grid cell) from the true location; the 16,145 premises fall on 7,041 distinct points. The coordinates are kept only so the app's find-by-coordinates lookup can pick the nearest premise.
- No table carries an address field (`concierge.config.PII_FIELDS`); city and circuit are the only location words the app shows. Transformer, device, pole and line tables keep their equipment `loc` identifiers because the zone tree and exposure joins are keyed on them; they are equipment labels, not customer data.
- Priority locations (`facilities.parquet`) are reduced to counts and distances per protective zone: columns `kind`, `circuit_id`, `zone_id`, `dist_m`; no facility name or coordinate column.
- Besides `customers.parquet`, `upstream.parquet` carries rows keyed on `premise_nu`: the organizer's matched_upstream.txt restricted to the bundle, i.e. the equipment identifiers (`eq_id`, `loc`) on each premise's path to the substation, from which the zone tree is rebuilt. The app does not read it at runtime (see the next bullet).
- Tables the app loads at runtime (`concierge.data.TABLES` plus the labor tables): `circuits`, `customers`, `damage`, `facilities`, `labor_adders`, `labor_cu`, `labor_meta`, `primary`, `transformers`, `zone_hazard`, `zones`. Kept only so the derived tables can be rebuilt from the bundle: `devices`, `labor_connectors`, `poles`, `primary_vertices`, `secondary`, `upstream`.

## Coverage

- 16,145 premises on 9 circuits; 16,142 have an upstream path on file (`has_path`), 3 use the no-path fallback branch.
- 1,714 protective zones (`zones.parquet`), 607 damage records (`damage.parquet`).
- 1 premise(s) have an upstream path naming zones that are not in `zones.parquet` (064667206): the path ends at the breaker of a circuit outside the bundle. Listed here so the app and the tests handle the case explicitly instead of discovering it on screen.

## Tables

17 parquet files, 5.6 MB in total.

| table | rows | size (KB) | columns |
|---|---|---|---|
| circuits | 9 | 16.0 | circuit_id, n_customers, n_with_path, n_life_support, n_overhead_service, city, oh_miles, ug_miles, lateral_miles, n_segments, n_zones, n_transformers, n_poles, pole_year_mean, pole_year_known_pct, n_damage_total, n_damage_ref, n_damage_ref_wire, n_damage_ref_pole, n_damage_ref_equipment, access_issue_rate_ref, affected_share_ref, station, cir_label |
| customers | 16,145 | 302.3 | premise_nu, circuit_id, service_kind, life_support, city, lon, lat, leaf_zone_id, path_zone_ids, path_depth, has_path |
| damage | 607 | 34.6 | damage_id, station, cir_label, report_date, repaired, repaired_date, repaired_date_was_placeholder, asset_type, damage_type, access_issue, special_equipment, media, in_reference_storm, lon, lat, circuit_id, zone_id, attribution_method, attribution_dist_m |
| devices | 1,981 | 83.2 | device_id, loc, kind, device_type, circuit_id, cir_label, station, normal_state, mounting, upstream_loc, customers_reported, lon, lat, circuit_id_layer |
| facilities | 21 | 3.1 | kind, circuit_id, zone_id, dist_m |
| labor_adders | 11 | 5.0 | original_intent, work_type, description, install_hours, unit |
| labor_connectors | 95 | 3.2 | cu, install_hours, remove_hours |
| labor_cu | 30 | 4.9 | category, size, install_hours, remove_hours, cu_count, sheet |
| labor_meta | 1 | 1.1 | ilm |
| poles | 15,545 | 575.4 | pole_id, loc, material, height_ft, pole_class, install_year, usage, status, lon, lat, circuit_id, zone_id, zone_method |
| primary | 14,528 | 961.5 | seg_idx, line_id, kind, loc_a, loc_b, circuit_id, cir_label, station, length_ft, mounting, usage, phase, install_year, status, mid_lon, mid_lat, geodesic_ft, circuit_id_layer, zone_id, zone_method |
| primary_vertices | 72,966 | 1293.2 | seg_idx, vertex_idx, lon, lat |
| secondary | 20,782 | 1269.5 | seg_idx, line_id, kind, loc_a, loc_b, circuit_id, cir_label, station, length_ft, mounting, usage, phase, install_year, status, mid_lon, mid_lat, geodesic_ft, circuit_id_layer |
| transformers | 6,289 | 277.6 | xfmr_id, loc, upstream_loc, circuit_id, cir_label, station, customers_reported, kva, mounting, structure, bank, device_type, install_year, lon, lat, zone_id, zone_source |
| upstream | 87,399 | 515.2 | premise_nu, pos, table, eq_id, eq_type, loc |
| zone_hazard | 1,714 | 101.2 | zone_id, circuit_id, kind, depth, parent_zone_id, oh_miles, ug_miles, n_transformers_overhead, n_customers_direct, n_customers_downstream, pole_year_mean, pole_year_known_pct, n_damage_ref, n_damage_ref_line, n_damage_ref_equipment, rate_line_per_oh_mile, n_equipment, mu_line, mu_equipment, lam_line, lam_equipment, lam_total |
| zones | 1,714 | 142.6 | zone_id, kind, eq_type, loc, circuit_id, parent_zone_id, depth, path_zone_ids, n_premise_paths, lon, lat, n_customers_direct, n_life_support_direct, n_customers_downstream, n_life_support_downstream, oh_miles, ug_miles, n_segments, n_poles, pole_year_mean, pole_year_known_pct, n_transformers, n_transformers_overhead, n_damage_total, n_damage_ref, n_damage_ref_line, n_damage_ref_equipment |

Other files in the bundle: `examples.json`.

Regenerate with `python scripts/prepare_data.py --source <organizer zip>`; re-slim an existing bundle with `python scripts/slim_demo_bundle.py`.
