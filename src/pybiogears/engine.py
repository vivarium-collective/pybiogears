"""Thin Python layer over the C++ thunk."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from ._pybiogears import BioGearsThunk, DataRequest

RUNTIME_ENV = "BIOGEARS_RUNTIME"


class BioGearsError(RuntimeError):
    pass


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

    def __init__(self, working_dir: str | os.PathLike | None = None, log_file: str = ""):
        self.working_dir = _runtime_dir(working_dir)
        self._thunk = BioGearsThunk(self.working_dir, log_file)
        self._requests: list[DataRequest] = []
        self._connected: list[bool] = []
        self.log: list = []
        self.events: list = []

    # -- state ---------------------------------------------------------------
    def load_state(self, path: str | os.PathLike, sim_time_s: float | None = None) -> None:
        ok = self._thunk.load_state(str(path), -1.0 if sim_time_s is None else float(sim_time_s))
        self._drain()
        if not ok:
            raise BioGearsError(f"LoadState failed for {path}: {self._last_errors()}")
        self._connected = list(self._thunk.set_data_requests(self._requests))

    def load_state_bytes(self, xml: bytes) -> None:
        ok = self._thunk.load_state_buffer(xml)
        self._drain()
        if not ok:
            raise BioGearsError(f"LoadState(buffer) failed: {self._last_errors()}")
        self._connected = list(self._thunk.set_data_requests(self._requests))

    def save_state(self, path: str | os.PathLike) -> None:
        if not self._thunk.save_state(str(path)):
            raise BioGearsError(f"SaveStateToFile failed for {path}")

    def save_state_bytes(self) -> bytes:
        return self._thunk.save_state_to_bytes()

    def initialize(self, patient_file: str) -> None:
        ok = self._thunk.initialize_engine(patient_file)
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
            raise BioGearsError(f"AdvanceModelTime failed: {self._last_errors()}")

    def process_actions(self, actions: str | Iterable[str]) -> None:
        xml = actions if isinstance(actions, str) else "\n".join(actions)
        ok = self._thunk.process_actions(xml)
        self._drain()
        if not ok:
            raise BioGearsError(f"ProcessAction failed: {self._last_errors()}")

    @property
    def sim_time_s(self) -> float:
        return self._thunk.sim_time_s

    @property
    def time_step_s(self) -> float:
        return self._thunk.time_step_s

    # -- logging -------------------------------------------------------------
    def _drain(self) -> None:
        self.log.extend(self._thunk.pull_log_messages())
        self.events.extend(self._thunk.pull_events())

    def _last_errors(self, n: int = 5) -> str:
        errs = [m.text for m in self.log if m.level in ("Error", "Fatal")]
        return " | ".join(errs[-n:]) or "(no error logged)"
