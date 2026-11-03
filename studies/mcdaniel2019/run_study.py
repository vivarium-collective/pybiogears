"""Replication of McDaniel et al. 2019 (Front. Physiol. 10:1321), core experiments,
on BioGears 8.2.0 through pybiogears.

    python run_study.py RUN [RUN ...] [--out DIR] [--hours H] [--jobs N]
      RUN: control | mild | moderate | severe | challenge | all

Runs (see README.md for the protocol and its sources):
  control, mild, moderate, severe: StandardMale@0s; at t = 0 an Infection action
      (Skin, severity, MIC 16 mg/L as in the paper; no infection for control);
      48 h with the nutrition schedule of BioGears' own InfectionModerate scenario
      (water every hour, five meals). Paper Figs 4-7.
  challenge: from the severe run's 44 h checkpoint, 1000 mL saline as two
      500 mL boluses at 44.0 and 44.5 h, each over 30 min (the paper: "two 500 mL
      boluses at 44 and 44.5 h ... over one hour"), then observation to 46 h.
      Paper Fig 9.

Every run writes OUT/<run>/data.csv (one row per simulated minute), events.csv
and log.txt, and saves a state every 4 simulated hours (OUT/<run>/state@<h>h.xml),
from which an interrupted run resumes. Runs are independent processes; --jobs
runs that many at once (challenge waits for severe's 44 h checkpoint).
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SAMPLE_STEPS = 3000          # 60 s of 0.02 s steps
CHECKPOINT_H = float(os.environ.get("MC2019_CHECKPOINT_H", 4))  # override only for testing
CHALLENGE_H = float(os.environ.get("MC2019_CHALLENGE_H", 2))   # hours after 44 h; override only for testing
SEVERITY = {"mild": "Mild", "moderate": "Moderate", "severe": "Severe"}

# (column, category, property, unit, compartment, substance)
REQUESTS = [
    # Fig 4: acute inflammatory response (arbitrary units)
    ("BloodPathogen", "Physiology", "InflammatoryResponse-BloodPathogen", "", "", ""),
    ("LocalPathogen", "Physiology", "InflammatoryResponse-LocalPathogen", "", "", ""),
    ("TNF", "Physiology", "InflammatoryResponse-TumorNecrosisFactor", "", "", ""),
    ("IL6", "Physiology", "InflammatoryResponse-Interleukin6", "", "", ""),
    ("IL10", "Physiology", "InflammatoryResponse-Interleukin10", "", "", ""),
    ("NO", "Physiology", "InflammatoryResponse-NitricOxide", "", "", ""),
    ("TissueIntegrity", "Physiology", "InflammatoryResponse-TissueIntegrity", "", "", ""),
    # Fig 5
    ("HR", "Physiology", "HeartRate", "1/min", "", ""),
    ("RR", "Physiology", "RespirationRate", "1/min", "", ""),
    ("CoreTemperature", "Physiology", "CoreTemperature", "degC", "", ""),
    ("WBC", "Physiology", "WhiteBloodCellCount", "ct/uL", "", ""),
    # Figs 6-7
    ("BloodVolume", "Physiology", "BloodVolume", "mL", "", ""),
    ("SBP", "Physiology", "SystolicArterialPressure", "mmHg", "", ""),
    ("DBP", "Physiology", "DiastolicArterialPressure", "mmHg", "", ""),
    ("MAP", "Physiology", "MeanArterialPressure", "mmHg", "", ""),
    ("SVR", "Physiology", "SystemicVascularResistance", "mmHg s/mL", "", ""),
    ("UrineRate", "Physiology", "UrineProductionRate", "mL/min", "", ""),
    ("MeanUrineOutput", "Physiology", "MeanUrineOutput", "mL/min", "", ""),
    ("Lactate", "LiquidCompartment", "Molarity", "mmol/L", "Aorta", "Lactate"),
    ("CardiacOutput", "Physiology", "CardiacOutput", "L/min", "", ""),
    ("StrokeVolume", "Physiology", "HeartStrokeVolume", "mL", "", ""),
    # tissue fluid and lymph (the paper's endothelial-leak / lymph model)
    ("CVP", "Physiology", "CentralVenousPressure", "mmHg", "", ""),
    ("ExtravascularFluid", "Physiology", "ExtravascularFluidVolume", "mL", "", ""),
    ("TotalBodyFluid", "Physiology", "TotalBodyFluidVolume", "mL", "", ""),
    ("Albumin", "LiquidCompartment", "Concentration", "g/dL", "Aorta", "Albumin"),
    ("LymphReturn", "FluidPath", "Flow", "mL/min", "LymphToVenaCava", ""),
    ("LymphPool", "FluidNode", "Volume", "mL", "Lymph", ""),
    ("MuscleEndothelialResistance", "FluidPath", "Resistance", "mmHg min/mL", "MuscleE1ToMuscleE2", ""),
] + [(f"{t}Lymph", "FluidPath", "Flow", "mL/min", f"{t}ToLymphValve", "")
     for t in ["Bone", "Brain", "Fat", "Gut", "LeftKidney", "LeftLung", "Liver", "Muscle",
               "Myocardium", "RightKidney", "RightLung", "Skin", "Spleen"]]
COLUMNS = ["t_h"] + [r[0] for r in REQUESTS]


def nutrition_actions():
    sched = json.loads((HERE / "nutrition_schedule.json").read_text())["events"]
    out = []
    for ev in sched:
        n = ev["nutrition"]
        parts = []
        for k in ["Carbohydrate", "Fat", "Protein", "Calcium", "Sodium"]:
            if k in n:
                parts.append(f'<{k} value="{n[k]}" unit="g"/>')
        parts.append(f'<Water value="{n["Water"]}" unit="L"/>')
        out.append((ev["hour"], '<Action xsi:type="ConsumeNutrientsData"><Nutrition><Name>Meal</Name>'
                    + "".join(parts) + "</Nutrition></Action>"))
    return out


def infection_action(severity):
    return (f'<Action xsi:type="InfectionData" Severity="{severity}">'
            '<MinimumInhibitoryConcentration value="16.0" unit="mg/L"/><Location>Skin</Location></Action>')


def saline(volume_mL, rate_mL_per_min):
    import pybiogears as pb
    return pb.actions.substance_compound_infusion("Saline", volume_mL, rate_mL_per_min)


def schedule(run):
    """[(hour, [action xml, ...])] for a run, sorted."""
    items = {}
    if run in SEVERITY:
        items.setdefault(0.0, []).append(infection_action(SEVERITY[run]))
    if run in ("control", *SEVERITY):
        for h, a in nutrition_actions():
            items.setdefault(h, []).append(a)
    if run == "challenge":
        for h, a in nutrition_actions():
            if 44.0 <= h < 46.0:
                items.setdefault(h, []).append(a)
        items.setdefault(44.0, []).append(saline(500.0, 500.0 / 30.0))
        items.setdefault(44.5, []).append(saline(500.0, 500.0 / 30.0))
    return sorted(items.items())


def run_one(run, out_root, hours):
    import pybiogears as pb
    out = out_root / run
    out.mkdir(parents=True, exist_ok=True)
    start_h, end_h = (44.0, 44.0 + CHALLENGE_H) if run == "challenge" else (0.0, hours)
    eng = pb.BioGearsEngine(".")
    eng.set_requests([pb.DataRequest(c, p, u, compartment=cm, substance=s) for _, c, p, u, cm, s in REQUESTS])
    # resume from the latest checkpoint of this run, if any
    ckpts = sorted(out.glob("state@*h.xml"), key=lambda p: float(p.stem.split("@")[1][:-1]))
    if run == "challenge":
        src = out_root / "severe" / os.environ.get("MC2019_CHALLENGE_STATE", "state@44h.xml")
        if not src.exists():
            raise SystemExit("challenge needs severe/state@44h.xml; run severe first")
        eng.load_state(src)
        t_h = 44.0
        mode = "w"
    elif ckpts:
        eng.load_state(ckpts[-1])
        t_h = float(ckpts[-1].stem.split("@")[1][:-1])
        mode = "a"
    else:
        eng.load_state("./states/StandardMale@0s.xml")
        t_h = 0.0
        mode = "w"
    if eng.unconnected:
        raise SystemExit(f"unconnected requests: {[(r.category, r.compartment, r.property) for r in eng.unconnected]}")
    dt_h = eng.time_step_s / 3600
    k0 = int(round(t_h / dt_h))  # global step index
    acts = [(int(round(h / dt_h)), a) for h, a in schedule(run) if h >= t_h - 1e-9]
    if mode == "a":  # drop rows after the checkpoint (they will be recomputed)
        rows = [r for r in csv.reader(open(out / "data.csv"))]
        keep = [rows[0]] + [r for r in rows[1:] if float(r[0]) <= t_h + 1e-9]
        csv.writer(open(out / "data.csv", "w", newline="")).writerows(keep)
    f = open(out / "data.csv", mode, newline="")
    w = csv.writer(f)
    ev_f = open(out / "events.csv", mode, newline="")
    ev_w = csv.writer(ev_f)
    if mode == "w":
        w.writerow(COLUMNS)
        ev_w.writerow(["t_h", "event", "active"])
        w.writerow([f"{t_h:.6f}"] + [repr(float(x)) for x in eng.pull()[1:]])
    log = open(out / "log.txt", "a")
    log.write(f"{time.ctime()} start {run} at {t_h} h (BioGears {pb.biogears_version()})\n")
    k, k_end = k0, int(round(end_h / dt_h))
    ai = 0
    wall0 = time.perf_counter()
    try:
        while k < k_end:
            while ai < len(acts) and acts[ai][0] <= k:
                eng.process_actions(acts[ai][1])
                ai += 1
            nxt = min(k + SAMPLE_STEPS - (k % SAMPLE_STEPS), k_end, acts[ai][0] if ai < len(acts) else k_end)
            eng.advance_steps(nxt - k)
            k = nxt
            if k % SAMPLE_STEPS == 0 or k == k_end:
                w.writerow([f"{k * dt_h:.6f}"] + [repr(float(x)) for x in eng.pull()[1:]])
                for e in eng.events:
                    if e.event not in ("StartOfCardiacCycle", "StartOfInhale", "StartOfExhale"):
                        ev_w.writerow([f"{e.sim_time_s / 3600:.6f}", e.event, int(e.active)])
                eng.events.clear()
            if run != "challenge" and k % int(round(CHECKPOINT_H / dt_h)) == 0 and k != k0:
                f.flush()
                ev_f.flush()
                eng.save_state(out / f"state@{k * dt_h:g}h.xml")
                log.write(f"{time.ctime()} t = {k * dt_h:g} h, wall {time.perf_counter() - wall0:.0f} s\n")
                log.flush()
    except pb.IrreversibleStateError as ex:
        log.write(f"{time.ctime()} irreversible state: {ex}\n")
    finally:
        errs = [m.text for m in eng.log if m.level in ("Error", "Fatal")]
        log.write(f"{time.ctime()} end at {k * dt_h:.3f} h; errors: {errs[:20]}\n")
        f.close()
        ev_f.close()
        log.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--hours", type=float, default=48.0, help="length of the 0-h runs (testing: shorter)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    runtime = os.environ.get("BIOGEARS_RUNTIME")
    if not runtime:
        raise SystemExit("set BIOGEARS_RUNTIME to <prefix>/share/biogears/runtime")
    os.chdir(runtime)
    runs = ["control", "mild", "moderate", "severe", "challenge"] if a.runs == ["all"] else a.runs
    if a.child:
        run_one(runs[0], out, a.hours)
        return
    first = [r for r in runs if r != "challenge"]
    procs = []
    cmd = [sys.executable, str(Path(__file__).resolve()), "--child", "--out", str(out), "--hours", str(a.hours)]
    pending = list(first)
    while pending or procs:
        while pending and len(procs) < a.jobs:
            r = pending.pop(0)
            procs.append((r, subprocess.Popen(cmd + [r])))
            print(f"started {r}")
        time.sleep(5)
        for r, p in list(procs):
            if p.poll() is not None:
                procs.remove((r, p))
                print(f"finished {r} (exit {p.returncode})")
    if "challenge" in runs:
        subprocess.run(cmd + ["challenge"], check=True)
        print("finished challenge")


if __name__ == "__main__":
    main()
