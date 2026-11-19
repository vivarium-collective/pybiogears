"""BioGears' data files from its spreadsheet templates: the CSV-to-XML conversion behind
``bg-cli GENERATE data``, run from Python.

BioGears describes patients, substances, compounds, environments and meals in XML files
(``patients/StandardMale.xml``, ``substances/Morphine.xml``, ...). Its command-line tool
can generate them from spreadsheet templates, one column (or block) per item:
``Patients.csv``, ``Substances.csv``, ``Compounds.csv``, ``Environments.csv`` and
``Nutritions.csv``, in the layout of ``<runtime>/templates``. :func:`generate_data` runs
the same converters (BioGears' own source, compiled into pybiogears unmodified; see
``src/cpp/biogears_data``), but on the CSV files you give it and into a folder you
choose, and it checks what they wrote::

    out = pybiogears.generate_data("my_templates/", "my_data/")
    out.files["patients"]        # [Path("my_data/patients/Jane.xml"), ...]

What BioGears' converters do and don't do, and what this adds:

- They write one file per item, named after its ``Name``, and never delete a file. Here
  each run writes into a fresh temporary folder and moves the result into ``out_dir``;
  with ``replace=True`` the output folders are emptied first, so an item removed from a
  CSV doesn't leave its old file behind. Without it, existing output raises.
- A value they can't convert (a number that doesn't parse, ...) is left out of the file,
  silently: BioGears 8.2.0's patient converter doesn't even report it. Here every value
  in ``Patients.csv`` is checked against the patient file written, and a value that
  didn't make it raises (``strict=True``) before anything reaches ``out_dir``. Rows
  BioGears doesn't read (its template's ``RightLungRatio``, ``SkinSurfaceArea``, a
  misspelt label) are listed in ``messages``. For the other kinds only BioGears' own
  "not everything parsed" result is checked.
- Two items with the same name overwrite each other; here that raises.
- ``Stabilizations.csv`` isn't supported: BioGears 8.2.0's converter for it crashes on
  its own template (``bg-cli GENERATE data`` segfaults there too).

The XML is not checked against BioGears' physiological rules (a patient's age or blood
volume, ...): BioGears applies those when it builds the patient
(:meth:`BioGearsEngine.initialize`).

The converters change the process's working directory and console output while they
run, so they run in a child process.
"""

from __future__ import annotations

import csv
import multiprocessing
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path

#: kind -> (template file name, output folder). Compounds are written next to substances,
#: where BioGears looks for both.
KINDS: dict[str, tuple[str, str]] = {
    "patients": ("Patients.csv", "patients"),
    "substances": ("Substances.csv", "substances"),
    "compounds": ("Compounds.csv", "substances"),
    "environments": ("Environments.csv", "environments"),
    "nutrition": ("Nutritions.csv", "nutrition"),
}
UNSUPPORTED = {
    "stabilizations": "BioGears 8.2.0's converter for Stabilizations.csv crashes on its own template "
                      "(bg-cli GENERATE data segfaults there too)",
}
#: The order bg-cli runs them in.
_ORDER = ("substances", "environments", "patients", "nutrition", "compounds")
#: The Patients.csv rows BioGears 8.2.0's patient converter reads, each into the patient
#: element of the same name (PatientGenerator::process; RightLungFraction and
#: SkinSurfaceArea are commented out there).
PATIENT_FIELDS = ("Name", "Sex", "Age", "Weight", "Height", "BodyFatFraction", "BasalMetabolicRate",
                  "BloodVolumeBaseline", "DiastolicArterialPressureBaseline", "HeartRateBaseline",
                  "RespirationRateBaseline", "SystolicArterialPressureBaseline", "HeartRateMaximum",
                  "HeartRateMinimum", "FunctionalResidualCapacity", "TotalLungCapacity", "AlveoliSurfaceArea",
                  "ResidualVolume")
_NS = "{uri:/mil/tatrc/physiology/datamodel}"


