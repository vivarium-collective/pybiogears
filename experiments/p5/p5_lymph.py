"""P5: lymph flow in BioGears 8.2.0.

Runs one protocol from StandardMale@0s through pybiogears and writes a CSV, and
writes the same protocol as a BioGears scenario for the bg-scenario oracle.

    python p5_lymph.py rest|hemorrhage|infusion OUTDIR

Readings
- Lymph return flow: circuit path LymphToVenaCava (FluidPath request).
  BioGears 8.2.0 creates no compartment links for lymph, so no standard
  request reads lymph flow; the compartment's InFlow/OutFlow read NaN.
- Lymph formation: the 13 one-way valve paths <Tissue>ToLymphValve into the
  Lymph node (FluidPath requests), summed.
- Lymph pool: Lymph liquid compartment volume (standard request, also
  available to bg-scenario) and the Lymph circuit node volume.
- Fluid volumes and vitals: standard physiology requests.

Oracles (independent of the circuit reader)
- Mass balance on the Lymph node: dV/dt = sum(valves) - LymphToVenaCava.
- bg-scenario run of the same protocol (standard requests only): lymph
  compartment volume, blood and extravascular volumes, MAP, HR.
"""

import csv
import sys
import time
from pathlib import Path

import numpy as np

import pybiogears as pb

TISSUES = ["Bone", "Brain", "Fat", "Gut", "LeftKidney", "LeftLung", "Liver", "Muscle",
           "Myocardium", "RightKidney", "RightLung", "Skin", "Spleen"]
SAMPLE_S = 30.0

# Each step: (action XML applied first, or None; then seconds to advance).
PROTOCOLS = {
    "rest": [(None, 7200)],
    "hemorrhage": [  # 5 min rest, 6 min aortic bleed at 150 mL/min, 54 min observation
        (None, 300),
        (pb.actions.hemorrhage("Aorta", 150.0), 360),
        (pb.actions.hemorrhage("Aorta", 0.0), 3240),
    ],
    "infusion": [  # 5 min rest, 1 L saline at 100 mL/min (ends at 15 min), 50 min observation
        (None, 300),
        (pb.actions.substance_compound_infusion("Saline", 1000.0, 100.0), 3600),
    ],
}

STANDARD = [  # also requested from bg-scenario
    ("LiquidCompartment", "Volume", "mL", "Lymph"),
    ("Physiology", "BloodVolume", "mL", ""),
    ("Physiology", "ExtravascularFluidVolume", "mL", ""),
    ("Physiology", "MeanArterialPressure", "mmHg", ""),
    ("Physiology", "HeartRate", "1/min", ""),
]
CIRCUIT = [("FluidPath", "Flow", "mL/min", "LymphToVenaCava"),
           ("FluidNode", "Volume", "mL", "Lymph"),
           ("FluidNode", "Pressure", "mmHg", "Lymph")] + \
          [("FluidPath", "Flow", "mL/min", f"{t}ToLymphValve") for t in TISSUES]

COLUMNS = (["t_s", "LymphVolume_cmpt_mL", "BloodVolume_mL", "ExtravascularFluidVolume_mL",
            "MAP_mmHg", "HR_per_min", "LymphReturn_mL_per_min", "LymphVolume_node_mL",
            "LymphPressure_mmHg"] + [f"{t}Lymph_mL_per_min" for t in TISSUES])


def run_binding(protocol: str, out: Path) -> None:
    eng = pb.BioGearsEngine(".")
    reqs = [pb.DataRequest(c, p, u, compartment=cm) for c, p, u, cm in STANDARD + CIRCUIT]
    eng.set_requests(reqs)
    eng.load_state("./states/StandardMale@0s.xml")
    bad = eng.unconnected
    if bad:
        raise SystemExit(f"unconnected requests: {[(r.category, r.compartment, r.property) for r in bad]}")
    rows = [eng.pull()]
    t0 = time.perf_counter()
    for action, seconds in PROTOCOLS[protocol]:
        if action is not None:
            eng.process_actions(action)
        n = int(round(seconds / SAMPLE_S))
        for _ in range(n):
            eng.advance(SAMPLE_S)
            rows.append(eng.pull())
    wall = time.perf_counter() - t0
    with open(out / f"{protocol}_binding.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([f"{x:.10g}" for x in r])
    errors = [m.text for m in eng.log if m.level in ("Error", "Fatal")]
    sim = rows[-1][0]
    (out / f"{protocol}_binding.log").write_text(
        f"sim {sim:.0f} s in {wall:.1f} s wall (RTF {sim / wall:.1f}x)\nerrors: {errors}\n")


def write_oracle_scenario(protocol: str, out: Path) -> Path:
    reqs = []
    for c, p, u, cm in STANDARD:
        if c == "LiquidCompartment":
            reqs.append(f'<DataRequest xsi:type="LiquidCompartmentDataRequestData" Compartment="{cm}" Name="{p}" Unit="{u}" Precision="10"/>')
        else:
            reqs.append(f'<DataRequest xsi:type="PhysiologyDataRequestData" Name="{p}" Unit="{u}" Precision="10"/>')
    acts = []
    for action, seconds in PROTOCOLS[protocol]:
        if action is not None:
            acts.append(action)
        acts.append(f'<Action xsi:type="AdvanceTimeData"><Time value="{seconds}" unit="s"/></Action>')
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Scenario xmlns="uri:/mil/tatrc/physiology/datamodel" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" contentVersion="BioGears_8.2.0">
  <Name>p5_{protocol}</Name>
  <Description>pybiogears probe P5 oracle: {protocol}</Description>
  <EngineStateFile>./states/StandardMale@0s.xml</EngineStateFile>
  <DataRequests SamplesPerSecond="1">
    {chr(10).join('    ' + r for r in reqs).strip()}
  </DataRequests>
  <Actions>
    {chr(10).join('    ' + a for a in acts).strip()}
  </Actions>
</Scenario>
"""
    path = out / f"p5_{protocol}.xml"
    path.write_text(xml)
    return path


if __name__ == "__main__":
    protocol, outdir = sys.argv[1], Path(sys.argv[2]).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    write_oracle_scenario(protocol, outdir)
    if len(sys.argv) < 4 or sys.argv[3] != "--scenario-only":
        run_binding(protocol, outdir)
