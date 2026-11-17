"""Engine features: schema-built actions and conditions, event and log callbacks,
active events, the patient, pickling, clear, configuration overrides, scenario files and
the engine pool. Needs $BIOGEARS_RUNTIME.

Oracles: hand-written BioGears XML, the engine driven step by step, BioGears' own scenario
executor, the patient file, and serial runs (for the pool); never the feature itself.
"""
import os
import pickle
import re
from pathlib import Path

import numpy as np
import pytest

import pybiogears as pb

pytestmark = pytest.mark.skipif("BIOGEARS_RUNTIME" not in os.environ, reason="needs $BIOGEARS_RUNTIME")
STATE = "./states/StandardMale@0s.xml"
REQ = [pb.DataRequest("Physiology", "HeartRate", "1/min"),
       pb.DataRequest("Physiology", "BloodVolume", "mL"),
       pb.DataRequest("Physiology", "MeanArterialPressure", "mmHg")]


@pytest.fixture
def eng():
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    e = pb.BioGearsEngine()
    e.set_requests(REQ)
    e.load_state(STATE)
    return e


# -- schema-built actions and conditions ---------------------------------------------------
def test_every_action_and_condition_type_is_listed():
    acts, conds = pb.schema.actions(), pb.schema.conditions()
    # BioGears 8.2.0's schema: 60 actions, 13 conditions (base types and scenario-only ones excluded).
    assert len(acts) == 60 and len(conds) == 13
    assert {"Hemorrhage", "Infection", "ConsumeNutrients", "AnesthesiaMachineConfiguration", "Sleep"} <= set(acts)
    assert {"ChronicAnemia", "Dehydration", "LobarPneumonia"} <= set(conds)
    assert not {"Action", "PatientAction", "AdvanceTime", "SerializeState"} & set(acts)


def test_built_xml_equals_the_hand_written_helpers():
    assert pb.action("Hemorrhage", Compartment="Aorta", InitialRate=(300, "mL/min")) == pb.actions.hemorrhage("Aorta", 300)
    assert (pb.action("SubstanceCompoundInfusion", SubstanceCompound="Saline", BagVolume=(500, "mL"), Rate=(100, "mL/min"))
            == pb.actions.substance_compound_infusion("Saline", 500, 100))
    assert pb.action("hemorrhage", compartment="Aorta", initial_rate=(300, "mL/min")) == pb.actions.hemorrhage("Aorta", 300)


@pytest.mark.parametrize("name, fields", [
    ("Infection", dict(Location="Skin", Severity="Severe", MinimumInhibitoryConcentration=(16, "mg/L"))),
    ("ConsumeNutrients", dict(Nutrition={"Carbohydrate": (40, "g"), "Water": (0.3, "L")})),
    ("Intubation", dict(Type="Tracheal")),
    ("NasalCannula", dict(FlowRate=(2, "L/min"))),
    ("AnesthesiaMachineConfiguration", dict(Configuration={
        "Connection": "Mask", "InletFlow": (5, "L/min"), "OxygenSource": "Wall", "PrimaryGas": "Nitrogen",
        "RespiratoryRate": (16, "1/min"), "VentilatorPressure": (10, "cmH2O")})),
    ("EnvironmentChange", dict(Conditions={"AmbientTemperature": (30, "degC")})),
    ("PainStimulus", dict(Location="LeftArm", Severity=0.5)),
    ("Urinate", {}),
])
def test_biogears_accepts_built_actions(eng, name, fields):
    eng.process_actions(pb.action(name, **fields))     # raises if BioGears refuses or can't parse it
    eng.advance(1.0)


def test_a_built_action_runs_like_the_hand_written_one():
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    hand = ('<Action xsi:type="InfectionData" Severity="Moderate">'
            '<MinimumInhibitoryConcentration value="8" unit="mg/L"/><Location>Gut</Location></Action>')
    built = pb.action("Infection", Severity="Moderate", MinimumInhibitoryConcentration=(8, "mg/L"), Location="Gut")
    out = []
    for xml in (hand, built):
        e = pb.BioGearsEngine()
        e.set_requests(REQ)
        e.load_state(STATE)
        e.process_actions(xml)
        e.advance(30)
        out.append(e.pull())
    np.testing.assert_array_equal(out[0], out[1])


