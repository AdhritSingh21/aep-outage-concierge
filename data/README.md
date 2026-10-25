# Data

This project uses the datasets supplied by **American Electric Power (AEP)** for the
**HackOHI/O 2026 "AI Outage Concierge" challenge**. The data is published by the organizers at

> https://github.com/hackohio/aep_challenge_hack26

and remains the property of AEP / the HackOHI/O organizers. It is **not** redistributed in this repository.
All credit for the datasets goes to AEP and HackOHI/O; this repository only contains code that reads them and small
derived summaries.

## What the organizer package contains

| File | Contents |
|---|---|
| `data/raw/Data1.zip` | GIS shapefiles: customers, poles, primary lines, fuse banks, damage assessments (ARCOS), ambulance services, fire stations, hospitals |
| `data/raw/Data2.zip` | GIS shapefiles: secondary lines, transformers, switches, recloser banks |
| `data/raw/matched_upstream.zip` | `matched_upstream.txt`: premise → upstream protective devices → circuit breaker |
| `data/Install_Labor_HackOHIO.xlsx` | Labor-hour reference (CU averages, adders, indirect labor multiplier) |
| `docs/*.docx`, `docs/structure_drawings.pdf` | Challenge brief and structure diagrams |

## How to obtain and prepare it

1. Download the organizer repository as a zip (GitHub → Code → Download ZIP) or clone it:
   `git clone https://github.com/hackohio/aep_challenge_hack26`
2. From this repository's root, run (point `--source` at the zip **or** the cloned folder):

```bash
python scripts/prepare_data.py --source "path/to/aep_challenge_hack26-main.zip"
```

This copies the organizer files into `data/raw/`, extracts the nested archives into `data/extracted/`, builds the
processed tables in `data/processed/` (parquet), and writes `data/data_report.md` with join statistics.
All of those folders are git-ignored.

3. `data/demo/` is a small committed bundle (a handful of circuits, anonymized premise IDs only) so the app can run
   without the full extract. It is derived from the organizer data and carries the same attribution.

## Privacy

Customer records are anonymized by the organizers. This project never displays address, road, meter, or
annotation fields, and refers to premises only by their anonymized `premise_nu`.
