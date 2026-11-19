"""pybiogears.generate_data: BioGears' CSV-to-XML converters (bg-cli GENERATE data), run
from Python on your own templates.

Oracles: BioGears' shipped data files (its converters reproduce them from its own
templates), BioGears' engine (a patient or substance from a new column loads and
behaves), and the guards generate_data adds around the converters."""

import csv
import hashlib
import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import pybiogears as pb
from pybiogears import DataGenerationError, generate_data

HERE = Path(__file__).resolve().parent
VENDORED = HERE.parent / "src" / "cpp" / "biogears_data"
NEEDS_RUNTIME = pytest.mark.skipif("BIOGEARS_RUNTIME" not in os.environ, reason="needs $BIOGEARS_RUNTIME")
NS = "{uri:/mil/tatrc/physiology/datamodel}"


def runtime() -> Path:
    return Path(os.environ["BIOGEARS_RUNTIME"])


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.reader(f))


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def column(rows, name):
    """Index of the column whose Name row holds ``name``."""
    return [c.strip() for c in next(r for r in rows if r and r[0] == "Name")].index(name)


def add_column(rows, values: dict, copy_of=None):
    """Append a column: ``values`` by row label, the rest copied from ``copy_of`` (or empty)."""
    src = None if copy_of is None else column(rows, copy_of)
    width = max(len(r) for r in rows)
    out = []
    for r in rows:
        r = r + [""] * (width - len(r))
        cell = values.get(r[0], r[src] if src is not None and r[0] else "") if r else ""
        out.append(r + [cell])
    return out


def canonical(path):
    root = ET.parse(path).getroot()
    root.attrib.pop("contentVersion", None)          # the BioGears version that wrote it
    for e in root.iter():
        e.text = e.text.strip() if e.text else None
        e.tail = e.tail.strip() if e.tail else None
    return ET.tostring(root)


@pytest.fixture
def templates(tmp_path):
    out = tmp_path / "templates"
    shutil.copytree(runtime() / "templates", out)
    return out


# -- the vendored converters -----------------------------------------------------------

def test_the_vendored_converters_are_unmodified():
    """Each file's SHA-256 is the one recorded from BioGears 8.2.0 (src/cpp/biogears_data/README.md)."""
    table = dict(re.findall(r"\| `([^`]+)` \| `([0-9a-f]{64})` \|", (VENDORED / "README.md").read_text()))
    sources = sorted(p.name for p in VENDORED.iterdir() if p.suffix in (".h", ".cpp"))
    assert sorted(table) == sources and len(sources) == 14
    for name in sources:
        assert hashlib.sha256((VENDORED / name).read_bytes()).hexdigest() == table[name], name


# -- the conversion --------------------------------------------------------------------

@NEEDS_RUNTIME
def test_bioGears_templates_reproduce_its_shipped_files(templates, tmp_path):
    """Oracle: BioGears' shipped data. Every file its templates describe comes out as shipped
    (apart from the version stamp), except three substances whose shipped files were edited
    after the template (EC50 in ug/L, the template's ug/mL)."""
    out = generate_data(templates, tmp_path / "data")
    assert {k: len(v) for k, v in out.files.items()} == {
        "substances": 60, "environments": 13, "patients": 20, "nutrition": 4, "compounds": 17}
    assert all(out.parsed.values())
    # BioGears' template fills a row its converter doesn't read; that is reported, not an error.
    assert out.messages == {"substances": [], "environments": [], "nutrition": [], "compounds": [],
                            "patients": ["Patients.csv row 'RightLungRatio' is not read by BioGears 8.2.0; "
                                         "its values are ignored"]}
    differ, same = [], 0
    for folder in ("patients", "substances", "environments", "nutrition"):
        for f in sorted((tmp_path / "data" / folder).iterdir()):
            shipped = runtime() / folder / f.name
            if shipped.is_file():
                if canonical(f) == canonical(shipped):
                    same += 1
                else:
                    differ.append(f.name)
    assert same == 93
    assert differ == ["Etomidate.xml", "Fentanyl.xml", "Morphine.xml"]


@NEEDS_RUNTIME
def test_a_new_patient_column_becomes_a_patient_biogears_stabilizes(templates, tmp_path):
    """Oracle: BioGears itself. A column added to Patients.csv comes out as a patient
    file with its values, and BioGears builds and stabilizes that patient."""
    rows = read_csv(templates / "Patients.csv")
    rows = add_column(rows, {"Name": "Avery", "Sex": "Female", "Age": "30 yr", "Weight": "140 lb",
                             "Height": "65 in", "BodyFatFraction": "0.25", "HeartRateBaseline": "68 bpm",
                             "SystolicArterialPressureBaseline": "110 mmHg",
                             "DiastolicArterialPressureBaseline": "72 mmHg", "RespirationRateBaseline": "14 bpm"})
    write_csv(templates / "Patients.csv", rows)
    out = generate_data(templates, tmp_path / "data", kinds=["patients"])
    avery = tmp_path / "data" / "patients" / "Avery.xml"
    assert avery in out.files["patients"] and len(out.files["patients"]) == 21
    root = ET.parse(avery).getroot()
    assert root.find(f"{NS}Name").text == "Avery" and root.find(f"{NS}Sex").text == "Female"
    assert root.find(f"{NS}Age").get("value") == "30" and root.find(f"{NS}Age").get("unit") == "yr"

    e = pb.BioGearsEngine(runtime())
    e.set_requests([pb.DataRequest("Physiology", "HeartRate", "1/min"),
                    pb.DataRequest("Patient", "Age", "yr")])
    e.initialize(str(avery))
    assert e.patient()["Name"] == "Avery"
    heart_rate, age = e.pull()[1:]
    assert age == pytest.approx(30.0) and abs(heart_rate - 68.0) < 5.0


