"""Thin Python layer over the C++ thunk."""

from __future__ import annotations

import os
import re
import threading
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from ._pybiogears import BioGearsThunk, DataRequest

RUNTIME_ENV = "BIOGEARS_RUNTIME"


#: Empty enum elements BioGears writes but can't read back (anesthesia-machine chamber State).
_EMPTY_ENUM = re.compile(rb"<State\s*/>|<State>\s*</State>")
#: BioGears 8.2.0 doesn't save TotalBloodVolumeLost (and zeroes it on load); pybiogears
#: keeps it in this comment of a saved state and restores it on load.
_BLOOD_LOST = re.compile(rb"<!-- pybiogears TotalBloodVolumeLost_mL=([-+0-9.eE]+) -->")


#: BioGears parses XML with one shared serializer; reading a file takes its lock
#: (Serializer::ReadFile), reading a buffer does not (Serializer::ReadBuffer, used by
#: LoadState(buffer)). Engines in different threads take this lock to load from bytes.
_BUFFER_LOCK = threading.Lock()

_CDM = "uri:/mil/tatrc/physiology/datamodel"
_XSI = "http://www.w3.org/2001/XMLSchema-instance"


def _merge_into(config: ET.Element, override: str, runtime: str) -> None:
    """Merge ``override`` (children of a BioGearsConfiguration) into ``config``: a scalar or
    value replaces the existing one, a section (``RenalConfiguration``) is merged field by
    field. Element order follows BioGears' schema, which its parser checks."""
    from .schema import schema

    new = ET.fromstring(f'<BioGearsConfiguration xmlns="{_CDM}" xmlns:xsi="{_XSI}">{override}</BioGearsConfiguration>')
    sch = schema(runtime)

    def merge(dst, src, type_name):
        fields = [f for f in sch.fields(type_name) if not f.attribute]
        names = [f.name for f in fields]
        types = {f.name: f.type for f in fields}
        for child in list(src):
            tag = child.tag.split("}")[-1]
            if tag not in names:
                raise BioGearsError(f"{type_name[:-4]} has no {tag}; it has {names}")
            old = dst.find(child.tag)
            if old is not None and len(child) and types[tag] in sch.types and not sch.is_scalar(types[tag]):
                merge(old, child, types[tag])
                continue
            if old is not None:
                dst[list(dst).index(old)] = child
                continue
            later = [i for i, e in enumerate(dst) if e.tag.split("}")[-1] in names
                     and names.index(e.tag.split("}")[-1]) > names.index(tag)]
            dst.insert(later[0] if later else len(dst), child)

    merge(config, new, config.get(f"{{{_XSI}}}type", "BioGearsConfigurationData"))


def _merge_configuration(state: bytes, override: str, runtime: str) -> bytes:
    """``state`` with ``override`` merged into its ``<Configuration>`` (BioGears reads a
    state's engine configuration from the state itself)."""
    ET.register_namespace("", _CDM)
    ET.register_namespace("xsi", _XSI)
    root = ET.fromstring(state)
    config = root.find(f"{{{_CDM}}}Configuration")
    if config is None:
        raise BioGearsError("the state has no <Configuration>")
    _merge_into(config, override, runtime)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root)


#: Parts of a state's configuration that are results or bulky data, not settings: the
#: stabilization criteria carry the last run's convergence values, and the ECG waveforms
#: are kept from the engine's defaults when left out.
_NOT_SETTINGS = ("StabilizationCriteria", "ElectroCardioGramInterpolator")
#: A state shipped with BioGears 8.2.0; its configuration is the engine's defaults
#: (checked against a freshly initialized engine: every setting equal).
_DEFAULTS_STATE = "states/StandardMale@0s.xml"


