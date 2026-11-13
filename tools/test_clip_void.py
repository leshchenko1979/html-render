#!/usr/bin/env python3
"""Regression test for the `--clip` panic on elements with no layout box (#1).

`--clip … '*'` aborts the whole run when any matched element has no geometry,
and `*` matches `<br>`, `<wbr>`, `<script>` and `<style>` in every real document.
The fix skips them and reports the count; this test pins that — and pins that the
skip stays narrow, since `<hr>` is a block box with a border and must survive.

    python3 tools/test_clip_void.py --bin target/release/render

Exit 0 = the contract holds. Exit 1 = it does not (the message says which part).
Run it against an OLD binary and it fails, which is the point: a test that cannot
observe the failure it was written for is decoration.
"""

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile

# Two boxes and every void element that used to abort the run. `br`/`wbr` are
# unlayoutable; `script`/`style` are in the DOM with no box. `<hr>` is included
# on purpose and is NOT boxless — it is the narrowness control.
DOC = """<!doctype html><html><body style="margin:0">
<div id="a" style="width:40px;height:20px;background:#c00">a<br>b<hr><wbr></div>
<div id="b" style="width:30px;height:10px;background:#00c"></div>
<script>var x = 1;</script>
<style>body { color: #000 }</style>
</body></html>
"""

SKIP_RE = re.compile(r"skipped (\d+) of (\d+) matched element\(s\): no layout box")


def png_size(path):
    with open(path, "rb") as fh:
        d = fh.read(33)
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("%s is not a PNG" % path)
    return struct.unpack(">II", d[16:24])


def run(binary, html, outdir, selector, extra=()):
    cmd = [binary, "--clip", html, outdir, "120", "60", "2", selector] + list(extra)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the renderer binary")
    args = ap.parse_args()
    binary = os.path.abspath(args.bin)
    fails = []

    with tempfile.TemporaryDirectory() as tmp:
        html = os.path.join(tmp, "void.html")
        with open(html, "w") as fh:
            fh.write(DOC)

        # 1. The reported panic: '*' over a document with void elements.
        #    Both paint paths, because they build `rects` the same way but crop
        #    differently, and a fix in one is not a fix in the other.
        for label, extra in (("crop", ()), ("scroll", ("--document", "--scroll"))):
            outdir = os.path.join(tmp, "out_" + label)
            rc, out, err = run(binary, html, outdir, "*", extra)
            tag = "[%s] '*' with void elements" % label
            if rc != 0:
                fails.append("%s: rc=%d (want 0)\n%s" % (tag, rc, err[-600:]))
                continue

            m = SKIP_RE.search(err)
            if not m:
                fails.append("%s: no skip report on stderr; stderr=%r" % (tag, err[-300:]))
                continue
            skipped, matched = int(m.group(1)), int(m.group(2))
            if skipped == 0:
                fails.append("%s: skipped 0, so the fixture has no void element" % tag)
                continue
            if not matched > skipped:
                fails.append("%s: matched %d <= skipped %d, nothing left to clip"
                             % (tag, matched, skipped))

            boxes = [ln for ln in out.splitlines() if ln.startswith("clip_")]
            want = matched - skipped
            if len(boxes) != want:
                fails.append("%s: %d clip line(s), want %d (%d matched - %d skipped)"
                             % (tag, len(boxes), want, matched, skipped))
            # Numbering must be contiguous over the EMITTED boxes: a caller that
            # reads clip_01..clip_NN must not find a hole where a <br> was.
            names = sorted(f for f in os.listdir(outdir) if f.startswith("clip_"))
            expect = ["clip_%02d.png" % (i + 1) for i in range(want)]
            if names != expect:
                fails.append("%s: files %r, want %r" % (tag, names, expect))
            for n in names:
                try:
                    w, h = png_size(os.path.join(outdir, n))
                except ValueError as exc:
                    fails.append("%s: %s" % (tag, exc))
                    continue
                if w < 1 or h < 1:
                    fails.append("%s: %s is %dx%d" % (tag, n, w, h))

        # 2. A selector whose matches are ALL boxless: exit 3, the same code as
        #    "matched nothing". Emitting an empty dump with rc=0 would report a
        #    complete run that produced no evidence.
        #
        #    `br, wbr` only, because `<hr>` is NOT boxless — it is a block box
        #    with a default border, and CI proved it: `br, hr, wbr` matched 3,
        #    skipped 2, and emitted one clip. Assuming "void element" meant "no
        #    box" is what made this leg fail on its first run.
        outdir = os.path.join(tmp, "out_void_only")
        rc, out, err = run(binary, html, outdir, "br, wbr")
        if rc != 3:
            fails.append("[void-only selector] rc=%d (want 3)\n%s" % (rc, err[-400:]))
        if os.path.isdir(outdir) and os.listdir(outdir):
            fails.append("[void-only selector] wrote %r" % os.listdir(outdir))

        # 2b. The skip must be NARROW. A "fix" that skips anything it cannot
        #     handle would pass leg 1 and silently drop real boxes, so a block
        #     element with a border is asserted to survive with no skip report.
        outdir = os.path.join(tmp, "out_hr")
        rc, out, err = run(binary, html, outdir, "hr")
        if rc != 0:
            fails.append("[hr control] rc=%d (want 0)\n%s" % (rc, err[-400:]))
        else:
            if SKIP_RE.search(err):
                fails.append("[hr control] <hr> was skipped; the skip is too broad")
            boxes = [ln for ln in out.splitlines() if ln.startswith("clip_")]
            if len(boxes) != 1:
                fails.append("[hr control] %d clip line(s), want 1" % len(boxes))

        # 3. Positive control: a normal selector still clips every match, so the
        #    skip path cannot be hiding a document that renders nothing at all.
        outdir = os.path.join(tmp, "out_div")
        rc, out, err = run(binary, html, outdir, "div")
        if rc != 0:
            fails.append("[div control] rc=%d (want 0)\n%s" % (rc, err[-400:]))
        else:
            if SKIP_RE.search(err):
                fails.append("[div control] reported a skip for a boxless-free selector")
            boxes = [ln for ln in out.splitlines() if ln.startswith("clip_")]
            if len(boxes) != 2:
                fails.append("[div control] %d clip line(s), want 2" % len(boxes))

    if fails:
        print("FAIL: --clip contract")
        for f in fails:
            print("  - " + f)
        return 1
    print("ok: --clip skips boxless elements, numbering contiguous, void-only exits 3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
