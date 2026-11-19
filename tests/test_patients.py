"""pybiogears.check_patient: what BioGears 8.2.0 does with a patient when it sets it up.

Oracle: BioGears itself. For each patient, BioGears' own log from initialize (its
SetupPatient messages) is classified field by field and compared with the findings.
A VitalCapacity, which BioGears always refuses, is added to each patient so that
initialize stops right after SetupPatient (about a millisecond) instead of stabilizing;
SetupPatient runs every rule before it returns, so its messages are complete. Silent
replacements (logged by nothing) and the effect of the adjustments are checked by
stabilizing one patient."""

import os
import random
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

import pybiogears as pb
from pybiogears import PatientError, check_patient, check_patients
from pybiogears.patients import FIELDS

NEEDS_RUNTIME = pytest.mark.skipif("BIOGEARS_RUNTIME" not in os.environ, reason="needs $BIOGEARS_RUNTIME")
NS = "uri:/mil/tatrc/physiology/datamodel"
STOP = {"VitalCapacity": (4.0, "L")}       # BioGears refuses it: initialize stops after SetupPatient


def runtime() -> Path:
    return Path(os.environ["BIOGEARS_RUNTIME"])


def patient_xml(fields: dict) -> bytes:
    """A patient file with ``fields`` ({name: (value, unit) | value | str}), in schema order."""
    root = ET.Element(f"{{{NS}}}Patient")
    for name in FIELDS:
        if name not in fields:
            continue
        v = fields[name]
        el = ET.SubElement(root, f"{{{NS}}}{name}")
        if FIELDS[name] is None:
            el.text = str(v).lower() if isinstance(v, bool) else str(v)
        else:
            value, unit = v if isinstance(v, tuple) else (v, "")
            el.set("value", repr(float(value)))
            if unit:
                el.set("unit", unit)
    return ET.tostring(root, xml_declaration=True, encoding="UTF-8")


# -- BioGears' messages, field by field ----------------------------------------------------

_FIELD_WORDS = [
    ("gender", "Sex"), ("pulse pressure", "PulsePressure"), ("lean body mass", "LeanBodyMass"),
    ("muscle mass", "MuscleMass"), ("body density", "BodyDensity"), ("patient age", "Age"),
    ("heart rate maximum", "HeartRateMaximum"), ("heart rate minimum", "HeartRateMinimum"),
    ("heart rate baseline", "HeartRateBaseline"), ("systolic", "SystolicArterialPressureBaseline"),
    ("diastolic", "DiastolicArterialPressureBaseline"), ("mean arterial", "MeanArterialPressureBaseline"),
    ("blood volume", "BloodVolumeBaseline"), ("respiration rate", "RespirationRateBaseline"),
    ("right lung", "RightLungRatio"), ("total lung capacity", "TotalLungCapacity"),
    ("functional residual", "FunctionalResidualCapacity"), ("residual volume", "ResidualVolume"),
    ("tidal volume", "TidalVolumeBaseline"), ("vital capacity", "VitalCapacity"),
    ("expiratory reserve", "ExpiratoryReserveVolume"), ("inspiratory reserve", "InspiratoryReserveVolume"),
    ("inspiratory capacity", "InspiratoryCapacity"), ("lung volumes", "LungVolumes"),
    ("alveoli", "AlveoliSurfaceArea"), ("skin surface", "SkinSurfaceArea"), ("basal metabolic", "BasalMetabolicRate"),
    ("work rate", "MaxWorkRate"), ("pain", "PainSusceptibility"), ("sweat", "Hyperhidrosis"),
    ("sleep amount", "SleepAmount"), ("body fat", "BodyFatFraction"), ("height", "Height"), ("weight", "Weight"),
    ("blood type", "BloodTypeABO"),
]


def field_of(text: str) -> str:
    t = text.lower()
    for words, name in _FIELD_WORDS:
        if words in t:
            return name
    raise AssertionError(f"unclassified BioGears message: {text!r}")


