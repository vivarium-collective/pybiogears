"""Smoke tests against the real engine. Needs $BIOGEARS_RUNTIME.

Oracles here are physical (mass balance) or BioGears' own outputs, never the binding.
"""
import math
import os
from pathlib import Path

import numpy as np
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
    # Exact step counts, so the reloaded run takes the same steps (probe P3: ~1e-12).
    eng.advance_steps(250)
    blob = eng.save_state_bytes()
    assert blob.lstrip().startswith(b"<?xml")
    eng.advance_steps(250)
    a = eng.pull()
    eng.load_state_bytes(blob)
    eng.advance_steps(250)
    b = eng.pull()
    assert a[0] == pytest.approx(b[0], abs=1e-9)
    assert np.allclose(a[1:], b[1:], rtol=1e-10, atol=0)


def test_reload_with_requests_logs_no_errors(eng):
    # Regression: LoadState with requests present logged "Unsupported DataRequest Received".
    blob = eng.save_state_bytes()
    eng.load_state_bytes(blob)
    assert not [m for m in eng.log if m.level in ("Error", "Fatal")]


def test_active_infusion_survives_reload():
    # Regression: after LoadState, an active compound listed every component twice,
    # so the reloaded run infused double solutes (P3: 1e-5 in blood volume after 60 s).
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    req = [pb.DataRequest("Physiology", "BloodVolume", "mL"),
           pb.DataRequest("Physiology", "MeanArterialPressure", "mmHg")]
    a = pb.BioGearsEngine()
    a.set_requests(req)
    a.load_state(STATE)
    a.process_actions(pb.actions.substance_compound_infusion("Saline", 1000.0, 200.0))
    a.advance_steps(500)
    blob = a.save_state_bytes()
    a.advance_steps(3000)
    b = pb.BioGearsEngine()
    b.set_requests(req)
    b.load_state_bytes(blob)
    b.advance_steps(3000)
    va, vb = a.pull(), b.pull()
    assert va[1] > 5600  # the infusion kept running
    assert vb[1] == pytest.approx(va[1], rel=1e-9)


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


def test_unknown_hemorrhage_compartment_is_refused(eng):
    # Regression (P7): BioGears accepts it, then segfaults on the next step.
    with pytest.raises(pb.BioGearsError, match="no bleed path"):
        eng.process_actions(pb.actions.hemorrhage("Nowhere", 100.0))
    eng.advance(1)  # the process is still alive and the engine still runs


def test_unknown_compound_raises_cleanly(eng):
    with pytest.raises(pb.BioGearsError, match="could not be read"):
        eng.process_actions(pb.actions.substance_compound_infusion("Unobtainium", 500.0, 100.0))
    assert not list(Path(os.environ["BIOGEARS_RUNTIME"]).glob(".pybiogears_tmp_*"))
    eng.advance(1)


def test_events_arrive_after_load(eng):
    # Regression (P7): LoadState rebuilt the patient and dropped the event handler.
    eng.advance(3)
    assert "StartOfCardiacCycle" in {e.event for e in eng.events}


def test_irreversible_state_raises():
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    e = pb.BioGearsEngine()
    e.set_requests([pb.DataRequest("Physiology", "BloodVolume", "mL")])
    e.load_state(STATE)
    e.process_actions(pb.actions.hemorrhage("Aorta", 1500.0))
    with pytest.raises(pb.IrreversibleStateError):
        for _ in range(40):  # BioGears declares it at 50 % blood loss, ~187 s here
            e.advance(15)
    assert e.irreversible_state
    assert "IrreversibleState" in {ev.event for ev in e.events}
    with pytest.raises(pb.IrreversibleStateError):
        e.advance(1)


def _thermal(heating_W=None, applied=None):
    """A ThermalApplication action; parts left as None are omitted."""
    parts = ""
    if heating_W is not None:
        parts += (f'<ActiveHeating><Power value="{heating_W}" unit="W"/>'
                  '<SurfaceAreaFraction value="1.0"/></ActiveHeating>')
    if applied is not None:
        state, temp = applied
        parts += (f'<AppliedTemperature><State>{state}</State><Temperature value="{temp}" unit="degC"/>'
                  '<SurfaceAreaFraction value="1.0"/></AppliedTemperature>')
    return f'<Action xsi:type="ThermalApplicationData" AppendToPrevious="false">{parts}</Action>'


def _skin_after(actions, seconds=120):
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    e = pb.BioGearsEngine()
    e.set_requests([pb.DataRequest("Physiology", "SkinTemperature", "degC")])
    e.load_state(STATE)
    if actions:
        e.process_actions(actions)
    e.advance(seconds)
    return float(e.pull()[1])


def test_thermal_application_parts_left_out_or_off_are_dropped():
    """BioGears 8.2.0 recreates omitted parts with NaN values ("Value is NaN" on the
    next step) and applies an applied temperature whatever its State, pinning the
    clothing node; prune_thermal_application removes both (viva-biogears probe
    circuits-probe-2026-11-03)."""
    rest = _skin_after(None)
    heated = _skin_after(_thermal(heating_W=500))                    # heating alone: no NaN
    heated_off = _skin_after(_thermal(heating_W=500, applied=("Off", 10)))
    applied = _skin_after(_thermal(applied=("On", 40)))
    assert heated > rest + 1.0
    assert heated_off == pytest.approx(heated, abs=1e-9)            # Off applied temperature ignored
    assert applied > rest + 1.0