def _complete_configuration(override: str, runtime: str) -> str:
    """A complete configuration (the defaults, every setting written out) with ``override``
    merged in, for BioGears' configuration loader.

    BioGears 8.2.0 can only read a complete configuration file: for each section a file
    leaves out it calls ``Invalidate()`` on the whole configuration, so everything the file
    set is lost (its own BioGearsConfiguration.xml override included), and for an omitted
    field inside a section it reads an absent value (a segfault for an On/Off field). So
    pybiogears writes every setting: the defaults, taken from a shipped state, with the
    override merged in."""
    ET.register_namespace("", _CDM)
    ET.register_namespace("xsi", _XSI)
    path = Path(runtime) / _DEFAULTS_STATE
    if not path.is_file():
        raise BioGearsError(f"a configuration override needs {path} for the engine's defaults")
    config = ET.fromstring(path.read_bytes()).find(f"{{{_CDM}}}Configuration")
    for tag in _NOT_SETTINGS:
        el = config.find(f"{{{_CDM}}}{tag}")
        if el is not None:
            config.remove(el)
    _merge_into(config, override, runtime)
    return "".join(ET.tostring(child, encoding="unicode") for child in config)


def _patient(state: bytes) -> dict[str, Any]:
    """The ``<Patient>`` of a state: scalars as (value, unit) (unit "" if none), text as str."""
    root = ET.fromstring(state)
    patient = root.find(f"{{{_CDM}}}Patient")
    if patient is None:
        raise BioGearsError("the state has no <Patient>")
    out: dict[str, Any] = {}
    for el in patient:
        tag = el.tag.split("}")[-1]
        if el.get("value") is not None:
            out[tag] = (float(el.get("value")), el.get("unit", ""))
        elif len(el) == 0 and el.text is not None:
            out[tag] = el.text.strip()
    return out


def _with_blood_lost(xml: bytes, value: float) -> bytes:
    if value != value:  # NaN: no bleed yet, nothing to keep
        return xml
    return xml.rstrip() + f"\n<!-- pybiogears TotalBloodVolumeLost_mL={value!r} -->\n".encode()


class BioGearsError(RuntimeError):
    pass


class IrreversibleStateError(BioGearsError):
    """The patient reached BioGears' irreversible state; the engine will not advance."""


def _runtime_dir(working_dir: str | os.PathLike | None) -> str:
    if working_dir is None:
        working_dir = os.environ.get(RUNTIME_ENV)
    if working_dir is None:
        raise BioGearsError(
            f"No BioGears runtime directory: pass working_dir or set ${RUNTIME_ENV} "
            "(the directory holding xsd/, substances/, patients/, states/ ...)"
        )
    p = Path(working_dir).expanduser().resolve()
    if not p.is_dir():
        raise BioGearsError(f"BioGears runtime directory not found: {p}")
    return str(p)


