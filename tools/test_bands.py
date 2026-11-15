#!/usr/bin/env python3
"""Banding is a memory optimisation, not a rendering change.

`--clip` without `--per-element` paints the document in row bands sized from
`BLITZ_BAND_MIB` (default 64 MiB), so peak memory is one band rather than the
whole page. That is only a safe change if the pixels are untouched, so this test
pins three things:

  1. banded output is BYTE-IDENTICAL to the single canvas it replaced —
     `BLITZ_BAND_MIB=0` still selects that path;
  2. a document taller than the renderer's 65532-row canvas now RENDERS, where
     `BLITZ_BAND_MIB=0` still refuses it with exit 5;
  3. the banded run really is cheaper — peak RSS measured, not assumed.

    python3 tools/test_bands.py --bin target/release/render

Exit 0 = the contract holds. Exit 1 = it does not. Against a binary from before
banding, leg 2 fails with exit 5 and leg 1 fails on the missing env knob, which
is the point: a test that cannot observe the change it was written for is
decoration.
"""

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_rss

CSS_W = 800
CSS_H = 600
SCALE = 2
SEL = ".s"


def deck(n, h):
    """`n` full-width slides, `h` CSS px tall, one distinct colour each."""
    out = ['<!doctype html><html><body style="margin:0">']
    for i in range(n):
        c = "#%02x%02x%02x" % ((i * 37) % 256, (i * 67) % 256, (i * 97) % 256)
        out.append(
            '<div class="s" style="width:%dpx;height:%dpx;background:%s">'
            '<span style="font:16px sans-serif">slide %d</span></div>' % (CSS_W, h, c, i + 1)
        )
    out.append("</body></html>")
    return "\n".join(out)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(binary, html, outdir, band_mib=None):
    """Render the deck; return (rc, out, err, peak MiB)."""
    cmd = [binary, "--clip", html, outdir, str(CSS_W), str(CSS_H), str(SCALE), SEL, "--document"]
    if band_mib is not None:
        # measure_rss takes no env kwarg, and `env` execs into the binary, so the
        # sampled process tree is the renderer's own.
        cmd = ["env", "BLITZ_BAND_MIB=%s" % band_mib] + cmd
    rc, out, err, _wall, kib = measure_rss.run_measured(cmd)
    return rc, out, err, kib / 1024.0


def frames(outdir):
    if not os.path.isdir(outdir):
        return []
    return sorted(f for f in os.listdir(outdir) if f.startswith("clip_"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bin", required=True)
    ap.add_argument("--keep", action="store_true", help="leave the temp dir behind")
    args = ap.parse_args()
    binary = os.path.abspath(args.bin)
    if not os.path.exists(binary):
        print("FAIL: no such binary %s" % binary)
        return 1

    tmp = tempfile.mkdtemp(prefix="bands-")
    fails = []

    # ---- leg 1: banded == single canvas, byte for byte ---------------------
    # 40 slides x 600 CSS x scale 2 = 48,000 rows: tall enough that the default
    # 64 MiB budget splits it into bands, short enough that the single canvas
    # still fits under the 65,532-row ceiling, so both paths can be compared.
    a_html = os.path.join(tmp, "a.html")
    with open(a_html, "w", encoding="utf-8") as fh:
        fh.write(deck(40, 600))
    a_band = os.path.join(tmp, "a_band")
    a_one = os.path.join(tmp, "a_one")
    rc_b, out_b, err_b, rss_b = run(binary, a_html, a_band)
    rc_o, out_o, err_o, rss_o = run(binary, a_html, a_one, band_mib=0)

    if rc_b != 0:
        fails.append("[banded] rc=%d (want 0)\n%s" % (rc_b, err_b[-600:]))
    if rc_o != 0:
        fails.append("[single canvas] rc=%d (want 0)\n%s" % (rc_o, err_o[-600:]))

    fb, fo = frames(a_band), frames(a_one)
    if len(fb) != 40:
        fails.append("[banded] %d frame(s), want 40" % len(fb))
    if fb != fo:
        fails.append("[banded] frame list differs from the single canvas: %r vs %r" % (fb[:5], fo[:5]))
    else:
        diff = [n for n in fb if md5(os.path.join(a_band, n)) != md5(os.path.join(a_one, n))]
        if diff:
            fails.append(
                "[banded] %d/%d frame(s) differ from the single canvas: %s"
                % (len(diff), len(fb), ", ".join(diff[:5]))
            )

    # Coordinates are reported in ABSOLUTE document rows on both paths, so a
    # caller parsing `clip_NN … at X,Y` sees one set of numbers either way.
    lb = [ln for ln in out_b.splitlines() if ln.startswith("clip_")]
    lo = [ln for ln in out_o.splitlines() if ln.startswith("clip_")]
    if lb != lo:
        fails.append("[banded] clip report differs from the single canvas")
    if len(lb) != 40:
        fails.append("[banded] %d clip line(s), want 40" % len(lb))

    # The saving is the whole point, so it is measured rather than claimed.
    if rc_b == 0 and rc_o == 0 and not rss_b < rss_o:
        fails.append(
            "[banded] peak %.1f MiB is not below the single canvas's %.1f MiB"
            % (rss_b, rss_o)
        )

    # ---- leg 2: a document past the ceiling now renders -------------------
    # 40 slides x 900 CSS x scale 2 = 72,000 rows, past MAX_CANVAS_H (65,532).
    # Banded it renders; with banding disabled it must still refuse with exit 5,
    # which is what the released beta.7 does unconditionally.
    b_html = os.path.join(tmp, "b.html")
    with open(b_html, "w", encoding="utf-8") as fh:
        fh.write(deck(40, 900))
    b_band = os.path.join(tmp, "b_band")
    b_one = os.path.join(tmp, "b_one")
    rc_b2, out_b2, err_b2, rss_b2 = run(binary, b_html, b_band)
    rc_o2, out_o2, err_o2, _ = run(binary, b_html, b_one, band_mib=0)

    if rc_b2 != 0:
        fails.append("[tall banded] rc=%d (want 0: banding removes the ceiling)\n%s"
                     % (rc_b2, err_b2[-600:]))
    if len(frames(b_band)) != 40:
        fails.append("[tall banded] %d frame(s), want 40" % len(frames(b_band)))
    if rc_o2 != 5:
        fails.append("[tall single canvas] rc=%d (want 5: banding disabled must still refuse)"
                     % rc_o2)
    if frames(b_one):
        fails.append("[tall single canvas] wrote %d file(s) before refusing"
                     % len(frames(b_one)))

    print("  leg 1  banded vs single canvas: %d frames, %s" % (
        len(fb), "identical" if not any("differ" in f for f in fails) else "DIFFER"))
    print("  leg 1  peak rss: banded %.1f MiB, single canvas %.1f MiB" % (rss_b, rss_o))
    print("  leg 2  72,000-row deck: banded rc=%d with %d frames, banding off rc=%d"
          % (rc_b2, len(frames(b_band)), rc_o2))

    if not args.keep:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("  kept %s" % tmp)

    if fails:
        print("FAIL: banding contract")
        for f in fails:
            print("  - " + f)
        return 1
    print("ok: banding is byte-identical, cheaper, and lifts the canvas ceiling")
    return 0


if __name__ == "__main__":
    sys.exit(main())
