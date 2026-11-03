# P6: determinism and speed

**Date:** 2026-11-02. BioGears `8.2.0+0-c5227b6`, pybiogears Release build. Machine: 2-core x86_64 cloud VM. Speeds on the Mac will differ; only determinism carries over directly.

**Result: pass.** Identical inputs give bit-identical outputs: one run after another in the same process, across separate processes, and with two engines running at once in two threads. One engine runs at about 6× real time on one core, and two engines in parallel threads scale almost linearly.

## Method (`p6_determinism_speed.py`)

- The reference run: StandardMale@0s, 60 s rest, then an Aorta hemorrhage at 150 mL/min plus saline 500 mL at 100 mL/min, then 240 s more.
- 30 requests (physiology, compartment, substance concentrations, circuit paths and nodes), pulled every 50 steps (1 s). All 30 connect.
- Determinism is checked with `np.array_equal` on the full 300 × 31 output: two sequential runs in one process; two runs in separate subprocesses, also compared with the first; two engines started together in two Python threads (pybiogears releases the GIL during `advance`), compared with the sequential run.
- Timing uses `time.perf_counter` around each 50-step `advance_steps`.

## Results (`out/p6_report.json`)

| check | result |
|---|---|
| same process, sequential | bit-identical |
| separate processes | bit-identical (and identical to the in-process run) |
| two engines concurrently in two threads | bit-identical to the sequential run |
| step time, median at rest / during actions | 3.11 ms / 3.20 ms |
| step time, 99th percentile | 4.06 ms |
| real-time factor, one engine | 6.2× (0.02 s steps) |
| two engines in threads | 5.9× each; 1.9× the throughput of one, on 2 cores |
| `pull()` with 30 requests | 7 µs |
| create an engine | 0.04 s |
| load the StandardMale state, file / bytes | 0.23 s / 0.18 s |

The engine has no hidden global state that makes concurrent engines interfere, so `viva-biogears` can run several engines in one process. Each step costs about 3 ms. That makes a 1-hour simulation about 10 minutes of wall time per engine on this machine.

## Correction

The first run of the script computed the one-engine real-time factor and the thread-throughput ratio from per-step times without multiplying by the 50 steps per sample (it reported 312× and 0.038×). The script is fixed, and the two values in `p6_report.json` were corrected after the run; the `note` field there says so. The step-time medians, which were right all along, give the same answer (0.02 s / 3.11 ms = 6.4×).
