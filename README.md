# pybiogears

A Python binding for the [BioGears](https://github.com/BioGearsEngine/core) physiology engine, pinned to **BioGears 8.2.0**. It is the engine layer under `viva-biogears`. (BioGears' own source has an experimental, off-by-default SWIG Python option, `Biogears_SWIG_PYTHON_BINDINGS`, that wraps only its `Logger`.)

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
- `process_actions(xml)` takes one or more BioGears scenario `<Action>` elements; `pb.action(...)` builds any of them (below). They match BioGears' own `bg-scenario` to its output precision (probe P4). Note that `bg-scenario` runs each `AdvanceTime` of T seconds as int(T/dt)+1 steps, so its actions land 0.02 s later per segment.

### Actions and conditions from BioGears' schema

BioGears' data model is its XML schema, so pybiogears reads `<runtime>/xsd` and builds checked XML for every action (60) and condition (13) BioGears 8.2.0 defines:

```python
pb.schema.actions()          # ['AcuteRespiratoryDistress', ..., 'Hemorrhage', 'Infection', ...]
pb.schema.describe("Infection")
# [..., Severity: Eliminated | Mild | Moderate | Severe, MinimumInhibitoryConcentration: ScalarMassPerVolumeData, (value, unit), Location: xs:string]
eng.process_actions(pb.action("Infection", Location="Skin", Severity="Severe",
                              MinimumInhibitoryConcentration=(16, "mg/L")))
eng.process_actions(pb.action("ConsumeNutrients", Nutrition={"Water": (0.3, "L"), "Carbohydrate": (40, "g")}))
eng.initialize("StandardMale.xml", [pb.condition("ChronicAnemia", ReductionFactor=0.3)])
```

A scalar is a number or `(value, unit)` (the unit is required where the schema requires it), an enumeration one of its values, a nested structure a dict, a repeated field a list. Unknown or missing fields, bad enumeration values, missing units and two alternatives of a `choice` raise `SchemaError` before BioGears sees the action. Field names are the schema's (`InitialRate`); snake_case works too.

### Engine settings, scenario files, the patient

- `initialize(patient, conditions, configuration=...)` and `load_state(path, configuration=...)` override engine settings, e.g. `pb.configuration(TimeStep=(0.01, "s"))` or a section such as `RenalConfiguration={"EnableRenal": "Off"}`. BioGears reads a state's configuration from the state, so for `load_state` pybiogears merges the override into it. For `initialize` it writes a complete configuration (the defaults from `states/StandardMale@0s.xml` plus the override), because BioGears 8.2.0's configuration loader drops every setting of a file that leaves a section out, and segfaults on a left-out On/Off field. BioGears is validated at its default 0.02 s step.
- `run_scenario(scenario.xml, results.csv)` runs a BioGears scenario file with BioGears' own executor (as `bg-scenario` does): stock behaviour, without pybiogears' workarounds, useful as an oracle. The engine then holds the scenario's final state.
- `patient()` returns the current patient (`Name`, `Sex`, `Age`: `(44.0, "yr")`, ...); `active_events` the events active now; `has_state` whether a state is loaded; `clear()` starts over with a new engine.
- An engine pickles (its working directory, requests and state) and continues the run after unpickling.

### Several engines at once

`pb.EnginePool(n, requests)` runs `n` engines on a thread pool: `load_state`, `initialize` and `advance` run them in parallel, `process_actions({index: xml})` addresses one, `pull()` returns a row per engine, `map(fn)` runs any function on all. pybiogears releases Python's GIL inside the engine, so the engines use separate cores (2 engines on 2 cores: 2× faster), and they give bit-identical results to the same engines run one after another (`tests/test_features.py`). BioGears parses XML through one shared serializer whose buffer reader takes no lock, so pybiogears serializes `load_state_bytes` across threads.

### Errors, events and logging

- Bad request names, units, compartments and substances are reported **unconnected** and read NaN, with the reason in `engine.log` (probe P7). `set_requests` returns the connected flags.
- Bad actions raise `BioGearsError`. Reaching BioGears' irreversible state raises `IrreversibleStateError` from `advance`.
- `engine.events` collects patient and equipment events (`event`, `active`, `sim_time_s`), including per-beat and per-breath markers. `set_event_handler(fn)` and `set_log_listener(fn)` call a function for each event or message, in order, when the call that produced it returns, not during the step. `engine.log` collects engine messages; both are bounded (`log_limit`, default 10,000). BioGears forwards its `Fatal` messages as `Error`.
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
| the configuration loader `Invalidate()`s everything for each section a file omits, and reads absent On/Off fields | a configuration override ignored, or a segfault (now: a complete configuration is written) | `test_features.py` |
| `Serializer::ReadBuffer` takes no lock on the shared serializer | possible races loading states from bytes in threads (now: a lock) | `test_features.py` |

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
