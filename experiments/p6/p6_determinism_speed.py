"""P6: determinism and speed.

    python p6_determinism_speed.py run OUT.npy     # one reference run, saved
    python p6_determinism_speed.py main OUTDIR     # everything else

Reference run: StandardMale@0s, 60 s rest, Aorta hemorrhage 150 mL/min with
saline 500 mL at 100 mL/min, 240 s more; 30 requests pulled every 50 steps (1 s).
Determinism is checked bit for bit (np.array_equal) between
  - two runs in one process, one after the other;
  - runs in separate processes;
  - two engines run at the same time in two threads (the GIL is released
    during advance), against the sequential runs.
Speed: wall time per step at rest and during the actions, pull and load costs,
and throughput with 1 vs 2 concurrent engines.
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

import pybiogears as pb
from pybiogears import actions as A

REQ = ([pb.DataRequest("Physiology", n, u) for n, u in [
    ("HeartRate", "1/min"), ("MeanArterialPressure", "mmHg"), ("SystolicArterialPressure", "mmHg"),
    ("DiastolicArterialPressure", "mmHg"), ("CardiacOutput", "L/min"), ("BloodVolume", "mL"),
    ("CentralVenousPressure", "mmHg"), ("RespirationRate", "1/min"), ("TidalVolume", "mL"),
    ("OxygenSaturation", ""), ("CoreTemperature", "degC"), ("ExtravascularFluidVolume", "mL"),
    ("HemoglobinContent", "g"), ("ArterialBloodPH", ""), ("UrineProductionRate", "mL/min")]]
    + [pb.DataRequest("LiquidCompartment", p, u, c) for c, p, u in [
        ("Aorta", "Pressure", "mmHg"), ("VenaCava", "Volume", "mL"), ("Lymph", "Volume", "mL"),
        ("LeftHeart", "Volume", "mL"), ("BrainVasculature", "InFlow", "mL/min")]]
    + [pb.DataRequest("LiquidCompartment", "Concentration", "g/L", "Aorta", s)
       for s in ["Sodium", "Glucose", "Albumin", "Lactate"]]
    + [pb.DataRequest("FluidPath", "Flow", "mL/min", p) for p in
       ["LymphToVenaCava", "MuscleToLymphValve", "AortaBleed"]]
    + [pb.DataRequest("FluidNode", "Pressure", "mmHg", n) for n in ["Lymph", "VenaCava", "Aorta1"]])
STEPS_PER_SAMPLE = 50


def reference_run(record_step_times=False):
    e = pb.BioGearsEngine(".")
    e.set_requests(REQ)
    e.load_state("./states/StandardMale@0s.xml")
    rows, step_times = [], []
    for k in range(300):
        if k == 60:
            e.process_actions([A.hemorrhage("Aorta", 150.0),
                               A.substance_compound_infusion("Saline", 500.0, 100.0)])
        t0 = time.perf_counter()
        e.advance_steps(STEPS_PER_SAMPLE)
        step_times.append((time.perf_counter() - t0) / STEPS_PER_SAMPLE)
        rows.append(e.pull())
    return np.array(rows), np.array(step_times), e


def main(out):
    report = {}
    here = Path(__file__).resolve()
    # 1. sequential, same process
    a, ta, eng = reference_run()
    b, tb, _ = reference_run()
    report["connected"] = f"{sum(eng._connected)}/{len(REQ)}"
    report["same_process_bit_identical"] = bool(np.array_equal(a, b))
    # 2. separate processes
    for i in range(2):
        subprocess.run([sys.executable, str(here), "run", str(out / f"proc{i}.npy")], check=True,
                       capture_output=True)
    p0, p1 = np.load(out / "proc0.npy"), np.load(out / "proc1.npy")
    report["separate_processes_bit_identical"] = bool(np.array_equal(p0, p1) and np.array_equal(p0, a))
    # 3. two engines at once in two threads
    res = [None, None]

    def worker(i):
        res[i] = reference_run()

    t0 = time.perf_counter()
    th = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    [t.start() for t in th]
    [t.join() for t in th]
    wall2 = time.perf_counter() - t0
    report["threads_bit_identical_to_sequential"] = bool(np.array_equal(res[0][0], a) and np.array_equal(res[1][0], a))
    # 4. speed
    sim_s = 300 * STEPS_PER_SAMPLE * eng.time_step_s
    rest, act = np.concatenate([ta[:60], tb[:60]]), np.concatenate([ta[60:], tb[60:]])
    report["step_ms_rest_median"] = 1e3 * float(np.median(rest))
    report["step_ms_actions_median"] = 1e3 * float(np.median(act))
    report["step_ms_p99"] = 1e3 * float(np.percentile(np.concatenate([ta, tb]), 99))
    report["real_time_factor_one_engine"] = float(sim_s / (ta.sum() * STEPS_PER_SAMPLE))  # ta: per-step times
    report["real_time_factor_two_threads_each"] = float(sim_s / wall2)
    report["throughput_two_threads_vs_one"] = float(2 * sim_s / wall2 / (sim_s / (ta.sum() * STEPS_PER_SAMPLE)))
    t0 = time.perf_counter()
    for _ in range(2000):
        eng.pull()
    report["pull_us_30_requests"] = 1e6 * (time.perf_counter() - t0) / 2000
    t0 = time.perf_counter()
    e2 = pb.BioGearsEngine(".")
    report["create_engine_s"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    e2.load_state("./states/StandardMale@0s.xml")
    report["load_state_file_s"] = time.perf_counter() - t0
    blob = eng.save_state_bytes()
    t0 = time.perf_counter()
    e2.load_state_bytes(blob)
    report["load_state_bytes_s"] = time.perf_counter() - t0
    report["errors"] = [m.text for m in eng.log if m.level in ("Error", "Fatal")]
    np.save(out / "reference.npy", a)
    (out / "p6_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if sys.argv[1] == "run":
        np.save(sys.argv[2], reference_run()[0])
    else:
        out = Path(sys.argv[2]).resolve()
        out.mkdir(parents=True, exist_ok=True)
        main(out)
