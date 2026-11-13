#!/usr/bin/env python3
"""Contract test for the corpus gate in tools/run_parity.py.

The gate itself needs Chromium, which is why its logic lives in a pure function:
these legs feed it synthetic rows, so the gate is tested on every push without a
browser. A gate that cannot be tested is a gate that silently stops working.

Every leg is a NEGATIVE control: the last six assert that a real defect makes
the gate exit nonzero. Without them, "the corpus is green" would only mean the
gate is asleep.
"""

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_parity  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = json.load(open(os.path.join(ROOT, "tools", "parity_baseline.json")))
FIXTURES = json.load(open(os.path.join(ROOT, "fixtures.json")))

fails = []


def check(leg, problems, want_empty, want_substr=None):
    ok = (not problems) if want_empty else bool(problems)
    if ok and want_substr:
        ok = any(want_substr in p for p in problems)
    print(f"{'ok  ' if ok else 'FAIL'} {leg}")
    if not ok:
        fails.append(f"{leg}: got {problems!r}")
    return ok


def row(fixture, colour, verdict="exact", delta=0, text=False):
    return {
        "fixture": fixture,
        "colour": colour,
        "verdict": verdict,
        "delta": delta,
        "text": text,
        "chromium": [0, 0, 10, 10],
        "blitz": [0, 0, 10, 10],
    }


# A corpus that matches the baseline exactly: every pinned divergence at its
# pinned delta, the pinned absence, the pinned box count.
def matching_corpus():
    rows = []
    for i in range(BASELINE["text_free_boxes"] - len(BASELINE["divergent"]) - len(BASELINE["absence"])):
        rows.append(row(f"fixture{i}", "red"))
    for key, delta in BASELINE["divergent"].items():
        fixture, colour = key.split(":")
        rows.append(row(fixture, colour, f"DIFF {delta}px", delta))
    for fixture in BASELINE["absence"]:
        rows.append(row(fixture, None, "ok (absence)", None))
    return rows


clean = matching_corpus()

# --- positive control: the shipped baseline describes this corpus -----------
problems, current = run_parity.gate(clean, FIXTURES, BASELINE)
check("baseline matches a corpus at the pinned deltas", problems, want_empty=True)
assert current["text_free_boxes"] == BASELINE["text_free_boxes"], current

# --- negative controls: each real defect must fail the gate -----------------
c = copy.deepcopy(clean)
c[0] = dict(c[0], verdict="DIFF 9px", delta=9)
check("a NEW divergence fails", run_parity.gate(c, FIXTURES, BASELINE)[0],
      want_empty=False, want_substr="NEW divergence")

c = copy.deepcopy(clean)
worst = max(BASELINE["divergent"], key=lambda k: BASELINE["divergent"][k])
for r in c:
    if f"{r['fixture']}:{r['colour']}" == worst:
        r["verdict"] = f"DIFF {r['delta'] + 10}px"
        r["delta"] = r["delta"] + 10
check("a divergence that GREW fails", run_parity.gate(c, FIXTURES, BASELINE)[0],
      want_empty=False, want_substr="WORSE divergence")

c = [r for r in copy.deepcopy(clean)
     if f"{r['fixture']}:{r['colour']}" not in BASELINE["divergent"]]
check("a fixed divergence fails until the baseline is updated",
      run_parity.gate(c, FIXTURES, BASELINE)[0],
      want_empty=False, want_substr="BASELINE STALE")

c = copy.deepcopy(clean)
c[1] = dict(c[1], verdict="MISSING", delta=None)
check("a MISSING box fails", run_parity.gate(c, FIXTURES, BASELINE)[0],
      want_empty=False, want_substr="MISSING box")

check("a corpus that lost boxes fails", run_parity.gate(clean[:-2], FIXTURES, BASELINE)[0],
      want_empty=False, want_substr="BOX COUNT")

c = copy.deepcopy(clean)
for r in c:
    if r["verdict"] == "ok (absence)":
        r["verdict"] = "exact"
check("an absence that started painting fails",
      run_parity.gate(c, FIXTURES, BASELINE)[0],
      want_empty=False, want_substr="ABSENCE SET")

check("a fixture dropped from fixtures.json fails",
      run_parity.gate(clean, FIXTURES[:-1], BASELINE)[0],
      want_empty=False, want_substr="FIXTURE COUNT")

# --- the baseline file itself must stay honest ------------------------------
for key in ("fixtures", "text_free_boxes", "divergent", "absence"):
    print(f"{'ok  ' if key in BASELINE else 'FAIL'} baseline declares {key!r}")
    if key not in BASELINE:
        fails.append(f"baseline missing {key!r}")

print()
if fails:
    print(f"legs run: {len(fails)} FAILED")
    for f in fails:
        print(f"  {f}")
    sys.exit(1)
print("legs run: 12 / PASS")
