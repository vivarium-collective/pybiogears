# P3: state round trip

**Date:** 2026-11-02. BioGears `8.2.0+0-c5227b6`, Linux x86_64.

**Result: pass, after two fixes in pybiogears.** Saving a state mid-run and reloading it reproduces the uninterrupted run to about 1e-12 at rest and with an active hemorrhage, and to about 2e-9 with an active infusion. That holds through a file or through bytes, into the same engine or a new one. Before the fixes, an active infusion diverged by up to 26 % (lymph return) and 1 % (MAP).

## Method

`p3_state.py CONDITION VARIANT`:
- Load StandardMale@0s and run 60 s; under `hemorrhage` or `infusion`, start that action (Aorta 150 mL/min, or 1 L saline at 100 mL/min). Run to 120 s, save, then continue to 600 s, sampling every 30 s.
- Variants:
  - `continuous`, the reference;
  - `file_new`: save to a file, load into a new engine;
  - `bytes_same`: `save_state_bytes`, load back into the same engine;
  - `bytes_new`: bytes, loaded into a new engine.
- All runs use `advance_steps` (exact step counts; new in this probe). `advance(seconds)` carries BioGears' floating-point step remainder between calls, so a freshly loaded engine and a long-running one could split the same interval into different step counts.
- Readings: HR, MAP, blood volume, extravascular fluid volume, CVP, lymph return and core temperature.
- `state_diff.py` saves at 120 s, loads into a fresh engine, saves again at once, and lists every state value that changed. Siblings are matched by name, so reordering is not reported. This shows what the state format does not carry.

Results: `analyze_p3.py` writes `out/p3_summary.txt`; the state diff is in `out/state_diff*.txt`.

## Results (after the fixes; max relative difference from the continuous run over 120–600 s)

| condition at the save | file_new | bytes_same | bytes_new | at the moment of reload |
|---|---|---|---|---|
| rest | 2e-12 | 2e-12 | 2e-12 | 1e-15 |
| hemorrhage active | 1e-12 | 1e-12 | 1e-12 | 2e-15 |
| infusion active | 2e-9 | 2e-9 | 2e-9 | 1e-15 |

- Active actions survive a reload. At 600 s the hemorrhage run is at 4490 mL of blood both ways (rest: 5549 mL), and the infusion run at 6409 mL both ways. The saved state carries the remaining bag volume and rate.
- Cost, for a 1.9 MB XML state: save to bytes 0.14 s, load 0.17–0.21 s.

## Bugs found, and what pybiogears does about them (BioGears unmodified)

1. **An active compound's components are duplicated on load.** `io::Substance::UnMarshall(SubstanceCompoundData)` appends the state's components to those already loaded from `substances/*.xml` without clearing them. After a reload, Saline lists Sodium, Chloride and its other components twice, so an infusion delivers double the solutes. The reloaded run then gained blood volume progressively (+5.9 mL by 600 s), and MAP and lymph return drifted with it.
   - Fix: after every load, the thunk removes duplicate components by substance name (`dedupe_compound_components`); both copies hold the same concentration.
   - Regression test: `test_active_infusion_survives_reload`. A mutant without the fix fails it.
2. **"Unsupported DataRequest Received", once per request, on every load while requests exist.** `BioGearsEngine::LoadState` copies the engine's existing data requests across the reload, but marshals them as the base `CDM::DataRequestData`, which its own factory rejects, and adds a null request to the manager. Nothing in the saved file causes it.
   - Fix: the thunk drops its requests before a load and re-creates them afterwards, as it already did (`drop_requests`).
   - Regression test: `test_reload_with_requests_logs_no_errors`. A mutant without the fix fails it.
3. **Not fixed, noted.** The sleep model's `AttentionLapses` and `BiologicalDebt` (nervous system) are not restored on load. They come back as 0, or as uninitialised memory (`5.5e-315`). This matters only for sleep-deprivation studies.
4. **Benign.** About 24 derived compartment values (parent-compartment volumes, lung gas partial pressures and the like) are not written in a save made right after a load. They are caches that BioGears recomputes on the next step, and the run comparisons above show no effect.

## Remaining difference

With an infusion active, a reloaded run still differs by about 2e-9 (HR, MAP, lymph return), against 1e-12 at rest. It is small enough not to matter. It is probably one more value the state format doesn't carry exactly; the state diff shows nothing infusion-specific left.

## Consequence for CLI comparisons

`bg-scenario` has bug 1 too. A scenario that *starts* from a state saved during an infusion runs with doubled solutes in the CLI, but not in pybiogears. In that one case the two legitimately differ, and the CLI is not a valid oracle.
