#!/usr/bin/env python3
"""Regression test for the default `--clip` strategy cutting wide elements (#17).

The default strategy paints the document into a shared canvas and crops each
frame out of it. That canvas used to be the VIEWPORT's width, so an element
wider than the viewport was written half-rendered: a 1600 px element in an
800 px viewport produced an 800x100 PNG, at exit 0, with nothing on stderr. The
canvas is now widened to the widest frame, which costs memory proportional to
that frame and nothing else.

    python3 tools/test_clip_wide.py --bin target/release/pageplate

Exit 0 = the contract holds. Exit 1 = it does not (the message says which part).

TWO legs exist purely so this test cannot pass on a blank canvas. Widening the
surface and cropping nothing would satisfy a width assertion while painting
white, so `b-overflow-painted` requires the GREEN half of the element — the half
that lives entirely past the viewport's right edge — to carry ink, and
`c-left-painted` requires the red half to still be there. On the pre-fix binary
`a` fails on the width while `b`/`c` pass on the truncated frame, which is what
makes the pair discriminating rather than decorative.

Run it against the released 0.3.0-beta.9 binary and `a` fails: that binary still
cuts. A test that cannot observe the defect it was written for is decoration.
"""

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure

# Viewport 800 wide; the element is twice that. The green child starts exactly at
# the viewport's right edge, so every green pixel is content the old canvas could
# not hold.
CSS_W = 800
CSS_H = 600
ELEM_W = 1600
ELEM_H = 100

DOC = """<!doctype html><html><body style="margin:0">
<div class="m" id="wide" style="position:relative;width:%dpx;height:%dpx;background:#c00000">
  <div style="position:absolute;left:%dpx;top:0;width:%dpx;height:%dpx;background:#00c000"></div>
</div>
<div class="m" id="narrow" style="width:120px;height:60px;background:#0000c0"></div>
</body></html>
""" % (ELEM_W, ELEM_H, CSS_W, ELEM_W - CSS_W, ELEM_H)


def run(bin_path, doc, outdir, scale, extra=()):
    cmd = [bin_path, "--clip", doc, outdir, str(CSS_W), str(CSS_H), str(scale), ".m"]
    cmd.extend(extra)
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def png(path):
    return measure.read_png(path)


def is_green(px, ch, x, y, w):
    i = (y * w + x) * ch
    r, g, b = px[i], px[i + 1], px[i + 2]
    return g > 150 and r < 80 and b < 80


def is_red(px, ch, x, y, w):
    i = (y * w + x) * ch
    r, g, b = px[i], px[i + 1], px[i + 2]
    return r > 150 and g < 80 and b < 80


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    args = ap.parse_args()

    fails = []

    def check(name, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
        if not ok:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        doc = os.path.join(tmp, "wide.html")
        with open(doc, "w") as fh:
            fh.write(DOC)

        # --- default strategy, scale 1 -------------------------------------
        d1 = os.path.join(tmp, "s1")
        rc, out, err = run(args.bin, doc, d1, 1)
        check("a rc is 0", rc == 0, "rc=%d stderr=%r" % (rc, err))

        f1 = os.path.join(d1, "clip_01.png")
        f2 = os.path.join(d1, "clip_02.png")
        check("a both frames written", os.path.exists(f1) and os.path.exists(f2), os.listdir(d1) if os.path.isdir(d1) else "no dir")
        if os.path.exists(f1):
            w, h, ch, px = png(f1)
            check("a wide frame keeps the element's width", (w, h) == (ELEM_W, ELEM_H), "%dx%d want %dx%d" % (w, h, ELEM_W, ELEM_H))
            # The control: the right half must carry the green child's ink.
            check("b overflow is painted, not blank", is_green(px, ch, 900, 50, w) and is_green(px, ch, 1500, 50, w))
            check("c left half still painted", is_red(px, ch, 100, 50, w))
        if os.path.exists(f2):
            w2, h2, _ch2, _px2 = png(f2)
            check("d narrow frame is not widened", (w2, h2) == (120, 60), "%dx%d want 120x60" % (w2, h2))
        check("g no cut warning", "cut" not in err.lower(), repr(err))

        # --- default strategy, scale 2 -------------------------------------
        d2 = os.path.join(tmp, "s2")
        rc2, _o2, _e2 = run(args.bin, doc, d2, 2)
        check("e scale 2 rc is 0", rc2 == 0, "rc=%d" % rc2)
        f3 = os.path.join(d2, "clip_01.png")
        if os.path.exists(f3):
            w3, h3, ch3, px3 = png(f3)
            check("e scale 2 doubles the frame", (w3, h3) == (ELEM_W * 2, ELEM_H * 2), "%dx%d" % (w3, h3))
            check("e scale 2 paints the overflow too", is_green(px3, ch3, 1900, 100, w3))

        # --- the two strategies now agree on width -------------------------
        d3 = os.path.join(tmp, "pe")
        rc3, _o3, _e3 = run(args.bin, doc, d3, 1, ("--per-element",))
        check("f per-element rc is 0", rc3 == 0, "rc=%d" % rc3)
        f4 = os.path.join(d3, "clip_01.png")
        if os.path.exists(f4):
            w4, h4, _c4, _p4 = png(f4)
            check("f strategies agree on width", (w4, h4) == (ELEM_W, ELEM_H), "%dx%d" % (w4, h4))

    if fails:
        print("\n%d leg(s) failed: %s" % (len(fails), ", ".join(fails)))
        return 1
    print("\nall legs hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
