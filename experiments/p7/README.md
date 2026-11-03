# P7: data-request coverage and bad inputs

**Date:** 2026-11-02. BioGears `8.2.0+0-c5227b6`, Linux x86_64.

**Result: coverage is broad (99.6 % of BioGears' own request specs connect).** Bad request names and units fail safely and visibly. Bad actions exposed one crash and three handling gaps in BioGears; all four are now guarded in pybiogears, with regression tests.

## 1. Coverage (`p7_requests.py coverage`; `out/coverage.txt`, `out/coverage.json`)

The input is every distinct data request in BioGears' own `Scenarios/` and `datarequests/` files: 4,576 after deduplicating by category, compartment, substance and name. They were all set on one engine from StandardMale@0s and read after 10 s.

| category | total | connected | finite at 10 s |
|---|---|---|---|
| LiquidCompartment | 3310 | 3310 | 3255 |
| Substance | 524 | 522 | 518 |
| GasCompartment | 328 | 324 | 316 |
| Physiology | 228 | 226 | 225 |
| TissueCompartment | 118 | 117 | 117 |
| ThermalCompartment | 32 | 24 | 19 |
| Patient | 27 | 27 | 27 |
| Environment | 8 | 8 | 8 |
| Equipment | 1 | 1 | 1 |

- **Not connected: 17.** All are stale or unsupported names in BioGears' own files:
  - thermal compartments `ExternalSkin` and `InternalSkin`;
  - gas compartments `Carina`, `LeftDeadSpace` and `RightDeadSpace`;
  - `LiverTissueIntracellular` as a tissue compartment;
  - enum-valued `HeartRhythm` and `UrineColor`, which aren't scalars;
  - kidney `Sodium` `MassCleared` and `MassExcreted` ("Unhandled data request").
- **Connected but NaN or inf at 10 s: about 80.**
  - InFlow and OutFlow of every tissue extracellular and intracellular compartment, `Lymph`, `Pericardium` and `SmallIntestineChyme`. These compartments have no links, the same cause as lymph flow in P5. Read flows through `FluidPath` instead.
  - `PlasmaConcentration` of Sodium, Potassium, Chloride and Calcium: NaN.
  - Thermal compartment `Heat`: NaN.
  - `CreatinineProductionRate`: NaN.
  - `GasSource` volumes: inf (the environment is an infinite reservoir).
  - `VentilatorConnection`: NaN, because no ventilator is connected.

## 2. Bad data requests (`p7_requests.py bad`; `out/bad_inputs.txt`)

All of these are reported **unconnected** and read **NaN**; none raises, and the message is in `engine.log`:
- an unknown property, an empty property, or a valid property in the wrong category (e.g. `Patient/HeartRate`) — "Unknown Data Request";
- an incompatible or nonexistent unit — "pybiogears: … is not a valid Frequency unit";
- an unknown liquid or tissue compartment;
- an unknown compartment property;
- an unknown substance — looked up with `GetSubstance`, which doesn't create one;
- an unknown circuit path, or a bad circuit property;
- equipment that isn't connected.

An unknown *category* raises `ValueError`, because it is a programming error, not a data question.

**Requesting an inactive substance changes the simulation slightly.** BioGears activates any substance named in a compartment or substance request (`AddActiveSubstance`). After 60 s, requesting Morphine (inactive at start) makes HR, MAP, blood volume, pH and core temperature differ from a run without the request by up to 1.3e-13. The values are no longer bit-identical, but the change is at rounding level. A CarbonMonoxide request and a request for an already-active substance leave the run bit-identical. In practice: requests are not perfectly passive, so **keep the request set fixed across runs that must be compared bit for bit.**

## 3. Bad actions

| input | before the fix | now |
|---|---|---|
| malformed XML, unknown action type, empty string | `BioGearsError` (XML parse error logged); engine unaffected | same |
| unknown substance compound | a raw `RuntimeError` from the BioGears factory, and the temporary scenario file left in the runtime folder | `BioGearsError` "actions could not be read: … Unknown substance"; the temporary file is always removed |
| **hemorrhage on an unknown compartment** | accepted ("Could not find compartment, defaulting to Aorta"), then a **segmentation fault on the next step** that kills the Python process. `Cardiovascular::Hemorrhage` dereferences the missing `<cmpt>Bleed` path. `bg-scenario` has the same code path. | refused before it reaches the engine: `BioGearsError` "hemorrhage refused, compartment has no bleed path" |
| hemorrhage with a negative rate | accepted; BioGears treats it as stopping the hemorrhage | same (documented) |
| `AdvanceTime` inside `process_actions` | `BioGearsError` "Unsupported Action" | same; use `advance`/`advance_steps` |

## 4. Fatal errors during `advance`

- **A bleed rate above cardiac output** is a BioGears `Fatal`. BioGears skips the bleed, re-reports it on every 0.02 s step (250 messages in 5 s), and `advance` succeeds; blood volume does not change. pybiogears keeps this behaviour but bounds its log: `BioGearsEngine.log` and `.events` are now `deque(maxlen=log_limit)`, 10,000 by default. Callers should check `engine.log` after setting high rates.
- **BioGears' logger forwards `Fatal` as `Error`** and drops `Exception`-level messages (`Logger.cpp`), so the forwarder can't tell a Fatal from an Error.
- **The irreversible state** (BioGears declares it at 50 % blood loss: 1500 mL/min from the aorta reaches it at 186.8 s) makes `AdvanceModelTime` return false without logging an error. pybiogears now raises `IrreversibleStateError` with the time, and every later `advance` raises it too. `engine.irreversible_state` is exposed.

## 5. Events and console logging

- **Events were lost after every load.** BioGears attaches the event handler to the patient only in `SetEventHandler` and at the end of `InitializeEngine`, and `LoadState` rebuilds the patient. The thunk now re-attaches it after every load. In the exsanguination run, pybiogears now receives Antidiuresis (21.3 s), Tachycardia (45.3 s), Tachypnea, RespiratoryAlkalosis, HypovolemicShock (95.4 s), IntracranialHypotension and IrreversibleState (186.8 s), matching the engine log. It also receives `StartOfCardiacCycle`, `StartOfInhale` and `StartOfExhale` markers, about 5 per second.
- **`Logger::LogToConsole` is a no-op in 8.2.0**: it stores a flag nothing reads. `set_log_to_console(False)` now also sets the console level to `eFatal`, the lowest, so only Fatal messages, and a load-time warning that predates the setting, still reach stdout. Every message still reaches `engine.log`. This closes the P2 "stray console lines" item as far as the public API allows.

## Regression tests (mutation-checked)

- `test_unknown_hemorrhage_compartment_is_refused`; the mutant without the guard fails.
- `test_unknown_compound_raises_cleanly`, which also checks that no temporary file is left.
- `test_events_arrive_after_load` and `test_irreversible_state_raises`; the mutant without the event re-attach fails both.
