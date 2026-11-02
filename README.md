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

1. Build and install BioGears 8.2.0 (C++20; needs Xerces-C, CodeSynthesis XSD, Eigen 3; Boost optional, for `bg-cli`):

   ```sh
   git clone --branch 8.2.0 https://github.com/BioGearsEngine/core.git biogears-8.2.0
   cmake -S biogears-8.2.0 -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
         -DCMAKE_INSTALL_PREFIX=$HOME/biogears-8.2.0-install \
         -DBiogears_BUILD_HOWTOS=OFF -DBiogears_BUILD_TEST=OFF
   cmake --build build && cmake --install build
   ```

2. Build this package against it:

   ```sh
   CMAKE_PREFIX_PATH=$HOME/biogears-8.2.0-install pip install -e .
   ```

3. Point it at a BioGears runtime directory (holds `xsd/`, `substances/`, `patients/`, `states/`):

   ```sh
   export BIOGEARS_RUNTIME=/path/to/biogears/runtime
   ```

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

Request categories: `Physiology`, `Patient`, `Environment`, `Equipment`, `Substance`, `LiquidCompartment`, `GasCompartment`, `TissueCompartment`, `ThermalCompartment`.

## Probes

`experiments/` holds one folder per probe from the plan (P1 build, P2 minimal binding, ...), each with its script and recorded output.

## License

Apache 2.0, as BioGears.
