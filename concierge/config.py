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