def classify(level: str, text: str):
    """(field, finding level) for one SetupPatient message, or None if it reports nothing
    about the given values (a value BioGears computes, the line paired with "overwritten")."""
    t = text.strip()
    if level == "Error":
        return ("file", "error") if "could not be read" in t or "reading data" in t else (field_of(t), "error")
    if level == "Warning":
        if "overwritten to" in t:
            return "Weight", "adjusted"
        if "Body Mass Index" in t and ("too high" in t or "too low" in t):
            return None
        return field_of(t), "warning"
    if "Resetting heart rate" in t:
        return "HeartRateBaseline", "adjusted"
    if "Sleep amount must be" in t:
        return "SleepAmount", "adjusted"
    if "tachycardic" in t or "bradycardic" in t:
        return "HeartRateBaseline", "info"
    if t.startswith("No patient") or "has not been set" in t:
        return field_of(t), "default"
    return None


@pytest.fixture(scope="module")
def engine():
    return pb.BioGearsEngine(runtime())


def biogears_says(engine, xml: bytes, tmp_path) -> Counter:
    """BioGears' SetupPatient messages for this patient file, classified."""
    path = tmp_path / "patient.xml"
    path.write_bytes(xml)
    engine.log.clear()
    with pytest.raises(pb.BioGearsError):
        engine.initialize(path)
    texts = [(m.level, m.text) for m in engine.log]
    start = next((i for i, (_, t) in enumerate(texts) if "Configuring patient" in t), None)
    if start is not None:
        texts = texts[start + 1:]
    return Counter(c for c in (classify(lvl, t) for lvl, t in texts) if c)


def we_say(xml: bytes) -> Counter:
    return Counter((f.field, f.level) for f in check_patient(xml).findings if f.logged)


def assert_agrees(engine, fields, tmp_path):
    xml = patient_xml({**fields, **STOP})
    assert we_say(xml) == biogears_says(engine, xml, tmp_path), fields


# -- the rules, one by one -------------------------------------------------------------------

