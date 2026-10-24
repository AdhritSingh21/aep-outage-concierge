"""Labor model tests: ILM applied once, adders, multiplier, bundle coverage."""
from __future__ import annotations

import pandas as pd
import pytest

from concierge import config
from concierge.labor import ASSET_TO_BUNDLE, BUNDLES, LaborTable


def synthetic_table():
    cats = sorted({c for b in BUNDLES.values() for c, *_ in b})
    rows = []
    for c in cats:
        if c == "Pole":
            for size, h in (("Small", 1.9), ("Medium", 2.5), ("Large", 3.4)):
                rows.append({"category": c, "size": size, "install_hours": h, "remove_hours": 0.9})
        elif c == "Transformer":
            for kva in (10, 25, 50, 100):
                rows.append({"category": c, "size": str(kva), "install_hours": 1.0 + kva / 1000, "remove_hours": 0.8})
        else:
            rows.append({"category": c, "size": "", "install_hours": 0.5, "remove_hours": 0.1})
    cu = pd.DataFrame(rows)
    adders = pd.DataFrame([
        {"original_intent": "Difficult Location", "install_hours": 0.5},
        {"original_intent": "Backyard Adder", "install_hours": 0.5},
        {"original_intent": "Remote Location Adder", "install_hours": 1.312},
        {"original_intent": "Specialized Handling Adder", "install_hours": 0.5},
        {"original_intent": "Access Constraint Adder", "install_hours": 0.5},
    ])
    return LaborTable(cu, adders, ilm=4.5)


@pytest.fixture(scope="module")
def lt():
    return synthetic_table()


def test_ilm_applied_exactly_once(lt):
    th = lt.task_hours("Fuse Bank")
    assert th.base_hours == pytest.approx((0.5 + 0.1) + 2 * 0.5)  # cutout install+remove + 2 connectors
    assert th.hours == pytest.approx(th.base_hours * 4.5)
    # nothing else multiplies by ILM: a second LaborTable call gives the same number
    assert lt.task_hours("Fuse Bank").hours == pytest.approx(th.hours)


def test_adders_apply_only_when_flagged(lt):
    plain = lt.task_hours("primary", "Damaged Wire")
    access = lt.task_hours("primary", "Damaged Wire", access_issue=True)
    backyard = lt.task_hours("primary", "Damaged Wire", special_equipment="Backyard Bucket")
    remote = lt.task_hours("primary", "Damaged Wire", special_equipment="Track Hoe")
    other = lt.task_hours("primary", "Damaged Wire", special_equipment="Big Bucket")
    scen = lt.task_hours("primary", "Damaged Wire", access_difficulty=True)
    assert plain.adder_hours == 0
    assert access.adder_hours == pytest.approx(0.5) and backyard.adder_hours == pytest.approx(0.5)
    assert remote.adder_hours == pytest.approx(1.312) and other.adder_hours == pytest.approx(0.5)
    assert scen.adder_hours == pytest.approx(0.5)
    both = lt.task_hours("primary", "Damaged Wire", access_issue=True, access_difficulty=True)
    assert both.adder_hours == pytest.approx(1.0)
    assert access.hours == pytest.approx((plain.base_hours + 0.5) * 4.5)


def test_multiplier_is_linear(lt):
    a = lt.task_hours("Pole", "Damaged Pole", labor_multiplier=1.0).hours
    b = lt.task_hours("Pole", "Damaged Pole", labor_multiplier=1.5).hours
    assert b == pytest.approx(1.5 * a)


def test_sizes(lt):
    small = lt.task_hours("Pole", pole_height_ft=30).base_hours
    medium = lt.task_hours("Pole").base_hours
    large = lt.task_hours("Pole", pole_height_ft=60).base_hours
    assert small < medium < large
    t25 = lt.task_hours("Transformer").components[0][1]
    t100 = lt.task_hours("Transformer", kva=100).components[0][1]
    assert t100 > t25


def test_damage_type_refines_bundle(lt):
    assert lt.bundle_for("primary", "Damaged Wire") == "primary_wire"
    assert lt.bundle_for("primary", "Damaged Equipment") == "primary_equipment"
    assert lt.bundle_for("secondary", "Damaged Service") == "service"
    assert lt.bundle_for("Recloser Bank", "Damaged Switch") == "recloser_bank"
    with pytest.raises(KeyError):
        lt.bundle_for("Nuclear Plant")


def test_bundle_summary_has_every_bundle(lt):
    s = lt.bundle_summary()
    assert set(s["bundle"]) == set(BUNDLES)
    assert (s["crew_hours_with_ilm"] == (s["base_hours"] * 4.5).round(1)).all()


@pytest.mark.data
def test_every_observed_asset_type_has_a_bundle():
    lt_real = LaborTable.from_processed(config.PROCESSED)
    dmg = pd.read_parquet(config.PROCESSED / "damage.parquet")
    pairs = dmg.groupby(["asset_type", "damage_type"]).size().index
    for asset, dtype in pairs:
        th = lt_real.task_hours(asset, dtype)  # raises if unmapped
        assert th.hours > 0
    assert lt_real.ilm == 4.5
    # sanity on the real workbook: a pole replacement is the biggest bundle, wire repairs among the smallest
    s = lt_real.bundle_summary().set_index("bundle")["crew_hours_with_ilm"]
    assert s["pole"] == s.max()
    assert 20 <= s["pole"] <= 45 and 2 <= s["primary_wire"] <= 8 and s["primary_wire"] <= s["fuse_bank"] <= s["transformer"]
