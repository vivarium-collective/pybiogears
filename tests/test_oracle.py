"""Actions checked against BioGears' own bg-scenario (probe P4).

Needs $BIOGEARS_RUNTIME and $BIOGEARS_SCENARIO (the bg-scenario.sh wrapper that
scripts/install_biogears.sh writes); skipped otherwise.

bg-scenario's SEScenarioExec runs each AdvanceTime of T seconds as int(T/dt)+1
steps, so the binding replays it with advance_steps to compare like for like.
"""
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

import pybiogears as pb
from pybiogears import actions as A

pytestmark = pytest.mark.skipif(
    "BIOGEARS_RUNTIME" not in os.environ or "BIOGEARS_SCENARIO" not in os.environ,
    reason="needs $BIOGEARS_RUNTIME and $BIOGEARS_SCENARIO")

PROTOCOL = [30, [A.hemorrhage("Aorta", 200.0)],
            30, [A.hemorrhage("Aorta", 0.0), A.substance_compound_infusion("Saline", 500.0, 200.0)],
            30]
REQUESTS = [("HeartRate", "1/min"), ("MeanArterialPressure", "mmHg"), ("BloodVolume", "mL"),
            ("CentralVenousPressure", "mmHg")]


def test_actions_match_bg_scenario(tmp_path):
    rt = Path(os.environ["BIOGEARS_RUNTIME"])
    os.chdir(rt)
    name = f"pybiogears_oracle_{os.getpid()}"
    reqs = "\n".join(f'<DataRequest xsi:type="PhysiologyDataRequestData" Name="{n}" Unit="{u}" Precision="12"/>'
                     for n, u in REQUESTS)
    acts = "\n".join(("\n".join(x) if isinstance(x, list) else
                      f'<Action xsi:type="AdvanceTimeData"><Time value="{x}" unit="s"/></Action>') for x in PROTOCOL)
    scenario = rt / "Scenarios" / f"{name}.xml"
    scenario.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<Scenario xmlns="uri:/mil/tatrc/physiology/datamodel" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<Name>{name}</Name><EngineStateFile>./states/StandardMale@0s.xml</EngineStateFile>
<DataRequests SamplesPerSecond="1">{reqs}</DataRequests><Actions>{acts}</Actions></Scenario>""")
    results = rt / "Scenarios" / f"{name}Results.csv"
    try:
        subprocess.run([os.environ["BIOGEARS_SCENARIO"], "-q", f"Scenarios/{name}.xml"], cwd=rt,
                       check=True, capture_output=True, timeout=600)
        oracle = np.genfromtxt(results, delimiter=",", skip_header=1)[:, :1 + len(REQUESTS)]
    finally:
        for f in (scenario, results, rt / "Scenarios" / f"{name}.log", rt / "Scenarios" / f"{name}Results.log"):
            f.unlink(missing_ok=True)

    eng = pb.BioGearsEngine(rt)
    eng.set_requests([pb.DataRequest("Physiology", n, u) for n, u in REQUESTS])
    eng.load_state("./states/StandardMale@0s.xml")
    dt = eng.time_step_s
    targets = [int(round(t / dt)) for t in oracle[:, 0]]
    k, ti, rows = 0, 0, []
    for item in PROTOCOL:
        if isinstance(item, list):
            eng.process_actions(item)
            continue
        end = k + int(item / dt) + 1
        while ti < len(targets) and targets[ti] <= end:
            eng.advance_steps(targets[ti] - k)
            k = targets[ti]
            rows.append(eng.pull())
            ti += 1
        eng.advance_steps(end - k)
        k = end
    rows = np.array(rows)
    assert len(rows) == len(oracle)
    assert rows[-1, 3] < oracle[0, 3]  # the bleed removed blood
    np.testing.assert_allclose(rows[:, 1:], oracle[:, 1:], rtol=1e-10)