M = {"Name": "P", "Sex": "Male"}
F = {"Name": "P", "Sex": "Female"}
CASES = {
    "male, nothing given": M,
    "female, nothing given": F,
    "standard male": {**M, "Age": (44, "yr"), "Weight": (170, "lb"), "Height": (71, "in"), "BodyFatFraction": 0.21,
                      "HeartRateBaseline": (72, "1/min"), "RespirationRateBaseline": (16, "1/min"),
                      "SystolicArterialPressureBaseline": (114, "mmHg"),
                      "DiastolicArterialPressureBaseline": (73.5, "mmHg")},
    "too young": {**M, "Age": (15, "yr")},
    "too old": {**F, "Age": (110, "yr")},
    "age 70 (work rate floor)": {**F, "Age": (70, "yr")},
    "age in days": {**M, "Age": (12000, "day")},
    "short male": {**M, "Height": (160, "cm")},
    "tall female": {**F, "Height": (1.8, "m")},
    "outrageous height": {**M, "Height": (4, "ft")},
    "BMI 35": {**M, "Height": (175, "cm"), "Weight": (107, "kg")},
    "BMI 27": {**F, "Height": (160, "cm"), "Weight": (69000, "g")},
    "BMI 17": {**M, "Height": (180, "cm"), "Weight": (55, "kg")},
    "BMI 14": {**F, "Height": (165, "cm"), "Weight": (38, "kg")},
    "fat high": {**M, "BodyFatFraction": 0.3},
    "fat low": {**F, "BodyFatFraction": 0.05},
    "computed masses given": {**M, "BodyDensity": (1.05, "g/cm^3"), "LeanBodyMass": (60, "kg"),
                              "MuscleMass": (30, "kg")},
    "tachycardic": {**M, "HeartRateBaseline": (105, "1/min")},
    "heart rate clamped high": {**M, "HeartRateBaseline": (2, "Hz")},
    "bradycardic": {**F, "HeartRateBaseline": (55, "1/min")},
    "heart rate clamped low": {**F, "HeartRateBaseline": (45, "1/min")},
    "heart rate max given": {**M, "HeartRateMaximum": (180, "1/min")},
    "heart rate max below baseline": {**M, "HeartRateBaseline": (115, "1/min"), "HeartRateMaximum": (112, "1/min")},
    "heart rate min above baseline": {**M, "HeartRateMinimum": (80, "1/min")},
    "low pressures": {**M, "SystolicArterialPressureBaseline": (85, "mmHg"),
                      "DiastolicArterialPressureBaseline": (55, "mmHg")},
    "high pressures": {**F, "SystolicArterialPressureBaseline": (2.5, "psi"),
                       "DiastolicArterialPressureBaseline": (85, "mmHg")},
    "narrow pulse pressure": {**M, "SystolicArterialPressureBaseline": (100, "mmHg"),
                              "DiastolicArterialPressureBaseline": (79, "mmHg")},
    "MAP given": {**M, "MeanArterialPressureBaseline": (90, "mmHg")},
    "blood volume within 15 %": {**M, "Weight": (80, "kg"), "BloodVolumeBaseline": (5.5, "L")},
    "blood volume too low": {**M, "Weight": (80, "kg"), "BloodVolumeBaseline": (40, "dL")},
    "blood volume too high": {**F, "BloodVolumeBaseline": (6000, "mL")},
    "fast breathing": {**M, "RespirationRateBaseline": (22, "1/min")},
    "slow breathing": {**F, "RespirationRateBaseline": (8, "1/min")},
    "right lung high": {**M, "RightLungRatio": 0.65},
    "right lung low": {**M, "RightLungRatio": 0.45},
    "lung volumes given": {**F, "TotalLungCapacity": (5, "L"), "FunctionalResidualCapacity": (2000, "mL"),
                           "ResidualVolume": (1.2, "L")},
    "negative lung volume": {**M, "FunctionalResidualCapacity": (1.0, "L"), "ResidualVolume": (1.5, "L")},
    "surfaces, BMR, work rate given": {**M, "AlveoliSurfaceArea": (70, "m^2"), "SkinSurfaceArea": (19000, "cm^2"),
                                       "BasalMetabolicRate": (80, "W"), "MaxWorkRate": (1.2, "kcal/s")},
    "sleep zero": {**M, "SleepAmount": (0, "hr")},
    "sleep given": {**F, "SleepAmount": (360, "min"), "PainSusceptibility": 0.5, "Hyperhidrosis": -0.5},
    "blood type given": {**M, "BloodTypeABO": "O", "BloodTypeRh": False},
    "derived baselines given": {**M, "TidalVolumeBaseline": (0.5, "L"), "ExpiratoryReserveVolume": (1, "L"),
                                "InspiratoryReserveVolume": (3, "L"), "InspiratoryCapacity": (3.5, "L")},
}


@NEEDS_RUNTIME
@pytest.mark.parametrize("case", CASES)
def test_each_rule_agrees_with_biogears(engine, tmp_path, case):
    assert_agrees(engine, CASES[case], tmp_path)


@NEEDS_RUNTIME
def test_every_shipped_patient_agrees_with_biogears(engine, tmp_path):
    files = sorted((runtime() / "patients").glob("*.xml"))
    assert len(files) == 20
    for f in files:
        if f.name == "Male_44_Normal.xml":
            # BioGears 8.2.0 ships this file with a git merge conflict in it.
            assert b"<<<<<<< HEAD" in f.read_bytes()
            assert [e.field for e in check_patient(f).errors] == ["file"]
            assert biogears_says(engine, f.read_bytes(), tmp_path) == Counter({("file", "error"): 2})
            continue
        root = ET.parse(f).getroot()
        fields = {}
        for el in root:
            name = el.tag.split("}")[1]
            fields[name] = el.text.strip() if FIELDS[name] is None else (float(el.get("value")), el.get("unit", ""))
        assert_agrees(engine, fields, tmp_path)