@NEEDS_RUNTIME
def test_a_new_substance_column_loads_into_the_engine(templates, tmp_path):
    """Oracle: BioGears itself. A substance from a new Substances.csv column, placed in the
    runtime's substances/, is known to the engine and can be infused."""
    rows = add_column(read_csv(templates / "Substances.csv"), {"Name": "Morphine2"}, copy_of="Morphine")
    write_csv(templates / "Substances.csv", rows)
    out = generate_data(templates, tmp_path / "data", kinds=["substances"])
    new = tmp_path / "data" / "substances" / "Morphine2.xml"
    assert new in out.files["substances"]

    # A runtime with the new substance: BioGears loads every file in substances/ at start.
    rt = tmp_path / "runtime"
    rt.mkdir()
    for item in runtime().iterdir():
        if item.name != "substances":
            (rt / item.name).symlink_to(item)
    shutil.copytree(runtime() / "substances", rt / "substances")
    shutil.copy(new, rt / "substances")

    e = pb.BioGearsEngine(rt)
    e.set_requests([pb.DataRequest("Substance", "PlasmaConcentration", "ug/L", substance="Morphine2")])
    e.load_state(rt / "states" / "StandardMale@0s.xml")
    assert not e.unconnected
    e.process_actions(pb.action("SubstanceInfusion", Substance="Morphine2", Concentration=(1.0, "mg/mL"),
                                Rate=(1.0, "mL/min")))
    e.advance(60.0)
    assert e.pull()[1] > 0.0


# -- what generate_data adds around the converters ------------------------------------

@NEEDS_RUNTIME
def test_existing_output_raises_unless_replaced_and_replacing_drops_removed_items(templates, tmp_path):
    """Falsifies: BioGears' converters never delete a file, so without emptying the output a
    patient removed from the CSV would linger."""
    generate_data(templates, tmp_path / "data", kinds=["patients"])
    with pytest.raises(FileExistsError, match="replace=True"):
        generate_data(templates, tmp_path / "data", kinds=["patients"])
    rows = read_csv(templates / "Patients.csv")
    drop = column(rows, "Gus")
    write_csv(templates / "Patients.csv", [r[:drop] + r[drop + 1:] for r in rows])
    out = generate_data(templates, tmp_path / "data", kinds=["patients"], replace=True)
    assert len(out.files["patients"]) == 19
    assert not (tmp_path / "data" / "patients" / "Gus.xml").exists()


@NEEDS_RUNTIME
def test_a_value_biogears_cannot_convert_raises(templates, tmp_path):
    """Falsifies: BioGears silently leaves an unconvertible value out of the file (its patient
    converter doesn't even report it)."""
    rows = add_column(read_csv(templates / "Patients.csv"),
                      {"Name": "Typo", "Sex": "Male", "Age": "forty yr"}, copy_of="StandardMale")
    write_csv(templates / "Patients.csv", rows)
    with pytest.raises(DataGenerationError, match=r"Typo Age = 'forty yr'"):
        generate_data(templates, tmp_path / "data", kinds=["patients"])
    assert not (tmp_path / "data").exists()                # nothing written
    out = generate_data(templates, tmp_path / "data", kinds=["patients"], strict=False)
    assert out.parsed["patients"] is False
    typo = ET.parse(tmp_path / "data" / "patients" / "Typo.xml").getroot()
    assert typo.find(f"{NS}Age") is None                   # what BioGears does with it


@NEEDS_RUNTIME
def test_two_items_with_one_name_raise(templates, tmp_path):
    rows = add_column(read_csv(templates / "Patients.csv"), {}, copy_of="StandardMale")
    write_csv(templates / "Patients.csv", rows)
    with pytest.raises(DataGenerationError, match="share a Name"):
        generate_data(templates, tmp_path / "data", kinds=["patients"])


def test_bad_requests_raise(tmp_path):
    (tmp_path / "Patients.csv").write_text("Name,X\nSex,Male\n")
    with pytest.raises(NotImplementedError, match="crashes"):
        generate_data(tmp_path, tmp_path / "out", kinds=["stabilizations"])
    with pytest.raises(ValueError, match="unknown kind"):
        generate_data(tmp_path, tmp_path / "out", kinds=["drugs"])
    with pytest.raises(FileNotFoundError, match="Substances.csv"):
        generate_data(tmp_path, tmp_path / "out", kinds=["substances"])
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="none of"):
        generate_data(empty, tmp_path / "out")
    with pytest.raises(ValueError, match="no CSV given"):
        generate_data({"patients": tmp_path / "Patients.csv"}, tmp_path / "out", kinds=["substances"])


def test_a_mapping_of_csv_files_is_accepted(tmp_path):
    """Any file name works when the kinds are given as a mapping."""
    path = tmp_path / "my_patients.csv"
    path.write_text("Name,Solo\nSex,Male\nAge,40 yr\n")
    out = generate_data({"patients": path}, tmp_path / "data")
    assert [p.name for p in out.files["patients"]] == ["Solo.xml"]
