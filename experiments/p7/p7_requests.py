"""P7: data-request coverage, bad names and bad actions.

    python p7_requests.py coverage REQUESTS.json OUTDIR
    python p7_requests.py bad OUTDIR

coverage: every distinct data request in BioGears' own Scenarios/ and
  datarequests/ files (deduplicated by category, compartment, substance and
  name; first unit seen), all set on one engine from StandardMale@0s; records
  whether each connects and whether it reads finite after 10 s.
bad: bad request names and units, the side effect of requesting an inactive
  substance, bad action XML, a hemorrhage above cardiac output, and the
  irreversible state.
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import pybiogears as pb
from pybiogears import actions as A

CATEGORY = {
    "LiquidCompartmentDataRequestData": "LiquidCompartment",
    "GasCompartmentDataRequestData": "GasCompartment",
    "TissueCompartmentDataRequestData": "TissueCompartment",
    "ThermalCompartmentDataRequestData": "ThermalCompartment",
    "PhysiologyDataRequestData": "Physiology",
    "PatientDataRequestData": "Patient",
    "SubstanceDataRequestData": "Substance",
    "EnvironmentDataRequestData": "Environment",
    "EquipmentDataRequestData": "Equipment",
}
STATE = "./states/StandardMale@0s.xml"


def errors(e):
    return [m.text for m in e.log if m.level in ("Error", "Fatal")]


def coverage(req_file, out):
    seen = {}
    for t, cm, sub, name, unit in json.load(open(req_file)):
        key = (CATEGORY[t], cm, sub, name)
        seen.setdefault(key, unit)
    keys = sorted(seen)
    reqs = [pb.DataRequest(c, n, seen[(c, cm, s, n)], cm, s) for c, cm, s, n in keys]
    e = pb.BioGearsEngine(".")
    e.set_requests(reqs)
    e.load_state(STATE)
    conn = list(e._connected)
    e.advance(10)
    v = e.pull()[1:]
    rows = []
    for (c, cm, s, n), ok, x in zip(keys, conn, v):
        rows.append({"category": c, "compartment": cm, "substance": s, "name": n, "unit": seen[(c, cm, s, n)],
                     "connected": ok, "finite": bool(np.isfinite(x)), "value": float(x)})
    json.dump(rows, open(out / "coverage.json", "w"), indent=0)
    summary = defaultdict(Counter)
    for r in rows:
        summary[r["category"]]["total"] += 1
        summary[r["category"]]["connected"] += r["connected"]
        summary[r["category"]]["finite"] += r["finite"]
    lines = [f"{len(rows)} distinct requests from BioGears' own scenario and data-request files",
             f"{'category':20s} {'total':>6s} {'connected':>10s} {'finite at 10 s':>15s}"]
    for c in sorted(summary):
        s = summary[c]
        lines.append(f"{c:20s} {s['total']:6d} {s['connected']:10d} {s['finite']:15d}")
    lines.append("\nNot connected:")
    for r in rows:
        if not r["connected"]:
            lines.append(f"  {r['category']}: {r['compartment']}/{r['substance']}/{r['name']} ({r['unit']})")
    lines.append("\nConnected but not finite at 10 s:")
    for r in rows:
        if r["connected"] and not r["finite"]:
            lines.append(f"  {r['category']}: {r['compartment']}/{r['substance']}/{r['name']} ({r['unit']}) = {r['value']}")
    err = Counter(m for m in errors(e))
    lines.append(f"\nDistinct errors logged ({len(err)}):")
    for m, k in err.most_common(40):
        lines.append(f"  {k:4d} x {m[:160]}")
    (out / "coverage.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:14]))


def bad(out):
    lines = []
    say = lambda s="": (print(s), lines.append(s))

    # 1. bad requests --------------------------------------------------------
    say("1. Bad data requests (one engine; connected flag, value after 2 s, what was logged)")
    cases = [
        ("unknown physiology property", pb.DataRequest("Physiology", "NoSuchThing", "")),
        ("empty property", pb.DataRequest("Physiology", "", "")),
        ("property in wrong category", pb.DataRequest("Patient", "HeartRate", "1/min")),
        ("incompatible unit", pb.DataRequest("Physiology", "HeartRate", "mL")),
        ("nonexistent unit", pb.DataRequest("Physiology", "HeartRate", "beats")),
        ("unknown liquid compartment", pb.DataRequest("LiquidCompartment", "Volume", "mL", "Nowhere")),
        ("unknown compartment property", pb.DataRequest("LiquidCompartment", "Colour", "", "Aorta")),
        ("unknown substance", pb.DataRequest("LiquidCompartment", "Concentration", "g/L", "Aorta", "Unobtainium")),
        ("unknown tissue compartment", pb.DataRequest("TissueCompartment", "Volume", "mL", "Nowhere")),
        ("unknown circuit path", pb.DataRequest("FluidPath", "Flow", "mL/min", "Nowhere")),
        ("circuit path, bad property", pb.DataRequest("FluidPath", "Colour", "", "LymphToVenaCava")),
        ("equipment (none connected)", pb.DataRequest("Equipment", "Inhaler", "")),
        ("valid control", pb.DataRequest("Physiology", "HeartRate", "1/min")),
    ]
    e = pb.BioGearsEngine(".")
    e.load_state(STATE)
    for label, r in cases:
        n0 = len(e.log)
        try:
            ok = e.set_requests([r])[0]
            e.advance(2)
            v = e.pull()[1]
            msg = "; ".join(m.text[:90] for m in list(e.log)[n0:] if m.level in ("Error", "Fatal", "Warning"))[:200]
            say(f"   {label:30s} connected={ok!s:5s} value={v!r:22s} {msg}")
        except Exception as ex:  # noqa: BLE001
            say(f"   {label:30s} raised {type(ex).__name__}: {ex}")
    try:
        e.set_requests([pb.DataRequest("NoSuchCategory", "X", "")])
        say("   unknown category               accepted (!)")
    except Exception as ex:  # noqa: BLE001
        say(f"   unknown category               raised {type(ex).__name__}: {ex}")

    # 2. side effect of requesting an inactive substance ----------------------
    say("\n2. Does requesting an inactive substance change the simulation?")
    base = [pb.DataRequest("Physiology", n, u) for n, u in
            [("HeartRate", "1/min"), ("MeanArterialPressure", "mmHg"), ("BloodVolume", "mL"),
             ("ArterialBloodPH", ""), ("CoreTemperature", "degC")]]

    def run(extra):
        x = pb.BioGearsEngine(".")
        x.set_requests(base + extra)
        x.load_state(STATE)
        x.advance_steps(3000)
        return x.pull()[1:1 + len(base)], x
    ref, _ = run([])
    for label, extra in [("Aorta Morphine concentration", [pb.DataRequest("LiquidCompartment", "Concentration", "ug/mL", "Aorta", "Morphine")]),
                         ("Morphine plasma concentration", [pb.DataRequest("Substance", "PlasmaConcentration", "ug/mL", "", "Morphine")]),
                         ("Aorta CarbonMonoxide partial pressure", [pb.DataRequest("LiquidCompartment", "PartialPressure", "mmHg", "Aorta", "CarbonMonoxide")]),
                         ("Aorta Glucose concentration (already active)", [pb.DataRequest("LiquidCompartment", "Concentration", "g/L", "Aorta", "Glucose")])]:
        v, x = run(extra)
        rel = np.max(np.abs(v - ref) / np.abs(ref))
        say(f"   {label:45s} bit-identical={bool(np.array_equal(v, ref))!s:5s} max rel diff after 60 s {rel:.1e}; "
            f"connected={x._connected[-1]}")

    # 3. bad actions -----------------------------------------------------------
    say("\n3. Bad actions (process_actions): raised?, then what was logged")
    acts = [
        ("malformed XML", '<Action xsi:type="HemorrhageData" Compartment="Aorta"><InitialRate value="10" unit="mL/min"/>'),
        ("unknown action type", '<Action xsi:type="TeleportData"/>'),
        ("unknown compound", A.substance_compound_infusion("Unobtainium", 500, 100)),
        ("hemorrhage, unknown compartment", A.hemorrhage("Nowhere", 100)),
        ("hemorrhage, negative rate", A.hemorrhage("Aorta", -50)),
        ("AdvanceTime inside process_actions", '<Action xsi:type="AdvanceTimeData"><Time value="10" unit="s"/></Action>'),
        ("empty string", ""),
    ]
    for label, xml in acts:
        x = pb.BioGearsEngine(".")
        x.set_requests(base)
        x.load_state(STATE)
        n0 = len(x.log)
        try:
            x.process_actions(xml)
            res = "accepted"
        except Exception as ex:  # noqa: BLE001
            res = f"raised {type(ex).__name__}"
        try:
            x.advance(2)
            after = f"advance ok, HR {x.pull()[1]:.1f}"
        except Exception as ex:  # noqa: BLE001
            after = f"advance raised {type(ex).__name__}"
        msg = "; ".join(m.text.replace("\n", " ")[:80] for m in list(x.log)[n0:] if m.level in ("Error", "Fatal", "Warning"))[:260]
        say(f"   {label:36s} {res:28s} {after:22s} | {msg}")

    # 4. Fatal during advance: bleed above cardiac output --------------------
    say("\n4. Hemorrhage above cardiac output (BioGears logs Fatal and returns from the bleed calculation)")
    x = pb.BioGearsEngine(".")
    x.set_requests(base)
    x.load_state(STATE)
    x.process_actions(A.hemorrhage("Aorta", 20000.0))
    n0 = len(x.log)
    try:
        x.advance(5)
        say(f"   advance(5) returned normally; BV {x.pull()[3]:.1f} mL; logged: "
            + "; ".join(f"{m.level}: {m.text[:90]}" for m in list(x.log)[n0:] if m.level in ("Error", "Fatal"))[:300])
    except Exception as ex:  # noqa: BLE001
        say(f"   advance raised {type(ex).__name__}: {str(ex)[:200]}")

    # 5. Irreversible state ---------------------------------------------------
    say("\n5. Exsanguination to the irreversible state")
    x = pb.BioGearsEngine(".")
    x.set_requests(base)
    x.load_state(STATE)
    x.process_actions(A.hemorrhage("Aorta", 1500.0))
    t_fail = None
    for k in range(120):
        try:
            x.advance(15)
        except Exception as ex:  # noqa: BLE001
            t_fail = x.sim_time_s
            say(f"   advance raised {type(ex).__name__} at t = {t_fail:.1f} s: {str(ex)[:200]}")
            break
    if t_fail is None:
        say(f"   no failure within {x.sim_time_s:.0f} s; BV {x.pull()[3]:.0f} mL")
    ev = [(ev.event, ev.active, round(ev.sim_time_s, 2)) for ev in x.events]
    say(f"   events: {ev[:12]}")
    say(f"   last values: HR {x.pull()[1]:.1f}, MAP {x.pull()[2]:.1f}, BV {x.pull()[3]:.0f}")
    try:
        x.advance(1)
        say("   a further advance succeeded (!)")
    except Exception as ex:  # noqa: BLE001
        say(f"   a further advance raised {type(ex).__name__} again")
    (out / "bad_inputs.txt").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    if sys.argv[1] == "coverage":
        out = Path(sys.argv[3]).resolve()
        out.mkdir(parents=True, exist_ok=True)
        coverage(sys.argv[2], out)
    else:
        out = Path(sys.argv[2]).resolve()
        out.mkdir(parents=True, exist_ok=True)
        bad(out)