@NEEDS_RUNTIME
def test_random_patients_agree_with_biogears(engine, tmp_path):
    """300 patients drawn around and across every threshold."""
    rng = random.Random(8_2_0)
    ranges = {"Age": (10, 115, "yr"), "Height": (130, 220, "cm"), "Weight": (30, 140, "kg"),
              "BodyFatFraction": (0.0, 0.4, ""), "HeartRateBaseline": (40, 120, "1/min"),
              "HeartRateMaximum": (90, 210, "1/min"), "HeartRateMinimum": (0, 90, "1/min"),
              "SystolicArterialPressureBaseline": (80, 130, "mmHg"),
              "DiastolicArterialPressureBaseline": (50, 95, "mmHg"), "BloodVolumeBaseline": (3000, 8000, "mL"),
              "RespirationRateBaseline": (8, 22, "1/min"), "RightLungRatio": (0.45, 0.65, ""),
              "TotalLungCapacity": (3, 8, "L"), "FunctionalResidualCapacity": (1, 4, "L"),
              "ResidualVolume": (0.5, 3, "L"), "SleepAmount": (-2, 10, "hr")}
    for _ in range(300):
        fields = {"Name": "R", "Sex": rng.choice(["Male", "Female"])}
        for name, (lo, hi, unit) in ranges.items():
            if rng.random() < 0.5:
                fields[name] = (round(rng.uniform(lo, hi), 2), unit)
        assert_agrees(engine, fields, tmp_path)


@NEEDS_RUNTIME
@pytest.mark.parametrize("fields", [
    {**M, "Weight": (170, "stone")},          # a unit BioGears' Mass doesn't accept
    {**M, "Hyperhidrosis": 2.0},              # outside the schema's -1..1
    {**M, "Sex": "Other"},
])
def test_files_biogears_cannot_read(engine, tmp_path, fields):
    xml = patient_xml(fields)
    path = tmp_path / "patient.xml"
    path.write_bytes(xml)
    with pytest.raises((pb.BioGearsError, RuntimeError)):
        engine.initialize(path)
    check = check_patient(xml)
    assert not check.ok and len(check.errors) == 1


def test_file_layout_problems_are_errors(tmp_path):
    good = patient_xml({**M, "Age": (30, "yr"), "Weight": (80, "kg")})
    assert check_patient(good).ok
    unknown = good.replace(b"ns0:Age", b"ns0:Tmp")
    out_of_order = patient_xml({**M, "Weight": (80, "kg")}).replace(
        b"</ns0:Patient>", b'<ns0:Age value="30" unit="yr" /></ns0:Patient>')
    assert [f.field for f in check_patient(out_of_order).errors] == ["Age"]
    assert [f.field for f in check_patient(unknown).errors] == ["Tmp"]
    assert check_patient(b"<Patient").errors[0].field == "file"


# -- the API ----------------------------------------------------------------------------------

def test_values_are_what_biogears_sets_up():
    check = check_patient({"Name": "Big", "Sex": "Male", "Height": "70 in", "Weight": "250 lb",
                           "HeartRateBaseline": (120, "1/min"), "TotalVentilationBaseline": "6 L/min",
                           "RespiratoryDriverAmplitudeBaseline": "-5 cmH2O", "BMI": 35})
    height_m = 70 * 2.54 / 100
    assert check.ok
    assert check.values["Weight"] == pytest.approx(30 * height_m ** 2)
    assert check.values["HeartRateBaseline"] == 109.0
    assert check.values["Age"] == 44.0 and check.values["BloodTypeABO"] == "AB"
    assert check.values["BloodVolumeBaseline"] == pytest.approx(65.6 * check.values["Weight"] ** 1.02)
    adjusted = {f.field: f for f in check.adjusted}
    assert set(adjusted) == {"Weight", "HeartRateBaseline", "TotalVentilationBaseline",
                             "RespiratoryDriverAmplitudeBaseline", "BMI"}
    assert adjusted["Weight"].given == pytest.approx(250 * 0.45359237)
    assert adjusted["TotalVentilationBaseline"].logged is None       # silent in BioGears
    text = check.report()
    assert "accepts" in text and "lowers the weight to BMI 30" in text and "not given" in text


