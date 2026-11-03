"""How long would the replication take on this machine?

    python benchmark.py [--minutes 15] [--parallel 3]

Runs the study's severe-infection setup (same requests, sampled every simulated
minute) for a few simulated minutes in N processes at once (one per severity in
the real study), measures the speed, and prints the expected wall time for
30-h and 48-h runs. Needs $BIOGEARS_RUNTIME. Takes about 1-5 minutes.
"""
import argparse
import multiprocessing as mp
import os
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def worker(minutes, q):
    import pybiogears as pb
    import run_study as rs
    eng = pb.BioGearsEngine(".")
    eng.set_requests([pb.DataRequest(c, p, u, compartment=cm, substance=s) for _, c, p, u, cm, s in rs.REQUESTS])
    eng.load_state("./states/StandardMale@0s.xml")
    eng.process_actions(rs.infection_action("Severe"))
    t0 = time.perf_counter()
    for _ in range(int(minutes)):
        eng.advance_steps(rs.SAMPLE_STEPS)
        eng.pull()
    q.put(minutes * 60 / (time.perf_counter() - t0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args()
    rt = os.environ.get("BIOGEARS_RUNTIME")
    if not rt:
        raise SystemExit("set BIOGEARS_RUNTIME to <prefix>/share/biogears/runtime")
    os.chdir(rt)
    print(f"{platform.platform()}, {os.cpu_count()} CPUs; {a.parallel} runs at once, {a.minutes:g} simulated min each ...",
          flush=True)
    q = mp.Queue()
    ps = [mp.Process(target=worker, args=(a.minutes, q)) for _ in range(a.parallel)]
    t0 = time.perf_counter()
    [p.start() for p in ps]
    [p.join() for p in ps]
    rtf = min(q.get() for _ in ps)  # the slowest run sets the finish time
    print(f"done in {time.perf_counter() - t0:.0f} s; speed {rtf:.1f}x real time per run "
          f"(slowest of {a.parallel})")
    for h in (30, 48):
        print(f"  {h}-h runs ({a.parallel} severities in parallel): about {h / rtf:.1f} h of wall time")
    print("  (the speed was measured over the first minutes; it may change once sepsis develops)")


if __name__ == "__main__":
    main()
