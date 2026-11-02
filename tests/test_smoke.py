"""Smoke tests against the real engine. Needs $BIOGEARS_RUNTIME.

Oracles here are physical (mass balance) or BioGears' own outputs, never the binding.
"""
import math
import os

import pytest

import pybiogears as pb

pytestmark = pytest.mark.skipif("BIOGEARS_RUNTIME" not in os.environ, reason="needs $BIOGEARS_RUNTIME")
STATE = "./states/StandardMale@0s.xml"
REQ = [
    pb.DataRequest("Physiology", "BloodVolume", "mL"),
    pb.DataRequest("Physiology", "HeartRate", "1/min"),
]


@pytest.fixture
def eng():
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    e = pb.BioGearsEngine()
    e.set_requests(REQ)
    e.load_state(STATE)
    return e


def test_version():
    assert pb.biogears_version().startswith("8.2.0")


def test_hemorrhage_removes_blood(eng):
    eng.advance(5)
    bv0 = eng.pull()[1]
    eng.process_actions(pb.actions.hemorrhage("Aorta", 300.0))
    eng.advance(60)
    bv1 = eng.pull()[1]
    # 300 mL/min for 60 s removes up to ~300 mL (rate falls as pressure falls);
    # transcapillary refill offsets a little. Mass balance bound, not a fit.
    assert 100.0 < bv0 - bv1 < 320.0


def test_bad_request_reads_nan(eng):
    ok = eng.set_requests(REQ + [pb.DataRequest("Physiology", "NoSuchProperty", "")])
    assert ok[:2] == [True, True] and ok[2] is False
    v = eng.pull()
    assert math.isnan(v[3]) and not math.isnan(v[1])


def test_unknown_substance_not_created(eng):
    ok = eng.set_requests([pb.DataRequest("LiquidCompartment", "Concentration", "g/L", "Aorta", "Unobtainium")])
    assert ok == [False]


def test_state_bytes_round_trip(eng):
    eng.advance(5)
    blob = eng.save_state_bytes()
    assert blob.lstrip().startswith(b"<?xml")
    eng.advance(5)
    a = eng.pull()
    eng.load_state_bytes(blob)
    eng.advance(5)
    b = eng.pull()
    assert abs(a[0] - b[0]) < 1e-9
    assert abs(a[1] - b[1]) / a[1] < 1e-6  # tolerance to be set by probe P3


def test_requested_units_are_honoured(eng):
    # Regression: with the engine's auto-tracking on, requests were silently
    # re-connected in the engine's native unit after the first sample.
    eng.set_requests([
        pb.DataRequest("Physiology", "BloodVolume", "mL"),
        pb.DataRequest("Physiology", "BloodVolume", "L"),
        pb.DataRequest("LiquidCompartment", "Volume", "mL", "Lymph"),
        pb.DataRequest("LiquidCompartment", "Volume", "L", "Lymph"),
    ])
    for _ in range(3):
        eng.advance(1)
        v = eng.pull()
        assert v[1] == pytest.approx(1000 * v[2], rel=1e-12)
        assert v[3] == pytest.approx(1000 * v[4], rel=1e-12)


def test_invalid_unit_is_unconnected(eng):
    ok = eng.set_requests([
        pb.DataRequest("Physiology", "BloodVolume", "mmHg"),
        pb.DataRequest("FluidNode", "Volume", "mmHg", "Lymph"),
    ])
    assert ok == [False, False]
    eng.advance(1)
    assert all(math.isnan(x) for x in eng.pull()[1:])


def test_circuit_requests(eng):
    ok = eng.set_requests([
        pb.DataRequest("FluidPath", "Flow", "mL/min", "LymphToVenaCava"),
        pb.DataRequest("FluidNode", "Volume", "mL", "Lymph"),
        pb.DataRequest("LiquidCompartment", "Volume", "mL", "Lymph"),
        pb.DataRequest("FluidPath", "Flow", "mL/min", "NoSuchPath"),
    ])
    assert ok == [True, True, True, False]
    eng.advance(5)
    v = eng.pull()
    assert v[1] > 0  # lymph returns to the vena cava
    # The Lymph compartment maps exactly the Lymph node: an oracle independent of the circuit reader.
    assert v[2] == pytest.approx(v[3], rel=1e-12)
