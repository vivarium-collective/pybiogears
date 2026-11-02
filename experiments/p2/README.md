# P2: minimal binding

**Date:** 2026-11-02. BioGears `8.2.0+0-c5227b6`, Linux x86_64 (P1 build), Python 3.13.

## Method

- `p2_minimal_binding.py`: create an engine, load `states/StandardMale@0s.xml`, request HeartRate (1/min), MeanArterialPressure (mmHg), BloodVolume (mL), advance 10 s, read.
- Oracle: BioGears' own `bg-scenario` running `oracle_p2.xml` (same state, same requests, `AdvanceTime 10 s`, CSV at 50 Hz, precision 10). Compared at the CSV row with t = 10.0 s.

```sh
cd $BIOGEARS_RUNTIME
cp oracle_p2.xml Scenarios/ && LD_LIBRARY_PATH=$PREFIX/lib $PREFIX/bin/bg-scenario -q Scenarios/oracle_p2.xml
python p2_minimal_binding.py Scenarios/oracle_p2Results.csv
```

## Result: pass

| | HR (1/min) | MAP (mmHg) | Blood volume (mL) |
|---|---|---|---|
| binding, t = 10 s | 72.301524 | 95.935661 | 5532.434516 |
| bg-scenario, t = 10 s | 72.30152366 | 95.93566093 | 5532.43451611 |
| binding − oracle | 2.6e-12 | 3.7e-11 | 4.6e-11 |

Max relative difference 3.8e-13, i.e. the CSV's 10-digit rounding: the binding and the CLI run the same computation. Full output: `out/p2_output.txt`.

- Time step 0.02 s. Load 0.25 s; 10 s simulated in 1.66 s wall (6x real time, one core, Release). P6 will measure this properly.
- No errors logged.

## Smoke checks beyond P2 (not the P3/P4 probes, which still have to be run)

From `tests/test_smoke.py` (5 pass) and a one-off run:
- Hemorrhage as scenario XML (`pb.actions.hemorrhage("Aorta", 300)`), 60 s: blood volume −281.7 mL, HR 72 → 79, MAP 96 → 90. Within the mass-balance bound (≤ 300 mL).
- An unknown property reads NaN and is reported unconnected; an unknown substance is reported unconnected and not created.
- State to bytes and back at t = 5 s, then 5 s more: differs from the uninterrupted run by ~1e-10 (HR) to ~1e-12 (MAP, BV).

## Issues found (for P3 and the thunk)

1. Reloading a state saved while data requests were active logs `Unsupported DataRequest Received` once per request: BioGears writes the engine's requests into the state but can't read them back. The thunk re-creates its requests after every load, so values are unaffected, but the thunk should clear the request manager before saving (P3).
2. Some engine log lines still reach stdout although the thunk calls `LogToConsole(false)` and installs a forwarder. To track down (likely the substance-manager loader logging before the forward is set, or a second logger).
3. bg-scenario's `--results` path is joined onto the scenario directory, so absolute paths break; the oracle CSV lands next to the scenario as `<name>Results.csv`.