class BioGearsEngine:
    """One BioGears engine instance.

    Values come back from :meth:`pull` in the order of the requests given to
    :meth:`set_requests`; requests the engine cannot connect read as NaN and
    are listed in :attr:`unconnected`.
    """

    def __init__(self, working_dir: str | os.PathLike | None = None, log_file: str = "",
                 log_limit: int = 10000):
        self.working_dir = _runtime_dir(working_dir)
        self._log_file = log_file
        self._log_limit = log_limit
        self._thunk = BioGearsThunk(self.working_dir, log_file)
        self._requests: list[DataRequest] = []
        self._connected: list[bool] = []
        # Bounded: BioGears can log every time step (e.g. a bleed rate above
        # cardiac output is re-reported each 0.02 s step).
        self.log: deque = deque(maxlen=log_limit)
        self.events: deque = deque(maxlen=log_limit)
        self._event_handler: Callable[[Any], None] | None = None
        self._log_listener: Callable[[Any], None] | None = None

    def clear(self) -> None:
        """Start over with a new engine (no state; the data requests are kept)."""
        self._thunk = BioGearsThunk(self.working_dir, self._log_file)
        self._connected = list(self._thunk.set_data_requests(self._requests))
        self.log.clear()
        self.events.clear()

    # Pickling (Pulse's PulseEngine pickles too): the working directory, the requests and
    # the current state, which is reloaded on unpickling.
    def __getstate__(self):
        return {"working_dir": self.working_dir, "log_file": self._log_file, "log_limit": self._log_limit,
                "requests": [(r.category, r.property, r.unit, r.compartment, r.substance) for r in self._requests],
                "state": self.save_state_bytes() if self.has_state else None}

    def __setstate__(self, d):
        self.__init__(d["working_dir"], d["log_file"], d["log_limit"])
        self.set_requests([DataRequest(*r) for r in d["requests"]])
        if d["state"] is not None:
            self.load_state_bytes(d["state"])

    @property
    def has_state(self) -> bool:
        """Whether a state is loaded or a patient initialized."""
        return self._thunk.ready

    # -- state ---------------------------------------------------------------
    def load_state(self, path: str | os.PathLike, sim_time_s: float | None = None,
                   configuration: str | None = None) -> None:
        """Load a saved state. ``configuration`` (children of a BioGearsConfiguration, e.g.
        from :func:`pybiogears.schema.configuration`) overrides the state's own engine
        configuration: BioGears reads it from the state, so pybiogears merges it in."""
        text = Path(path).read_bytes()
        if configuration:
            if sim_time_s is not None:
                raise BioGearsError("sim_time_s and configuration can't be combined")
            return self.load_state_bytes(text, configuration=configuration)
        if _EMPTY_ENUM.search(text) or _BLOOD_LOST.search(text):
            # BioGears 8.2.0 saves an anesthesia machine's unused vaporizer chambers as
            # <State/>, which its own schema then rejects on load; the element is optional.
            if sim_time_s is not None:
                raise BioGearsError("sim_time_s is not supported for this state (pybiogears rewrites it on load)")
            return self.load_state_bytes(_EMPTY_ENUM.sub(b"", text))
        ok = self._thunk.load_state(str(path), -1.0 if sim_time_s is None else float(sim_time_s))
        if ok:
            self._restore_blood_lost(text)
        self._drain()
        if not ok:
            raise BioGearsError(f"LoadState failed for {path}: {self._last_errors()}")
        self._connected = list(self._thunk.set_data_requests(self._requests))

    def load_state_bytes(self, xml: bytes, configuration: str | None = None) -> None:
        original = xml
        xml = _EMPTY_ENUM.sub(b"", xml)
        if configuration:
            xml = _merge_configuration(xml, configuration, self.working_dir)
        with _BUFFER_LOCK:
            ok = self._thunk.load_state_buffer(xml)
        xml = original
        if ok:
            self._restore_blood_lost(xml)
        self._drain()
        if not ok:
            raise BioGearsError(f"LoadState(buffer) failed: {self._last_errors()}")
        self._connected = list(self._thunk.set_data_requests(self._requests))

    def _restore_blood_lost(self, xml: bytes) -> None:
        m = _BLOOD_LOST.search(xml)
        if m:
            self._thunk.set_total_blood_volume_lost_mL(float(m.group(1)))

    def save_state(self, path: str | os.PathLike) -> None:
        if not self._thunk.save_state(str(path)):
            raise BioGearsError(f"SaveStateToFile failed for {path}")
        lost = self._thunk.total_blood_volume_lost_mL()
        if lost == lost:
            Path(path).write_bytes(_with_blood_lost(Path(path).read_bytes(), lost))

    def save_state_bytes(self) -> bytes:
        return _with_blood_lost(self._thunk.save_state_to_bytes(), self._thunk.total_blood_volume_lost_mL())

    def initialize(self, patient_file: str, conditions: str | Iterable[str] = "",
                   configuration: str | None = None) -> None:
        """Stabilize ``patient_file`` (BioGears' InitializeEngine), applying ``conditions``:
        <Condition xsi:type="..."> elements in BioGears' scenario schema (ChronicAnemiaData,
        DehydrationData, ...; see :func:`pybiogears.schema.condition`), as one string or
        several. ``configuration`` overrides engine settings (children of a
        BioGearsConfiguration; :func:`pybiogears.schema.configuration`), merged into the
        defaults the way BioGears merges its BioGearsConfiguration.xml. Takes minutes."""
        xml = conditions if isinstance(conditions, str) else "\n".join(conditions)
        config = _complete_configuration(configuration, self.working_dir) if configuration else ""
        ok = self._thunk.initialize_engine(str(patient_file), xml, config)
        self._drain()
        if not ok:
            raise BioGearsError(f"InitializeEngine failed for {patient_file}: {self._last_errors()}")
        self._connected = list(self._thunk.set_data_requests(self._requests))

    # -- requests ------------------------------------------------------------
    def set_requests(self, requests: Sequence[DataRequest]) -> list[bool]:
        self._requests = list(requests)
        self._connected = list(self._thunk.set_data_requests(self._requests))
        self._drain()
        return self._connected

    @property
    def unconnected(self) -> list[DataRequest]:
        return [r for r, ok in zip(self._requests, self._connected) if not ok]

    @property
    def headings(self) -> list[str]:
        return list(self._thunk.request_headings())

    def pull(self) -> np.ndarray:
        """[sim_time_s, v0, v1, ...]"""
        return self._thunk.pull_data()

    def pull_dict(self) -> dict[str, float]:
        v = self.pull()
        out = {"sim_time_s": float(v[0])}
        for h, x in zip(self.headings, v[1:]):
            out[h] = float(x)
        return out

    # -- time and actions ----------------------------------------------------
    def advance(self, seconds: float) -> None:
        ok = self._thunk.advance(float(seconds))
        self._drain()
        if not ok:
            self._advance_failed()

    def _advance_failed(self):
        if self._thunk.irreversible_state:
            raise IrreversibleStateError(
                f"patient reached the irreversible state at t = {self.sim_time_s:.2f} s; the engine will not advance")
        raise BioGearsError(f"AdvanceModelTime failed: {self._last_errors()}")

    @property
    def irreversible_state(self) -> bool:
        return self._thunk.irreversible_state

    def advance_steps(self, n: int) -> None:
        """Advance exactly n engine time steps (see advance_steps in the thunk)."""
        ok = self._thunk.advance_steps(int(n))
        self._drain()
        if not ok:
            self._advance_failed()

    def process_actions(self, actions: str | Iterable[str]) -> None:
        xml = actions if isinstance(actions, str) else "\n".join(actions)
        ok = self._thunk.process_actions(xml)
        self._drain()
        if not ok:
            raise BioGearsError(f"ProcessAction failed: {self._last_errors()}")

    def run_scenario(self, scenario_file: str | os.PathLike, results_file: str | os.PathLike) -> None:
        """Run a BioGears scenario file with BioGears' own scenario executor, writing its
        data requests to ``results_file`` (CSV), as ``bg-scenario`` does. pybiogears'
        workarounds don't apply inside it (it is BioGears' stock behaviour, useful as an
        oracle). Afterwards this engine holds the scenario's final state; its own data
        requests are reconnected."""
        ok = self._thunk.run_scenario(str(scenario_file), str(results_file))
        self._drain()
        self._connected = list(self._thunk.set_data_requests(self._requests))
        if not ok:
            raise BioGearsError(f"scenario {scenario_file} failed: {self._last_errors()}")

    @property
    def active_events(self) -> list[str]:
        """Patient and anesthesia machine events active now (Pulse's ``pull_active_events``),
        e.g. ``["Tachycardia", "SevereSepsis"]``."""
        return list(self._thunk.active_events())

    def patient(self) -> dict[str, Any]:
        """The current patient (BioGears' SEPatient), from a saved copy of the state:
        scalars as ``(value, unit)``, text as str (``Name``, ``Sex``, ...)."""
        if not self.has_state:
            raise BioGearsError("no state loaded")
        return _patient(self._thunk.save_state_to_bytes())

    def set_event_handler(self, handler: Callable[[Any], None] | None) -> None:
        """Call ``handler(event)`` for each patient or anesthesia machine event change
        (:class:`EventChange`: ``event``, ``active``, ``sim_time_s``). Events are delivered
        when the call that produced them (advance, process_actions, ...) returns, in order."""
        self._event_handler = handler

    def set_log_listener(self, listener: Callable[[Any], None] | None) -> None:
        """Call ``listener(message)`` for each BioGears log message (:class:`LogMessage`:
        ``level``, ``text``), delivered like events."""
        self._log_listener = listener

    @property
    def sim_time_s(self) -> float:
        return self._thunk.sim_time_s

    @property
    def time_step_s(self) -> float:
        return self._thunk.time_step_s

    # -- logging -------------------------------------------------------------
    def _drain(self) -> None:
        messages = self._thunk.pull_log_messages()
        events = self._thunk.pull_events()
        self.log.extend(messages)
        self.events.extend(events)
        if self._log_listener is not None:
            for m in messages:
                self._log_listener(m)
        if self._event_handler is not None:
            for e in events:
                self._event_handler(e)

    def _last_errors(self, n: int = 5) -> str:
        errs = [m.text for m in self.log if m.level in ("Error", "Fatal")]
        return " | ".join(errs[-n:]) or "(no error logged)"
