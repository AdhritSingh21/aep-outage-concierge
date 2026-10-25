"""Streamlit AppTest: per-premise defaults (calibrated crews, the account's life-support flag) must win over stale widget
state when the judge switches premises, and the demo story must reset every control it lists."""
from __future__ import annotations

import pytest

from concierge import config
from concierge.data import DataBundle
from concierge.scenarios import DamageSets

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

ROOT = config.ROOT


def _run(at):
    at.run(timeout=240)
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_premise_change_resets_crews_and_needs():
    d = DataBundle(config.DEMO)
    s = DamageSets(d)
    samples = d.sample_premises()
    ls = [x for x in samples if "life-support" in x["label"]]
    if not ls:
        pytest.skip("no life-support example in the bundle")
    at = _run(AppTest.from_file(str(ROOT / "app.py"), default_timeout=240))
    first = samples[0]["premise_nu"]
    assert at.session_state["last_premise"] == first
    assert at.session_state["crews"] == s.default_crews(str(d.premise(first)["circuit_id"]))
    # switch to the life-support example: crews follow its circuit's calibrated default, the medical need pre-fills
    label = next(o for o in at.selectbox(key="sample_pick").options if o.endswith(ls[0]["premise_nu"]))
    at.selectbox(key="sample_pick").select(label)
    _run(at)
    assert at.session_state["last_premise"] == ls[0]["premise_nu"]
    assert at.session_state["crews"] == s.default_crews(str(d.premise(ls[0]["premise_nu"])["circuit_id"]))
    assert "medical" in at.session_state["needs"]
    assert any("priority" in m.value for m in at.markdown)
    # switch back: the medical need does not leak to a premise without the flag
    at.selectbox(key="sample_pick").select(next(o for o in at.selectbox(key="sample_pick").options if o.endswith(first)))
    _run(at)
    assert at.session_state["needs"] == []


def test_demo_story_resets_controls():
    d = DataBundle(config.DEMO)
    demo = d.demo_story()
    if not demo:
        pytest.skip("no demo story")
    at = _run(AppTest.from_file(str(ROOT / "app.py"), default_timeout=240))
    at.toggle(key="opted").set_value(False)
    _run(at)
    at.button[0].click()   # "Load demo story" is the first button in the sidebar
    _run(at)
    assert at.session_state["premise_text"] == demo["premise_nu"]
    assert at.session_state["crews"] == int(demo["crews"])
    assert at.session_state["opted"] is True and at.session_state["needs"] == []
    assert at.session_state["mode"] == "Premise ID"