class DataGenerationError(RuntimeError):
    """BioGears' converters failed, or wrote something that can't be used."""


@dataclass
class GeneratedData:
    """What :func:`generate_data` wrote."""

    out_dir: Path
    #: kind -> the XML files written, in the order BioGears wrote them.
    files: dict[str, list[Path]] = field(default_factory=dict)
    #: kind -> False if BioGears could not convert every value (only with strict=False).
    parsed: dict[str, bool] = field(default_factory=dict)
    #: kind -> anything else the converter printed (warnings, serialization errors).
    messages: dict[str, list[str]] = field(default_factory=dict)


def _run_generators(workdir: str, kinds: list[str]) -> list[tuple[str, bool, str]]:
    # In the child process (see generate_data).
    from ._pybiogears import generate_data as run
    return [(r.kind, r.parsed, r.log) for r in run(workdir, kinds)]


def _sources(source, kinds) -> dict[str, Path]:
    if isinstance(source, Mapping):
        found = {str(k): Path(v).expanduser() for k, v in source.items()}
        kinds = list(found) if kinds is None else list(kinds)
        missing = [k for k in kinds if k not in found]
        if missing:
            raise ValueError(f"no CSV given for {missing}")
    else:
        root = Path(source).expanduser()
        if not root.is_dir():
            raise FileNotFoundError(f"{root} is not a folder of BioGears templates")
        found = {k: root / name for k, (name, _) in KINDS.items() if (root / name).is_file()}
        if kinds is None:
            kinds = list(found)
            if not kinds:
                raise FileNotFoundError(f"{root} holds none of {[n for n, _ in KINDS.values()]}")
        else:
            kinds = list(kinds)
            missing = [k for k in kinds if k in KINDS and k not in found]
            if missing:
                raise FileNotFoundError(f"{root} has no {[KINDS[k][0] for k in missing]}")
    for k in kinds:
        if k in UNSUPPORTED:
            raise NotImplementedError(f"{k}: {UNSUPPORTED[k]}")
        if k not in KINDS:
            raise ValueError(f"unknown kind {k!r}; one of {list(KINDS)}")
        if not found[k].is_file():
            raise FileNotFoundError(f"{k}: no CSV at {found[k]}")
    return {k: found[k] for k in _ORDER if k in kinds}


