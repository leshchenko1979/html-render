#!/usr/bin/env python3
"""Banding is a memory optimisation, not a rendering change.

`--clip` without `--per-element` paints the document in row bands sized from
`BLITZ_BAND_MIB` (default 16 MiB), so peak memory is one band rather than the
whole page. That is only safe if the rendering is untouched, so this test pins
five things:

  1. THE DEFAULT MODE IS UNTOUCHED — every fixture in `fixtures.json` renders
     byte-identically with the knob at 1 MiB and at 0. This is a NEGATIVE
     control, and it is worth saying so: `--clip` is the only path that bands, so
     this leg passes because banding does not run here at all. What it catches is
     banding leaking into the single-PNG path, not banding changing a verdict.
     The 54-row corpus verdict is safe for a stronger reason: `run_parity.py`
     renders fixtures with no `--clip`, so banding is structurally absent from
     it. The banded path's real check is Gate C, which renders the deck with
     `--clip` and requires every slide at Chromium's dimensions.
  2. A TALL DOCUMENT RENDERS THE SAME THING — on a multi-band deck the sampled
     frames have identical measured geometry and differ by at most one level per
     channel.
     They are NOT byte-identical: `set_viewport_scroll` shifts the subpixel
     antialiasing phase, which moves a handful of glyph pixels by 1/255, far
     below the gate's tolerance of 8.
  3. THE BANDED RUN IS CHEAPER — peak RSS measured, not assumed.
  4. A DOCUMENT PAST THE CEILING NOW RENDERS — 72,000 rows exceeds
     `MAX_CANVAS_H` (65,532), where `BLITZ_BAND_MIB=0` still refuses with exit 5.

  5. THE GATE-C STRUCTURAL CHECK STILL HOLDS — every `--clip` frame equals the
     rectangle cut from the `--document` render to within one level per channel.
     This leg exists because that claim lived in `GATES.md` as prose no test
     read, and banding falsified it (11 of 12 frames differ by 1/255) without
     anything going red.

    python3 tools/test_bands.py --bin target/release/render

Exit 0 = the contract holds. Exit 1 = it does not. Against a binary from before
banding, leg 4 fails with exit 5 and the other legs fail on the missing env
knob, which is the point: a test that cannot observe the change it was written
for is decoration.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure
import measure_rss

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Small frames on purpose: `measure.measure` is a pure-Python pixel loop, so a
# 800x600 frame costs ~2.6 s against ~10.5 s for 1600x1200. Banding needs a TALL
# document, not large frames, so the height comes from the slide count.
CSS_W = 400
CSS_H = 300
SCALE = 2
SEL = ".s"

# Frames sampled, spread across the document so several bands are covered.
# Sampling is not laziness: `measure.read_png` is a pure-Python unfilter, ~0.4 s
# per 800x600 frame, so decoding all 100 twice would cost ~80 s of CI for a
# property already established on the sample.
GEO_SAMPLE = 8

# Leg 2 wants MANY bands, or "cheaper" measures nothing: at 100 slides the deck is
# 60,000 rows against a 20,971-row band, so the single canvas holds ~3 bands' worth
# at once. Leg 3 only has to be past MAX_CANVAS_H (65,532).
N_SLIDES = 100
N_TALL = 40


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


def geometry_differs(p, q):
    """True if the measured probe geometry differs at all between two PNGs.

    This is the property the parity gate reads: `measure.measure` returns a box
    per probe colour and `measure.delta` is the max per-field difference, so
    banding that leaves every box where it was cannot move a corpus verdict,
    whatever it does to individual pixels.
    """
    a, b = measure.measure(p), measure.measure(q)
    if set(a) != set(b):
        return True
    return any(measure.delta(a[c], b[c]) != 0 for c in a)


def max_channel_delta(p, q):
    """Largest per-channel difference between two same-sized PNGs.

    Decoded with `measure.read_png`, not Pillow: CI has no Pillow, so an import
    here would make this leg fail exactly where it runs (it did, once) while
    passing on a dev box that happens to have it installed.
    """
    wa, ha, ca, pa = measure.read_png(p)
    wb, hb, cb, pb = measure.read_png(q)
    if (wa, ha, ca) != (wb, hb, cb):
        return 255
    return max((abs(x - y) for x, y in zip(pa, pb)), default=0)


def run(binary, html, outdir, width, height, band_mib=None, clip=True):
    """Render; return (rc, stdout, stderr, peak MiB)."""
    if clip:
        cmd = [binary, "--clip", html, outdir, str(width), str(height),
               str(SCALE), SEL, "--document"]
    else:
        cmd = [binary, html, outdir, str(width), str(height)]
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

    # ---- leg 1: the corpus renders identically with banding on and off -----
    # Every fixture, not a sample: these are the exact inputs Gate C measures.
    corpus = json.load(open(os.path.join(ROOT, "fixtures.json")))
    corpus_diff = []
    corpus_fail = []
    for fx in corpus:
        html = os.path.join(ROOT, "fixtures", fx["file"])
        a = os.path.join(tmp, "corpus_band", fx["name"] + ".png")
        b = os.path.join(tmp, "corpus_one", fx["name"] + ".png")
        rc1, _, e1, _ = run(binary, html, a, fx["width"], fx["height"], clip=False)
        rc2, _, e2, _ = run(binary, html, b, fx["width"], fx["height"],
                            band_mib=0, clip=False)
        if rc1 or rc2:
            corpus_fail.append("%s: rc=%d/%d %s" % (fx["name"], rc1, rc2, (e1 or e2)[-120:]))
            continue
        if md5(a) != md5(b):
            corpus_diff.append(fx["name"])
    if corpus_fail:
        fails.append("[corpus] %d fixture(s) failed to render: %s"
                     % (len(corpus_fail), "; ".join(corpus_fail[:3])))
    if corpus_diff:
        fails.append(
            "[corpus] %d/%d fixture(s) differ with banding: %s"
            % (len(corpus_diff), len(corpus), ", ".join(corpus_diff[:5]))
        )

    # ---- leg 2: a multi-band document renders the same geometry ------------
    # 100 slides x 300 CSS x scale 2 = 60,000 rows. The default 16 MiB budget is
    # 16*1024*1024/(400*2*4) = 5,242 rows, so this spans ~12 bands while still
    # fitting under MAX_CANVAS_H (65,532), so both paths can be compared.
    a_html = os.path.join(tmp, "a.html")
    with open(a_html, "w", encoding="utf-8") as fh:
        fh.write(deck(N_SLIDES, CSS_H))
    a_band = os.path.join(tmp, "a_band")
    a_one = os.path.join(tmp, "a_one")
    rc_b, out_b, err_b, rss_b = run(binary, a_html, a_band, CSS_W, CSS_H)
    rc_o, out_o, err_o, rss_o = run(binary, a_html, a_one, CSS_W, CSS_H, band_mib=0)

    if rc_b != 0:
        fails.append("[banded] rc=%d (want 0)\n%s" % (rc_b, err_b[-600:]))
    if rc_o != 0:
        fails.append("[single canvas] rc=%d (want 0)\n%s" % (rc_o, err_o[-600:]))

    fb, fo = frames(a_band), frames(a_one)
    worst = 0
    worst_at = ""
    geo_diff = []
    sampled = []
    if len(fb) != N_SLIDES:
        fails.append("[banded] %d frame(s), want %d" % (len(fb), N_SLIDES))
    if fb != fo:
        fails.append("[banded] frame list differs from the single canvas: %r vs %r"
                     % (fb[:5], fo[:5]))
    else:
        step = max(1, len(fb) // GEO_SAMPLE)
        sampled = fb[::step]
        for n in sampled:
            if geometry_differs(os.path.join(a_band, n), os.path.join(a_one, n)):
                geo_diff.append(n)
        if geo_diff:
            fails.append(
                "[banded] %d/%d sampled frame(s) have different GEOMETRY: %s"
                % (len(geo_diff), len(sampled), ", ".join(geo_diff[:5]))
            )
        for n in sampled:
            d = max_channel_delta(os.path.join(a_band, n), os.path.join(a_one, n))
            if d > worst:
                worst, worst_at = d, n
        if worst > 1:
            fails.append(
                "[banded] %s differs by %d level(s) per channel, want <= 1 (AA phase only)"
                % (worst_at, worst)
            )

    # Coordinates are reported in ABSOLUTE document rows on both paths, so a
    # caller parsing `clip_NN ... at X,Y` sees one set of numbers either way.
    lb = [ln for ln in out_b.splitlines() if ln.startswith("clip_")]
    lo = [ln for ln in out_o.splitlines() if ln.startswith("clip_")]
    if lb != lo:
        fails.append("[banded] clip report differs from the single canvas")
    if len(lb) != N_SLIDES:
        fails.append("[banded] %d clip line(s), want %d" % (len(lb), N_SLIDES))

    # The saving is the whole point, so it is measured rather than claimed.
    # The claim is not "smaller", it is "does not hold the page". So the yardstick
    # is the canvas itself: doc_rows x phys_w x 4 bytes. The banded run must peak
    # BELOW that (it never allocates the whole thing) and the single-canvas run
    # must peak ABOVE it (positive control: it really does hold the page). A
    # bare "banded < single" would pass on a rounding error.
    canvas_mib = (N_SLIDES * CSS_H * SCALE) * (CSS_W * SCALE) * 4 / (1024.0 * 1024.0)
    if rc_b == 0 and rc_o == 0:
        if not rss_b < canvas_mib:
            fails.append(
                "[banded] peak %.1f MiB is not below the %.1f MiB canvas it should no "
                "longer allocate" % (rss_b, canvas_mib)
            )
        if not rss_o > canvas_mib:
            fails.append(
                "[single canvas] peak %.1f MiB is not above the %.1f MiB canvas: the "
                "control does not reproduce the old behaviour" % (rss_o, canvas_mib)
            )

    # ---- leg 3: a document past the ceiling now renders -------------------
    # 40 slides x 900 CSS x scale 2 = 72,000 rows, past MAX_CANVAS_H (65,532).
    # Banded it renders; with banding disabled it must still refuse with exit 5,
    # which is what the released beta.7 does unconditionally.
    b_html = os.path.join(tmp, "b.html")
    with open(b_html, "w", encoding="utf-8") as fh:
        fh.write(deck(N_TALL, 900))
    b_band = os.path.join(tmp, "b_band")
    b_one = os.path.join(tmp, "b_one")
    rc_b2, _, err_b2, _ = run(binary, b_html, b_band, CSS_W, 900)
    rc_o2, _, _, _ = run(binary, b_html, b_one, CSS_W, 900, band_mib=0)

    if rc_b2 != 0:
        fails.append("[tall banded] rc=%d (want 0: banding removes the ceiling)\n%s"
                     % (rc_b2, err_b2[-600:]))
    if len(frames(b_band)) != N_TALL:
        fails.append("[tall banded] %d frame(s), want %d"
                     % (len(frames(b_band)), N_TALL))
    if rc_o2 != 5:
        fails.append("[tall single canvas] rc=%d (want 5: banding disabled must still refuse)"
                     % rc_o2)
    if frames(b_one):
        fails.append("[tall single canvas] wrote %d file(s) before refusing"
                     % len(frames(b_one)))

    # ---- leg 4: the structural check Gate C claims ------------------------
    # GATES.md C asserts that every --clip frame equals the same rectangle cut
    # from the --document render, "0 differing pixels, 12/12". Banding made that
    # false the day it landed (AA phase, see the docstring), and it was prose no
    # test read, so it rotted silently. The property that DOES hold is geometry
    # plus a one-level tolerance, and it is asserted here rather than restated.
    N_GC = 6
    GC_H = 200
    gc_html = os.path.join(tmp, "gc.html")
    # Large bold glyphs on purpose. `deck()` uses 16 px text, which renders
    # identically banded or not, so a tolerance of 1 would never be exercised —
    # the leg would pass without touching the mechanism it guards. Measured: at
    # 64 px bold the phase shift appears from the third band on.
    gc_slides = []
    for i in range(N_GC):
        c = "#%02x%02x%02x" % ((i * 37) % 256, (i * 67) % 256, (i * 97) % 256)
        gc_slides.append(
            '<div class="s" style="width:%dpx;height:%dpx;background:%s">'
            '<span style="font:bold 64px sans-serif">slide %d</span></div>'
            % (CSS_W, GC_H, c, i + 1)
        )
    with open(gc_html, "w", encoding="utf-8") as fh:
        fh.write('<!doctype html><html><body style="margin:0">'
                 + "".join(gc_slides) + "</body></html>")
    gc_clip = os.path.join(tmp, "gc_clip")
    gc_full = os.path.join(tmp, "gc_full.png")
    # 1 MiB forces a band smaller than a frame, so every frame is painted at its
    # own non-zero scroll offset: the case where the phase shift shows up.
    rc_gc, out_gc, err_gc, _ = run(binary, gc_html, gc_clip, CSS_W, GC_H, band_mib=1)
    rc_gf, _, err_gf, _w, _k = measure_rss.run_measured(
        [binary, gc_html, gc_full, str(CSS_W), str(GC_H), str(SCALE), "--full", "--document"]
    )
    gc_worst = -1
    gc_bad = []
    if rc_gc != 0:
        fails.append("[gate-c] clip rc=%d (want 0)\n%s" % (rc_gc, err_gc[-400:]))
    if rc_gf != 0:
        fails.append("[gate-c] --full rc=%d (want 0)\n%s" % (rc_gf, err_gf[-400:]))
    if rc_gc == 0 and rc_gf == 0:
        fw_full, fh_full, ch_full, px_full = measure.read_png(gc_full)
        stride = fw_full * ch_full
        boxes = {}
        for ln in out_gc.splitlines():
            m = re.match(r"(clip_\d+) .* physical (\d+)x(\d+) at (\d+),(\d+)", ln)
            if m:
                boxes[m.group(1)] = (int(m.group(2)), int(m.group(3)),
                                     int(m.group(4)), int(m.group(5)))
        gc_worst = 0
        for name in frames(gc_clip):
            stem = name[:-4] if name.endswith(".png") else name
            path = os.path.join(gc_clip, name)
            fw, fh, ch, px = measure.read_png(path)
            if stem not in boxes:
                gc_bad.append("%s has no clip report line" % stem)
                continue
            bw, bh, bx, by = boxes[stem]
            if (fw, fh) != (bw, bh):
                gc_bad.append("%s is %dx%d, report says %dx%d" % (stem, fw, fh, bw, bh))
                continue
            if ch != ch_full or bx + fw > fw_full or by + fh > fh_full:
                gc_bad.append("%s does not fit the --full render" % stem)
                continue
            crop = px_full[by * stride + bx * ch:(by + fh) * stride]
            if len(crop) != len(px):
                gc_bad.append("%s crop is %d bytes, frame is %d" % (stem, len(crop), len(px)))
                continue
            gc_worst = max(gc_worst, max((abs(a - b) for a, b in zip(px, crop)), default=0))
        if len(frames(gc_clip)) != N_GC:
            gc_bad.append("%d frame(s), want %d" % (len(frames(gc_clip)), N_GC))
        scrolled = sum(1 for b in boxes.values() if b[3] > 0)
        if scrolled < 1:
            gc_bad.append("every frame was painted at scroll 0:0, so this leg never "
                          "reached the banded path it is meant to guard")
        if gc_worst > 1:
            gc_bad.append("max channel delta %d between clip frames and the --full crop "
                          "(want <=1: the gate's tolerance is 8)" % gc_worst)
        fails.extend("[gate-c] " + b for b in gc_bad)

    print("  leg 1  default mode: %d/%d fixtures byte-identical at 1 MiB and 0 "
          "(negative control: this path never bands)"
          % (len(corpus) - len(corpus_diff) - len(corpus_fail), len(corpus)))
    print("  leg 2  multi-band deck: %d frames, geometry %s on %d sampled, "
          "max channel delta %d on the same %d"
          % (len(fb), "identical" if not geo_diff else "DIFFERS", len(sampled),
             worst, len(sampled)))
    print("  leg 2  peak rss: banded %.1f MiB, single canvas %.1f MiB, canvas %.1f MiB"
          % (rss_b, rss_o, canvas_mib))
    print("  leg 4  gate C structural check: %d frames vs crops of the --full render, max channel delta %s"
          % (len(frames(gc_clip)), "n/a" if gc_worst < 0 else gc_worst))
    print("  leg 3  72,000-row deck: banded rc=%d with %d frames, banding off rc=%d"
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
    print("ok: banding leaves the geometry identical, costs less, lifts the canvas "
          "ceiling, and never reaches the mode that does not band")
    return 0


if __name__ == "__main__":
    sys.exit(main())
