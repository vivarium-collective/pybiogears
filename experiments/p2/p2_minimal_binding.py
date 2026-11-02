"""P2: minimal binding probe.

Create an engine, load StandardMale@0s, request heart rate, MAP and blood volume,
advance 10 s, and print the values. The oracle is BioGears' own bg-scenario run
of oracle_p2.xml (same state, same requests); its CSV row at t = 10 s is compared.

Usage: BIOGEARS_RUNTIME=/path/to/runtime python p2_minimal_binding.py [oracle.csv]
"""

import csv
import sys
import time

import numpy as np

import pybiogears as pb

REQUESTS = [
    pb.DataRequest("Physiology", "HeartRate", "1/min"),
    pb.DataRequest("Physiology", "MeanArterialPressure", "mmHg"),
    pb.DataRequest("Physiology", "BloodVolume", "mL"),
]

print("BioGears", pb.biogears_version())
eng = pb.BioGearsEngine(log_file="p2.log")
eng.set_requests(REQUESTS)
t0 = time.perf_counter()
eng.load_state("./states/StandardMale@0s.xml")
t_load = time.perf_counter() - t0
print("connected:", [not (r in eng.unconnected) for r in REQUESTS], eng.headings)
print("dt =", eng.time_step_s, "s; t0 values:", eng.pull())

t0 = time.perf_counter()
eng.advance(10.0)
t_adv = time.perf_counter() - t0
v = eng.pull()
print(f"t = {v[0]:.3f} s  HR = {v[1]:.6f}/min  MAP = {v[2]:.6f} mmHg  BV = {v[3]:.6f} mL")
print(f"load {t_load:.2f} s; advance 10 s sim in {t_adv:.2f} s wall (RTF {10 / t_adv:.1f}x)")
errs = [m for m in eng.log if m.level in ("Error", "Fatal")]
print("errors logged:", len(errs), errs[:3])

if len(sys.argv) > 1:
    with open(sys.argv[1]) as f:
        rows = list(csv.reader(f))
    header, data = rows[0], np.array([[float(x) for x in r] for r in rows[1:] if r])
    print("oracle header:", header)
    i = int(np.argmin(np.abs(data[:, 0] - v[0])))
    o = data[i]
    print(f"oracle row t = {o[0]}: {o[1:]}")
    diff = v[1:] - o[1:4]
    print("binding - oracle:", diff)
    print("max |rel diff|:", np.max(np.abs(diff) / np.abs(o[1:4])))