def _check_file(path: Path, kind: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise DataGenerationError(f"{kind}: BioGears reported writing {path.name}, but it is missing or empty")
    try:
        ET.parse(path)
    except ET.ParseError as e:
        raise DataGenerationError(f"{kind}: {path.name} is not well-formed XML: {e}") from e


def _patient_values(csv_path: Path):
    """[(patient, label, value)] for every non-empty cell, and the labels BioGears ignores."""
    with open(csv_path, newline="") as f:
        rows = [r for r in csv.reader(f) if r]
    names = next((r[1:] for r in rows if r[0] == "Name"), [])
    values, ignored = [], set()
    for r in rows:
        label = r[0].strip()
        cells = [(names[i].strip(), v.strip()) for i, v in enumerate(r[1:]) if i < len(names) and v.strip()]
        if not cells or label == "Name" or not names:
            continue
        if label in PATIENT_FIELDS:
            values += [(n, label, v) for n, v in cells if n]
        elif label:
            ignored.add(label)
    return values, sorted(ignored)


def _check_patients(csv_path: Path, work: Path, files: list[str]) -> tuple[list[str], list[str]]:
    """(values BioGears dropped, rows it ignores), comparing Patients.csv with the files."""
    values, ignored = _patient_values(csv_path)
    by_name = {}
    for name in files:
        root = ET.parse(work / name).getroot()
        by_name[root.findtext(f"{_NS}Name")] = {child.tag[len(_NS):] for child in root}
    dropped = [f"{patient} {label} = {value!r}" for patient, label, value in values
               if patient in by_name and label not in by_name[patient]]
    return dropped, [f"Patients.csv row {label!r} is not read by BioGears 8.2.0; its values are ignored"
                     for label in ignored]


def generate_data(source: str | Path | Mapping[str, str | Path], out_dir: str | Path,
                  kinds: Iterable[str] | None = None, *, replace: bool = False,
                  strict: bool = True) -> GeneratedData:
    """Convert BioGears spreadsheet templates to BioGears XML data files.

    ``source`` is a folder holding any of ``Patients.csv``, ``Substances.csv``,
    ``Compounds.csv``, ``Environments.csv`` and ``Nutritions.csv`` (the layout of
    ``<runtime>/templates``), or a mapping ``{kind: csv path}``. ``kinds`` (any of
    :data:`KINDS`) defaults to every template found. The XML goes to
    ``out_dir/patients``, ``out_dir/substances`` (substances and compounds),
    ``out_dir/environments`` and ``out_dir/nutrition``.

    Existing files in those folders raise ``FileExistsError`` unless ``replace=True``,
    which empties the folders first. ``strict=True`` raises
    :class:`DataGenerationError` when BioGears could not convert a value (it leaves such
    values out of the file); nothing is written to ``out_dir`` then.
    """
    sources = _sources(source, kinds)
    out = Path(out_dir).expanduser()
    folders = sorted({KINDS[k][1] for k in sources})
    occupied = [out / f for f in folders if (out / f).is_dir() and any((out / f).iterdir())]
    if occupied and not replace:
        raise FileExistsError(f"{[str(p) for p in occupied]} already hold files; pass replace=True to "
                              "replace them (the folders are emptied first)")

    with tempfile.TemporaryDirectory(prefix="pybiogears-data-") as tmp:
        work = Path(tmp)
        (work / "templates").mkdir()
        for k, csv in sources.items():
            shutil.copyfile(csv, work / "templates" / KINDS[k][0])
            (work / KINDS[k][1]).mkdir(exist_ok=True)
        try:
            with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn")) as pool:
                results = pool.submit(_run_generators, str(work), list(sources)).result()
        except BrokenProcessPool as e:
            raise DataGenerationError(f"BioGears' converters crashed on {list(sources)}") from e

        result = GeneratedData(out)
        written: dict[str, str] = {}
        for kind, parsed, log in results:
            folder = KINDS[kind][1]
            files, messages = [], []
            for line in log.splitlines():
                saved = line.strip()
                if saved.startswith(f"Saved {folder}/"):
                    name = saved[len("Saved "):]
                    if name in written:
                        raise DataGenerationError(f"{kind}: {name} is written twice ({written[name]} and {kind}); "
                                                  "two items share a Name")
                    written[name] = kind
                    _check_file(work / name, kind)
                    files.append(name)
                elif saved:
                    messages.append(saved)
            if not files:
                raise DataGenerationError(f"{kind}: BioGears wrote nothing from {KINDS[kind][0]}"
                                          + (f" ({'; '.join(messages)})" if messages else ""))
            if kind == "patients":
                dropped, ignored = _check_patients(work / "templates" / KINDS[kind][0], work, files)
                messages += ignored
                if dropped:
                    parsed = False
                    messages += [f"not converted: {d}" for d in dropped]
                    if strict:
                        raise DataGenerationError(
                            "patients: BioGears could not convert these values and left them out of the files: "
                            + "; ".join(dropped) + " (check the numbers and units, or pass strict=False)")
            if strict and not parsed:
                raise DataGenerationError(
                    f"{kind}: BioGears could not convert every value in {KINDS[kind][0]} and leaves such values "
                    "out of the files; check the numbers and units (or pass strict=False)")
            result.files[kind] = files
            result.parsed[kind] = parsed
            result.messages[kind] = messages

        for folder in folders:
            target = out / folder
            if target.is_dir():
                shutil.rmtree(target)
            target.mkdir(parents=True)
        for kind, names in result.files.items():
            result.files[kind] = [Path(shutil.move(work / name, out / name)) for name in names]
    return result
