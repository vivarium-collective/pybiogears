"""P5 follow-up: is the resting drift an artifact of the shipped 8.1.0+74 state?

Initialise StandardMale from patients/StandardMale.xml with BioGears 8.2.0's own
stabilisation, save the state, then run the same 2-h rest protocol from it.
    python p5_restate.py OUTDIR
"""
import sys
import time
from pathlib import Path

import pybiogears as pb

sys.path.insert(0, str(Path(__file__).parent))
import p5_lymph as p5  # noqa: E402

out = Path(sys.argv[1]).resolve()
eng = pb.BioGearsEngine(".")
t0 = time.perf_counter()
eng.initialize("StandardMale.xml")
init_wall = time.perf_counter() - t0
state = out / "StandardMale@0s_8.2.0.xml"
eng.save_state(state)
errors = [m.text for m in eng.log if m.level in ("Error", "Fatal")]
(out / "restate.log").write_text(f"initialize: {init_wall:.0f} s wall\nerrors: {errors}\n")

# Same rest protocol and requests as p5_lymph.py, from the regenerated state.
eng = pb.BioGearsEngine(".")
eng.set_requests([pb.DataRequest(c, p, u, compartment=cm) for c, p, u, cm in p5.STANDARD + p5.CIRCUIT])
eng.load_state(str(state))
rows = [eng.pull()]
for _ in range(int(7200 / p5.SAMPLE_S)):
    eng.advance(p5.SAMPLE_S)
    rows.append(eng.pull())
import csv  # noqa: E402
with open(out / "rest82_binding.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(p5.COLUMNS)
    for r in rows:
        w.writerow([f"{x:.10g}" for x in r])
