"""Compare P3 reloaded runs with the continuous run: python analyze_p3.py OUTDIR"""
import sys
from pathlib import Path

import numpy as np

OUT = Path(sys.argv[1])
lines = []
for cond in ["rest", "hemorrhage", "infusion"]:
    f = OUT / f"{cond}_continuous.csv"
    if not f.exists():
        continue
    h = f.open().readline().strip().split(",")
    ref = np.genfromtxt(f, delimiter=",", skip_header=1)
    lines.append(f"\n{cond} (action, if any, active from 60 s; state saved at 120 s; compared 120-600 s)")
    for var in ["file_new", "bytes_same", "bytes_new"]:
        g = OUT / f"{cond}_{var}.csv"
        if not g.exists():
            lines.append(f"  {var}: missing")
            continue
        d = np.genfromtxt(g, delimiter=",", skip_header=1)
        d = np.vstack([d[:1], d[2:]])  # drop the extra row pulled right after the reload
        # the row right after reload, compared with the row before saving
        d_reload = np.genfromtxt(g, delimiter=",", skip_header=1)[1]
        assert d.shape == ref.shape, (d.shape, ref.shape)
        dtime = np.max(np.abs(d[:, 0] - ref[:, 0]))
        rel = np.abs(d[:, 1:] - ref[:, 1:]) / np.maximum(np.abs(ref[:, 1:]), 1e-12)
        at_reload = np.abs(d_reload[1:] - ref[0, 1:]) / np.maximum(np.abs(ref[0, 1:]), 1e-12)
        worst = int(np.argmax(rel.max(axis=0)))
        lines.append(f"  {var:10s} max rel diff {rel.max():.1e} ({h[1 + worst]}); "
                     f"at reload {at_reload.max():.1e}; max |dt| {dtime:.1e} s")
        lines.append("  " + " " * 11 + "per variable: " + ", ".join(f"{n} {x:.0e}" for n, x in zip(h[1:], rel.max(axis=0))))
    if cond != "rest":
        rest = np.genfromtxt(OUT / "rest_continuous.csv", delimiter=",", skip_header=1)
        bv = h.index("BloodVolume")
        lines.append(f"  action still acting after reload? blood volume at 600 s: {cond} {ref[-1, bv]:.0f} mL "
                     f"(file_new {np.genfromtxt(OUT / f'{cond}_file_new.csv', delimiter=',', skip_header=1)[-1, bv]:.0f}) "
                     f"vs rest {rest[-1, bv]:.0f} mL")
text = "\n".join(lines)
print(text)
(OUT / "p3_summary.txt").write_text(text.strip() + "\n")
