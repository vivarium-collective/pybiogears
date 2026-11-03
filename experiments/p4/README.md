# P4: actions as scenario XML, checked against bg-scenario

**Date:** 2026-11-02. BioGears `8.2.0+0-c5227b6`, Linux x86_64.

**Result: pass.** Actions sent through pybiogears give the same physiology as BioGears' own `bg-scenario`, to 1e-13–1e-16 relative (the 12 digits the CLI writes), over 15–21 simulated minutes. That covers starting, changing and stopping a hemorrhage, changing an infusion's rate, a blood transfusion, and two actions in one call. It holds once the binding replays the CLI's step-count convention.

## The CLI's step-count convention

`SEScenarioExec` runs each `AdvanceTime` of T seconds as

```cpp
int count = (int)(time_s / dT_s);
for (int i = 0; i <= count; i++) m_Engine.AdvanceModelTime();
```

That is `int(T/dt) + 1` steps, one 0.02 s step more than T. So in a CLI scenario every action lands 0.02 s later per preceding `AdvanceTime`: its logs show actions at 60.02, 180.04 and 300.06 s. pybiogears' `advance(T)` advances T itself.

To use `bg-scenario` as an oracle, replay its steps with `advance_steps(int(T/dt) + 1)` and sample at the CLI's sample times; `p4_actions.py` and `tests/test_oracle.py` do this. Without the extra step, the oracle test fails by 1.3 % (mutation check). P5's earlier hemorrhage comparison (1 % in HR) was exactly this offset.

## Protocols (`p4_actions.py`; from StandardMale@0s)

| protocol | actions |
|---|---|
| hemorrhage | 60 s; RightLeg bleed 100 mL/min; 120 s; rate → 250 mL/min; 120 s; stop; 600 s |
| infusion | 60 s; Saline 500 mL at 100 mL/min; 120 s; same compound, rate → 200 mL/min; 300 s; Blood_ONegative 500 mL at 50 mL/min; 720 s |
| resuscitation | 60 s; Aorta bleed 200 mL/min; 300 s; stop **and** Saline 1 L at 200 mL/min, in one `process_actions` call; 900 s |

The readings, all standard requests so the CLI records them too, are HR, MAP, blood volume, CVP, cardiac output, haemoglobin content and vena cava volume. They are sampled at 1 Hz and compared at equal simulation times (largest time offset 9e-10 s).

## Results (max relative difference, binding vs bg-scenario)

| | HR | MAP | blood volume | CVP | cardiac output | haemoglobin | vena cava volume |
|---|---|---|---|---|---|---|---|
| hemorrhage (900 rows) | 7e-15 | 5e-15 | 2e-16 | 1e-13 | 9e-14 | 6e-16 | 5e-16 |
| infusion (1200 rows) | 8e-15 | 5e-15 | 2e-16 | 1e-13 | 9e-14 | 5e-16 | 4e-16 |
| resuscitation (1260 rows) | 7e-15 | 6e-15 | 2e-16 | 2e-13 | 9e-14 | 6e-16 | 6e-16 |

No errors were logged. The actions clearly act, so the agreement is not trivial:

| | | blood volume | haemoglobin | MAP | HR |
|---|---|---|---|---|---|
| hemorrhage | before → after the bleed (299 s) | 5533 → 5238 mL | 846 → 800 g | 95.9 → 92.5 | 72 → 77 |
| infusion | 1200 s | 6673 mL | 920 g (the transfusion adds haemoglobin; saline does not) | 111.7 | 61 |
| resuscitation | end of the bleed (359 s) → 1260 s | 4722 → 5726 mL | 719 g | 84.1 → 98.1 | 93 → 69 |

## Notes

- **Hemorrhage compartments are not liquid compartments.** `Hemorrhage` takes names such as `RightLeg`, which map to the circuit path `RightLegBleed`; there is no `RightLeg` liquid compartment. A compartment request for it is reported unconnected by pybiogears. The CLI writes `-1.$` in that column without stopping.
- A hemorrhage rate above cardiac output is a `Fatal` in BioGears, which logs and continues (`Cardiovascular.cpp`). It was not exercised here; P7 should check how the binding surfaces it.
- The regression test `tests/test_oracle.py` covers bleed, stop and saline in one call, against a live `bg-scenario` run. It runs when `$BIOGEARS_SCENARIO` points at `<prefix>/bin/bg-scenario.sh`, and is skipped otherwise.