@pytest.mark.parametrize("name, fields, match", [
    ("Hemorrhage", dict(Compartment="Aorta"), "InitialRate is required"),
    ("Hemorrhage", dict(Compartment="Aorta", InitialRate=150), "needs a unit"),
    ("Hemorrhage", dict(Compartment="Aorta", InitialRate=(1, "mL/min"), Rate=3), "no field 'Rate'"),
    ("Infection", dict(Location="Skin", Severity="Awful", MinimumInhibitoryConcentration=(1, "mg/L")), "must be one of"),
    ("ConsumeNutrients", {}, "one of .* is required"),
    ("ConsumeNutrients", dict(Nutrition={"Water": (0.3, "L")}, NutritionFile="x.xml"), "give one of"),
    ("ConsumeNutrients", dict(Nutrition={"Beer": (1, "L")}), "no field 'Beer'"),
    ("ConsumeNutrients", dict(Nutrition=3), "takes a dict"),
    ("Bleed", {}, "not a BioGears action"),
])
def test_bad_fields_are_refused_before_biogears(name, fields, match):
    with pytest.raises(pb.SchemaError, match=match):
        pb.action(name, **fields)


def test_conditions_and_descriptions():
    assert pb.condition("ChronicAnemia", ReductionFactor=0.3) == \
        '<Condition xsi:type="ChronicAnemiaData"><ReductionFactor value="0.3"/></Condition>'
    fields = {f.name: f for f in pb.schema.describe("Hemorrhage")}
    assert fields["InitialRate"].required and fields["InitialRate"].unit_required
    assert fields["Compartment"].attribute and not fields["BleedResistance"].required
    with pytest.raises(pb.SchemaError, match="ReductionFactor is required"):
        pb.condition("ChronicAnemia")


# -- events, logs, patient -----------------------------------------------------------------
def test_callbacks_get_every_event_and_message_in_order(eng):
    events, messages = [], []
    eng.set_event_handler(events.append)
    eng.set_log_listener(messages.append)
    eng.events.clear()
    eng.log.clear()
    eng.process_actions(pb.action("Hemorrhage", Compartment="Aorta", InitialRate=(1500, "mL/min")))
    eng.advance(120)
    assert [(e.event, e.active, e.sim_time_s) for e in events] == [(e.event, e.active, e.sim_time_s) for e in eng.events]
    assert [m.text for m in messages] == [m.text for m in eng.log]
    assert any(e.event == "HypovolemicShock" and e.active for e in events)  # at ~100 s


def test_active_events_agree_with_the_event_log(eng):
    """Falsifies: an event read from the wrong enum, or the anesthesia machine left out."""
    eng.events.clear()
    eng.process_actions(pb.action("Hemorrhage", Compartment="Aorta", InitialRate=(1500, "mL/min")))
    eng.advance(120)
    last = {}
    for e in eng.events:
        last[e.event] = e.active
    active = set(eng.active_events)
    # Every event that changed and ended active is reported, and none that ended inactive.
    assert {k for k, v in last.items() if v} <= active
    assert not {k for k, v in last.items() if not v} & active
    assert "HypovolemicShock" in active


def test_patient_matches_the_patient_file(eng):
    p = eng.patient()
    text = Path("patients/StandardMale.xml").read_text()
    assert p["Name"] == "StandardMale" and p["Sex"] == "Male"
    age = float(re.search(r'<Age[^>]*value="([0-9.]+)"', text).group(1))
    assert p["Age"] == (age, "yr")
    assert p["Weight"][0] > 0 and p["Weight"][1]


# -- pickling and clear --------------------------------------------------------------------
def test_a_pickled_engine_continues_the_run(eng):
    eng.advance(20)
    copy = pickle.loads(pickle.dumps(eng))
    assert copy.sim_time_s == pytest.approx(eng.sim_time_s)
    eng.advance(10)
    copy.advance(10)
    # A saved state continues the run to ~1e-11 (probe P3), not bit for bit.
    np.testing.assert_allclose(copy.pull(), eng.pull(), rtol=1e-9)


def test_clear_starts_over(eng):
    eng.clear()
    assert not eng.has_state
    assert np.isnan(eng.pull()[1:]).all()
    eng.load_state(STATE)
    assert eng.has_state and eng.pull()[1] == pytest.approx(72.0)


# -- configuration overrides ---------------------------------------------------------------
def test_configuration_override_on_load():
    """BioGears reads a state's configuration from the state; pybiogears merges the override in."""
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    e = pb.BioGearsEngine()
    e.set_requests(REQ)
    e.load_state(STATE, configuration=pb.configuration(TimeStep=(0.01, "s")))
    assert e.time_step_s == 0.01
    e.advance_steps(100)
    assert e.sim_time_s == pytest.approx(1.0)
    with pytest.raises(pb.SchemaError, match="no field 'Nonsense'"):
        pb.configuration(Nonsense=1)
    with pytest.raises(pb.BioGearsError, match="has no Nonsense"):
        e.load_state(STATE, configuration='<Nonsense value="1"/>')


