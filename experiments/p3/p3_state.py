"""P3: state round trip.

    python p3_state.py CONDITION VARIANT OUTDIR
      CONDITION: rest | hemorrhage | infusion
                 (the action starts at 60 s and is still active at the save at 120 s)
      VARIANT:   continuous | file_new | bytes_same | bytes_new

All runs start from StandardMale@0s and use exact step counts (advance_steps),
so a reloaded run takes the same steps as the continuous one. The state is
saved at 120 s; the run continues to 600 s, sampled every 30 s.
"""

import csv
import sys
import time
from pathlib import Path

import pybiogears as pb

DT_STEPS_30S = 1500  # 0.02 s steps
REQ = [
    pb.DataRequest("Physiology", "HeartRate", "1/min"),
    pb.DataRequest("Physiology", "MeanArterialPressure", "mmHg"),
    pb.DataRequest("Physiology", "BloodVolume", "mL"),
    pb.DataRequest("Physiology", "ExtravascularFluidVolume", "mL"),
    pb.DataRequest("Physiology", "CentralVenousPressure", "mmHg"),
    pb.DataRequest("FluidPath", "Flow", "mL/min", "LymphToVenaCava"),
    pb.DataRequest("Physiology", "CoreTemperature", "degC"),
]
COLUMNS = ["t_s", "HR", "MAP", "BloodVolume", "ExtravascularFluidVolume", "CVP", "LymphReturn", "CoreTemperature"]
ACTIONS = {
    "rest": None,
    "hemorrhage": pb.actions.hemorrhage("Aorta", 150.0),
    "infusion": pb.actions.substance_compound_infusion("Saline", 1000.0, 100.0),
}


def new_engine():
    e = pb.BioGearsEngine(".")
    e.set_requests(REQ)
    return e


def main(condition, variant, out):
    e = new_engine()
    e.load_state("./states/StandardMale@0s.xml")
    rows = []
    e.advance_steps(2 * DT_STEPS_30S)  # 60 s
    if ACTIONS[condition]:
        e.process_actions(ACTIONS[condition])
    e.advance_steps(2 * DT_STEPS_30S)  # 120 s
    rows.append(e.pull())
    info = {}
    if variant == "file_new":
        path = out / f"{condition}_state@120s.xml"
        e.save_state(path)
        info["state_bytes"] = path.stat().st_size
        e = new_engine()
        t0 = time.perf_counter()
        e.load_state(path)
        info["load_s"] = time.perf_counter() - t0
        path.unlink()
    elif variant in ("bytes_same", "bytes_new"):
        t0 = time.perf_counter()
        blob = e.save_state_bytes()
        info["save_s"] = time.perf_counter() - t0
        info["state_bytes"] = len(blob)
        if variant == "bytes_new":
            e = new_engine()
        t0 = time.perf_counter()
        e.load_state_bytes(blob)
        info["load_s"] = time.perf_counter() - t0
    if variant != "continuous":
        rows.append(e.pull())  # right after reload, same sim time
    for _ in range(16):  # to 600 s
        e.advance_steps(DT_STEPS_30S)
        rows.append(e.pull())
    errors = [m.text for m in e.log if m.level in ("Error", "Fatal")]
    with open(out / f"{condition}_{variant}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([repr(float(x)) for x in r])
    (out / f"{condition}_{variant}.log").write_text(f"{info}\nerrors: {errors}\n")


if __name__ == "__main__":
    out = Path(sys.argv[3]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    main(sys.argv[1], sys.argv[2], out)
