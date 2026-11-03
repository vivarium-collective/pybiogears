# pybiogears

A Python binding for the [BioGears](https://github.com/BioGearsEngine/core) physiology engine, pinned to **BioGears 8.2.0**. It is the counterpart of Pulse's `PyPulse`, and the base for `viva-biogears` (see `plan-viva-biogears.md`).

Status: **probe stage** (plan section 4). The API below may change.

## Layers

| Layer | File | Role |
|---|---|---|
| C++ thunk | `src/cpp/BioGearsThunk.{h,cpp}` | Wraps `biogears::PhysiologyEngine` with plain types: state load/save (file, bytes), data requests, advance, actions as scenario XML, log and event capture. Uses only BioGears' installed public headers. |
| pybind11 module | `src/cpp/bindings.cpp` | `pybiogears._pybiogears` |
| Python | `src/pybiogears/` | `BioGearsEngine`, `DataRequest`, `actions` (XML builders) |

How each piece maps to BioGears:

- **Actions** are BioGears' own scenario `<Action>` XML. The thunk wraps them in a minimal `<Scenario>` document and parses it with the public `SEScenario::Load`, then calls `ProcessAction` on each. (BioGears' action serializer, `io::Actions`, is `BIOGEARS_PRIVATE_API` in 8.2.0, so this is the public route.)
- **Data requests** are created through the engine's `SEDataRequestManager` and connected with `PhysiologyEngineTrack::TrackRequest`; values are read the way `PhysiologyEngineTrack::PullData` reads them, without its CSV stream. Requests the engine cannot connect read as NaN.
- **Substances** are looked up with `GetSubstance`, which never creates one.
- **Save to bytes** goes through a temporary file in the working directory, since BioGears has no save-to-string.

## Build

One command builds and installs stock BioGears 8.2.0, then pybiogears, and runs the tests:

```sh
scripts/install_biogears.sh --with-pybiogears        # defaults: ~/biogears-8.2.0-install, ~/biogears-8.2.0-build
export BIOGEARS_RUNTIME=~/biogears-8.2.0-install/share/biogears/runtime
```

Dependencies are listed at the top of the script (apt on Linux, Homebrew on macOS). BioGears itself is not modified. After installing, the script works around three 8.2.0 install gaps (see `experiments/p1/README.md`):
- It copies the XSD-generated `biogears/schema` headers, which the install omits, into the install.
- It assembles a runtime directory (xsd, substances, patients, states) at `<prefix>/share/biogears/runtime`.
- It writes `bg-scenario.sh` and `bg-cli.sh` wrappers, because the installed tools can't find their libraries on their own.

Re-running it is incremental. Changing `--prefix` recompiles everything, because BioGears builds the prefix into a header.

To build pybiogears alone against an existing install: `CMAKE_PREFIX_PATH=<prefix> pip install -e .`

## Use

```python
import pybiogears as pb

eng = pb.BioGearsEngine()                      # uses $BIOGEARS_RUNTIME
eng.set_requests([
    pb.DataRequest("Physiology", "HeartRate", "1/min"),
    pb.DataRequest("Physiology", "MeanArterialPressure", "mmHg"),
    pb.DataRequest("LiquidCompartment", "Pressure", "mmHg", compartment="Aorta"),
])
eng.load_state("./states/StandardMale@0s.xml")
eng.advance(10.0)
print(eng.pull_dict())

eng.process_actions(pb.actions.hemorrhage("Aorta", 150.0))
eng.advance(60.0)
```

Request categories: `Physiology`, `Patient`, `Environment`, `Equipment`, `Substance`, `LiquidCompartment`, `GasCompartment`, `TissueCompartment`, `ThermalCompartment`, and two circuit categories, `FluidPath` (Flow, FlowSource, PressureSource, Resistance, Compliance) and `FluidNode` (Pressure, Volume), which read elements of the active cardiovascular circuit by name (put the name in `compartment`). The circuit categories exist because some quantities, such as lymph flow, have no compartment or system request in BioGears 8.2.0.

### Time, state and actions

- `advance(seconds)` advances simulated time; `advance_steps(n)` advances exactly n engine steps (0.02 s each). Use `advance_steps` when two runs must take identical steps (e.g. comparing a reloaded run with a continuous one): `advance(seconds)` carries BioGears' floating-point step remainder between calls.
- `save_state(path)` / `save_state_bytes()` and `load_state(path)` / `load_state_bytes(blob)` round-trip a run to about 1e-12, including active hemorrhages and infusions (probe P3). Requests are re-created after every load.
- `process_actions(xml)` takes one or more BioGears scenario `<Action>` elements; `pybiogears.actions` builds the common ones. They match BioGears' own `bg-scenario` to its output precision (probe P4). Note that `bg-scenario` runs each `AdvanceTime` of T seconds as int(T/dt)+1 steps, so its actions land 0.02 s later per segment.

### Errors, events and logging

- Bad request names, units, compartments and substances are reported **unconnected** and read NaN, with the reason in `engine.log` (probe P7). `set_requests` returns the connected flags.
- Bad actions raise `BioGearsError`. Reaching BioGears' irreversible state raises `IrreversibleStateError` from `advance`.
- `engine.events` collects patient and equipment events (`event`, `active`, `sim_time_s`), including per-beat and per-breath markers. `engine.log` collects engine messages; both are bounded (`log_limit`, default 10,000). BioGears forwards its `Fatal` messages as `Error`.
- Engines are deterministic: identical inputs give bit-identical outputs across runs, processes and concurrent threads (probe P6). Requesting an inactive substance activates it and changes results at the 1e-13 level, so keep request sets fixed across runs that must match exactly.

### BioGears 8.2.0 issues worked around in pybiogears

| issue | effect without the workaround | probe |
|---|---|---|
| engine auto-tracking re-connects data requests every sample | requested units silently replaced by the engine's native units | P5 |
| invalid unit throws from BioGears | exception out of `set_requests` (now: request reported unconnected) | P5 |
| no compartment links for lymph | lymph flow unreadable by standard requests (now: `FluidPath`) | P5 |
| `LoadState` re-marshals existing requests as the base type | "Unsupported DataRequest Received" per request on every load | P3 |
| compound components appended, not replaced, on load | an infusion active across a reload delivers double solutes | P3 |
| hemorrhage on an unknown compartment accepted, then a null dereference | segfault of the Python process on the next step (now: refused with `BioGearsError`) | P7 |
| unknown compound throws from the action factory | raw `RuntimeError`, temporary file left behind (now: `BioGearsError`, file removed) | P7 |
| `LoadState` rebuilds the patient without the event handler | no events after a load (now: re-attached) | P7 |
| irreversible state: `AdvanceModelTime` returns false silently | unexplained failure (now: `IrreversibleStateError`) | P7 |
| `Logger::LogToConsole` is a no-op | engine messages on stdout (now: console level `eFatal` when off) | P7 |
| a bleed above cardiac output is re-reported every step | unbounded Python log (now: `log` and `events` are bounded deques) | P7 |

## Tests

```sh
export BIOGEARS_RUNTIME=<prefix>/share/biogears/runtime
export BIOGEARS_SCENARIO=<prefix>/bin/bg-scenario.sh   # optional: enables tests/test_oracle.py
python -m pytest
```

All tests run the real engine. Their oracles are physical bounds and mass balance, BioGears' own outputs and saved states, or a live `bg-scenario` run, never the binding itself.

## Probes

`experiments/` holds one folder per probe from the plan (P1 build, P2 minimal binding, ...), each with its script and recorded output.

## License

Apache 2.0, as BioGears.
