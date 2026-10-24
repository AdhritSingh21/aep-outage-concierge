# CLAUDE.md — working agreement for aep-outage-concierge

HackOHI/O 2026 AEP "AI Outage Concierge" submission. Python 3.11 + Streamlit. Windows / PowerShell host.
Milestones and the requirement-traceability table live in `PLAN.md`; assumptions in `ASSUMPTIONS.md`.

## Non-negotiable rules
1. **Data never enters git.** `data/raw`, `data/extracted`, `data/processed`, `.venv`, `.env`, secrets are ignored.
   Only `data/demo/` (small derived bundle, a few circuits, anonymized IDs only) and `results/` summaries are committed.
2. **Three labels, always.** Every number shown to a user or judge is tagged **Observed** (organizer data as-is),
   **Assumed** (our modeling assumption; listed in `ASSUMPTIONS.md`), or **Simulated** (output of a scenario run).
   Never blur them in UI text, README, or commit messages.
3. **No manufactured labels or claims.** No invented outage negatives, no repair-date durations treated as outage
   durations, no performance numbers that a script in `scripts/` did not produce. Report solver status and limits.
4. **No PII in any output.** Never render `address`, `road_name`, `address_nu`, `street_cod`, `macss_mete`,
   `anno_strin`, employee or crew names, or precise facility coordinates. Premises are referred to by anonymized
   `premise_nu` only; city + circuit are the only location words shown.
5. **One milestone at a time.** Finish → `pytest -q` → manual check → commit (`Mx: ...` message) → push → next.
6. **Single scenario state.** All customer-facing outputs are pure functions of a `Scenario` object
   (`concierge/scenario.py`). Do not read Streamlit widgets inside model code.

## Data facts to rely on (verified 2026-10-24, see data/data_report.md once generated)
- `matched_upstream.txt` keys on customer `premise_nu` (not `id`). 47 customers have no path → fallback branch.
- Path tokens are `table*id*type*location`; reclosers match the Recloser layer by **location**, fuses by id.
- `report_len` is feet. AEP layers are WGS84 lon/lat; the USGS facility layers are Web Mercator meters.
- Damage circuit key is `(Substation, "CIRnn")`, which is not numerically equal to `circuit_id`; 14 keys are
  ambiguous → resolve by nearest primary segment. Ignore `NEAR_FID`/`NEAR_DIST` (computed on unclipped layers).
- Reference storm = Feb 12–18 2025 (1,254 of 1,659 damage records). Dates are day-granularity;
  `Repaired_D == 2025-10-24` with `Repaired == "No"` is a placeholder → null.
- ILM 4.5 applies once to all labor hours and already includes drive/safety/setup → no separate travel time.

## Engineering notes
- Shapefiles are read with the stdlib reader in `concierge/shapefile_io.py` (no GDAL). Prefix paths with `\\?\`
  when they may exceed 260 chars.
- Run Python as `.venv\Scripts\python`; run tests with `.venv\Scripts\python -m pytest -q`
  (`-m data` for tests that need processed data; they skip when it is absent).
- Processed tables are parquet in `data/processed/`; regenerate with `python scripts/prepare_data.py --source <zip>`.
- Streamlit: `streamlit run app.py`. Cache heavy loads with `st.cache_data`.
- Commit messages: `M<n>: <what changed>`; no attribution trailers.
