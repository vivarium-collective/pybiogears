"""What BioGears will do with a patient, before it stabilizes one.

BioGears checks a patient file when it builds the patient (``BioGears::SetupPatient``,
the first step of :meth:`BioGearsEngine.initialize`). Some values it refuses (the
engine fails to initialize), some it warns about and uses, some it replaces with other
values, and anything missing it fills in. Several of those changes are easy to miss:
a weight above BMI 30 is lowered to BMI 30, a heart rate above 109 /min is lowered to
109 at Info level, and given baselines such as the respiratory driver amplitude are
replaced without a message. :func:`check_patient` reports all of it from the patient
alone, in microseconds, without a runtime::

    check = pybiogears.check_patient("my_data/patients/Avery.xml")
    check.ok                 # False if BioGears would refuse the patient
    print(check.report())    # errors, warnings, adjusted values, defaults
    check.values["Weight"]   # the weight BioGears will use, in kg

The rules are BioGears 8.2.0's, transcribed from ``SetupPatient`` (BioGears.cpp) in
its order, with its constants. The tests compare every finding with the messages
BioGears itself logs for the same patient.

Stabilization then moves some baselines again: BioGears replaces the heart rate,
arterial pressures, respiration rate, tidal volume, functional residual capacity and
blood volume with the values the engine settles at.
:meth:`PatientCheck.after_stabilization` compares those with what was set up.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_NS = "{uri:/mil/tatrc/physiology/datamodel}"

# -- units ------------------------------------------------------------------------------
# The units each BioGears scalar type accepts (SEScalar<Type>.cpp, IsValidUnit), with
# factors to the unit used here, from BioGears' unit definitions (UCEDefs.conf).
_LB_KG = 16 * 28.349523125 / 1000.0
_IN_CM = 2.54
_MMHG_PA = 133.322368
_KCAL_J = 4184.0
_UNITS: dict[str, tuple[str, dict[str, float]]] = {
    "Time": ("yr", {"yr": 1.0, "day": 1 / 365.242199, "hr": 1 / (24 * 365.242199),
                    "min": 1 / (60 * 24 * 365.242199), "s": 1 / (3600 * 24 * 365.242199)}),
    "Mass": ("kg", {"kg": 1.0, "g": 1e-3, "mg": 1e-6, "ug": 1e-9, "lb": _LB_KG}),
    "Length": ("cm", {"cm": 1.0, "m": 100.0, "mm": 0.1, "um": 1e-4, "in": _IN_CM, "ft": 12 * _IN_CM}),
    "Frequency": ("1/min", {"1/min": 1.0, "1/s": 60.0, "Hz": 60.0, "1/hr": 1 / 60.0}),
    "Pressure": ("mmHg", {"mmHg": 1.0, "Pa": 1 / _MMHG_PA, "cmH2O": 98.0665 / _MMHG_PA,
                          "psi": _LB_KG * 9.80665 / (0.0254 ** 2) / _MMHG_PA, "atm": 760.0}),
    "Volume": ("L", {"L": 1.0, "dL": 0.1, "mL": 1e-3, "uL": 1e-6, "m^3": 1000.0}),
    "Area": ("m^2", {"m^2": 1.0, "cm^2": 1e-4}),
    "Power": ("W", {"W": 1.0, "J/s": 1.0, "kcal/s": _KCAL_J, "kcal/hr": _KCAL_J / 3600,
                    "kcal/day": _KCAL_J / 86400, "BTU/hr": 1055.05585 / 3600}),
    "MassPerVolume": ("g/cm^3", {"g/cm^3": 1.0, "g/mL": 1.0, "g/L": 1e-3, "g/dL": 1e-2, "g/m^3": 1e-6,
                                 "kg/m^3": 1e-3, "kg/L": 1.0, "kg/mL": 1e3, "mg/mL": 1e-3, "mg/L": 1e-6,
                                 "mg/dL": 1e-5, "mg/m^3": 1e-9, "ug/mL": 1e-6, "ug/L": 1e-9}),
    "VolumePerTime": ("L/min", {"L/min": 1.0, "L/s": 60.0, "mL/s": 0.06, "mL/min": 1e-3, "mL/hr": 1e-3 / 60,
                                "mL/day": 1e-3 / 1440, "L/day": 1 / 1440, "m^3/s": 60000.0}),
    "Scalar": ("", {"": 1.0}),
    "Neg1To1": ("", {"": 1.0}),
}

#: The patient file's elements, in the order BioGears' schema requires (Patient.xsd), with
#: the scalar type of each (None: text).
FIELDS: dict[str, str | None] = {
    "Annotation": None, "Name": None, "Sex": None, "Age": "Time", "Weight": "Mass", "BodyMassIndex": "Scalar",
    "Height": "Length", "BMI": "Scalar", "BodyDensity": "MassPerVolume", "BodyFatFraction": "Scalar",
    "LeanBodyMass": "Mass", "MaxWorkRate": "Power", "MuscleMass": "Mass", "BloodTypeABO": None,
    "BloodTypeRh": None, "AlveoliSurfaceArea": "Area", "Hyperhidrosis": "Neg1To1", "RightLungRatio": "Scalar",
    "SkinSurfaceArea": "Area", "SleepAmount": "Time", "PainSusceptibility": "Neg1To1",
    "BasalMetabolicRate": "Power", "BloodVolumeBaseline": "Volume", "DiastolicArterialPressureBaseline": "Pressure",
    "HeartRateBaseline": "Frequency", "MeanArterialPressureBaseline": "Pressure",
    "RespirationRateBaseline": "Frequency", "SystolicArterialPressureBaseline": "Pressure",
    "TidalVolumeBaseline": "Volume", "HeartRateMaximum": "Frequency", "HeartRateMinimum": "Frequency",
    "ExpiratoryReserveVolume": "Volume", "FunctionalResidualCapacity": "Volume", "InspiratoryCapacity": "Volume",
    "InspiratoryReserveVolume": "Volume", "ResidualVolume": "Volume", "RespiratoryDriverAmplitudeBaseline": "Pressure",
    "TotalLungCapacity": "Volume", "TotalVentilationBaseline": "VolumePerTime", "VitalCapacity": "Volume",
    "ActiveEvent": None,
}
#: Units of :attr:`PatientCheck.values` (the unit BioGears' rules use for each field).
UNITS: dict[str, str] = {name: _UNITS[kind][0] for name, kind in FIELDS.items() if kind}
UNITS.update({"SleepAmount": "hr", "Height": "cm", "BloodVolumeBaseline": "mL", "BasalMetabolicRate": "kcal/day"})
_TO_RULE_UNIT = {"SleepAmount": 365.242199 * 24, "BloodVolumeBaseline": 1000.0,
                 "BasalMetabolicRate": 86400 / _KCAL_J}

#: The baselines BioGears replaces with the engine's own values when it stabilizes.
STABILIZED = ("HeartRateBaseline", "SystolicArterialPressureBaseline", "DiastolicArterialPressureBaseline",
              "MeanArterialPressureBaseline", "RespirationRateBaseline", "TidalVolumeBaseline",
              "FunctionalResidualCapacity", "BloodVolumeBaseline")

LEVELS = ("error", "warning", "adjusted", "default", "info")


class PatientError(ValueError):
    """BioGears would refuse the patient."""


@dataclass(frozen=True)
class Finding:
    """One thing BioGears does with a patient value.

    ``level`` is one of :data:`LEVELS`:

    - ``error``: BioGears refuses the patient (initialize fails);
    - ``warning``: BioGears warns and uses the value;
    - ``adjusted``: BioGears uses a different value from the one given;
    - ``default``: nothing was given; BioGears uses ``used``;
    - ``info``: a note (a tachycardic or bradycardic baseline).

    ``logged`` is the level BioGears logs it at (``"Error"``, ``"Warning"``, ``"Info"``),
    or None when BioGears changes the value without a message.
    """

    level: str
    field: str
    message: str
    given: float | str | None = None
    used: float | str | None = None
    logged: str | None = None

    def __str__(self) -> str:
        return f"{self.level}: {self.field}: {self.message}"


@dataclass(frozen=True)
class Shift:
    """A baseline moved by stabilization (:meth:`PatientCheck.after_stabilization`)."""

    field: str
    unit: str
    set_up: float
    stabilized: float

    @property
    def change(self) -> float:
        return self.stabilized - self.set_up

    @property
    def relative(self) -> float:
        return self.change / self.set_up if self.set_up else math.inf


@dataclass
class PatientCheck:
    """What BioGears 8.2.0 will do with one patient (:func:`check_patient`)."""

    name: str | None
    source: Path | None
    findings: list[Finding] = field(default_factory=list)
    #: The values BioGears sets the patient up with (given, adjusted or defaulted), in
    #: :data:`UNITS`; text fields (Name, Sex, BloodTypeABO) as str, BloodTypeRh as bool.
    values: dict[str, Any] = field(default_factory=dict)

    def _at(self, level):
        return [f for f in self.findings if f.level == level]

    @property
    def errors(self) -> list[Finding]:
        return self._at("error")

    @property
    def warnings(self) -> list[Finding]:
        return self._at("warning")

    @property
    def adjusted(self) -> list[Finding]:
        return self._at("adjusted")

    @property
    def defaults(self) -> list[Finding]:
        return self._at("default")

    @property
    def ok(self) -> bool:
        """True unless BioGears would refuse the patient."""
        return not self.errors

    def raise_for_errors(self) -> PatientCheck:
        if self.errors:
            where = f" ({self.source})" if self.source else ""
            raise PatientError(f"BioGears would refuse patient {self.name!r}{where}: "
                               + "; ".join(f"{f.field}: {f.message}" for f in self.errors))
        return self

    def report(self, defaults: bool = False) -> str:
        """The findings as text, most serious first (defaults only if asked)."""
        head = f"Patient {self.name or '(unnamed)'}" + (f" ({self.source})" if self.source else "")
        shown = [lvl for lvl in ("error", "adjusted", "warning", "info", "default") if defaults or lvl != "default"]
        lines = [head + (": BioGears would refuse it" if self.errors else ": BioGears accepts it")]
        for lvl in shown:
            for f in self._at(lvl):
                lines.append(f"  {f.level:<8} {f.field}: {f.message}")
        if not defaults and self.defaults:
            lines.append(f"  ({len(self.defaults)} values not given; BioGears fills them in: "
                         + ", ".join(f.field for f in self.defaults) + ")")
        return "\n".join(lines)

    def after_stabilization(self, stabilized) -> list[Shift]:
        """How stabilization moved the baselines BioGears replaces (:data:`STABILIZED`).

        ``stabilized`` is a :class:`BioGearsEngine` after ``initialize``, a saved state
        (path or bytes) or its patient (``engine.patient()``).
        """
        patient = _stabilized_patient(stabilized)
        out = []
        for name in STABILIZED:
            if name not in patient or name not in self.values:
                continue
            value, unit = patient[name]
            out.append(Shift(name, UNITS[name], self.values[name], _to_rule_unit(name, float(value), unit)))
        return out


def _stabilized_patient(stabilized) -> dict[str, Any]:
    from .engine import _patient
    if isinstance(stabilized, Mapping):
        return dict(stabilized)
    if hasattr(stabilized, "patient") and callable(stabilized.patient):
        return stabilized.patient()
    if isinstance(stabilized, (bytes, bytearray)):
        return _patient(bytes(stabilized))
    return _patient(Path(stabilized).expanduser().read_bytes())


def _to_rule_unit(name: str, value: float, unit: str) -> float:
    kind = FIELDS[name]
    factors = _UNITS[kind][1]
    if unit not in factors:
        raise ValueError(f"{name}: unit {unit!r} is not one BioGears accepts here ({sorted(factors)})")
    return value * factors[unit] * _TO_RULE_UNIT.get(name, 1.0)


# -- reading ------------------------------------------------------------------------------

def _read(patient) -> tuple[dict[str, Any], str | None, Path | None, list[Finding]]:
    """{field: (value, unit) | str} from a file, XML bytes or a mapping, and file problems."""
    source = None
    if isinstance(patient, Mapping):
        return _from_mapping(patient), None, None, []
    if isinstance(patient, (bytes, bytearray)):
        text = bytes(patient)
    else:
        source = Path(patient).expanduser()
        if not source.is_file():
            raise FileNotFoundError(f"no patient file at {source}")
        text = source.read_bytes()
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return {}, None, source, [Finding("error", "file", f"not well-formed XML: {e}", logged="Error")]
    raw, problems = {}, []
    if root.tag != f"{_NS}Patient":
        problems.append(Finding("error", "file", f"the root element is {root.tag!r}, not a BioGears <Patient>",
                                logged="Error"))
        return raw, None, source, problems
    order = list(FIELDS)
    last = -1
    for el in root:
        if not isinstance(el.tag, str):
            continue                                  # comments
        name = el.tag[len(_NS):] if el.tag.startswith(_NS) else el.tag
        if name not in FIELDS:
            problems.append(Finding("error", name, "BioGears' patient schema has no such element; "
                                    "BioGears can't read the file", logged="Error"))
            continue
        i = order.index(name)
        if i < last or (i == last and name != "ActiveEvent"):
            problems.append(Finding("error", name, "out of order or repeated (BioGears' schema fixes the "
                                    "order of a patient's elements); BioGears can't read the file", logged="Error"))
        last = max(last, i)
        if FIELDS[name] is None:
            raw[name] = (el.text or "").strip()
        else:
            raw[name] = (el.get("value"), el.get("unit", ""))
    return raw, raw.get("Name") if isinstance(raw.get("Name"), str) else None, source, problems


def _from_mapping(patient: Mapping) -> dict[str, Any]:
    raw = {}
    for name, v in patient.items():
        if FIELDS.get(name, "") is None:
            raw[name] = v if isinstance(v, (str, bool)) else str(v)
        elif isinstance(v, tuple):
            raw[name] = (v[0], v[1] if len(v) > 1 else "")
        elif isinstance(v, str) and " " in v.strip():
            value, unit = v.strip().split(None, 1)
            raw[name] = (value, unit)
        else:
            raw[name] = (v, "")
    return raw


def _values(raw: dict[str, Any]) -> tuple[dict[str, Any], list[Finding]]:
    values, problems = {}, []
    for name, v in raw.items():
        kind = FIELDS.get(name, "")
        if kind == "":
            problems.append(Finding("error", name, "BioGears' patient schema has no such element; "
                                    "BioGears can't read the file", logged="Error"))
        elif kind is None:
            if name == "Sex" and v not in ("Male", "Female"):
                problems.append(Finding("error", name, f"{v!r} is not Male or Female; BioGears can't read the file",
                                        given=v, logged="Error"))
            elif name == "BloodTypeABO" and v not in ("A", "B", "AB", "O"):
                problems.append(Finding("error", name, f"{v!r} is not A, B, AB or O; BioGears can't read the file",
                                        given=v, logged="Error"))
            elif name == "BloodTypeRh":
                rh = {"true": True, "1": True, "false": False, "0": False}.get(str(v).strip().lower()) \
                    if not isinstance(v, bool) else v
                if rh is None:
                    problems.append(Finding("error", name, f"{v!r} is not true or false; BioGears can't read "
                                            "the file", given=v, logged="Error"))
                else:
                    values[name] = rh
            elif name != "ActiveEvent":
                values[name] = v
        else:
            value, unit = v
            try:
                number = float(value)
            except (TypeError, ValueError):
                problems.append(Finding("error", name, f"value {value!r} is not a number; BioGears can't read the "
                                        "file", given=value, logged="Error"))
                continue
            factors = _UNITS[kind][1]
            if unit not in factors:
                accepted = ", ".join(repr(u) for u in factors)
                problems.append(Finding("error", name, f"unit {unit!r} is not one BioGears accepts for this value "
                                        f"({accepted})", given=f"{value} {unit}", logged="Error"))
                continue
            if kind == "Neg1To1" and not -1.0 <= number <= 1.0:
                problems.append(Finding("error", name, f"{number:g} is outside -1 to 1; BioGears can't read the file",
                                        given=number, logged="Error"))
                continue
            values[name] = _to_rule_unit(name, number, unit)
    return values, problems


# -- BioGears' rules (BioGears::SetupPatient, 8.2.0) --------------------------------------

def check_patient(patient) -> PatientCheck:
    """What BioGears 8.2.0 will do with ``patient`` when it sets it up.

    ``patient`` is a patient file (path), its XML (bytes), or a mapping of patient
    fields: ``{"Sex": "Female", "Age": (30, "yr"), "Weight": "140 lb", ...}``, values as
    ``(value, unit)``, ``"value unit"`` or a bare number (unitless fields).
    """
    raw, name, source, problems = _read(patient)
    values, unreadable = _values(raw)
    check = PatientCheck(name or (values.get("Name") if isinstance(values.get("Name"), str) else None), source)
    check.findings += problems + unreadable
    check.values = values
    if not check.findings:        # an unreadable file: BioGears doesn't get as far as its rules
        _Rules(check, values).run()
    return check


def check_patients(source) -> dict[str, PatientCheck]:
    """:func:`check_patient` for several: a folder of patient files, a
    :class:`GeneratedData` (its patients), or an iterable of files. Keyed by patient name
    (the file name if it has none)."""
    from .data import GeneratedData
    if isinstance(source, GeneratedData):
        files = source.files.get("patients", [])
    elif isinstance(source, (str, Path)) and Path(source).expanduser().is_dir():
        files = sorted(Path(source).expanduser().glob("*.xml"))
    else:
        files = list(source)
    out = {}
    for f in files:
        c = check_patient(f)
        out[c.name or Path(f).stem] = c
    return out


class _Rules:
    def __init__(self, check: PatientCheck, values: dict[str, Any]):
        self.c = check
        self.v = values
        self.given = dict(values)

    def add(self, level, fieldname, message, used=None, logged=None, given=None):
        g = self.given.get(fieldname) if given is None else given
        self.c.findings.append(Finding(level, fieldname, message, g, used, logged))

    def default(self, name, value, message, logged="Info"):
        self.v[name] = value
        self.add("default", name, message, used=value, logged=logged)

    def has(self, name):
        return name in self.v

    def computed(self, name, computed, label, unit, fmt="{:g}"):
        """Fill ``name`` with ``computed`` if missing; warn if given and different."""
        if not self.has(name):
            self.default(name, computed, f"not given; BioGears computes {fmt.format(computed)} {unit}")
        elif self.v[name] != computed:
            self.add("warning", name, f"{fmt.format(self.v[name])} {unit} differs from the {fmt.format(computed)} "
                     f"{unit} BioGears computes for this patient; {label}", used=self.v[name], logged="Warning")
        return self.v[name]

    def cannot_set(self, name, why):
        if self.has(name):
            self.add("error", name, f"can't be given; {why}", logged="Error")

    def run(self):
        v, add = self.v, self.add
        if not self.has("Sex"):
            add("error", "Sex", "missing; BioGears needs the patient's sex", logged="Error")
            return                       # every other rule depends on it (BioGears assumes Male)
        female = v["Sex"] == "Female"

        if not self.has("Age"):
            self.default("Age", 44.0, "not given; BioGears uses 44 yr")
        age = v["Age"]
        if age < 18.0:
            add("error", "Age", f"{age:g} yr is below 18 yr; BioGears doesn't model pediatrics", logged="Error")
        elif age > 105.0:
            add("error", "Age", f"{age:g} yr is above 105 yr; BioGears doesn't model geriatrics", logged="Error")

        if not self.has("PainSusceptibility"):
            self.default("PainSusceptibility", 0.0, "not given; BioGears uses 0")
        if not self.has("Hyperhidrosis"):
            self.default("Hyperhidrosis", 0.0, "not given; BioGears uses 0")
        if not self.has("SleepAmount"):
            self.default("SleepAmount", 8.0, "not given; BioGears uses 8 hr")
        if v["SleepAmount"] <= 0:
            add("adjusted", "SleepAmount", f"{v['SleepAmount']:g} hr is not positive; BioGears uses 8 hr instead",
                used=8.0, logged="Info")
            v["SleepAmount"] = 8.0

        h_min, h_max, h_std = (151.0, 175.5, 163.0) if female else (163.0, 190.0, 177.0)
        if not self.has("Height"):
            self.default("Height", h_std, f"not given; BioGears uses {h_std:g} cm")
        height = v["Height"]
        height_ft = height / (12 * _IN_CM)
        if height_ft < 4.5 or height_ft > 7.0:
            add("error", "Height", f"{height:g} cm is outside 4.5-7.0 ft (137.2-213.4 cm)", logged="Error")
        if height < h_min:
            add("warning", "Height", f"{height:g} cm is below the 3rd percentile ({h_min:g} cm); no guarantee of "
                "model validity", logged="Warning")
        elif height > h_max:
            add("warning", "Height", f"{height:g} cm is above the 97th percentile ({h_max:g} cm); no guarantee of "
                "model validity", logged="Warning")

        height_m2 = (height / 100.0) ** 2
        if not self.has("Weight"):
            self.default("Weight", 21.75 * height_m2, f"not given; BioGears uses BMI 21.75: {21.75 * height_m2:.1f} kg")
        weight = v["Weight"]
        bmi = weight / height_m2
        if bmi > 30.0:
            weight = 30.0 * height_m2
            add("adjusted", "Weight", f"BMI {bmi:.1f} is above 30 (obesity must be modelled with a condition); "
                f"BioGears lowers the weight to BMI 30: {weight:.1f} kg", used=weight, logged="Warning")
            bmi = weight / height_m2
            v["Weight"] = weight
        if bmi > 25.0:
            add("warning", "Weight", f"BMI {bmi:.1f} is overweight (above 25); no guarantee of model validity",
                logged="Warning")
        if bmi < 16.0:
            weight = 16.0 * height_m2
            add("adjusted", "Weight", f"BMI {bmi:.1f} is below 16 (severe underweight must be modelled with a "
                f"condition); BioGears raises the weight to BMI 16: {weight:.1f} kg", used=weight, logged="Warning")
            bmi = weight / height_m2
            v["Weight"] = weight
        if bmi < 18.5:
            add("warning", "Weight", f"BMI {bmi:.1f} is underweight (below 18.5); no guarantee of model validity",
                logged="Warning")

        f_min, f_max, f_std = (0.10, 0.32, 0.28) if female else (0.02, 0.25, 0.21)
        if not self.has("BodyFatFraction"):
            self.default("BodyFatFraction", f_std, f"not given; BioGears uses {f_std:g}")
        fat = v["BodyFatFraction"]
        if fat > f_max:
            add("warning", "BodyFatFraction", f"{fat:g} is above {f_max:g} (obesity must be modelled with a "
                "condition); no guarantee of model validity", logged="Warning")
        elif fat < f_min:
            add("warning", "BodyFatFraction", f"{fat:g} is below the essential fat fraction {f_min:g}; no guarantee "
                "of model validity", logged="Warning")

        self.cannot_set("LeanBodyMass", "BioGears computes it from weight and body fat")
        v["LeanBodyMass"] = weight * (1.0 - fat)
        self.cannot_set("MuscleMass", "BioGears computes it from weight")
        v["MuscleMass"] = weight * (0.306 if female else 0.384)
        self.cannot_set("BodyDensity", "BioGears computes it from body fat")
        v["BodyDensity"] = (4.95 / (fat + 4.50) + 4.57 / (fat + 4.142)) / 2.0

        if not self.has("HeartRateBaseline"):
            self.default("HeartRateBaseline", 72.0, "not given; BioGears uses 72 /min")
        hr = v["HeartRateBaseline"]          # BioGears keeps checking the given value below
        if hr > 100.0:
            if hr <= 109.0:
                add("info", "HeartRateBaseline", f"{hr:g} /min is tachycardic", logged="Info")
            else:
                add("adjusted", "HeartRateBaseline", f"{hr:g} /min is above the 109 /min BioGears can stabilize; "
                    "it uses 109 /min (logged only at Info level)", used=109.0, logged="Info")
                v["HeartRateBaseline"] = 109.0
        elif hr < 60.0:
            if hr >= 50.0:
                add("info", "HeartRateBaseline", f"{hr:g} /min is bradycardic", logged="Info")
            else:
                add("adjusted", "HeartRateBaseline", f"{hr:g} /min is below the 50 /min BioGears can stabilize; "
                    "it uses 50 /min (logged only at Info level)", used=50.0, logged="Info")
                v["HeartRateBaseline"] = 50.0

        hr_max = 208.0 - 0.7 * age
        if not self.has("HeartRateMaximum"):
            self.default("HeartRateMaximum", hr_max, f"not given; BioGears computes 208 - 0.7 x age = {hr_max:g} /min")
        else:
            if v["HeartRateMaximum"] < hr:
                add("error", "HeartRateMaximum", f"{v['HeartRateMaximum']:g} /min is below the heart rate baseline "
                    f"({hr:g} /min)", logged="Error")
            add("warning", "HeartRateMaximum", f"{v['HeartRateMaximum']:g} /min differs from the {hr_max:g} /min "
                "BioGears computes (208 - 0.7 x age); no guarantee of model validity", logged="Warning")
        if not self.has("HeartRateMinimum"):
            self.default("HeartRateMinimum", 0.001, "not given; BioGears uses 0.001 /min")
        if v["HeartRateMinimum"] > hr:
            add("error", "HeartRateMinimum", f"{v['HeartRateMinimum']:g} /min is above the heart rate baseline "
                f"({hr:g} /min)", logged="Error")

        if not self.has("SystolicArterialPressureBaseline"):
            self.default("SystolicArterialPressureBaseline", 114.0, "not given; BioGears uses 114 mmHg")
        sbp = v["SystolicArterialPressureBaseline"]
        if sbp < 90.0:
            add("warning", "SystolicArterialPressureBaseline", f"{sbp:g} mmHg is below 90 mmHg (hypotension must be "
                "modelled with a condition); no guarantee of model validity", logged="Warning")
        elif sbp > 120.0:
            add("warning", "SystolicArterialPressureBaseline", f"{sbp:g} mmHg is above 120 mmHg (hypertension must "
                "be modelled with a condition); no guarantee of model validity", logged="Warning")
        if not self.has("DiastolicArterialPressureBaseline"):
            self.default("DiastolicArterialPressureBaseline", 73.5, "not given; BioGears uses 73.5 mmHg")
        dbp = v["DiastolicArterialPressureBaseline"]
        if dbp < 60.0:
            add("warning", "DiastolicArterialPressureBaseline", f"{dbp:g} mmHg is below 60 mmHg (hypotension must "
                "be modelled with a condition); no guarantee of model validity", logged="Warning")
        elif dbp > 80.0:
            add("warning", "DiastolicArterialPressureBaseline", f"{dbp:g} mmHg is above 80 mmHg (hypertension must "
                "be modelled with a condition); no guarantee of model validity", logged="Warning")
        if dbp > 0.75 * sbp:
            add("warning", "PulsePressure", f"diastolic/systolic = {dbp / sbp:.3f}, above 0.75: an abnormally narrow "
                "pulse pressure", given=dbp / sbp, logged="Warning")
        self.cannot_set("MeanArterialPressureBaseline", "BioGears computes it from the systolic and diastolic "
                        "baselines (and stabilization replaces it)")
        v["MeanArterialPressureBaseline"] = 1.0 / 3.0 * sbp + 2.0 / 3.0 * dbp

        if not self.has("BloodTypeRh"):
            v["BloodTypeRh"] = True
        if not self.has("BloodTypeABO"):
            self.default("BloodTypeABO", "AB", "not given; BioGears uses AB")

        bv_computed = 65.6 * weight ** 1.02
        if not self.has("BloodVolumeBaseline"):
            self.default("BloodVolumeBaseline", bv_computed, f"not given; BioGears computes 65.6 x weight^1.02 = "
                         f"{bv_computed:.0f} mL")
        bv = v["BloodVolumeBaseline"]
        if bv != bv_computed:
            add("warning", "BloodVolumeBaseline", f"{bv:.0f} mL differs from the {bv_computed:.0f} mL BioGears "
                "computes from the weight; the patient may not reach a resting state", logged="Warning")
        if bv < 0.85 * bv_computed:
            add("error", "BloodVolumeBaseline", f"{bv:.0f} mL is more than 15 % below the computed {bv_computed:.0f} "
                "mL (hypovolemia must be modelled with a condition)", logged="Error")
        elif bv > 1.15 * bv_computed:
            add("error", "BloodVolumeBaseline", f"{bv:.0f} mL is more than 15 % above the computed {bv_computed:.0f} "
                "mL (excess volume must be modelled with a condition)", logged="Error")

        if not self.has("RespirationRateBaseline"):
            self.default("RespirationRateBaseline", 14.0, "not given; BioGears uses 14 /min")
        rr = v["RespirationRateBaseline"]
        if rr > 20.0:
            add("error", "RespirationRateBaseline", f"{rr:g} /min is above 20 /min (model it with a condition)",
                logged="Error")
        elif rr < 10.0:
            add("error", "RespirationRateBaseline", f"{rr:g} /min is below 10 /min (model it with a condition)",
                logged="Error")

        if not self.has("RightLungRatio"):
            self.default("RightLungRatio", 0.525, "not given; BioGears uses 0.525")
        rl = v["RightLungRatio"]
        if rl > 0.60:
            add("error", "RightLungRatio", f"{rl:g} is above 0.60", logged="Error")
        elif rl < 0.50:
            add("error", "RightLungRatio", f"{rl:g} is below 0.50", logged="Error")

        lung = "no guarantee of model validity"
        tlc = self.computed("TotalLungCapacity", 80.0 * weight / 1000.0, lung, "L", "{:.3f}")
        frc = self.computed("FunctionalResidualCapacity", 30.0 * weight / 1000.0, lung, "L", "{:.3f}")
        rv = self.computed("ResidualVolume", 20.0 * weight / 1000.0, lung, "L", "{:.3f}")
        for name in ("TidalVolumeBaseline", "VitalCapacity", "ExpiratoryReserveVolume", "InspiratoryReserveVolume",
                     "InspiratoryCapacity"):
            self.cannot_set(name, "BioGears computes it from the other lung volumes"
                            + (" (and stabilization replaces it)" if name == "TidalVolumeBaseline" else ""))
        tidal = 7.0 * weight / 1000.0
        if self.has("TotalVentilationBaseline"):
            add("adjusted", "TotalVentilationBaseline", "BioGears replaces it with tidal volume x respiration rate, "
                "without a message", used=tidal * rr)
        v["TotalVentilationBaseline"] = tidal * rr
        driver = -6.2 + 0.25 * (rr - 12.0) if rr <= 16.0 else -5.2 + 0.125 * (rr - 16.0)
        driver = min(max(driver * frc / 2.31332, -6.5), -3.5)
        if self.has("RespiratoryDriverAmplitudeBaseline"):
            add("adjusted", "RespiratoryDriverAmplitudeBaseline", "BioGears replaces it with its own starting "
                f"amplitude ({driver:.2f} cmH2O), without a message", used=driver)
        v["RespiratoryDriverAmplitudeBaseline"] = driver
        derived = {"VitalCapacity": tlc - rv, "ExpiratoryReserveVolume": frc - rv,
                   "InspiratoryReserveVolume": tlc - frc - tidal, "InspiratoryCapacity": tlc - frc}
        if min(tlc, frc, rv, tidal, *derived.values()) < 0.0:
            add("error", "LungVolumes", "the lung volumes leave a negative volume (total lung capacity > functional "
                "residual capacity + tidal volume > residual volume is needed)", given=None, logged="Error")
        v["TidalVolumeBaseline"] = tidal
        v.update(derived)

        self.computed("AlveoliSurfaceArea", tlc / 6.17 * 70.0, lung, "m^2", "{:.2f}")
        self.computed("SkinSurfaceArea", 0.20247 * weight ** 0.425 * (height / 100.0) ** 0.725, lung, "m^2", "{:.3f}")
        bmr = (447.593 + 9.247 * weight + 3.098 * height - 4.330 * age) if female else \
              (88.632 + 13.397 * weight + 4.799 * height - 5.677 * age)
        self.computed("BasalMetabolicRate", bmr, lung, "kcal/day", "{:.0f}")
        if female:
            work = -20.7 * min(age, 60.0) + 1673.0 if age < 60.0 else -20.7 * 60.0 + 1673.0
        else:
            work = -24.3 * age + 2070.0 if age < 60.0 else -24.3 * 60.0 + 2070.0
        self.computed("MaxWorkRate", work, lung, "W", "{:.1f}")

        for name in ("BodyMassIndex", "BMI"):
            if self.has(name):
                add("adjusted", name, "BioGears doesn't use it (the BMI comes from weight and height)", used=None)
                del v[name]
