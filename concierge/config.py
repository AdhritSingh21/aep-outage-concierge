"""Paths and constants shared across the package.

Anything here that is a modeling choice is also listed in ASSUMPTIONS.md with its label (Assumed).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
EXTRACTED = DATA / "extracted"
PROCESSED = DATA / "processed"
DEMO = DATA / "demo"
RESULTS = ROOT / "results"

# Observed: structure of the organizer package
SHAPEFILES = {
    "customers": EXTRACTED / "Data1" / "Customer_Clip",
    "poles": EXTRACTED / "Data1" / "Pole_Clip",
    "primary": EXTRACTED / "Data1" / "Primary_ExportFeatures",
    "fuses": EXTRACTED / "Data1" / "Fuse_Bank_Clip",
    "damage": EXTRACTED / "Data1" / "arcos_prod_damage_09_28_Clip",
    "ambulance": EXTRACTED / "Data1" / "Ambulance_Servi_Pairwis_Clip",
    "fire": EXTRACTED / "Data1" / "Fire_Stations_E_Pairwis_Clip",
    "hospitals": EXTRACTED / "Data1" / "Hospitals_Medic_Pairwis_Clip",
    "secondary": EXTRACTED / "Data2" / "Secondary_ExportFeatures",
    "transformers": EXTRACTED / "Data2" / "Transformer_Clip",
    "switches": EXTRACTED / "Data2" / "Switch_Clip",
    "reclosers": EXTRACTED / "Data2" / "Recloser_Bank_Clip",
}
UPSTREAM_TXT = EXTRACTED / "matched_upstream" / "matched_upstream.txt"
LABOR_XLSX = RAW / "Install_Labor_HackOHIO.xlsx"

# Observed: reference storm window (76% of all damage records fall in it)
REFERENCE_STORM_START = "2025-02-12"
REFERENCE_STORM_END = "2025-02-18"
# Observed: placeholder repair date carried by unrepaired rows
REPAIR_DATE_PLACEHOLDER = "2025-10-24"

# Customer fields that must never be rendered (privacy)
PII_FIELDS = ("address", "road_name", "address_nu", "street_cod", "macss_mete", "anno_strin", "road_id")

# Assumed: indirect labor multiplier from the organizer workbook, applied once to all labor hours
ILM = 4.5

FEET_PER_MILE = 5280.0

# Assumed: risk bands on P(outage | storm of the chosen severity)
RISK_BANDS = [(0.15, "Low"), (0.40, "Moderate"), (0.70, "High"), (1.01, "Very high")]
# Assumed: storm severity multipliers relative to the Feb-2025 reference event
SEVERITY_OPTIONS = {
    "Minor storm (0.5x the Feb-2025 event)": 0.5,
    "Like the Feb-2025 storm (1x)": 1.0,
    "Major storm (2x)": 2.0,
    "Extreme storm (3x)": 3.0,
}
# Assumed: confidence rules
CONFIDENCE_MIN_CIRCUIT_DAMAGE = 10      # reference-storm records on the circuit for "High"
CONFIDENCE_MIN_POLE_YEAR_KNOWN_PCT = 50

# Circuits included in the committed demo bundle (chosen for coverage: heavy damage, zero damage, life-support
# customers, underground service). Observed circuit ids from the organizer extract.
DEMO_CIRCUITS = ["0000114", "0000005", "0000026", "0000027", "0000073"]
