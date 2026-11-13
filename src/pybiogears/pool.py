"""Several BioGears engines advanced in parallel (Pulse's ``PulseEnginePool``).

The engine releases Python's GIL while it loads, initializes, processes actions and
advances, and each :class:`BioGearsEngine` owns its own BioGears instance, so threads
run engines on separate cores. ``tests/test_features.py`` checks that engines run in
parallel give the same numbers as the same engines run one after another.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from .engine import BioGearsEngine


class EnginePool:
    """``n`` engines (or one per state file) sharing a thread pool.

    >>> pool = EnginePool(4, requests=REQ)
    >>> pool.load_state(["a.xml", "b.xml", "c.xml", "d.xml"])
    >>> pool.process_actions({0: hemorrhage_xml})     # engine 0 only
    >>> pool.advance(60.0)                             # all four at once
    >>> pool.pull()                                    # one row per engine
    """

    def __init__(self, n: int, requests: Sequence = (), working_dir: str | os.PathLike | None = None,
                 max_workers: int | None = None):
        if n < 1:
            raise ValueError("an EnginePool needs at least one engine")
        self.engines = [BioGearsEngine(working_dir) for _ in range(n)]
        for e in self.engines:
            e.set_requests(list(requests))
        self._executor = ThreadPoolExecutor(max_workers=max_workers or min(n, os.cpu_count() or 1))

    def __len__(self):
        return len(self.engines)

    def __getitem__(self, i: int) -> BioGearsEngine:
        return self.engines[i]

    def close(self) -> None:
        self._executor.shutdown(wait=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def map(self, fn: Callable[[BioGearsEngine, Any], Any], args: Iterable[Any] | None = None) -> list:
        """``fn(engine, arg)`` for every engine in parallel (``arg`` None if ``args`` is None);
        the results in engine order. An exception in any engine is raised."""
        args = [None] * len(self.engines) if args is None else list(args)
        if len(args) != len(self.engines):
            raise ValueError(f"{len(args)} arguments for {len(self.engines)} engines")
        return list(self._executor.map(fn, self.engines, args))

    def load_state(self, paths: str | os.PathLike | Sequence[str | os.PathLike]) -> None:
        """Load one state into every engine, or one state per engine."""
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths] * len(self.engines)
        self.map(lambda e, p: e.load_state(p), paths)

    def initialize(self, patient_files: str | Sequence[str], conditions: Sequence = ()) -> None:
        """Stabilize a patient in every engine (or one patient per engine), in parallel."""
        if isinstance(patient_files, str):
            patient_files = [patient_files] * len(self.engines)
        self.map(lambda e, p: e.initialize(p, list(conditions)), patient_files)

    def process_actions(self, actions: dict[int, str | Iterable[str]] | str | Iterable[str]) -> None:
        """Actions for every engine, or ``{engine index: actions}``."""
        if not isinstance(actions, dict):
            actions = {i: actions for i in range(len(self.engines))}
        for i, a in actions.items():
            self.engines[i].process_actions(a)

    def advance(self, seconds: float) -> None:
        """Advance every engine by ``seconds``, in parallel."""
        self.map(lambda e, _: e.advance(seconds))

    def pull(self) -> np.ndarray:
        """One row per engine: [sim_time_s, v0, v1, ...]."""
        return np.vstack([e.pull() for e in self.engines])
