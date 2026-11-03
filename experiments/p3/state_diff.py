"""Which state fields don't survive save -> load -> save?  python state_diff.py
Saves at 120 s (optionally with an action active from 60 s), loads the bytes into
a fresh engine, saves again immediately, and lists leaf values that changed.
Entries are keyed by element path, with siblings identified by Name attribute,
<Name> child or xsi:type, so reordering is not reported."""
import sys
import xml.etree.ElementTree as ET
import pybiogears as pb

XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"

def leaves(s):
    out = {}
    def ident(el):
        n = el.get("Name") or el.get("name")
        if not n:
            for c in el:
                if c.tag.split("}")[1] == "Name" and c.text:
                    n = c.text.strip(); break
        return n or el.get(XSI) or ""
    def walk(el, path):
        tag = el.tag.split("}")[1]
        p = f"{path}/{tag}" + (f"[{ident(el)}]" if ident(el) else "")
        k, i = p, 1
        while k in out or any(x.startswith(k + "/") for x in ()):
            i += 1; k = f"{p}#{i}"
        if "value" in el.attrib:
            out[k] = el.attrib["value"]
        elif el.text and el.text.strip() and len(el) == 0:
            out[k] = el.text.strip()
        else:
            out.setdefault(k, None)
        for c in el:
            walk(c, k)
    walk(ET.fromstring(s), "")
    return {k: v for k, v in out.items() if v is not None}

def run(action):
    e = pb.BioGearsEngine("."); e.load_state("./states/StandardMale@0s.xml"); e.advance_steps(3000)
    if action: e.process_actions(action)
    e.advance_steps(3000); a = e.save_state_bytes()
    f = pb.BioGearsEngine("."); f.load_state_bytes(a); b = f.save_state_bytes()
    return leaves(a.decode()), leaves(b.decode())

cases = [("rest", None), ("hemorrhage", pb.actions.hemorrhage("Aorta", 150.0)),
         ("infusion", pb.actions.substance_compound_infusion("Saline", 1000.0, 100.0))]
for label, act in cases:
    A, B = run(act)
    diff = [(k, A.get(k), B.get(k)) for k in sorted(set(A) | set(B)) if A.get(k) != B.get(k)]
    print(f"== {label}: {len(A)} values; {len(diff)} change across load+save")
    for k, x, y in diff[:40]:
        print(f"   {k[-120:]} | {x} -> {y}")
