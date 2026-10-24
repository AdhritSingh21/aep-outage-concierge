"""Shared pytest configuration.

Tests marked ``data`` need the processed organizer data (data/processed) and are skipped when it is absent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from concierge import config as cfg  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "data: needs data/processed built by scripts/prepare_data.py")


def pytest_collection_modifyitems(config, items):
    if (cfg.PROCESSED / "customers.parquet").exists():
        return
    skip = pytest.mark.skip(reason="processed data not present; run scripts/prepare_data.py")
    for item in items:
        if "data" in item.keywords:
            item.add_marker(skip)