def test_errors_raise_on_request():
    check = check_patient({"Name": "Kid", "Sex": "Female", "Age": "12 yr"})
    assert not check.ok and check.errors[0].field == "Age"
    with pytest.raises(PatientError, match="Kid.*Age"):
        check.raise_for_errors()
    assert check_patient({"Name": "A", "Sex": "Male"}).raise_for_errors().ok
    assert [f.field for f in check_patient({"Name": "A"}).errors] == ["Sex"]


@NEEDS_RUNTIME
def test_check_patients_takes_a_folder_or_generated_data(tmp_path):
    checks = check_patients(runtime() / "patients")
    assert len(checks) == 20 and checks["StandardMale"].ok
    assert checks["StandardMale"].warnings == []
    assert not checks["Male_44_Normal"].ok                 # unnamed: keyed by file name
    out = pb.generate_data(runtime() / "templates", tmp_path / "data", kinds=["patients"])
    generated = check_patients(out)
    assert len(generated) == 20 and all(c.ok for c in generated.values())


@NEEDS_RUNTIME
def test_a_missing_patient_file_raises_instead_of_crashing(tmp_path, monkeypatch):
    """Falsifies: BioGears 8.2.0 segfaults on a patient file it can't find (it looks
    relative to the runtime, not Python's working directory)."""
    e = pb.BioGearsEngine(runtime())
    with pytest.raises(FileNotFoundError, match="nope.xml"):
        e.initialize("nope.xml")
    # A relative path to an existing file is taken relative to Python's working directory.
    monkeypatch.chdir(tmp_path)
    Path("kid.xml").write_bytes(patient_xml({**F, "Age": (12, "yr")}))
    with pytest.raises(pb.BioGearsError, match="too young"):
        e.initialize("kid.xml")


# -- after stabilization ------------------------------------------------------------------------

@NEEDS_RUNTIME
def test_stabilization_uses_the_adjusted_values(tmp_path):
    """Oracle: BioGears itself. A patient above BMI 30 with a heart rate of 120 /min
    stabilizes at BMI 30 and near 109 /min, not at the given values, as reported."""
    fields = {"Name": "Adjusted", "Sex": "Female", "Age": (35, "yr"), "Height": (165, "cm"),
              "Weight": (95, "kg"), "HeartRateBaseline": (120, "1/min"), "RespirationRateBaseline": (14, "1/min")}
    path = tmp_path / "Adjusted.xml"
    path.write_bytes(patient_xml(fields))
    check = check_patient(path)
    assert check.ok and {f.field for f in check.adjusted} == {"Weight", "HeartRateBaseline"}

    e = pb.BioGearsEngine(runtime())
    e.initialize(path)
    weight, unit = e.patient()["Weight"]
    assert unit == "kg" or unit == "lb"
    weight_kg = weight * (0.45359237 if unit == "lb" else 1.0)
    assert weight_kg == pytest.approx(check.values["Weight"], rel=0.01)       # 81.7 kg, not 95
    shifts = {s.field: s for s in check.after_stabilization(e)}
    assert set(shifts) == set(pb.patients.STABILIZED)
    hr = shifts["HeartRateBaseline"]
    assert hr.set_up == 109.0 and 100.0 < hr.stabilized < 112.0
    assert all(abs(s.relative) < 0.5 for s in shifts.values()), shifts
    state = tmp_path / "state.xml"
    e.save_state(state)
    assert check.after_stabilization(state) == check.after_stabilization(e)
