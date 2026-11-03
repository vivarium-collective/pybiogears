"""P4: actions as scenario XML, checked against BioGears' own bg-scenario.

    python p4_actions.py PROTOCOL OUTDIR scenario   # write OUTDIR/p4_<PROTOCOL>.xml
    python p4_actions.py PROTOCOL OUTDIR binding ORACLE_CSV

The binding replays the protocol the way bg-scenario's SEScenarioExec executes
it: each AdvanceTime of T seconds runs int(T / dt) + 1 engine steps (its loop is
`for (i = 0; i <= count; i++)`). Values are pulled at the oracle's sample times.
Actions in one list entry are sent in one process_actions call.
"""

import csv
import sys
from pathlib import Path

import numpy as np

import pybiogears as pb
from pybiogears import actions as A

PROTOCOLS = {
    # start, change rate, stop a limb bleed
    "hemorrhage": [
        60, [A.hemorrhage("RightLeg", 100.0)],
        120, [A.hemorrhage("RightLeg", 250.0)],
        120, [A.hemorrhage("RightLeg", 0.0)],
        600,
    ],
    # infusion, rate change of the same compound, then a second compound (blood)
    "infusion": [
        60, [A.substance_compound_infusion("Saline", 500.0, 100.0)],
        120, [A.substance_compound_infusion("Saline", 500.0, 200.0)],
        300, [A.substance_compound_infusion("Blood_ONegative", 500.0, 50.0)],
        720,
    ],
    # aortic bleed, then stop + resuscitate in one call (two actions)
    "resuscitation": [
        60, [A.hemorrhage("Aorta", 200.0)],
        300, [A.hemorrhage("Aorta", 0.0), A.substance_compound_infusion("Saline", 1000.0, 200.0)],
        900,
    ],
}

REQUESTS = [  # (category, property, unit, compartment); all standard, so bg-scenario has them too
    ("Physiology", "HeartRate", "1/min", ""),
    ("Physiology", "MeanArterialPressure", "mmHg", ""),
    ("Physiology", "BloodVolume", "mL", ""),
    ("Physiology", "CentralVenousPressure", "mmHg", ""),
    ("Physiology", "CardiacOutput", "L/min", ""),
    ("Physiology", "HemoglobinContent", "g", ""),
    ("LiquidCompartment", "Volume", "mL", "VenaCava"),
]
NAMES = ["HR", "MAP", "BloodVolume", "CVP", "CardiacOutput", "HemoglobinContent", "VenaCavaVolume"]


def scenario_xml(protocol):
    reqs = []
    for c, p, u, cm in REQUESTS:
        if c == "LiquidCompartment":
            reqs.append(f'<DataRequest xsi:type="LiquidCompartmentDataRequestData" Compartment="{cm}" Name="{p}" Unit="{u}" Precision="12"/>')
        else:
            reqs.append(f'<DataRequest xsi:type="PhysiologyDataRequestData" Name="{p}" Unit="{u}" Precision="12"/>')
    acts = []
    for item in PROTOCOLS[protocol]:
        if isinstance(item, list):
            acts.extend(item)
        else:
            acts.append(f'<Action xsi:type="AdvanceTimeData"><Time value="{item}" unit="s"/></Action>')
    nl = "\n    "
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Scenario xmlns="uri:/mil/tatrc/physiology/datamodel" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" contentVersion="BioGears_8.2.0">
  <Name>p4_{protocol}</Name>
  <Description>pybiogears probe P4 oracle: {protocol}</Description>
  <EngineStateFile>./states/StandardMale@0s.xml</EngineStateFile>
  <DataRequests SamplesPerSecond="1">
    {nl.join(reqs)}
  </DataRequests>
  <Actions>
    {nl.join(acts)}
  </Actions>
</Scenario>
"""


def run_binding(protocol, oracle_csv, out):
    oracle = np.genfromtxt(oracle_csv, delimiter=",", skip_header=1)
    eng = pb.BioGearsEngine(".")
    eng.set_requests([pb.DataRequest(c, p, u, compartment=cm) for c, p, u, cm in REQUESTS])
    eng.load_state("./states/StandardMale@0s.xml")
    if eng.unconnected:
        raise SystemExit(f"unconnected: {eng.unconnected}")
    dt = eng.time_step_s
    targets = sorted({int(round(t / dt)) for t in oracle[:, 0]})  # oracle sample times as step indices
    k = 0  # steps taken
    rows = []
    ti = 0
    for item in PROTOCOLS[protocol]:
        if isinstance(item, list):
            eng.process_actions(item)
            continue
        end = k + int(item / dt) + 1  # SEScenarioExec: int(T/dt)+1 steps
        while ti < len(targets) and targets[ti] <= end:
            if targets[ti] > k:
                eng.advance_steps(targets[ti] - k)
                k = targets[ti]
            if targets[ti] == k:
                rows.append(eng.pull())
            ti += 1
        if end > k:
            eng.advance_steps(end - k)
            k = end
    rows = np.array(rows)
    with open(out / f"{protocol}_binding.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s"] + NAMES)
        for r in rows:
            w.writerow([repr(float(x)) for x in r])
    # compare at equal sim times
    idx = [int(np.argmin(np.abs(oracle[:, 0] - t))) for t in rows[:, 0]]
    o = oracle[idx]
    lines = [f"rows compared: {len(rows)}; max |t_binding - t_oracle| = {np.max(np.abs(rows[:, 0] - o[:, 0])):.2e} s"]
    for j, name in enumerate(NAMES, start=1):
        rel = np.abs(rows[:, j] - o[:, j]) / np.maximum(np.abs(o[:, j]), 1e-12)
        lines.append(f"{name:18s} max rel diff {rel.max():.2e} (at t = {rows[rel.argmax(), 0]:.2f} s)")
    errors = [m.text for m in eng.log if m.level in ("Error", "Fatal")]
    lines.append(f"errors logged: {errors}")
    (out / f"{protocol}_compare.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    protocol, out, mode = sys.argv[1], Path(sys.argv[2]).resolve(), sys.argv[3]
    out.mkdir(parents=True, exist_ok=True)
    if mode == "scenario":
        (out / f"p4_{protocol}.xml").write_text(scenario_xml(protocol))
    else:
        run_binding(protocol, sys.argv[4], out)