def test_configuration_override_on_initialize():
    """Falsifies: the override lost to BioGears' configuration loader, which drops every
    setting of a file that leaves a section out (see _complete_configuration)."""
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    e = pb.BioGearsEngine()
    e.set_requests(REQ)
    e.initialize("StandardMale.xml", configuration=pb.configuration(TimeStep=(0.04, "s")))
    assert e.time_step_s == 0.04
    hr = e.pull()[1]
    assert 50 < hr < 100


# -- scenario files ------------------------------------------------------------------------
SCENARIO = """<?xml version="1.0" encoding="UTF-8"?>
<Scenario xmlns="uri:/mil/tatrc/physiology/datamodel" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" contentVersion="BioGears_8.2.0">
  <Name>ShortBleed</Name>
  <Description>30 s of an aortic bleed from StandardMale@0s.</Description>
  <EngineStateFile>./states/StandardMale@0s.xml</EngineStateFile>
  <DataRequests SamplesPerSecond="1">
    <DataRequest xsi:type="PhysiologyDataRequestData" Name="HeartRate" Unit="1/min" Precision="6"/>
    <DataRequest xsi:type="PhysiologyDataRequestData" Name="BloodVolume" Unit="mL" Precision="6"/>
  </DataRequests>
  <Actions>
    <Action xsi:type="AdvanceTimeData"><Time value="5" unit="s"/></Action>
    <Action xsi:type="HemorrhageData" Compartment="Aorta"><InitialRate value="300" unit="mL/min"/></Action>
    <Action xsi:type="AdvanceTimeData"><Time value="25" unit="s"/></Action>
  </Actions>
</Scenario>
"""


def test_run_scenario_equals_the_engine_driven_step_by_step(tmp_path):
    """BioGears' executor advances time/dt + 1 steps per AdvanceTime (5 s = 251 steps)."""
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    (tmp_path / "s.xml").write_text(SCENARIO)
    e = pb.BioGearsEngine()
    e.set_requests(REQ)
    e.run_scenario(tmp_path / "s.xml", tmp_path / "s.csv")
    rows = (tmp_path / "s.csv").read_text().splitlines()
    assert rows[0].startswith("Time(s),HeartRate(1/min),BloodVolume(mL)") and len(rows) == 31
    d = pb.BioGearsEngine()
    d.set_requests(REQ)
    d.load_state(STATE)
    d.advance_steps(251)
    d.process_actions(pb.actions.hemorrhage("Aorta", 300))
    d.advance_steps(1251)
    np.testing.assert_array_equal(e.pull(), d.pull())
    assert e.pull()[2] < 5500                          # it bled


def test_a_failed_scenario_raises(tmp_path):
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    (tmp_path / "bad.xml").write_text(SCENARIO.replace("StandardMale@0s.xml", "Nobody@0s.xml"))
    with pytest.raises(pb.BioGearsError, match="failed"):
        pb.BioGearsEngine().run_scenario(tmp_path / "bad.xml", tmp_path / "bad.csv")


# -- the engine pool -----------------------------------------------------------------------
def test_engines_in_parallel_equal_engines_one_after_another():
    """Falsifies: engines sharing state across threads (BioGears keeps a global serializer)."""
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    bleed = pb.action("Hemorrhage", Compartment="RightLeg", InitialRate=(100, "mL/min"))
    serial = []
    for k in range(4):
        e = pb.BioGearsEngine()
        e.set_requests(REQ)
        e.load_state(STATE)
        if k % 2:
            e.process_actions(bleed)
        e.advance(20)
        serial.append(e.pull())
    with pb.EnginePool(4, REQ) as pool:
        pool.load_state(STATE)
        pool.process_actions({1: bleed, 3: bleed})
        pool.advance(20)
        parallel = pool.pull()
        assert len(pool) == 4 and pool[0].sim_time_s == pytest.approx(20, abs=0.02)
    np.testing.assert_array_equal(np.vstack(serial), parallel)
    assert parallel[1, 2] < parallel[0, 2]             # the bled engines lost blood


def test_pool_loads_states_from_bytes_in_parallel():
    os.chdir(os.environ["BIOGEARS_RUNTIME"])
    xml = Path(STATE).read_bytes()
    with pb.EnginePool(3, REQ) as pool:
        pool.map(lambda e, _: e.load_state_bytes(xml))
        pool.advance(5)
        rows = pool.pull()
    np.testing.assert_array_equal(rows[0], rows[1])
    np.testing.assert_array_equal(rows[0], rows[2])
