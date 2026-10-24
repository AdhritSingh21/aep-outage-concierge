# AEP AI Outage Concierge — HackOHI/O 2026

A customer-facing assistant for storm outages, built on the AEP challenge data: explained outage risk for an
anonymized premise, a restoration *range* from labor-hour and crew-scheduling simulation, tailored preparation
guidance, alert eligibility, and plain-language messages for the four outage stages. Its operational core is a
**network-aware repair scheduler** that minimizes customer-outage hours under limited crews and is benchmarked against
FIFO and a simple customer-benefit heuristic on identical scenarios.

> Status: under construction during the hackathon. Sections below are filled in as milestones complete (see PLAN.md).

## Quick start

```bash
git clone https://github.com/AdhritSingh21/aep-outage-concierge
cd aep-outage-concierge
python -m venv .venv
.venv\Scripts\activate        # Windows   (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
streamlit run app.py           # runs on the committed demo bundle in data/demo
```

To use the full organizer dataset, see [data/README.md](data/README.md) and run
`python scripts/prepare_data.py --source <organizer zip>`.

## Data

Organizer data is provided by AEP for HackOHI/O 2026: https://github.com/hackohio/aep_challenge_hack26.
It is not redistributed here; `data/README.md` explains how to obtain it and what the preparation script does.

## Methods, assumptions, results

_To be completed._ Every figure in this README is labeled **Observed**, **Assumed**, or **Simulated**; see
`ASSUMPTIONS.md` for the register.

## License

MIT for the code (see LICENSE). The challenge data remains the property of AEP / HackOHI/O.
